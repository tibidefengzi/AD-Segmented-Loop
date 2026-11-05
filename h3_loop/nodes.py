from __future__ import annotations
import re

import json
from pathlib import Path
import math

from . import assets
from . import compat
from .color_match import COLOR_MATCH_CHOICES as _COLOR_MATCH_CHOICES, parse_blend as _color_match_blend, reference_frame_stats
from .normalization import normalize_source
from .spec import relay_reanchor_interval as _relay_reanchor_interval, relay_reanchors, relay_parent_stage
from .audio import h3_window, read_audio_entry, read_pieces
from .engine import reference_manifest, segment_signatures, template_for
from .storage import ROOT, STORE, digest
from .spec import (ASPECT_CHOICES, DEFAULT_ASPECT, DEFAULT_MEGAPIXELS, FPS, MEGAPIXEL_CHOICES,
                       generation_frames, resolve_canvas, target_frames, validate_fps)
from .timeline import (DEFAULT_SEGMENT_MAX_FRAMES, EMPTY_TIMELINE, MAX_AUDIO_PORTS, MAX_IMAGE_PORTS, MAX_VIDEO_PORTS,
                       audio_pieces, clip_span, loop_settings, output_timeline, reference_pack, source_pieces, timeline_identity, timeline_plan)
from .video import check_alignment, video_identity, video_plan, video_window
from .continuum import (
    continuum_available, continuum_node, continuum_status_report,
    load_continuum_state, save_continuum_state,
)

CATEGORY = "AD-Segmented-Loop"


class MaterialOutputTypes(tuple):
    """素材清单库：仅分段清单（index 0）。保留类以免旧引用报错。"""
    def __getitem__(self, index):
        if isinstance(index, slice) or index < 0:
            return super().__getitem__(index)
        if index == 0:
            return "H3_AUDIO_QUEUE"
        return "IMAGE,VIDEO,AUDIO"


SEGMENT_FIXED_NAMES = ("context", "index", "start_frame", "generation_frames", "fps", "width", "height", "positive_prompt", "negative_prompt", "round_image", "round_audio", "round_video", "source_video", "original_audio")

SEGMENT_FIXED_TYPES = ("H3_LOOP_CONTEXT", "INT", "IMAGE", "INT", "FLOAT", "INT", "INT", "STRING", "STRING", "IMAGE", "AUDIO", "VIDEO", "VIDEO", "AUDIO")

SEGMENT_REF_NAMES = (
    *(f"ref_image_{i}" for i in range(MAX_IMAGE_PORTS)),
    *(f"ref_video_{i}" for i in range(MAX_VIDEO_PORTS)),
    *(f"ref_video_audio_{i}" for i in range(MAX_VIDEO_PORTS)),
    *(f"ref_audio_{i}" for i in range(MAX_AUDIO_PORTS)),
)


SEGMENT_EXTRA_NAMES = ("mask_frames",)
SEGMENT_EXTRA_TYPES = ("IMAGE",)

# 潜桥前置预卷：H3 生成帧必须落在 5+17k 网格；T 是最终目标帧数，C 是桥上下文帧数。
# G = 最小 5+17k >= T+C，E = G-T-C；动作迁移模式把源窗口向前多取 C+E 帧，
# 生成 G 帧后裁头 C+E，得到精确 T 帧，且保存 latent 的尾部 C 帧正好落在最终末帧。
RELAY_MODES = ("关闭", "动作迁移预卷", "普通多参考")
RELAY_CONTEXTS = ["5", "22", "39"]
RELAY_CHOICES = (
    "0 · 关闭",
    "5 · 上下文5帧",
    "22 · 上下文22帧（推荐）",
    "39 · 上下文39帧",
)
RELAY_CHOICE_VALUES = {"0 · 关闭": 0, "5 · 上下文5帧": 5,
                       "22 · 上下文22帧（推荐）": 22, "39 · 上下文39帧": 39}
# 潜桥链重锚定：每 N 段清一次上一段 latent 上下文（第 N、2N…段从当前段源画面/提示词重新锚定）。
# 用于抑制长链自回归累积的“越跑越糊 / 越跑越暗”；0=关闭。
RELAY_REANCHOR_CHOICES = (
    "0 · 不重锚定",
    "2 · 每2段",
    "3 · 每3段",
    "5 · 每5段",
    "7 · 每7段",
    "10 · 每10段",
)


def _relay_choice_value(raw):
    """把新下拉标签或旧版纯数字统一解析为 0/5/22/39。"""
    if isinstance(raw, (int, float)):
        return int(raw) if int(raw) in (0, 5, 22, 39) else 0
    text = str(raw or "").strip()
    if not text:
        return 0
    if text in RELAY_CHOICE_VALUES:
        return RELAY_CHOICE_VALUES[text]
    match = re.match(r"^\s*(\d+)", text)
    if match:
        value = int(match.group(1))
        if value in (0, 5, 22, 39):
            return value
    return 0


# 参考视频口输出统一等比缩放到“最长边 864”像素（用户 2026-10-29 确认）。
# H3 内部还会按自己的 adapt_canvas（短边 768 / 面积上限 768×1344 / 32 对齐）再重采样；
# 这里先缩是为了把解码内存与 frame-cache 从 1080p 级降到 864 级，避免整机被拉爆。
REF_VIDEO_LONG_EDGE = 864

# 兼容性告警只打印一次，避免每段执行刷屏。
_WARNED_ONCE = set()


def _material_label(source):
    """素材的可读名称：优先 name，其次 file，最后 id，用于错误提示定位。"""
    if isinstance(source, dict):
        for key in ("name", "file"):
            value = source.get(key)
            if value:
                return str(value)
        if source.get("id"):
            return str(source["id"])
    return "未知素材"


def _material_error(source, exc):
    """有素材但读取/解码失败：带上素材名与原始原因，便于用户定位问题文件。"""
    return ValueError(f"素材读取失败：{_material_label(source)}（{exc}）")


class SegmentOutputTypes(tuple):
    """当前段：前 N 口固定类型，其后为动态参考素材口（视频参考口按 H3 要求输出 IMAGE 帧）。"""
    def __getitem__(self, index):
        if isinstance(index, slice) or index < 0:
            return super().__getitem__(index)
        if index < len(SEGMENT_FIXED_TYPES):
            return SEGMENT_FIXED_TYPES[index]
        ref_index = index - len(SEGMENT_FIXED_TYPES)
        if ref_index < len(SEGMENT_REF_NAMES):
            # 动态槽位随参考数量紧凑排列，用官方ANY允许按实际媒体判型。
            # 前端仍按端口名声明IMAGE/AUDIO，返回值仍是原张量/音频；
            # 不能返回联合字符串，否则通用预览器会把它当作普通数据。
            return "*"
        return SEGMENT_EXTRA_TYPES[ref_index - len(SEGMENT_REF_NAMES)]


def _active_image_sources(state, index=None):
    """参与输出的图片素材：路径优先素材独占；否则跳过“禁止输出”(-2)。"""
    scopes = {image.get("source"): image.get("segment", -1) for image in (state.get("images") or [])}
    images = [source for source in (state.get("sources") or []) if source.get("kind") == "image"
              and scopes.get(source.get("id"), -1) != -2
              and (index is None or scopes.get(source.get("id"), -1) in (-1, 0, index + 1))]
    priority = [source for source in images if source.get("priority")]
    return priority or images


def _round_material(state, kind):
    """按当前轮（索引号）取素材：存在路径优先素材时只取优先素材。"""
    priority = [source for source in (state.get("sources") or []) if source.get("priority") and source.get("kind") == kind]
    if priority:
        return priority
    return [source for source in (state.get("sources") or []) if source.get("kind") == kind]


def _priority_round(state, index):
    priority = [source for source in (state.get("sources") or []) if source.get("priority")]
    if not priority:
        return None
    return priority[index % len(priority)]


def _priority_of_kind(state, kind):
    return [source for source in (state.get("sources") or [])
            if source.get("priority") and source.get("kind") == kind]


def _single_kind_only(state, kind):
    """素材准备里只有一种素材类型：没有其它类型的时间线片段，素材全部是该类型。"""
    tracks = {"video": state.get("video_tracks") or [], "audio": state.get("audio_tracks") or []}
    for other_kind, lanes in tracks.items():
        if other_kind == kind:
            continue
        if any(lane for lane in lanes):
            return False
    sources = state.get("sources") or []
    return bool(sources) and all(source.get("kind") == kind for source in sources)


def _round_image(state, index):
    """本轮图像：有「素材路径」优先图片时用路径素材，否则用素材准备内的内容。

    只有图片时（无音视频片段）：按图片加载顺序逐段输出，第 N 段取第 N 张（禁止输出的跳过），
    与纯音频/纯视频按片段顺序取本轮素材的逻辑一致。
    """
    priority = _priority_of_kind(state, "image")
    if priority:
        item = priority[index % len(priority)]
        try:
            return assets.load_image_tensor(item)
        except Exception as exc:
            # 有素材但解码失败必须报错，不能静默输出空结果让用户误以为素材缺失。
            raise _material_error(item, exc) from exc
    if _single_kind_only(state, "image"):
        scopes = {image.get("source"): image.get("segment", -1) for image in (state.get("images") or [])}
        images = [source for source in (state.get("sources") or [])
                  if source.get("kind") == "image" and scopes.get(source.get("id"), -1) != -2]
        if images:
            # 纯图片模式也遵守「第 N 段参考」：本段指定图优先；否则用全局参考；都没有时保留按图片顺序逐段取。
            scoped = any(scopes.get(source.get("id"), -1) > 0 for source in images)
            specific = [source for source in images if scopes.get(source.get("id"), -1) == index + 1]
            globals_ = [source for source in images if scopes.get(source.get("id"), -1) in (-1, 0)]
            if specific:
                item = specific[0]
            elif scoped and globals_:
                item = globals_[0]
            else:
                item = images[index % len(images)]
            try:
                return assets.load_image_tensor(item)
            except Exception as exc:
                raise _material_error(item, exc) from exc
    return _round_image_legacy(state, index)


def _round_audio(state, index):
    """本轮音频：有「素材路径」优先音频时用路径素材，否则用素材准备内的内容。"""
    priority = _priority_of_kind(state, "audio")
    if priority:
        item = priority[index % len(priority)]
        try:
            import torch
            wave, rate = _decode_audio(item)
            if wave is None or wave.shape[1] == 0:
                return None
            return {"waveform": torch.from_numpy(wave).unsqueeze(0), "sample_rate": rate}
        except Exception as exc:
            raise _material_error(item, exc) from exc
    sources = _round_material(state, "audio")
    if not sources:
        return None
    item = sources[index % len(sources)]
    try:
        import torch
        wave, rate = _decode_audio(item)
        if wave is None or wave.shape[1] == 0:
            return None
        return {"waveform": torch.from_numpy(wave).unsqueeze(0), "sample_rate": rate}
    except Exception as exc:
        raise _material_error(item, exc) from exc


def _segment_video_source(state, index, entry):
    """首帧与本段视频共用已选中的源范围；轨道静音只影响声音。"""
    priority = _priority_of_kind(state, "video")
    if priority:
        source = priority[index % len(priority)]
        selected = entry.get("video_source")
        if selected and (selected.get("origin"), selected.get("file")) == (source.get("origin"), source.get("file")):
            return selected
        duration = source.get("duration") or assets.probe_duration(assets.resolve_source(source))
        if not duration:
            raise ValueError("无法读取本段优先视频的时长")
        return {**source, "src_in": 0.0, "src_out": min(float(duration), float(entry["seconds_float"]))}
    selected = entry.get("video_source")
    return {**selected, "muted": bool(entry.get("video_audio_muted"))} if selected else None


def _round_video(source):
    if source is None:
        return None
    start, end = float(source["src_in"]), float(source["src_out"])
    if end <= start:
        raise ValueError("本段视频的起止范围无效")
    return assets.load_video_object(source, start_time=start, duration=end-start)

def _stack_image_sources(sources):
    return assets.cached_media("aligned-image-batch", sources, [], lambda: _stack_image_sources_uncached(sources))


def _stack_image_sources_uncached(sources):
    """把多张图片叠成一个 IMAGE 批次（尺寸不同时等比缩放后居中填充到第一张的尺寸，不拉伸、不裁切）。"""
    import torch
    tensors = []
    for source in sources:
        try:
            tensor = assets.load_image_tensor(source)
        except Exception as exc:
            # 多图叠批时任何一张读取失败都直接报错并带上素材名，便于定位坏图。
            raise _material_error(source, exc) from exc
        if tensor is not None:
            tensors.append(tensor)
    if not tensors:
        return None
    if len(tensors) == 1:
        return tensors[0]
    height, width = int(tensors[0].shape[1]), int(tensors[0].shape[2])
    aligned = []
    for tensor in tensors:
        h, w = int(tensor.shape[1]), int(tensor.shape[2])
        if h == height and w == width:
            aligned.append(tensor)
            continue
        scale = min(width / w, height / h)
        new_w = max(1, round(w * scale))
        new_h = max(1, round(h * scale))
        resized = torch.nn.functional.interpolate(tensor.permute(0, 3, 1, 2), size=(new_h, new_w),
                                                  mode="bilinear", align_corners=False).permute(0, 2, 3, 1)
        canvas = torch.zeros((tensor.shape[0], height, width, tensor.shape[3]), dtype=tensor.dtype, device=tensor.device)
        top = (height - new_h) // 2
        left = (width - new_w) // 2
        canvas[:, top:top + new_h, left:left + new_w, :] = resized
        aligned.append(canvas)
    return torch.cat(aligned, dim=0)


def _round_image_legacy(state, index):
    """没有素材路径时：本段图像 = 本轮实际使用的那几张参考图（全局参考 + 第 N 段参考，跳过禁止输出）。"""
    sources = _active_image_sources(state, index)
    if not sources:
        return None
    return _stack_image_sources(sources)


def _audio_window_full(path, begin, want, sample_rate=48000):
    return assets.cached_media("video-audio-full-window", path, [begin, want, sample_rate],
                               lambda: _audio_window_full_uncached(path, begin, want, sample_rate))


def _audio_window_full_uncached(path, begin, want, sample_rate=48000):
    """完整解码后按采样窗口截取（保底路径，行为与旧实现一致）。"""
    import av
    import numpy as np
    chunks = []
    with av.open(str(path)) as container:
        if container.streams.audio:
            resampler = av.AudioResampler(format="fltp", layout="stereo", rate=sample_rate)
            for frame in container.decode(audio=0):
                for converted in resampler.resample(frame):
                    chunks.append(converted.to_ndarray())
            for converted in resampler.resample(None):
                chunks.append(converted.to_ndarray())
    if not chunks:
        return None
    wave = np.concatenate(chunks, axis=1)
    return wave[:, begin:begin + want]


