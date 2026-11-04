"""One-time CPU/FFmpeg imports and small preview proxies; originals are never overwritten."""
from __future__ import annotations

import json
import math
from pathlib import Path
import subprocess
import threading
import uuid

from .storage import ROOT, atomic_json, contained, digest

POLICY = "h3-24fps-video864-image2160-pad32-v1"
_LOCK = threading.Lock()
_LOCKS = {}


def normalized_root(origin: str) -> Path:
    """导入素材的规范化副本跟随素材所在的库：input 库 → input/AD-Segmented-Loop-input/normalized。"""
    if origin == "input":
        from . import layout
        base = layout.active_input_library() or layout.input_library_dir()
        return base / "normalized"
    return ROOT / "media" / "normalized"


def normalized_relative(origin: str, name: str) -> str:
    from . import layout
    return (f"{layout.INPUT_LIBRARY}/normalized/" if origin == "input" else "normalized/") + name


def _saved_root(saved: dict) -> Path:
    """saved 里的 file 是相对于素材库根目录的路径（input 库含素材库名前缀）。"""
    if saved.get("origin") == "input":
        from . import layout
        base = layout.input_base()
        return base if base is not None else ROOT / "media"
    return ROOT / "media"


def _saved_path(saved: dict) -> Path:
    from . import layout
    if saved.get("origin") == "input":
        path = layout.resolve_input_file(str(saved.get("file") or ""))
        return path if path is not None else _saved_root(saved) / str(saved.get("file") or "")
    return contained(_saved_root(saved), str(saved.get("file") or ""))


def aligned_size(width, height, longest):
    if width <= 0 or height <= 0:
        raise ValueError("素材宽高无效")
    scale = longest / max(width, height)
    scaled = (max(1, round(width * scale)), max(1, round(height * scale)))
    canvas = tuple(max(32, math.ceil(value / 32) * 32) for value in scaled)
    return scaled, canvas


def video_info(path):
    import av
    with av.open(str(path)) as container:
        if not container.streams.video:
            raise ValueError("文件没有视频画面")
        stream = container.streams.video[0]
        duration = float(stream.duration * stream.time_base) if stream.duration is not None else float(container.duration or 0) / av.time_base
        width, height = stream.width, stream.height
        rotation = int(float(stream.metadata.get("rotate", 0))) % 360
        # PyAV exposes the display matrix rotation on decoded frames.
        first = next(container.decode(video=0), None)
        if first is None:
            raise ValueError("视频无法解码")
        rotation = int(getattr(first, "rotation", rotation) or rotation) % 360
        if rotation in (90, 270):
            width, height = height, width
        return {"width": width, "height": height, "duration": duration,
                "frames": stream.frames, "fps": float(stream.average_rate or 0), "has_audio": bool(container.streams.audio)}


