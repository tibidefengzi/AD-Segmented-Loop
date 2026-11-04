# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 ComfyUI-H3-Latent-Relay contributors
# 第三方出处与许可见 THIRD-PARTY-NOTICES.md
"""relay_core.upscale —— 画质域 AV latent 分块放大（拆包 → 逐块 → 回包）。

本模块由 `relay_core.py` 拆分而来（2026-10-07）；§22 的代码**逐字搬运**，未改任何算式。
2026-10-12 追加 §23「空间分块」（**新写的**，不是搬运）：长边单轴切条 + 核心区独占窗 +
余弦羽化，作为**不动时间上下文**的显存旋钮。
对外仍由 `relay_core/__init__.py` 全量再导出 ⇒ 旧调用点（`relay_core.xxx`）不变。
"""

from __future__ import annotations

import math

import torch

# ---------------------------------------------------------------------------
# 22 · 画质域 · AV latent 分块放大（时间维切块 + 重叠加权拼接）
# ---------------------------------------------------------------------------
#   背景：上游学习式 3D 潜空间放大器（MIT，`MinimaxH3LatentUpscaler3D`）
#   ① 只吃普通 [B,C,T,H,W]，接不住 H3 的 **AV 打包 latent（NestedTensor）**；
#   ② 内部分块**写死 32 帧**，用户无法按显存调块数。
#   故这里只做「拆包 → 逐块放大 → 回包」的适配层，**零 patch 第三方包**，
#   并把块数交还给用户。拼接数学与上游同口径（两侧 replicate 填充 + 线性渐变
#   权重 + 按累计权重归一），以免换实现就换接缝。
# ---------------------------------------------------------------------------

UPSCALE_CHUNK_FRAMES_DEFAULT = 32      # 上游内部分块口径 ⇒ 我们的默认块长
UPSCALE_OVERLAP_DEFAULT = 5            # = 3D 放大模型 temporal_kernel（实测权重形状第 3 维）


