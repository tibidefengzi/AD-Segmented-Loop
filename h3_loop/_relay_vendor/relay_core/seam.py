# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 ComfyUI-H3-Latent-Relay contributors
# 第三方出处与许可见 THIRD-PARTY-NOTICES.md
"""relay_core.seam —— 接缝自检与画质观测（沉降检测 / 硬跳 / 锐度 / 色档 / 复现残留 / 直方图与白平衡）。

本模块由 `relay_core.py` 拆分而来（2026-10-07）；代码**逐字搬运**，未改任何算式。
对外仍由 `relay_core/__init__.py` 全量再导出 ⇒ 旧调用点（`relay_core.xxx`）不变。
"""

from __future__ import annotations

from typing import Any, Optional, Tuple
import torch

from ._grid import pixel_frames
from .latent import streams_from_latent

# ---------------------------------------------------------------- 接缝自检
# 实测（2026-09-11）：续接段的「钉住区 → 新内容」切换**不总落在 trim 值上**。
# 73 帧段实测切换点在原第 22→23 帧之间（MAE 6.5 → 102.3），
# 而 trim=22 恰好把切换点前的那一帧留在裁剪后的第 0 帧 → 首帧突变。
# 107 帧段同一 trim 值则无此现象（切换点被完整裁掉）。
# ⇒ trim 值必须按**本段的实际切换点**定，不能写死。
#
# 落点：切换点是**逐段不同的随机变量**，所以它不该是用户填的配置，而是
# **观测出来的量**。裁节点手里就是完整 decode 序列（含钉住区），真实切换点
# 此刻就在手上 —— `detect_settle` 就在那里量，量不出就退回只裁钉住区。
JUMP_RATIO: float = 4.0       # 首帧差 / 段内基线 的报警阈值
# 基线过小时的保护下限——**随数据量纲缩放**（v0.3.2）：ComfyUI decode 张量是 0-1，
# 旧写死 1.0（0-255 量纲）会把 0-1 数据的帧差路径整个抬死（基线 0.0076 被抬到 1.0，
# 阈值 4.0 永不可达 → 帧差法在产线上从未生效过）。0.004 ≈ 1/255 满量程，
# 对 0-255 的旧测试行为不变（0.004×250 ≈ 1.0）。
BASELINE_FLOOR_REL: float = 0.004
MAX_SETTLE: int = 12          # 沉降帧上限（≈0.5 s）。自动检测不会超过它
# 裁后仍见突变时，只有位置在这么靠前才值得动刀：
# 裁后下标 j 表示"还差 j+1 帧沉降"，所以上限是 MAX_SETTLE-1。
ADVISE_WITHIN: int = MAX_SETTLE - 1
# —— 模糊型沉降（v0.3.1）：切换不是硬跳而是「重绘发虚」——帧差法看不见，改看高频能量。——
# 度量用 **Laplacian 响应的均方**（≈方差）：模糊先杀高对比边缘，平方放大这一损失。
# mean-abs 版实测钝 3 倍（HD mild 塌陷 0.29-0.45× 在它眼里是 0.79-0.83×，过不了 0.35 阈）。
BLUR_ZONE_RATIO: float = 0.55      # 锐度 < 基准×此比例 → 记为塌陷帧
BLUR_COLLAPSE_RATIO: float = 0.35  # 塌陷最深要低于基准×此比例才算真模糊（防误伤天生偏软的段）
BLUR_RECOVER_RATIO: float = 0.5    # 窗内必须看到恢复到基准×此比例，才敢裁（看不到恢复宁少勿多）
# —— 锐度路三量解耦（2026-09-15，本仓作者 目检「每处接缝都看到模糊沉降」）——
# ⚠ 版本号待定：本改动与在飞的 v0.4.3+ 未提交改动同处一树，由 本仓作者 决定并入哪个版本。
# 实测（onerA_cond4_4seg，cond 桥）：糊区 11–17 帧宽、最深 0.29–0.31×基准。原实现让
# MAX_SETTLE=12 同时充当扫描窗宽 → 糊区把窗口填满时 after 为空 →「窗内必须见恢复」
# 永假 → settle 恒 0（s2/s3 实测 settle=0；段头/段体锐度比 0.71 / 0.41，糊区原样留在成片）。
# 又：原实现把三件事压在同一个 MAX_SETTLE 上——扫描窗宽、裁剪上限、体区参考偏移
# （far_lo = pin + max_settle + 2）——单纯加窗会连带漂移参考区，破坏 12.9/13.8 两条契约。
# 故锐度路这三者拆开独立；**硬跳路与色档路仍用 MAX_SETTLE，安全阀不动**。
SETTLE_SCAN: int = 36          # 锐度路扫描窗宽（≈1.5 s）
SETTLE_CAP: int = 36           # 锐度路裁剪上限（= 扫描窗，糊区才裁得干净）
# 🔴 2026-09-15 实测上调 24→36（依据：节点 report 剖面 + 裁量→跳跃曲线）：
#   cond 桥的暖机「塌陷 + 爬升」总长**实测 >25 帧**（剖面里到第 17 帧锐度 0.77 仍在涨）
#   → 24 帧的窗**看不到恢复点** → 只能退回「最后一个塌陷帧」→ 留下几帧 0.53–0.55× 残余
#   （本仓作者 目检「接缝处从模糊变清晰」）。
#   而 `trim_jump_curve` 显示：裁 8 帧→跳跃 8.4×、裁 12 帧→~9.0×（**多裁 4 帧只多 0.6×**）
#   ⇒ **把窗放到 36 让恢复点进窗 = 切净糊、跳跃几乎不变**，是净赚。
#   ⚠ 配套：出词纪律的「段首无台词」要按新裁头上限放宽（≥ 裁头 + 0.2 s）。
SETTLE_REF_OFF: int = 14       # 体区参考起点相对 pin 的偏移（固定；pin=22 时 = 原 pin+12+2）
BLUR_COLLAPSE_RATIO_SHARP: float = 0.45
SETTLE_RECOVER_TARGET: float = 0.8

# —— 路径 4：复现残留（RESEARCH_seam_frontier §13 D7）——
# 动机：现有三路（硬跳 / 锐度 / 色档）**没有一路能看见复现残留** ——
#   复现帧是**清晰**的（锐度路看不见）、**色档一致**的（色档路看不见）、
#   也**没有硬跳**（硬跳路看不见）。
# 判据：窗内第 i 帧与**钉住区（前 pin 帧）**的**最小** MAE。
# 实测分离度（0–255 灰度）：重复 = 2.81；非重复 = 11.3 / 45.2 → 分离比 > 2×。
# 本实现内部用 0–1 归一化，故阈值 = 5/255。
REPEAT_MAE: float = 5.0 / 255.0        # 判「这一帧是钉住区内容的复现」的 MAE 门槛
REPEAT_MIN_RUN: int = 2                # 连续重复至少几帧才采信（防单帧巧合）
REPEAT_SCAN: int = 36                  # 扫描窗宽（与 SETTLE_SCAN 同量级）
# 锐度路的「恢复点」目标：裁到锐度回到此比例的首帧，而不是停在「最后一个塌陷帧」。
# 动机（2026-09-15 本仓作者 目检）：只裁到塌陷边界（<0.55×）会留下几帧 0.53–0.55× 的**恢复尾巴**，
# 观感 =「接缝处从模糊变清晰」——糊本身修好了，但「从糊变清」这个过渡仍是可见缺陷。
#
# 🔴🔴 2026-09-15 深夜**重大修正**：这条「裁到恢复点」把 settle 从 8 推到 16，
#   **代价被严重低估**——实测跳帧随裁量单调上升（同一条 s2，只改裁量）：
#
#     settle  0 → 最大单帧跳 5.08（归一 0.020）  ← 本仓作者：「几乎无感」
#     settle  8 →                7.2×（归一 0.044）  ← 本仓作者：「跳了」
#     settle 16 →               14.08（归一 0.055）  ← 本仓作者：「跳」
#
#   **裁切 = 时间跳跃**：裁掉的帧越多，缝处要跨过的时间就越长，跳得越大。
#   而「糊」是**清晰度的渐变**，人眼对渐变容忍度极高（本仓作者 对 settle 0 的评语就是「几乎无感」）。
#   ⇒ **拿「可容忍的渐变」去换「不可容忍的突变」，是拿错的筹码换对的东西。**
#
#   正确方向（2026-09-15 定案）：**时间轴上什么都不做**（不裁沉降、不重影），
#   把「治糊」放到**画质域**去解 —— 对糊区做锐化/去模糊修复（不改变帧数、不改变时间轴）。
#   糊区是「上段尾的复现」，上段尾的**真值帧是清晰的**，天然可作修复的参考引导。
#   ⇒ 本常量保留（诊断价值：它量出的塌陷深度是真的），但**默认不据此裁切**。
#
# 锐度路单列的深线（硬跳路验身仍用 BLUR_COLLAPSE_RATIO=0.35）：cond 路钉住区本身被重绘
# 发软 → 自参考基准被污染、塌陷比值被抬浅，实测 S1 深塌陷只到 0.36×，卡在 0.35 门外漏检。
# —— v0.4.1 色档收敛信号：注噪/taper 路的「收敛尾巴」是低频现象（重影+色档漂移），
# 锐度法不可见（L1 taper 实测：可见头部 3 帧亮度 0.270→0.282 爬升 + 首帧重影——本仓作者 目检确认）。
# 旧像素注噪路裁 26=22+4 裁的就是它——本信号是它的观测端版本。
GRADE_DEV_Z: float = 4.0        # 体区 MAD 的 z 门槛
GRADE_DEV_FLOOR: float = 0.003  # 绝对下限（0-1 量纲；255 量纲测试由 z 项主导）
# —— v0.3.3 稳健统计层：参考分布来自段体（窗外体区），z 分数定位，深度比值做证据闸 ——
# 动机（本仓作者 2026-09-13）：分辨率/步数/LoRA/场景内容都会整体移动锐度与帧差的绝对量级，
# 任何"绝对常数"都会在某个参数组合下失效。因此：
#   · 参考分布 = 段体自身（窗外 ≥8 帧，median + MAD）——4 步软渲染、运动模糊、平坦场景
#     自动进基准，零配置、零量纲；
#   · z 分数（|x-med|/(1.4826·MAD)）负责"是不是异常"，比值门槛（0.35/0.55/0.5，本来就
#     无量纲）负责"是不是深到值得动刀"——两道闸缺一不可：只有 z 会把"段体天生比头锐"
#     的正常渐变误裁，只有比值会在分布漂移时定位失准；
#   · 硬跳必须**验身**：跳后 2-5 帧锐度回到体分布才采信，否则视为假跳/闪烁交给锐度路。
Z_JUMP: float = 8.0                # 硬跳的稳健 z 门槛（对段体帧差 MAD 标准化；实测真跳 z≈90）
_LAP_KERNELS: dict = {}            # 按 device 字符串惰性缓存的 3×3 Laplacian 卷积核


def _robust_stats(x: torch.Tensor) -> Tuple[float, float]:
    """稳健 (median, 1.4826×MAD)。空输入返回 (0, 0)。"""
    if x.numel() == 0:
        return 0.0, 0.0
    med = float(x.median())
    mad = float((x - med).abs().median()) * 1.4826
    return med, mad