def _audio_window_seek(path, begin, want, sample_rate=48000):
    return assets.cached_media("video-audio-seek-window", path, [begin, want, sample_rate],
                               lambda: _audio_window_seek_uncached(path, begin, want, sample_rate))


def _audio_window_seek_uncached(path, begin, want, sample_rate=48000):
    """只解码本段附近音频：seek 到起点前 0.5 秒，按 pts 对齐；定位失败返回 None。"""
    import av
    import numpy as np
    margin = sample_rate // 2
    seek_begin = max(0, begin - margin)
    with av.open(str(path)) as container:
        if not container.streams.audio:
            return None
        stream = container.streams.audio[0]
        if seek_begin > 0:
            try:
                container.seek(int(seek_begin / sample_rate / float(stream.time_base)), stream=stream, backward=True)
            except Exception:
                return None
        resampler = av.AudioResampler(format="fltp", layout="stereo", rate=sample_rate)
        pieces = []
        cursor = seek_begin
        stopped_early = False
        for frame in container.decode(audio=0):
            start_sample = None
            if frame.pts is not None:
                try:
                    start_sample = int(round(float(frame.pts * frame.time_base) * sample_rate))
                except Exception:
                    start_sample = None
            if start_sample is None:
                start_sample = cursor
            elif pieces and abs(start_sample - cursor) <= sample_rate * .005:
                # MKV 等容器的毫秒时间基会把连续音频包的时间戳取整；
                # 用连续采样游标衔接，避免每个包边界产生细小静音/覆盖。
                start_sample = cursor
            for converted in resampler.resample(frame):
                array = converted.to_ndarray()
                pieces.append((start_sample, array))
                start_sample += array.shape[1]
                cursor = start_sample
            if cursor >= begin + want + margin:
                stopped_early = True
                break
        if not stopped_early:
            for converted in resampler.resample(None):
                array = converted.to_ndarray()
                pieces.append((cursor, array))
                cursor += array.shape[1]
    if not pieces:
        return None
    low = min(item[0] for item in pieces)
    high = max(item[0] + item[1].shape[1] for item in pieces)
    if begin < low or begin + want > high:
        return None
    canvas = np.zeros((2, high - low), dtype=np.float32)
    for offset, array in pieces:
        if array.shape[0] < 2:
            array = np.repeat(array[:1], 2, axis=0)
        canvas[:, offset - low:offset - low + array.shape[1]] = array[:2]
    return canvas[:, begin - low:begin - low + want]


def _segment_audio(entry, window):
    """本段音频：优先取视频素材自身的音轨（按本段范围裁剪，保留立体声）。"""
    source = entry.get("source_video")
    if source and not entry.get("video_audio_muted"):
        try:
            import torch
            sample_rate = 48000
            start = float(int(window.get("start_frame") or 0)) / float(FPS)
            seconds = float(int(window.get("target_frames") or window.get("frame_count") or 0)) / float(FPS)
            begin = max(0, int(round(start * sample_rate)))
            want = int(round(seconds * sample_rate))
            path = assets.resolve_source(_original_source(source))
            import os
            use_seek = os.environ.get("AIDEN_H3_AUDIO_SEEK", "1").strip().lower() not in {"0", "false", "off"}
            wave = _audio_window_seek(path, begin, want, sample_rate) if use_seek else None
            if wave is None:
                wave = _audio_window_full(path, begin, want, sample_rate)
            if wave is not None and wave.shape[1] > 0:
                return {"waveform": torch.from_numpy(wave).unsqueeze(0), "sample_rate": sample_rate}
        except Exception:
            pass
    return read_audio_entry(entry)


def _frame_buffer(shape):
    """大视频使用临时磁盘映射，保持原帧、分辨率与 float32 精度。"""
    import numpy as np
    import tempfile
    import weakref
    if math.prod(shape) * 4 <= 256 * 1024 * 1024:
        return np.empty(shape, dtype=np.float32)
    directory = ROOT / "frame-cache"
    directory.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(prefix="frames_", suffix=".npy", dir=directory, delete=False) as handle:
        path = Path(handle.name)
    try:
        buffer = np.lib.format.open_memmap(path, mode="w+", dtype=np.float32, shape=shape)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    def release(mapping, filename):
        mapping.close()
        filename.unlink(missing_ok=True)
    weakref.finalize(buffer, release, buffer._mmap, path)
    return buffer


def _video_frames(source, start=0, count=None, start_time=None, end_time=None, long_edge=None):
    if not source:
        return None
    return assets.cached_media("reference-video-frames", source, [start, count, start_time, end_time, long_edge],
                               lambda: _video_frames_uncached(source, start, count, start_time, end_time, long_edge),
                               allow_large=True)


def _video_frames_uncached(source, start=0, count=None, start_time=None, end_time=None, long_edge=None):
    """把视频素材解码成 IMAGE 帧张量（可选窗口、可选统一最长边缩放）。

    内存友好：一次性预分配 float32 输出，逐帧就地写入并 /255，
    避免“先叠 uint8、再转 float、再除”造成的多份大缓冲（VHS 同思路）。
    long_edge 给定时在解码阶段就用 PyAV 的 reformat 等比缩放（长边 = long_edge），
    不会先分配原分辨率的整段缓冲。
    """
    if not source:
        return None
    try:
        import av
        import numpy as np
        import torch
        path = assets.resolve_source(source)
        with av.open(str(path)) as container:
            stream = container.streams.video[0]
            if start_time is not None and start_time > 0 and not start:
                try:
                    container.seek(int(start_time / stream.time_base), stream=stream, backward=True)
                except Exception:
                    # Preserve the original sequential fallback for unseekable files.
                    container.seek(0)
            total = (max(1, math.ceil((end_time - start_time) * float(stream.average_rate or FPS)))
                     if start_time is not None and end_time is not None else int(count or stream.frames or 0))
            target = None
            if long_edge:
                long_edge = max(2, int(long_edge))
                source_w, source_h = int(stream.width or 0), int(stream.height or 0)
                if source_w > 0 and source_h > 0:
                    scale = long_edge / float(max(source_w, source_h))
                    target = (max(2, int(round(source_w * scale))), max(2, int(round(source_h * scale))))
            out = None
            index = 0
            written = 0
            for frame in container.decode(video=0):
                if start_time is not None:
                    timestamp = float(frame.pts * frame.time_base) if frame.pts is not None else index / float(stream.average_rate or FPS)
                    if timestamp < start_time - 1e-7:
                        index += 1
                        continue
                    if end_time is not None and timestamp >= end_time - 1e-7:
                        break
                if index < start:
                    index += 1
                    continue
                if target is not None:
                    frame = frame.reformat(width=target[0], height=target[1], format="rgb24")
                array = frame.to_ndarray(format="rgb24")
                if out is None:
                    if total <= 0:
                        total = written + 1
                    out = _frame_buffer((max(total, 1), array.shape[0], array.shape[1], 3))
                if written >= out.shape[0]:
                    grown = _frame_buffer((out.shape[0] * 2, out.shape[1], out.shape[2], 3))
                    grown[:written] = out[:written]
                    out = grown
                out[written] = array
                out[written] /= 255.0
                written += 1
                index += 1
                if count is not None and written >= count:
                    break
        if out is None or written == 0:
            return None
        return torch.from_numpy(out[:written])
    except Exception as exc:
        # 有素材但解码失败必须报错（完全取不到帧仍按“范围为空”返回 None）。
        raise _material_error(source, exc) from exc


def _reference_lanes(state, kind):
    """参考口按有素材的轨道顺序固定；未上轨素材不冒充当前段。"""
    return [(number, lane) for number, lane in enumerate(state.get(kind + "_tracks") or []) if lane]


def _lane_clip_for_segment(lane, segment_index):
    """单条素材整条复用给所有段；切成多段时按分段序号依次取对应片段（段多时循环）。"""
    clips = sorted(lane or [], key=lambda item: clip_span(item)[0])
    if not clips:
        return None, False
    if len(clips) == 1:
        return clips[0], True
    return clips[segment_index % len(clips)], False


