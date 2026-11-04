# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 ComfyUI-H3-Latent-Relay contributors
# 第三方出处与许可见 THIRD-PARTY-NOTICES.md
"""relay_core._grid —— 网格常量与网格工具（H3 VAE 的 5+17k 时序网格、窗口吸附、常量导出键）。

本模块由 `relay_core.py` 拆分而来（2026-10-07）；代码**逐字搬运**，未改任何算式。
对外仍由 `relay_core/__init__.py` 全量再导出 ⇒ 旧调用点（`relay_core.xxx`）不变。
"""

from __future__ import annotations

from typing import List, Optional, Tuple
import itertools

# ---------------------------------------------------------------- 网格常量
# 与 H3 VAE 的时序跨度一致。改这里等于改 H3 的 VAE，必须同步。
FRAME_PER_TOKEN: Tuple[int, ...] = (1, 4, 4, 4, 4)
FPS: float = 24.0
FRAME_RESCALE: float = 5.0 / 3.0   # 像素帧 → 音频 latent 步的换算
AUDIO_HZ: float = 40.0             # 音频 latent 的采样率（步/秒）

# 合法窗口，降序。必须与 FRAME_PER_TOKEN 自洽（见 self_check）。
GUIDE_RUNS: Tuple[int, ...] = (124, 107, 90, 73, 56, 39, 22, 5, 1)

# Apt_Preset 在 latent 里留下的导出尾段键（有则优先复用，避免二次切片）
KEY_EXPORT_TAIL_VIDEO = "apt_h3_export_tail_latent"
KEY_EXPORT_TAIL_AUDIO = "apt_h3_export_tail_audio_latent"
KEY_EXPORT_FRAMES = "apt_h3_export_context_frames"

# ---------------------------------------------------------------- 网格工具
def pixel_frames(latent_t: int) -> int:
    """latent token 数 → 覆盖的像素帧数。"""
    return sum(FRAME_PER_TOKEN[k % 5] for k in range(int(latent_t)))


def step_offsets(latent_t: int) -> List[int]:
    """每个 latent token 的**起始像素帧位置**（首位恒为 0）。

    与 ``pixel_frames`` 是同一张表的两种读法：``pixel_frames(n)`` 给总量，
    本函数给每个 token 的入口。写成**排他前缀和** —— 第 i 个位置 = 它前面
    所有 token 的跨度之和。``cycle`` 让周期由 ``FRAME_PER_TOKEN`` 自己决定。
    """
    n = max(int(latent_t), 0)
    spans = itertools.islice(itertools.cycle(FRAME_PER_TOKEN), n)
    return [0, *itertools.accumulate(spans)][:n]


def steps_for_frames(n: int) -> Optional[int]:
    """像素帧数 → 整步的 latent token 数；不在网格上返回 None。

    跨度按 ``1,4,4,4,4`` 循环，所以**可达的帧数就是这些跨度的前缀和**：
    5 → 2 步、22 → 7 步、39 → 12 步、56 → 17 步。落在两格之间的值
    （4、6、7…）无解。0 帧视作 0 步。

    ⚠️ 这里**不硬编码周期**（虽然 5 步和为 17，可写成闭式解、再快一个数量级）：
    闭式解在上游改 ``FRAME_PER_TOKEN`` 时会**静默算错**，而 ``layout_contract``
    只对照常量、查不出公式里的硬编码。``cycle`` 跟着常量走，网格变了自动跟着变。
    """
    n = int(n)
    for steps, total in enumerate(
            itertools.accumulate(itertools.islice(
                itertools.cycle(FRAME_PER_TOKEN), max(n, 0))),
            start=1):
        if total >= n:
            return steps if total == n else None
    return 0 if n == 0 else None


def snap_guide_run(n: int) -> int:
    """向下吸附到最近的合法窗口（供 UI 侧提示用；核心路径仍会硬校验）。"""
    n = int(n)
    for g in GUIDE_RUNS:
        if g <= n:
            return g
    return 0