def _abs_max(t: torch.Tensor) -> float:
    """max(|t|)，不物化 abs() 的全量拷贝（float 张量上 max(|min|, max) 与之等价）。"""
    return max(-float(t.min()), float(t.max()))


def _baseline_floor(images: torch.Tensor) -> float:
    """帧差基线的保护下限，随数据实际量纲缩放（0-1 产线 / 0-255 测试同一套阈值）。"""
    try:
        scale = _abs_max(images.detach())
    except Exception:
        return 0.0
    return BASELINE_FLOOR_REL * scale if scale > 0 else 0.0


# —— L1 域归一 / L4 跨尺度仲裁（2026-09-15 实测新增）——
# 实测（`scale_invariance_probe.py`，同一段素材四尺度）：绝对锐度基准跨 **37.4×**
# （116px 1.16e-2 → 928px 3.11e-4）⇒ **绝对阈值跨分辨率必然失效**；
# 且**高分辨率下塌陷变浅**（928×1600 只到 0.45×，而 232×400 是 0.34×）——
# 因为 3×3 Laplacian 是**固定像素核**，高分辨率下它测的是最细的噪声级细节，**对中频的糊迟钝**。
# 对策：L1 先归一分析域（缩到固定短边），L4 要求两个尺度都见深塌陷才动刀（拒绝尺度伪影）。
ANALYSIS_SHORT_SIDE: int = 256        # L1：梯度类指标的默认分析短边（**只缩不放**）
ANALYSIS_SHORT_SIDE_ALT: int = 384    # L4：仲裁用的第二个尺度
SETTLE_CROSS_SCALE: bool = True       # L4 开关
# 路径 4（复现残留）开关。
# 🔴 默认 **False**：本路**只会让裁量变大**，而既有契约是「宁可维持旧行为也不赌」。
#   实测发现它会在**低纹理 / 周期内容**上误报——合成夹具 `seam_seg`（3 帧周期）下
#   `pin` 之后的帧必然与钉住区某帧逐位相同 ⇒ 必报。真实静态镜头同理。
#   ⇒ 本路已实现且单测覆盖，但**默认不启用**；启用前必须用真实渲染验证误报率。
#   置 True 会改变 settle 行为（可能多裁）——**不是逐位兼容的改动**。
SETTLE_REPEAT_PATH: bool = False


def _canonicalize(images: torch.Tensor, short_side: int = None) -> torch.Tensor:
    """L1 域归一：把帧缩到**固定短边**再算梯度类指标（**只缩不放**）。

    帧比目标还小时**原样返回** —— 合成夹具（8×8）与低分辨率素材不受影响，
    故该层对既有行为是"只在高分辨率下生效"的纯增益。
    """
    ss = ANALYSIS_SHORT_SIDE if short_side is None else int(short_side)
    if ss <= 0 or images.dim() != 4:
        return images
    h, w = int(images.shape[1]), int(images.shape[2])
    if min(h, w) <= ss:
        return images
    k = ss / float(min(h, w))
    nh, nw = max(1, int(round(h * k))), max(1, int(round(w * k)))
    f = images.to(torch.float32)
    ch_last = f.shape[-1] in (1, 3, 4)                 # [N,H,W,C]（ComfyUI IMAGE）
    if ch_last:
        f = f.permute(0, 3, 1, 2)                      # → [N,C,H,W]
    f = torch.nn.functional.interpolate(f, size=(nh, nw), mode="area")
    return f.permute(0, 2, 3, 1) if ch_last else f


def _sharpness(images: torch.Tensor, short_side: int = None) -> torch.Tensor:
    """逐帧高频能量代理：3×3 **Laplacian 响应的均方**（≈锐度方差）。纯 torch，不依赖 cv2/PIL。

    带纹理的画面显著大于 0；重绘发虚（模糊）先杀高对比边缘，平方度量把它放大
    （实测：HD mild 塌陷帧在方差度量下 0.29-0.45×基准，mean-abs 度量下只见 0.79-0.83×）。
    """
    f = _canonicalize(images, short_side)              # L1 域归一（只缩不放）
    if f.dim() != 4:
        return torch.zeros(0)
    f = f.to(torch.float32)
    g = f.mean(dim=-1).unsqueeze(1)                    # [N,1,H,W]
    # 按 device 分键缓存：多 GPU / 多设备交替时不会来回重建，也无全局竞态
    key = str(g.device)
    kern = _LAP_KERNELS.get(key)
    if kern is None or kern.device != g.device:
        kern = g.new_tensor([[0.0, 1.0, 0.0],
                             [1.0, -4.0, 1.0],
                             [0.0, 1.0, 0.0]]).view(1, 1, 3, 3)
        _LAP_KERNELS[key] = kern
    # replicate pad（不能用 conv2d 自带的 zero pad：常数帧边界会吃出假响应，
    # 小分辨率合成测试里边界占比过半，整个度量直接反转）
    gp = torch.nn.functional.pad(g, (1, 1, 1, 1), mode="replicate")
    resp = torch.nn.functional.conv2d(gp, kern)
    return resp.pow(2).mean(dim=(1, 2, 3))             # [N]


def _frame_diffs(images: torch.Tensor) -> torch.Tensor:
    """相邻帧差的逐帧标量（纯 CPU、纯张量，不依赖 cv2/PIL）。"""
    f = images.to(torch.float32)
    if f.dim() == 4 and f.shape[-1] in (1, 3, 4):   # [N,H,W,C] → 按通道均值
        return (f[1:] - f[:-1]).abs().mean(dim=(1, 2, 3))
    return (f[1:] - f[:-1]).abs().flatten(1).mean(dim=1)


def scan_head_jump(images: torch.Tensor, scan: int = 40) -> Tuple[int, float, float]:
    """段首扫描的**原始观测**：返回 ``(argmax 下标, 该处帧差, 段内基线)``。

    不做显著性判断、不加范围限制 —— 由上层按各自口径解释：
    ``find_head_jump`` 只判显著性，``describe_head_jump`` 再叠加"可执行范围"。
    """
    n = int(images.shape[0])
    if n < 4:
        return -1, 0.0, 0.0
    # ★ 先切窗再做差：整段物化两遍全量帧差，在 120+ 帧 / 768×448 上是 ~1GB 的瞬时峰值
    hi = min(scan, n - 1)
    if hi <= 2:
        return -1, 0.0, 0.0
    diff = _frame_diffs(images[:hi + 1])
    baseline = float(diff[2:hi].median())
    seg = diff[:hi]
    j = int(torch.argmax(seg).item())
    return j, float(seg[j].item()), baseline


def find_head_jump(images: torch.Tensor, scan: int = 40) -> Tuple[int, float, float]:
    """在前 ``scan`` 帧内找「本段起点」处的突变。

    返回 ``(jump_index, jump_mae, baseline_mae)``：
      - ``baseline_mae``：段内相邻帧差的中位数（跳过前 2 帧）
      - ``jump_mae``：``images[jump_index]`` 与后一帧的差
      - ``jump_index``：突变发生在前一帧的下标（即"应当再往前裁 1 帧"的位置）
    找不到突变时返回 ``(-1, 0.0, baseline)``。

    纯 CPU、纯张量：不依赖 cv2/PIL，可在节点里直接调。
    """
    j, jump, baseline = scan_head_jump(images, scan)
    if j < 0:
        return -1, jump, baseline
    if jump > JUMP_RATIO * max(baseline, _baseline_floor(images)):
        return j, jump, baseline
    return -1, jump, baseline


# —— 边界跳帧观测（2026-09-15 实测新增）——
# 动机：copy 桥在「钉住区最后一帧 → 本段第一帧新内容」处的帧差，实测达**段内基线的 29.6×**
# （视觉上就是「跳帧」）。而沉降公式 `settle = j + 1 - pin` 在 **j == pin-1**
# （跳变正好落在钉住区边界）时返回 **0** —— 把边界跳变**静默判成「无沉降」**。
# 语义上没错（边界就是切换点，没有"多余复现"可裁），但**它掩盖了「边界本身是断的」这个事实**。
# 故把该量单独暴露，供 report 直接显示 —— **裁切治不了跳变，但至少要看得见。**
BOUNDARY_JUMP_WARN: float = 6.0   # 报警阈值：边界帧差 ÷ 段内基线


# —— 缝帧重影（极短交叉溶）——
# 🔴 2026-09-15 深夜实测定案：**默认关（0）**。保留能力，但不要默认开。
#
# 原设想：把缝处一跳拆成两个半跳跨两格 → 眼不及辨。数值上确实如此
# （最大单帧跳 14.08 → 7.51，归一 0.055 → 0.029）。
#
# **但观感实测更差**（本仓作者 目检原话：「比之前的实现还差，有明显跳帧、不流畅」）。根因：
#   ① **总位移几乎没减**：超基线总量 12.61 → 11.68（仅 −7%）—— 一跳变两跳，位移守恒；
#   ② **异常帧数 1 → 2**：单帧瞬跳可能被当成眨眼/运动模糊，**连续两帧异常 = 卡了两下**；
#   ③ 重影帧是**鬼影**：内容既不属于上段尾也不属于本段首，本身就是一个可见异物。
#   ⇒ **「把突变摊平成两个小突变」不等于消除突变**——人眼对「持续异常」比对「瞬时异常」更敏感。
#
# 更重要的对照（同一条 s2，只改裁量）：
#   settle 0  → 跳帧 0.020（本仓作者：**几乎无感**）
#   settle 16 → 跳帧 0.055（本仓作者：跳）
#   ⇒ **跳的源头是「裁切」本身**（裁切 = 时间跳跃）。重影治不了它，只会改变它的形状。
#   ⇒ 正确方向见 `SETTLE_RECOVER_TARGET` 处的注释：**时间轴不动，画质域修复**。
SEAM_GHOST_FRAMES: int = 0        # 重影帧数（**默认 0 = 关**；>0 仅在确知收益时手动开）
SEAM_GHOST_ALPHA: float = 0.5     # 上段末帧的权重（0.5 = 对半）


def seam_ghost_blend(head: torch.Tensor, prev_last: torch.Tensor,
                     frames: int = SEAM_GHOST_FRAMES,
                     alpha: float = SEAM_GHOST_ALPHA) -> torch.Tensor:
    """把 ``head`` 的**前 frames 帧**替换为「上段末帧 ⊕ 本段首帧」的加权混合。

    帧数守恒（替换而非插入）⇒ 音频不需要跟着动 ⇒ **无 A/V 漂移**。
    纯张量、无依赖，便于离线回测。
    """
    if frames <= 0 or int(head.shape[0]) == 0:
        return head
    k = min(int(frames), int(head.shape[0]))
    a = float(min(max(alpha, 0.0), 1.0))
    ref = prev_last.to(head.dtype)
    if ref.dim() == head.dim() - 1:          # [H,W,C] → [1,H,W,C]
        ref = ref.unsqueeze(0)
    return torch.cat([a * ref + (1.0 - a) * head[:k], head[k:]], dim=0)