def _transcode(arguments, seconds, progress):
    from .assets import ffmpeg_executable
    executable = ffmpeg_executable()
    if not executable:
        raise ValueError("需要 FFmpeg 来规范视频素材")
    process = subprocess.Popen([executable, "-v", "error", "-nostdin", "-n", "-progress", "pipe:1", "-nostats", *arguments],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    # Drain stderr concurrently so a damaged source cannot fill the pipe and block progress.
    errors = bytearray()
    def read_errors():
        for chunk in iter(lambda: process.stderr.read(4096), b""):
            errors.extend(chunk)
            if len(errors) > 8192:
                del errors[:-8192]
    reader = threading.Thread(target=read_errors, daemon=True)
    reader.start()
    try:
        for line in process.stdout:
            if line.startswith(b"out_time_us="):
                try:
                    progress(min(.99, max(0.0, int(line.split(b"=", 1)[1]) / 1_000_000 / max(seconds, .001))))
                except ValueError:
                    pass
        code = process.wait()
        reader.join()
        if code:
            raise ValueError("素材规范化失败：" + errors.decode("utf-8", "replace")[-1200:])
    finally:
        if process.poll() is None:
            process.terminate(); process.wait()
        process.stdout.close(); process.stderr.close()


def normalize_source(source, progress=lambda fraction: None):
    from .assets import resolve_source
    kind = source.get("kind")
    if kind not in {"image", "video"}:
        return dict(source)
    path = resolve_source(source)
    if not path.is_file():
        raise ValueError(f"素材文件缺失：{source.get('name', path.name)}")
    if source.get("normalization") == POLICY and source.get("preview") and resolve_source(source["preview"]).is_file():
        progress(1)
        return dict(source)
    stat = path.stat()
    key = digest([POLICY, str(path.resolve()), stat.st_size, stat.st_mtime_ns, kind])
    with _LOCK:
        lock = _LOCKS.setdefault(key, threading.Lock())
    origin = source.get("origin") if source.get("origin") in {"input", "media"} else "media"
    root = normalized_root(origin)
    root.mkdir(parents=True, exist_ok=True)
    manifest = root / f"{key}.json"
    with lock:
        if manifest.is_file():
            saved = json.loads(manifest.read_text(encoding="utf-8"))
            if _saved_path(saved).is_file() and _saved_path(saved.get("preview") or {}).is_file():
                progress(1)
                return {**source, **saved, "id": source.get("id", key[:24]), "name": source.get("name", path.name)}
        # A retry has a distinct destination; never overwrite a prior import or partial artifact.
        token = key[:24] + "_" + uuid.uuid4().hex[:12]
        original = source.get("original") or {"origin": source["origin"], "file": source["file"], "name": source.get("name", path.name)}
        created = []
        try:
            if kind == "image":
                import numpy as np
                from PIL import Image, ImageOps
                with Image.open(path) as image:
                    image = ImageOps.exif_transpose(image).convert("RGBA" if "A" in image.getbands() else "RGB")
                    scaled, canvas = aligned_size(image.width, image.height, 2160)
                    image = image.resize(scaled, Image.Resampling.LANCZOS)
                    pixels = np.asarray(image)
                    dx, dy = canvas[0] - scaled[0], canvas[1] - scaled[1]
                    pixels = np.pad(pixels, ((dy//2, dy-dy//2), (dx//2, dx-dx//2), (0,0)), mode="edge")
                    normalized = Image.fromarray(pixels)
                    filename, previewname = f"{token}.png", f"{token}_preview.png"
                    created.extend([root / filename, root / previewname])
                    normalized.save(root / filename)
                    normalized.thumbnail((384,384), Image.Resampling.LANCZOS)
                    normalized.save(root / previewname)
                details = {"width": canvas[0], "height": canvas[1], "duration": None}
            else:
                info = video_info(path)
                if info["duration"] <= 0:
                    raise ValueError("视频时长无效")
                scaled, canvas = aligned_size(info["width"], info["height"], 864)
                # Preserve aspect, then add at most 31 px of padding to the 32-grid.
                filters = f"fps=24:round=near,scale={scaled[0]}:{scaled[1]}:flags=lanczos,"
                dx, dy = canvas[0]-scaled[0], canvas[1]-scaled[1]
                filters += f"pad={canvas[0]}:{canvas[1]}:{dx//2}:{dy//2}:black,setsar=1"
                filename, previewname = f"{token}.mp4", f"{token}_preview.mp4"
                created.extend([root / filename, root / previewname])
                target_frames = max(1, math.floor(info["duration"] * 24 + .5))
                args = ["-i", str(path), "-map", "0:v:0", "-map", "0:a:0?", "-vf", filters,
                        "-frames:v", str(target_frames), "-c:v", "libx264", "-preset", "fast", "-crf", "18", "-threads", "0",
                        "-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", "48000", "-af", f"apad,atrim=duration={target_frames/24:.9f}",
                        "-t", f"{target_frames/24:.9f}", "-movflags", "+faststart", str(root / filename)]
                _transcode(args, info["duration"], lambda p: progress(p * .8))
                result = video_info(root / filename)
                if result["fps"] != 24 or result["width"] % 32 or result["height"] % 32:
                    raise ValueError("规范化结果未通过帧率与尺寸检查")
                _transcode(["-i", str(root / filename), "-map", "0:v:0", "-map", "0:a:0?", "-c:a", "copy", "-vf", "scale=480:480:force_original_aspect_ratio=decrease:force_divisible_by=2",
                            "-c:v", "libx264", "-preset", "veryfast", "-crf", "27", "-threads", "0", "-g", "24", "-pix_fmt", "yuv420p",
                            "-movflags", "+faststart", str(root / previewname)], result["duration"], lambda p: progress(.8 + p * .19))
                details = {**result, "fps": 24}
            saved = {"kind": kind, "origin": origin, "file": normalized_relative(origin, filename), **details,
                     "original": original, "normalization": POLICY,
                     "preview": {"origin": origin, "file": normalized_relative(origin, previewname)}}
            created.append(manifest)
            atomic_json(manifest, saved)
        except Exception:
            # 转码/保存失败：删除本次新建的输出、预览与 JSON，避免孤儿文件占用额度。
            if created:
                print(f"AD-Segmented-Loop: 素材规范化失败，已清理未完成文件：{[str(item) for item in created]}")
            for item in created:
                try:
                    Path(item).unlink(missing_ok=True)
                except OSError:
                    pass
            raise
        progress(1)
        return {**source, **saved}
