"""素材加载与 ffmpeg 操作：探测时长、抽音轨、加载图片与视频为节点输出。

仅在 ComfyUI 运行环境内使用；探测与抽轨复用环境自带 ffmpeg。
"""
from __future__ import annotations

import re
import shutil
import subprocess
import time
from pathlib import Path
from collections import OrderedDict
from threading import RLock
from functools import lru_cache
from concurrent.futures import Future

_IMAGE_CACHE = OrderedDict()
_IMAGE_CACHE_BYTES = 0
_IMAGE_CACHE_LIMIT = 128 * 1024 * 1024
_IMAGE_CACHE_LOCK = RLock()
_VIDEO_MUTE_LOCK = RLock()


class PreparedMediaCache:
    """Reuse decoded data, never loop context/results. Large existing mmap output has one slot."""
    def __init__(self, budget=1024**3, limit=64):
        self.budget, self.limit = budget, limit
        self.entries, self.pending = OrderedDict(), {}
        self.large = None
        self.bytes = 0
        self.hits = self.misses = 0
        self.lock = RLock()
        self.epoch = 0
        self.signatures = OrderedDict()
        self.touched = {}
        self.clock = time.monotonic

    @staticmethod
    def size(value, seen=None):
        seen = set() if seen is None else seen
        if id(value) in seen:
            return 0
        seen.add(id(value))
        if hasattr(value, "numel") and hasattr(value, "element_size"):
            return value.numel() * value.element_size()
        if hasattr(value, "nbytes"):
            return int(value.nbytes)
        if isinstance(value, dict):
            return sum(PreparedMediaCache.size(v, seen) for v in value.values())
        if isinstance(value, (tuple, list)):
            return sum(PreparedMediaCache.size(v, seen) for v in value)
        if hasattr(value, "images"):
            return sum(PreparedMediaCache.size(getattr(value, name, None), seen) for name in ("images", "audio", "alpha"))
        return 0

    def get(self, key, loader, allow_large=False):
        with self.lock:
            if key in self.entries:
                self.touched[key] = self.clock()
                self.hits += 1
                value, size = self.entries[key]
                self.entries.move_to_end(key)
                return value
            if self.large is not None and self.large[0] == key:
                self.touched[key] = self.clock()
                self.hits += 1
                return self.large[1]
            pending = self.pending.get(key)
            owner = pending is None
            if owner:
                pending = Future()
                self.pending[key] = pending
                self.misses += 1
            epoch = self.epoch
        if not owner:
            return pending.result()
        try:
            value = loader()
            size = self.size(value)
            with self.lock:
                if value is not None and epoch == self.epoch:
                    if size > self.budget:
                        if allow_large:
                            if self.large is not None: self.touched.pop(self.large[0], None)
                            self.large = (key, value, size)
                            self.touched[key] = self.clock()
                    else:
                        self.entries[key] = (value, size)
                        self.touched[key] = self.clock()
                        self.bytes += size
                        while self.bytes > self.budget or len(self.entries) > self.limit:
                            evicted_key, (_, evicted) = self.entries.popitem(last=False)
                            self.touched.pop(evicted_key, None)
                            self.bytes -= evicted
                pending.set_result(value)
            return value
        except BaseException as error:
            pending.set_exception(error)
            raise
        finally:
            with self.lock:
                if self.pending.get(key) is pending:
                    self.pending.pop(key, None)

    def trim(self, keep_recent=8, max_age=120):
        """任务边界回收旧缓存；仍被执行器持有的对象按Python引用规则继续有效。"""
        with self.lock:
            now = self.clock()
            before = self.bytes + (self.large[2] if self.large else 0)
            for key in list(self.entries):
                if key in self.pending:
                    continue
                if (len(self.entries) > keep_recent or self.bytes > self.budget // 2
                        or now - self.touched.get(key, now) >= max_age):
                    _, size = self.entries.pop(key)
                    self.bytes -= size
                    self.touched.pop(key, None)
            if self.large and self.large[0] not in self.pending:
                self.touched.pop(self.large[0], None)
                self.large = None
            return {"cache_bytes_before": before, "cache_bytes_after": self.bytes}

    def clear(self):
        with self.lock:
            self.epoch += 1
            self.entries.clear()
            self.pending.clear()
            self.large = None
            self.bytes = 0
            self.touched.clear()

    def bind(self, owner, signature):
        with self.lock:
            if owner in self.signatures and self.signatures[owner] != signature:
                self.clear()
            self.signatures[owner] = signature
            self.signatures.move_to_end(owner)
            while len(self.signatures) > 64:
                self.signatures.popitem(last=False)