# —— 画质域修复：糊区锐化（2026-09-16 本仓作者 定方向：先试零 GPU 传统锐化）——
# 动机：默认 settle_frames=0（不裁沉降）后，成片段头保留几帧「模型重绘导致的糊」
#   （实测锐度 0.33–0.8× 基准）；而「裁掉它」会引入跳帧（裁 16 帧跳 0.055）。
#   ⇒ 时间轴两条路都不走，改走**画质域**：不裁、不动时间轴，只提升糊区高频。
#
# 方法：unsharp mask  `out = img + amount·(img − blur3x3(img))`，
#   强度按「越靠缝越强」线性衰减（糊本身是渐变的：0.33 → 1.0）。
#
# 诚实边界（**别当万能药**）：
#   **锐化只能恢复「对比度」，不能恢复「已丢失的真实细节」。**
#   对「结构还在、只是软」的重绘糊有效；对「细节完全丢失」无效。
#   副作用：放大噪声、强边缘可能出 halo（白边）→ 故 amount 上限设 1.5。
SETTLE_SHARPEN: float = 0.0          # 锐化强度（0 = 关；建议 0.4–1.0）
SETTLE_SHARPEN_FRAMES: int = 24      # 作用帧数（从裁后首帧起，线性衰减到 0）
SETTLE_SHARPEN_MAX: float = 1.5      # 强度上限（防呆）


def seam_crossfade(images: torch.Tensor, cut: int, pin: int, frames: int = 1) -> torch.Tensor:
    """极短交叉溶：返回**裁后**帧序列，但把开头 ``frames`` 帧替换为
    「上段末尾 frames 帧 ⊕ 本段开头 frames 帧」的渐变混合。

    参数
    ----
    ``cut``  裁切点（= ``pin + settle``）—— 本段新内容从这里开始
    ``pin``  钉住区长度 —— 「上段末尾 k 帧」从 ``images[pin-k : pin]`` 取
    ``frames`` 溶的帧数（1 = 对半单帧；2–3 = 极短交叉溶；上限 6）

    与 ``seam_ghost_blend`` 的关键区别：**两侧都是连续运动的序列**
    （而非把上段末帧复制 N 次）→ k>1 时不会"内容冻结"，过渡是逐帧摊开的。
    ``t_i = (i+1)/(k+1)``：k=1 → 0.5；k=3 → 0.25/0.5/0.75；每帧只走总跳幅的 1/(k+1)。
    **帧数守恒**、不动音频、零采样开销。
    """
    n = int(images.shape[0])
    cut, pin, k = int(cut), int(pin), int(frames)
    if k <= 0 or pin < 1 or n <= cut:
        return images[cut:] if 0 < cut <= n else images
    k = min(k, pin, n - cut)          # 上段末尾要有 k 帧、裁后要有 k 帧
    if k <= 0:
        return images[cut:]

    a = images[pin - k: pin]          # 上段末尾 k 帧：A_1 … A_k（A_k = 上段末帧）
    b = images[cut: cut + k]          # 本段开头 k 帧：B_1 … B_k
    a_rev = torch.flip(a, dims=[0])   # A_k … A_1 —— 让输出第 0 帧最接近上段末帧

    t = torch.linspace(1.0 / (k + 1.0), k / (k + 1.0), k,
                       device=images.device, dtype=images.dtype).view(k, 1, 1, 1)
    mixed = (1.0 - t) * a_rev + t * b
    return torch.cat([mixed, images[cut + k:]], dim=0)

# —— 低频残差传递（2026-09-16，借鉴 Director 的段间引导低频对齐）——
# 来源与依据见 `lowfreq_pull` 文档串；核心一行：
#     out = clamp(src + w·(blur(guide) − blur(src)), 0, 1)
# **只吸收 guide 的低频色档/布光，保留 src 自己的细节与姿态** —— 故不会产生第二个轮廓。
LOWFREQ_PULL_WEIGHT: float = 0.0      # 默认关（0 = 不动）；Director 实测用 0.70
LOWFREQ_PULL_FRAMES: int = 12         # 作用帧数（从裁后首帧起，权重线性衰减到 0）
LOWFREQ_PULL_BLUR: int = 64           # 低频尺度（盒式模糊核，Director 用 64）


def _box_blur_hwc(x: torch.Tensor, kernel: int) -> torch.Tensor:
    """盒式模糊（NHWC 进出，reflect pad）。kernel 强制奇数且 >=3。

    用 `avg_pool2d` 实现（与 Director 的 GPU 批量路径同算子）：
    该运算逐样本独立、不跨帧耦合，故可一次批量算完。
    """
    k = int(kernel)
    if k < 3 or x.dim() != 4:
        return x.float()
    if k % 2 == 0:
        k += 1
    n, h, w, c = x.shape
    pad = k // 2
    t = x.float().permute(0, 3, 1, 2)                       # NHWC → NCHW
    t = torch.nn.functional.pad(t, (pad, pad, pad, pad), mode="reflect")
    t = torch.nn.functional.avg_pool2d(t, kernel_size=k, stride=1, count_include_pad=False)
    return t.permute(0, 2, 3, 1).contiguous()


def lowfreq_pull(images: torch.Tensor, guide: torch.Tensor,
                 frames: int = LOWFREQ_PULL_FRAMES,
                 weight: float = LOWFREQ_PULL_WEIGHT,
                 blur: int = LOWFREQ_PULL_BLUR) -> torch.Tensor:
    """把 ``images`` **开头 frames 帧**的低频拉向 ``guide``（通常 = 上段末帧）。

    逐帧：``out[i] = clamp(images[i] + w_i·(blur(guide) − blur(images[i])), 0, 1)``，
    权重 ``w_i`` 从 ``weight`` 线性衰减到 0（缝端最强 → 尾端不动）。

    **关键性质**：只动低频 ⇒ **不复制 guide 的姿态轮廓** ⇒ 无重影。
    这与「全 RGB 混合」（交叉溶/重影）有本质区别 —— 后者会复制姿态边缘。

    **帧数守恒、不动音频、零采样开销。**
    """
    if weight <= 0.0 or images.dim() != 4 or int(images.shape[0]) == 0:
        return images
    if guide is None:
        return images
    n = int(images.shape[0])
    k = min(int(frames), n)
    if k <= 0:
        return images

    g = guide
    if g.dim() == 4:
        g = g[0]                                  # [H,W,C]
    if tuple(g.shape[:2]) != tuple(images.shape[1:3]):
        return images                             # 画布不一致 → 不动刀（安全兜底）
    g = g.unsqueeze(0).to(images.dtype)           # [1,H,W,C]

    zone = images[:k]
    b_guide = _box_blur_hwc(g, blur)              # guide 的低频只算一次
    b_zone = _box_blur_hwc(zone, blur)            # k 帧批量一次算完
    w = torch.linspace(float(weight), 0.0, k, device=images.device,
                       dtype=torch.float32).view(k, 1, 1, 1)
    pulled = (zone.float() + w * (b_guide - b_zone)).clamp(0.0, 1.0).to(images.dtype)
    return torch.cat([pulled, images[k:]], dim=0)

# —— P2 跨段统计匹配（Reinhard 式一阶+二阶矩）—— 2026-09-17 / RESEARCH_seam_frontier §2
# 动机：色档/曝光漂移是**低阶统计量现象**，一阶（均值）二阶（标准差）矩理论上是充分统计。
#   现有 `lowfreq_pull` 只做**低频加性**修正（一阶）；本函数补上**二阶（对比度）+ 逐通道色度**。
# 文献：Reinhard《Color transfer between images》IEEE CG&A 21(5):34-41, 2001（lab 空间逐通道
#   均值/方差匹配）· WCT《Universal Style Transfer via Feature Transforms》NeurIPS 2017,
#   arXiv:1705.08086 · TTC(Pathwise Test-Time Correction) arXiv:2602.05871。
# ⚠ 与「全 RGB 混合 / 交叉溶」有本质区别：后者复制**姿态轮廓**（→ 重影）；
#   本函数只对齐**统计量**（均值/标准差）⇒ 不复制结构 ⇒ 无重影。
MATCH_PREV_WEIGHT: float = 0.0     # 默认关（0 = 不动）；建议从 0.5 起试
MATCH_PREV_FRAMES: int = 12        # 作用帧数（从裁后首帧起，权重线性衰减到 0）
MATCH_PREV_GAIN_MAX: float = 1.15  # 逐通道增益上限（σt/σs 截断，防把已通过的内容改坏）
MATCH_PREV_OFF_MAX: float = 0.06   # 逐通道偏移上限（μt−μs 截断）
# 🔴 2026-09-17 真渲染实测定案：统计量**只取紧贴缝的那一帧**（= 1），不再对整个作用区聚合。
#   旧口径（聚合整个作用区）与「单帧 guide」口径**不匹配**，会给出错方向的修正：
#   当段头**内部有亮度梯度**时（实测：首帧已到 guide 水平、第 2 帧起掉 ~0.008），
#   聚合均值被后续帧拉低 ⇒ offs 变成「段头平均 vs guide」的差 ⇒ 首帧被**推过 guide**，
#   缝上凭空多出一个阶跃（实测纯末→首阶跃 0.0007 → 0.0088，×12.6）。
#   我们要的目标函数是「**首帧 ≈ guide**」，不是「段头均值 ≈ guide」。
#   设 0 可回退旧口径（仅作对照，勿用于产线）。
MATCH_PREV_STATS_FRAMES: int = 1
# —— levellock 纪律（借鉴 obvpm 2026-09 实测：38 个 parent→child 缝，step 中位 2.87
#    vs 帧间噪声中位 0.44，≈6×；同一 parent 的四个独立 take 开头 +3.78/+3.18/+3.38/+3.59
#    ⇒ 是模型偏置不是采样噪声，reroll 治不好）——
# ① 噪声闸：step 不超过「局部帧间噪声 3 倍」且不超过地板就**原样返回**。
#    obvpm 原话：低于它「没什么可修」，硬修只会给缝添一道自己的慢漂移。
#    ⚠ H 仓是 0-1 浮点域，obvpm 的地板 0.6 是 0-255 域 ⇒ 按 /255 折。
MATCH_PREV_STEP_OVER_NOISE: float = 3.0
MATCH_PREV_STEP_FLOOR: float = 0.6 / 255.0
# ② 去向预测：guide 带多帧时逐通道对帧均值序列拟合直线、外推到**末帧之后一帧**
#    （父段合法「走进阴影」时不被「修」回旧色档）；单帧 guide 退化为该帧本身
#    ⇒ 与旧版逐位一致（单帧路径）。
# ③ 外推取父段尾部几帧（obvpm FIT=12）：TrimAV 调用点传钉住区末尾这么多帧
#    （拷贝桥下 = 上段真实尾部；cond 桥下是本段重画——该路线本项应保持 0）。
MATCH_PREV_GUIDE_FIT: int = 12
# ④ 趋势显著性闸（2026-10-14 真实缝实测后加）：外推量须超过「尾部自身帧间
#    噪声 ×3」且超过地板才采信——否则尾部「走向」是噪声，外推会把目标推离
#    缝的真正另一半。实测（18 条真实缝，33b 验收轮 + 追痕 18 段实跑）：
#    外推Δ中位 0.0026 / 最大 0.0091，多数超过缝阶跃本身（0.0001–0.0041）；
#    无此闸时新修正在 2/18 条缝上把阶跃放大（0.0012→0.0030）。
MATCH_PREV_TREND_OVER_NOISE: float = 3.0
MATCH_PREV_TREND_FLOOR: float = 0.6 / 255.0
# ⑤ 单调性护栏：外推目标若比末帧离段头更远 ⇒ 修正会放大阶跃 ⇒ 退回末帧
#    （见 match_prev_stats 内实现；实测 33b 缝无此护栏时无闸臂 0.0012→0.0030）。