def _fit_reference_frames(frames, height, width):
    """把不同尺寸的参考视频帧统一到同一画布：等比缩放 + 居中补黑，便于多片段顺序拼接。"""
    import torch
    if int(frames.shape[1]) == int(height) and int(frames.shape[2]) == int(width):
        return frames
    batch, src_h, src_w, _ = frames.shape
    scale = min(width / float(src_w), height / float(src_h))
    new_w = max(2, int(round(src_w * scale)))
    new_h = max(2, int(round(src_h * scale)))
    canvas = torch.zeros((batch, 3, int(height), int(width)), dtype=frames.dtype)
    top, left = (int(height) - new_h) // 2, (int(width) - new_w) // 2
    # 保留同一插值算法和输出精度；缩放中间量最多约32MiB，而非再分配整批。
    chunk = max(1, (32 * 1024 * 1024) // max(1, 3 * new_h * new_w * frames.element_size()))
    for begin in range(0, batch, chunk):
        end = min(batch, begin + chunk)
        data = frames[begin:end, ..., :3].movedim(-1, 1)
        if (new_h, new_w) != (src_h, src_w):
            data = torch.nn.functional.interpolate(data, size=(new_h, new_w), mode="bilinear", align_corners=False)
        canvas[begin:end, :, top:top + new_h, left:left + new_w] = data
    return canvas.movedim(1, -1)


def _canvas_mask_frames(source, window, width, height):
    """遮罩专用输出：本段视频帧按生成画布缩放，并补齐/裁到本段帧数（尾部重复末帧）。"""
    import torch
    if source is None:
        return None
    video = _round_video(source)
    if video is None:
        return None
    try:
        images = video.get_components().images
    except Exception:
        return None
    if images is None or not len(images):
        return None
    frames = _fit_reference_frames(images[..., :3].float(), int(height), int(width))
    count = max(1, int(window.get("frame_count") or frames.shape[0]))
    if frames.shape[0] > count:
        frames = frames[:count]
    elif frames.shape[0] < count:
        frames = torch.cat([frames, frames[-1:].expand(count - frames.shape[0], -1, -1, -1)], dim=0)
    return frames


def _relay_mask_frames(segment_source, window, width, height, pre_frames):
    """潜桥动作迁移模式：遮罩专用帧从源段起点向前多取 pre_frames 帧，长度=生成帧 G。

    这些预卷帧会交给 VAEEncode/编辑遮罩参与采样，最终被 TrimAV 连同桥钉住区一起裁掉；
    段首没有足够源帧时用首帧补齐（保持首帧，不复制运动）。
    """
    if segment_source is None:
        return None
    import torch
    total = max(1, int(window.get("frame_count") or 0))
    pre = max(0, int(pre_frames))
    start = float(segment_source.get("src_in") or 0)
    shifted = start - pre / float(FPS)
    clamped = max(0.0, -shifted)
    source = {**segment_source, "src_in": max(0.0, shifted),
              "src_out": max(0.0, shifted) + total / float(FPS)}
    video = _round_video(source)
    if video is None:
        return None
    try:
        images = video.get_components().images
    except Exception:
        return None
    if images is None or not len(images):
        return None
    frames = _fit_reference_frames(images[..., :3].float(), int(height), int(width))
    pad = int(round(clamped * float(FPS)))
    if pad > 0:
        frames = torch.cat([frames[:1].expand(pad, -1, -1, -1), frames], dim=0)
    if frames.shape[0] > total:
        frames = frames[:total]
    elif frames.shape[0] < total:
        frames = torch.cat([frames, frames[-1:].expand(total - frames.shape[0], -1, -1, -1)], dim=0)
    return frames


def _existing_relay_suffix(session_id):
    try:
        import folder_paths
        root = Path(folder_paths.get_output_directory()) / "relay_kit"
        pattern = "ad_%s_*" % str(session_id)
        candidates = [p for p in root.glob(pattern) if p.is_dir() and not p.name[:-len(str(session_id)) - 4].endswith("_v")]
        candidates = [p for p in candidates if "_v" not in p.name]
        if not candidates:
            return ""
        candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        name = candidates[0].name
        prefix = "ad_%s_" % str(session_id)
        return name[len(prefix):] if name.startswith(prefix) else ""
    except Exception:
        return ""


def _ensure_relay_run_id(session_id, session):
    """稳定潜桥 run 后缀：首次生成后落盘，恢复历史组不再改目录。"""
    rid = str(session.get("relay_run_id") or "") if isinstance(session, dict) else ""
    if not rid:
        rid = _existing_relay_suffix(session_id)
    if not rid:
        import uuid
        rid = uuid.uuid4().hex
    if isinstance(session, dict):
        session["relay_run_id"] = rid
    try:
        with STORE.lock(session_id):
            state = STORE._load(session_id)
            if not state.get("relay_run_id"):
                state["relay_run_id"] = rid
                STORE._save(state)
    except Exception:
        pass
    return rid


def _relay_run_id(session_id, session):
    """潜桥片名：会话 + 持久化 stable id；恢复历史组/升级旧会话不变目录。"""
    rid = str(session.get("relay_run_id") or "") if isinstance(session, dict) else ""
    if not rid:
        rid = str(session.get("reset_id") or "") if isinstance(session, dict) else ""
    if not rid:
        rid = "g%s" % ((session.get("group_seq") or session.get("round_no") or 1) if isinstance(session, dict) else 1)
    return "ad_%s_%s" % (str(session_id), rid)


def _migrate_legacy_relay_dir(session_id, target_run):
    """旧版 relay_kit 目录名补到新稳定目录；旧工作流/旧历史组无感续接。"""
    try:
        import folder_paths
        import shutil
        root = Path(folder_paths.get_output_directory()) / "relay_kit"
        target = root / str(target_run)
        if not target.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        if any(target.glob("stage_*.safetensors")):
            return
        legacy = [p for p in root.glob("ad_%s_r*_e*" % str(session_id)) if p.is_dir()]
        if not legacy:
            return
        legacy.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        for source in legacy:
            copied = False
            for file in source.glob("stage_*.safetensors"):
                dst = target / file.name
                if not dst.exists():
                    shutil.copy2(file, dst)
                    copied = True
            if copied:
                break
    except Exception:
        return


def _relay_stage_file(run_id, stage_index):
    """稳定 run 目录里某一段的 stage 文件路径（父 latent 的唯一来源）。"""
    import folder_paths
    root = Path(folder_paths.get_output_directory()) / "relay_kit"
    return root / str(run_id) / ("stage_%05d.safetensors" % int(stage_index))


def _require_parent_relay_stage(run_id, index, label="", reanchor=False):
    """普通续接使用上一段，重锚定使用该轮第 1 段。"""
    previous = relay_parent_stage(int(index), reanchor)
    if previous is None:
        return
    path = _relay_stage_file(run_id, previous)
    if not path.is_file():
        scope = ("（%s）" % label) if label else ""
        if reanchor:
            raise ValueError(f"潜桥重锚定需要本轮第 1 段的外观锚{scope}，但稳定目录里还没有 stage_00000。"
                             "请恢复或重新生成并通过第 1 段，再继续重锚定。")
        raise ValueError(
            "潜桥续接需要上一段（第 %d 段）的 latent%s，但稳定目录里还没有 stage_%05d。"
            "请先在第 %d 段所在轮次生成/通过该段，或用「运行此段」重跑后再抽卡。"
            % (int(index), scope, previous, previous))


def _relay_identity(session_id, session, index):
    """返回 (bridge_run_id, save_run_id, stage_index)。

    抽卡/预览子会话：桥读父会话的稳定 stage；存盘写本次生成唯一的 run_id，
    选中结果时再把该 stage 提升到父会话的稳定 stage。
    """
    parent = session.get("preview_parent") if isinstance(session, dict) else None
    legacy_owner = session_id
    if parent:
        parent_id = str(parent.get("session_id") or "")
        # 历史轮/旧批次回抽：preview_parent 显式携带该轮自己的潜桥链，不能读当前轮链。
        target_run = str(parent.get("relay_run_id") or "")
        stage_index = int(parent.get("index", index))
        legacy_owner = parent_id
        if not target_run:
            try:
                parent_state = STORE.read(parent_id)
            except Exception:
                parent_state = {}
            _ensure_relay_run_id(parent_id, parent_state)
            target_run = _relay_run_id(parent_id, parent_state)
    else:
        _ensure_relay_run_id(session_id, session)
        target_run = _relay_run_id(session_id, session)
        stage_index = int(index)
    token = ""
    active = session.get("active") if isinstance(session, dict) else None
    if isinstance(active, dict):
        token = str(active.get("token") or "")
    if not token:
        token = str(index)
    save_run = "%s_v%s" % (target_run, token[:24])
    _migrate_legacy_relay_dir(legacy_owner, target_run)
    return target_run, save_run, stage_index


def _relay_targets_for_entries(entries):
    """全潜桥段统一按总时长向下取整到 17 网格，再尽量均匀分配。

    逐段独立 floor 会让 65 帧段只剩 51 帧，并让总帧数低于 total//17*17。
    这里先固定总单位 = sum(T_i)//17，再按各段小数余数从大到小补 17 帧，
    保证所有段输出都是 17n 且总输出 = floor(源总帧/17)*17。
    """
    sizes = []
    for entry in entries or []:
        try:
            sizes.append(max(0, int(target_frames(float(entry.get("seconds_float") or 0)))))
        except Exception:
            sizes.append(0)
    total_units = sum(sizes) // 17
    units = [size // 17 for size in sizes]
    remainder = total_units - sum(units)
    if remainder > 0 and sizes:
        order = sorted(range(len(sizes)), key=lambda i: (sizes[i] % 17, sizes[i]), reverse=True)
        for key in order:
            if remainder <= 0:
                break
            units[key] += 1
            remainder -= 1
    return [17 * u for u in units]


def relay_plan(target, context, mode=None):
    """潜桥 17 网格计划：输出 L 必须是 17 的倍数，G=L+C 自动是 5+17k。

    这时 E=0，裁头只裁 C；源/参考视频窗口是否预卷由调用方按是否有视频帧决定。
    """
    target = int(target)
    context = int(context)
    if target < 1:
        raise ValueError("目标帧数必须为正")
    if context < 1:
        raise ValueError("潜桥上下文帧数必须为正")
    if target % 17:
        raise ValueError("潜桥输出段长必须是 17 的倍数")
    generated = target + context
    if generated < 5 or (generated - 5) % 17:
        raise ValueError("H3 生成长度必须落在 5+17k")
    return {"target_frames": target, "context_frames": context,
            "generation_frames": generated, "residue_frames": 0,
            "trim_frames": context, "pre_roll_frames": context}


def _reference_spans(lane, sources, start, end):
    from .spec import frame_index
    for clip in sorted(lane, key=lambda item: item["tl_start"]):
        clip_start, clip_end = map(frame_index, clip_span(clip))
        lo, hi = max(start, clip_start), min(end, clip_end)
        if hi > lo:
            yield clip, sources[clip["source"]], lo, hi, frame_index(clip["src_in"]) + lo - clip_start


def _normalized_reference(source, cache):
    key = (source.get("origin"), source.get("file"))
    if not all(key):
        return source
    if key not in cache:
        cache[key] = normalize_source({**source, "kind": "video"})
    return cache[key]


def _reference_video_window_frames(lane, source_map, start, end, target_frames=None, *,
                                   body_start=None, cache=None, canvas=None, stats=None):
    """按 24 FPS 时间线帧位写入 G 帧。段内空隙留黑，缺失预卷前补，段尾余量保持末帧。"""
    import torch
    from .spec import frame_index
    start, end = frame_index(start), frame_index(end)
    count = int(target_frames or max(5, end - start))
    end = min(end, start + count)
    body_start = start if body_start is None else body_start
    spans = list(_reference_spans(lane or [], source_map, start, end))
    if not any(hi > body_start for _, _, _, hi, _ in spans):
        return None
    cache = {} if cache is None else cache
    result, cursor = None, 0
    for _, source, lo, hi, first in spans:
        normalized = _normalized_reference(source, cache)
        frames = _video_frames(normalized, start_time=first / FPS, end_time=(first + hi - lo) / FPS,
                               long_edge=REF_VIDEO_LONG_EDGE)
        if frames is None or not len(frames):
            continue
        frames = frames[:hi - lo]
        offset = lo - start
        if stats is not None and not stats and lo + len(frames) > body_start:
            first_body = max(body_start - lo, 0)
            stats.update(reference_frame_stats(frames[first_body], normalized, first + first_body))
        if result is None and not canvas and offset == 0 and len(frames) == count:
            return frames
        if result is None:
            height, width = canvas or tuple(frames.shape[1:3])
            result = torch.from_numpy(_frame_buffer((count, height, width, 3)))
            chunk = max(1, (32 * 1024 * 1024) // (height * width * 3 * result.element_size()))
        if cursor < offset:
            result[cursor:offset].zero_()
        for begin in range(0, len(frames), chunk):
            part = _fit_reference_frames(frames[begin:begin + chunk], height, width)
            result[offset + begin:offset + begin + len(part)].copy_(part)
        if cursor == 0 and 0 < offset <= body_start - start:
            result[:offset].copy_(result[offset:offset + 1].expand_as(result[:offset]))
        written = offset + len(frames)
        cursor = hi - start
        if written < cursor:
            result[written:cursor].copy_(result[written - 1:written].expand_as(result[written:cursor]))
        del frames, part
    if result is not None:
        body_end = end - start
        if cursor < body_end:
            result[cursor:body_end].zero_()
        if body_end < count:
            result[body_end:].copy_(result[body_end - 1:body_end].expand_as(result[body_end:]))
    return result
def _full_lane_audio_pieces(lane, source_map):
    """整条音频复用：按时间线顺序拼接该轨道所有片段，不受当前段起止范围裁剪。"""
    pieces = []
    for clip in sorted(lane, key=lambda item: clip_span(item)[0]):
        source = source_map[clip["source"]]
        first, last = float(clip["src_in"]), float(clip["src_out"])
        if last <= first:
            continue
        pieces.append({"origin": source["origin"], "file": source["file"],
                       "src_in": first, "src_out": last})
    return pieces


def _lane_muted(state, kind, number):
    flags = (state.get("muted_tracks") or {}).get(kind + "_tracks") or []
    return number < len(flags) and bool(flags[number])


def _reference_video_audio(state, number, lane, sources, start, end, target_frames=None, *, cache=None):
    import torch
    from .spec import frame_index
    rate = 48000
    samples_per_frame = rate // FPS
    start, end = frame_index(start), frame_index(end)
    frames = int(target_frames or max(1, end - start))
    result = torch.zeros((1, 2, frames * samples_per_frame))
    if not _lane_muted(state, "video", number):
        flags = (state.get("muted_tracks") or {}).get("video_tracks") or []
        cache = {} if cache is None else cache
        for clip, source, lo, hi, first in _reference_spans(lane, sources, start, min(end, start + frames)):
            if clip.get("audio_muted") or (source.get("muted") and number >= len(flags)):
                continue
            path = assets.resolve_source(_normalized_reference(source, cache))
            if not assets.has_audio_stream(path):
                continue
            begin, count = first * samples_per_frame, (hi - lo) * samples_per_frame
            wave = _audio_window_seek(path, begin, count, rate)
            if wave is None:
                wave = _audio_window_full(path, begin, count, rate)
            if wave is not None:
                at = (lo - start) * samples_per_frame
                chunk = torch.from_numpy(wave)
                if chunk.shape[0] == 1:
                    chunk = chunk.expand(2, -1)
                length = min(chunk.shape[-1], result.shape[-1] - at)
                result[0, :, at:at + length] = chunk[:2, :length]
    return {"waveform": result, "sample_rate": rate}


def _video_clip_audio(state, number, clip, source_map):
    """参考视频轨道选中的那条视频片段的音轨（整条复用/按段取片段）。"""
    import torch
    rate = 48000
    source = source_map[clip["source"]]
    span = max(1, round((float(clip["src_out"]) - float(clip["src_in"])) * rate))
    result = torch.zeros((1, 2, span))
    flags = (state.get("muted_tracks") or {}).get("video_tracks") or []
    if clip.get("audio_muted") or (number < len(flags) and bool(flags[number])):
        return {"waveform": result, "sample_rate": rate}
    path = assets.resolve_source(_original_source(source))
    if not assets.has_audio_stream(path):
        return {"waveform": result, "sample_rate": rate}
    begin, count = round(float(clip["src_in"]) * rate), span
    wave = _audio_window_seek(path, begin, count, rate)
    if wave is None:
        wave = _audio_window_full(path, begin, count, rate)
    if wave is not None:
        chunk = torch.from_numpy(wave)
        if chunk.shape[0] == 1:
            chunk = chunk.expand(2, -1)
        length = min(chunk.shape[-1], span)
        result[0, :, :length] = chunk[:2, :length]
    return {"waveform": result, "sample_rate": rate}


def _timeline_material_values(queue, used=None, index=None, window=None, *,
                              media_details=None, mask_size=None, mask_source=None, color_match=False):
    """紧凑顺序：图 -> 视频 -> 视频音频 -> 音频，末尾 None 垫满；与前端动态口一致。

    used 为下游实际连接的输出口序号集合；未连接的口不加载素材（避免无谓解码）。
    """
    from .spec import frame_index
    state = output_timeline(queue.get("timeline") or {})
    sources = state.get("sources") or []
    image_sources = [source for source in sources if source.get("kind") == "image"]
    image_scopes = {image.get("source"): image.get("segment", -1) for image in (state.get("images") or [])}
    segment_index = int(index or 0)
    # 参考视频/音频按轨道位置固定（视频1→参考视频1…），空轨也保留位置。
    video_lanes = list(enumerate(state.get("video_tracks") or []))
    audio_lanes = list(enumerate(state.get("audio_tracks") or []))
    source_map = {source["id"]: source for source in sources}
    entry = (queue.get("entries") or [])[index or 0] if queue.get("entries") else None
    segment_start = float(entry.get("source_start") or 0) if entry else 0.0
    seconds = (int(window["target_frames"]) / FPS if window else
               round(float(entry["seconds_float"]) * FPS) / FPS if entry else float(state.get("duration") or 0))
    # 参考视频帧数跟本段生成帧数 G 一致：C=0 为 17k+5 向上取整，潜桥为 L+C。
    # 多出的补充帧最终由 commit 裁掉；C>0 时窗口前移 C 帧，把上一段尾部作为参考上下文。
    reference_count = int((window or {}).get("frame_count") or (window or {}).get("target_frames") or frame_index(seconds))
    pre_roll = int((window or {}).get("pre_roll_frames") or 0)
    body_start = frame_index(segment_start)
    body_end = body_start + frame_index(seconds)
    if entry:
        body_end = min(body_end, frame_index(segment_start + float(entry["seconds_float"])))
    reference_start = (body_start - pre_roll) / FPS
    reference_end = body_end / FPS
    normalized = {}
    color_stats = {} if color_match else None
    def needed(slot):
        return used is None or slot in used
    values = []
    base = len(SEGMENT_FIXED_TYPES)
    # 参考图端口按图片加载位置固定编号：第 2 段的图应从“参考图2”口输出，
    # 其它段专属图/禁止输出的端口保持空，不能把后面的图往前顶。
    for port in range(MAX_IMAGE_PORTS):
        slot = base + port
        source = image_sources[port] if port < len(image_sources) else None
        allowed = source is not None and image_scopes.get(source.get("id"), -1) in (-1, 0, segment_index + 1)
        values.append(assets.load_image_tensor(source) if needed(slot) and allowed else None)
    video_base = base + MAX_IMAGE_PORTS
    # 参考视频/参考视频音频帧数 = 本段生成帧数 G（C=0 为 17k+5 向上取整，潜桥为 L+C）；最终成片再裁回本段目标帧数。
    for index in range(MAX_VIDEO_PORTS):
        slot = video_base + index
        frames = None
        if needed(slot) and index < len(video_lanes) and seconds > 0:
            _, lane = video_lanes[index]
            # 参考视频按段窗口裁剪 + 统一缩放到最长边 REF_VIDEO_LONG_EDGE；
            # 视频轨静音只影响声音，不隐藏画面。
            if lane:
                stats = color_stats if color_stats is not None and not color_stats else None
                frames = _reference_video_window_frames(lane, source_map, reference_start,
                                                        reference_end, target_frames=reference_count,
                                                        body_start=body_start, cache=normalized, stats=stats)
                if stats:
                    stats["track"] = index + 1
        values.append(frames)
    audio_video_base = base + MAX_IMAGE_PORTS + MAX_VIDEO_PORTS
    for index in range(MAX_VIDEO_PORTS):
        slot = audio_video_base + index
        if needed(slot) and index < len(video_lanes) and seconds > 0:
            number, lane = video_lanes[index]
            # 参考视频音频同样按当前段窗口取该轨道的音轨（空隙补静音，轨道静音则全静音）。
            values.append(_reference_video_audio(state, number, lane, source_map,
                                                 reference_start, reference_end, reference_count,
                                                 cache=normalized) if lane else None)
        else:
            values.append(None)
    audio_base = base + MAX_IMAGE_PORTS + 2 * MAX_VIDEO_PORTS
    for index in range(MAX_AUDIO_PORTS):
        slot = audio_base + index
        # 参考音频口按轨道位置固定：音频1→参考音频1…。
        # 单条音频（未切分）整条复用给所有段作为音色参考；
        # 切成多段的音频按分段序号依次取对应片段（第1段取第1段音频…）。
        if needed(slot) and index < len(audio_lanes) and seconds > 0:
            number, lane = audio_lanes[index]
            if _lane_muted(state, "audio", number) or not lane:
                values.append(None)
                continue
            clips = sorted(lane, key=lambda item: clip_span(item)[0])
            if len(clips) == 1:
                pieces = _full_lane_audio_pieces(lane, source_map)
            else:
                clip = clips[segment_index % len(clips)]
                source = source_map[clip["source"]]
                pieces = [{"origin": source["origin"], "file": source["file"],
                           "src_in": float(clip["src_in"]), "src_out": float(clip["src_out"])}]
            values.append(read_pieces(pieces) if pieces else None)
        else:
            values.append(None)
    if media_details is not None and (mask_size or color_match):
        primary = (entry or {}).get("video_source") or {}
        primary_key = (primary.get("origin"), primary.get("file"))
        primary_index, primary_lane = next(((number, lane) for number, lane in video_lanes if primary and any(
            (source.get("origin"), source.get("file")) == primary_key
            for _, source, _, _, _ in _reference_spans(lane, source_map, body_start, body_end))), (None, None))
        if mask_size and mask_source and primary_lane and (mask_source.get("origin"), mask_source.get("file")) == primary_key:
            media_details["mask_frames"] = _reference_video_window_frames(
                primary_lane, source_map, reference_start, reference_end, reference_count,
                body_start=body_start, cache=normalized, canvas=mask_size)
        if color_match:
            if not color_stats and primary_lane:
                _, source, _, _, first = next(_reference_spans(primary_lane, source_map, body_start, body_end))
                try:
                    source = _normalized_reference(source, normalized)
                    frame = _video_frames(source, start_time=first / FPS, end_time=(first + 1) / FPS,
                                          long_edge=REF_VIDEO_LONG_EDGE)
                    if frame is not None and len(frame):
                        color_stats = reference_frame_stats(frame[0], source, first)
                        color_stats["track"] = primary_index + 1
                except Exception as exc:
                    print("[AD-颜色] 读取本段参考色彩失败：%r" % (exc,))
            media_details["color_match_reference"] = color_stats or None
    return values


# 清单库只暴露分段清单；参考口改由当前段动态挂载。


def _config_dict(raw):
    if isinstance(raw, dict):
        return raw
    try:
        data = json.loads(raw or "{}")
        return data if isinstance(data, dict) else {}
    except (TypeError, ValueError):
        return {}


def _merged_queue_config(queue_state, h3_editor_state):
    """主清单与编辑器状态的合并规则：与 build 完全一致，IS_CHANGED 复用同一份结果。"""
    main_cfg = _config_dict(queue_state)
    editor_cfg = _config_dict(h3_editor_state)
    # 编辑器实际状态可能保存在 h3_editor_state；空清单时用它，且补齐缺失的单段上限
    if not main_cfg.get("version") and editor_cfg.get("version"):
        main_cfg = editor_cfg
    elif editor_cfg.get("max_frames") is not None and main_cfg.get("max_frames") is None:
        main_cfg = dict(main_cfg, max_frames=editor_cfg["max_frames"])
    return main_cfg


class AidenH3AudioQueue:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "queue_state": ("STRING", {"default": EMPTY_TIMELINE, "multiline": True}),
        }, "optional": {"h3_editor_state": ("STRING", {"default": "{}"})},
            "hidden": {"prompt": "PROMPT", "unique_id": "UNIQUE_ID"}}

    RETURN_TYPES = ("H3_AUDIO_QUEUE",)
    RETURN_NAMES = ("分段清单",)
    FUNCTION = "build"
    CATEGORY = CATEGORY
    DESCRIPTION = ("素材清单库：统一导入图片/视频/音频（按钮或素材路径），在时间线上裁剪、切分、合并、删除与换轨；"
                  "按每段长度/分段数「应用分段」，可调单段上限与每帧数；支持「重新加载」input 素材库与「生成加载」output 生成结果。"
                  "只输出「分段清单」，图片与音频由当前段节点逐轮输出，画面/音色预览在本节点时间线内完成。")

    @classmethod
    def IS_CHANGED(cls, queue_state, h3_editor_state="{}", prompt=None, unique_id=None):
        # build 会在主清单为空时改用 h3_editor_state、并补齐缺失的单段上限；
        # 缓存键必须用同一份合并配置，否则只改编辑器状态/上限时不会重新执行。
        # timeline_identity 不包含 max_frames（它只描述素材时间线），这里单独并入。
        merged = _merged_queue_config(queue_state, h3_editor_state)
        raw = json.dumps(merged, ensure_ascii=False)
        return digest([timeline_identity(raw), merged.get("max_frames")])

    def build(self, queue_state, h3_editor_state="{}", prompt=None, unique_id=None):
        import json as _json
        main_cfg = _merged_queue_config(queue_state, h3_editor_state)
        raw = _json.dumps(main_cfg, ensure_ascii=False)
        assets.PREPARED_MEDIA.bind(str(unique_id or "material-queue"), timeline_identity(raw))
        plan = timeline_plan(raw)  # old queue formats auto-migrate to timeline
        state = plan["timeline"]
        queue = {"entries": plan["entries"], "timeline": state, "references": reference_pack(state)}
        # ref image/av ports removed; media flows via current-segment outputs
        return (queue,)


class AidenH3VideoQueue:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"video_queue_state": ("STRING", {"default": '{"segments":[]}', "multiline": True})}}

    RETURN_TYPES = ("H3_VIDEO_QUEUE",)
    RETURN_NAMES = ("video_queue",)
    FUNCTION = "build"
    CATEGORY = CATEGORY
    DESCRIPTION = "（可选/进阶）显式视频分段清单 JSON。当前段节点已改为只接素材清单库的「分段清单」；视频取段请在时间线编排。本节点保留供导出/调试。"

    @classmethod
    def IS_CHANGED(cls, video_queue_state):
        return digest(video_identity(video_queue_state))

    def build(self, video_queue_state):
        return (video_plan(video_queue_state),)


