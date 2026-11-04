# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 ComfyUI-H3-Latent-Relay contributors
# 第三方出处与许可见 THIRD-PARTY-NOTICES.md
"""relay_core.metrics —— 🧪 实验层只读观测（DTW 对齐代价 / 外观统计 / 漂移曲线）。

本模块由 `relay_core.py` 拆分而来（2026-10-07）；代码**逐字搬运**，未改任何算式。
对外仍由 `relay_core/__init__.py` 全量再导出 ⇒ 旧调用点（`relay_core.xxx`）不变。
"""

from __future__ import annotations

import torch

from .seam import ANALYSIS_SHORT_SIDE, REPEAT_MIN_RUN, REPEAT_SCAN, _canonicalize, _sharpness

# ============================================================================
# 🧪 实验层（exp/seam-frontier）——默认全关，主干语义逐位不变
#
# 每个函数都在文件头注明：治什么 / 论文出处 / 为什么默认关。
# 判据：改动机理必须可证伪，且**零 GPU 可断言**（见 tests/test_experimental.py）。
# ============================================================================


def _dtw_feat(frames: torch.Tensor) -> torch.Tensor:
    """E3｜把 ``[T,H,W,C]`` 帧序列压成 DTW 用的 ``[T,D]`` 特征。

    口径与 ``observation_profile`` 一致：**下采样后的逐帧像素**（不取梯度/频域），
    因为 DTW 要回答的是「这两帧是不是同一画面」，像素差就是最直接的度量。
    展平维度随分辨率变 ⇒ 单测里只用**同源**序列对，不跨分辨率比较绝对代价。
    """
    f = frames.detach().float()
    if f.dim() != 4:
        raise ValueError("_dtw_feat 需要 [T,H,W,C]，得到 %s" % (f.shape,))
    return f.reshape(int(f.shape[0]), -1).contiguous()