def _guide_stats(g: torch.Tensor) -> Tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
    """guide 的 (mu_t, sd_t, mu_last)：**多帧 ⇒ 均值按走向外推一帧、标准差取窗内聚合**；
    单帧/三维 ⇒ 与该帧自身的均值/标准差（逐位同旧版）。

    levellock 纪律②：拿父段**末帧**当目标，会在父段合法走进阴影时把子段「修」回
    旧色档；正解是用末若干帧的走向外推一帧。对比度是慢变量，不玩外推——
    取窗内逐帧方差的均值（K=1 时退化为该帧标准差，与旧版一致）。

    🔴 趋势显著性闸（纪律④，2026-10-14 真实缝实测后加）：外推量不超过
    「尾部**拟合残差**噪声 ×3」且不超过地板 ⇒ **不外推，用末帧**。实测 18 条真实缝的
    外推Δ多数超过缝阶跃本身 ⇒ 不设此闸时外推会把修正目标推离缝的真正另一半。
    父段真在走（强趋势、残差小）时外推照常采信。``mu_last`` 一并返回，供调用方做
    缝最小化选目标（纪律⑤）。
    """
    if g.dim() == 3:
        return g.mean(dim=(0, 1)), g.std(dim=(0, 1)), g.mean(dim=(0, 1))
    k = int(g.shape[0])
    if k <= 1:
        f = g[0]
        return f.mean(dim=(0, 1)), f.std(dim=(0, 1)), f.mean(dim=(0, 1))
    flat = g.reshape(k, -1, int(g.shape[-1])).float()     # [K, HW, C]
    means = flat.mean(dim=1)                              # [K, C]
    sd_t = flat.var(dim=1, unbiased=False).mean(dim=0).clamp_min(1e-6).sqrt()
    last = means[-1]
    x = torch.arange(k, dtype=torch.float32, device=g.device)
    xm = x.mean()
    denom = float(((x - xm) ** 2).sum())
    if denom <= 0.0:
        return last, sd_t, last
    ym = means.mean(dim=0)                                # [C]
    slope = ((x - xm).view(-1, 1) * (means - ym)).sum(dim=0) / denom
    mu_ext = (ym + slope * (float(k) - float(xm))).clamp(0.0, 1.0)
    # 尾部「走向」稳不稳 = **拟合残差**的帧间噪声（不是原始帧间差——那会把趋势
    # 本身算成噪声，干净斜坡永远过不了自己的闸；2026-10-14 实测发现并修正）。
    resid = means - (ym.view(1, -1) + slope.view(1, -1) * (x - xm).view(-1, 1))
    tail_noise = float((resid[1:] - resid[:-1]).abs().mean(dim=1).median())
    lead_ok = (mu_ext - last).abs() > max(
        MATCH_PREV_TREND_OVER_NOISE * tail_noise, MATCH_PREV_TREND_FLOOR)
    mu_t = torch.where(lead_ok, mu_ext, last)
    return mu_t, sd_t, last


def _head_noise(src: torch.Tensor) -> float:
    """段头区域的帧间噪声：逐帧 mean|Δ| 的**中位数**（0-1 域）。不足 2 帧 ⇒ 0。"""
    if int(src.shape[0]) < 2:
        return 0.0
    d = (src[1:] - src[:-1]).abs().mean(dim=(1, 2, 3))
    return float(d.median())


def match_prev_stats(images: torch.Tensor, guide: torch.Tensor,
                     frames: int = MATCH_PREV_FRAMES,
                     weight: float = MATCH_PREV_WEIGHT,
                     gain_max: float = MATCH_PREV_GAIN_MAX,
                     offset_max: float = MATCH_PREV_OFF_MAX,
                     stats_frames: int = MATCH_PREV_STATS_FRAMES,
                     step_over_noise: float = MATCH_PREV_STEP_OVER_NOISE,
                     step_floor: float = MATCH_PREV_STEP_FLOOR) -> torch.Tensor:
    """跨段统计匹配：把 ``images`` 开头 ``frames`` 帧的色档/曝光对齐到 ``guide``（上段末帧）。

    逐通道 ``out = (x − μs)/σs · σt + μt``，再按**缝端最强 → 尾端 0**的线性权重与
    原帧混合。修正量**一次性算出**（不对每帧重算统计）⇒ 时间上平滑。

    🔴 ``stats_frames``（统计量取几帧）——**这是本条最容易写错的地方**：
      · ``1``（默认）= 统计量取**紧贴缝的那一帧**，与 ``guide``（单帧）口径一致
        ⇒ ``offs`` 恰是「缝上的阶跃」，首帧按权重被拉向 guide，**不会越过它**；
      · ``0`` = 旧口径：对**整个作用区**聚合。与单帧 guide 口径不匹配 ⇒
        段头内部有亮度梯度时，聚合均值被后续帧拉低，修正量变成「段头平均 vs guide」的差，
        于是**首帧被推过 guide**、缝上凭空多出一个阶跃（2026-09-17 真渲染实测 ×12.6）。
        **仅作对照档，勿用于产线。**
      · ``>1`` = 用前 N 帧聚合（介于两者之间；用于「段头前几帧整体就是一个色档」的情形）。

    **护栏**（防把已通过的内容改坏）：
      · 逐通道增益 ``σt/σs`` 截断到 ``[1/gain_max, gain_max]``
      · 逐通道偏移 ``μt−μs`` 截断到 ``±offset_max``
      · 画布不一致 / 权重 ≤0 / 空输入 ⇒ **原样返回**（安全兜底）
      · **噪声闸**：step 不超过「帧间噪声 × ``step_over_noise``」且不超过
        ``step_floor`` ⇒ 原样返回（没什么可修就不修；两闸填 0 = 关闸复旧）

    **去向预测**：guide 传多帧时按帧均值序列外推一帧当目标、对比度取窗内聚合
    （父段合法走进阴影不被「修」回旧色档）；单帧 guide 逐位同旧版。
    🔴 **趋势显著性闸**：外推量不超过「尾部帧间噪声 ×3」且不超过地板 ⇒ 不外推、
    用末帧（2026-10-14 真实缝实测：不设此闸时外推会把目标推离缝的另一半分、
    18 条里 2 条阶跃被放大）。父段真在走时外推照常采信。
    🔴 **单调性护栏**：外推目标比末帧离段头更远 ⇒ 退回末帧——修正的唯一目的
    是缩小可见阶跃，反向即自我否定（chroma 噪声拟合假趋势的实证见 CHANGES ⑬）。
    🔴 **缝最小化选目标**（纪律⑤，替代上面的单调性护栏）：逐通道比较「末帧」与
    「外推」两个目标的修正后阶跃，只采信更小的 ⇒ 多帧 guide 永不放大阶跃。
    （2026-10-14 实测：18 条真实缝上外推无必然收益；无此护栏时无闸臂在 33b
    缝上把 0.0012 放大成 0.0030。）

    **帧数守恒、不动音频、零采样开销。**
    """
    if weight <= 0.0 or images.dim() != 4 or int(images.shape[0]) == 0:
        return images
    if guide is None:
        return images
    n = int(images.shape[0])
    k = min(int(frames), n)
    if k <= 0:
        return images

    if tuple(guide.shape[-3:-1]) != tuple(images.shape[1:3]):
        return images                              # 画布不一致 → 不动刀
    if int(guide.shape[-1]) != int(images.shape[-1]):
        return images                              # 通道数不符 → 不动刀

    src = images[:k].float()                       # [k,H,W,C]

    # 统计量的**取样窗**：默认只取紧贴缝的一帧（与单帧 guide 同口径）。
    _sf = int(stats_frames)
    ref = src if _sf <= 0 else src[:max(1, min(_sf, k))]

    mu_s = ref.mean(dim=(0, 1, 2))                 # [C]
    sd_s = ref.std(dim=(0, 1, 2)).clamp_min(1e-6)
    mu_t, sd_t, mu_last = _guide_stats(guide.to(images.dtype))   # 多帧：均值外推 + 对比度聚合
    sd_t = sd_t.clamp_min(1e-6)

    # —— 缝最小化选目标（纪律⑤，2026-10-14 真实缝实测后加）——
    # 缝的真正两侧 = 父段**末帧** ↔ 子段首帧；外推是「假想的下一帧」。逐通道比较
    # 两个目标的「修正后阶跃」，只采信更小的那个 ⇒ 多帧 guide **永不放大阶跃**。
    # 实测（18 条真实缝，33b 验收轮 + 追痕实跑）：外推无一次必然收益；无此护栏时
    # 无闸臂在 33b 缝上把 0.0012 放大成 0.0030（chroma 噪声拟合出 0.0079 假趋势）。
    _a = mu_last - mu_s                       # 既有阶跃（子段首帧 vs 父段末帧）
    _b = mu_t - mu_s                          # 外推目标对应的修正量
    _w = float(weight)
    mu_t = torch.where((_a - _w * _b).abs() < (_a - _w * _a).abs(), mu_t, mu_last)

    gain = (sd_t / sd_s).clamp(1.0 / float(gain_max), float(gain_max))
    offs = (mu_t - mu_s).clamp(-float(offset_max), float(offset_max))

    # —— levellock 纪律①：噪声闸（失败安全）——
    # step（缝上的阶跃）不超过「局部帧间噪声 × step_over_noise」且不超过地板
    # ⇒ 没什么可修，原样返回（逐位不动）。两个闸都填 0 = 关闸（旧行为）。
    _step = float((mu_t - mu_s).abs().max())
    _noise = _head_noise(src)
    if _step <= max(float(step_floor), float(step_over_noise) * _noise):
        return images

    corr = (src - mu_s) * gain + mu_s + offs
    w = torch.linspace(float(weight), 0.0, k, device=images.device,
                       dtype=torch.float32).view(k, 1, 1, 1)
    out = (src + w * (corr - src)).clamp(0.0, 1.0).to(images.dtype)
    return torch.cat([out, images[k:]], dim=0)