def max_upscale_chunks(total_frames: int, overlap: int = UPSCALE_OVERLAP_DEFAULT) -> int:
    """给定总 latent 帧数与重叠，返回**合法的最大块数**（每块至少还能留 1 帧非重叠区）。"""
    t = int(total_frames)
    ov = int(overlap)
    if t < 1:
        raise ValueError("total_frames 必须 ≥ 1，得到 %d。" % t)
    if ov < 0:
        raise ValueError("overlap 不能为负，得到 %d。" % ov)
    return max(1, t // (2 * ov + 1))


def upscale_chunk_plan(total_frames: int, chunks: int = 1,
                       overlap: int = UPSCALE_OVERLAP_DEFAULT) -> dict:
    """规划「沿时间维分块」的窗口（块数自选 + 数值合规校验，非法一律 raise）。

    块长 ``chunk_frames = ceil(T / chunks)``，实际块数 = ``ceil(T / chunk_frames)``
    （向上取整 ⇒ 末块可能更短，**不会**多出空块）。

    合规硬约束（fail-closed，报错都给可照做的建议值）：
      · ``chunks ≥ 1``；``overlap ≥ 0``
      · **每块帧数 ≥ 2·overlap + 1** —— 否则本块的重叠区会互相吞干净，
        拼接后出现「权重累计为 0」的帧（放大结果被静默丢成黑帧）。
    """
    t = int(total_frames)
    n = int(chunks)
    ov = int(overlap)
    if t < 1:
        raise ValueError("total_frames 必须 ≥ 1，得到 %d。" % t)
    if ov < 0:
        raise ValueError("overlap 不能为负，得到 %d。" % ov)
    if n < 1:
        raise ValueError("chunks 必须 ≥ 1（1 = 整段一次过），得到 %d。" % n)
    cf = -(-t // max(1, n))                                  # ceil
    if cf < 2 * ov + 1:
        n_max = max_upscale_chunks(t, ov)
        raise ValueError(
            "chunks=%d ⇒ 每块只有 %d 帧，塞不下两侧各 %d 帧重叠（需 ≥ %d 帧）。\n"
            "    overlap=%d 时最大合法块数 = %d（T=%d）；要更多块请同时调小 overlap。"
            % (n, cf, ov, 2 * ov + 1, ov, n_max, t))
    spans = []
    start = 0
    while start < t:
        seg_start, seg_end = start, min(t, start + cf)
        out_start, out_end = max(0, seg_start - ov), min(t, seg_end + ov)
        spans.append({
            "out_start": out_start, "out_end": out_end,
            "blend_head": seg_start - out_start, "blend_tail": out_end - seg_end,
        })
        start += cf
    return {"total_frames": t, "chunk_frames": cf, "n_chunks": len(spans),
            "requested_chunks": n, "overlap": ov, "spans": spans}


def temporal_tile_upscale(video: torch.Tensor, upscale_fn, plan: dict) -> torch.Tensor:
    """按 ``plan`` 逐块放大 ``video[B,C,T,H,W]`` 并加权拼回（时间维不变、只放大空间）。

    ``upscale_fn(seg)`` 收 ``[B,C,t,H,W]`` 吐 ``[B,C,t,H2,W2]``（**帧数不许变**，
    变了当场 raise —— 否则拼接会静默错位）。块内是模型的全部工作集 ⇒ 显存峰值 = 一块。
    """
    was4 = video.dim() == 4                           # [B,C,H,W] 单帧：与上游同口径，进 4D 出 4D
    if was4:
        video = video.unsqueeze(2)
    if video.dim() != 5:
        raise ValueError("期望视频 latent [B,C,T,H,W]（或单帧 [B,C,H,W]），得到 %s。" % (tuple(video.shape),))
    b, c, t, h, w = (int(x) for x in video.shape)
    if t != int(plan["total_frames"]):
        raise ValueError("plan 的帧数 %d 与 latent 的 %d 不符。" % (plan["total_frames"], t))
    ov = int(plan["overlap"])
    out_full = weight_full = None
    for sp in plan["spans"]:
        o0, o1 = int(sp["out_start"]), int(sp["out_end"])
        seg = _pad_frames(video, o0, o1, ov)      # 越界处复制首/末帧（上游同口径）
        got = upscale_fn(seg)
        if not torch.is_tensor(got):
            raise ValueError("upscale_fn 必须返回张量，得到 %s。" % type(got))
        if int(got.shape[2]) != int(seg.shape[2]):
            raise ValueError(
                "放大块改变了帧数（%d → %d）：本分块拼接只允许**空间**放大。"
                % (int(seg.shape[2]), int(got.shape[2])))
        n_valid = o1 - o0
        valid = got[:, :, ov:ov + n_valid]
        if int(valid.shape[2]) != n_valid:
            raise ValueError("块输出可用帧数不足：需要 %d，只有 %d。" % (n_valid, int(valid.shape[2])))
        wt = torch.ones(n_valid, device=video.device, dtype=video.dtype)
        bh, bt = int(sp["blend_head"]), int(sp["blend_tail"])
        if bh:
            wt[:bh] = torch.arange(1, bh + 1, device=video.device, dtype=video.dtype) / (bh + 1)
        if bt:
            wt[-bt:] = torch.arange(bt, 0, -1, device=video.device, dtype=video.dtype) / (bt + 1)
        view = (1, 1, n_valid, 1, 1)
        if out_full is None:
            out_full = torch.zeros(b, int(got.shape[1]), t, int(got.shape[3]), int(got.shape[4]),
                                   device=got.device, dtype=got.dtype)
            weight_full = torch.zeros(1, 1, t, 1, 1, device=got.device, dtype=got.dtype)
        out_full[:, :, o0:o1] += valid.to(out_full.dtype) * wt.view(view).to(out_full.dtype)
        weight_full[:, :, o0:o1] += wt.view(view).to(weight_full.dtype)
        del seg, got, valid
    if out_full is None:
        raise ValueError("plan 里没有任何块（spans 为空）。")
    zero_w = int((weight_full.squeeze() <= 0).sum())
    if zero_w:
        raise ValueError("拼接后有 %d 帧权重为 0（重叠配置不合规）。" % zero_w)
    out = out_full / weight_full.clamp(min=1e-8).to(out_full.dtype)
    return out.squeeze(2) if was4 else out


def _pad_frames(video: torch.Tensor, o0: int, o1: int, ov: int) -> torch.Tensor:
    """取原帧闭开区 ``[o0-ov, o1+ov)``，越界处**复制首/末帧**（与上游 replicate 同口径）。

    padded 序列 = [复制 ov] + 全序列 + [复制 ov]，其下标 i 对应原帧 ``i-ov``
    ⇒ 原帧区间 ``[o0-ov, o1+ov)`` 在 padded 里正好是 ``[o0, o1+2·ov)``，无需裁剪。
    """
    t = int(video.shape[2])
    if not (0 <= o0 <= o1 <= t):
        raise ValueError("窗口越界：需要 0 ≤ out_start ≤ out_end ≤ T，得到 %d/%d/%d。" % (o0, o1, t))
    if ov <= 0:
        return video[:, :, o0:o1].contiguous()
    pre = video[:, :, :1].expand(-1, -1, ov, -1, -1)
    post = video[:, :, -1:].expand(-1, -1, ov, -1, -1)
    padded = torch.cat([pre, video, post], dim=2)
    return padded[:, :, o0:o1 + 2 * ov].contiguous()


# ---------------------------------------------------------------------------
# 23 · 画质域 · 空间分块放大（长边单轴切条 + 核心区独占窗 + 余弦羽化）
# ---------------------------------------------------------------------------
#   动机（2026-10-12，追痕 18 段实盘）：§22 的时间分块是**唯一**的显存旋钮，而它恰恰
#   改画面 —— 3D 放大器的 TemporalConv（时间核 5）在块边界换上下文。低显存用户没有
#   「只省显存、不动画面」的档。空间分块沿**长边单轴**切条：每块的**时间轴完整**
#   （时间核的上下文一点不丢），代价换成**空间边界**的上下文变窄 —— 用重叠 + 核心区
#   独占窗 + 余弦羽化把接缝压进过渡带。
#
#   与 §22 的关系：两者可叠加，**时间分块在外、空间分块在内**（每个时间块内部再切空间
#   条）⇒ 峰值显存 = 一个时间块 × 一条空间块。
#
#   🔴 两条纪律（都写进节点 report，别只在注释里）：
#     · **尺寸必须与整段跑逐格一致** ⇒ 每块的请求尺寸由 `upscale_out_size` 反推，而不是
#       让上游按块自己算（否则各块四舍五入不同 ⇒ 拼不回原尺寸）。
#     · **切空间也会改画面**（边界处感受野变窄）⇒ 与 `chunks>1` 同一性质的显存旋钮，
#       不是「无副作用」档。
# ---------------------------------------------------------------------------

UPSCALE_TILE_OVERLAP_DEFAULT = 8       # 相邻空间条在长边上的重叠（latent 格）
UPSCALE_TILE_FEATHER_DEFAULT = 4       # 接缝羽化过渡带宽度（latent 格，1 格 ≈ 16 像素）
# 上游 `MinimaxH3LatentUpscaler3D` 的 `width`/`height` 是 `min=64`（像素）—— 空间分块的每块
# 都要**显式**给上游目标尺寸 ⇒ 块比这还小就连请求都发不出去（会报上游的校验错，读不懂）。
# 这里按「像素」存常量，换算交给 downsample（别把 16 写死进判据）。
UPSCALE_TILE_MIN_PX = 64


def upscale_out_size(h_in: int, w_in: int, mode: str, scale=None, width=None,
                     height=None, megapixels=None, align: int = 32,
                     downsample: int = 16) -> tuple:
    """复刻上游的目标尺寸口径，返回 ``(h_out, w_out)``（latent 格）。

    **逐行对照** ``Comfyui_Minimax_h3_latent_Upscaler/nodes/minimax_h3_latent_upscaler_3d.py``
    的 ``execute`` 第 541–569 行（三档 mode → 像素目标 → `align` 收边 → 再收到
    `downsample` 的倍数 → 整除）。空间分块必须**先知道整段的目标尺寸**，否则各块自己
    四舍五入出来的尺寸拼不回原样。
    """
    hi, wi = int(h_in), int(w_in)
    ds = int(downsample)
    if hi < 1 or wi < 1:
        raise ValueError("latent 边长必须 ≥ 1，得到 %dx%d。" % (hi, wi))
    if ds < 1:
        raise ValueError("downsample 必须 ≥ 1，得到 %d。" % ds)
    if mode == "scale by multiplier":
        if scale is None:
            raise ValueError("mode=scale by multiplier 需要 scale。")
        w_px = wi * ds * float(scale)
        h_px = hi * ds * float(scale)
    elif mode == "target dimensions":
        if width is None or height is None:
            raise ValueError("mode=target dimensions 需要 width 与 height。")
        w_px = float(width)
        h_px = float(height)
    elif mode == "megapixels":
        if megapixels is None:
            raise ValueError("mode=megapixels 需要 megapixels。")
        target_px = float(megapixels) * 1024 * 1024
        ar = wi / hi
        h_px = (target_px / ar) ** 0.5
        w_px = h_px * ar
    else:
        raise ValueError("未知 mode=%r（可选：scale by multiplier / target dimensions / megapixels）。" % (mode,))
    a = max(1, int(align))
    w_aligned = round(w_px / a) * a
    h_aligned = round(h_px / a) * a
    w_final = round(w_aligned / ds) * ds
    h_final = round(h_aligned / ds) * ds
    return max(1, int(h_final // ds)), max(1, int(w_final // ds))


def spatial_tile_plan(h_in: int, w_in: int, h_out: int, w_out: int, tiles: int = 1,
                      overlap: int = UPSCALE_TILE_OVERLAP_DEFAULT,
                      feather: int = UPSCALE_TILE_FEATHER_DEFAULT,
                      align: int = 32, downsample: int = 16) -> dict:
    """规划「沿**长边**单轴切条」的几何（输入裁剪 / 输出落位 / 核心区 / 请求尺寸）。

    几何三原则（缺一条就会在拼接处出鬼影或丢格）：
      1. **核心区独占**：每块只在自己核心区内取值（权重 1），核心区外的重叠余量全为 0
         —— 重叠只当**上下文**，不当融合原料（两块的预测叠加 = 半透明重影）。
      2. **边界在输出坐标里定义**：块 i 的核心上界 == 块 i+1 的核心下界（同一个整数
         `y`）⇒ 相邻两块在**同一条**羽化带上互补（一个 1→0、一个 0→1，权重和恒为 1）。
      3. **请求尺寸反推**：每块的输出边长 = 落在 `align/downsample` 网格上的整数，
         且**必须覆盖自己的核心**（向上取整，只放大不缩）。

    非法配置一律 raise（铁律七），并把「该调哪个参数」写进消息。
    """
    hi, wi, ho, wo = int(h_in), int(w_in), int(h_out), int(w_out)
    ds = max(1, int(downsample))
    n, ov, fe = int(tiles), int(overlap), int(feather)
    if hi < 1 or wi < 1 or ho < 1 or wo < 1:
        raise ValueError("latent 边长必须 ≥ 1，得到 in=%dx%d out=%dx%d。" % (hi, wi, ho, wo))
    if n < 1:
        raise ValueError("tiles 必须 ≥ 1（1 = 不切空间），得到 %d。" % n)
    if ov < 0:
        raise ValueError("overlap 不能为负，得到 %d。" % ov)
    if fe < 0:
        raise ValueError("feather 不能为负，得到 %d。" % fe)

    grid = max(2, -(-max(1, int(align)) // ds))      # 每块输出边长须是它的倍数（对齐 + 偶数）
    if (ds * grid) % max(1, int(align)):
        raise ValueError(
            "align=%d 与 downsample=%d 不匹配 ⇒ 空间分块的每块输出边长无法同时满足"
            "「align 的倍数」与「H3 的偶数」两条要求。请把 align 设回 32（默认）。"
            % (int(align), ds))
    axis = "H" if hi >= wi else "W"
    axis_in, axis_out = (hi, ho) if axis == "H" else (wi, wo)
    other_out = wo if axis == "H" else ho
    if axis_out % grid or other_out % grid:
        raise ValueError(
            "输出边长 %dx%d 不是 %d 的倍数 ⇒ 空间分块无法逐格对齐（align=%d，downsample=%d）。\n"
            "    请把 align 设回 32（默认），或调整放大倍数 / 目标尺寸。"
            % (ho, wo, grid, int(align), ds))

    half = max(1, fe // 2) if fe > 0 else 0
    stride = max(1, int(round(axis_in / n)))
    xs = sorted({0, axis_in} | {min(axis_in, i * stride) for i in range(1, n)})
    ratio = axis_out / axis_in
    # 🔴 先判「块数装不装得下」——这条与「重叠太窄」是**两种病、两种处方**（2026-10-12 自查抓到：
    #   旧写法在块数过多时也报「请把 spatial_overlap 调大」，而调大重叠只会更糟）。
    max_tiles = max(1, axis_out // grid)
    if len(xs) - 1 > max_tiles:
        raise ValueError(
            "spatial_tiles=%d ⇒ 要切 %d 条，但长边只有 %d 格输出、每条至少 %d 格核心 ⇒ 最多 %d 条。\n"
            "    请把 spatial_tiles 调到 ≤ %d。" % (n, len(xs) - 1, axis_out, grid, max_tiles, max_tiles))

    # 输出侧核心边界：先按比例取整、再收边到 grid、最后**强制严格递增**（保证每块核心非空）
    raw = [0] + [grid * int(round(x * ratio / grid)) for x in xs[1:-1]] + [axis_out]
    ys = [0]
    for idx in range(1, len(raw) - 1):
        room = axis_out - grid * (len(raw) - 1 - idx)
        ys.append(max(ys[-1] + grid, min(raw[idx], room)))
    ys.append(axis_out)

    out_tiles = []
    for i in range(len(ys) - 1):
        y0, y1 = ys[i], ys[i + 1]
        x0, x1 = xs[i], xs[i + 1]
        in0 = max(0, x0 - ov)
        in1 = min(axis_in, x1 + ov)
        if in1 - in0 < 2:
            raise ValueError(
                "第 %d 条切得太窄（输入 %d 格）⇒ 模型算不了。请把 spatial_tiles 调小或把 "
                "spatial_overlap 调小。" % (i + 1, in1 - in0))
        p = int(round(in0 * ratio))
        # 本块输出长度：取**最近**的 grid 倍数（不是向上取整 —— 取整越远，块内被拉伸得越多，
        # 与整段跑的内容差就越大）；再确认它至少覆盖「核心 + 右侧羽化带」，不够才往上补一格。
        need = y1 - p + (half if i < len(ys) - 2 else 0)
        o = max(grid, grid * int(round((in1 - in0) * ratio / grid)))
        if o < need:
            o = grid * max(1, -(-need // grid))
        if o * ds < UPSCALE_TILE_MIN_PX:
            raise ValueError(
                "第 %d 条太窄：输出只有 %d 像素，而上游放大器的 `width`/`height` 最小是 %d 像素。\n"
                "    请把 spatial_tiles 调小（长边越短，能切的条数越少）。"
                % (i + 1, o * ds, UPSCALE_TILE_MIN_PX))
        lo, hi_ = y0 - p, y1 - p
        if lo < 0 or hi_ > o:
            raise ValueError(
                "第 %d 条的核心区 [%d,%d) 超出本块输出 [0,%d) ⇒ 重叠太窄。请把 "
                "spatial_overlap 调大。" % (i + 1, lo, hi_, o))
        if i > 0 and lo - half < 0:
            raise ValueError(
                "第 %d 条的接缝羽化带（%d 格）超出本块输出 ⇒ 重叠太窄。请把 "
                "spatial_overlap 调到 ≥ %d。" % (i + 1, 2 * half, half + 1))
        # 🔴 判据必须落在**可用区** `use` 上，不是整块输出：本块输出可以超出画面末端
        #   （多出来的那几格会被丢弃），但**羽化带**必须落在画面内 —— 否则相邻两块
        #   在过渡带上不再互补。2026-10-12 自查抓到：旧写法用 `p + o > axis_out` 直接
        #   拒了「末段几条输出自然溢出」的**合法**配置，还给了错处方。
        if i < len(ys) - 2 and hi_ + half > min(o, axis_out - p):
            raise ValueError(
                "第 %d 条的尾部装不下羽化带（可用 %d 格、需要 %d 格）⇒ 重叠太窄或块数太多。\n"
                "    请把 spatial_overlap 调大、或把 spatial_tiles 调小。"
                % (i + 1, min(o, axis_out - p), hi_ + half))
        out_tiles.append({
            "in0": in0, "in1": in1, "p": p, "o": o, "lo": lo, "hi": hi_,
            "req_h": o if axis == "H" else other_out,
            "req_w": other_out if axis == "H" else o,
        })
    # 🔴 退化配置要**当场报错**，不许静默白跑：两条裁的是同一块 ⇒ 显存一点不省、算力翻倍。
    #   （2026-10-12 自查：`tiles=3, overlap=60` 会产 3 条一模一样的裁剪。）
    _crops = [(t["in0"], t["in1"]) for t in out_tiles]
    if len(set(_crops)) != len(_crops):
        raise ValueError(
            "有两条裁的是**同一块**（spatial_overlap=%d 太大）⇒ 显存一点不省、算力白翻倍。\n"
            "    请把 spatial_overlap 调小（一般取「接缝羽化 + 几格」即可）。" % ov)
    return {
        "axis": axis, "h_in": hi, "w_in": wi, "h_out": ho, "w_out": wo,
        "axis_out": axis_out, "other_out": other_out,
        "tiles_requested": n, "n_tiles": len(out_tiles), "overlap": ov,
        "feather": fe, "half": half, "align": int(align), "downsample": ds,
        "grid": grid, "tiles": out_tiles,
    }


def _core_window_1d(size: int, lo: int, hi: int, half: int, left_ramp: bool,
                    right_ramp: bool, dtype, device) -> torch.Tensor:
    """核心区独占窗（长度 ``size``）：核心内 1，核心外 0，边界做 ``2·half`` 宽余弦过渡。

    🔴 相邻两块的过渡带**区间相同、方向相反**（一个 1→0、一个 0→1，用同一个
    ``cos`` 参数化 ⇒ 逐点相加恒为 1）⇒ 融合不会把两份预测半透明叠加。首块不做左过渡、
    末块不做右过渡（那里没有邻居）。
    """
    w = torch.zeros(int(size), dtype=dtype, device=device)
    a0, b0 = max(0, int(lo)), min(int(size), int(hi))
    if b0 <= a0:
        raise ValueError("核心区为空（[%d,%d) 在长度 %d 内）——分块几何不合法。" % (lo, hi, size))
    w[a0:b0] = 1.0
    if left_ramp and a0 > 0 and half > 0:
        a, b = max(0, a0 - half), min(int(size), a0 + half)
        t = torch.linspace(0.0, 1.0, b - a, dtype=dtype, device=device)
        w[a:b] = 0.5 - 0.5 * torch.cos(t * math.pi)                 # 0 → 1
    if right_ramp and b0 < int(size) and half > 0:
        a, b = max(0, b0 - half), min(int(size), b0 + half)
        t = torch.linspace(0.0, 1.0, b - a, dtype=dtype, device=device)
        w[a:b] = 0.5 - 0.5 * torch.cos((1.0 - t) * math.pi)         # 1 → 0
    return w


def spatial_tile_upscale(video: torch.Tensor, upscale_fn, plan: dict) -> torch.Tensor:
    """按 ``plan`` 逐条裁剪 → 放大 → 按核心区窗口拼回（**时间维与帧数不动**）。

    ``upscale_fn(seg, req_h, req_w)`` 收 ``[B,C,T,h,w]`` 吐 ``[B,C,T,req_h,req_w]``
    （帧数或尺寸不符当场 raise —— 否则拼接会静默错位）。块内是模型的全部工作集 ⇒
    峰值显存 = 一条。
    """
    if video.dim() != 5:
        raise ValueError("期望视频 latent [B,C,T,H,W]，得到 %s。" % (tuple(video.shape),))
    b, c, t, h, w = (int(x) for x in video.shape)
    if (h, w) != (int(plan["h_in"]), int(plan["w_in"])):
        raise ValueError("plan 的输入尺寸 %dx%d 与 latent 的 %dx%d 不符。"
                         % (plan["h_in"], plan["w_in"], h, w))
    axis = plan["axis"]
    ho, wo = int(plan["h_out"]), int(plan["w_out"])
    axis_out = ho if axis == "H" else wo
    tiles = plan["tiles"]
    if not tiles:
        raise ValueError("plan 里没有任何块（tiles 为空）。")
    out_full = weight_full = None
    for idx, tile in enumerate(tiles):
        if axis == "H":
            seg = video[:, :, :, int(tile["in0"]):int(tile["in1"]), :].contiguous()
        else:
            seg = video[:, :, :, :, int(tile["in0"]):int(tile["in1"])].contiguous()
        got = upscale_fn(seg, int(tile["req_h"]), int(tile["req_w"]))
        if not torch.is_tensor(got):
            raise ValueError("upscale_fn 必须返回张量，得到 %s。" % type(got))
        if int(got.shape[2]) != t:
            raise ValueError("空间分块只允许放大**空间**，帧数不许变（%d → %d）。"
                             % (t, int(got.shape[2])))
        want = (int(tile["req_h"]), int(tile["req_w"]))
        if (int(got.shape[3]), int(got.shape[4])) != want:
            raise ValueError("上游返回的空间尺寸 %dx%d 与请求的 %dx%d 不符 —— 检查 align 与目标尺寸。"
                             % (int(got.shape[3]), int(got.shape[4]), want[0], want[1]))
        if out_full is None:
            out_full = torch.zeros(b, int(got.shape[1]), t, ho, wo,
                                   device=got.device, dtype=got.dtype)
            weight_full = torch.zeros(1, 1, 1, ho, wo, device=got.device, dtype=torch.float32)
        o, p = int(tile["o"]), int(tile["p"])
        use = min(o, axis_out - p)
        if use <= 0:
            raise ValueError("第 %d 条落在输出范围之外（p=%d，o=%d，轴长 %d）。"
                             % (idx + 1, p, o, axis_out))
        w1d = _core_window_1d(o, int(tile["lo"]), int(tile["hi"]), int(plan["half"]),
                              idx > 0, idx < len(tiles) - 1, torch.float32, got.device)[:use]
        view = (1, 1, 1, use, 1) if axis == "H" else (1, 1, 1, 1, use)
        win = w1d.view(view)
        if axis == "H":
            out_full[:, :, :, p:p + use, :] += got[:, :, :, :use, :].to(out_full.dtype) * win.to(out_full.dtype)
            weight_full[:, :, :, p:p + use, :] += win
        else:
            out_full[:, :, :, :, p:p + use] += got[:, :, :, :, :use].to(out_full.dtype) * win.to(out_full.dtype)
            weight_full[:, :, :, :, p:p + use] += win
        del seg, got, w1d
    zero_w = int((weight_full <= 0).sum())
    if zero_w:
        raise ValueError("拼接后有 %d 个输出格权重为 0（分块几何不合规）——请调大 spatial_overlap。"
                         % zero_w)
    return out_full / weight_full.clamp(min=1e-8).to(out_full.dtype)