def dtw_residual(a: torch.Tensor, b: torch.Tensor, max_steps: int = 64):
    """E3｜DTW 求两段序列的对齐路径，给「窗 vs 钉住区」的**序列级相似度量**。

    出处：调研 §11-A（DTW / 自相似矩阵是视频同步领域的成熟工具；
    Lai et al., ECCV 2018《Learning Blind Video Temporal Consistency》）。
    现有第 4 路 ``scan_head_repeat`` 只给**单点**（从 pin 起连续复现几帧）；
    DTW 给的是**整条对齐路径**的代价剖面 ⇒ 能补上第 4 路看不见的两种形态：
      ① 复现区**不连续**（中间夹新内容）——第 4 路的 ``run`` 遇到断点就报 0；
      ② 复现**强度**——同样的「连续 3 帧」，代价 0.001 与 0.10 是两回事。

    输入：``a``/``b`` 均为 ``[T, D]`` 的逐帧特征（如灰度下采样的帧向量）。
    返回 ``(align_cost, mean_cost)``：
      · ``align_cost`` = 最优路径的**平均每步代价**（已按路径长度归一）——
        与 ``len(a)-len(b)`` **无关**的量，这才是可跨窗口比较的相似度；
      · ``mean_cost``  = 总代价 ÷ 路径步数（同上，保留给想直接读 dp 的人）。

    🔴 对称性（2026-09-21 补记）：局部代价 ``|a−b|`` 对调换参数**不变** ⇒ 本函数
      对 ``(a, b)`` **双向对称**（``dtw_residual(a,b)[0] == dtw_residual(b,a)[0]``
      恒成立：DP 总代价矩阵转置不变 + 回溯 tie-break 镜像）。
      ⇒ **调用方不要重复调反向** —— 那两个数永远相等，反向调用是纯冗余（DTW 是 O(n²)）。
      性质证明与真实链实测证据见 ``head_repeat_dtw`` docstring。

    🔴 2026-09-20 语义更正（第一次接线时写错过，别再回潮）：
      旧版返回 ``stay_b`` = 路径里「a 走一步而 b 原地踏步」的次数。
      **它不是「a 里无法被 b 解释的帧数」**——实测 ``a=6,b=4`` 全异源时
      它恒等于 2（= ``Ta-Tb``），与内容无关，因为 b 自己的步进也能
      消化 a 的帧 ⇒ 这个量由**长度差**主导，是坏指标。
      现在改成代价量：单调、可归一、不随长度差假性抬升。
      构造样例见 ``tests/test_experimental.py`` E3 组（同源 < 异源；
      「a=b 追加 k 帧新内容」的代价随 k 单调升）。

    ⚠ 为什么只作**观测**、不进裁量契约（2026-09-20 本仓作者 要求）：
        ``detect_settle`` 的三路各有**体参考 + 比值门槛 + 衰减形态**约束，
        而 DTW 路径对「运动中的相似姿态」也给低代价 ⇒ 单独看会把
        「正常运动」误判成「残留」。故接进 TrimAV 的 report 只读数，
        不改任何裁量默认值。
    """
    if a.ndim != 2 or b.ndim != 2:
        raise ValueError("dtw_residual 需要 [T,D] 的二维特征，得到 %s / %s。" % (a.shape, b.shape))
    if int(a.shape[1]) != int(b.shape[1]):
        raise ValueError("特征维度不一致：%d ≠ %d。" % (int(a.shape[1]), int(b.shape[1])))
    Ta, Tb = int(a.shape[0]), int(b.shape[0])
    if Ta == 0 or Tb == 0:
        return 0, 0.0
    if max(Ta, Tb) > int(max_steps):
        raise ValueError("序列过长（%d×%d > %d）：DTW 是 O(n²)，实验档只在小窗上用。"
                         % (Ta, Tb, int(max_steps)))
    INF = float("inf")
    # dp[i][j] = a[:i] 与 b[:j] 的最小累积代价；mv[i][j] 记来自哪个方向
    dp = [[INF] * (Tb + 1) for _ in range(Ta + 1)]
    mv = [[0] * (Tb + 1) for _ in range(Ta + 1)]
    dp[0][0] = 0.0
    af, bf = a.detach().float(), b.detach().float()
    for i in range(1, Ta + 1):
        for j in range(1, Tb + 1):
            cost = float((af[i - 1] - bf[j - 1]).abs().mean())
            cand = (dp[i - 1][j - 1], dp[i - 1][j], dp[i][j - 1])
            k = min(range(3), key=cand.__getitem__)
            dp[i][j] = cand[k] + cost
            mv[i][j] = k            # 0 = 对角(都走)，1 = a 走 b 停，2 = b 走 a 停
    # 回溯只为求**路径步数**（用于把总代价归一成「每步代价」）。
    # 🔴 边界必须显式处理：循环条件只要求 `i > 0 or j > 0` ⇒ 单边归零时 **mv 还没被写过**
    #   （`mv[0][j]` / `mv[i][0]` 全是初始化值 0）⇒ 若照 mv 走会进「对角」分支把另一维也减一
    #   ⇒ i/j 变负后 Python 不报错（负下标静默取到列表尾）⇒ 路径长度算错。
    #   （2026-09-20 实测确诊的静默 bug；此前这里还统计过 stay_b 计数，
    #     已连同证伪一起删除——见 docstring「语义更正」。）
    i, j, steps = Ta, Tb, 0
    while i > 0 or j > 0:
        if i == 0:                       # a 已走完，只剩 b
            j -= 1
        elif j == 0:                     # b 已走完而 a 还有剩
            i -= 1
        else:
            k = mv[i][j]
            if k == 0:
                i, j = i - 1, j - 1
            elif k == 1:
                i -= 1
            else:
                j -= 1
        steps += 1
        if steps > (Ta + Tb) * 2:   # 防御：不该发生
            break
    total_cost = float(dp[Ta][Tb])
    per_step = total_cost / max(1, steps)
    return float(per_step), float(per_step)