# —— P3 反卷积去模糊（Wiener）—— 2026-09-16 补齐后处理层方案
def deconv_head_zone(images: torch.Tensor, frames: int = 12,
                     strength: float = 0.0, radius: float = 1.5,
                     noise: float = 1e-4) -> torch.Tensor:
    """段头**频域高频增强**（原「反卷积」路线，2026-09-16 修正）。

    ⚠️ 为什么不用 Wiener 逆滤波：逆滤波要求**已知 PSF**，而我们的「糊」是
       **模型生成时的低频化**，PSF 未知 —— 硬套一个高斯 PSF 只会适得其反。
       实测：逆滤波的增益在中低频段趋于 **0**（等于又加一次低通），
       段头锐度反而从 0.000026 掉到 0.000005。

    ⇒ 改为**频域高频增强**：按频段增益，高频段放大、低频段保持，避免空域
      unsharp 的 halo。`radius` 越大 ⇒ 截止频率越低 ⇒ 被增强的高频越多（控制
      从哪个尺度开始算高频，等效截止频率）。

    strength = 与原帧混合比（0=不动）；帧数守恒、只作用开头 frames 帧、权重渐减。
    """
    if strength <= 0.0 or images.dim() != 4 or int(images.shape[0]) == 0:
        return images
    n = int(images.shape[0])
    k = min(int(frames), n)
    if k <= 0:
        return images
    x = images[:k].float()
    h, w = int(x.shape[1]), int(x.shape[2])
    Y = torch.fft.rfft2(x.permute(0, 3, 1, 2))            # [k,C,H,Wr]
    # 径向频率（0..1），用来做软截止
    fy = torch.fft.fftfreq(h, device=x.device).view(-1, 1)
    fx = torch.fft.rfftfreq(w, device=x.device).view(1, -1)
    fr = torch.sqrt(fy ** 2 + fx ** 2)
    fr = fr / fr.max().clamp_min(1e-6)
    cutoff = min(max(1.0 / max(float(radius), 0.5), 0.05), 1.0)   # 半径越大 → 截止越低（增强的高频越多）
    gain = 1.0 + float(strength) * (fr / cutoff).clamp(0.0, 1.0) ** 2   # 高频段最多 ×2
    out = torch.fft.irfft2(Y * gain.unsqueeze(0).unsqueeze(0), s=(h, w)).permute(0, 2, 3, 1)
    out = out.clamp(0.0, 1.0).to(images.dtype)
    wgt = torch.linspace(1.0, 0.0, k, device=images.device, dtype=torch.float32).view(k, 1, 1, 1)
    blended = x.to(images.dtype) * (1.0 - wgt * float(strength)) + out * (wgt * float(strength))
    return torch.cat([blended.clamp(0.0, 1.0).to(images.dtype), images[k:]], dim=0)


# —— 后处理层的「段体参考」稳健化（2026-09-19）——
# 治的病：段内三层（直方图 / 白平衡 / 高频迁移）的基准一律是「第 40 帧之后整段均值」。
#   段只有 90–192 帧 ⇒ 90 帧时段体只剩 50 帧；段体若有运动/推镜/曝光漂移，
#   **基准本身就不是「这段的常态外观」却照样拿去改段头** ——
#   与音频缝「取到近静默窗当床声」是同一类错（选窗准则反了）。
POST_BODY_DISP_MAX: float = 0.08   # 段体逐帧亮度 (p75−p25)/中位 超此值 ⇒ 基准不可信 ⇒ 弃权


def robust_body(body: torch.Tensor, enabled: bool = True):
    """段体参考帧的**稳健选取** + **离散度报数**。返回 ``(subset, dispersion)``。

    - ``dispersion`` = 段体逐帧 mean luma 的 ``(p75−p25)/中位数``（无量纲）；
    - ``subset`` = 亮度落在 ``[p25, p75]`` 的帧（中央 50%）—— 段体有运动/曝光漂移时，
      整段均值会被两端拉偏，**中央段的统计才代表这段的常态外观**；
    - ``enabled=False`` ⇒ 不筛（原样返回），供 ``baseline=legacy`` 复现旧口径做对照。

    ⚠️ 这是**零帧数代价**的统计（只读），不产生额外 pass。
    """
    n = int(body.shape[0])
    if n <= 0:
        return body, 0.0
    lum = body.float().mean(dim=(1, 2, 3))
    med = float(lum.median())
    if not enabled or n < 4 or med <= 1e-6:
        return body, 0.0
    q = torch.quantile(lum, torch.tensor([0.25, 0.75], device=lum.device, dtype=lum.dtype))
    disp = float((q[1] - q[0]) / med)
    sel = body[(lum >= q[0]) & (lum <= q[1])]
    if int(sel.shape[0]) < 2:
        return body, disp
    return sel, disp


def head_metrics(images: torch.Tensor, frames: int):
    """段头的两个**可审计标量**：``(平均亮度, 高频能量)``。

    用途：把「黑箱叠 N 层」变成「**每层可归因**」——每层作用后各报一次，谁把段头改了多少一目了然。
    高频 = 与 3×3 盒式模糊之差的绝对值均值（尺度无关，够用）。
    """
    k = max(1, min(int(frames), int(images.shape[0])))
    h = images[:k].float()
    return float(h.mean()), float((h - _box_blur_hwc(h, 3)).abs().mean())


def borrow_detail_from_body(images: torch.Tensor, frames: int = 12,
                            strength: float = 0.0, blur: int = 9,
                            body_start: int = 40, robust: bool = True) -> torch.Tensor:
    """把**段体**的高频结构迁移到段头：段头留自己的低频，高频换成段体的。

    做法：``head + s·(highpass(body_ref) 的能量匹配到 head 的高频)``。
    ``body_ref`` 取段体若干帧的中位（避免单帧噪声）。

    用途：段头"糊"= 高频缺失，而段体高频是**同一个场景/光照**下的 ⇒ 迁移不会引入异质内容
    （这点比 P2 低频残差更进一层：P2 只补低频，本函数补的是高频）。

    ⚠ 风险：若段头与段体内容差异大（如人物位移大），迁移会带出"纹理错位"。
    """
    if strength <= 0.0 or images.dim() != 4 or int(images.shape[0]) <= int(body_start):
        return images
    n = int(images.shape[0])
    k = min(int(frames), n)
    if k <= 0:
        return images
    x = images.float()
    head = x[:k]
    body, _ = robust_body(x[int(body_start):], robust)       # 稳健参考（见 robust_body）
    ref = body.median(dim=0).values.unsqueeze(0)             # [1,H,W,C] 段体代表帧
    lo_head = _box_blur_hwc(head, blur)
    lo_ref = _box_blur_hwc(ref, blur)
    hi_head = head - lo_head                                  # 段头高频
    hi_ref = ref - lo_ref                                     # 段体高频
    # 能量匹配：让迁移的高频与段头高频同量级（避免强度失控）
    e_head = hi_head.abs().mean().clamp_min(1e-6)
    e_ref = hi_ref.abs().mean().clamp_min(1e-6)
    hi_ref = hi_ref * (e_head / e_ref)
    wgt = torch.linspace(1.0, 0.0, k, device=images.device, dtype=torch.float32).view(k, 1, 1, 1)
    add = hi_ref.expand(k, -1, -1, -1) * (wgt * float(strength))
    out = (head + add).clamp(0.0, 1.0).to(images.dtype)
    return torch.cat([out, images[k:]], dim=0)


# —— 分位统计的安全上限（2026-09-25 实测钉死）——
# 🔴 `torch.quantile` 有 **numel 硬上限 = 2^24（16,777,216）**，超过即抛
#    `RuntimeError: quantile() input tensor is too large`。
# 段体/段头是**整帧像素** flatten ⇒ 高分辨率下极易撞上：
#    0.796MP(672×1184) 每通道只能容 21.1 帧、1.03MP(768×1344) 只有 16.3 帧；
#    而 90 帧段的段体有 50 帧（`robust_body` 筛后 ~25 帧 = 19.9M）⇒ **必然超限**。
# ⚠ 0.3MP 时代测不出来（那里每通道能容 54.6 帧）—— 那是**分辨率红利**，不是设计。
QUANTILE_MAX_ELEMS: int = 1 << 24
# 子采样的**目标点数** —— 让工作量与分辨率**脱钩**（这是关键，不是"采到刚好不崩"）。
# 🔴 实测（2026-09-25，16 线程 CPU，256 个分位点）：
#      2^21 = 2.1M 点 → 378 ms     2^24 = 16.7M 点 → 3501 ms
#    而 `match_hist_head_to_body` 是**逐通道 ×(段体+段头)** ⇒ 3×2 = **6 次** quantile：
#      · 若采到刚好 ≤2^24：2.0MP 段（段体 51.8M）→ 13M 点 ⇒ 单段 **~18 s**（不崩，但等于卡住）
#      · 采到 2^21：单段 **~2.3 s**，且**与分辨率无关**（2MP / 4MP / 8MP 都一样）
# 精度代价：2^21 点估计分位，标准误 ~sqrt(0.25/2^21) = 3.5e-4 ——
#   在 0–1 色阶上 ≈ **0.09/255**，仍**比 256 桶的桶宽（3.9e-3）小一个量级** ⇒ 肉眼不可见。
QUANTILE_SAMPLE_ELEMS: int = 1 << 21