PREPARED_MEDIA = PreparedMediaCache()


def cached_media(role, sources, settings, loader, *, allow_large=False):
    """Only stat source identities on a hit; source edits/ranges/settings create new keys."""
    from .storage import digest
    sources = sources if isinstance(sources, (tuple, list)) else [sources]
    identities = []
    try:
        for source in sources:
            path = resolve_source(source) if isinstance(source, dict) else Path(source)
            stamp = path.stat()
            identities.append((str(path.resolve()), stamp.st_mtime_ns, stamp.st_size))
            if isinstance(source, dict) and source.get("original"):
                original = resolve_source({**source, **source["original"]})
                version = original.stat()
                identities.append((str(original.resolve()), version.st_mtime_ns, version.st_size))
    except (OSError, ValueError, KeyError, TypeError):
        return loader()
    key = digest([role, identities, settings])
    return PREPARED_MEDIA.get(key, loader, allow_large=allow_large)


def _image_pixels(path):
    """只缓存不可变的 RGB 原像素；每次输出独立 float32 张量。"""
    import numpy as np
    from PIL import Image
    global _IMAGE_CACHE_BYTES
    path = Path(path)
    stat = path.stat()
    key = (str(path.resolve()), stat.st_mtime_ns, stat.st_size)
    with _IMAGE_CACHE_LOCK:
        pixels = _IMAGE_CACHE.get(key)
        if pixels is not None:
            _IMAGE_CACHE.move_to_end(key)
            return pixels
        with Image.open(path) as picture:
            pixels = np.array(picture.convert("RGB"))
        pixels.setflags(write=False)
        if pixels.nbytes <= _IMAGE_CACHE_LIMIT:
            for old in list(_IMAGE_CACHE):
                if old[0] == key[0]:
                    _IMAGE_CACHE_BYTES -= _IMAGE_CACHE.pop(old).nbytes
            while _IMAGE_CACHE and _IMAGE_CACHE_BYTES + pixels.nbytes > _IMAGE_CACHE_LIMIT:
                _, evicted = _IMAGE_CACHE.popitem(last=False)
                _IMAGE_CACHE_BYTES -= evicted.nbytes
            _IMAGE_CACHE[key] = pixels
            _IMAGE_CACHE_BYTES += pixels.nbytes
        return pixels

AUDIO_EXTS = {".wav", ".mp3", ".flac", ".ogg", ".m4a", ".aac", ".opus", ".aiff", ".aif"}
VIDEO_EXTS = {".mp4", ".webm", ".mov", ".mkv", ".avi", ".m4v"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}


def ffmpeg_executable():
    executable = shutil.which("ffmpeg")
    if executable:
        return executable
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def _run(arguments, log_path=None):
    executable = ffmpeg_executable()
    if executable is None:
        raise ValueError("需要 FFmpeg 来处理视频素材，请将 FFmpeg 加入 PATH 或安装 imageio-ffmpeg")
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    finished = subprocess.run([executable, *arguments], stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, stdin=subprocess.DEVNULL, creationflags=creationflags)
    if log_path:
        # 只保留失败日志；成功的空 .log 文件会误导排查。
        try:
            if finished.returncode:
                Path(log_path).write_bytes(finished.stderr or b"")
            else:
                Path(log_path).unlink(missing_ok=True)
        except OSError:
            pass
    return finished


_DURATION = re.compile(rb"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")


def probe_duration(path):
    """用 ffmpeg 的输入摘要读时长（秒）；读不出时返回 None。"""
    finished = _run(["-v", "info", "-i", str(path)])
    match = _DURATION.search(finished.stderr or b"")
    if not match:
        return None
    hours, minutes, seconds = (float(part) for part in match.groups())
    duration = hours * 3600 + minutes * 60 + seconds
    return duration if duration > 0 else None


