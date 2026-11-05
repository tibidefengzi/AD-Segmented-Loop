"""Per-segment RGB exposure/color matching to the reference video's first frame.

只做“整段一个固定校正”（不对逐帧自动增益），避免段内闪烁；
目标是当前段参考视频首帧的逐通道均值，校正生成帧的整体亮度/白平衡。
不经过 VAE 往返，不改变生成过程。
"""
from __future__ import annotations

import numpy as np

from .spec import FPS

COLOR_MATCH_CHOICES = (
    "0 · 关闭",
    "0.5 · 半强",
    "1 · 全强",
)
COLOR_MATCH_VALUES = {"0 · 关闭": 0.0, "0.5 · 半强": 0.5, "1 · 全强": 1.0}
MAX_OFFSET = 0.18
MIN_REF_MEAN = 0.015


def parse_blend(raw):
    """解析下拉/数字：返回 0 / 0.5 / 1。"""
    if isinstance(raw, (int, float)):
        value = float(raw)
    else:
        text = str(raw or "").strip()
        if text in COLOR_MATCH_VALUES:
            return COLOR_MATCH_VALUES[text]
        try:
            value = float(text.split()[0]) if text else 0.0
        except (TypeError, ValueError):
            return 0.0
    if value <= 0:
        return 0.0
    return 1.0 if value >= 0.75 else 0.5


def reference_frame_stats(frame, source, frame_index):
    """使用已经解码的正文首个参考画面，只向提交节点传递统计值。"""
    import torch
    mean = frame.detach().mean(dim=(0, 1), dtype=torch.float32).cpu().tolist()
    return {"path": source.get("file"), "frame_index": frame_index, "frame_time": frame_index / FPS, "mean": mean}


def apply_reference_color_match(body, stats, blend):
    """body: torch [N,H,W,3] 0..1；返回 (body, info)。

    对整段使用同一个逐通道偏移：target_mean - generated_mean，乘 blend 并限幅；
    不改对比度/锐度，不做逐帧增益。参考缺失/黑帧时原样返回。
    """
    try:
        blend = float(blend)
    except (TypeError, ValueError):
        blend = 0.0
    if body is None or blend <= 0 or not stats:
        return body, None
    target = np.asarray(stats["mean"], dtype=np.float32).reshape(-1)[:3]
    if target.size < 3 or float(target.mean()) < MIN_REF_MEAN:
        return body, None
    import torch

    step = max(1, int(body.shape[0]) // 8)
    sample = body[::step].detach()
    generated = sample.mean(dim=(0, 1, 2), dtype=torch.float32).cpu().numpy()
    offset = np.clip((target - generated) * blend, -MAX_OFFSET, MAX_OFFSET)
    if float(np.abs(offset).max()) < 0.002:
        return body, None
    correction = torch.as_tensor(offset, device=body.device, dtype=body.dtype).view(1, 1, 1, 3)
    corrected = torch.empty_like(body)
    chunk = max(1, (32 * 1024 * 1024) // (body[0].numel() * body.element_size()))
    for start in range(0, len(body), chunk):
        destination = corrected[start:start + chunk]
        torch.add(body[start:start + chunk].detach(), correction, out=destination)
        destination.clamp_(0.0, 1.0)
    info = {
        "blend": float(blend if blend in (0.5, 1.0) else parse_blend(blend)),
        "reference": stats["path"],
        "reference_track": stats.get("track"),
        "reference_frame_index": stats.get("frame_index"),
        "frame_time": float(stats["frame_time"]),
        "target_mean": [round(float(v), 5) for v in target],
        "generated_mean": [round(float(v), 5) for v in generated],
        "offset": [round(float(v), 5) for v in offset],
    }
    return corrected, info
