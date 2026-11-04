# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 ComfyUI-H3-Latent-Relay contributors
# 第三方出处与许可见 THIRD-PARTY-NOTICES.md
"""relay_core.bridge —— 拷贝桥（latent 硬拷贝 + 噪声掩码 + 前缀权重族）· build_continue_latent。

本模块由 `relay_core.py` 拆分而来（2026-10-07）；代码**逐字搬运**，未改任何算式。
对外仍由 `relay_core/__init__.py` 全量再导出 ⇒ 旧调用点（`relay_core.xxx`）不变。
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple
import torch

from ._grid import pixel_frames
from .latent import audio_from_latent, audio_tail_from_latent, video_from_latent, video_tail_from_latent
from .plan import _voice_anchor_tail, match_stats
from .seam import describe_latent

# ---------------------------------------------------------------- 0.4.0 拷贝桥
# 机制出处（拆解吸收，均开放许可）：
#   · AIMixer/ComfyUI_MiniMaxH3_Director（Apache-2.0）——latent 硬拷贝 + 噪声掩码 +
#     音频尾拷贝 + NestedTensor 双流打包；
#   · comfyui-minimax-h3-audio-T8（native_masked_context）——掩码走 **ComfyUI 原生
#     H3 契约**（mask=0 保留 / 1 生成，MiniMaxH3.scale_latent_inpaint /
#     mask_row_values，0.30+），与具体采样器无关 → 通用兼容；
#     且 T8 用全 0 硬锁（钉住区零重绘）——与我们 0.3.x 实测「复现=漂移源」同向，
#     设为默认；Director 的 1.0→seam_min 锥形作为实验档保留。
#   · 消费端实测：SelfLiftH3Sampler 原生读 latent["noise_mask"]（[B,1,T,H,W]，
#     post-CFG hook 把 0 区每步钉回 clean anchor）——无需改任何采样器。
# 音频：尾随拷贝只作采样上下文（掩码只做视频流）；可见的声画拼接仍归 TrimAV/组装层。
#
# 0.4.3 新增 "ramp"（噪声斜坡，方向一：历史 = 带噪声等级的软证据）：
#   原生契约本来就支持**连续**掩码值——宿主源码两处实证（2026-09-15 读码）：
#     comfy/ldm/minimax/model.py  forward()：
#       "masked rows run at their own strength: mask value m puts a row at
#        sigma = m * sigma_stream" → 逐 token 的 timestep 标签 = 1 − m·σ；
#     comfy/model_base.py  MiniMaxH3.scale_latent_inpaint()：
#       x_blend_weight = (token_grid_mask − denoise_mask)/(1 − denoise_mask)
#       ——m∈(0,1) 走连续混合，不是四舍五入到两态。
#   外加外层每步输出 blend out = out·m + anchor·(1−m)（RePaint 式逐步回锚）。
#   因此 ramp 档的 m∈(0,1) 语义 = Diffusion Forcing（arXiv:2407.01392）的
#   逐 token 噪声等级 + SDEdit（arXiv:2108.01073）的按强度重绘：缝侧 token
#   以 ramp_top 强度被模型轻度 harmonize（重上色/微调曝光），远端仍硬钉。
#   与 taper 的区别：taper 头部 m=1.0 = 无锚全重绘；ramp 全程 m≤ramp_top<1，
#   每一步都被 (1−m) 权重锚回拷贝尾——是"软证据"，不是"没证据"。
SEAM_TAPER_TOKENS: int = 4
SEAM_MIN_MASK: float = 0.10
SEAM_MIN_FLOOR: float = 0.0
SEAM_RAMP_TOP: float = 0.25
SEAM_RAMP_TOP_MIN: float = 0.0     # 0 = 显式退化为 hard
SEAM_RAMP_TOP_MAX: float = 1.0     # 1.0 是**理论最优**（FIFO-Diffusion Thm 3.3：误差以噪声等级差为上界；
                                   # ramp_top=1.0 让斜坡恰好抵达满噪声 ⇒ 窗边界断崖归零）。
                                   # ⚠ 旧注释写「m=1 即无锚，归 taper 管」是**误判**：ramp 只有最后
                                   # 一个 token 到 top，前面的 token 仍被 (1−m) 锚回拷贝尾；taper 是头部 m=1。
                                   # ⚠ 想在大 ramp_top 下不恶化，必须**同时加长窗口**（窗内台阶 = ramp_top/(n−1)）。
AUDIO_RELEASE_TICKS: int = 6

# —— 0.5.0 `blend`：重叠区双向融合（RESEARCH_seam_frontier §4 方向四）——
# 与 ramp 的关系：**同一套掩码语义、不同的权重曲线**。
#   · ramp  = 线性升（0 → top）
#   · blend = **窗形升**（smoothstep / Hann 式 S 曲线，两端导数为 0 ⇒ 过渡更柔）
# 理论出处：FlowLong / Unified Long Video Inpainting 的滑窗 **Hamming 加权混合**
#   （arXiv:2511.03272 —— 与本仓库最贴的一篇）；SEINE(arXiv:2310.20700) 把过渡段当 inpainting；
#   SynCoS(arXiv:2503.08605) 多段同步耦合共享首尾锚帧；GLC-Diffusion(arXiv:2501.05484) 时序耦合去噪。
# 共同做法：重叠区由**两个独立估计**加权平均，权重取**窗函数**（端点导数小 ⇒ 过渡柔）。
# ⚠ 诚实标注：在本仓库的掩码表述下，blend 与 ramp 是**同一族的不同曲线**，
#   不是新机制；能否胜过 `ramp@0.5`（跳帧 14.9×）是**实测问题**。
SEAM_BLEND_TOP: float = 0.50       # 缝端模型占比（= ramp_top 的对应物）
SEAM_BLEND_TOP_MAX: float = 1.0
BLEND_SHAPES: Tuple[str, ...] = ("smoothstep", "hann")
BLEND_SHAPE_DEFAULT: str = "smoothstep"

# —— 0.6.0 `window`：**对称窗**（两端低、中心高）——
# 与 ramp/blend 的本质区别：那两档**单调上升**（缝端最自由），本档缝端**回落**（缝端重新钉牢）。
# 依据：VideoMerge(arXiv:2503.09926) 正弦窗；Diff-VF(arXiv:2608.05976) WWS
#   「中心权重高、边界权重低」+ 消融「移除 WWS → 窗口边界突然跳跃」（= 本项目症状）。
SEAM_WINDOW_TOP: float = 0.50      # 窗函数峰值（中心处允许的最大重绘自由度）
WINDOW_SHAPES: Tuple[str, ...] = ("sine", "hann")
WINDOW_SHAPE_DEFAULT: str = "sine"

MASK_MODES: Tuple[str, ...] = ("hard", "taper", "ramp", "blend", "window")


def prefix_taper_weights(
    steps: int,
    taper: int = SEAM_TAPER_TOKENS,
    seam_min: float = SEAM_MIN_MASK,
) -> Tuple[float, ...]:
    """拷贝前缀的掩码权重：头部 1.0（**完全重绘**，反正被裁），线性降到缝端 seam_min。

    ⚠️ **这不是「软一点的硬锁」——taper 档下钉住区没有被钉住。**
    掩码语义是 ``denoised = 模型生成 * m + 上段尾 * (1-m)``：m=0 才钉住，m=1 是重绘。
    所以 taper 档每一帧都留 ``seam_min``~100% 的重绘自由度（seam_min=0.3 即缝端仍 30% 重绘），
    段首会被模型改写 ⇒ 观感「续不上」，而 TrimAV 仍按窗口帧数照裁 ⇒ 顺带裁掉真实剧情。
    本档**只用于「渐进接管」对照实验**；真续接请用 ``mask_mode="hard"``。
    （2026-09-15：产线曾误把 taper 当默认跑了 23 次，见 CHANGES 0.4.2 文档节。）
    """
    n = int(steps)
    if n < 1:
        return ()
    taper = max(1, min(int(taper), n))
    head = n - taper
    floor = max(SEAM_MIN_FLOOR, min(1.0, float(seam_min)))
    weights = [1.0] * head
    weights.extend(1.0 + (floor - 1.0) * (float(i + 1) / float(taper)) for i in range(taper))
    return tuple(weights)


def prefix_ramp_weights(
    steps: int,
    ramp_top: float = SEAM_RAMP_TOP,
    ramp_tokens: int = 0,
) -> Tuple[float, ...]:
    """0.4.3 噪声斜坡权重：窗远端 0.0（硬钉）线性升到缝端 ramp_top。

    与 taper 的本质区别：taper 是"重绘自由度"刻度（头 1.0 = 无锚全重绘）；
    ramp 是"重噪强度"刻度——每帧都留 (1−m) 的锚权重，每一步都被采样器按
    (1−m) 拉回拷贝尾，m 只决定该 token 以多大 sigma 参与去噪。
    ramp_top 必须落在 [0, 1]（越界即按端点截断）：m=0 退化成 hard——远端硬钉
    且缝端也无重绘自由度；m→1 则锚权重 (1−m)→0，等于没有锚，那是 taper 干的事，
    widget 侧把上限收到 0.95 防呆。

    ramp_tokens>0 时只让最后这么多 token 参与斜坡，更早的钉住 token 恒 0（硬钉）——
    用于"只松缝、锁运动"的窄斜坡；默认 0 = 整个窗口铺开。

    端点约定：整窗铺开时 token i 取 top·i/(n−1)——远端严格 0（硬钉）、缝端严格 top
    （n=1 时唯一 token 即缝端取 top）；窄斜坡时最后 k 个 token 取 top·(j+1)/k（渐进软化）。
    """
    n = int(steps)
    if n < 1:
        return ()
    top = min(SEAM_RAMP_TOP_MAX, max(SEAM_RAMP_TOP_MIN, float(ramp_top)))
    if int(ramp_tokens) <= 0:
        if n == 1:
            return (float(top),)
        return tuple(top * float(i) / float(n - 1) for i in range(n))
    span = max(1, min(int(ramp_tokens), n))
    weights = [0.0] * (n - span)
    weights.extend(top * float(j + 1) / float(span) for j in range(span))
    return tuple(weights)


def _window_shape(x: float, shape: str) -> float:
    """把 ``x ∈ [0,1]`` 映射成**端点导数为 0**的 S 曲线（单调、0→1）。"""
    x = min(1.0, max(0.0, float(x)))
    if shape == "hann":
        import math
        return 0.5 - 0.5 * math.cos(math.pi * x)      # (1−cos πx)/2
    return x * x * (3.0 - 2.0 * x)                     # smoothstep


def prefix_window_weights(
    steps: int,
    top: float = SEAM_WINDOW_TOP,
    shape: str = WINDOW_SHAPE_DEFAULT,
) -> Tuple[float, ...]:
    """0.6.0 `window` 档：**对称窗**——两端低、中心高（非单调）。

    与 ramp / blend 的**本质区别**：那两档都是**单调上升**（远端钉住、缝端最自由）；
    本档是**窗函数**，缝端那一头重新回到低位（= 缝端重新钉牢）。

    依据（2026-09-19 联网核实原文）：
      · **VideoMerge**(arXiv:2503.09926)：「用 **sine weighting** 替代线性加权……
        minimizes artifacts due to **abrupt change in semantics**」；
      · **Diff-VF**(arXiv:2608.05976) 的 WWS：「窗口**中心**的帧获得**更高**权重，
        靠近**边界**的帧获得**较低**权重」，且消融证明
        「**移除 WWS 导致窗口边界处出现突然的「跳跃」**」——正是本项目的症状。

    ⚠️ 诚实标注：我们的钉住窗**整窗都会被 TrimAV 裁掉**（不进成片），
    所以本档的作用是**塑造模型状态**，而不是"让可见区变柔"。
    缝端那一头重新钉牢（窗函数在 i=n−1 处回落到低位）才是它相对 ramp 的关键差别。
    """
    n = int(steps)
    if n < 1:
        return ()
    t = min(1.0, max(0.0, float(top)))
    if n == 1:
        return (float(t),)
    import math
    if shape == "hann":
        # Hann：端点严格 0（完全硬钉），中心 1
        return tuple(t * (0.5 - 0.5 * math.cos(2.0 * math.pi * (i + 0.5) / n))
                     for i in range(n))
    # sine：端点低但不为 0（留一点自由度，避免整窗死钉）
    return tuple(t * math.sin(math.pi * (i + 0.5) / n) for i in range(n))


def prefix_blend_weights(
    steps: int,
    blend_top: float = SEAM_BLEND_TOP,
    blend_tokens: int = 0,
    shape: str = BLEND_SHAPE_DEFAULT,
) -> Tuple[float, ...]:
    """0.5.0 重叠区**双向融合**权重（RESEARCH_seam_frontier §4 方向四）。

    与 `prefix_ramp_weights` 的关系：**同一套掩码语义（``输出 = 生成·m + 上段尾·(1−m)``）、
    不同的权重曲线**——
      · ramp  = **线性**升（``top·i/(n−1)``）
      · blend = **窗形**升（smoothstep / Hann，两端导数为 0）

    理论依据：重叠区由**两个独立估计**加权平均时，权重取**窗函数**（Hamming 类）——
    端点处权重变化率为 0 ⇒ 与"窗外"的衔接没有折角 ⇒ 过渡更柔
    （FlowLong / Unified Long Video Inpainting 的滑窗 Hamming 混合，arXiv:2511.03272）。

    ``blend_tokens>0`` 时只让最后这么多 token 参与融合，更早的恒 0（硬钉）——
    用于"只融缝、锁运动"。

    端点约定：整窗铺开时 token i 取 ``top·S(i/(n−1))``——远端严格 0、缝端严格 top；
    窄融合时最后 k 个 token 取 ``top·S((j+1)/k)``。
    """
    n = int(steps)
    if n < 1:
        return ()
    top = min(SEAM_BLEND_TOP_MAX, max(0.0, float(blend_top)))
    sh = shape if shape in BLEND_SHAPES else BLEND_SHAPE_DEFAULT
    if int(blend_tokens) <= 0:
        if n == 1:
            return (float(top),)
        return tuple(top * _window_shape(float(i) / float(n - 1), sh) for i in range(n))
    span = max(1, min(int(blend_tokens), n))
    weights = [0.0] * (n - span)
    weights.extend(top * _window_shape(float(j + 1) / float(span), sh) for j in range(span))
    return tuple(weights)


def _nested_pair(video: torch.Tensor, audio: torch.Tensor, template: Any = None) -> Any:
    """打包 AV 双流为 NestedTensor（ComfyUI 运行时），离线环境退化为 list。"""
    try:
        from comfy.nested_tensor import NestedTensor

        return NestedTensor((video, audio))
    except Exception:
        cls = type(template) if template is not None else None
        if cls is not None and cls is not torch.Tensor:
            try:
                return cls((video, audio))
            except Exception:
                pass
    return [video, audio]


def build_continue_latent(
    target: Any,
    prev: Any,
    frames: int,
    mask_mode: str = "hard",
    taper: int = SEAM_TAPER_TOKENS,
    seam_min: float = SEAM_MIN_MASK,
    pin_audio: bool = True,
    ramp_top: float = SEAM_RAMP_TOP,
    ramp_tokens: int = 0,
    blend_top: float = SEAM_BLEND_TOP,
    blend_tokens: int = 0,
    blend_shape: str = BLEND_SHAPE_DEFAULT,
    window_top: float = SEAM_WINDOW_TOP,
    window_shape: str = WINDOW_SHAPE_DEFAULT,
    anchor_latent: Optional[Any] = None,
    anchor_blend: float = 1.0,
    voice_anchor: Optional[Any] = None,
) -> Tuple[Dict[str, Any], int, str]:
    """0.4.0 拷贝桥：把上一段 AV 尾部**逐位拷贝**进本段初始 latent + 噪声掩码。

    返回 ``(latent, covered, report)``；``covered`` = 应裁帧数（接 TrimAV 的 trim_frames）。

    与复合桥的 conditioning 钉帧路线（原 H3RelayMotionContext）的本质区别：钉住区**不重绘**——
    mask=0 区每步被采样器钉回拷贝进来的上段尾部 latent，0.3.x 实测的
    「复现发糊/漂移」这一类伪影从机制上消失。三条硬约束（违反即 raise）：
      1. ``frames`` 落在 5+17k 网格且尾段起点 5-token 对齐（复用 video_tail_from_latent）；
      2. 上段与本段分辨率一致；
      3. 拷贝前缀必须给新内容留至少 1 个 token。

    ``mask_mode="ramp"``（0.4.3）把钉住区从"硬事实"改成"软证据"：掩码连续值按
    原生契约就是逐 token 的 sigma 标签（详见上方机制注释），缝侧轻度 harmonize、
    远端硬钉，全程有锚——治硬接缝处色档/曝光"台阶化"。语义与 taper 相反，见
    ``prefix_ramp_weights``。
    """
    if mask_mode not in MASK_MODES:
        raise ValueError(
            "mask_mode 只认 %s，得到 %r" % (" / ".join(MASK_MODES), mask_mode)
        )
    tv = video_from_latent(target)
    pv = video_from_latent(prev)
    if (int(tv.shape[3]), int(tv.shape[4])) != (int(pv.shape[3]), int(pv.shape[4])):
        raise ValueError(
            "拷贝桥禁止跨分辨率：上段 %dx%d ≠ 本段 %dx%d"
            % (int(pv.shape[4]) * 16, int(pv.shape[3]) * 16,
               int(tv.shape[4]) * 16, int(tv.shape[3]) * 16)
        )
    blocks, _offsets, covered = video_tail_from_latent(prev, frames)
    steps = len(blocks)
    total_t = int(tv.shape[2])
    if steps >= total_t:
        raise ValueError(
            "拷贝前缀 %d 步占满本段 %d 步 → 没有新内容可生成；缩短 context_frames 或加长本段"
            % (steps, total_t)
        )

    # tv.clone() 是必须的：下面要原位写前缀，不能污染调用方的 latent。
    # 峰值 ≈ 1×target 视频流（latent 在 GPU 时即 1×显存，尾段 blocks 本身已占 steps/total）。
    video = tv.clone()
    tail_v = torch.cat(blocks, dim=2).to(device=video.device, dtype=video.dtype)

    # 0.5.0 统计纠偏（方向二）：把拷贝前缀的逐通道矩拉向全局锚段。
    # 纠偏只作用于被裁掉的钉住前缀——它是消耗品条件，绝不碰上一段成片；
    # 而采样时每步钉回的就是这份"已复位色档"的上下文 → 本段新内容随之 harmonize。
    stats_desc = ""
    if anchor_latent is not None:
        av = video_from_latent(anchor_latent)
        if (int(av.shape[1]) != int(video.shape[1])):
            raise ValueError(
                "anchor_latent 有 %d 通道，本段有 %d —— 不是同一底模产出的 H3 视频 latent。"
                % (int(av.shape[1]), int(video.shape[1]))
            )
        tail_v = match_stats(tail_v, av, blend=anchor_blend).to(tail_v.dtype)
        stats_desc = "；统计纠偏 blend=%.2f（锚 %s）" % (
            max(0.0, min(1.0, float(anchor_blend))), describe_latent(anchor_latent))
    video[:, :, :steps] = tail_v

    audio = None
    rt = 0
    va_desc = ""
    if pin_audio:
        # 只有真的要钉音频时才要求 target 带音频流——pin_audio=False
        # 必须允许纯视频 latent 走通（参数语义）。
        audio = audio_from_latent(target).clone()
        if voice_anchor is not None:
            # 0.6.1 声锚：钉住的音频前缀同样改用声锚尾窗（与 plan_relay 的
            # audio_ref 同源 —— 两处读的都是"上段尾"，只改一处会打架）。
            a_tail, rt, _raw, _vwin = _voice_anchor_tail(voice_anchor, int(frames))
            va_desc = "；音频前缀改用声锚（voice_anchor）尾 %d 步" % int(rt)
            if _vwin.get("note"):
                va_desc += "（%s）" % _vwin["note"]
        else:
            a_tail, rt, _overhang, _raw, _grid_off = audio_tail_from_latent(
                prev, int(frames), pixel_frames(int(pv.shape[2])))
        rt = max(0, min(int(rt), int(audio.shape[-1]) - 1))
        if rt > 0:
            audio[..., :rt] = a_tail[..., :rt].to(device=audio.device, dtype=audio.dtype)
    else:
        if voice_anchor is not None:
            va_desc = "；⚠ voice_anchor 已接但 pin_audio=False ⇒ 本段不钉音频，声锚被忽略"
        try:
            audio = audio_from_latent(target)     # 有则原样保留（不拷贝、不改动）
        except ValueError:
            audio = None                          # 纯视频桥：只出视频流

    # 掩码与 latent 同设备同 batch：GPU latent + CPU 掩码会在采样器里炸或静默错位
    vmask = torch.ones((int(tv.shape[0]), 1, total_t, int(tv.shape[3]), int(tv.shape[4])),
                       dtype=torch.float32, device=tv.device)
    if mask_mode == "hard":
        vmask[:, :, :steps] = 0.0
        mask_desc = "硬锁（全 0，钉住区零重绘）"
    elif mask_mode == "ramp":
        w = prefix_ramp_weights(steps, ramp_top, ramp_tokens)
        # 权重直接建在掩码设备上，省一次跨设备拷贝（latent 在 GPU 时 vmask 也在 GPU）
        vmask[:, :, :steps] = torch.tensor(w, dtype=torch.float32,
                                           device=vmask.device).view(1, 1, steps, 1, 1)
        mask_desc = "噪声斜坡 0.00→%.2f（每步锚回 (1−m)，软证据档）" % w[-1]
    elif mask_mode == "blend":
        w = prefix_blend_weights(steps, blend_top, blend_tokens, blend_shape)
        vmask[:, :, :steps] = torch.tensor(w, dtype=torch.float32,
                                           device=vmask.device).view(1, 1, steps, 1, 1)
        mask_desc = "重叠区双向融合 0.00→%.2f（%s 窗，两端导数 0 ⇒ 过渡柔）" % (w[-1], blend_shape)
    elif mask_mode == "window":
        w = prefix_window_weights(steps, window_top, window_shape)
        vmask[:, :, :steps] = torch.tensor(w, dtype=torch.float32,
                                           device=vmask.device).view(1, 1, steps, 1, 1)
        mask_desc = ("对称窗 %s（峰值 %.2f）：两端低、中心高 ⇒ **缝端重新钉牢**，"
                     "中心留自由度" % (window_shape, max(w) if w else 0.0))
    else:
        w = prefix_taper_weights(steps, taper, seam_min)
        # 权重直接建在掩码设备上，省一次跨设备拷贝（latent 在 GPU 时 vmask 也在 GPU）
        vmask[:, :, :steps] = torch.tensor(w, dtype=torch.float32,
                                           device=vmask.device).view(1, 1, steps, 1, 1)
        mask_desc = "锥形 %.2f→%.2f（taper=%d）" % (w[0], w[-1], taper)

    out = dict(target) if isinstance(target, dict) else {}
    if audio is None:
        out["samples"] = video          # 纯视频路径：不打包 NestedTensor
    else:
        out["samples"] = _nested_pair(video, audio, tv)
    out["noise_mask"] = vmask
    report = (
        "[H3 Relay] 拷贝桥：写入 %d 步（%d 帧）视频尾 + %d 音频 tick（上下文用%s）；"
        "掩码 %s%s%s；trim=%d"
        % (steps, covered, rt,
           "" if audio is not None else "／本段无音频流", mask_desc, stats_desc,
           va_desc, covered)
    )
    return out, int(covered), report