def head_repeat_dtw(images: torch.Tensor, pin: int,
                    scan: int = REPEAT_SCAN,
                    short_side: int = ANALYSIS_SHORT_SIDE,
                    max_steps: int = 64) -> dict:
    """E3｜只读观测：算「窗 vs 钉住区」的 DTW 对齐代价（序列级、连续量）。

    为什么不是替代 ``scan_head_repeat``（0.5.0 的第 4 路）而是**并行读数**：
      第 4 路给的是**单点整数**——「从 pin 起连续复现几帧」，遇到断点就报 0。
      本函数给**整条路径的平均代价**：复现 3 帧时代价 0.001 与 0.10 是两件事，
      且「复现区不连续」也能从代价上看出来（第 4 路对这种形态完全瞎）。

    返回 dict（**不返回裁量**——本函数不出 settle，理由见 ``dtw_residual``
    「为什么只作观测」）：
      · ``align_cost``  = 窗→钉住区 最优路径的平均每步代价（小 = 像复现）
      · ``feat``        = 实际喂给 DTW 的特征形状（出问题时可复现）
      · ``frames``      = 参与比较的 (窗帧数, 钉住帧数）

    🔴 2026-09-21 删 ``reverse_cost``（本仓作者 拍板方案 a）——**对称性证明**：
      ``dtw_residual`` 的局部代价是 ``|a−b|``，对调换两个参数**不变**（对称）
      ⇒ DP 递推的总代价矩阵转置不变 ⇒ 最优总代价对称；回溯的 tie-break 在镜像
      输入下也镜像 ⇒ **两方向路径步数相等、平均代价恒等**。
      真实链三次实测两列逐位恒等（0.10644 / 0.08962 / 0.17440）。
      ⇒ 反向那次调用是**纯冗余计算**（DTW 是 O(n²)，白算一半）。
      旧 docstring 声称反向代价能「看出是否只是长度差造成的假象」——**该用途永不生效**：
      长度差假象已由 ``dtw_residual`` 自身的「按路径长度归一」消掉（见其 09-20 语义更正）。
      对称性作为**性质**锁进单测（``tests/test_experimental.py`` E3.2），不再逐档断言。

    画布退化 / 序列超长 / ``pin`` 非法 ⇒ 返回空 dict（调用方跳过该行，**不 raise**：
    这是 report 里的一行观测，不该让它炸掉整条链）。
    """
    pin = int(pin)
    if pin < 1:
        return {}
    n = int(images.shape[0])
    if n <= pin + REPEAT_MIN_RUN:
        return {}
    k = min(int(scan), n - pin)
    if k < REPEAT_MIN_RUN:
        return {}
    small = _canonicalize(images[: pin + k], int(short_side))
    zone = small[pin:]                      # 窗 [k,H,W,C]
    pinreg = small[:pin]                    # 钉住区 [pin,H,W,C]
    try:
        fa, fb = _dtw_feat(zone), _dtw_feat(pinreg)
        if max(fa.shape[0], fb.shape[0]) > int(max_steps):
            return {}
        align_cost, _ = dtw_residual(fa, fb, max_steps=int(max_steps))
    except Exception:  # 观测层绝不炸链
        return {}
    return {"align_cost": float(align_cost),
            "frames": [int(fa.shape[0]), int(fb.shape[0])],
            "feat": [int(fa.shape[0]), int(fa.shape[1])]}