def _looks_like_run_token(value) -> bool:
    """True only for real reserve tokens (uuid4 hex). Reject widget leaks (0.8, 9:16, ...)."""
    if value is None:
        return False
    text = str(value).strip()
    if not text:
        return False
    # megapixels / ints / floats accidentally shifted into run_token
    if re.fullmatch(r"[-+]?\d+(?:\.\d+)?", text):
        return False
    # aspect ratio labels
    if ":" in text or "Landscape" in text or "Portrait" in text or "Square" in text:
        return False
    # uuid4().hex is 32 hex chars; accept 16..64 hex for forward compat
    return bool(re.fullmatch(r"[0-9a-fA-F]{16,64}", text))


def _normalize_run_token(value) -> str:
    return str(value).strip() if _looks_like_run_token(value) else ""


def _safe_int(value, default=0) -> int:
    try:
        if isinstance(value, bool):
            return default
        if isinstance(value, (int, float)):
            return int(value)
        text = str(value).strip()
        if not text or not re.fullmatch(r"[-+]?\d+", text):
            return default
        return int(text)
    except (TypeError, ValueError):
        return default


def _safe_aspect(value, default=None):
    default = DEFAULT_ASPECT if default is None else default
    text = str(value or "").strip() or default
    if text in ASPECT_CHOICES:
        return text
    for choice in ASPECT_CHOICES:
        if text.startswith(choice.split()[0]) or choice.startswith(text):
            return choice
    return default


def _safe_megapixels(value, default=None):
    default = DEFAULT_MEGAPIXELS if default is None else default
    text = str(value if value is not None else default).strip()
    choices = [str(x) for x in MEGAPIXEL_CHOICES]
    if text in choices:
        return text
    try:
        num = float(text)
    except (TypeError, ValueError):
        return str(default)
    # snap to nearest declared choice
    best = min(choices, key=lambda c: abs(float(c) - num))
    return best




def _original_source(source):
    """尽量切回原始文件（视频/音频的 original 记录），避免读取规范化副本。"""
    source = dict(source or {})
    original = source.get("original") or {}
    for key in ("origin", "file", "name"):
        if original.get(key):
            source[key] = original[key]
    return source


def _decode_audio(source):
    """解码音频为 (声道, 采样点) 数组，保留原始采样率，不做有损转换。"""
    from .audio import load_audio
    data = load_audio(assets.resolve_source(_original_source(source)))
    return data["waveform"][0].cpu().numpy(), int(data["sample_rate"])


def _video_original_audio(entry, state=None):
    """源音频回退：没有音频轨素材时输出当前段关联视频轨素材的裁后原声。

    2026-11-02 用户确认 B 方案：源音频优先音频轨素材；没有音频轨时回退视频自带原声，
    视频本身无音轨仍返回 None（不能凭空造声音）。源素材口按既有规则不受轨道静音影响。
    """
    source = entry.get("source_video") or entry.get("video_source")
    if not source:
        return None
    try:
        path = assets.resolve_source(_original_source(source))
        if not assets.has_audio_stream(path):
            return None
    except Exception:
        return None
    retained = source.get("retained_range")
    if not retained and state and source.get("id"):
        from .timeline import retained_source_range
        retained = retained_source_range(state, source["id"])
    if retained:
        first, last = float(retained[0]), float(retained[1])
    else:
        first = float(source.get("src_in") or 0)
        last = float(source.get("src_out") or source.get("duration") or 0)
    if last - first <= 1e-6:
        return None
    from .audio import load_audio_range
    return load_audio_range(path, first, last)


def _original_audio(entry, state=None, index=0):
    """源音频输出关联素材的裁后目标；整段复用，不按生成段截断。

    优先顺序：循环主音频 → 当前段关联的音频轨素材 → 视频轨素材自带原声（0.4.51 起）。
    """
    from .audio import load_audio
    from .timeline import retained_source_range
    if state:
        priority = _priority_of_kind(state, "audio")
        if priority:
            return load_audio(assets.resolve_source(_original_source(priority[index % len(priority)])))
        sources = {source["id"]: source for source in state.get("sources") or []}
        start = float(entry.get("source_start") or 0)
        end = start + float(entry["seconds_float"])
        for lane in state.get("audio_tracks") or []:
            for clip in lane:
                lo, hi = clip_span(clip)
                if min(end, hi) - max(start, lo) > .5 / FPS:
                    from .audio import load_audio_range
                    first, last = retained_source_range(state, clip["source"], clip)
                    return load_audio_range(assets.resolve_source(_original_source(sources[clip["source"]])), first, last)
        return _video_original_audio(entry, state)
    pieces = []
    for track in (entry.get("timeline_audio") or {}).get("tracks", []):
        pieces.extend(track or [])
    if pieces:
        from .audio import load_audio, resolve_entry
        return load_audio(resolve_entry(pieces[0]))
    return _video_original_audio(entry, None)


def _source_video(source, state):
    from .timeline import retained_source_range
    retained = source.get("retained_range") or retained_source_range(state, source.get("id"))
    first, duration = (retained[0], retained[1] - retained[0]) if retained else (0.0, 0.0)
    return assets.load_video_object(_original_source(source), start_time=first, duration=duration, normalize=False)