def _flags():
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _exec(arguments):
    executable = ffmpeg_executable()
    if executable is None:
        raise ValueError("需要 FFmpeg 来处理视频素材，请将 FFmpeg 加入 PATH 或安装 imageio-ffmpeg")
    return subprocess.run([executable, *arguments], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          stdin=subprocess.DEVNULL, creationflags=_flags())


def has_audio_stream(path):
    return cached_media("audio-stream", Path(path), [], lambda: _has_audio_stream(path))


def _has_audio_stream(path):
    """ffprobe 可用则直接查询；否则用 ffmpeg 映射音轨到空输出探测（无音轨时映射报错）。"""
    probe = shutil.which("ffprobe")
    if probe:
        finished = subprocess.run([probe, "-v", "error", "-select_streams", "a",
                                   "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(path)],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  stdin=subprocess.DEVNULL, creationflags=_flags())
        if finished.returncode == 0:
            return b"audio" in (finished.stdout or b"")
    finished = _exec(["-v", "error", "-i", str(path), "-map", "0:a:0", "-c", "copy", "-f", "null", "-"])
    return finished.returncode == 0


def extract_audio(source, target):
    """把视频的音轨抽成 48 kHz WAV；成功返回 True。"""
    target.parent.mkdir(parents=True, exist_ok=True)
    log = target.with_suffix(".log")
    finished = _run(["-v", "error", "-nostdin", "-n", "-i", str(source),
                     "-vn", "-acodec", "pcm_s16le", "-ar", "48000", str(target)], log_path=log)
    if finished.returncode != 0 or not target.is_file() or target.stat().st_size == 0:
        target.unlink(missing_ok=True)
        return False
    return True


def _source_roots(origin):
    """素材可能的位置：input 素材库（新名/旧名）→ 生成归档 → 数据根 media（兼容旧工作流）。"""
    from . import layout
    from .storage import data_roots
    if origin == "input":
        return [path for path in layout.input_libraries() if path is not None]
    if origin == "output":
        return [path for path in layout.output_libraries() if path is not None]
    if origin == "session":
        # 分段生成/合并结果的预览记录 origin=session，文件在 data_root/sessions/<会话>/...
        from .storage import STORE
        return [STORE.root]
    if origin != "media":
        raise ValueError(f"未知素材来源，请重新导入（origin={origin}）")
    roots = [root / "media" for root in data_roots()]
    roots.extend(path for path in layout.input_libraries() if path is not None)
    return roots


def resolve_source(source):
    """时间线 source → 绝对路径（新/旧库名、旧工作流路径互相兜底）。"""
    from . import layout
    from .storage import contained
    origin = source.get("origin")
    relative = str(source.get("file") or "")
    if origin == "input":
        path = layout.resolve_input_file(relative)
        if path is not None:
            return path
    elif origin == "output":
        path = layout.resolve_output_file(relative)
        if path is not None:
            return path
    roots = _source_roots(origin)
    candidates = [relative]
    if origin in {"input", "output"}:
        cleaned = relative.replace("\\", "/").lstrip("/")
        for prefix in (layout.INPUT_LIBRARY, layout.LEGACY_INPUT_LIBRARY,
                       layout.OUTPUT_LIBRARY, layout.LEGACY_OUTPUT_LIBRARY):
            if cleaned.startswith(prefix + "/"):
                stripped = cleaned[len(prefix) + 1:]
                candidates.extend(f"{name}/{stripped}" for name in
                                  (layout.INPUT_LIBRARY, layout.LEGACY_INPUT_LIBRARY,
                                   layout.OUTPUT_LIBRARY, layout.LEGACY_OUTPUT_LIBRARY))
                break
    for root in roots:
        for candidate in candidates:
            try:
                path = contained(root, candidate)
            except ValueError:
                continue
            if path.is_file():
                return path
    return contained(roots[0], relative)


def load_image_tensor(source, *, normalize=True):
    return cached_media("image-tensor", source, [normalize, source.get("normalization")],
                        lambda: _load_image_tensor(source, normalize=normalize))


def _load_image_tensor(source, *, normalize=True):
    """图片素材 → IMAGE 张量 (1,H,W,3) 0..1。"""
    import numpy as np
    import torch
    from PIL import Image
    from .normalization import normalize_source
    source = normalize_source({**source, "kind": "image"}) if normalize else dict(source)
    frame = _image_pixels(resolve_source(source)).astype(np.float32) / 255.0
    return torch.from_numpy(frame).unsqueeze(0)


@lru_cache(maxsize=128)
def _retained_frame_count(filename, size, modified, start, duration):
    """Use CFR metadata, otherwise count presentation timestamps without decoding pixels."""
    import av
    count = 0
    with av.open(filename) as container:
        stream = container.streams.video[0]
        origin = (stream.start_time or 0) * stream.time_base
        if stream.frames and stream.average_rate and stream.average_rate == stream.base_rate:
            import math
            rate = float(stream.average_rate)
            first = min(stream.frames, max(0, math.ceil(start * rate - 1e-7)))
            last = min(stream.frames, math.ceil((start + duration) * rate - 1e-7)) if duration else stream.frames
            return max(0, last - first)
        for packet in container.demux(stream):
            if packet.pts is None or not packet.size:
                continue
            stamp = float(packet.pts * stream.time_base - origin)
            if stamp + 1e-7 < start:
                continue
            if duration and stamp >= start + duration - 1e-7:
                continue
            count += 1
    return count


def load_video_object(source, *, start_time=0.0, duration=0.0, normalize=True):
    """视频素材 → ComfyUI VIDEO 对象（与官方 LoadVideo 同类）。"""
    from comfy_api.input_impl import VideoFromFile
    from .normalization import normalize_source
    source = normalize_source({**source, "kind": "video"}) if normalize else dict(source)
    path = resolve_source(source)
    if not path.is_file():
        raise ValueError(f"视频素材缺失：{source.get('name', path.name)}")
    if normalize and source.get("muted") and has_audio_stream(path):
        # 仅剥离音轨，画面流直接复制；不更改帧率、分辨率或编码质量。
        from .storage import ROOT, digest
        stat = path.stat()
        target = ROOT / "media" / "derived" / ("muted_" + digest([str(path), stat.st_size, stat.st_mtime_ns])[:24] + path.suffix)
        with _VIDEO_MUTE_LOCK:
            if not target.is_file():
                target.parent.mkdir(parents=True, exist_ok=True)
                result = _run(["-v", "error", "-nostdin", "-n", "-i", str(path), "-map", "0:v:0", "-c:v", "copy", "-an", str(target)])
                if result.returncode:
                    raise ValueError("轨道视频静音失败：" + (result.stderr or b"").decode("utf-8", errors="replace")[-500:])
        path = target
    class PreparedVideoFromFile(VideoFromFile):
        def get_components(self):
            return cached_media("video-components", path, [float(start_time), float(duration)],
                                lambda: super(PreparedVideoFromFile, self).get_components())

        def get_frame_count(self):
            if not normalize and (start_time or duration):
                stamp = path.stat()
                return _retained_frame_count(str(path), stamp.st_size, stamp.st_mtime_ns, float(start_time), float(duration))
            return super().get_frame_count()
    return PreparedVideoFromFile(str(path), start_time=start_time, duration=duration)


def video_first_frame(source, start_time, end_time):
    return cached_media("video-first-frame", source, [float(start_time), float(end_time)],
                        lambda: _video_first_frame(source, start_time, end_time))


def _video_first_frame(source, start_time, end_time):
    """取分段范围内的第一帧，按源时间戳定位，不缩放或重编码素材。"""
    import av
    import numpy as np
    import torch
    with av.open(str(resolve_source(source))) as container:
        stream = container.streams.video[0]
        origin = (stream.start_time or 0) * stream.time_base
        if start_time > 0:
            container.seek(int(start_time / stream.time_base) + (stream.start_time or 0),
                           stream=stream, backward=True)
        for frame in container.decode(video=0):
            if frame.pts is None:
                raise ValueError("视频缺少画面时间戳，无法准确定位分段首帧")
            stamp = float(frame.pts * stream.time_base - origin)
            if stamp + 1e-7 < start_time:
                continue
            if stamp >= end_time - 1e-7:
                break
            pixels = frame.to_ndarray(format="rgb24").astype(np.float32) / 255.0
            return torch.from_numpy(pixels).unsqueeze(0)
    raise ValueError("当前分段的视频范围内没有可读取的首帧")


def video_audio(source, cache_root):
    """视频参考音频：抽轨结果缓存到 derived 目录后按 AUDIO 字典返回。"""
    path = resolve_source(source)
    stat = path.stat()
    cached = cache_root / "derived" / f"{source['id']}_{stat.st_mtime_ns}.wav"
    if not cached.is_file():
        if not has_audio_stream(path):
            raise ValueError(f"视频 {source.get('name', path.name)} 没有音轨，无法作为音频参考")
        if not extract_audio(path, cached):
            raise ValueError(f"视频 {source.get('name', path.name)} 的音轨提取失败")
    from .audio import load_audio
    return load_audio(cached)


def merge_clips(clips, kind, target):
    """Render only retained source ranges; never fill a removed source interval."""
    import math
    if kind not in {"audio", "video"} or not isinstance(clips, list) or not 1 <= len(clips) <= 100:
        raise ValueError("请选择同一轨道上的音频或视频片段合并")
    retained = []
    for clip in clips:
        path = resolve_source(clip)
        duration = probe_duration(path)
        # 预览缩略图合并：未提供裁剪范围时直接使用整条素材。
        first = float(clip.get("src_in", 0))
        last = float(clip["src_out"]) if clip.get("src_out") is not None else duration
        if not path.is_file() or duration is None or not all(math.isfinite(t) for t in (first, last)) \
                or not 0 <= first < last or last > duration + 1 / 48:
            raise ValueError("待合并片段的源范围无效")
        retained.append((path, first, last, bool(clip.get("muted"))))
    arguments = ["-v", "error", "-nostdin", "-n"]
    for path, *_ in retained:
        arguments.extend(["-i", str(path)])
    filters, inputs = [], []
    has_audio = [not muted and has_audio_stream(path) for path, _, _, muted in retained]
    audio_enabled = kind == "audio" or any(has_audio)
    if kind == "video":
        import av
        with av.open(str(retained[0][0])) as container:
            stream = container.streams.video[0]
            width, height = stream.width // 2 * 2, stream.height // 2 * 2
    for index, (_, first, last, _) in enumerate(retained):
        if kind == "video":
            filters.append(f"[{index}:v]trim=start={first:.9f}:end={last:.9f},setpts=PTS-STARTPTS,"
                           f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
                           f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=24,format=yuv420p[v{index}]")
            inputs.append(f"[v{index}]")
        if audio_enabled:
            if has_audio[index]:
                chain = f"[{index}:a]atrim=start={first:.9f}:end={last:.9f},asetpts=PTS-STARTPTS,aresample=48000,aformat=channel_layouts=stereo,apad"
            else:
                chain = "anullsrc=r=48000:cl=stereo"
            filters.append(f"{chain},atrim=duration={last-first:.9f}[a{index}]")
            inputs.append(f"[a{index}]")
    outputs = ("[vout]" if kind == "video" else "") + ("[aout]" if audio_enabled else "")
    filters.append("".join(inputs) + f"concat=n={len(retained)}:v={int(kind=='video')}:a={int(audio_enabled)}" + outputs)
    arguments.extend(["-filter_complex", ";".join(filters)])
    if kind == "video":
        arguments.extend(["-map", "[vout]", "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p"])
    if audio_enabled:
        arguments.extend(["-map", "[aout]", "-c:a", "pcm_s16le" if kind == "audio" else "aac"])
    arguments.append(str(target))
    target.parent.mkdir(parents=True, exist_ok=True)
    result = _exec(arguments)
    if result.returncode or not target.is_file():
        raise ValueError("合并片段失败：" + (result.stderr or b"").decode("utf-8", errors="replace")[-800:])
    return {"duration": sum(last-first for _, first, last, _ in retained), "has_audio": audio_enabled}