def segment_appearance_stats(images: torch.Tensor, body_start: int = 40,
                             body_len: int = 40) -> tuple:
    """E4｜单段自报**外观统计三元组** ``(mean, std, sharpness)``，供 ``drift_curve`` 吃。

    为什么单独一个函数（而不是让调用方自己算）：``drift_curve`` 要的是
    **同一把尺子**量出来的逐段统计。各段自己算 mean/std/锐度，只要口径差一点
    （比如有人用整段、有人只用体区），斜率就会被口径差污染 ⇒ 结论不可比。
    本函数锁定口径：**只取体区**（跳过头部糊区/裁切区），中位数稳健，全纯 CPU。

    调 ``drift_curve([stats(seg0), stats(seg1), ...])`` 就是调研 §2 要的
    「漂移是否随段数复利」的定量答案。
    """
    f = images.detach().float()
    if f.dim() != 4:
        raise ValueError("segment_appearance_stats 需要 [N,H,W,C]，得到 %s" % (f.shape,))
    n = int(f.shape[0])
    lo = max(0, min(int(body_start), n))
    body = f[lo: min(n, lo + int(body_len))]
    if int(body.shape[0]) < 4:
        body = f                                  # 短段退化到整段（仍比没有强）
    mean_v = float(body.mean())
    std_v = float(body.std(unbiased=False))
    sharp_v = float(_sharpness(body).median())
    return (mean_v, std_v, sharp_v)


def drift_curve(stats, ref_idx: int = 0):
    """E4｜把「单缝 ≤0.008」扩成**沿段数的漂移曲线**（调研 §2「衡量标尺」）。

    ``stats``：每段的 ``(mean, std, sharpness)`` 三元组列表（按段号顺序）。
    返回 dict：
      · ``mean_shift`` / ``std_ratio`` / ``sharp_ratio``：相对基准段（默认第 0 段）的逐段偏移；
      · ``slope`` / ``std_slope`` / ``sharp_slope``：对段号做最小二乘的**漂移斜率**
        （每段的平均偏移量）——这是「是否随段数复利」的定量答案，对标
        VBench(arXiv:2311.17982) 的 subject/background consistency 维度思路。
        ⚠ ``slope`` 走**绝对量纲**（受分辨率/内容影响，只适合同片内比）；
          ``std_slope``/``sharp_slope`` 走**比值** ⇒ 跨片可比，优先看这两条。
    少于 2 段时所有斜率记为 0.0（无从判断趋势，不伪造）。
    """
    _EMPTY = {"mean_shift": [], "std_ratio": [], "sharp_ratio": [],
              "slope": 0.0, "std_slope": 0.0, "sharp_slope": 0.0}
    if not stats:
        return _EMPTY
    ref = stats[int(ref_idx)] if 0 <= int(ref_idx) < len(stats) else stats[0]
    m0, s0, q0 = [float(x) for x in ref]
    ms, sr, qr = [], [], []
    for m, s, q in stats:
        m, s, q = float(m), float(s), float(q)
        ms.append(m - m0)
        sr.append((s / s0) if s0 > 1e-12 else 1.0)
        qr.append((q / q0) if q0 > 1e-12 else 1.0)
    n = len(ms)
    if n < 2:
        return {"mean_shift": ms, "std_ratio": sr, "sharp_ratio": qr,
                "slope": 0.0, "std_slope": 0.0, "sharp_slope": 0.0}
    xs = [float(i) for i in range(n)]
    xb = sum(xs) / n

    def _ls(y):
        """最小二乘斜率（对段号）。den 恒 >0（段号互异），只防 y_den=0 的常数串。"""
        yb = sum(y) / len(y)
        num = sum((x - xb) * (v - yb) for x, v in zip(xs, y, strict=False))
        den = sum((x - xb) ** 2 for x in xs)
        return (num / den) if den > 1e-12 else 0.0

    return {"mean_shift": ms, "std_ratio": sr, "sharp_ratio": qr,
            "slope": float(_ls(ms)),
            # 三条斜率对称给出：mean 是**绝对量纲**（受分辨率/内容影响，跨片不可直接比），
            # ratio 是**无量纲**（跨片可比）⇒ 「漂移是否复利」优先看这两条。
            "std_slope": float(_ls(sr)),
            "sharp_slope": float(_ls(qr))}