class AidenH3Segment:
    """纯数据节点：只从素材清单库的分段清单取当前段数据，不做界面/生成。

    所有输出口都是可预览的数据：文本、数值、图像、视频、音频；直接接
    「显示任意类型」/预览节点即可查看每个口的内容。
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {},
                "optional": {"queue": ("H3_AUDIO_QUEUE",),
                             "潜桥续接": (list(RELAY_CHOICES), {"default": "0 · 关闭", "tooltip": "0=关闭潜桥；5/22/39=开启潜桥，C为上下文帧数。开启后每段输出=17的倍数，H3生成=输出+C，裁头=C"}),
                             "aspect_ratio": (list(ASPECT_CHOICES), {"default": DEFAULT_ASPECT}),
                             "megapixels": ("FLOAT", {"default": 0.5, "min": 0.05, "max": 64.0, "step": 0.1, "tooltip": "百万像素，自由填写"}),
                             "multiple": ("INT", {"default": 32, "min": 1, "max": 64, "tooltip": "倍数（宽高按此倍数对齐）"}),
                             "正向提示词": ("STRING", {"multiline": True, "default": ""}),
                             "统一提示词前缀": ("STRING", {"multiline": True, "default": ""}),
                             "反向提示词": ("STRING", {"multiline": True, "default": ""}),
                             "段索引": ("INT", {"default": -1, "min": -1, "max": 9999, "tooltip": "独立模式下的起始段索引；-1 表示 0，由分段循环自动设置"}),
                             "ref_counts": ("STRING", {"default": "{}", "tooltip": "动态参考口数量（前端自动写入）"}),
                             "潜桥模式": (list(RELAY_MODES), {"default": "关闭", "tooltip": "关闭=原有逐段生成；普通多参考=自选17倍数段长做潜桥；动作迁移预卷=源视频动作迁移/人物替换，前置预卷后裁头，保持65帧等任意目标帧数"}),
                             "潜桥上下文帧数": (list(RELAY_CONTEXTS), {"default": "22", "tooltip": "旧参数：兼容旧工作流，前端会自动迁移"}),
                             "潜桥重锚定": (list(RELAY_REANCHOR_CHOICES), {"default": "0 · 不重锚定", "tooltip": "每 N 段清一次潜桥上下文（第 N、2N…段不读取上一段 latent，从当前段源画面/提示词重新锚定）；可缓解长链越跑越糊/越跑越暗。0=关闭"}),
                             "参考首帧色彩匹配": (list(_COLOR_MATCH_CHOICES), {"default": "0 · 关闭", "tooltip": "按本段参考视频首帧的亮度/白平衡校正生成画面（整段固定偏移，不逐帧自动增益，不做 VAE 往返）；0=关闭"})},
                "hidden": {"prompt": "PROMPT", "unique_id": "UNIQUE_ID", "extra_pnginfo": "EXTRA_PNGINFO"}}

    RETURN_TYPES = SegmentOutputTypes(SEGMENT_FIXED_TYPES + tuple(
        "IMAGE" if name.startswith("ref_image") or (name.startswith("ref_video_") and not name.startswith("ref_video_audio"))
        else "AUDIO" if name.startswith("ref_audio") or name.startswith("ref_video_audio") else "IMAGE"
        for name in SEGMENT_REF_NAMES) + SEGMENT_EXTRA_TYPES)
    RETURN_NAMES = SEGMENT_FIXED_NAMES + SEGMENT_REF_NAMES + SEGMENT_EXTRA_NAMES
    FUNCTION = "select"
    CATEGORY = CATEGORY
    DESCRIPTION = ("当前段（纯数据）：按分段清单的当前轮输出宽度/高度、正向/反向提示词、本轮图像/音频/视频与参考素材端口；"
                  "提示词用 # 每行一段并按轮取用。不接分段清单时进入独立模式：按提示词 # 段落数分段，可用段索引指定轮次，"
                  "并配合预览与分段循环逐段执行。")

    @classmethod
    def IS_CHANGED(cls, queue=None, aspect_ratio=DEFAULT_ASPECT, megapixels=DEFAULT_MEGAPIXELS,
                   multiple=32, prompt=None, unique_id=None, **kwargs):
        # select除素材输出外还预约任务、建立token/epoch；整节点不能当作纯素材缓存。
        # 素材解码缓存独立保留，运行上下文须在每次执行中重新取得。
        from .cache_identity import segment_changed
        return segment_changed(queue=queue, aspect_ratio=aspect_ratio, megapixels=megapixels,
                               multiple=multiple, inputs=kwargs)

    def select(self, queue=None, aspect_ratio=DEFAULT_ASPECT, megapixels=DEFAULT_MEGAPIXELS, multiple=32,
               prompt_text="", prompt_prefix="", negative_prompt="", prompt=None, unique_id=None, extra_pnginfo=None, **kwargs):
        body_text = kwargs.get("正向提示词", prompt_text)
        negative_text = kwargs.get("反向提示词", negative_prompt)
        prefix_text = kwargs.get("统一提示词前缀", prompt_prefix)
        queue = queue or {}
        assets.PREPARED_MEDIA.trim()
        state = output_timeline(queue.get("timeline") or {})
        entries = queue.get("entries") or []
        if not entries and state.get("generation_clips") == []:
            raise ValueError("时间线已清空，请导入素材或重新应用分段后生成")
        if not entries and any(lane for key in ("video_tracks", "audio_tracks")
                               for lane in (queue.get("timeline") or {}).get(key) or []):
            raise ValueError("所有音视频轨道均已隐藏，请先开启需要输出的轨道")
        if not entries:
            # 独立模式：不接素材清单库时，按正向提示词的 # 段落数决定分段数量
            from .prompts import select_prompt
            body = str(body_text or "").strip()
            paragraphs = [line.lstrip("#").strip() for line in body.splitlines()
                          if line.lstrip().startswith("#") and line.lstrip("#").strip()]
            count = len(paragraphs) or 1
            width, height = resolve_canvas(aspect_ratio, megapixels, int(multiple))
            round_index = int(kwargs.get("段索引", -1))
            if round_index < 0:
                round_index = 0
            round_index = round_index % count if count else 0
            preview = {
                "session_id": "AD-H3-standalone", "index": round_index, "total": count,
                "workflow_task_id": (prompt.get(str(unique_id),{}).get('_meta') or {}).get('ad_task_id') if isinstance(prompt,dict) else None,
                "segments": [{"index": i, "status": "ready"} for i in range(count)],
                "standalone": True,
            }
            try:
                positive = select_prompt(body, round_index)
            except ValueError:
                positive = body
            prefix = str(prefix_text or "").strip()
            positive = "\n".join(part for part in (prefix, str(positive or "").strip()) if part)
            try:
                negative = select_prompt(negative_text, round_index)
            except ValueError:
                negative = str(negative_text or "")
            fixed = (preview, round_index, None, 243, float(FPS), int(width), int(height),
                     positive, negative, None, None, None, None, None)
            try:
                import json as _json
                counts = _json.loads(kwargs.get("ref_counts") or "{}")
            except Exception:
                counts = {}
            sizes = [MAX_IMAGE_PORTS, MAX_VIDEO_PORTS, MAX_VIDEO_PORTS, MAX_AUDIO_PORTS]
            wanted = [counts.get("image"), counts.get("video"), counts.get("video_audio"), counts.get("audio")]
            total = 0
            for size, count in zip(sizes, wanted):
                total += min(size, count) if isinstance(count, int) and count >= 0 else size
            return fixed + tuple([None] * total) + (None,)
        declared_session = ((prompt.get(str(unique_id)) or {}).get('inputs') or {}).get('session_id') if isinstance(prompt,dict) else None
        session_id = str(kwargs.get("session_id") or declared_session or state.get("session_id") or (f"AD-H3-{unique_id}" if unique_id is not None else "AD-H3-session"))
        fps = float(FPS)
        validate_fps(fps)
        aspect_ratio = str(aspect_ratio or DEFAULT_ASPECT)
        megapixels = str(megapixels or DEFAULT_MEGAPIXELS)
        width, height = resolve_canvas(aspect_ratio, megapixels, int(multiple))
        settings = loop_settings(state)
        settings["reanchor_every"] = int(state.get("reanchor_every") or 0)
        settings["overlap_frames"] = 0
        settings["guard_frames"] = 0
        base_prompt = str(state.get("prompt") or "")
        graph_prompt = prompt if isinstance(prompt, dict) else {}
        signature, template = template_for(graph_prompt, unique_id, extra_pnginfo)
        signatures = segment_signatures(graph_prompt, unique_id, entries, fps, None)
        task_id=(graph_prompt.get(str(unique_id),{}).get('_meta') or {}).get('ad_task_id')
        if task_id:
            try:
                owner=STORE.read(session_id).get('workflow_task_id')
                if owner and owner!=task_id:raise ValueError("当前进度属于其它工作流的任务，已拒绝混用；请重新打开本工作流后再试。")
            except FileNotFoundError:
                pass
        STORE.sync(session_id, entries, signatures, template, keep_completed=True)
        if task_id:
            with STORE.lock(session_id):
                owned=STORE._load(session_id)
                if not owned.get('workflow_task_id'):owned['workflow_task_id']=task_id;STORE._save(owned)
        run_token = STORE.ensure_run_token(session_id, kwargs.get("run_token", ""))
        session = STORE.begin(session_id, run_token)
        executing = compat.executing_context()
        if executing is None:
            # ComfyUI 私有执行上下文接口缺失（版本差异）：跳过进度监听，不影响本次生成。
            if "executing_context" not in _WARNED_ONCE:
                _WARNED_ONCE.add("executing_context")
                print("AidenH3: 未找到 ComfyUI 执行上下文接口，已跳过进度监听（不影响本次生成）")
        elif not session["active"].get("prompt_id"):
            import asyncio
            from server import PromptServer
            from .engine import watch_prompt
            with STORE.lock(session_id):
                current = STORE._load(session_id)
                current["active"]["prompt_id"] = executing.prompt_id
                STORE._save(current)
            asyncio.run_coroutine_threadsafe(watch_prompt(session_id, run_token, executing.prompt_id), PromptServer.instance.loop)
        index = int(session["active"]["index"])
        entry = entries[index]
        # 未显式设置单段上限时按统一默认 362 计算窗口（与时间线/前端默认一致）。
        settings["max_frames"] = int(state.get("max_frames") or DEFAULT_SEGMENT_MAX_FRAMES)
        batch_source = entry.get("batch_source")
        if batch_source:
            metadata = entry.get("batch_metadata") or {}
            fps = float(metadata.get("fps") or FPS)
            width, height = int(metadata.get("width") or width), int(metadata.get("height") or height)
            frames = int(metadata.get("frames") or round(entry["seconds_float"] * fps))
            window = {"start_frame": 0, "source_frames": frames, "target_frames": frames, "frame_count": frames,
                      "overlap_frames": 0, "history_frames": 0, "reanchor": False}
        else:
            window = h3_window(entries, index, **settings)
        # ---- 潜桥续接：单下拉 0/5/22/39。0=关闭；>0=开启，输出 L=17n，G=L+C，裁头=C。 ----
        relay_raw = kwargs.get("潜桥续接")
        relay_context = _relay_choice_value(relay_raw)
        relay_active = bool(relay_context > 0 and not batch_source)
        relay_pre_roll = 0
        relay_trim_frames = 0
        relay_reanchor_every = 0
        relay_reanchor = False
        relay_run_id = ""
        color_match_blend = _color_match_blend(kwargs.get("参考首帧色彩匹配"))
        relay_save_run_id = ""
        relay_stage_index = int(index)
        if relay_active:
            source_target = int(window["target_frames"])
            relay_target = _relay_targets_for_entries(entries)[index]
            if relay_target < 17:
                raise ValueError("潜桥开启时每段至少需要 17 帧目标长度")
            if relay_target <= relay_context:
                raise ValueError(f"潜桥上下文 C={relay_context} 必须小于本段输出帧数 {relay_target}")
            plan = relay_plan(relay_target, relay_context)
            # 潜桥输出 L 与上下文 C 都确定后，必须按最终生成帧数 G=L+C 重新核对单段上限，
            # 不能只沿用 h3_window 里按 L 算的旧上限（否则 G 会静默超限）。
            limit_frames = int(settings.get("max_frames") or DEFAULT_SEGMENT_MAX_FRAMES)
            need_frames = int(plan["generation_frames"])
            if need_frames > limit_frames:
                raise ValueError(
                    f"潜桥开启后本段需要生成 {need_frames} 帧（输出 {relay_target} 帧 + 上下文 C={relay_context} 帧），"
                    f"超过单段上限 {limit_frames} 帧；请缩短本段、降低上下文 C，或提高单段上限后再运行。")
            window["target_frames"] = relay_target
            window["frame_count"] = plan["generation_frames"]
            relay_trim_frames = plan["trim_frames"]
            relay_pre_roll = plan["pre_roll_frames"]
            relay_run_id, relay_save_run_id, relay_stage_index = _relay_identity(session_id, session, index)
            # 分段重锚定：第 N、2N…段跳过上一段 latent，从当前段源画面/提示词重新起锚。
            relay_reanchor_every = _relay_reanchor_interval(kwargs.get("潜桥重锚定"))
            relay_reanchor = relay_reanchors(relay_reanchor_every, int(index))
            if relay_reanchor:
                print("[AD-潜桥] 第 %d 段重锚定：跳读上一段 latent，从当前段重新起锚" % (int(index) + 1))
            # 上一段的稳定 stage 是桥接的唯一父 latent 来源；缺失说明存盘/提升还没完成，
            # 必须显式报错，不能带着空父 latent 静默生成（预览子会话用父段号校验）。
            relay_label = str((session.get("preview_parent") or {}).get("group_label") or "")
            _require_parent_relay_stage(relay_run_id, relay_stage_index, relay_label, reanchor=relay_reanchor)
        used = None
        if isinstance(prompt, dict) and unique_id is not None:
            slots = set()
            uid = str(unique_id)
            for node in prompt.values():
                for value in (node.get("inputs") or {}).values():
                    if isinstance(value, list) and len(value) == 2 and str(value[0]) == uid and isinstance(value[1], int):
                        slots.add(value[1])
            if slots:
                used = slots
        need = lambda slot: used is None or slot in used
        try:
            import json as _json
            counts = _json.loads(kwargs.get("ref_counts") or "{}")
        except Exception:
            counts = {}
        sizes = [MAX_IMAGE_PORTS, MAX_VIDEO_PORTS, MAX_VIDEO_PORTS, MAX_AUDIO_PORTS]
        wanted = [counts.get("image"), counts.get("video"), counts.get("video_audio"), counts.get("audio")]
        active_sizes = [min(size, count) if isinstance(count, int) and count >= 0 else size
                        for size, count in zip(sizes, wanted)]
        extra_slot = len(SEGMENT_FIXED_TYPES) + sum(active_sizes)
        timeline_video = entry.get("video_source")
        if timeline_video is not None and not batch_source:
            mapped = {"start_frame": round(timeline_video["src_in"] * FPS),
                      "end_frame": round(timeline_video["src_out"] * FPS)}
            window.update(video_window(mapped, window["target_frames"], window["overlap_frames"]))
        window["pre_roll_frames"] = int(relay_pre_roll)
        prompts = state.get("prompts") or []
        state_prompt = str(prompts[index % len(prompts)]) if prompts else ""
        context = {"session_id": session_id, "token": run_token, "index": index,
                   "round_no": int(session.get("round_no", 1) or 0),
                   "round_id": (session.get("execution_round") or {}).get("id", ""),
                   "workflow_task_id": session.get('workflow_task_id') or (graph_prompt.get(str(unique_id),{}).get('_meta') or {}).get('ad_task_id'),
                   "segment_key": session["active"].get("segment_key"),
                   "entry_snapshot": session["active"].get("entry_snapshot"),
                   "epoch": int(session.get("epoch", 0)),
                   "fps": fps, "width": width, "height": height,
                   "aspect_ratio": aspect_ratio,
                   "megapixels": float(megapixels),
                   "sequence_mode": bool(state.get("sequence_mode", True)),
                   "references": reference_manifest(graph_prompt, unique_id, index),
                   "timeline_references": queue.get("references") or None,
                   "video_segment": None if timeline_video is None else timeline_video.get("name"),
                   "video_segment_file": None if timeline_video is None else timeline_video["file"],
                   "prompt_text": state_prompt,
                   "relay_run_id": relay_run_id,
                   "relay_save_run_id": relay_save_run_id,
                   "relay_stage_index": relay_stage_index,
                   # 裁头必须从 context 拿到 C，否则内部裁头会静默按 0 帧处理（v0.5.4 音频/画面裁切失效的根因）。
                   "relay_trim_frames": int(relay_trim_frames),
                   "relay_context_frames": int(relay_context),
                   "relay_pre_roll_frames": int(relay_pre_roll),
                   "relay_reanchor": bool(relay_reanchor),
                   "relay_reanchor_every": int(relay_reanchor_every),
                   "color_match_blend": float(color_match_blend),
                   **window}
        source_video = _source_video(entry["source_video"], state) if (need(12) and entry.get("source_video")) else None
        original_audio = _original_audio(entry, state, index) if need(13) and not batch_source else None
        round_image = _round_image(state, index) if need(9) and not batch_source else None
        # 时间线音频必须按本段范围取样，不能每一轮重播整条原音频。
        timeline_audio = entry.get("timeline_audio", {}).get("tracks") or []
        use_segment_audio = any(state.get("audio_tracks") or []) and not any(source.get("priority") for source in state.get("sources", []))
        round_audio = (_segment_audio(entry, window) if use_segment_audio else _round_audio(state, index)) if need(10) and not batch_source else None
        if round_audio is None and need(10) and not batch_source:
            round_audio = _segment_audio(entry, window)
        segment_source = (entry.get("video_source") if batch_source else _segment_video_source(state, index, entry)) if need(2) or need(11) or need(extra_slot) else None
        first_frame = (assets.video_first_frame(segment_source, float(segment_source["src_in"]), float(segment_source["src_out"]))
                       if need(2) and segment_source is not None else None)
        round_video = (_round_video(segment_source) if not batch_source else
                       assets.load_video_object(_original_source(batch_source), normalize=False) if batch_source["kind"] == "video" else None) if need(11) else None
        if batch_source:
            if batch_source["kind"] == "image" and need(9):
                round_image = assets.load_image_tensor(batch_source, normalize=False)
            if (need(10) or need(13)) and (batch_source["kind"] == "audio" or batch_source["kind"] == "video" and assets.has_audio_stream(assets.resolve_source(batch_source))):
                import torch
                wave, rate = _decode_audio(batch_source)
                raw_audio = {"waveform": torch.from_numpy(wave).unsqueeze(0), "sample_rate": rate}
                if need(10): round_audio = raw_audio
                if need(13) and batch_source["kind"] in {"audio", "video"}: original_audio = raw_audio
        try:
            from .prompts import select_prompt
            try:
                body = select_prompt(body_text, index)
            except ValueError:
                body = str(body_text or "")
            prefix = str(prefix_text or "").strip()
            positive = "\n".join(part for part in (prefix, str(body or "").strip()) if part)
            try:
                negative = select_prompt(negative_text, index)
            except ValueError:
                negative = str(negative_text or "")
        except Exception:
            positive = str(body_text or "")
            negative = str(negative_text or "")
        if session.get("preview_parent"):
            context["preview_parent_session"] = session["preview_parent"]["session_id"]
        context["prompt_text"] = positive
        if batch_source: context["batch_mode"] = "source_files"
        fixed = (context, index, first_frame, window["frame_count"], fps,
                 width, height, positive, negative,
                 round_image, round_audio, round_video, source_video, original_audio)
        extra_slot = len(SEGMENT_FIXED_TYPES) + sum(active_sizes)
        schema_used = None
        if used is not None:
            schema_used = {slot for slot in used if slot < len(SEGMENT_FIXED_TYPES)}
            compact = canonical = len(SEGMENT_FIXED_TYPES)
            for size, active in zip(sizes, active_sizes):
                schema_used.update(canonical + part for part in range(active) if compact + part in used)
                compact += active
                canonical += size
        media_details = {}
        refs = list(_timeline_material_values(
            queue, schema_used, index, window, media_details=media_details,
            mask_size=(height, width) if need(extra_slot) and not batch_source else None,
            mask_source=segment_source, color_match=bool(color_match_blend and not batch_source)))
        context["color_match_reference"] = media_details.get("color_match_reference")
        out_refs = []
        offset = 0
        for size, count in zip(sizes, wanted):
            if isinstance(count, int) and count >= 0:
                out_refs.extend(refs[offset:offset + min(size, count)])
            else:
                out_refs.extend(refs[offset:offset + size])
            offset += size
        out_refs.extend(refs[offset:])
        extra_frames = media_details.get("mask_frames")
        if "mask_frames" not in media_details and need(extra_slot) and segment_source is not None and not batch_source:
            if relay_active:
                extra_frames = _relay_mask_frames(segment_source, window, width, height, relay_pre_roll)
            else:
                extra_frames = _canvas_mask_frames(segment_source, window, width, height)
        return fixed + tuple(out_refs) + (extra_frames,)


class AidenH3SegmentMedia:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"context": ("H3_LOOP_CONTEXT",)},
                "optional": {"video_frames": ("IMAGE",), "mask": ("MASK",),"source_fps":("FLOAT",{"default":24.0})}}

    RETURN_TYPES = ("IMAGE", "MASK")
    RETURN_NAMES = ("video_frames", "mask")
    FUNCTION = "select"
    CATEGORY = CATEGORY
    DESCRIPTION = "外部视频与遮罩按同一起止范围取段；短于 H3 对齐帧数时重复末帧。不检测、不跟踪、不扩张遮罩。"

    def select(self, context, video_frames=None, mask=None,source_fps=24.0):
        import torch
        validate_fps(source_fps)
        start, count, target = context["start_frame"], context["source_frames"], context["frame_count"]
        if mask is not None and video_frames is None:
            raise ValueError("角色替换需要同时连接外部视频和对应遮罩")
        if video_frames is not None and mask is not None and mask.shape[0] not in (1, video_frames.shape[0]):
            raise ValueError("视频与遮罩帧数不一致；静态遮罩可为单帧")
        if video_frames is not None and mask is not None and tuple(video_frames.shape[1:3]) != tuple(mask.shape[1:3]):
            raise ValueError("视频与遮罩宽高不一致，请使用同一源视频制作遮罩")

        def cut(value, static=False):
            if value is None:
                return None
            if static:
                return value[:1].expand(target, *value.shape[1:])
            if start + count > value.shape[0]:
                raise ValueError(f"源素材帧数不足：需要 {start + count} 帧，实际 {value.shape[0]} 帧")
            part = value[start:start + count]
            if part.shape[0] < target:
                part = torch.cat((part, part[-1:].expand(target - part.shape[0], *part.shape[1:])), dim=0)
            return part

        return cut(video_frames), cut(mask, mask is not None and mask.shape[0] == 1)


class AidenH3EditMask:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"latent": ("LATENT",), "source_latent": ("LATENT",), "mask": ("MASK",)}}

    RETURN_TYPES = ("LATENT",)
    RETURN_NAMES = ("latent",)
    FUNCTION = "apply"
    CATEGORY = CATEGORY
    DESCRIPTION = "把外部视频编码与遮罩适配为 H3 音视频潜变量。白色区域允许替换，黑色区域保留源视频。遮罩制作、VAE 编码与原生采样保持外置。"

    def apply(self, latent, source_latent, mask):
        from .editing import apply_edit_mask
        return (apply_edit_mask(latent, source_latent, mask),)


class AidenH3References:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "segment": ("INT", {"default": 0, "min": 0, "tooltip": "0 设置全局参考；1 起设置指定段覆盖"}),
            "mode": (["inherit", "custom", "none"], {"default": "custom"}),
        }, "optional": {"previous": ("H3_REFERENCE_PACK",), **{f"image{i}": ("IMAGE",) for i in range(1, 5)}}}

    RETURN_TYPES = ("H3_REFERENCE_PACK",)
    RETURN_NAMES = ("references",)
    FUNCTION = "pack"
    CATEGORY = CATEGORY

    def pack(self, segment, mode, previous=None, **images):
        previous = previous or {"global": {}, "overrides": {}}
        result = {"global": previous["global"], "overrides": dict(previous["overrides"])}
        entry = {"mode": mode, "images": {k: v for k, v in images.items() if v is not None}}
        if mode == "custom" and not entry["images"]:
            raise ValueError("自定义参考需要至少连接一张图片")
        if segment == 0:
            result["global"] = entry if mode != "inherit" else previous["global"]
        else:
            result["overrides"][segment - 1] = entry
        return (result,)


class AidenH3PickReferences:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"context": ("H3_LOOP_CONTEXT",), "references": ("H3_REFERENCE_PACK",)}}

    RETURN_TYPES = ("IMAGE", "IMAGE", "IMAGE", "IMAGE", "STRING")
    RETURN_NAMES = ("image1", "image2", "image3", "image4", "mode")
    FUNCTION = "pick"
    CATEGORY = CATEGORY

    def pick(self, context, references):
        pack = context.get("timeline_references")
        if pack and (pack.get("global") or (not context.get("reanchor") and context["index"] in pack.get("overrides", {}))):  # 图片池有有效指定时优先
            override = None if context.get("reanchor") else pack.get("overrides", {}).get(context["index"])
            chosen = override if override is not None else pack.get("global", [])
            images = [assets.load_image_tensor(source) for source in chosen[:4]]
            return *(images + [None] * (4 - len(images))), ("custom" if images else "none")
        entry = None if context.get("reanchor") else references["overrides"].get(context["index"])
        if entry is None or entry["mode"] == "inherit":
            entry = references["global"]
        images = entry.get("images", {}) if entry.get("mode") != "none" else {}
        return *(images.get(f"image{i}") for i in range(1, 5)), entry.get("mode", "none")


class AidenH3Commit:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"video": ("VIDEO",), "context": ("H3_LOOP_CONTEXT",)},
                "optional":{"strict_sync":("BOOLEAN",{"default":True,"tooltip":"默认开启：每段音频最多微调半帧，逐段贴合 24 FPS，消除累计偏差"}),
                            "prompt_text":("STRING",{"default":"","forceInput":True})},
                "hidden":{"prompt":"PROMPT"}}

    RETURN_TYPES = ("VIDEO",)
    RETURN_NAMES = ("video",)
    FUNCTION = "commit"
    OUTPUT_NODE = True
    CATEGORY = CATEGORY
    DESCRIPTION = "保存当前段的选用版本、保真素材与同步信息，并交给视频预览。"

    def commit(self, video, context, strict_sync=True, prompt_text="", prompt=None):
        session_id, token = context["session_id"], context["token"]
        directory = STORE.directory(session_id)
        directory.mkdir(parents=True, exist_ok=True)
        name = f"segment_{context['index'] + 1:04d}_{token}.mp4"
        path = directory / name
        if path.exists():
            raise ValueError("本次任务的结果已经保存过，无需重复提交；可在「分段预览」继续查看。")
        try:
            from .media import normalize_video
            current=STORE.read(session_id)
            normalized,details=normalize_video(video,context,current,directory,strict_sync=strict_sync)
            details["prompt_text"]=prompt_text
            details["seeds"]={key:{k:v for k,v in node.get("inputs",{}).items() if k in {"seed","noise_seed"}}
                              for key,node in (prompt or {}).items() if any(k in node.get("inputs",{}) for k in ("seed","noise_seed"))}
            normalized.save_to(str(path))
            state = STORE.commit(session_id, token, name, details, index=context.get("index"))
        except Exception as exc:
            STORE.fail(session_id, exc)
            raise
        from server import PromptServer
        PromptServer.instance.send_sync("aiden-h3/progress", {"session_id": session_id, "status": state["status"]})
        # ComfyUI 私有视频接口集中在 compat 适配层，版本升级只需改一处。
        saved = compat.video_from_file(path)
        saved._ad_h3_preview_record = {"origin": "session", "file": f"{session_id}/{name}"}
        return {"ui": {"h3_session": [session_id]}, "result": (saved,)}


class AidenH3VideoPreview:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {},
                "optional": {"media": ("*",),
                             "对比媒体": ("*",),
                             "context": ("H3_LOOP_CONTEXT",),
                             "video_oasis_ui": ("STRING", {"default":"{}"}),
                             "save_config": ("STRING", {"default":"{}"})},
                "hidden": {"prompt":"PROMPT", "extra_pnginfo":"EXTRA_PNGINFO"}}

    RETURN_TYPES = ("*",)
    RETURN_NAMES = ("media",)
    FUNCTION = "preview"
    OUTPUT_NODE = True
    CATEGORY = CATEGORY
    DESCRIPTION = "分段预览：可接图像、音频、视频；分段、合并条与版本对比共用同一播放器。"

    def preview(self, media=None, context=None, video_oasis_ui="{}", save_config="{}", prompt=None, extra_pnginfo=None, 对比媒体=None, compare_media=None):
        compare_media = 对比媒体 if 对比媒体 is not None else compare_media
        primary_role = "A"
        # 媒体A为空、媒体B有内容时：把 B 当作主媒体处理（A 可以空着）
        if media is None and compare_media is not None:
            media, compare_media = compare_media, None
            primary_role = "B"
        try:
            import json as _json
            preview_config = _json.loads(save_config) if isinstance(save_config, str) and save_config.strip() else {}
        except Exception:
            preview_config = {}
        only_b = bool(preview_config.get("only_b"))
        if compare_media is not None and only_b:
            # 只保存B：完全跳过A，只把B作为主结果记录
            save_index = 1
            if isinstance(context, dict) and context.get("token"):
                try:
                    save_index = int(context.get("index", 0)) + 1
                except Exception:
                    save_index = 1
            only = self._preview_single(compare_media, context, "{}", save_config, prompt, extra_pnginfo, save_index=save_index)
            for record in only["ui"].get("h3_preview") or []:
                record["role"] = "B"
                record["segment_index"] = save_index
            only["ui"]["h3_preview"] = only["ui"].get("h3_preview") or []
            only["result"] = (media,)
            return only
        result = self._preview_single(media, context, video_oasis_ui, save_config, prompt, extra_pnginfo)
        records = result["ui"].get("h3_preview") or []
        for record in records:
            record["role"] = primary_role
        if compare_media is not None:
            save_index = 1
            if isinstance(context, dict) and context.get("token"):
                try:
                    save_index = int(context.get("index", 0)) + 1
                except Exception:
                    save_index = 1
            second = self._preview_single(compare_media, None, "{}", save_config, prompt, extra_pnginfo, save_index=save_index)
            for record in second["ui"].get("h3_preview") or []:
                record["role"] = "B"
                record["segment_index"] = save_index
                if isinstance(context, dict) and context.get("session_id"):
                    record["session_id"] = context.get("preview_parent_session") or context["session_id"]
                    if context.get("segment_key"):
                        record["segment_key"] = context["segment_key"]
                records.append(record)
        result["ui"]["h3_preview"] = records
        return result

    def _preview_single(self, media=None, context=None, video_oasis_ui="{}", save_config="{}", prompt=None, extra_pnginfo=None, save_index=None, archive=True):
        from .storage import ROOT, STORE
        from .preview import preview_record
        import uuid
        import shutil
        directory = ROOT / "media"
        directory.mkdir(parents=True, exist_ok=True)
        record = None
        registered_name = None
        loop_context = context if isinstance(context, dict) and context.get("token") else None
        details = {}
        previous_record = getattr(media, "_ad_h3_preview_record", None)
        if loop_context and not previous_record:
            # 在编码/尾帧落盘前拦截真实旧结果；登记时仍再次检查，防止处理中被重置。
            current_epoch = STORE.read(loop_context["session_id"]).get("epoch", 0)
            if loop_context.get("epoch") is not None and int(loop_context["epoch"]) != int(current_epoch):
                raise ValueError("本次生成结果未纳入进度：生成期间进度被停止或清空重置。该结果尚未写入会话目录，请从当前段重新开始。")
        if hasattr(media, "save_to") or previous_record:
            if loop_context and not previous_record:
                from .media import normalize_video
                session_id = loop_context["session_id"]
                current = STORE.read(session_id)
                session_dir = STORE.directory(session_id)
                if loop_context.get("batch_mode") == "source_files":
                    normalized = media
                    rate = float(media.get_frame_rate())
                    details = {"frames": media.get_frame_count(), "fps": rate, "width": media.get_dimensions()[0],
                               "height": media.get_dimensions()[1], "seconds": media.get_frame_count() / rate}
                else:
                    normalized, details = normalize_video(media, loop_context, current, session_dir, strict_sync=True)
                name = f"segment_{loop_context['index'] + 1:04d}_{uuid.uuid4().hex[:8]}.mp4"
                normalized.save_to(str(session_dir / name))
                details.update(media_kind="video", prompt_text=loop_context.get("prompt_text", ""))
                for _key in ("relay_run_id", "relay_save_run_id", "relay_stage_index"):
                    if loop_context.get(_key) not in (None, ""):
                        details[_key] = loop_context.get(_key)
                STORE.register_result(session_id, name, details, context=loop_context)
                record = {"origin": "session", "file": f"{session_id}/{name}", "kind": "video"}
            else:
                record = {**preview_record(media), "kind": "video"}
        elif isinstance(media, dict) and "waveform" in media:
            import wave
            import numpy as np
            tensor = media["waveform"]
            if hasattr(tensor, "detach"):
                tensor = tensor.detach().cpu().float().numpy()
            data = np.asarray(tensor)
            while data.ndim > 2:
                data = data[0]
            if data.ndim == 1:
                data = data[None, :]
            pcm = (np.clip(data.T, -1.0, 1.0) * 32767).astype("int16")
            name = f"preview_{uuid.uuid4().hex}.wav"
            with wave.open(str(directory / name), "wb") as handle:
                handle.setnchannels(int(data.shape[0]))
                handle.setsampwidth(2)
                handle.setframerate(int(media.get("sample_rate") or 48000))
                handle.writeframes(pcm.tobytes())
            record = {"origin": "media", "file": name, "kind": "audio"}
        elif media is not None and hasattr(media, "detach"):
            import numpy as np
            from PIL import Image
            frame = media[0] if getattr(media, "ndim", 0) == 4 else media
            array = (frame.detach().cpu().float().numpy() * 255).clip(0, 255).astype(np.uint8)
            if array.ndim == 3 and array.shape[-1] == 1:
                array = array[..., 0]
            name = f"preview_{uuid.uuid4().hex}.png"
            image = Image.fromarray(array)
            image.save(directory / name)
            record = {"origin": "media", "file": name, "kind": "image"}
            # 缩略图只给前端预览用（原图仍按原路径归档，避免大图批量解码卡顿）
            try:
                import base64
                import io
                thumb = image.copy()
                thumb.thumbnail((320, 320))
                buffer = io.BytesIO()
                thumb.save(buffer, format="WEBP", quality=75, method=4)
                record["thumb_b64"] = base64.b64encode(buffer.getvalue()).decode("ascii")
                record["thumb_mime"] = "image/webp"
            except Exception:
                pass
        if loop_context and record is None:
            raise ValueError("当前段没有可记录的媒体结果，请检查预览输入连线")
        if record and loop_context:
            record["round_no"] = loop_context.get("round_no", 0)
            record["round_id"] = loop_context.get("round_id", "")
            record["session_id"] = loop_context.get("preview_parent_session") or loop_context["session_id"]
            if loop_context.get("segment_key"):
                record["segment_key"] = loop_context["segment_key"]
            if loop_context.get("preview_parent_session"):
                record["preview_only"] = True
            # 预览卡片据此识别所属段：点选预览后“重置抽卡”可只重抽该段。
            try:
                record["segment_index"] = int(loop_context.get("index", 0)) + 1
            except (TypeError, ValueError):
                pass
        if record and archive:
            task=(context or {}).get('workflow_task_id') if isinstance(context,dict) else None
            if not task and isinstance(extra_pnginfo,dict):
                task=((extra_pnginfo.get('workflow') or {}).get('extra') or {}).get('ad_segmented_task',{}).get('id')
            if task:record['task_id']=task
            if record["origin"] == "session":
                source = STORE.root / record["file"]
            else:
                source = directory / record["file"]
            if source.is_file():
                from . import dedupe
                from .save_target import save_with_template, configured_directory
                try:
                    config = json.loads(save_config) if isinstance(save_config, str) and save_config.strip() else {}
                except (ValueError, TypeError):
                    config = {}
                custom_dir = str(config.get("dir") or "").strip().strip('"').strip("'").strip()
                # 保存路径留空：按媒体类型归档到 ComfyUI/output/AD-Segmented-Loop-output/{图片,音频,视频}
                target_dir = configured_directory(custom_dir, record["kind"])
                if not target_dir.is_absolute():
                    raise ValueError("保存路径请填写绝对文件夹路径")
                target_dir.mkdir(parents=True, exist_ok=True)
                index = save_index if save_index is not None else (loop_context or {}).get("index", 0) + 1
                if custom_dir or str(config.get("template") or "").strip():
                    target = save_with_template(source, {**config, "dir": str(target_dir)}, index,
                                                prompt_text=(loop_context or {}).get("prompt_text", ""))
                else:
                    target = target_dir / source.name
                    # 用户可见归档独占复制，不与会话内部结果共享 inode，避免外部剪辑软件原地改写相互影响。
                    dedupe.publish(source, target, hardlink=False)
                record["archive"] = str(target)
                record["auto_saved"] = str(target)  # 兼容已有前端结果字段。
                if loop_context and record["kind"] != "video":
                    session_dir = STORE.directory(loop_context["session_id"])
                    session_name = f"segment_{loop_context['index']+1:04d}_{source.name}"
                    dedupe.publish(source, session_dir / session_name)
                    STORE.register_result(loop_context["session_id"], session_name,
                                          {"media_kind": record["kind"], "prompt_text": loop_context.get("prompt_text", ""),
                                           "preview_origin": record["origin"], "preview_file": record["file"]},
                                          context=loop_context)
                    registered_name = session_name
        if record and loop_context:
            from .preview_identity import attach_identity
            attach_identity(STORE, record, loop_context, registered_name)
        return {"ui": {"h3_preview": [record] if record else []}, "result": (media,)}


class AidenH3Prompt:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required":{"strings":("STRING",{"default":"# 第一段提示词\n# 第二段提示词","multiline":True}),
                            "select":("INT",{"default":0,"min":0,"forceInput":True})},
                "optional":{"global_prefix":("STRING",{"default":"","advanced":True}),
                            "global_suffix":("STRING",{"default":"","advanced":True})}}

    RETURN_TYPES=("STRING",)
    RETURN_NAMES=("prompt",)
    FUNCTION="select_prompt"
    CATEGORY=CATEGORY
    DESCRIPTION="内置 multiline=true，按行首 # 提取；段号不足时循环复用。自有解析，不依赖 Impact-Pack。"

    def select_prompt(self,strings,select,global_prefix="",global_suffix=""):
        """自有 # 解析：只认行首 #，空段丢弃，段号越界循环复用。"""
        if isinstance(select,bool) or not isinstance(select,int) or select<0:
            raise ValueError("生成段号必须是非负整数")
        segments,current,seen=[],[],False
        for line in str(strings).splitlines():
            stripped=line.lstrip()
            if stripped.startswith("#"):
                seen=True
                if current:
                    segments.append("\n".join(current).strip());current=[]
                current=[stripped[1:]]
            elif current:
                current.append(line)
        if current:
            segments.append("\n".join(current).strip())
        segments=[part for part in segments if part]
        if not seen:
            plain=str(strings).strip()
            if plain:segments=[plain]
        if not segments:
            raise ValueError("请按行首 # 输入至少一段提示词")
        selected=segments[select%len(segments)]
        return ("\n".join(part for part in (global_prefix,selected,global_suffix) if part),)




