"""运行时复用 Continuum 的接缝分析/修正能力（不复制其源码）。

Continuum 以 custom_nodes/ComfyUI-H3-Continuum 形式安装；未安装时本模块返回不可用，
调用方回退到插件自身的 FFmpeg 拼接。
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

_PACKAGE = "ComfyUI-H3-Continuum"


def _import(module: str):
    root = Path(__file__).resolve().parents[2] / "custom_nodes"
    candidates = [root]
    try:
        import folder_paths
        base = getattr(folder_paths, "base_path", None)
        if base:
            candidates.append(Path(base) / "custom_nodes")
    except Exception:
        pass
    for candidate in candidates:
        if candidate.exists() and str(candidate) not in sys.path:
            sys.path.insert(0, str(candidate))
    return importlib.import_module(f"{_PACKAGE}.{module}")


def available() -> bool:
    try:
        _import("v3.video_seam")
        return True
    except Exception:
        return False


def _build_plan(chunks, width, height):
    plan_module = _import("v3.plan")
    items = []
    total = 0
    first_seconds = 0.0
    for index, chunk in enumerate(chunks):
        frames = int(chunk["total_frames"])
        trim = int(chunk.get("trim_frames", 0))
        total += frames
        if index == 0:
            first_seconds = frames / 24.0
        items.append({
            "sequence_index": index + 1,
            "chunk_index": index + 1,
            "total_frames": frames,
            "trim_frames": trim,
            "net_frames": frames - trim,
            "context_frames": trim,
            "expected_video_latent_t": 1,
            "expected_audio_latent_t": 1,
        })
    return {
        "magic": plan_module.ASSEMBLY_PLAN_MAGIC,
        "schema_version": plan_module.ASSEMBLY_PLAN_SCHEMA_VERSION,
        "fps": 24,
        "width": int(width),
        "height": int(height),
        "chunk_seconds": float(first_seconds),
        "target_frames": int(total),
        "preserve_final_frame": True,
        "chunks": items,
    }


def correct_boundaries(images, chunks, enable_exposure_ramp=True):
    """按 Continuum 的 assembly plan 修正段间边界。

    images: 每段解码后的 torch 帧张量列表（T,H,W,C，0~1）
    chunks: [{"total_frames": int, "trim_frames": int}, ...]
    返回 (修正后的 images, {边界序号: 处理说明})
    """
    seam = _import("v3.video_seam")
    first = images[0]
    plan = _build_plan(chunks, first.shape[2], first.shape[1])
    analyses = seam.analyze_decoded_boundaries(images=images, assembly_plan=plan)
    corrected, actions = seam.correct_decoded_boundaries(
        images=images, assembly_plan=plan, analyses=analyses,
        enable_exposure_ramp=bool(enable_exposure_ramp),
    )
    return corrected, actions


def merge_segments(segments, options=None):
    """接缝拼接入口：优先使用 Continuum 的边界修正，失败时抛出以便回退。"""
    images = [item["frames"] for item in segments]
    chunks = [{"total_frames": int(item["frames"].shape[0]),
               "trim_frames": int(item.get("trim_frames", 0))} for item in segments]
    return correct_boundaries(images, chunks, enable_exposure_ramp=True)

