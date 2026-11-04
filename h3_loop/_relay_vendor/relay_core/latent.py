# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 ComfyUI-H3-Latent-Relay contributors
# 第三方出处与许可见 THIRD-PARTY-NOTICES.md
"""relay_core.latent —— latent 取流 / 尾段切片 / AV latent 落盘往返 / 裁头重叠。

本模块由 `relay_core.py` 拆分而来（2026-10-07）；代码**逐字搬运**，未改任何算式。
对外仍由 `relay_core/__init__.py` 全量再导出 ⇒ 旧调用点（`relay_core.xxx`）不变。
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple
import json
import math
import os
import torch

from ._grid import AUDIO_HZ, FPS, FRAME_RESCALE, GUIDE_RUNS, KEY_EXPORT_FRAMES, KEY_EXPORT_TAIL_AUDIO, KEY_EXPORT_TAIL_VIDEO, pixel_frames, step_offsets, steps_for_frames

# ---------------------------------------------------------------- latent 取流
def streams_from_latent(latent: Any) -> List[torch.Tensor]:
    """从 LATENT 取 [video, audio, ...]。

    支持三种形态：
      - ``NestedTensor``（H3 AV 联合 latent，本产线常态）→ 按 ``.tensors`` 拆流
      - list/tuple（已拆开的流）
      - 单个 ``torch.Tensor``（video-only latent）→ 当作**一条**流

    ⚠️ 不要用 ``hasattr(samples, "unbind")`` 来判定"是不是 NestedTensor" ——
    **任何 torch.Tensor 都有 unbind**，普通 ``[B,C,T,H,W]`` 会被沿 batch 维拆开，
    B=1 时碰巧看不出错、B>1 时静默产出垃圾流。故这里显式按类型分支。
    """
    if latent is None:
        raise ValueError("latent 为空：续接需要一段真实的 H3 AV latent。")
    samples = latent["samples"] if isinstance(latent, dict) else latent
    nested = getattr(samples, "tensors", None)
    if nested is not None:
        parts = list(nested)
    elif isinstance(samples, (tuple, list)):
        parts = list(samples)
    elif torch.is_tensor(samples):
        parts = [samples]
    else:
        raise ValueError(
            "期望 H3 的 AV latent（nested 视频/音频对），得到 %r。\n"
            "    接续接错的常见原因：把 video-only latent 接到了 context_latent。" % type(samples)
        )
    parts = [t for t in parts if torch.is_tensor(t)]
    if not parts:
        raise ValueError("AV latent 里没有可用的张量流。")
    return parts


def video_from_latent(latent: Any) -> torch.Tensor:
    """取视频流，规范成 [B,C,T,H,W]。"""
    v = streams_from_latent(latent)[0]
    if v.ndim == 4:
        v = v.unsqueeze(0)
    if v.ndim != 5:
        raise ValueError(
            "期望视频 latent 形状 [B,C,T,H,W]，得到 %s。" % (tuple(v.shape),)
        )
    return v


def audio_from_latent(latent: Any) -> torch.Tensor:
    """取音频流，规范成 [B,C,2,T]。"""
    parts = streams_from_latent(latent)
    if len(parts) < 2:
        raise ValueError(
            "context_latent 没有音频流。\n"
            "    续接需要采样器的 AV 输出（视频+音频同源），不是纯视频 latent。"
        )
    a = parts[1]
    if a.ndim == 3:
        a = a.unsqueeze(0)
    if a.ndim != 4:
        raise ValueError("期望音频 latent 形状 [B,C,2,T]，得到 %s。" % (tuple(a.shape),))
    return a


# ---------------------------------------------------------------- 尾段切片
def video_tail_from_latent(
    latent: Any, frames: int
) -> Tuple[List[torch.Tensor], List[int], int]:
    """从 AV latent 切出 ``frames`` 帧的**视频尾段**，切成逐 token 的块。

    返回 ``(blocks, offsets, covered)``：
      - ``blocks[k]`` 是第 k 个 latent token（[1,C,1,H,W]）
      - ``offsets[k]`` 是该 token 的起始像素帧位置
      - ``covered`` 是实际覆盖帧数（等于 frames，否则 raise）

    三条硬约束（违反即 raise，绝不静默降级）：
      1. ``frames`` 必须落在网格上；
      2. 尾段不得长于 latent 本身；
      3. 尾段起始必须落在 5-token 周期边界，否则各 token 的帧跨度
         会和写入的位置对不上 → 渲染出**整体位移的接缝**。
    """
    frames = int(frames)

    # 优先复用外层（Apt 链）已经写好的导出尾段，省一次切片
    exported = latent.get(KEY_EXPORT_TAIL_VIDEO) if isinstance(latent, dict) else None
    exported_frames = int(
        latent.get(KEY_EXPORT_FRAMES, 0) if isinstance(latent, dict) else 0
    )
    if exported is not None and exported_frames and frames == exported_frames:
        if exported.ndim == 4:
            exported = exported.unsqueeze(0)
        steps = steps_for_frames(frames)
        if exported.ndim != 5 or int(exported.shape[2]) != steps:
            raise ValueError(
                "latent 内附的导出尾段与 %d 帧的 H3 网格不符（形状 %s，期望 %d 步）。"
                % (frames, tuple(exported.shape), steps)
            )
        blocks = [exported[:1, :, k:k + 1].clone() for k in range(steps)]
        return blocks, step_offsets(steps), frames

    video = video_from_latent(latent)
    total = int(video.shape[2])
    steps = steps_for_frames(frames)
    if steps is None:
        raise ValueError(
            "%d 帧不是 H3 latent 的整步窗口，无法从 latent 切片。\n"
            "    合法窗口：%s。\n"
            "    若确实要用这个帧数，请改用像素路径（context_frames）而不是 context_latent。"
            % (frames, ", ".join(str(g) for g in GUIDE_RUNS if g > 1))
        )
    if steps > total:
        # 2026-10-16 升级：原文案只说「上一段太短」，用户得自己反推可用窗。
        #   实测踩过（扫窗实验第二档 CTX=56 对 39 帧源片）⇒ 改成**直接给出可用档位与出路**。
        _maxf = pixel_frames(total)
        _ok = [g for g in GUIDE_RUNS if 1 < g <= _maxf]
        raise ValueError(
            "需要 %d 个 latent 步（%d 帧），但上一段只有 %d 步 —— 够 %d 帧。\n"
            "    ⚠ 硬约束：context_frames 必须 ≤ 上一段的帧数。桥按帧数取尾段，取不到就拒。\n"
            "    （第 1 段的「上一段」就是源片／接入片。）\n"
            "    ⇒ 三选一：① 把窗降到 %s 之一；② 把上一段加长到 ≥%d 帧；③ 改用像素路径。"
            % (steps, frames, total, _maxf,
               " / ".join(str(g) for g in _ok) if _ok else "（上一段太短，无可用窗）",
               frames)
        )
    start = total - steps
    if start % 5 != 0:
        raise RuntimeError(
            "%d 步的尾段在 %d 步的 latent 里起始于周期位置 %d（非 0），"
            "各 token 的帧跨度会与写入位置错位 → 接缝整体位移。\n"
            "    通常说明段长不匹配；调整段长使 (总步数 - %d) 是 5 的倍数。"
            % (steps, total, start % 5, steps)
        )
    covered = pixel_frames(steps)
    if covered != frames:
        raise RuntimeError(
            "%d 步覆盖 %d 帧，期望 %d 帧（网格自洽性被破坏）。" % (steps, covered, frames)
        )
    blocks = [video[:1, :, start + k:start + k + 1].clone() for k in range(steps)]
    return blocks, step_offsets(steps), covered


def audio_tail_from_latent(
    latent: Any, a_frames: int, src_total_frames: int
) -> Tuple[torch.Tensor, int, float, float, bool]:
    """从 AV latent 切出 ``a_frames`` 帧对应的**音频尾段**。

    ``src_total_frames`` 是**源段（latent）的总像素帧数**——H3 的音频 tick 数按
    总帧数四舍五入生成，外溢偏差只有对着总帧数量才有意义（对着尾窗量恒为巨值）。

    返回 ``(tail, take, grid_slack, raw_steps, grid_off)``：
      · ``take`` —— 实际取用的音频步数；非 40Hz 网格整步的 ``a_frames``
        会**向上拓宽**到最近整步，要的比源段现有还多则全给；
      · ``grid_slack`` —— 音频栅格相对视频栅格的外溢量（正常在 ±⅓ 步内）；
      · ``raw_steps`` —— 换算出的理论步数，供上层留注记；
      · ``grid_off`` —— 外溢量超出半整步（输入段可能非标准网格），
        此时 ``grid_slack`` 按 0 报出，告警文案由上层写进 notes。
    """
    a_frames = int(a_frames)

    exported = latent.get(KEY_EXPORT_TAIL_AUDIO) if isinstance(latent, dict) else None
    if exported is not None:
        if exported.ndim == 3:
            exported = exported.unsqueeze(0)
        if exported.ndim != 4:
            raise ValueError("latent 内附的导出音频尾段形状非法。")
        n_t = int(exported.shape[-1])
        return exported[:1].clone(), n_t, 0.0, float(n_t), False

    audio = audio_from_latent(latent)
    total_t = int(audio.shape[-1])
    # 音频栅格相对视频栅格的**外溢量**。H3 按源段总帧数四舍五入生成音频 tick，
    # 正常只落在 ±⅓ 步内；超出这条带 ⇒ 输入本身不是标准网格。
    grid_slack = total_t - FRAME_RESCALE * int(src_total_frames)
    grid_off = abs(grid_slack) >= 0.5
    if grid_off:
        # 只置标志、不改事实：外溢值按 0 报出，告警文案留给上层写进 notes。
        grid_slack = 0.0

    raw_steps = a_frames / float(FPS) * AUDIO_HZ
    # 非 40Hz 网格整步的值一律**向上拓宽**到最近整步：
    # 音频窗的作用是给模型"已经播过的声音"当上下文，多带半步是安全的，
    # 截短半步则可能丢掉节拍点。换算误差只往"多带"方向偏。
    want = int(math.ceil(raw_steps - 1e-9))
    take = min(want, total_t)          # 要的比现有的多 ⇒ 全给，不报错
    if take < 1:
        raise ValueError("音频窗口为空（%d 帧换算后不足一步）。" % a_frames)
    tail = audio[:1].narrow(-1, total_t - take, take).clone()
    return tail, take, float(grid_slack), raw_steps, grid_off


# ---------------------------------------------------------------- AV latent 存取
# 磁盘格式：一个 safetensors，内含 streams.video / streams.audio + metadata。
# NestedTensor 无法直接 safetensors 序列化，故按流拆开存。
_LATENT_META_KEY = "relay_kit_meta"


def save_av_latent(latent: Any, path: str, note: str = "",
                   lineage: Dict[str, Any] | None = None) -> str:
    """把 H3 的 AV latent 落盘，供下一段当 context_latent 读回。

    ``lineage``（B1 谱系簿记）：写进头 metadata 的结构谱系（run_id / stage_index /
    parent_stage / pack 等）。**纯附加**——读取方按 JSON 键取用，多出的键被忽略
    ⇒ 旧版部署副本读 format 2 文件不坏；旧 format 1 文件（无谱系键）也照读。
    """
    from safetensors.torch import save_file

    parts = streams_from_latent(latent)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tensors: Dict[str, torch.Tensor] = {}
    names = []
    for i, t in enumerate(parts):
        name = "video" if i == 0 else ("audio" if i == 1 else "stream_%d" % i)
        names.append(name)
        # safetensors 要求连续内存。detach() 已脱离 autograd 共享（与旧版 clone 的
        # 安全性等价），跨设备时 .cpu() 本来就物化新张量——不再额外 clone，
        # 落盘瞬间的 CPU 峰值从 2-3 倍降到 1 倍。
        tc = t.detach()
        if tc.device.type != "cpu":
            tc = tc.cpu()
        tensors["streams." + name] = tc.contiguous()
    meta = {
        "format": 2,   # 2 = 带头谱系簿记（B1）；1 = 旧版（无谱系键）。读取方兼容两者
        "streams": names,
        "shapes": [list(t.shape) for t in parts],
        "note": str(note),
    }
    if lineage:
        for _k, _v in lineage.items():
            meta[str(_k)] = _v
    # UTF-8 字节流：note 含中文时 ord(c) 会超 uint8 直接崩，必须先 encode
    meta_bytes = json.dumps(meta, ensure_ascii=False).encode("utf-8")
    tensors[_LATENT_META_KEY] = torch.frombuffer(bytearray(meta_bytes), dtype=torch.uint8)
    # 原子写：先写同目录 .tmp 再替换，中途崩溃不会留下截断的 safetensors
    tmp = path + ".tmp"
    try:
        save_file(tensors, tmp)
        os.replace(tmp, path)
    except Exception:
        try:
            if os.path.isfile(tmp):
                os.remove(tmp)
        except OSError:
            pass
        raise
    return path


def load_av_latent(path: str):
    """读回落盘的 AV latent，重建 NestedTensor。"""
    from safetensors.torch import load_file

    try:
        import comfy.nested_tensor as _nt
    except Exception:  # pragma: no cover - ComfyUI 运行时一定可用
        _nt = None

    if not os.path.isfile(path):
        raise FileNotFoundError("读不到续接 latent：%s" % path)
    raw = load_file(path)
    meta_t = raw.pop(_LATENT_META_KEY, None)
    if meta_t is None:
        raise ValueError("%s 不是本工具写的 AV latent（缺少元数据）。" % path)
    meta = json.loads(bytes(meta_t.tolist()).decode("utf-8"))
    parts = [raw["streams." + n] for n in meta["streams"]]
    if _nt is not None:
        samples = _nt.NestedTensor(parts)
    else:  # pragma: no cover
        samples = parts
    return {"samples": samples}


def read_av_latent_meta(path: str) -> dict:
    """只读边车的**头 metadata**（不实例化 video/audio 张量）。

    B1 谱系簿记的读取侧：断点续跑换源、拼片选段、事后复现时，
    「这个文件是谁、在哪一版、承接哪一段」不必为它构造 NestedTensor。
    ⚠ 2026-10-14 实测修正：safetensors mmap 下全量 `load_file` 同样不物化数据
    ⇒ 计时与全量加载**基本无差**（本机热缓存甚至更慢）——本函数的价值在谱系本身
    与「扫描器不碰张量对象」的干净 API，**不在速度**；冷缓存/网络盘未测。
    旧 format 1 头（无谱系键）同样可读 ⇒ 向后兼容。
    """
    from safetensors import safe_open
    with safe_open(path, framework="pt", device="cpu") as f:
        if _LATENT_META_KEY not in f.keys():
            raise ValueError("%s 不是本工具写的 AV latent（缺少元数据）。" % path)
        raw = bytes(f.get_tensor(_LATENT_META_KEY).tolist())
    return json.loads(raw.decode("utf-8"))


def trim_head_frames(images: torch.Tensor, frames: int) -> torch.Tensor:
    """裁掉 IMAGE 的前 ``frames`` 帧（沿第 0 维）。

    续接段的前 ``trim_frames`` 帧是对上一段尾部的**重生成**（实测逐帧 MAE ≈ 6/255），
    它们是钉住区的产物，不属于新内容 —— 不裁就会在拼接处看到约 0.9s 重播。
    """
    frames = int(frames)
    if frames <= 0:
        return images
    n = int(images.shape[0])
    if frames >= n:
        raise ValueError(
            "要裁 %d 帧，但本段只有 %d 帧 —— 裁完就没画面了。\n"
            "    检查 trim_frames 是否误填成整段长度。" % (frames, n)
        )
    return images[frames:]


def trim_audio_head(audio: Any, frames: int, fps: float = FPS) -> Any:
    """把 AUDIO 的前 ``frames`` 帧对应的采样点裁掉（与视频同裁，保住 A/V 同步）。"""
    frames = int(frames)
    if frames <= 0 or audio is None:
        return audio
    wf = audio["waveform"]
    sr = int(audio["sample_rate"])
    n = int(round(frames / float(fps) * sr))
    total = int(wf.shape[-1])
    if n >= total:
        raise ValueError(
            "要裁 %d 帧（%d 个采样点），但音频只有 %d 点。"
            % (frames, n, total)
        )
    return {"waveform": wf[..., n:], "sample_rate": sr}