class AidenH3MergedVideo:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required":{"session_id":("STRING",{"default":"h3-session"})}}

    RETURN_TYPES=("VIDEO",)
    RETURN_NAMES=("video",)
    FUNCTION="load"
    CATEGORY=CATEGORY
    DESCRIPTION="全部通过并合并后，按进度名称读取成片，供外部去闪烁、帧插值或保存节点连接。不会生成新片段。"

    @classmethod
    def IS_CHANGED(cls,session_id):
        try:
            state=STORE.read(session_id)
            return digest([state.get("merged"),state.get("manifest")])
        except FileNotFoundError:return "missing"

    def load(self,session_id):
        from comfy_api.input_impl import VideoFromFile
        from .storage import contained
        state=STORE.read(session_id)
        if not state.get("merged") or state["status"]!="completed":
            raise ValueError("请先在「分段预览」完成选择与合并，再读取成片")
        path=contained(STORE.directory(session_id),state["merged"])
        if not path.is_file():raise ValueError("合并视频文件缺失，请重新合并")
        video = VideoFromFile(str(path))
        video._ad_h3_preview_record = {"origin": "session", "file": f"{session_id}/{path.name}"}
        return (video,)




class AidenH3ContinuumStatus:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {}}

    RETURN_TYPES = ("STRING", "BOOLEAN")
    RETURN_NAMES = ("report", "available")
    FUNCTION = "status"
    OUTPUT_NODE = True
    CATEGORY = CATEGORY
    DESCRIPTION = "检测本机是否已加载 ComfyUI-H3-Continuum；不采样、不改进度。"

    def status(self):
        from .continuum import continuum_loaded
        return continuum_status_report(), continuum_loaded()