def _quantile_capped(t: torch.Tensor, qs: torch.Tensor, tag: str = "") -> torch.Tensor:
    """``torch.quantile`` 的安全包装：超限时**等间隔子采样**到 ``QUANTILE_SAMPLE_ELEMS`` 再求分位。

    为什么是子采样而不是分桶直方图：分位估计的误差量级是 O(1/√n)，子采样到 2^21 点时
    标准误 3.5e-4 —— **比 256 桶的桶宽（~3.9e-3）还小一个量级**。用更小的改动拿到更准的
    结果，且**不动 `torch.quantile` 自身的算法**（只是输入变稀疏）。
    等间隔（而非随机）采样 ⇒ 结果**可复现**，不引入随机种子依赖。

    超限时打印一行（``tag`` 非空时）—— 包内纪律「近似要打标、不静默」。
    """
    n = int(t.numel())
    # 🔴 门槛用 SAMPLE 而不是 MAX —— 2026-09-25 自我审核抓到的**耗时非单调**：
    #   若按 MAX(2^24) 判，2.1M~16.7M 这一段会走**精确**路径，而实测 15.4M 点要 **3.1 s/次**；
    #   本函数一次调用共 6 次 quantile ⇒ **单段 19 s** —— 反倒比"超限后采样"（0.38 s/次）**慢 8 倍**。
    #   0.3MP 的段体（15.4M）正好落在这段里 ⇒ **低分辨率比高分辨率还慢**，反直觉但真实。
    #   按 SAMPLE(2^21) 判 ⇒ 耗时上界恒 ~0.38 s/次、**与点数无关**；代价是 2.1M 点以上的输入
    #   都走近似（标准误 3.5e-4 ≈ 0.09/255，仍比 256 桶的桶宽小一个量级 ⇒ 不可见）。
    if n <= QUANTILE_SAMPLE_ELEMS:
        return torch.quantile(t, qs)                      # 够小 ⇒ 精确，且耗时上界 ~0.4 s
    step = -(-n // QUANTILE_SAMPLE_ELEMS)                 # ceil：采到 ~2^21 点，与分辨率无关
    sel = t[::step]
    if tag:
        # ⚠️ 文案必须说清「触发的是**采样门槛**，不是 quantile 的硬上限」—— 2026-09-25 修：
        #   原来写的是「超出 torch.quantile 上限 2^24」，而 n=3M（离 2^24 还远）也会走到这里
        #   ⇒ **标签本身说错了**。在一个把「近似要打标、不静默」当纪律的包里，
        #   标错的标签比不打标更糟（用户会以为"没到上限就不该采样"）。
        #   同时这一行让 `QUANTILE_MAX_ELEMS` 真正被引用（此前它是**死常量**，只在注释里出现）。
        print("[H3 Relay] 分位统计子采样（%s）：%d 点超过采样门槛 %d（torch.quantile 硬上限为 %d）⇒ "
              "每 %d 点取 1（实际用 %d 点，色阶统计误差 ~1/√n）"
              % (tag, n, QUANTILE_SAMPLE_ELEMS, QUANTILE_MAX_ELEMS, step, int(sel.numel())))
    return torch.quantile(sel, qs)


# —— P6 直方图匹配（段头 → 段体）—— 2026-09-16 补齐后处理层方案
def match_hist_head_to_body(images: torch.Tensor, frames: int = 12,
                            strength: float = 0.0, body_start: int = 40,
                            bins: int = 256, robust: bool = True) -> torch.Tensor:
    """把段头的**色阶分布**（直方图）对齐到段体 —— 比 P2 低频残差更强。

    P2（``lowfreq_pull``）只对齐**低频均值**；本函数对齐**整条分布曲线**
    （即不只"亮度一致"，而是"亮的更亮、暗的更暗"的比例也一致）。

    逐通道做 CDF 映射；``strength`` = 与原帧的混合比。
    只作用开头 ``frames`` 帧，权重线性衰减。
    """
    if strength <= 0.0 or images.dim() != 4 or int(images.shape[0]) <= int(body_start):
        return images
    n = int(images.shape[0])
    k = min(int(frames), n)
    if k <= 0:
        return images
    x = images.float()
    body, _ = robust_body(x[int(body_start):], robust)       # 稳健参考（见 robust_body）
    out = x[:k].clone()
    for c in range(int(x.shape[3])):
        src = x[:k, :, :, c].flatten()
        ref = body[:, :, :, c].flatten()
        # 段体的分位点
        qs = torch.linspace(0.0, 1.0, int(bins), device=x.device)
        # ⚠ 必须走 _quantile_capped：整帧 flatten 在高分辨率下会撞 torch.quantile 的
        #   2^24 上限（90 帧 / 0.796MP 的段体筛后 ~25 帧 = 19.9M ⇒ 直接抛），
        #   而 0.3MP 下不会 —— 2026-09-25 实测钉死。
        ref_q = _quantile_capped(ref, qs, "段体" if c == 0 else "")
        # 把段头像素按其在段头分布中的分位，映射到段体同分位的值
        src_q = _quantile_capped(src, qs, "段头" if c == 0 else "")
        idx = torch.searchsorted(src_q, src.clamp(src_q[0], src_q[-1]))
        idx = idx.clamp(1, int(bins) - 1)
        lo, hi = src_q[idx - 1], src_q[idx]
        t = ((src - lo) / (hi - lo).clamp_min(1e-8)).clamp(0.0, 1.0)
        mapped = ref_q[idx - 1] * (1.0 - t) + ref_q[idx] * t
        out[:, :, :, c] = mapped.reshape(k, int(x.shape[1]), int(x.shape[2]))
    wgt = torch.linspace(1.0, 0.0, k, device=images.device, dtype=torch.float32).view(k, 1, 1, 1)
    blended = x[:k] * (1.0 - wgt * float(strength)) + out * (wgt * float(strength))
    return torch.cat([blended.clamp(0.0, 1.0).to(images.dtype), images[k:]], dim=0)


# —— P7 灰世界白平衡校正（段头 → 段体）—— 2026-09-16 补齐后处理层方案
def match_white_balance(images: torch.Tensor, frames: int = 12,
                        strength: float = 0.0, body_start: int = 40,
                        robust: bool = True) -> torch.Tensor:
    """把段头的**通道比例（色温）**对齐到段体 —— 对症"色温滑档"。

    与 P2 正交：P2 管**亮度/低频总量**，本函数管**R:G:B 的相对比例**（色温/色调）。
    做法：灰世界假设下，令段头各通道均值比例 = 段体的比例；逐通道乘增益。

    ``strength`` = 增益与原值的混合比（0 = 不动；1 = 完全对齐）。
    """
    if strength <= 0.0 or images.dim() != 4 or int(images.shape[0]) <= int(body_start):
        return images
    n = int(images.shape[0])
    k = min(int(frames), n)
    if k <= 0:
        return images
    x = images.float()
    head = x[:k]
    body, _ = robust_body(x[int(body_start):], robust)       # 稳健参考（见 robust_body）
    hm = head.mean(dim=(0, 1, 2)).clamp_min(1e-6)       # [C]
    bm = body.mean(dim=(0, 1, 2)).clamp_min(1e-6)
    # 只取通道比例，不改变整体亮度
    h_ratio = hm / hm.mean()
    b_ratio = bm / bm.mean()
    gain = (b_ratio / h_ratio).clamp(0.8, 1.25)          # 限幅防偏色
    g = 1.0 + (gain - 1.0) * float(strength)
    wgt = torch.linspace(1.0, 0.0, k, device=images.device, dtype=torch.float32).view(k, 1, 1, 1)
    adj = 1.0 + (g.view(1, 1, 1, -1) - 1.0) * wgt
    out = (head * adj).clamp(0.0, 1.0).to(images.dtype)
    return torch.cat([out, images[k:]], dim=0)

def unsharp_frames(images: torch.Tensor, amount: float) -> torch.Tensor:
    """3×3 高斯 unsharp mask。``amount<=0`` 原样返回（零开销短路）。

    纯 torch、无 cv2/PIL 依赖；通道分组卷积，NHWC 进出（与节点 IMAGE 约定一致）。
    """
    if amount <= 0.0 or images.dim() != 4 or int(images.shape[0]) == 0:
        return images
    x = images.to(torch.float32).permute(0, 3, 1, 2)        # NHWC → NCHW
    c = int(x.shape[1])
    k = x.new_tensor([1.0, 2.0, 1.0])
    k = (k[:, None] * k[None, :]).div(16.0).view(1, 1, 3, 3)
    k = k.expand(c, 1, 3, 3).contiguous()
    # replicate pad：zero pad 会在画面边界吃出假响应（与 _sharpness 同一坑）
    xp = torch.nn.functional.pad(x, (1, 1, 1, 1), mode="replicate")
    blur = torch.nn.functional.conv2d(xp, k, groups=c)
    out = (x + float(amount) * (x - blur)).clamp(0.0, 1.0)
    return out.permute(0, 2, 3, 1).to(images.dtype)


def settle_compensate(images: torch.Tensor, body_start: int = 40,
                      strength: float = 1.0, smooth: int = 3) -> Tuple[torch.Tensor, str]:
    """**自适应糊区补偿**——方向二「观测—校正闭环」的落地：settle 检测器升级成纠偏器。

    症状（2026-09-19 逐帧剖面的定案）：缝后第 2 帧起清晰度断崖（实测段体基线的 42%）
    再单调爬升 ~15 帧——**生成层 settle 行为在像素域的残留**。固定 ``settle_sharpen``
    按「缝端最强→尾端 0」线性衰减，与真实亏空曲线（帧 0-1 清晰、第 2 帧最深）**不重合**；
    本函数**当场量**每帧高频能量（与 3×3 盒式模糊之差，拉普拉斯能量比的廉价代理），
    以段体稳健中位为基准，**按亏空比例**做 unsharp：

      · 亏空越深补得越多（帧 2 最深 → 补最强）；
      · 已达基线的帧（含清晰的帧 0-1）与**段体一律不动**（deficit=0）；
      · 权重上限 1.0（防振铃/噪声放大）、时间平滑（默认 3 帧）防逐帧跳变；
      · 段长 ≤ body_start 或 strength≤0 ⇒ 直通。

    纯 torch、帧数守恒、零采样开销。返回 ``(out, report)``。
    """
    if strength <= 0.0 or images.dim() != 4:
        return images, ""
    x = images.float()
    n = int(x.shape[0])
    bs = int(body_start)
    if n <= bs:
        return images, ""
    hp = x - _box_blur_hwc(x, 3)                            # 高通 [N,H,W,C]
    hf = hp.abs().mean(dim=(1, 2, 3))                       # 每帧高频能量 [N]
    body, _ = robust_body(x[bs:])                           # 段体稳健筛选（复用）
    base = float((body - _box_blur_hwc(body, 3)).abs().mean())
    if base <= 1e-7:
        return images, ""
    ratio = hf / base                                       # 每帧 / 段体基准
    deficit = ((base - hf) / base).clamp(0.0, 1.0)          # 亏空比例 [0,1]
    deficit[bs:] = 0.0                                      # 段体一律不动
    w = (deficit * float(strength)).clamp(0.0, 1.0)
    sm = max(1, int(smooth))
    if sm > 1:                                              # 时间平滑（边缘复制 pad）
        wp = torch.nn.functional.pad(w.view(1, 1, -1), (sm // 2, sm // 2), mode="replicate")
        w = wp.view(-1)[sm // 2: sm // 2 + n].clamp(0.0, 1.0)
    w = w.masked_fill(deficit <= 0.0, 0.0)              # 原本无亏空的帧（含帧 0-1）严格不动：
                                                        # 平滑会把邻帧的亏空渗进来，必须屏蔽
    out = (x + w.view(-1, 1, 1, 1) * hp).clamp(0.0, 1.0).to(images.dtype)

    lo = int(torch.argmin(ratio[:bs]))                      # 量测报告（可审计）
    climb = next((i for i in range(lo, n) if float(ratio[i]) >= 0.95), -1)
    acted = int((w > 0.02).sum())
    rep = ("自适应糊区补偿 %.2f：段头最低 %.0f%%（第 %d 帧）｜回基线第 %d 帧｜"
           "补 %d 帧（最大增益 %.2f，段体与已达标帧不动）"
           % (float(strength), float(ratio[:bs].min()) * 100, lo, climb,
              acted, float(w.max())))
    return out, rep


def sharpen_head_zone(images: torch.Tensor, frames: int = SETTLE_SHARPEN_FRAMES,
                      amount: float = SETTLE_SHARPEN) -> torch.Tensor:
    """对**开头 frames 帧**做渐变锐化（缝端最强 → 尾端 0）。

    只做**一次**卷积（整段），再按线性权重与原帧混合——逐帧不同强度不必逐帧卷积。
    **帧数守恒、不动音频、零采样开销。**
    """
    if amount <= 0.0 or images.dim() != 4:
        return images
    n = int(images.shape[0])
    k = min(int(frames), n)
    if k <= 0:
        return images
    zone = images[:k]
    sharp = unsharp_frames(zone, amount)
    w = torch.linspace(1.0, 0.0, k, device=images.device, dtype=sharp.dtype)
    w = w.view(k, 1, 1, 1)
    return torch.cat([w * sharp + (1.0 - w) * zone, images[k:]], dim=0)

def _blur_collapse(images: torch.Tensor, pin: int, short_side: int = None):
    """在**指定分析尺度**上判「是否有深塌陷」。返回 ``(has_collapse, dip, ref)``。

    供 L4 跨尺度仲裁复用：同一现象在两个尺度上都出现 = 真现象；
    只在单一尺度出现 = 尺度伪影（编码噪声 / 原生分辨率细节噪声）→ 不动刀。
    """
    n = int(images.shape[0])
    pin = int(pin)
    if pin <= 0 or n < pin + 2:
        return False, 0.0, 0.0
    sh = _sharpness(images[: min(n, pin + SETTLE_SCAN + 2)], short_side=short_side)
    if sh.numel() < pin + 2:
        return False, 0.0, 0.0
    ref_lo = pin + SETTLE_REF_OFF
    far = _sharpness(images[ref_lo: min(n, ref_lo + 40)], short_side=short_side)
    ref = max(float(sh[:pin].median()), float(far.median()) if far.numel() else 0.0)
    if ref <= 0.0:
        return False, 0.0, 0.0
    zone = sh[pin: pin + SETTLE_SCAN + 1]
    dip = float(zone.min())
    has = bool((zone < BLUR_ZONE_RATIO * ref).any()) and dip < BLUR_COLLAPSE_RATIO_SHARP * ref
    return has, dip, ref


def boundary_jump_ratio(images: torch.Tensor, pin: int,
                        max_settle: int = MAX_SETTLE) -> Tuple[float, float, float]:
    """量「钉住区边界」处的帧差倍数：``D(pin-1 → pin) ÷ 段内基线``。

    返回 ``(ratio, d_edge, d_base)``；数据不足返回 ``(0.0, 0.0, 0.0)``。
    纯比值、无量纲 —— 分辨率/步数/LoRA 整体移动量级时结论不变。
    """
    pin = int(pin)
    n = int(images.shape[0])
    if pin <= 1 or n < pin + 2:
        return 0.0, 0.0, 0.0
    diff = _frame_diffs(images[: min(n, pin + max_settle + 2)])
    if diff.numel() <= pin:
        return 0.0, 0.0, 0.0
    d_edge = float(diff[pin - 1].item())          # 钉住区末帧 → 首帧新内容
    inner = diff[2:max(3, pin - 1)]
    d_base = float(inner.median()) if inner.numel() else float(diff.median())
    d_base = max(d_base, _baseline_floor(images))
    return (d_edge / d_base if d_base > 0 else 0.0), d_edge, d_base


def trim_jump_curve(images: torch.Tensor, pin: int,
                    max_settle: int = SETTLE_CAP) -> list:
    """**裁量 vs 跳跃** 曲线：预测「裁掉 settle 帧后，成片缝处会有多大的跳」。

    关键几何（2026-09-15 想通）：成片的缝是
        ``raw[pin-1]``（= 上段末帧的复现，裁后它成为缝前末帧）
      → ``raw[pin+settle]``（= 裁后首帧）
    —— **两者相隔 settle+1 帧**。所以那个"跳"不是相邻帧差，而是**跨过被裁区的「时间跳跃量」**。
    ⇒ **裁得越多，跳得越大**（本仓作者：「裁切 = 时间跳跃 = 跳切」）。节点可在裁之前就把它算出来。

    返回 ``[(settle, 归一跳跃), ...]``；跳跃 = ``MAE(raw[pin+s], raw[pin-1]) ÷ 段内基线``。
    纯比值、无量纲；``settle=0`` 那一项即"只裁钉住区"时的天然跳跃（下界）。
    """
    pin = int(pin)
    n = int(images.shape[0])
    if pin <= 1 or n < pin + 2:
        return []
    f = images.to(torch.float32)
    inner = _frame_diffs(images[: min(n, pin + MAX_SETTLE + 2)])
    base = float(inner[2:max(3, pin - 1)].median()) if inner.numel() > pin else float(inner.median())
    base = max(base, _baseline_floor(images))
    if base <= 0:
        return []
    ref = f[pin - 1]
    out = []
    for s in range(max(0, int(max_settle)) + 1):
        i = pin + s
        if i >= n:
            break
        out.append((s, float((f[i] - ref).abs().mean()) / base))
    return out


def observation_profile(images: torch.Tensor, pin: int,
                        scan: int = SETTLE_SCAN) -> dict:
    """节点实测的**多通道观测剖面**：锐度 / 亮度 / 色阶(RGB) / 帧差，覆盖「钉住区之后 scan+1 帧」。

    动机（2026-09-15 本仓作者 要求「不只是锐度，色阶、明暗等都需要」）：
    沉降检测本来就是**三路**（帧差 / 锐度 / 色档），report 只报一路 = **只开了三分之一的窗**。
    把节点实际看到的**各通道原始数**全报出来，人眼与机检才能在同一组数上对话。

    返回 dict：
      ref_sharp   锐度基准（= `detect_settle` 锐度路用的那个）
      ref_rgb     [R,G,B] 体区色档基准（= 色档路用的那个）
      sharp[i]    第 pin+i 帧 锐度 ÷ ref_sharp
      luma[i]     第 pin+i 帧 灰度均值（0-1）
      rgb[c][i]   第 pin+i 帧 R/G/B 均值（0-1）
      diff[i]     第 pin+i-1 → pin+i 的帧差（MAE，0-1 量纲）
    """
    pin = int(pin)
    n = int(images.shape[0])
    out = {"ref_sharp": 0.0, "ref_rgb": [0.0, 0.0, 0.0],
           "sharp": [], "luma": [], "rgb": [[], [], []], "diff": []}
    if pin <= 0 or n < pin + 2:
        return out
    hi = min(n, pin + scan + 2)
    win = images[:hi]
    sh = _sharpness(win)
    ref_lo = pin + SETTLE_REF_OFF
    far = _sharpness(images[ref_lo: min(n, ref_lo + 40)])
    ref = max(float(sh[:pin].median()), float(far.median()) if far.numel() else 0.0)
    out["ref_sharp"] = ref

    f = win.to(torch.float32)
    if f.dim() == 4 and f.shape[-1] in (1, 3, 4):
        rgb = f.mean(dim=(1, 2))                      # [N,C]（ComfyUI IMAGE = [N,H,W,C]）
    else:
        rgb = f.reshape(f.shape[0], -1).mean(dim=1, keepdim=True)
    # 色档基准 = 窗外体区 RGB 均值中位（与色档路同口径）
    body_lo = pin + MAX_SETTLE + 2
    body = images[body_lo: min(n, body_lo + 40)].to(torch.float32)
    if body.shape[0] >= 4:
        brgb = (body.mean(dim=(1, 2)) if (body.dim() == 4 and body.shape[-1] in (1, 3, 4))
                else body.reshape(body.shape[0], -1).mean(dim=1, keepdim=True))
        out["ref_rgb"] = [float(brgb[:, c].median()) if brgb.shape[1] > c else 0.0
                          for c in range(3)]

    diffs = _frame_diffs(win)
    nd = int(diffs.shape[0])
    idx = list(range(pin, min(hi, pin + scan + 1)))
    out["sharp"] = [(float(sh[i]) / ref) if ref > 0 else 0.0 for i in idx]
    out["luma"] = [float(rgb[i].mean()) for i in idx]
    for c in range(3):
        out["rgb"][c] = [float(rgb[i, c]) if rgb.shape[1] > c else 0.0 for i in idx]
    out["diff"] = [float(diffs[i - 1]) if 0 < i <= nd else 0.0 for i in idx]
    return out


def scan_head_repeat(images: torch.Tensor, pin: int,
                     scan: int = REPEAT_SCAN,
                     thresh: float = REPEAT_MAE) -> Tuple[int, float, float]:
    """路径 4：**复现残留**检测——窗内帧与钉住区的最小 MAE。

    动机见模块常量 ``REPEAT_MAE`` 上方注释（RESEARCH_seam_frontier §13 D7）：
    现有三路都看不见复现残留 —— 复现帧**清晰**（锐度路盲）、**色档一致**（色档路盲）、
    **无硬跳**（硬跳路盲）。本路是唯一能看见它的一路。

    返回 ``(settle, signal, reference)``：
      ``settle``    = 从 ``pin`` 起**连续复现**的帧数（超出钉住区、应裁掉的部分）
      ``signal``    = 最后一个复现帧的最小 MAE（越小越像）
      ``reference`` = 阈值本身（便于上层按同一量纲报数）

    **纯自参考**：不与任何全局常数比，只问「这一帧像不像钉住区自己」。
    测不准（窗太短 / ``pin`` 为 0 / 画布退化）⇒ 0 ⇒ 与旧行为逐位一致。

    ⚠ 本路**只会让裁量变大**（上层取 max）⇒ 若误判会多吃内容。
      故 ``REPEAT_MIN_RUN`` 要求连续 ≥2 帧，且阈值收到 5/255（近位级相同）。
    """
    n = int(images.shape[0])
    if pin < 1 or n <= pin:
        return 0, 0.0, float(thresh)
    k = min(int(scan), n - pin)
    if k < REPEAT_MIN_RUN:
        return 0, 0.0, float(thresh)

    small = _canonicalize(images[: pin + k], ANALYSIS_SHORT_SIDE)
    zone = small[pin:]                      # [k,H,W,C]
    pinreg = small[:pin]                    # [pin,H,W,C]

    # 逐帧「到钉住区的最小 MAE」——对 pin 循环，避免 pin×k 整块物化
    best = torch.full((k,), float("inf"), dtype=torch.float32)
    for p in range(pin):
        best = torch.minimum(best, (zone - pinreg[p]).abs().mean(dim=(1, 2, 3)))

    hit = best < float(thresh)
    if not bool(hit.any()):
        return 0, float(best.min().item()), float(thresh)

    # 只认**从 pin 起连续**的那一段：复现残留的形态就是"接着钉住区继续重复"
    run = 0
    for i in range(k):
        if bool(hit[i]):
            run = i + 1
        else:
            break
    if run < REPEAT_MIN_RUN:
        return 0, float(best.min().item()), float(thresh)
    return run, float(best[run - 1].item()), float(thresh)


def detect_settle(
    images: torch.Tensor,
    pin: int,
    max_settle: int = MAX_SETTLE,
    ratio: float = JUMP_RATIO,
) -> Tuple[int, float, float]:
    """在**未裁剪**的段首附近量出真实切换点，返回建议的沉降帧数。

    ``images`` 是完整 decode 结果（长度 N，前 ``pin`` 帧是钉住区的复现）。
    返回 ``(settle, signal, reference)``——signal/reference 的量纲随判定路径不同：
    硬跳路 = 帧差，锐度路 = Laplacian 均方，色档路 = RGB 均值偏离；全**纯比值判定**。
    三路独立出候选，**取 settle 最大者**（不同伪影类型互补，谁检测到得深听谁的）。

    v0.4.1 四层判定（本仓作者 要求的多维度/自参考方案）：

      0. **结构性护栏**（不变）：窄窗贴 ``pin`` 不做全局 argmax；``settle ≤ max_settle``；
         测不准 ⇒ 0 ⇒ 与"只裁钉住区"的旧行为逐位一致，最坏不会更差。
      1. **硬跳路（帧差 · 双门槛 + 验身）**：窗内最大帧差须同时过
         「体 z 分数 > Z_JUMP」（对段体帧差的 median+MAD 标准化——运动剧烈的段
         体差大，同样的跳不值钱）和「比值 > JUMP_RATIO×体中位」两道门；
         再**验身**：跳后 2-5 帧锐度须回到体分布（无一帧深塌陷）——跳完还是糊的
         = 假跳/闪烁，否决。
      2. **锐度路（塌陷-恢复 · 深度证据闸）**：基准 = max(复现区中位, 窗外体区中位)
         （v0.3.2 双基准：复现区自身可能已发软）；窗内须**同时**出现
         「塌陷帧（<0.55×基准）」和「深塌陷证据（最深处 <0.35×基准）」，
         且窗内可见恢复（>0.5×基准，宁少勿多）——沉降 = 最后一个塌陷帧 − pin + 1。
      3. **色档收敛路（v0.4.1，新增）**：注噪/taper 续接的「收敛尾巴」——可见头部
         若干帧的亮度/色档仍在对齐去噪轨迹（重影+低频漂移），锐度法不可见。
         基准 = 窗外体区 RGB 均值的**逐通道中位数**；阈值 = max(GRADE_DEV_Z×体MAD,
         绝对下限)。**必须在窗内观察到收敛**（头部偏离 → 其后全部回归基准）才动刀：
         没有收敛 = 头部本来就是另一档内容的正常延续（如窗外远处切镜），照裁会
         把合法内容裁掉（12.9 类反例）。沉降 = 首个回归帧的下标。
      4. **参考分布全部来自段体**：分辨率/步数/LoRA/内容整体移动量级时，基准同步
         移动——4 步低清、928p 高清、CombatV2、平坦场景共用同一套比值。
    """
    pin, max_settle = int(pin), max(0, int(max_settle))
    if pin <= 0 or max_settle <= 0:
        return 0, 0.0, 0.0
    n = int(images.shape[0])
    if n < pin + 2:
        return 0, 0.0, 0.0
    # ★ 只算用得到的那一小段，别对整段做差（120 帧 448x768 实测 39 ms vs 187 ms）
    win = images[: min(n, pin + max_settle + 2)]
    far_lo = pin + max_settle + 2
    body = images[far_lo: min(n, far_lo + 40)]
    if body.shape[0] < 8:
        body = win                      # 短段：体参考退化到窗内（仍优于任何全局常数）
    diff = _frame_diffs(win)
    inner = diff[2:max(3, pin - 1)]
    baseline = float(inner.median()) if inner.numel() else float(diff.median())
    lo = max(2, pin - 1)
    hi = min(int(diff.shape[0]), pin + max_settle)
    if hi <= lo:
        return 0, 0.0, baseline
    seg = diff[lo:hi]
    j = lo + int(torch.argmax(seg).item())
    val = float(diff[j].item())

    best = (0, 0.0, baseline)           # (settle, signal, reference)——取最大

    # —— 路径 1：硬跳（体 z 分数 + 比值双门槛，过了再验身）——
    b_med, b_mad = _robust_stats(_frame_diffs(body))
    z_denom = max(b_mad, _baseline_floor(win))
    z_jump = (val - b_med) / z_denom if z_denom > 0 else 0.0
    if z_jump > Z_JUMP and val > ratio * max(b_med, _baseline_floor(win)):
        sharp_all = _sharpness(images[: min(n, pin + max_settle + 8)])
        ref_all = max(float(sharp_all[:pin].median()),
                      _robust_stats(_sharpness(body))[0])
        accept = ref_all <= 0.0         # 锐度基准退化 → 无法验身 → 采信跳点
        if not accept:
            after_jump = sharp_all[j + 1: min(j + 5, int(sharp_all.shape[0]))]
            accept = after_jump.numel() == 0 or not bool(
                (after_jump < BLUR_COLLAPSE_RATIO * ref_all).any())
        if accept:
            s = max(0, min(max_settle, j + 1 - pin))
            if s > best[0]:
                best = (s, val, baseline)

    # —— 路径 2：锐度塌陷-恢复（扫描窗 / 参考偏移 / 裁剪上限 三者独立，见 SETTLE_SCAN 注释）——
    # ⚠ 不能用 win：win 的宽度绑在 max_settle 上；本路按 SETTLE_SCAN 独立取窗，
    #   参考区按 SETTLE_REF_OFF 固定偏移（不再随窗漂移）。
    sh_scan = _sharpness(images[: min(int(images.shape[0]), pin + SETTLE_SCAN + 2)])
    if sh_scan.numel() >= pin + 2:
        ref_lo = pin + SETTLE_REF_OFF
        far_sh = _sharpness(images[ref_lo: min(int(images.shape[0]), ref_lo + 40)])
        ref_sh = max(float(sh_scan[:pin].median()),
                     float(far_sh.median()) if far_sh.numel() else 0.0)
        if ref_sh > 0.0:
            zone = sh_scan[pin: pin + SETTLE_SCAN + 1]
            below = zone < BLUR_ZONE_RATIO * ref_sh
            dip = float(zone.min())
            # —— L4 跨尺度仲裁（见 `_blur_collapse`）：真塌陷在**两个分析尺度上都该出现**；
            #    只在单一尺度出现 = 尺度伪影（编码噪声 / 原生分辨率细节噪声）→ 本路不出候选。——
            _ok = True
            if SETTLE_CROSS_SCALE:
                _ok, _d2, _r2 = _blur_collapse(images, pin, ANALYSIS_SHORT_SIDE_ALT)
            if _ok and bool(below.any()) and dip < BLUR_COLLAPSE_RATIO_SHARP * ref_sh:
                last = int(below.nonzero()[-1].item())
                after = zone[last + 1:]
                if after.numel() > 0 and float(after.max()) >= BLUR_RECOVER_RATIO * ref_sh:
                    # 🔴 2026-09-15 修：裁到**恢复点**，不是「最后一个塌陷帧」。
                    #   实测（onerA_cond4）：只裁到塌陷边界（<0.55×）会留下 4 帧
                    #   0.53–0.55× 的「恢复尾巴」——本仓作者 目检 = 「接缝处从模糊变清晰」。
                    #   故继续往后找锐度回到 SETTLE_RECOVER_TARGET×ref 的首帧，从那里起算 settle。
                    # ⚠ 必须**从最后一个塌陷帧之后**开始找恢复点！
                    #   2026-09-15 实测踩到：真实数据的形态是「首帧还算锐(0.79×) → 塌陷 → 恢复」，
                    #   若从窗首开始找，"首个恢复帧"就是第 0 帧 → s=0 → 一帧不裁（settle 从 8 退化成 0）。
                    rec = zone[last + 1:] >= SETTLE_RECOVER_TARGET * ref_sh
                    idx = rec.nonzero()
                    s = (min(SETTLE_CAP, last + 1 + int(idx[0].item())) if idx.numel()
                         else min(SETTLE_CAP, last + 1))
                    if s > best[0]:
                        best = (s, dip, ref_sh)

    # —— 路径 3：色档收敛（v0.4.1）——头部偏离体区色档、且窗内可见回归才动刀——
    rgb_head = win.float().mean(dim=(1, 2))                       # [Nw,3]（量纲随输入）
    rgb_body = body.float().mean(dim=(1, 2))                      # [Nb,3]
    if rgb_head.shape[0] >= pin + 1 and rgb_body.shape[0] >= 4:
        ref_rgb = rgb_body.median(dim=0).values
        devs_head = (rgb_head[pin: pin + max_settle + 1] - ref_rgb).abs().mean(-1)
        devs_body = (rgb_body - ref_rgb).abs().mean(-1)
        _, b_mad_g = _robust_stats(devs_body)
        # 量纲下限用已切出的小窗 + 体区估计，不对整段物化 abs()
        scale = max(_abs_max(win), _abs_max(body)) or 1.0
        thr = max(GRADE_DEV_Z * b_mad_g, GRADE_DEV_FLOOR * scale)
        ok = devs_head <= thr
        if bool(ok.any()):
            c = int(ok.nonzero()[0].item())            # 首个回归基准的帧（窗内偏移）
            if bool(ok[c:].all()):                     # 其后全部回归 = 观察到收敛
                s = min(max_settle, c)
                if s > best[0]:
                    best = (s, float(devs_head[c - 1]) if c > 0 else 0.0, float(thr))

    # —— 路径 4：复现残留（v0.5.0 / RESEARCH_seam_frontier §13 D7）——
    #   现有三路对「模型把钉住区内容又演了一遍」完全盲：那种帧**清晰**、**色档一致**、
    #   **无硬跳** ⇒ 三路都不出候选。本路用「到钉住区的最小 MAE」把它抓出来。
    #   仍取 max ⇒ 契约不变（只会让裁量变大，不会变小）。
    if SETTLE_REPEAT_PATH:
        s4, sig4, ref4 = scan_head_repeat(images, pin, REPEAT_SCAN, REPEAT_MAE)
        s4 = min(max_settle, s4)
        if s4 > best[0]:
            best = (s4, sig4, ref4)

    return best


def describe_head_jump(images: torch.Tensor, scan: int = 40,
                       within: Optional[int] = None) -> str:
    """给日志用的一行接缝自检结论。

    ``within`` 之内才给"再裁 N 帧"这种**可执行**的建议（默认 ``ADVISE_WITHIN``）。
    超出的突变只报位置与数值 —— 那多半是本段自己的真实切镜而不是接缝，
    照它动刀会把新内容裁掉（v0.3.0 之前就是这个行为）。
    """
    if within is None:
        within = ADVISE_WITHIN
    j, jump, baseline = scan_head_jump(images, scan)
    floor = max(baseline, _baseline_floor(images))
    ratio = jump / floor if floor > 0 else 0.0
    if j < 0 or jump <= JUMP_RATIO * floor:
        return ("[H3 Relay] 接缝自检：前 %d 帧无突变（最大帧差 %.2f，段内基线 %.2f）→ 起点干净。"
                % (scan, jump, baseline))
    if j > int(within):
        return ("[H3 Relay] ⚠ 接缝自检：第 %d→%d 帧有突变（%.2f vs 基线 %.2f，比值 %.1f×），"
                "但位置超出自动沉降上限（%d 帧）→ 大概率是本段自己的切镜，不是接缝，不动刀。"
                % (j, j + 1, jump, baseline, ratio, MAX_SETTLE))
    return ("[H3 Relay] ⚠ 接缝自检：第 %d→%d 帧有突变（%.2f vs 基线 %.2f，比值 %.1f×）\n"
            "            → 把「Trim AV」的 settle_frames 从 -1（自动）改成 %d 再跑"
            "（多裁掉突变前那帧）。\n"
            "            别改 trim_frames —— 那一格已被连线接管，前端会藏起来，改不了。"
            % (j, j + 1, jump, baseline, ratio, j + 1))


def describe_latent(latent: Any) -> str:
    """一行摘要，用于日志与节点输出。"""
    try:
        parts = streams_from_latent(latent)
    except Exception as e:
        return "无法解析 latent：%s" % e
    bits = []
    for i, t in enumerate(parts):
        name = "视频" if i == 0 else ("音频" if i == 1 else "#%d" % i)
        bits.append("%s%s" % (name, tuple(t.shape)))
    v = parts[0]
    if v.ndim >= 5:
        bits.append("%d 帧 @ %dx%d" % (pixel_frames(int(v.shape[2])), int(v.shape[4]) * 16, int(v.shape[3]) * 16))
    return " | ".join(bits)