def is_frame_grid(n: int) -> bool:
    """``n`` 是否落在 **5+17k 段长档**上（帧数栅格谓词，唯一真相源）。

    🔴 为什么必须是这一个函数、而不是各处写 ``(n - 5) % 17 != 0``：
    「段长必须在 5+17k 档上」这条事实**同时被三处需要** ——
    ⏩ FiniteRun 的 ``validate_finite_params``（执行期硬闸）·
    ``tools/check_ui_workflow.py`` 的跑批前体检（拦 🔗 Chain 画布那条路）·
    跑批脚本的 ``--length`` 校验（脚本侧）。
    2026-10-22 实测：三处各写一遍取模表达式 ⇒ 改档位规则要改三个地方，
    漏一个就出现「FiniteRun 拦、Chain 不拦」的半边漏洞。这正是
    《H仓规范补条-节点层体量与拆分》要治的「同一个事实在多处声明」。

    档位同样由 ``FRAME_PER_TOKEN`` 推导（与 `frame_grid_floor` 同一套常量，不硬编码 5/17）。
    """
    n = int(n)
    epoch = sum(FRAME_PER_TOKEN[:2])      # 5
    cycle = sum(FRAME_PER_TOKEN)          # 17
    if n < epoch:
        return False
    return (n - epoch) % cycle == 0


def frame_grid_neighbors(n: int) -> Tuple[int, int]:
    """``n`` 的下邻/上邻合法段长档（供报错文案"最接近的合法档是 X"）。

    返回 ``(floor, ceil)``：``floor ≤ n ≤ ceil``，两者都在 5+17k 档上。
    ``n`` 本身在档上时两者相等。
    """
    n = int(n)
    epoch = sum(FRAME_PER_TOKEN[:2])
    cycle = sum(FRAME_PER_TOKEN)
    if n < epoch:
        return (0, epoch)
    k = (n - epoch) // cycle
    lo = int(epoch + cycle * k)
    hi = lo if lo == n else int(epoch + cycle * (k + 1))
    return (lo, hi)


def frame_grid_floor(n: int) -> int:
    """≤ n 的最大**段长档**（5+17k）；不足 5 帧返回 0。

    用途：把**外部来源**的视频接到 H3 上时，源帧数几乎不可能落在网格上。

    🔴 为什么必须是**粗档** 5+17k、而不是 ``FRAME_PER_TOKEN`` 的**全部**前缀和
    （1,5,9,13,17,**18**,22,…）：粗档 ⇔ ``latent_t ≡ 2 (mod 5)``，
    而桥有一条**相位硬约束** —— 尾段起点必须落在周期位置 0
    （见 `bridge.py` 的「尾段起点非 5 对齐 ⇒ raise」）。细档会让 ``latent_t − 尾段步数``
    不整除 5 ⇒ **桥当场 raise**。所以这里只认粗档。

    档位由 ``FRAME_PER_TOKEN`` 推导（``epoch = 前两项之和``、``cycle = 全周期和``），
    **不硬编码 5 / 17**：上游改常量时自动跟随。
    """
    n = int(n)
    epoch = sum(FRAME_PER_TOKEN[:2])      # 5  ← 前两项（1+4）
    cycle = sum(FRAME_PER_TOKEN)          # 17 ← 一个完整周期
    if n < epoch:
        return 0
    return int(epoch + cycle * ((n - epoch) // cycle))


def audio_steps(n: int) -> int:
    """像素帧数 → 音频 latent 步数（宿主 ``temporal_shape`` 的同款口径：``round(n/24*40)``）。"""
    return int(round(int(n) * FRAME_RESCALE))


def self_check() -> List[str]:
    """自洽性检查：GUIDE_RUNS 必须全部落在网格上。返回问题列表（空=通过）。"""
    bad = []
    for g in GUIDE_RUNS:
        if steps_for_frames(g) is None:
            bad.append(f"GUIDE_RUNS 含非网格值 {g}（pixel_frames 无法整步覆盖）")
    return bad