# Continuum Join 只要英文枚举；界面给 Aiden 用中文（含 DD 翻译常见写法）
_CONTINUITY_ZH = ("均衡 · 22 帧", "快速 · 5 帧", "强力 · 39 帧（实验）")
_CONTINUITY_EN = ("Balanced — 22 frames", "Fast — 5 frames", "Strong — 39 frames (Experimental)")
_CONTINUITY_TO_JOIN = {
    "均衡 · 22 帧": "Balanced — 22 frames",
    "快速 · 5 帧": "Fast — 5 frames",
    "强力 · 39 帧（实验）": "Strong — 39 frames (Experimental)",
    "均衡 — 22 帧": "Balanced — 22 frames",
    "快速 — 5 帧": "Fast — 5 frames",
    "强力 — 39 帧（实验）": "Strong — 39 frames (Experimental)",
    "均衡 - 22 帧": "Balanced — 22 frames",
    "快速 - 5 帧": "Fast — 5 frames",
    "强力 - 39 帧（实验）": "Strong — 39 frames (Experimental)",
    "Balanced — 22 frames": "Balanced — 22 frames",
    "Fast — 5 frames": "Fast — 5 frames",
    "Strong — 39 frames (Experimental)": "Strong — 39 frames (Experimental)",
    "Balanced - 22 frames": "Balanced — 22 frames",
    "Fast - 5 frames": "Fast — 5 frames",
    "Strong - 39 frames (Experimental)": "Strong — 39 frames (Experimental)",
}
_POLICY_ZH = (
    "使用 Continuum 上下文并保留视觉身份（推荐）",
    "若存在首帧关键帧则报错",
    "同时保留首帧关键帧（高级）",
)
_POLICY_EN = (
    "Use Continuum context; keep visual identity tokens (Recommended)",
    "Error if a first-frame keyframe exists",
    "Keep first-frame keyframe too (Advanced)",
)
_POLICY_TO_JOIN = {
    _POLICY_ZH[0]: _POLICY_EN[0],
    _POLICY_ZH[1]: _POLICY_EN[1],
    _POLICY_ZH[2]: _POLICY_EN[2],
    _POLICY_EN[0]: _POLICY_EN[0],
    _POLICY_EN[1]: _POLICY_EN[1],
    _POLICY_EN[2]: _POLICY_EN[2],
}


def _join_continuity(value: str) -> str:
    key = str(value).strip()
    if key not in _CONTINUITY_TO_JOIN:
        raise ValueError(f"continuity 无法识别：{value!r}；请选 均衡/快速/强力")
    return _CONTINUITY_TO_JOIN[key]


def _join_policy(value: str) -> str:
    key = str(value).strip()
    if key not in _POLICY_TO_JOIN:
        raise ValueError(f"first_frame_policy 无法识别：{value!r}")
    return _POLICY_TO_JOIN[key]


class AidenH3ContinuumPrepare:
    """Soft-call H3ContinuumJoin; load prior Aiden-session Continuum state when present."""

    @classmethod
    def INPUT_TYPES(cls):
        # 中文在前（界面默认）；英文保留给旧工作流/API，校验两边都过
        return {"required": {
            "context": ("H3_LOOP_CONTEXT",),
            "model": ("MODEL",),
            "conditioning": ("CONDITIONING",),
            "latent": ("LATENT",),
            "continuity": (list(_CONTINUITY_ZH) + list(_CONTINUITY_EN),
                            {"default": _CONTINUITY_ZH[0]}),
            "extend_frames": ("INT", {"default": 0, "min": 0, "max": 4096, "step": 1, "forceInput": True, "tooltip": "新增帧数：从 AD-当前段「目标帧数」接入；无默认，必接"}),
            "audio_continuity": ("BOOLEAN", {"default": True}),
            "first_frame_policy": (list(_POLICY_ZH) + list(_POLICY_EN),
                                   {"default": _POLICY_ZH[0]}),
            "preserve_last_frame": ("BOOLEAN", {"default": True}),
            "strict_compatibility": ("BOOLEAN", {"default": True}),
            "debug": ("BOOLEAN", {"default": False}),
        }}

    RETURN_TYPES = ("MODEL", "CONDITIONING", "LATENT", "H3_CONTINUUM_PLAN", "STRING", "BOOLEAN")
    RETURN_NAMES = ("model", "conditioning", "latent", "plan", "report", "used_continuum")
    FUNCTION = "prepare"
    CATEGORY = CATEGORY
    DESCRIPTION = ("Continuum 续段准备：软调用 H3ContinuumJoin。"
                   "首段或无入库状态时走初始剪辑；有前段 Continuum 状态时潜空间续采。"
                   "未安装 Continuum 时抛出中文说明。")

    def prepare(self, context, model, conditioning, latent, continuity, extend_frames,
                audio_continuity, first_frame_policy, preserve_last_frame,
                strict_compatibility, debug):
        try:
            join_cls = continuum_node("H3ContinuumJoin")
        except ValueError as error:
            report = f"[AD-H3] 未安装可用的 Continuum，已直通（像素续接照常）。{error}"
            return model, conditioning, latent, None, report, False
        previous = None
        used = False
        index = int(context.get("index", 0))
        if index > 0:
            previous = load_continuum_state(STORE.directory(context.get("preview_parent_session") or context["session_id"]), index - 1)
            if previous is None:
                raise ValueError(
                    f"第 {index + 1} 段需要 Continuum 续段，但第 {index} 段没有入库状态。"
                    "请确认上一段采样后接了「AD-续段存盘」，并完成生成。"
                )
            used = True
        continuity_en = _join_continuity(continuity)
        policy_en = _join_policy(first_frame_policy)
        # UI is frames@FPS; Continuum Join still takes seconds.
        extend_frames = int(extend_frames)
        if extend_frames <= 0:
            raise ValueError("请接入新增帧数（接当前段「目标帧数」）；0=未设置")
        # 自动计算：Continuum 续段会裁掉一段上下文帧，这里按“目标帧数 + 连续性帧数 + 17 帧缓冲”补足，保证有效帧不短
        import re as _re
        target_frames_needed = int(context.get("target_frames") or 0)
        if target_frames_needed:
            match = _re.search(r"(\d+)\s*帧", str(continuity))
            context_frames = int(match.group(1)) if match else 22
            needed = target_frames_needed + max(1, context_frames) + 17
            needed = 5 + 17 * ((needed - 5 + 16) // 17)
            extend_frames = max(extend_frames, needed)
        extend_seconds = float(extend_frames) / float(FPS)
        extend_frames = int(extend_frames)
        if extend_frames <= 0:
            raise ValueError("请接入新增帧数（接当前段「目标帧数」）；0=未设置")
        extend_seconds = float(extend_frames) / float(FPS)
        result = join_cls().join(
            model, conditioning, latent, continuity_en, float(extend_seconds),
            bool(audio_continuity), policy_en, bool(preserve_last_frame),
            bool(strict_compatibility), bool(debug), previous_state=previous,
        )
        model_out, cond_out, latent_out, plan, report = result
        mode = "潜空间续段" if used else "首段/初始剪辑"
        report = (
            f"[Aiden→Continuum] session={context['session_id']} 段{index + 1} {mode} "
            f"continuity={continuity}→{continuity_en}。 {report}"
        )
        return model_out, cond_out, latent_out, plan, report, used


class AidenH3ContinuumCapture:
    """Soft-call H3ContinuumFinish and persist state into the Aiden session."""

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "context": ("H3_LOOP_CONTEXT",),
            "samples": ("LATENT",),
            "images": ("IMAGE",),
            "audio": ("AUDIO",),
            "plan": ("H3_CONTINUUM_PLAN",),
        }}

    RETURN_TYPES = ("IMAGE", "AUDIO", "H3_CONTINUUM_STATE", "STRING", "STRING")
    RETURN_NAMES = ("images", "audio", "state", "path", "report")
    FUNCTION = "capture"
    CATEGORY = CATEGORY
    DESCRIPTION = ("Continuum 入库：软调用 H3ContinuumFinish，裁掉重叠后把 AV latent 状态写入本 session。"
                   "输出的 images/audio 再 CreateVideo → AD-记录分段结果。")

    def capture(self, context, samples, images, audio, plan):
        if plan is None:
            return images, audio, None, "", "[AD-H3] 未使用续段，已跳过入库"
        try:
            finish_cls = continuum_node("H3ContinuumFinish")
        except ValueError as error:
            return images, audio, None, "", f"[AD-H3] 未安装可用的 Continuum，已跳过入库：{error}"
        trimmed_images, trimmed_audio, state, report = finish_cls().finish(samples, images, audio, plan)
        path = save_continuum_state(state, STORE.directory(context["session_id"]), int(context["index"]), context)
        report = (
            f"[Aiden←Continuum] session={context['session_id']} 段{int(context['index']) + 1} "
            f"状态已写入 {path}。 {report}"
        )
        print(report)
        return trimmed_images, trimmed_audio, state, path, report


