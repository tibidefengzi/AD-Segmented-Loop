"""Explicit video material lists: same driving logic as the audio queue."""
from __future__ import annotations

import json
import math

from .spec import FPS


def video_config(raw):
    return json.loads(raw or '{"segments":[]}')


def prepare_video_segments(raw_segments):
    segments = []
    for position, raw in enumerate(raw_segments or [], start=1):
        if not isinstance(raw, dict):
            raise ValueError("视频分段条目必须是对象")
        name = raw.get("name") or f"视频段{position}"
        start = raw.get("start")
        if not isinstance(start, (int, float)) or isinstance(start, bool) or not math.isfinite(start) or start < 0:
            raise ValueError(f"{name} 的起始秒数无效")
        first = math.floor(start * FPS + 0.5)
        end = raw.get("end")
        last = None
        if end is not None:
            if not isinstance(end, (int, float)) or isinstance(end, bool) or not math.isfinite(end) or end <= start:
                raise ValueError(f"{name} 的结束秒数必须大于起始秒数")
            last = math.floor(end * FPS + 0.5)
            if last <= first:
                raise ValueError(f"{name} 短于一帧，请调整起止时间")
        segments.append({"name": str(name), "start": float(start),
                         "end": None if end is None else float(end),
                         "start_frame": first, "end_frame": last})
    return segments


def video_plan(raw):
    config = video_config(raw)
    if not isinstance(config, dict) or "segments" not in config:
        raise ValueError("视频分段清单格式无效，缺少 segments")
    return {"segments": prepare_video_segments(config["segments"])}


def video_identity(raw):
    return [(item.get("name"), item.get("start"), item.get("end"))
            for item in (video_config(raw).get("segments") or [])]


def check_alignment(segments, entries):
    if len(segments) != len(entries):
        raise ValueError(f"视频分段清单有 {len(segments)} 段，音频分段清单有 {len(entries)} 段；两份清单段数必须一致")
    return segments


def video_window(segment, count, overlap):
    """The list's explicit range replaces the audio-cumulative start derivation."""
    backoff = min(overlap, segment["start_frame"])
    take = count + overlap
    if segment["end_frame"] is not None:
        take = min(take, segment["end_frame"] - segment["start_frame"] + backoff)
    return {"start_frame": segment["start_frame"] - backoff, "source_frames": max(1, take)}