class AidenH3ContinuumRecall:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"context": ("H3_LOOP_CONTEXT",)}}

    RETURN_TYPES = ("H3_CONTINUUM_STATE", "BOOLEAN", "STRING")
    RETURN_NAMES = ("previous_state", "has_state", "report")
    FUNCTION = "recall"
    CATEGORY = CATEGORY
    DESCRIPTION = ("读取上一节已入库的 Continuum 状态，供手动接线 H3ContinuumJoin.previous_state。"
                   "循环图更推荐直接用「AD-续段准备」。首段 has_state=false。")

    def recall(self, context):
        if not continuum_available():
            raise ValueError(continuum_status_report())
        index = int(context.get("index", 0))
        if index <= 0:
            return None, False, "首段无前态；请让 Join 的 previous_state 留空或用续段准备节点"
        state = load_continuum_state(STORE.directory(context["session_id"]), index - 1)
        if state is None:
            return None, False, f"未找到第 {index} 段 Continuum 状态；请先对前段执行续段存盘"
        return state, True, f"已加载第 {index} 段 Continuum 状态供续段"



class AidenH3RoundBlocked:
    """内部保护节点：整轮设置准备失败时阻断执行，并给出可读原因。

    由 rounds.prompt_handler 在无法安全准备本轮快照时写入排队副本；
    正常 Workflow 不会使用。它让 ComfyUI 正常排队后在执行阶段报出真实原因，
    而不是用未注册的假 class 让用户只看到“节点不存在”。
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"reason": ("STRING", {"default": "", "multiline": True})}}

    RETURN_TYPES = ()
    FUNCTION = "block"
    OUTPUT_NODE = True
    CATEGORY = CATEGORY
    DESCRIPTION = "内部：整轮设置准备失败时停止执行并显示原因（不在正常流程中使用）。"

    def block(self, reason=""):
        raise ValueError("本轮排队被阻止：" + (str(reason or "").strip() or "未知原因"))


from .final_output import FinalSave, SelectedResult

# 最小内嵌潜桥（B 最小 vendor）：只使用上游 MIT 许可的桥接/存盘/裁头三块运行时，
# 其余上游节点不注册、不暴露。上游许可证见 h3_loop/_relay_vendor/LICENSE-MIT.txt。
try:
    from ._relay_vendor import nodes as _relay_vendor_runtime
except Exception as _relay_vendor_error:  # pragma: no cover - 依赖/宿主兼容问题时给出明确提示
    _relay_vendor_runtime = None
    _RELAY_VENDOR_IMPORT_ERROR = str(_relay_vendor_error)
else:
    _RELAY_VENDOR_IMPORT_ERROR = ""

NODE_CLASS_MAPPINGS = {
    "AD_FinalSave": FinalSave,
    "AD_FinalSelectedMedia": SelectedResult,
    "AidenH3EditMask": AidenH3EditMask,
    "AidenH3AudioQueue": AidenH3AudioQueue,
    "AidenH3Segment": AidenH3Segment,
    "AidenH3VideoPreview": AidenH3VideoPreview,
    "AD_RoundBlocked": AidenH3RoundBlocked,
}
NODE_DISPLAY_NAME_MAPPINGS = {
    "AD_FinalSave": "AD-最终保存",
    "AD_FinalSelectedMedia": "AD-已选成果读取（内部）",
    "AidenH3EditMask": "AD-角色替换遮罩适配",
    "AidenH3AudioQueue": "AD-素材准备",
    "AidenH3Segment": "AD-分段生成",
    "AidenH3VideoPreview": "AD-分段预览",
    "AD_RoundBlocked": "AD-本轮排队保护（内部）",
}

class ADH3InternalBridge:
    """内部桥：从 AD 分段节点的 context 输出读取 run_id/stage，不接受用户接线。"""
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "latent": ("LATENT",),
            "relay_context": ("H3_LOOP_CONTEXT",),
            "context_frames": ("INT", {"default": 22, "min": 5, "max": 124, "step": 17}),
        }, "optional": {
            "mask_mode": (["hard", "taper", "ramp", "blend", "window"], {"default": "hard"}),
            "pin_audio": ("BOOLEAN", {"default": True}),
        }}
    RETURN_TYPES = ("LATENT",)
    FUNCTION = "run"
    CATEGORY = CATEGORY
    OUTPUT_NODE = False

    def run(self, latent, relay_context, context_frames=22, mask_mode="hard", pin_audio=True):
        ctx = relay_context if isinstance(relay_context, dict) else {}
        run_id = str(ctx.get("relay_run_id") or "ad_relay")
        stage = int(ctx.get("relay_stage_index") or 0)
        upstream = _relay_vendor_runtime.H3RelayCopyBridge()
        if ctx.get("relay_reanchor"):
            # 分段重锚定：不再读“上一段”的尾 latent，但仍用第 1 段的稳定 stage 作为
            # 全局外观锚（人物身份/服装/环境）。失败时明确停止，避免本段丢失角色身份。
            _require_parent_relay_stage(run_id, stage, reanchor=True)
            anchor_path = _relay_stage_file(run_id, 0)
            try:
                from ._relay_vendor import relay_core as _relay_core
                anchor = _relay_core.load_av_latent(str(anchor_path))
                result = upstream.bridge(latent, context_latent=anchor,
                                         context_frames=int(context_frames),
                                         mask_mode=str(mask_mode or "hard"),
                                         pin_audio=bool(pin_audio),
                                         run_id=run_id, stage_index=stage)
            except Exception as exc:
                raise ValueError(f"潜桥重锚定失败，本轮第 1 段外观锚无法读取或无法与本段续接：{exc}") from exc
            return (result[0],)
        result = upstream.bridge(latent, context_frames=int(context_frames), mask_mode=str(mask_mode or "hard"),
                                 pin_audio=bool(pin_audio), run_id=run_id, stage_index=stage)
        return (result[0],)


class ADH3InternalSave:
    """内部存盘：run_id 使用每次生成的唯一 relay_save_run_id，选中后再提升。"""
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "latent": ("LATENT",),
            "relay_context": ("H3_LOOP_CONTEXT",),
        }, "optional": {"note": ("STRING", {"default": "AD hidden relay"})}}
    RETURN_TYPES = ("LATENT",)
    FUNCTION = "run"
    CATEGORY = CATEGORY
    OUTPUT_NODE = True

    def run(self, latent, relay_context, note="AD hidden relay"):
        ctx = relay_context if isinstance(relay_context, dict) else {}
        upstream = _relay_vendor_runtime.H3RelayLatentSave()
        upstream.save(latent, str(ctx.get("relay_save_run_id") or "ad_relay_save"),
                      int(ctx.get("relay_stage_index") or 0), note=note)
        # 存盘与结果登记可能谁先谁后：存盘完成后再查一次，若本版本已被选中/批准就立即提升，
        # 不再依赖“登记时刻源文件已存在”这一顺序假设。
        try:
            parent_session = str(ctx.get("preview_parent_session") or "")
            if not parent_session:
                from .storage import STORE as _STORE
                _STORE.promote_relay_stage_for_save(
                    str(ctx.get("session_id") or ""),
                    str(ctx.get("relay_save_run_id") or ""),
                    int(ctx.get("relay_stage_index") or 0))
        except Exception as exc:  # 提升失败不影响本段生成，但必须可见
            print("[AD-潜桥] 存盘后提升检查失败（不影响本段生成）：%r" % (exc,))
        return (latent,)


class ADH3InternalTrim:
    """内部裁头：trim_frames 从 context 读取，不再暴露独立输出口。"""
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "images": ("IMAGE",),
            "relay_context": ("H3_LOOP_CONTEXT",),
        }, "optional": {
            "audio": ("AUDIO",),
            "fps": ("FLOAT", {"default": 24.0, "min": 1.0, "max": 120.0, "step": 0.001}),
        }}
    RETURN_TYPES = ("IMAGE", "AUDIO")
    RETURN_NAMES = ("images", "audio")
    FUNCTION = "run"
    CATEGORY = CATEGORY
    OUTPUT_NODE = False

    def run(self, images, relay_context, audio=None, fps=24.0):
        import contextlib
        import io

        ctx = relay_context if isinstance(relay_context, dict) else {}
        if ctx.get("relay_run_id") and "relay_trim_frames" not in ctx:
            # 兼容防线：旧前端/旧序列化没有携带裁头帧数时不能静默按 0 帧处理。
            raise ValueError(
                "潜桥裁头缺少 relay_trim_frames：本次提交与后台版本不匹配。"
                "请保存工作流后重新点「开始」；若仍出现，请更新插件到 0.5.5 并重启 ComfyUI。")
        frames = int(ctx.get("relay_trim_frames") or 0)
        upstream = _relay_vendor_runtime.H3RelayTrimAV()
        before_images = int(images.shape[0])
        before_audio = (int(audio["waveform"].shape[-1])
                        if isinstance(audio, dict) and audio.get("waveform") is not None else None)
        # 上游裁头会打印含 emoji/制表符的报告，在重定向 stdout（日志采集/服务化启动）时会抛
        # UnicodeEncodeError；这里捕获其输出，只回一行中文摘要，同时关闭无用的 PCM 边车。
        captured = io.StringIO()
        with contextlib.redirect_stdout(captured):
            result = upstream.trim(images, trim_frames=frames, fps=float(fps),
                                   audio=audio, settle_frames=0, save_pcm=False)
        if isinstance(result, dict):
            result = result.get("result")
        trimmed_images, trimmed_audio = result[0], result[1]
        after_images = int(trimmed_images.shape[0])
        after_audio = (int(trimmed_audio["waveform"].shape[-1])
                       if isinstance(trimmed_audio, dict) and trimmed_audio.get("waveform") is not None else None)
        line = "[AD-潜桥] 裁头 %d 帧：画面 %d → %d 帧" % (frames, before_images, after_images)
        if before_audio is not None and after_audio is not None:
            line += "；音频 %d → %d 采样点" % (before_audio, after_audio)
        print(line)
        return (trimmed_images, trimmed_audio)


# ---- 内嵌潜桥的运行节点（完全隐藏：只由前端提交时注入，不进入画布/菜单） ----
_INTERNAL_RELAY_KEYS = {
    "ADH3InternalBridge": "bridge",
    "ADH3InternalSave": "save",
    "ADH3InternalTrim": "trim",
}
if _relay_vendor_runtime is not None:
    for _key, _kind in _INTERNAL_RELAY_KEYS.items():
        if _kind == "bridge":
            NODE_CLASS_MAPPINGS[_key] = ADH3InternalBridge
            NODE_DISPLAY_NAME_MAPPINGS[_key] = "AD-内部潜桥桥接"
        elif _kind == "save":
            NODE_CLASS_MAPPINGS[_key] = ADH3InternalSave
            NODE_DISPLAY_NAME_MAPPINGS[_key] = "AD-内部潜桥存盘"
        elif _kind == "trim":
            NODE_CLASS_MAPPINGS[_key] = ADH3InternalTrim
            NODE_DISPLAY_NAME_MAPPINGS[_key] = "AD-内部潜桥裁头"

    # 旧工作流无感加载：外部插件未安装时，注册上游三个旧类型为兼容层。
    # 外部插件已安装时跳过，避免同名类型重复注册。
    try:
        _custom_nodes_root = Path(__file__).resolve().parents[2]
        _external_relay = ((_custom_nodes_root / "ComfyUI-H3-Latent-Relay").is_dir()
                           or (_custom_nodes_root / "h3-relay").is_dir())
    except Exception:
        _external_relay = False
    if not _external_relay:
        NODE_CLASS_MAPPINGS.setdefault("H3RelayApplyEditMask", AidenH3EditMask)
        NODE_DISPLAY_NAME_MAPPINGS.setdefault("H3RelayApplyEditMask", "AD-角色替换遮罩适配（兼容）")
        NODE_CLASS_MAPPINGS.setdefault("H3RelayCopyBridge", _relay_vendor_runtime.H3RelayCopyBridge)
        NODE_CLASS_MAPPINGS.setdefault("H3RelayLatentSave", _relay_vendor_runtime.H3RelayLatentSave)
        NODE_CLASS_MAPPINGS.setdefault("H3RelayTrimAV", _relay_vendor_runtime.H3RelayTrimAV)
        NODE_DISPLAY_NAME_MAPPINGS.setdefault("H3RelayCopyBridge", "H3 Relay · Copy Bridge（兼容）")
        NODE_DISPLAY_NAME_MAPPINGS.setdefault("H3RelayLatentSave", "H3 Relay · Latent Save（兼容）")
        NODE_DISPLAY_NAME_MAPPINGS.setdefault("H3RelayTrimAV", "H3 Relay · Trim AV（兼容）")

