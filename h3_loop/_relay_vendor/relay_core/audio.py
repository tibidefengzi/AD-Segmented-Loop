# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 ComfyUI-H3-Latent-Relay contributors
# 第三方出处与许可见 THIRD-PARTY-NOTICES.md
"""relay_core.audio —— 音频缝（patch + 床环铺）· 孤立瞬态抑制 declick · 音频读存 · 多段音频拼接。

本模块由 `relay_core.py` 拆分而来（2026-10-07）；代码**逐字搬运**，未改任何算式。
对外仍由 `relay_core/__init__.py` 全量再导出 ⇒ 旧调用点（`relay_core.xxx`）不变。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
import json
import math
import os
import torch

from ._grid import FPS

# ---------------------------------------------------------------- 音频缝（节点内实现）
# 为什么必须在节点里做：组装层（ffmpeg）那三件事——crossfade / 头部补丁 / 床环铺——
# 都是「渲染之后的第二步」。效果要由节点在**渲染时**就落进段文件，组装才只剩拼接。
#
# 本组只实现**长度守恒**的两类（本段自己能改完的）：
#   · patch：把本段头部 N 秒（含解码 priming 的近静默 + 生成瞬态）换成**上一段的同场景环境声**
#   · tile ：补丁床用 W 秒瓦片自叠化环铺（干净窗短于 N 时的正解）
#
# ⚠️ 真 crossfade（三角窗叠化）**无法在单段内长度守恒地实现**：ffmpeg acrossfade 的语义是
#    「上一段尾部裁掉 X 秒」——那要改**上一段的文件**，而单段节点只能改本段。
#    所以 crossfade 只能留在组装层，或者改用 patch（等长、零 A/V 位移）。
#    附带收益：patch 不消耗时间轴 ⇒ 组装层 acrossfade「每缝吃 0.25s、
#    使后段音频相对画面整体提前」那个副作用在 patch 路线上不存在。
AUDIO_SEAM_PATCH: float = 0.0    # 头部补丁秒数（0 = 关，逐位直通）
AUDIO_SEAM_TILE: float = 0.0     # 补丁床瓦片秒数（0 = 整窗直取 N 秒）
AUDIO_SEAM_FADE: float = 0.25    # 补丁边界交叉淡变宽度（秒）
#: `declick_transients` 的**范围闸 fallback**（秒）：当调用方拿不到「台词起点」
#: （`_speech_onset_in_head` 返回 0/None）时退回这个上界。
#: 依据 = 产线纪律「前 1.2s 无词」（见 `patch_seconds` 的 tooltip）⇒ 1.2s 是安全上界。
#: 🔴 **绝不能退化成「不限范围」** —— 那会让 declick 扫到台词区（2026-10-03 实测：
#:    onset=0 ⇒ 出 8 处、台词区峰值被削 4.8%）。
AUDIO_DECLICK_FALLBACK_S: float = 1.2
#: 🔴 `declick_gate` 的**裁帧边界保护下界**（秒）：允许动手的范围**至少**要盖过
#: 「裁帧边界 + 这么长」。理由：接缝处那一记孤立瞬态 = 裁帧点切断波形 ⇒ 半波形，**它恒在 `cut` 处**。
#:   而原来的范围闸用「头部首个有声 run」划线，那个 run 在 **pin 区（上一段尾巴）里**
#:   ⇒ 线被画在 `cut` **之前** ⇒ 孤立瞬态**被挡在门外、逐位直通**，报告却只写「未命中」（静默漏治）。
#:   `cut` 之后才是新段内容，「上一段语音在哪」对划这条线没有意义。
#: 取 0.05s：孤立瞬态本体 ≤2ms + 两端淡变 2ms ⇒ 富余两个量级；又短到不会伸进新段首句台词。
#: ⚠️ 若 `cut = 0`（第 1 段 / 未传 `cut_head_frames`）⇒ 本保护**不生效** ⇒ 退回原行为。
AUDIO_DECLICK_CUT_GUARD_S: float = 0.05
#: `declick_gate` 探测「台词起点」的窗长（秒）。要够长才覆盖得到起点
#: （实测本例「孤立瞬态」0.930s、台词 1.33s ⇒ 3.0s 富余）。短窗 ⇒ 起点测不到 ⇒ 退 fallback。
#: 2026-10-04 从 `nodes.py` 的闭包搬来（原为硬写 `3.0 * _sr`，无出处）。
#: ⚠️ **只在 `AUDIO_DECLICK_SCOPE_ALL = False` 时才会被用到**。
AUDIO_DECLICK_PROBE_S: float = 3.0
#: 🔴🔴 **范围闸政策**（2026-10-04 二次修订 —— 当天两份互相矛盾的实测逼出来的）。
#:   `True` ⇒ **不设范围闸**（全段扫），把关交给「**邻域静**」闸。
#:   `False` ⇒ 退回旧政策：`_speech_onset_in_head` 划「台词起点之前」的线，测不到用 fallback。
#:
#: 为什么改（证据链，不是偏好）：
#:   · 旧政策的前提是「孤立瞬态恒在**台词之前**」。2026-10-04 端到端实测**证伪**它：
#:     一段 10s 三人对话里 17 处孤立峰 **15 处落在「前 1.408s」之外**
#:     （成片 @4.43s / @4.56s 正是耳检点名的那两声）⇒ **一律没治**，
#:     而报告只写「范围 = 前 1.408s」⇒ 看起来一切正常（**静默漏治**）。
#:   · 旧政策要防的是「**句首爆破音**被当孤立瞬态削掉」（2026-10-03 实测：不限范围 ⇒ 台词区峰值削 26%）。
#:     ⚠️ 那次实测是在**「邻域静」闸之前**做的。该闸 2026-10-04 才加，而它**恰好**实现同一个目的：
#:     爆破音在句首 ⇒ 右邻是元音（响）⇒ `事件峰 < 邻域` ⇒ **跳过**。
#:     同一段实测：全段 ⇒ 治 11 处 + **跳 6 处**（6 处全是语音/音效，**台词区一个样本没碰**）。
#:   · ⇒ 两条闸目的重合，而「邻域静」按**内容**判、范围闸按**位置**粗筛 ⇒ 后者严格更弱。
#: ⚠️ 「邻域静」闸现在是全段**唯一**的把关 ⇒ 若它被弱化/删除，**本决定必须重新评估**。
#: ⚠️ `AUDIO_DECLICK_FALLBACK_S` / `AUDIO_DECLICK_CUT_GUARD_S` **只在 `False` 时生效**；
#:    保留它们是为了回退只改一个常量、不动代码结构。
AUDIO_DECLICK_SCOPE_ALL: bool = True
#: 🔵 `declick_transients` 的**算法常量**（不是用户旋钮 —— 用户只碰 `ratio` /
#: `quiet_dbfs` / `cut_head_frames` 三个）。**2026-10-04 定**：原先这些是函数参数，
#: 但**没有一个调用方传过** ⇒ 6 个死参数把签名撑到 12 位，读代码的人分不清
#: 「哪些能调、哪些是算法的一部分」。⇒ 收成常量，签名只留会变的。
#: 判据（每条都有实测支撑，改动前先看 `declick_transients` 的 docstring）：
AUDIO_DECLICK_BG_WIN_MS: float = 50.0      # 背景滑窗宽。短窗（5ms）会被事件本身抬高
AUDIO_DECLICK_BG_PCT: float = 20.0          # 背景取窗内 P20 低分位（长窗 + 低分位才拿得到"安静底"）
AUDIO_DECLICK_FADE_MS: float = 2.0          # 事件两端淡变宽度
#: 事件最长（毫秒）= **「多宽算一声」**。🔴 2026-10-04 由 **2.0 放宽到 50.0**：
#:   实测「孤立瞬态」宽 **8ms**（峰 −43 dBFS / 背景 −61 dBFS），旧的 2.0 把它判成「不是一声」**直接丢弃**。
#:   宽度**不是**有效的区分判据（候选越宽越响，想靠它分开孤立瞬态 / 音效会失败），但也不能设得比「一声」还短。
#:   50ms = 人耳仍会把一个瞬态听成「一声」的上限；再长就属于内容。
#:   ⚠️ 真正的把关是 `ratio` 与 `quiet_dbfs` 两个**相对 / 物理量**；本值只是安全阀。
AUDIO_DECLICK_MAX_LEN_MS: float = 50.0
#: 📌 硬上限：超过它的候选**静默丢弃**（只为防病态输入把循环拉爆）。
#:   ⚠️ 已知**静默**失效面：一段里若真有 > 本值 处孤立峰，超出的不会被治，而报告**不提**
#:   （它只统计「已处理的」）。实测真实产物最多 17 处 ⇒ 本包场景够用；
#:   开源素材若远超 ⇒ 需要按「事件数」把它报出来（见 README 局限表）。
AUDIO_DECLICK_MAX_EVENTS: int = 64
#: 🔵 **背景闸的相对放宽倍数**（2026-10-04，**开源兼容**）。
#:   实际生效的闸 = `max(quiet_dbfs 的绝对值, 全段背景 × 本值)` —— 取**较宽**者。
#:   动机：`declick_quiet_dbfs` 是**绝对 dBFS** 阈值，隐含假设「素材底噪 ≈ −50 或更低」。
#:   而开源用户的素材千奇百怪：兼容性探针实测，底噪 **−45 dBFS** 的素材里，
#:   一声**相对突出 3.2×** 的孤立瞬态 **永不命中** ⇒ 整段静默失效，报告只说「未命中」。
#:   改成相对闸后判据变成「**局部背景比全段最静处还静**」，与素材绝对电平**无关**。
#:   取 4.0（+12 dB）：容得下「底噪比基准高一个量级」的素材；语音区**仍被挡住**
#:   （那里的局部背景远高于全段基准），`ratio` 那道闸另兜一层。
#:   ⚠️ 全段基准取 `B` 的 **P20** 而非中位：中位会把「一半语音一半静」的素材抬上去
#:      ⇒ 相对闸过宽。P20 代表「最静的那批格」，才是该比的基准。
AUDIO_DECLICK_QUIET_REL: float = 4.0
#: 🔵 **「邻域算不算静」的倍数判据**（2026-10-04 二次修订）。
#:   跳过判据从「邻域 max **>** 事件峰」改成「**邻域 max × 本值 >** 事件峰」。
#:   动机：旧判据只要求"邻域不更大"，在「**持续噪声 + 音量断崖**」的素材上**必然翻车** ——
#:   断崖处最后一个 1ms 格的峰值 ≈ 素材电平，而左邻（同一段噪声）的 max 也在同一量级
#:   ⇒ 两者谁大**纯看那两窗的随机样本** ⇒ 判据约 50% 概率失效，把「断崖」当孤立峰
#:   **填成背景**（实测夹具 `_bed_a` @1.983s：0.4997 → 0.0489，而那本是从 0.5 掉到 0.05 的分界）。
#:   ⚠️ 这类素材**很常见**（音乐收尾、门声、场景切换处的电平跳变）。
#:   取 2.0：真伪影要求「邻域比自己**低一个量级**」（实测真实孤立瞬态 5.7×、被治的邻域比 20×+），
#:   而断崖（≈1.0×）被挡。⚠️ 调大更保守（少治），调小更激进（可能削内容边缘）。
AUDIO_DECLICK_EDGE_REL: float = 2.0


AUDIO_DECLICK_RATIO: float = 4.0            # 默认倍数（判据：峰值/背景）
AUDIO_DECLICK_QUIET_DBFS: float = -50.0     # 默认背景闸（背景必须低于它才动手）
# 🔴 2026-09-19 真渲染阴性结果后的修法（见 CHANGES「音频缝床声选择」）：
#   旧行为「取床源全局最静窗」在「环境声 + 音乐」素材上会取到**近乎无内容**的窗
#   （实测床声 −42 dBFS vs 缝前 −12.8 dBFS）⇒ 补丁本身就是一段更静的东西，
#   缝上从「凹陷」变成「静音洞」，还把本该有的起拍压平。
AUDIO_SEAM_BED_SELECT: str = "tail"      # tail（默认，新：取床源尾部同长窗，与缝天然连续）
                                         # quiet（0.5.0 旧行为，仅作对照复现）
AUDIO_SEAM_BED_FLOOR_DB: float = 12.0    # quiet 档的选窗下限：只取「≥ 目标 − 该值 dB」的窗（避免取到静默）
AUDIO_SEAM_BED_GAIN_MAX_DB: float = 6.0  # 床声电平对齐上限（±dB）；超出则夹住并报 ⚠
# 🔴 2026-09-21 语音规避（本仓作者 耳检阳性，真渲染）：**tail 窗会撞上一句台词**。
#   实测 expE5（patch=1.2 + jitter=1.2）：床窗 @2.05s×1.2s **完整包住**床源段（stage 0）
#   的台词（该段台词在 3.0–3.2s）⇒ patch 把整句台词搬进本段头部 ⇒ 再叠上缝处 0.25s
#   crossfade 里的床源尾 ⇒ 听感「**对白重叠**」（4.46–5.66s，很短很轻）。
#   故：tail 窗撞语音 ⇒ 退到「**非语音窗里能量最高**」的窗
#   （避开语音，同时避开 771 行那个「最静窗近乎无内容」的坑）。
#   🔴 2026-09-21 二次修正（本仓作者：发现问题就从根本处理）：规避原先只加在 `tile<=0` 分支，
#   而 `tile>0` 恰是产线脚本默认档（`TILE_W=1.2`）⇒ 默认档走的是**没被保护**的那条路。
#   现改为**所有取床源路径共用同一个选窗器** `pick_bed_window`（含 tail / quiet / E5 错开）。
AUDIO_SEAM_BED_VOICED_K: float = 3.0       # 帧 RMS 超全源中位多少倍 ⇒ 该帧算「有声」
AUDIO_SEAM_BED_VOICED_FRAC: float = 0.10   # 窗内「有声帧」占比超此值 ⇒ 判定撞语音
# 🔴 瓦片档（tile>0）的阈值**必须更严**：瓦片会被自叠化环铺 **k 次**（k≈2–3），
#   窗内任何一段语音都会**被听成 k 遍**（实测 tile=1.2 + patch=2.0 ⇒ k=2）。
#   故瓦片窗**不允许出现任何有声帧**（0.0），而不是沿用 10%。
AUDIO_SEAM_BED_TILE_VOICED_FRAC: float = 0.0
# 持续帧滤波（2026-09-21 P0）：单帧瞬态（关门/砰响，<100ms）不计入「有声帧」——
#   人声起振至少持续 2 帧（100ms@32k）；连续台词实测 58% 占比**无损保留**，瞬态归零。
#   主要保护**瓦片档**：那里阈值是 0%，没有它一帧瞬态就会触发换窗。
#   ⚠ 能量判据的既有边界不变：低电平人声（<3× 中位）与「语音当底噪/BGM 型」仍漏检。
AUDIO_SEAM_BED_VOICED_SUSTAIN: int = 2
# 🛡 patch 台词守卫（2026-09-21 P0）：patch 会整段替换**本段**头部 ⇒ 本段自己的台词
#   落在里面就被吞（耳检实锤：「这家店」0.60–1.70s 被 patch=2.0 吃掉，词级时间戳钉死）。
#   守卫 = 探测本段头部台词起点 ⇒ patch 收缩到 onset − MARGIN；onset ≤ MIN ⇒ patch 关闭。
AUDIO_SEAM_PATCH_GUARD_MARGIN_S: float = 0.40   # 收缩后与台词起点保留的安全间隔（覆盖探测滞后：实测能量判据对弱起音滞后 0.30s）
AUDIO_SEAM_PATCH_GUARD_MIN_S: float = 0.10      # 收缩后小于此值 ⇒ patch 整个关闭（0）
#   守卫判据 **宁枉勿纵**：误报（把环境当台词）代价 = patch 变短；漏报代价 = 吞字（本仓作者 耳检抓的）。
#   故探测阈值用 **2×P10**（低于床窗判据的 3×中位）—— 实测把「这家店」的起音低估从
#   0.85s 修正回 0.60s（能量判据对渐强起音天然滞后，margin 再兜一层）。
_AUDIO_META_KEY = "relay_kit_audio_meta"


def _audio_parts(audio: Any) -> Tuple[torch.Tensor, int, Tuple[int, ...], torch.dtype]:
    """把 AUDIO 拆成 ([C,T] float32 视图, sample_rate, 原前导维, 原 dtype)。

    ComfyUI 的 AUDIO 约定 ``{"waveform": [B, C, T], "sample_rate": int}``；
    但 [C,T] / [T] 也要能跑（手搓或第三方节点给的张量形状不一）。
    """
    if not isinstance(audio, dict) or "waveform" not in audio:
        raise TypeError("AUDIO 必须是 {'waveform': 张量, 'sample_rate': int}，得到 %r" % (type(audio),))
    wf = audio["waveform"]
    if not isinstance(wf, torch.Tensor):
        raise TypeError("AUDIO.waveform 必须是张量，得到 %r" % (type(wf),))
    if wf.dim() == 0:
        raise ValueError("AUDIO.waveform 不能是 0 维张量。")
    lead = tuple(int(v) for v in wf.shape[:-2])
    # 🔴 前导维**显式求积**，别用 `-1`（2026-10-04 多维度审核发现）：`-1` 与「长为 0 的维」冲突
    #    ⇒ 空音频 `[1,1,0]` 会抛 `cannot reshape tensor of 0 elements into shape [-1, 1, 0]`。
    #    空音频是**合法输入**（上游可能给空段）⇒ 不该炸整条链，应作为「无内容」往下走
    #    （`declick_transients` 的 `total < 20*w1` 会接住它）。
    _lead_n = 1
    for _v in wf.shape[:-2]:
        _lead_n *= int(_v)
    _ch = int(wf.shape[-2]) if wf.dim() >= 2 else 1
    _t = int(wf.shape[-1])
    flat = wf.reshape(_lead_n, _ch, _t)
    flat = flat[0] if flat.shape[0] else flat.reshape(_ch, 0)     # [C, T]（前导维 0 ⇒ 空）
    # 采样率取整；缺失时按 H3 的 32kHz 兜底（不静默按 1 处理，那会把秒数算成样本数）
    sr = int(audio.get("sample_rate") or 32000)
    if sr <= 0:
        raise ValueError("AUDIO.sample_rate 必须是正数，得到 %r。" % (audio.get("sample_rate"),))
    return flat.float(), sr, lead, wf.dtype


def _match_channels(wf: torch.Tensor, channels: int) -> torch.Tensor:
    """把床源声道数对齐到本段（单声道广播 / 多声道降混 / 取前 N 路）。"""
    c = int(wf.shape[0])
    if c == channels:
        return wf
    if c == 1:
        return wf.expand(channels, -1)
    if channels == 1:
        return wf.mean(dim=0, keepdim=True)
    return wf[:channels]


def _resample_to(wf: torch.Tensor, src_sr: int, dst_sr: int) -> torch.Tensor:
    """线性重采样（床源与本段采样率不一致时才走）。"""
    if src_sr == dst_sr or src_sr <= 0 or dst_sr <= 0:
        return wf
    import torch.nn.functional as F

    n = max(1, int(round(int(wf.shape[-1]) * float(dst_sr) / float(src_sr))))
    return F.interpolate(wf.unsqueeze(0), size=n, mode="linear",
                         align_corners=False).squeeze(0)


def quietest_window(wf: torch.Tensor, n_samples: int,
                    floor: float = 0.0) -> Tuple[int, float]:
    """在 ``[C,T]`` 波形里找**能量最低**的连续 ``n_samples`` 窗。

    返回 ``(起点样本, 窗内 RMS)``。用前缀和一次算完全部窗（O(T)，不是 O(T·n)）。

    **入参形状**：接受 ``[T]`` / ``[C,T]`` / ``[B,C,T]``（同族判据 ``bed_window_voiced_fraction``
    也接受任意 rank，两边**必须一致** —— 2026-09-21 修：此前本函数写死 ``sum(dim=0)``，
    以 3D 调用时会把**批维当时间轴**求和，得到零指向性的崩溃；产线走 ``_audio_parts``（2D）
    故为潜伏缺陷）。

    ``floor``（可选）：只接受 RMS ≥ 该值的窗；**全部不达标才**退回全局最静窗。
    动机（2026-09-19 实测）：全局最静窗在「环境声 + 音乐」素材上是**近乎静默**的一段
    （实测 −42 dBFS，比缝前低 29 dB）⇒ 拿它当床声等于把缝上的洞换个位置。
    """
    total = int(wf.shape[-1])
    n = int(n_samples)
    if n <= 0 or n > total:
        return 0, float("nan")
    ch = max(1, int(wf.numel()) // max(1, total))         # 跨前导维的声道/批数
    p = (wf ** 2).reshape(-1, total).sum(dim=0)           # [T] 逐样本能量（跨声道合计）
    c = torch.cat([p.new_zeros(1), torch.cumsum(p, dim=0)])
    win = c[n:] - c[:-n]                        # [T-n+1] 每窗总能量
    rms = torch.sqrt(win.clamp_min(0.0) / (n * ch))
    if float(floor) > 0.0:
        ok = rms >= float(floor)
        if bool(ok.any()):
            idx = torch.nonzero(ok, as_tuple=False).flatten()
            i = int(idx[int(torch.argmin(rms[idx]))])
            return i, float(rms[i])
    i = int(torch.argmin(rms))
    return i, float(rms[i])


# ------------------------------------------------------------------ 响度归一
# 🔴 2026-10-08 立（反馈「不接声锚也应当响度均衡，且应是默认」+ 实测数据）。
#   背景：模型**跟随锚的响度** —— 接了声锚时 `tools/voice_bank.py::normalize_anchor`
#   把锚对齐到 `TARGET_RMS_DBFS`；**不接锚**时没人管，每段的电平由模型自己决定。
#   实测（同一部片子三段真实素材，有声窗 RMS 中位）：
#     −23.22 / −20.63 / −15.25 dBFS ⇒ **段间差 7.97 dB**，且后两段峰值 1.378（**已削波**）。
#   ⇒ 把**每段音频**对齐到同一个目标：段间台阶消失，顺带治削波。
#   ⚠️ 目标值与统计量**必须与 `tools/voice_bank.py` 逐字一致**（有锚段 / 无锚段要落到同一处，
#      否则只是把一种台阶换成另一种）；两者的一致性由对照断言锁住（`tests/cases/_g35_*`）。
AUDIO_LOUDNESS_TARGET_DBFS: float = -23.0   # 有声窗 RMS 中位目标（**与 voice_bank 同值**）
AUDIO_LOUDNESS_WIN_S: float = 0.05          # 分析窗长（秒）
AUDIO_LOUDNESS_PCT: float = 75.0            # 有声判据：逐窗 RMS 的该百分位
AUDIO_LOUDNESS_TOL_DB: float = 0.5          # 已在目标 ±该值内 ⇒ **不动**（别白改无损素材）
AUDIO_LOUDNESS_MAX_GAIN_DB: float = 12.0    # 抬升上限（静音段不该被硬抬起来）
AUDIO_LOUDNESS_MAX_CUT_DB: float = 18.0     # 衰减上限
AUDIO_LOUDNESS_PEAK_CEIL: float = 0.98      # 峰值护栏：加完增益后不许超过它
AUDIO_LOUDNESS_MIN_VOICED: int = 2          # 有声窗少于该数 ⇒ 判「测不出人声」⇒ 可见降级


def speech_rms_db(x, sr: int, win_s: float = AUDIO_LOUDNESS_WIN_S,
                  pct: float = AUDIO_LOUDNESS_PCT):
    """「人声」响度 = **有声窗 RMS 中位**（dBFS）。返回 ``(db, 有声窗占比)``。

    口径与 `tools/voice_bank.py::speech_rms_db` **逐字一致**：单声道 / 50 ms 窗 /
    阈值 = 全段逐窗 RMS 的 P75 / 取 ≥ 阈值的那些窗的 RMS **中位**。
    **不把静音与环境声算进来** —— 整段 RMS 会被非语音内容带偏。

    测不出（太短 / 几乎全静音）⇒ ``(None, 占比)``：调用方必须走**可见降级**（铁律 15）。
    """
    import numpy as np
    x = np.asarray(x, dtype=np.float32).reshape(-1)
    w = int(win_s * sr)
    n = len(x) // w if w > 0 else 0
    if n < 3:
        return None, 0.0
    rms = np.sqrt((x[:n * w].reshape(n, w) ** 2).mean(axis=1))
    db = 20.0 * np.log10(np.maximum(rms, 1e-9))
    voiced = db >= float(np.percentile(db, pct))
    frac = float(voiced.mean())
    if int(voiced.sum()) < AUDIO_LOUDNESS_MIN_VOICED:
        return None, frac
    return float(np.median(db[voiced])), frac


def normalize_loudness(audio: Any, target_dbfs: float = AUDIO_LOUDNESS_TARGET_DBFS,
                       peak_ceil: float = AUDIO_LOUDNESS_PEAK_CEIL):
    """把一段 AUDIO 的**人声响度**对齐到 ``target_dbfs``；返回 ``(out, 说明)``。

    说明里带实测值 / 增益 / 走了哪条分支 —— 上层**必须**把它打进日志（铁律 15）。
    测不出人声 ⇒ **原样返回 + 说明**，不猜、不动（宁可不动，也不要瞎抬一段静音）。

    只改 ``waveform``，其余键原样带过（`lead` / `dt` 这类边车字段不归它管）。
    """
    import torch
    if audio is None:
        return audio, ""
    wf = audio.get("waveform")
    sr = int(audio.get("sample_rate") or 0)
    if not torch.is_tensor(wf) or wf.numel() == 0 or sr <= 0:
        return audio, "⚠ 音频形状不可用（sr=%s）⇒ 不归一" % sr
    mono = wf
    while mono.dim() > 1:                       # 压到 1 维做测量（与 voice_bank 同口径）
        # stereo ⇒ `(L+R)/2`（标准 downmix）。实测与 `voice_bank` 的**单声道**口径逐位一致
        # （5 组素材差 = 0.000000，见 `tests/cases/_g35_*`）。
        # ⚠️ 已知边界：若 L/R 反相，均值会抵消 ⇒ 测出的响度偏低。真实对白素材不反相；
        #   真要覆盖它得换 `max(|L|,|R|)` 口径，那会**改口径**（与声锚不再同源）⇒ 不做。
        mono = mono.mean(dim=0)
    db, frac = speech_rms_db(mono.detach().float().cpu().numpy(), sr)
    if db is None:
        return audio, "⚠ 人声响度不可测（有声窗占比 %.2f）⇒ **不归一**（原样）" % frac
    gain_db = float(target_dbfs) - db
    if abs(gain_db) <= AUDIO_LOUDNESS_TOL_DB:
        return audio, "响度 %.2f dBFS 已在目标 %.1f ±%.1f dB 内 ⇒ 不动" % (
            db, float(target_dbfs), AUDIO_LOUDNESS_TOL_DB)
    g = max(-AUDIO_LOUDNESS_MAX_CUT_DB, min(AUDIO_LOUDNESS_MAX_GAIN_DB, gain_db))
    pk0 = float(wf.abs().max())
    capped = ""
    # 🔴 峰值护栏**只在抬升时**限制，而且一次算到位 —— 这是"幂等"的必要条件：
    #   旧写法先按目标增益、超护栏再乘一次 ⇒ 结果响度偏离目标（实测 −23.61 而非 −23.00）
    #   ⇒ 下一次调用又会算出 +0.61 dB 再被压回 ⇒ **来回动**。与**声锚**叠加时那就是双重处理
    #   （锚已把上游对齐到同一目标 ⇒ 这里必须落在容忍带内 ⇒ "不动"）。
    #   衰减时峰值必然下降，不需要限制。
    if g > 0 and pk0 > 0:
        room = 20.0 * math.log10(peak_ceil / pk0)
        if room < g:
            g = room
            capped = "｜⚠ 抬升被峰值护栏限到 %+.2f dB" % g
    y = wf * (10.0 ** (g / 20.0))
    pk1 = float(y.abs().max())
    if pk1 > 1.0:
        # 输入本身就已过冲（AAC 解码常见）⇒ 只**报告**不额外压：压了会把响度拉离目标，
        # 与"段间均衡"这个目的直接冲突。要让成片不削波请查上游（生成/落盘的增益）。
        # ⚠️ 这里的 1.0 = **0 dBFS 物理上限**，与参数 `peak_ceil` 不是一回事
        #   （后者是"抬升时留多少余量"的软目标）。
        capped += "｜⚠ 本段峰值 %.3f 超 0 dBFS（输入即过冲）⇒ 不额外压" % pk1
    out = dict(audio)
    out["waveform"] = y
    return out, ("响度 %.2f → %.2f dBFS（增益 %+.2f dB%s）%s"
                 % (db, db + g, g, "，夹在上限" if abs(g - gain_db) > 1e-6 else "", capped))


def _frame_rms(wf: torch.Tensor, hop_samples: int = 1600):
    """整段「逐帧 RMS」+ 帧参数 ⇒ ``(rms[nfr], hop, nfr)``；不足 2 帧返回 ``(None, hop, 0)``。

    为什么单列一个函数（2026-09-21 性能修补）：语音判据是**每候选窗算一次占比**，
    而每个候选窗都要重算一遍整段帧 RMS ⇒ 原实现是 **O(T²/hop)**。床源 4.5s 时无感
    （70 候选 × 112k），但 5 分钟床源就是 6000 × 9.6M ≈ 5×10¹⁰ 次乘加 ——
    节点在主线程上跑，会卡成假死。现在整段**只算一次**，候选遍历是 O(候选数)。
    """
    if not isinstance(wf, torch.Tensor) or wf.numel() == 0:
        return None, max(1, int(hop_samples)), 0
    x = wf
    while x.dim() > 1:                      # 压到 1 维（[1,2,T]→[T]；[C,T]→[T]）
        x = x.mean(dim=0)
    total = int(x.shape[-1])
    hop = max(1, int(hop_samples))
    nfr = total // hop
    if nfr < 2:
        return None, hop, 0
    rms = x[:nfr * hop].reshape(nfr, hop).pow(2).mean(dim=1).clamp_min(0.0).sqrt()
    return rms, hop, nfr


def _voiced_frac_from_frames(rms: torch.Tensor, hop: int, nfr: int, start: int,
                             n_samples: int, k: float,
                             sustain: int = AUDIO_SEAM_BED_VOICED_SUSTAIN) -> float:
    """由**预算好的**帧 RMS 求窗内有声帧占比（判据本体，见 ``bed_window_voiced_fraction``）。

    ``sustain``（持续帧滤波，默认 2 = 100ms@32k）：只有**连续 ≥sustain 帧**超阈值
    才计入「有声帧」—— 人声起振不可能只有一帧；单帧尖峰是瞬态（关门/砰响），
    计入它会让瓦片档（阈值 0%）被一帧瞬态触发换窗。连续台词实测占比无损（58%→58%）。
    """
    base = float(rms.median())
    if base <= 0.0:
        return 0.0
    thr = base * float(k)
    a = max(0, int(start) // hop)
    b = min(nfr, max(a + 1, (int(start) + int(n_samples)) // hop))
    if b <= a:
        return 0.0
    v = (rms[a:b] > thr)
    sus = int(sustain)
    if sus <= 1:
        return float(v.float().mean())
    seg = v.to(torch.int8)
    pad = torch.cat([seg.new_zeros(1), seg, seg.new_zeros(1)])
    d = pad[1:] - pad[:-1]                       # +1=run 起点，-1=run 结束后一位
    lens = (d == -1).nonzero(as_tuple=False).flatten() - \
           (d == 1).nonzero(as_tuple=False).flatten()
    keep = lens[lens >= sus]
    return float(int(keep.sum())) / max(1, int(seg.numel()))


AUDIO_SEAM_PATCH_GUARD_K: float = 2.0
# 🛡 台词守卫·判据档位（0.6.10）。
#
# 🔴 默认 = 0。**为什么不是 2**（2026-09-26 复盘，含一次我自己的口径失误）：
#   多档判据的 acc 0.938 是在 `n_probe = 2.0s`（= 素材全长）口径下标定的，
#   而**真产线 `n_probe = patch 窗长`**（`chain_auto.sh`: `PATCH_N=1.0`；`l1_api` 副路 1.2）。
#   按真口径重测：档 2 在 `patch=1.2` 时 acc 0.562、`patch=1.0` 时 0.625，
#   且 **TP 只有 0~3 / 5**。阈值（12 / 2500 / 0.11）**随窗长漂移，不可迁移**。
#   ⇒ 依据不足，**不能设成默认**。要用请自行标定后显式开启。
#
# 🔴 更根本的边界（实测，见 docs/10 §7.3.1）：**守卫只对「段首有静音垫」的素材有效**。
#   素材若整段有人声/音乐铺底（人声自己就是底噪），中位被抬高 ⇒ 阈值高过人声峰值
#   ⇒ **永不触发**，任何档位都救不了（实锤：`aseamC_s2` / `l1At2_s2`，台词从 0.10s 起，
#   整段最大帧 −4.0 dB，而阈值要 −3.5 dB ⇒ 窗内超阈帧 = 0）。
#   ⇒ **台词保护的主防线是 `patch_seconds ≤ 1.2`（对齐出词侧段首留白纪律），不是守卫。**
#
# 档位语义（`layers`，累积）：
#   0（默认） = 现行单判据：2×全源中位能量 + 持续 ≥2 帧。**= 0.6.9 行为，逐位一致。**
#   1 = 门①：能量阶跃 `step = P95(fdb) − P20(fdb)` ≥ 12 dB（治「安静素材误报」）。
#   2 = 门①+门②：再加「有声帧谱质心 `cent_act` < 2500 Hz」（治高频冲击：碰杯/门响）。
#   3 = 门①+门②+门③：再加「有声帧谱平坦度 `flat_act` < 0.11」（治稳态乐音：钢琴/BGM）。
#
# ⚠️ 档 1~3 的方向是**降低灵敏度**（更不容易判「有台词」）。这与守卫「宁枉勿纵」的
#    设计意图**相反** —— 实测误报代价极低（patch 只要 ≥100ms，静默早在 10ms 内结束），
#    所以**理论正确方向应是「多报」**；但「多报」的判据实测全都不可分（15 类已穷尽）。
#    ⇒ 档 1~3 仅在「确诊为误报、且素材属 A 类（头部有静音垫）」时才值得开。
AUDIO_SEAM_PATCH_GUARD_LAYERS: int = 0
AUDIO_SEAM_PATCH_GUARD_STEP_DB: float = 12.0      # 门①：能量阶跃 ≥ 此值（dB）
AUDIO_SEAM_PATCH_GUARD_CENT_HZ: float = 2500.0    # 门②：有声帧谱质心 < 此值（Hz）
AUDIO_SEAM_PATCH_GUARD_FLAT: float = 0.11         # 门③：有声帧谱平坦度 < 此值（0–1，越小越「有音高」）
AUDIO_SEAM_PATCH_GUARD_WIN: int = 1024            # 三特征共用 STFT 窗长（样本 @32k = 32ms）
AUDIO_SEAM_PATCH_GUARD_HOP: int = 512             # 上述 STFT 的 hop（样本 @32k = 16ms，50% 重叠）
# ⚠️ 上面两行的「样本数」与 `_guard_features` 的 `1.0/32000.0` 频率换算，
#   都以 **H3 默认音频采样率 32000 Hz** 为前提（不是笔误）；H3 若改默认采样率须一并调整。
#   同一前提还贯穿：`_frame_rms(hop=1600)` = 50ms、`_speech_onset_in_head` 返回的样本数单位。


def _guard_features(wf: torch.Tensor, n_probe: int,
                    win: int = AUDIO_SEAM_PATCH_GUARD_WIN,
                    hop: int = AUDIO_SEAM_PATCH_GUARD_HOP) -> Optional[dict]:
    """多档守卫的三个**跨信息域**特征；算不出（音频太短）返回 ``None``。

    | 键 | 量 | 域 | 判的是 | 人声实测 | 干扰源实测 |
    |---|---|---|---|---|---|
    | `step_db` | `P95(fdb) − P20(fdb)` | 能量/时间 | **有没有事件** | 12.1–70.2 | 钢琴 6.5 / 静音 4.8 |
    | `cent_act` | 有声帧能量加权谱质心中位 | 谱形状 | 事件**是不是高频冲击** | 895–2182 Hz | 碰杯 4131–4930 |
    | `flat_act` | 有声帧谱平坦度中位 | 谐噪结构 | 事件**是不是稳态乐音** | 0.029–0.104 | 钢琴/BGM 0.075–0.206 |

    三个量分属**能量 / 谱形状 / 谐噪**三域 —— 这是关键：同一域里堆特征无效
    （实测 `P50+动态` 等同域组合 LOO ≤0.688，跨域组合 LOO 0.875）。

    - `fdb` = 32ms 窗 / 50% 重叠 STFT 的帧 dB。用 `P95−P20` 而非 `max−min`，
      是为了不让**单点瞬态**抬高判据。
    - 「有声帧」= `fdb > P90 − 25dB` 的帧（参与事件的帧，不含事件前的静音垫）。
      ⚠️ 必须用「有声帧」而非全帧：全帧中位会被**人声进场前的静音/底噪**拉低 ——
      实测 `tone_s1` 全帧质心 4098 Hz（看着像高频冲击）而 `cent_act` 1284 Hz（正确识别人声）。
    - `flat_act` = `exp(mean(ln|X|)) / mean(|X|)`，0=纯音，1=白噪。
    """
    x = wf
    while x.dim() > 1:                       # 压到 1 维（与 `_frame_rms` 同口径：先混单声道）
        x = x.mean(dim=0)
    total = min(int(x.shape[-1]), max(0, int(n_probe)))
    w = max(64, int(win))
    h = max(1, int(hop))
    if total < w + h:
        return None
    nf = 1 + (total - w) // h
    idx = torch.arange(w, device=x.device).unsqueeze(0) + \
        h * torch.arange(nf, device=x.device).unsqueeze(1)
    fr = x[idx] * torch.hann_window(w, device=x.device, dtype=x.dtype).unsqueeze(0)
    frms = fr.pow(2).mean(dim=1).clamp_min(0.0).sqrt()
    fdb = 20.0 * torch.log10(frms + 1e-12)
    step_db = float(torch.quantile(fdb, 0.95) - torch.quantile(fdb, 0.20))
    p90 = float(torch.quantile(fdb, 0.90))
    act = fdb > (p90 - 25.0)
    if int(act.sum()) < 3:                   # 全段平坦 ⇒ 没有「事件帧」，退回全帧（保守）
        act = torch.ones_like(fdb, dtype=torch.bool)
    F = torch.fft.rfft(fr, dim=1).abs() + 1e-12
    # ⚠️ 32000 = **H3 的默认音频采样率**（MiniMax-H3 音频 VAE 固定输出 32 kHz），
    #   **不是笔误、也不是写死的 bug**。守卫全程以 32000 为前提：
    #     · 本行的 `1.0/32000.0` 只用于把 FFT 频点换算成 Hz（`cent_act` 的判据线 2500 Hz 同此前提）；
    #     · `_frame_rms` 的 `hop=1600` 也正是「50ms @ 32k」，与返回的「样本数」单位一致。
    #   ⇒ 若将来 H3 改了默认采样率，**这两处必须一起改**（可用 `wf` 的实际采样率替换本行）。
    freqs = torch.fft.rfftfreq(w, 1.0 / 32000.0, device=x.device, dtype=x.dtype)
    cent = (F * freqs.unsqueeze(0)).sum(dim=1) / F.sum(dim=1)
    flat = torch.exp(torch.log(F).mean(dim=1)) / F.mean(dim=1)
    return {
        "step_db": step_db,
        "cent_act": float(torch.median(cent[act])),
        "flat_act": float(torch.median(flat[act])),
    }


def _guard_layers_pass(feats: Optional[dict], layers: int) -> Tuple[bool, str]:
    """按档位跑前置门 ⇒ ``(是否可能有人声, 未通过时的人类可读原因)``。

    层与档位是**累积**关系：档 2 必须同时过门①与门②。
    """
    lay = max(0, min(3, int(layers)))
    if lay <= 0:
        return True, ""
    if feats is None:
        return False, "音频太短（不足一个分析窗）"
    if feats["step_db"] < float(AUDIO_SEAM_PATCH_GUARD_STEP_DB):
        return False, ("能量阶跃 %.1fdB < %.1fdB（头部无「事件」）"
                       % (feats["step_db"], AUDIO_SEAM_PATCH_GUARD_STEP_DB))
    if lay >= 2 and feats["cent_act"] >= float(AUDIO_SEAM_PATCH_GUARD_CENT_HZ):
        return False, ("有声帧谱质心 %.0fHz ≥ %.0fHz（事件是高频宽带冲击，非人声）"
                       % (feats["cent_act"], AUDIO_SEAM_PATCH_GUARD_CENT_HZ))
    if lay >= 3 and feats["flat_act"] >= float(AUDIO_SEAM_PATCH_GUARD_FLAT):
        return False, ("有声帧谱平坦度 %.3f ≥ %.3f（事件是稳态乐音，非人声）"
                       % (feats["flat_act"], AUDIO_SEAM_PATCH_GUARD_FLAT))
    return True, ""


def _speech_onset_in_head(wf: torch.Tensor, n_probe: int,
                          hop_samples: int = 1600,
                          k: float = AUDIO_SEAM_PATCH_GUARD_K,
                          sustain: int = AUDIO_SEAM_BED_VOICED_SUSTAIN,
                          layers: int = AUDIO_SEAM_PATCH_GUARD_LAYERS,
                          ) -> Optional[int]:
    """目标段**头部 [0, n_probe)** 里首个「持续 ≥sustain 帧」有声 run 的起点（样本）；无 ⇒ None。

    与床窗判据（`_voiced_frac_from_frames`）同机件同基线（**全源中位**），阈值松一档（2× vs 3×，
    宁枉勿纵：误报 = patch 变短；漏报 = 吞字）。
    🔴 2026-09-21 压力矩阵（S2-2/S2-5）打回过一版 **P10 基线**：连续 BGM 素材上 P10 = BGM
    谷底 ⇒ 2×谷底低于 BGM 乐句峰 ⇒ **BGM 被当台词**，守卫误触发、patch 被过度收缩
    （「头部干净 ⇒ 零副作用」承诺在 BGM 素材上被打破）。中位的失效域只有「语音占 >50% 帧」
    —— 那是「语音当底噪」既知边界，且那种素材 patch 本来就无意义。BGM 乐句峰（±3dB 起伏）
    < 2×中位 ⇒ 不触发；台词通常 +14dB 以上 ⇒ 稳触发。

    🛡 ``layers``（0.6.10，**默认 0**）：多档前置判据，见常量区 `AUDIO_SEAM_PATCH_GUARD_LAYERS`。
    0 = 0.6.9 行为（逐位一致）；1/2/3 = 追加「能量阶跃」「谱质心」「谱平坦度」门。
    **判据不过 ⇒ 直接返回 None（= 头部无台词）** ⇒ patch 不做避让，等同现行「头部干净」路径。
    ⚠️ 非零档在**通过**判据后**仍走原来的 2×中位逻辑**找 run 起点 —— 前置门只管「有没有」，
    不管「在哪」，因此它只可能**减少**误报，不会**改变**已触发时的收缩位置。
    🔴 **为什么默认是 0 而不是某个正档**：多档判据的准确率是在 `n_probe` = 素材全长（2.0s）
    下标定的，而**真产线 `n_probe` = patch 窗长**（`chain_auto.sh`: `PATCH_N=1.0`），
    阈值随窗长漂移 ⇒ 正档在真口径下 TP 掉到 0~3/5。详见常量区与 docs/10 §7.3.1。
    """
    # ---- 多档前置门 ----
    _lay = max(0, min(3, int(layers)))
    if _lay > 0:
        _ok, _why = _guard_layers_pass(_guard_features(wf, n_probe), _lay)
        if not _ok:
            return None
    rms, hop, nfr = _frame_rms(wf, hop_samples)
    if rms is None:
        return None
    base = float(rms.median())
    if base <= 0.0:
        return None
    thr = base * float(k)
    sus = max(1, int(sustain))
    nframes = min(nfr, max(1, (int(n_probe) + hop - 1) // hop))
    v = (rms[:nframes] > thr).to(torch.int8)
    pad = torch.cat([v.new_zeros(1), v, v.new_zeros(1)])
    d = pad[1:] - pad[:-1]                       # +1=run 起点，-1=run 结束后一位
    starts = (d == 1).nonzero(as_tuple=False).flatten().tolist()
    ends = (d == -1).nonzero(as_tuple=False).flatten().tolist()
    for s, e in zip(starts, ends, strict=False):
        if e - s >= sus:
            return int(s * hop)
    return None


def bed_window_voiced_fraction(wf: torch.Tensor, start: int, n_samples: int,
                               hop_samples: int = 1600,
                               k: float = AUDIO_SEAM_BED_VOICED_K,
                               sustain: int = AUDIO_SEAM_BED_VOICED_SUSTAIN) -> float:
    """窗内「**有声帧**」占比（0–1）——用来判一个床窗是否撞上了语音。

    做法：把床源切成 ``hop``（默认 50ms@32k）帧，算逐帧 RMS，取其**中位数当环境基线**；
    帧 RMS 超过 ``k`` × 基线**且连续 ≥ ``sustain`` 帧**（人声起振 ≥100ms，滤掉单帧瞬态）
    即判「有声」。对白/口播是**间歇性高能**，稳态环境声不是
    ⇒ 这个占比能把两者分开（实测：段1 台词落在 3.0–4.0s，尾部窗占比高而全源中位段为 0）。

    ``k`` 取值动机：雨声/room tone 的帧间起伏通常 <2×；语音相对环境通常 >6 dB（≥2×）
    ⇒ 3× 落在一个两侧都不敏感的位置。**这是判据不是定律，可随素材调整。**

    ⚠ 需要**逐窗多次调用**时（候选遍历），请用 ``_frame_rms`` + ``_voiced_frac_from_frames``
    复用帧 RMS —— 本函数每次调用都会重算整段（单次调用无妨，循环调用是 O(T²)）。
    """
    rms, hop, nfr = _frame_rms(wf, hop_samples)
    if rms is None:
        return 0.0
    return _voiced_frac_from_frames(rms, hop, nfr, start, n_samples, k, sustain)


def nonvoiced_candidates(wf: torch.Tensor, n_samples: int,
                         hop_samples: int = 1600,
                         frac: float = AUDIO_SEAM_BED_VOICED_FRAC) -> list:
    """按位置升序列出所有「不撞语音」的候选窗起点（样本）。空列表 = 全撞语音。

    O(候选数) 而非 O(T²)：整段帧 RMS 只算一次（见 ``_frame_rms``）。
    """
    rms, hop, nfr = _frame_rms(wf, hop_samples)
    if rms is None:
        return []
    total = int(wf.shape[-1])
    n = int(min(n_samples, total))
    if n <= 0:
        return []
    out = []
    s = 0
    while s + n <= total:
        if _voiced_frac_from_frames(rms, hop, nfr, s, n, AUDIO_SEAM_BED_VOICED_K) <= float(frac):
            out.append(s)
        s += hop
    return out


def find_nonvoiced_bed_window(wf: torch.Tensor, n_samples: int,
                              hop_samples: int = 1600,
                              frac: float = AUDIO_SEAM_BED_VOICED_FRAC,
                              prefer: Optional[int] = None
                              ) -> Tuple[int, float]:
    """在所有「不撞语音」的候选窗里挑一个 ⇒ ``(起点, 占比)``。

    两种挑法（由 ``prefer`` 决定）：
      · ``prefer=None`` ⇒ 取**能量最高**的（给 tail 用）。理由见 771 行 ——
        最静窗在「环境声+音乐」素材上近乎无内容，会把缝上的洞换个位置。
        **避开语音**与**避开静音洞**要同时满足，交集就是「非语音窗里最饱满的那个」。
      · ``prefer=<样本>`` ⇒ 取**离该点最近**的（给 E5 的 jitter 用）。

    全部候选都撞语音 ⇒ 返回 ``(-1, 1.0)``（由调用方决定退路，不在这里静默兜底）。

    ⚠ **策略层入口是 ``pick_bed_window``** —— 它才是「首选窗 + 规避 + 退路」的**唯一**实现；
    本函数只负责「在候选里按一种挑法取一个」。取床源的新代码请走 ``pick_bed_window``。
    """
    total = int(wf.shape[-1])
    n = int(min(n_samples, total))
    hop = max(1, int(hop_samples))
    cands = nonvoiced_candidates(wf, n, hop, frac)
    if not cands:
        return -1, 1.0
    if prefer is not None:
        return min(cands, key=lambda c: abs(c - int(prefer))), 0.0
    best, best_rms = cands[0], -1.0
    for c in cands:
        r = float(wf[..., c:c + n].pow(2).mean().sqrt())
        if r > best_rms:
            best, best_rms = c, r
    return best, 0.0


def bed_source_window_len(total: int, n_samples: int, tile_samples: int) -> int:
    """取床源时**实际采样**的窗长（样本）：瓦片档 = 瓦片长，否则 = 补丁长。

    为什么要有这个函数：窗长同时决定 ① 选窗器该看多长的一段、② report 按哪个窗算语音占比。
    两处必须**同源** —— 否则会出现「报告说安全、实际取的那段撞了语音」（2026-09-21 立）。
    """
    total = int(max(0, int(total)))
    W = int(tile_samples)
    if W > 0:
        return int(min(W, total))
    return int(min(int(n_samples), total))


def pick_bed_window(wf: torch.Tensor, n_samples: int,
                    select: str = AUDIO_SEAM_BED_SELECT,
                    prefer: Optional[int] = None,
                    floor: float = 0.0,
                    frac: float = AUDIO_SEAM_BED_VOICED_FRAC
                    ) -> Tuple[int, bool, float, float]:
    """**统一的床窗选取** —— 所有「从床源取窗」的路径都必须走它。

    返回 ``(起点样本, 是否因撞语音换过窗, 首选窗语音占比, 最终窗语音占比)``。
    （两个占比都要给：report 要能说清「首选窗撞了 X% ⇒ 已换到 Y 窗」，只报最终值等于隐瞒原因。）

    首选窗（按语义）：
      · ``prefer`` 给定（E5 按段号错开）⇒ 从该点起；
      · ``select="tail"``（默认）⇒ 床源**尾部同长窗**（与缝天然连续）；
      · 其它（``quiet``，0.5.0 旧口径）⇒ ``floor`` 限定的最静窗。

    首选窗**撞语音**（占比 > ``frac``）⇒ 换到非语音候选：
      · ``prefer`` ⇒ 起点映射成候选序号 ``(start // hop) % len(cands)`` —— **错开语义必须保留**，
        若改成"取最饱满"或"取最静"，各段会退到同一个窗，E5 就没了靶（第一版踩过）；
      · ``tail`` ⇒ 非语音候选里**能量最高**（避开语音，同时避开「最静窗近乎无内容」，见 771 行）；
      · ``quiet`` ⇒ 非语音候选里**最静**（保持 quiet 的语义）。
    **全部候选都撞语音** ⇒ 退回首选窗、``changed=False`` —— 调用方在 report 里显式告警，
    **不 raise**（「素材本身以语音为底噪」是合法素材，raise 会把可用素材判死）。
    """
    total = int(wf.shape[-1])
    n = int(min(int(n_samples), total))
    if n <= 0:
        raise ValueError("床声长度必须为正，得到 %d。" % int(n_samples))
    hop = 1600                                   # 候选步长（50ms@32k），与既有实现一致
    if prefer is not None:
        start = int(max(0, min(int(prefer), max(0, total - n))))
    elif str(select) == "tail":
        start = max(0, total - n)
    else:
        start, _ = quietest_window(wf, n, floor=floor)
    _rms, _hop, _nfr = _frame_rms(wf, hop)
    def _vf(s):                                  # 帧 RMS 复用 ⇒ 不做 O(T²) 重算
        if _rms is None:
            return 0.0
        return float(_voiced_frac_from_frames(_rms, _hop, _nfr, int(s), n,
                                               AUDIO_SEAM_BED_VOICED_K))
    vf_first = _vf(start)
    if vf_first <= float(frac):
        return int(start), False, float(vf_first), float(vf_first)
    cands = nonvoiced_candidates(wf, n, hop, frac)
    if not cands:
        # 无解：**不静默兜底**。tail 档退回「最静窗」（= 语音残留最少的那段，旧行为），
        # 其余档保持首选窗；两条都由调用方在 report 里带上 ⚠（占比 > 阈值）。
        if prefer is None and str(select) == "tail":
            alt, _ = quietest_window(wf, n, floor=floor)
            return int(alt), int(alt) != int(start), float(vf_first), _vf(alt)
        return int(start), False, float(vf_first), float(vf_first)
    if prefer is not None:
        # 错开语义保留：不同 prefer ⇒ 不同候选序号 ⇒ 各段窗仍互不相同。
        alt = cands[(int(start) // hop) % len(cands)]
    else:
        # 候选窗能量：前缀和一次算全（O(候选数)）。
        # 🔴 2026-09-21 性能修补：原先逐候选切窗重算 O(n) ⇒ 全静音素材上候选数 ~T/hop，
        #    退化为 O(T²/hop) —— 与帧 RMS 那次（`_frame_rms`）同类，5 分钟床源会卡死主线程。
        _ct = torch.tensor(cands, dtype=torch.long)
        _ch = max(1, int(wf.numel()) // max(1, total))
        _p = (wf ** 2).reshape(-1, total).sum(dim=0)
        _c = torch.cat([_p.new_zeros(1), torch.cumsum(_p, dim=0)])
        _wr = torch.sqrt((_c[_ct + n] - _c[_ct]).clamp_min(0.0) / (n * _ch))
        if str(select) == "tail":
            alt = int(_ct[int(torch.argmax(_wr))])       # 非语音候选里能量最高
        else:
            alt = int(_ct[int(torch.argmin(_wr))])       # quiet：保持「最静」语义
    return int(alt), True, float(vf_first), _vf(alt)


def _rms_db(x: torch.Tensor) -> float:
    return 20.0 * math.log10(max(float(x.pow(2).mean().sqrt()), 1e-9))


def target_level(wf: torch.Tensor, probe_samples: int = 6400,
                 hop_samples: int = 640) -> float:
    """补丁要对齐的**目标电平**：尾部 ``probe_samples`` 窗内「20ms 子窗 RMS 的**中位数**」。

    为什么不是单窗 RMS（2026-09-19，BGM 适配性反思）：
      · 环境声（雨声/room tone）是 stationary ⇒ 单窗 RMS 稳定；
      · **BGM/音乐是非 stationary**：一个 200ms 窗可能正好落在**弱拍/换气/衰减**上，
        单窗 RMS 会比真实伴奏电平低 6–10 dB ⇒ 拿它当目标会把补丁整体压低，制造人为凹陷。
      取「20ms 子窗 RMS 的中位数」对乐句内的强弱起伏稳健，两种素材都适用。

    取尾部的理由：补丁区紧跟在「上一段末尾」之后，要延续的是**它**的电平。
    节点优先把上一段音频传进来（``target_audio``），拿不到才退回床源自身尾部。
    """
    total = int(wf.shape[-1])
    k = max(1, min(int(probe_samples), total))
    seg = wf[..., total - k:]
    hop = max(1, min(int(hop_samples), k))
    n_sub = max(1, k // hop)
    sub = seg[..., :n_sub * hop].reshape(*seg.shape[:-1], n_sub, hop)
    rms = sub.pow(2).mean(dim=-1).clamp_min(0.0).sqrt()      # [..., n_sub]
    return float(rms.reshape(-1).median())


def build_bed(wf: torch.Tensor, n_samples: int, tile_samples: int,
              fade_samples: int, select: str = AUDIO_SEAM_BED_SELECT,
              floor: float = 0.0,
              start_override: Optional[int] = None) -> Tuple[torch.Tensor, int, float]:
    """从床源波形取 ``n_samples`` 长的床声；``tile_samples>0`` 时按瓦片自叠化环铺。

    ``select``：
      · ``tail``（**默认**）取**尾部同长窗**——紧邻缝，电平与音色与缝前天然连续；
      · ``quiet`` 取（可带下限的）最静窗——0.5.0 旧行为，**仅作对照**。

    返回 ``(床波形 [C,n], 起点样本, 原始床声 RMS)``。**不做电平处理**——
    对齐与峰值护栏都在 ``audio_seam_patch`` 里一步做完（少一层处理 = 少一层累计误差）。
    """
    total = int(wf.shape[-1])
    n = int(n_samples)
    if n <= 0:
        raise ValueError("床声长度必须为正，得到 %d。" % n)
    if tile_samples <= 0:
        # 单窗档：tail / quiet / E5 错开，全部交给**同一个选窗器**（含语音规避）。
        start, _, _, _ = pick_bed_window(wf, n, select=select, prefer=start_override, floor=floor)
        bed = wf[..., start:start + n]
    else:
        W, X = int(tile_samples), int(fade_samples)
        if X <= 0 or X >= W:
            raise ValueError(
                "瓦片环铺要求 0 < 边界淡变 %.3fs < 瓦片长 %.3fs。\n"
                "    瓦片太短会退化成「同一段噪声原样重复」，反而听得出来。" % (X / 32000.0, W / 32000.0)
            )
        # 🔴 2026-09-21 修补（原缺口）：瓦片**也**必须过语音检查，且阈值更严 ——
        #   瓦片会被自叠化环铺 k 次（k≈2–3），窗内任何一段语音都会被听成 k 遍。
        #   旧代码只保护了 tile<=0 分支，而 tile>0 恰是产线脚本默认档（TILE_W=1.2）。
        win = bed_source_window_len(total, n, W)
        start, _, _, _ = pick_bed_window(wf, win, select=select, prefer=start_override,
                                         floor=floor, frac=AUDIO_SEAM_BED_TILE_VOICED_FRAC)
        tile = wf[..., start:start + W]
        k = max(2, int(math.ceil((n - X) / float(W - X) - 1e-9)))
        w = torch.linspace(0.0, 1.0, X, device=wf.device, dtype=torch.float32)
        acc = tile
        for _ in range(k - 1):
            blend = acc[..., -X:] * (1.0 - w) + tile[..., :X] * w
            acc = torch.cat([acc[..., :-X], blend, tile[..., X:]], dim=-1)
        bed = acc[..., :n]
    if int(bed.shape[-1]) < n:                  # 床源比 N 还短：循环凑够（防御，不炸）
        reps = int(math.ceil(n / max(1, int(bed.shape[-1]))))
        # 任意 rank 通用（旧写法 ``repeat(1, reps)`` 只对 2D 成立）
        bed = bed.repeat(*([1] * (bed.dim() - 1)), reps)[..., :n]
    return bed, start, float(bed.pow(2).mean().sqrt())


def audio_seam_patch(audio: Any, bed_audio: Any, patch: float = AUDIO_SEAM_PATCH,
                     tile: float = AUDIO_SEAM_TILE,
                     fade: float = AUDIO_SEAM_FADE,
                     select: str = AUDIO_SEAM_BED_SELECT,
                     target_audio: Any = None,
                     gain_max_db: float = AUDIO_SEAM_BED_GAIN_MAX_DB,
                     stage_index: int = 0,
                     patch_guard: bool = True,
                     patch_guard_layers: int = AUDIO_SEAM_PATCH_GUARD_LAYERS) -> Tuple[Any, str]:
    """把本段头部 ``patch`` 秒换成 ``bed_audio``（上一段）里的床声窗；**长度守恒**。

    替换区 ``[0, N-X)`` 纯床声，``[N-X, N)`` 是床声 → 本段自身音频的交叉淡变
    （X = ``fade``），``N`` 之后**逐位不动**。返回 ``(audio, report)``。

    🔴 2026-09-19 修正（真渲染阴性结果驱动）：床声窗**默认取尾部窗**（``select="tail"``），
    并把床声**电平对齐**到「缝前电平」（``target_audio`` 的尾部；没给就用床源自身尾部），
    增益限幅 ``±gain_max_db``。旧行为 ``select="quiet"``（全局最静窗）会取到近乎静默的一段
    ⇒ 实测把缝上的凹陷换成静音洞，仅保留作对照。
    """
    if patch is None or float(patch) <= 0:
        return audio, ""
    if bed_audio is None:
        return audio, ""
    wf, sr, lead, dtype = _audio_parts(audio)
    total = int(wf.shape[-1])
    ch = int(wf.shape[0])
    n = int(round(float(patch) * sr))
    X = max(0, int(round(float(fade) * sr)))
    warn = ""
    if n >= total:                              # 补丁比整段还长：夹住并显著告警（不炸整条链）
        n = max(1, total - 1)
        X = min(X, n)
        warn = "｜ ⚠ 补丁超出段长，已夹到 %.3fs" % (n / float(sr))
    if X >= n:                                  # 边界淡变不能吃掉整个替换区
        X = max(0, n - 1)
    # 🛡 patch 台词守卫（0.6.5，默认开）：见常量区注释。检不出台词 ⇒ 逐位走旧行为。
    #    守卫**判据档位** `patch_guard_layers` 默认 0 = 0.6.9 行为（多档判据见常量区注释）。
    guard_note = ""
    if patch_guard:
        _onset = _speech_onset_in_head(wf, n, layers=patch_guard_layers)
        if _onset is not None:
            _margin = int(round(AUDIO_SEAM_PATCH_GUARD_MARGIN_S * sr))
            _safe = _onset - _margin
            if _safe < int(round(AUDIO_SEAM_PATCH_GUARD_MIN_S * sr)):
                return audio, ("[H3 Relay] 音频缝：🛡 patch 台词守卫 —— 本段头部台词 @%.2fs 太靠前\n"
                               "           （< 最小保护窗 %.2fs）⇒ patch 自动关闭（0），本段头部原样保留。\n"
                               "           缝处平滑交回「Trim AV」与出词侧段首留白纪律（docs/06）。"
                               % (_onset / float(sr), AUDIO_SEAM_PATCH_GUARD_MIN_S))
            guard_note = ("｜ 🛡 patch 台词守卫：本段头部台词 @%.2fs ⇒ patch 自动 %.2f→%.2fs（避让台词）"
                          % (_onset / float(sr), float(n) / sr, _safe / float(sr)))
            n = _safe
            if X >= n:
                X = max(0, n - 1)
    bed_wf, bed_sr, _, _ = _audio_parts(bed_audio)
    bed_wf = _match_channels(_resample_to(bed_wf, bed_sr, sr), ch)
    tgt_wf = bed_wf
    if target_audio is not None:
        _t, _tsr, _, _ = _audio_parts(target_audio)
        tgt_wf = _match_channels(_resample_to(_t, _tsr, sr), ch)
    tgt = target_level(tgt_wf, int(round(0.2 * sr)))
    floor = tgt * (10.0 ** (-AUDIO_SEAM_BED_FLOOR_DB / 20.0))
    _tile_n = int(round(float(tile) * sr))
    _bed_total = int(bed_wf.shape[-1])
    _win = bed_source_window_len(_bed_total, n, _tile_n)
    _frac = (AUDIO_SEAM_BED_TILE_VOICED_FRAC if _tile_n > 0
             else AUDIO_SEAM_BED_VOICED_FRAC)
    start, _changed, _vf_first, _vf_final = pick_bed_window(
        bed_wf, _win, select=select, floor=floor, frac=_frac)
    bed, start, bed_rms = build_bed(bed_wf, n, _tile_n, X,
                                    select=select, floor=floor,
                                    start_override=start)
    bed_db = _rms_db(bed)
    # 电平对齐 + 峰值护栏，**一步做完**（不额外加处理层：少一层 = 少一层累计误差）
    g_db, clamped = 0.0, False
    if bed_rms > 0.0 and tgt > 0.0:
        _g = 20.0 * math.log10(tgt / bed_rms)
        _used = max(-float(gain_max_db), min(float(gain_max_db), _g))
        _peak = float(bed.abs().max()) if int(bed.numel()) else 0.0
        if _peak > 0.0:                       # 尾部窗本来就响，+dB 会推过 1.0 ⇒ 削波
            _head = 20.0 * math.log10(0.995 / _peak)
            clamped = clamped or _head < _used
            _used = min(_used, _head)
        clamped = clamped or abs(_g) > float(gain_max_db)
        if _used != 0.0:
            bed = bed * (10.0 ** (_used / 20.0))
        g_db = _used

    out = wf.clone()
    keep = n - X                                # [0, keep) 纯床声
    if keep > 0:
        out[..., :keep] = bed[..., :keep]
    if n > keep:                                # [keep, n) 床声 → 本段自身
        w = torch.linspace(0.0, 1.0, n - keep, device=wf.device, dtype=torch.float32)
        out[..., keep:n] = bed[..., keep:n] * (1.0 - w) + wf[..., keep:n] * w

    tile_note = "，%.2fs 瓦片自叠化环铺" % float(tile) if float(tile) > 0 else ""
    mode_note = "尾部窗（与缝天然连续）" if str(select) == "tail" else "最静窗（旧口径·仅对照）"
    if _changed and str(select) == "tail":
        # 🔴 2026-09-21 措辞诚实化：已换窗就别再自称「尾部窗」—— 实测首行曾打出
        #   「尾部窗…@0.80s」（0.80 并非尾部），靠后续 🎙 行才能纠正 = 前后矛盾。
        mode_note = "非语音窗（首选尾窗撞语音，已避开）"
    # 🔴 2026-09-21：报出**实际取样窗**的语音占比，并对「已换窗」「仍撞语音」显式标注 ——
    #   不静默：撞语音必须可见（否则会像 expE5 那样只能靠人耳发现重叠）。
    #   瓦片档按**瓦片窗**（_win）算，与 ① 选窗器看的窗 ② 实际取样 三者同源。
    _vf = _vf_final
    voice_note = "｜ 🎙 取样窗内有声帧占比 %.0f%%（判据阈值 %.0f%%）" % (
        100.0 * _vf, 100.0 * _frac)
    if _vf > _frac:
        voice_note += (" ⚠ **撞语音**（全源无非语音窗可选）：该窗含对白 ⇒ 开头 %.2fs 会把它"
                       "带进来，可能听到重叠" % float(patch))
    elif _changed:
        voice_note += ("；首选窗原撞语音（占比 %.0f%%）⇒ 已换到非语音窗 @%.2fs"
                       % (100.0 * _vf_first, start / float(sr)))
    warn = warn + guard_note
    rep = ("[H3 Relay] 音频缝：头部补丁 %.2fs ← 床源%s @%.2fs%s，边界交叉淡变 %.2fs\n"
           "           床声电平 %.1f dBFS → 对齐目标 %.1f dBFS（%s %+.1f dB%s）\n"
           "           音频 %d → %d 采样点（长度守恒，零 A/V 位移）%s%s"
           % (float(patch), mode_note, start / float(sr), tile_note, X / float(sr),
              bed_db, 20.0 * math.log10(max(tgt, 1e-9)),
              "增益" + ("已夹住" if clamped else ""), g_db,
              "⚠ 超出限幅，建议换床源段" if clamped else "",
              total, int(out.shape[-1]), warn, voice_note))
    shaped = out.reshape(*lead, ch, total) if lead else out
    return {"waveform": shaped.to(dtype), "sample_rate": sr}, rep


def _declick_envelope(x: Any, w1: int, n1: int) -> Tuple[Any, Any, int]:
    """① 1ms **最大值**包络 `e` + ② `AUDIO_DECLICK_BG_WIN_MS` 长窗**低分位**底噪 `B`。

    🔴 包络用 `max` 不用 `mean` —— 单样本尖峰会被 mean 抹平（本函数要治的正是 1~2ms 尖峰）。
    🔴 底噪**必须是「长窗 + 低分位」**：初版用 **5ms 窗的中位** ⇒ 窗里只有 5 个 1ms 样本，
       而「孤立瞬态」自己占了 3 个 ⇒ **B 被事件本身抬高**（实测 B=0.00336 而真实底噪 ~0.0015）
       ⇒ ratio 只有 2.84，**一个都没抓到**（2026-10-03 自测当场抓到）。
       长窗（50ms）+ 低分位（P20）才能取到「安静底」而不是「被事件污染的中位」。

    返回 `(e, B, half)`（`half` 给裁帧边界的邻域重算用）。
    """
    import numpy as np
    from numpy.lib.stride_tricks import sliding_window_view

    m = (x.mean(dim=0) if int(x.shape[0]) > 1 else x[0]).abs().detach().cpu().numpy()
    e = m[:n1 * w1].reshape(n1, w1).max(axis=1)
    half = max(1, int(round(AUDIO_DECLICK_BG_WIN_MS / 2.0)))
    B = np.percentile(sliding_window_view(np.pad(e, (half, half), mode="edge"),
                                          2 * half + 1), AUDIO_DECLICK_BG_PCT, axis=-1)
    return e, B, half


def _declick_bg_at_cut(B: Any, e: Any, n1: int, w1: int, half: int,
                       cut_head_n: Any) -> int:
    """④b 把**裁帧边界**当「新文件头」重算邻域背景（原地改 `B`）；返回边界样本数 `cut`。

    🔴 为什么必须做：`#931` 工作在**未裁**视图，而**产物是裁后的** ⇒ 「裁出来的孤立峰」在
       `#931` 眼里**不孤立**（前面还有 pin 的语音 ⇒ 背景不静 ⇒ 不命中），但在成片里它前面
       什么都没有 ⇒ 听感就是「无缘无故的孤立瞬态」。
       （2026-10-03 实测：节点内坐标 = 产物坐标 + 0.208s = `context_frames/fps`，精确吻合。）
    做法：在边界处把背景窗**截断**（只往右看），等价于「认为边界左侧不存在」。
    """
    import numpy as np

    cut = max(0, int(cut_head_n or 0))
    cut_idx = cut // w1
    if cut_idx > 0:
        for i in range(max(0, cut_idx - half), min(n1, cut_idx + half + 1)):
            a = max(cut_idx, i - half)
            b = min(n1, i + half + 1)
            if b > a:
                B[i] = float(np.percentile(e[a:b], AUDIO_DECLICK_BG_PCT))
    return cut


def _declick_fade_in_at_cut(x: Any, total: int, cut: int, sr: int) -> Tuple[Any, bool]:
    """④c 裁帧边界处的**短淡入**（治段首那个「孤立瞬态」的正解）。返回 `(x, 是否做了)`。

    🔴 链条：`#931` 工作在**未裁**音频上，`Trim AV` 在它下游裁掉 `context_frames` 帧
       ⇒ **裁帧点落在波形中间** ⇒ 新开头是个「半波形」⇒ 在成片里它前面什么都没有
       ⇒ 听感就是「无缘无故的孤立瞬态」（本仓作者 耳检定性为伪影 ✓）。
       ⚠️ **不是模型生成的** —— 产物本身已是裁后的（我最初判断成「模型生成」是错的）。
    🔴 **必须 `clone` 后再写**：`x` 可能**与输入张量共享存储**
       （`wf.detach().to(torch.float32)` 在 dtype 已匹配时**不复制**）⇒ 原地写会改掉调用方的输入。
    """
    if cut <= 0 or cut >= total:
        return x, False
    n = min(max(1, int(round(AUDIO_DECLICK_FADE_MS * sr / 1000.0))), total - cut)
    ramp = torch.linspace(0.0, 1.0, n, device=x.device, dtype=torch.float32)
    x = x.clone()
    x[..., cut:cut + n] = x[..., cut:cut + n] * ramp
    return x, True


def _declick_find_events(cand: Any, n1: int, w1: int, sr: int,
                         max_len_ms: float) -> Tuple[List[Tuple[int, int]], int]:
    """④ 合并相邻候选成事件；长于 `max_len_ms` 的丢弃（太长就不是「一声」）。

    返回 ``(events, dropped)``：`dropped` = **因超长被丢弃的段数**。
    🔴 为什么要报它：`ratio` 调得**很小**时，候选会连成**一大片**⇒ 合并成超长段 ⇒ **全被丢** ⇒
       一个都不治。此时旧行为只在报告里写「未命中」，用户**无从知道**是自己把 ratio 调坏了
       （2026-10-04 审核实测：`ratio=0.001` ⇒ 命中 0 处，报告却不提任何原因）。⇒ 铁律 15。
    """
    events, dropped, i = [], 0, 0
    while i < n1:
        if cand[i]:
            j = i
            while j + 1 < n1 and cand[j + 1]:
                j += 1
            # 🔴 **格数 → 毫秒**：`max_len_ms` 是毫秒，而这里数的是 **1ms 格**
            #    （`w1 = 0.001*sr`）。直接拿格数与毫秒比（旧写法）在 `sr < 1000` 或将来改包络
            #    粒度时会**静默错位**（判据变松/变紧都不报错）。
            if (j - i + 1) * (w1 / float(sr) * 1000.0) <= float(max_len_ms):
                events.append((i, j))
            else:
                dropped += 1
            i = j + 1
        else:
            i += 1
    return events, dropped


def _declick_fill(out: Any, s: int, t: int, total: int,
                  lo_bound: int = 0, hi_bound: Any = None) -> Any:
    """用**左右邻域波形线性过渡**填满 `[s, t)` —— 「消失」的正解：**换掉**，不是压低。

    🔴 2026-10-04 由「压低」改「换掉」（本仓作者：「目标是消失」）：
       旧法 `seg * (_scale + (1-_scale)*w)` 只把事件**缩到邻域背景量级** ⇒ 对 1~2ms 脉冲
       **压不到零**（实测残留 ≈10× 背景，仍听得见），且整窗乘系数把窗内背景一起压低 ⇒ 留洞。
       新法把整段替换成「左邻 → 右邻」的线性过渡 ⇒ **脉冲在波形里不存在**。
    🔴 `[lo_bound, hi_bound)` = **本事件周围不含其他事件的干净范围**（调用方算好传进来）。
       为什么必须：邻域若伸进**另一个事件**，就会把那个尖峰**原样搬进本段** ⇒ 本处留下的
       是邻居峰的副本，而它不在邻居的坐标上、**再也不会被治**
       （2026-10-04 审核实测：两个相隔 7ms 的尖峰 ⇒ 治后残留 **0.416 = 原峰 83%**，等于没治）。
    🔴 两端**不是样本级严丝合缝**：`fill[0] = left[0]`、`fill[-1] = right[-1]`，与接边的
       `out[s-1]` / `out[t]` **同分布但不同相位**（都是极静背景的噪声样本）。
       ⚠️ 这个"跳"在 −60 dBFS 量级、且只有 1 个样本 ⇒ 听不见；真实素材实测治后落到
       **0.00194（= 背景电平）**、没有冒出新的可闻瞬态，佐证这一点。
    🔴 **为什么敢直接拿邻域**：能走到这里的事件已过 `quiet_dbfs` 闸（左右邻都是**极静背景**）
       ⇒ 填进去的是背景质感，不会把语音搬进来。
    """
    L = t - s
    hi = total if hi_bound is None else min(int(hi_bound), total)
    la, lb = max(int(lo_bound), s - L), s
    ra, rb = t, min(hi, t + L)

    def _fit(v: Any) -> Any:
        n = int(v.shape[-1])
        if n == L:
            return v
        if n > L:
            return v[..., :L]
        if n == 0:                       # 邻域整个被其他事件占掉（病态输入）
            return torch.zeros(*v.shape[:-1], L, device=v.device, dtype=torch.float32)
        rep = (L + n - 1) // n           # 邻域不够长 ⇒ 重复自身补足
        return v.repeat(*([1] * (v.dim() - 1)), rep)[..., :L]

    left = _fit(out[..., la:lb])
    right = _fit(out[..., ra:rb])
    ramp = torch.linspace(0.0, 1.0, L, device=out.device, dtype=torch.float32)
    return left * (1.0 - ramp) + right * ramp


def _declick_suppress(x: Any, sr: int, w1: int, events: List[Tuple[int, int]],
                      e: Any, B: Any) -> Tuple[Any, List[Tuple[float, float, float]],
                                               List[Tuple[float, float, float]]]:
    """⑤ 逐事件**换掉**（用邻域背景线性过渡填充）。返回 `(out, done, skipped)`。

    `done` = 每处**已治**事件的 `(秒, 事件峰值, 邻域背景中位)` —— 报告与自测都读它。
    `skipped` = 每处**被跳过**事件的 `(秒, 事件峰值, 邻域峰值)`（判定见下）。

    🔴 **不是「压到背景电平」，是「换掉」**（2026-10-04 第二轮，本仓作者：「目标是消失」）：
       · 初版 `out = seg * w`（中间 w=0）⇒ 在背景里**挖了静音洞** ⇒ 洞边缘是新瞬态（10-03 打回）。
       · 上一版压到邻域背景 P50 ⇒ 对 1~2ms 脉冲**压不到零**（实测残留 ≈10× 背景），且整窗
         乘系数把窗内背景一起压低 ⇒ 留洞 ⇒ 本仓作者「抑制始终不生效」。
       · **本版**：`_declick_fill` 用左右邻域波形线性过渡填满整段 ⇒ 波形里不再有那个脉冲。
    🔴 **两遍：先算全部区间，再逐个填** —— 填充的邻域必须**避开其他事件**
       （否则把邻居的尖峰搬进来；审核实测残留 83%）。见 `_declick_fill` 的 docstring。
    🔴 **填充前先验「邻域是不是静背景」（2026-10-04 端到端实测补）**：
       起因：`declick_gate` 在「台词起点不可测」时退回**产线纪律上界**（= `cut` + 1.2s，
       本次实测 1.408s），这个范围**把 pin 区也包了进来** —— 而 pin 区是**上一段语音**的副本
       ⇒ 那里命中的「事件」其实是**语音的收尾**，不是孤立伪影。
       实测（`t3p_dc2_r4` @0.0710s，节点坐标）：左邻 **0.02905（−30.7 dBFS）** /
       事件 0.01434（−36.9）⇒ 旧写法治后 **0.02663（−31.5）** = **比事件本身还响 +5.4 dB**
       —— 等于把上一段语音**搬**到了这一处。
       判据：**邻域 max × `AUDIO_DECLICK_EDGE_REL` > 事件峰 ⇒ 跳过该事件（一个样本都不碰）**。
       （🔴 2026-10-04 二次修订：判据从「邻域 **>** 事件」改成「邻域 **×2 >** 事件」——
       前者在"持续噪声 + 音量断崖"的素材上约 50% 概率失效，把断崖当孤立峰填掉。
       完整依据见 `AUDIO_DECLICK_EDGE_REL` 的注释。）
       为什么站得住：孤立伪影能成立的前提就是「它周围极静」（判据 ③ 的 `quiet_dbfs`），
       而极静背景的波动**远达不到**事件峰的量级（同一次实测里的真实孤立瞬态：邻域 0.00319
       vs 事件峰 0.01466）。
       ⚠️ 跳过**必须可观测**（铁律 15）⇒ 进 `skipped`，报告里点名。
    """
    import numpy as np

    total = int(x.shape[-1])
    out = x.clone()
    fn = max(1, int(round(AUDIO_DECLICK_FADE_MS * sr / 1000.0)))
    done: List[Tuple[float, float, float]] = []
    skipped: List[Tuple[float, float, float]] = []
    spans: List[Tuple[int, int, int, int]] = []          # (a, b, s, t)
    for a, b in events[:max(1, AUDIO_DECLICK_MAX_EVENTS)]:
        s = max(0, a * w1 - fn)
        t = min(total, (b + 1) * w1 + fn)
        if t - s >= 3:
            spans.append((a, b, s, t))
    _edge_rel = float(AUDIO_DECLICK_EDGE_REL)            # 提到循环外：常量不该每轮重算
    for i, (a, b, s, t) in enumerate(spans):
        lo = spans[i - 1][3] if i > 0 else 0             # 左邻不许伸进前一个事件
        hi = spans[i + 1][2] if i + 1 < len(spans) else total   # 右邻不许伸进后一个
        pk = float(out[..., s:t].abs().max())
        L = t - s
        _nb = 0.0                                        # 邻域峰值（左右各取 1×事件长）
        for _s0, _s1 in ((max(lo, s - L), s), (t, min(hi, t + L))):
            if _s1 > _s0:
                _nb = max(_nb, float(out[..., _s0:_s1].abs().max()))
        if _nb * _edge_rel > pk:                          # 邻域不够静（未低一个量级）⇒ 不是孤立伪影
            skipped.append((s / float(sr), pk, _nb))
            continue
        out[..., s:t] = _declick_fill(out, s, t, total, lo, hi)
        done.append((s / float(sr), float(e[a:b + 1].max()), float(np.median(B[a:b + 1]))))
    return out, done, skipped


@dataclass
class _DeclickCtx:
    """`declick_transients` 一次调用的**判定上下文**：报告要的「按什么判、在哪份音频上、范围多宽」。

    🔴 **为什么要它**（2026-10-04）：报告拼装的入参一度是 **10 个位置参数** —— 恰好等于调用点
       当时的全部局部变量；更糟的是其中 `sr` / `total` / `out_len` **三个连着都是 int**
       ⇒ 写错顺序（`total` 与 `out_len` 对调）会**生成一份看着完全正常的报告**，
       而「长度守恒 %d → %d」那行会打印成 `324000 → 324000`，**谁也不会发现异常**。
       收成具名对象 ⇒ 这类错不可能再犯；且**新增报告项时不必再改签名**（只加字段）。
    """

    sr: int
    total: int
    ratio: float
    quiet_dbfs: float
    max_len_ms: float = AUDIO_DECLICK_MAX_LEN_MS
    limit_n: Any = None
    limit_is_fallback: bool = False
    cut: int = 0
    cut_faded: bool = False
    #: 🔵 **实际生效**的背景闸（线性幅度）= `max(quiet_dbfs, 全段背景 × AUDIO_DECLICK_QUIET_REL)`。
    #:   与 `quiet_dbfs` **分开记**：报告要能看出"这次用的是绝对值、还是相对放宽后的"
    #:   （否则用户按「背景 < −50」对账、却发现自己素材底噪 −45，会以为参数失效）。
    quiet_eff: float = 0.0


def _declick_report(done: List[Tuple[float, float, float]], ctx: "_DeclickCtx",
                    out_len: int, skipped: Any = ()) -> str:
    """报告拼装（口径见下）。**入参收成 `ctx` 的由来见 `_DeclickCtx` 的 docstring。**

    `skipped` = 「邻域不静 ⇒ 跳过」的事件（见 `_declick_suppress`）：**必须点名**
    （铁律 15）—— 否则读者只看到「治了 N 处」而不知道还有 M 处被保守放过。

    🔴 **范围闸要写明是不是 fallback**：拿不到台词起点时退回产线纪律上界，与「真的测到台词起点」
       不是一回事 —— 读者要能分辨。
    🔴 **位置同时给「节点内坐标」与「产物坐标」**：本节点工作在未裁音频上、产物是裁后的，
       二者相差 `cut`；只报一个 ⇒ 与产物对账时必然对不上（2026-10-03 为此查了三轮）。
    """
    off = ctx.cut / float(ctx.sr)
    if ctx.limit_n is not None and int(ctx.limit_n) >= int(ctx.total):
        # 🔴 全段政策（`AUDIO_DECLICK_SCOPE_ALL`）。**必须写明** —— 否则读者会把
        #    「没范围限制」读成「会误伤台词」，而实际上把关全在「邻域静」那道闸上
        #    （按**内容**判：事件峰必须高于左右邻）。这是 2026-10-04 那次静默漏治的
        #    教训 —— 报告写「范围 = 前 1.408s」时，读者**看不出** 4.4s 那两声根本没进闸。
        range_txt = ("，范围 = **全段**（靠「邻域静」闸把关："
                     "事件峰必须高于左右邻才算孤立伪影）")
    elif ctx.limit_n is None:
        range_txt = "，⚠ 未设范围闸（可能碰到句首爆破音）"
    else:
        range_txt = "，范围 = 前 %.3fs（%s）" % (
            int(ctx.limit_n) / float(ctx.sr),
            "⚠ 台词起点不可测 ⇒ 退回产线纪律上界" if ctx.limit_is_fallback else "台词起点前")

    def _ev_txt(t: float, pk: float, bg: float) -> str:
        if off > 0:
            return "@%.3fs（产物 @%.3fs）%.4f→背景%.4f" % (t, max(0.0, t - off), pk, bg)
        return "@%.3fs %.4f→背景%.4f" % (t, pk, bg)

    _skip_txt = ""
    if skipped:
        _skip_txt = ("\n           跳过 %d 处（**邻域不够静**：事件峰未达到邻域的 %.1f 倍 "
                     "⇒ 判为真实内容的边缘 / 收尾，一个样本都不碰）：%s"
                     % (len(skipped), AUDIO_DECLICK_EDGE_REL,
                        "；".join("@%.3fs 事件%.4f<邻域%.4f×%.0f" % (tt, pk, nb, AUDIO_DECLICK_EDGE_REL)
                                  for tt, pk, nb in skipped[:4])
                        + ("…" if len(skipped) > 4 else "")))

    # 🔵 报告显示**实际生效**的背景闸（见 `_DeclickCtx.quiet_eff`）—— 兼容性缺口那次
    #    就是"参数写着 −50，用户素材底噪 −45，于是永不命中而报告只说未命中"。
    _q_db = (20.0 * math.log10(ctx.quiet_eff) if ctx.quiet_eff > 0 else ctx.quiet_dbfs)
    _q_txt = "%.0f dBFS" % _q_db
    if ctx.quiet_eff > 0 and abs(_q_db - ctx.quiet_dbfs) > 0.5:
        _q_txt = "%.0f dBFS【全段背景×%.0f 放宽后；绝对值 %.0f】" % (
            _q_db, AUDIO_DECLICK_QUIET_REL, ctx.quiet_dbfs)
    return ("[H3 Relay] 音频缝：**孤立瞬态抑制** %d 处"
            "（判据 峰值/背景 > %.1f 且 背景 < %s，事件 ≤ %.1fms，两端淡变 %.1fms%s%s）\n"
            "           位置与量级：%s\n"
            "           其余内容**逐位不动**（长度守恒 %d → %d）%s"
            % (len(done), ctx.ratio, _q_txt,
               ctx.max_len_ms, AUDIO_DECLICK_FADE_MS, range_txt,
               "，裁帧边界 @%.3fs 已淡入 %.1fms" % (off, AUDIO_DECLICK_FADE_MS)
               if ctx.cut_faded else "",
               "；".join(_ev_txt(tt, pk, bg) for tt, pk, bg in done[:8])
               + ("…" if len(done) > 8 else ""),
               ctx.total, out_len, _skip_txt))


def declick_transients(audio: Any, ratio: float = AUDIO_DECLICK_RATIO,
                       quiet_dbfs: float = AUDIO_DECLICK_QUIET_DBFS,
                       limit_n: Any = None, cut_head_n: Any = None,
                       limit_is_fallback: bool = False,
                       max_len_ms: Any = None) -> Tuple[Any, str]:
    """抑制**背景极静区**里的**孤立瞬态**（<2ms 尖脉冲）。**长度守恒、其余内容逐位不动**。

    【为什么要它（2026-10-03 实测 + 本仓作者 耳检定性）】
      模型会在**无台词区**生成孤立尖脉冲。实测段2 @**0.930s**：峰值 **0.0095（−40 dBFS）**，
      而该处背景仅 **−60 dBFS** ⇒ 信噪比 ~20 dB ⇒ 在安静背景里突出成
      **「无缘无故的孤立瞬态」**（本仓作者 原话，并定性为**伪影**而非动作音效）。
      ⚠️ 它**不是**接缝 / pin / 交界伪影 —— **各臂都有**（nopin 0.0070 / A 0.0018 / P5 0.0095），
         只是 D/N1 段首有语音把它盖住了（0.26–0.38）。
      ⚠️ 与 `audio_seam_patch` 的区别：patch 是**整段替换** ⇒ 实测把床源瞬态**搬进来**
         （0.930s → 0s）**治不了根**；本函数**只掐那一下**，其余逐位不动。

    【判据（保守优先：宁可漏，也不误伤语音）】
      ③ 候选 = `E > ratio*B` **且** `B < quiet_dbfs 线性值`
         ← 🔴 **第二个闸判的是「背景」，不是「事件本身」**（两轮试错换来的）：
            · 初版给**事件**设绝对上限（floor）⇒ 卡不住：语音的**轻辅音**（p/t/k 的送气段）
              可以比「孤立瞬态」还轻，两者量级**重叠** ⇒ 无论 floor 取 −45/−36/−32/−20，
              **要么漏掉「孤立瞬态」、要么掐到台词**（实测台词区最大改动 0.152 → 0.023 → 0.014，始终非 0）。
            · **正确判据是「它周围有多静」**：「孤立瞬态」之所以听得见，正是因为**它周围极静**
              （实测背景 B=0.0016 = **−56 dBFS**）；而语音的辅音**周围是元音**（B≈0.017 = −35 dBFS）。
            · ⇒ 用 `B < quiet_dbfs`（默认 **−50 dBFS**）就能干净地分开两者，且**对事件量级不敏感**
              （将来画幅/模型让「孤立瞬态」变响或变轻，这个判据依然成立）。

    【实现】① 包络 ② 底噪 ④b 裁帧边界重算底噪 ④c 裁帧边界淡入 ④ 事件合并 ⑤ 压制
      —— 各自在 `_declick_envelope` / `_declick_bg_at_cut` / `_declick_fade_in_at_cut` /
      `_declick_find_events` / `_declick_suppress` / `_declick_report` 里（本函数只做编排）。
      ⚠️ 拆函数是**纯代码搬移**，一个算式都没改 —— 判据 = **离线重放的逐位比对**（波形 sha 全等）
         的**逐位重放**（波形 sha 必须与重构前完全一致）+ `tests/test_relay_core.py` 第 30 组。

    返回 ``(audio, report)``。`ratio <= 0` ⇒ 关（返回原 audio + 空 report，**逐位直通**）。
    无事件 ⇒ `(原 audio, "")`（**不打日志**，保持「没动就不报」的语义）。
      ⚠️ 已知取舍：无事件时**连裁帧边界淡入也不生效**（那条改动在克隆张量上，随原 audio 一起丢弃）。
         实测该边界伪影与孤立峰通常同时出现（`#931` 报告里两者同时命中），故保持现状不改 ——
         动它属于**行为变更**，要有独立证据与 A/B，不混在重构里做。
    """
    if ratio is None or float(ratio) <= 0:
        return audio, ""
    # 🔴 「多宽算一声」：节点层可覆盖（widget `declick_max_len_ms`）；缺省取常量
    #    ⇒ **默认值只有一个出处**（`AUDIO_DECLICK_MAX_LEN_MS`），别在这里再写字面量。
    _ml = float(AUDIO_DECLICK_MAX_LEN_MS if max_len_ms is None else max_len_ms)
    wf, sr, lead, dtype = _audio_parts(audio)
    # 🔴 **先展平前导维**：`_audio_parts` 返回的 `wf` 可能带 batch 维（如 `[1,1,T]`），
    #    而下游 helper 假设 `[C, T]`。不展平 ⇒ `x[0]` 拿到二维数组 ⇒ reshape 抛异常
    #    ⇒ **被宿主吞掉**（铁律 8：宿主会吞异常）⇒ 节点照常出产物、但**declick 静默没执行**
    #    （2026-10-03 实测：图上 `declick_ratio=4.0` 传对了、日志里却没有抑制报告行）。
    #    ⚠️ 同时记下**输入**的原始形状（从 `audio` 直接取，**不是** `_audio_parts` 归一后的），
    #    返回时还原 —— 否则 1D 输入 `[T]` 会变成 `[1,T]`
    #    （自测 `test_declick.py` ①组抓到；第一版误把「归一后形状」当原始形状，又错一轮）。
    _orig = None
    if isinstance(audio, dict):
        _w0 = audio.get("waveform")
        if _w0 is not None and hasattr(_w0, "shape"):
            _orig = tuple(int(v) for v in _w0.shape)
    # 🔴 展平前导维**用显式求积，不用 `-1`**（与 `_audio_parts` / `declick_gate` 同款修复）：
    #    `reshape(-1, T)` 在**空张量**（如 `[1, 0]`）上会抛
    #    `cannot reshape … unspecified dimension size -1`，而空音频是**合法输入**
    #    （上游可能给空段）⇒ 不该炸整条链，应作为「无内容」往下走（下面 `total < 20*w1` 接住）。
    _t = int(wf.shape[-1])
    _n = 1
    for _v in wf.shape[:-1]:
        _n *= int(_v)
    wf = wf.reshape(_n, _t) if _n else wf.reshape(1, _t)
    ch, total = int(wf.shape[0]), int(wf.shape[-1])
    w1 = max(1, int(0.001 * sr))                 # 1ms
    if total < 20 * w1:                          # 太短，不值得动
        return audio, ""
    import numpy as np

    x = wf.detach().to(torch.float32)
    # 🔴 **非有限输入 fail-closed**（2026-10-04 多维度审核发现）：NaN/Inf 会让两条判据同时失效 ——
    #    ① `np.quantile`（全段背景基准）出 NaN ⇒ 相对闸失去意义；② 「用邻域波形填充」把 NaN
    #    **传遍全段** ⇒ 输出整轨非有限，而报告照常打「抑制了 N 处」⇒ **静默产出坏音频**。
    #    与 `_voice_anchor_tail` 的内容守卫同一条纪律：可疑输入**直接 raise**，不静默降级。
    if not bool(torch.isfinite(x).all()):
        raise ValueError(
            "音频缝（孤立瞬态抑制）：输入含 NaN/Inf ⇒ 分位判据与邻域填充都会失效 ⇒ 拒绝处理。\n"
            "    先查上游（VAEDecodeAudio / 上一段 latent）为什么出非有限值。")
    n1 = total // w1
    e, B, half = _declick_envelope(x, w1, n1)
    cut = _declick_bg_at_cut(B, e, n1, w1, half, cut_head_n)
    x, cut_faded = _declick_fade_in_at_cut(x, total, cut, sr)
    quiet_lin = 10.0 ** (float(quiet_dbfs) / 20.0)
    # 🔵 2026-10-04 **智能兼容**：实际闸取「`quiet_dbfs` 绝对值」与「全段背景 × 倍数」
    #    **较宽**者 —— 取值依据（含为什么用 P20 而不是中位）见 `AUDIO_DECLICK_QUIET_REL`。
    #    ⇒ 判据从「素材绝对电平」解耦成「局部背景 vs 全段最静处」，跨素材通用。
    _ref_bg = float(np.quantile(np.asarray(B).reshape(-1), 0.20))
    quiet_eff = max(quiet_lin, _ref_bg * float(AUDIO_DECLICK_QUIET_REL))
    cand = (e > float(ratio) * np.maximum(B, 1e-9)) & (B < quiet_eff)    # ③ 相对突出 + 背景够静
    # ④ 🔴 **范围闸**：只在前 `limit_n` 个样本内动手（由调用方传「台词起点」）。
    #    为什么必须：**「句首爆破音」与「孤立瞬态」在特征上分不开**（都是"静音后的第一个峰"，
    #    背景同样极静）—— 2026-10-03 实测：不加范围闸时台词区峰值被削 26%
    #    （0.4089 → 0.3019）。而「孤立瞬态」恒在**台词之前**（本例 0.930s vs 台词 1.25s）
    #    ⇒ 用节点已有的 `_speech_onset_in_head` 划一条线就能干净分开。
    #    `limit_n` 为 None ⇒ 不设范围（老行为，会误伤，仅作对照用）。
    if limit_n is not None:
        lim_idx = max(0, int(limit_n)) // w1
        # 🔴 `lim_idx <= 0` 时**不设范围**，**不能直接 return 空**：段首就有声时
        #    `_speech_onset_in_head` 会返回 0（或很小值）⇒ 若在这里 return，**整个 declick
        #    静默失效**（2026-10-03 实测：图上参数传对了、宿主也加载了新定义，但日志里没有抑制行）。
        #    「段首有声」的典型场景 = pin 区本身就是语音 ⇒ 那种情况靠 `quiet_dbfs` 兜底
        #    （pin 区背景不静 ⇒ 不会命中）。
        if lim_idx > 0:
            cand[lim_idx:] = False
    # 📌 这里曾有一个「段首跳过闸」（`skip_head_ms`，默认 20ms），**已删**：
    #    它的理由（「段首是文件头、背景恒为 0 ⇒ 起音会误判」）被实测推翻 —— 本仓作者 听到的
    #    「孤立瞬态」**就在段首 0.0–2.0ms**，那个闸把唯一要治的东西挡在门外（白跑两轮）。
    #    文件头的「背景恒为 0」改由 `s == 0` 的淡出特判解决。
    #    **教训**：加保护闸前先确认「被保护的范围里有没有要治的东西」。

    events, _dropped = _declick_find_events(cand, n1, w1, sr, _ml)
    if not events:
        # 🔴 2026-10-04 曾想把「裁帧边界淡入」从本 return 里救出来（让它无条件生效）——**已否决**。
        #    实测：那样会让**每一段**都在 `cut` 处淡入 2ms
        #    并各打一行报告 ⇒ 违背「没动就不报」的零副作用语义，还把「本无需处理」的段标成处理过。
        #    抑制孤立瞬态的路已经通了：`_declick_bg_at_cut` 把 `cut` 处的背景按「只看右边」重算
        #    ⇒ 背景极静 ⇒ `cand[cut]` 必中 ⇒ 孤立瞬态**一定进 `events`** ⇒ 淡入随 ⑤ 压制一起生效。
        #    ⚠️ 修 ④ 之前它不进 events，真因是**范围闸把 cut 排除了**，不是「淡入被短路」——
        #       若把病因记错，就会去改这个 return（白改，还带副作用）。
        # 🔴 **「被超长闸丢掉」必须说出来**（铁律 15）：否则用户把 `declick_ratio` 调得很小、
        #    候选连成一片 ⇒ 全被丢 ⇒ 一个都不治，而报告只写「未命中」⇒ **无从归因**。
        if _dropped:
            return audio, ("[H3 Relay] 音频缝：孤立瞬态抑制**未命中事件** —— "
                           "但有 %d 个候选段因**超过 %.1f ms**（`declick_max_len_ms`）被丢弃；"
                           "若这是有意的（如 ratio 调得过小 ⇒ 候选连成一片），可忽略。"
                           % (_dropped, _ml))
        return audio, ""
    out, done, skipped = _declick_suppress(x, sr, w1, events, e, B)
    if not done:
        # 命中过候选事件、但**一处都没动**（全部因「邻域不静」被跳过，或区间过短被弃）
        # ⇒ 与「没命中」同语义：返回原 audio（逐位直通）。
        # 🔴 「跳过」必须说出来（铁律 15）：否则用户看到「参数没错、却没治到」无从归因。
        if skipped:
            return audio, ("[H3 Relay] 音频缝：孤立瞬态抑制**未改动任何内容** —— "
                           "%d 处候选因**邻域不够静**（邻域峰值 × %.1f > 事件峰）被跳过；"
                           "这是保守选择：邻域量级与事件相当（音量断崖、语音/音效的边缘或收尾）"
                           "时硬填会改动那段内容。"
                           % (len(skipped), AUDIO_DECLICK_EDGE_REL))
        return audio, ""
    # 🔴 ctx 只在**真有事件要报**时才构造（上面 `if not events: return` 已经短路）⇒
    #    没命中就不建对象，保持「没动就不报」的零开销语义。
    rep = _declick_report(done, _DeclickCtx(
        sr=sr, total=total, ratio=float(ratio), quiet_dbfs=float(quiet_dbfs),
        quiet_eff=quiet_eff,
        max_len_ms=_ml,
        limit_n=limit_n, limit_is_fallback=bool(limit_is_fallback),
        cut=cut, cut_faded=cut_faded), int(out.shape[-1]), skipped)
    shaped = out.reshape(*lead, ch, total) if lead else out
    # 还原原始形状（_audio_parts 会把 1D 输入 [T] 归一成 [1,T]）
    if tuple(shaped.shape) != _orig:
        shaped = shaped.reshape(_orig)
    return {"waveform": shaped.to(dtype), "sample_rate": sr}, rep


def declick_gate(wf: Any, sr: int, cut_head_frames: Any = 0,
                 scope_all: Any = None) -> Tuple[int, bool, int]:
    """节点层「**范围闸政策**」：把台词起点 / 裁帧边界换算成 `declick_transients` 要的三个数。

    2026-10-04 从 `nodes.py` 的 `_run_declick` 闭包**原样搬来**（一个算式都没改）。
    动机**不是省行数**（core 加 ~40 行、节点减 ~40 行，总量不变），而是**可测性** ——
    这三条分支此前**零断言覆盖**（闭包不在任何门槛里，`smoke_nodes.py` 也不碰 declick），
    而它们的失败模式**全是静默的**：范围算错 ⇒ 该治的没治，而日志看起来一切正常。

    返回 ``(limit_n, is_fallback, cut_n)``：
      · `limit_n`      — 允许动手的样本数（台词起点之前）
      · `is_fallback`  — True ⇒ 起点**没测到**，`limit_n` 是产线纪律上界（报告必须能分辨）
      · `cut_n`        — 裁帧边界（样本），让 `declick_transients` 把边界当「新文件头」判

    🔴 三条分支各自的由来（搬来时一并保留 —— 每条都对应一次实测）：
      ① **先展平前导维**：`_audio_parts` 的 `wf` 可能带 batch 维（节点里实测 `[1,1,T]`），
         而 `_speech_onset_in_head` 内部走 `_frame_rms` ⇒ 形状不对会让判据行为与展平后不同
         （返回 None）⇒ 下游范围闸失效。
      ② **fallback 不能退化成「不限范围」**：onset=None 时若返回 None/0，declick 会一路
         扫到台词区（2026-10-03 实测：出 8 处、台词区峰值被削 4.8%）
         ⇒ 退回产线纪律 `AUDIO_DECLICK_FALLBACK_S`（1.2s）。
      ③ **裁帧边界要补偿**：fallback 的 1.2s 是**从头**算的，而真凶（裁出来的孤立峰）在
         **边界之后** ⇒ pin 长（22 帧 = 0.917s）时边界后只剩 0.28s ⇒ 治不到
         ⇒ fallback 时范围至少覆盖到「边界 + 1.2s」（安全方向仍由 `quiet_dbfs` 闸兜住）。
    """
    # 🔴 换算按 **`FPS` 常量（24）**：`cut_head_frames` 是「裁掉多少**帧**」，而 `#931`
    #    手里的音频是**样本**。用户的 `Trim AV` 若用了别的 fps，这里就会偏
    #    —— `cut_head_frames` 的 tooltip 已写明要自行折算，报告的 `裁帧边界 @x.xxxs` 可对账。
    cut_n = int(round(max(0, int(cut_head_frames or 0)) / float(FPS) * sr))
    _scope = AUDIO_DECLICK_SCOPE_ALL if scope_all is None else bool(scope_all)
    if _scope:                                                               # ⓪ 全段政策
        # 🔴 依据见 `AUDIO_DECLICK_SCOPE_ALL`（旧政策前提「孤立瞬态恒在台词前」已被端到端实测证伪）。
        #    `limit_n = 全长` ⇒ `declick_transients` 里 `cand[lim_idx:] = False` 退化成
        #    **空切片** ⇒ 等价于「不设范围」，**不必改那个函数**（零连带）。
        #    `is_fallback` 报 `False`：全段模式下「台词起点测没测到」与动手范围**无关**
        #    ⇒ 报 True 会让报告说「退回产线纪律上界」，那是假话。
        #    ⚠️ 顺带省掉 `_speech_onset_in_head` 的开销（探 3s 的 RMS）。
        return int(wf.shape[-1]), False, cut_n
    # ① 展平前导维 —— **只在 head 分支做**（2026-10-04 多维度审核）：
    #    · 全段分支只用 `shape[-1]`，上面已 `return` ⇒ 不必展平（省一次无谓张量操作）；
    #    · `reshape(-1, T)` 在**空张量**（如 `[1, 0]`）上会抛
    #      `cannot reshape … unspecified dimension size -1`，而空音频是**合法输入**
    #      （见 `_audio_parts` 的同款修复）⇒ 显式求积，**不用 `-1`**；
    #    · 要展平的理由：`_speech_onset_in_head` 内部走 `_frame_rms`，形状不对会让判据
    #      与展平后不同（返回 None）⇒ 下游范围闸失效。
    _t = int(wf.shape[-1])
    _n = 1
    for _v in wf.shape[:-1]:
        _n *= int(_v)
    _wf = wf.reshape(_n, _t) if _n else wf.reshape(1, _t)
    n_probe = min(int(_wf.shape[-1]), int(AUDIO_DECLICK_PROBE_S * sr))
    _lim = _speech_onset_in_head(_wf, n_probe)
    is_fallback = not _lim                                                   # ②
    if is_fallback:                                                          # ③
        _fb_n = int(round(AUDIO_DECLICK_FALLBACK_S * sr))
        _lim = max(_fb_n, cut_n + _fb_n)
    # ④ 🔴 **范围闸必须盖过裁帧边界**（2026-10-04 新增；零 GPU 复现逼出来的）。
    #    接缝处那一记孤立瞬态 = 裁帧切断波形留下的半波形，**恒在 `cut` 处**。
    #    而 ② 的线画在「头部首个有声 run」—— 那个 run 在 **pin 区（上一段尾巴）里**
    #    ⇒ 线落在 `cut` **之前** ⇒ 孤立瞬态被 `cand[lim_idx:] = False` 排除
    #    （实测 C1：pin 语音 0.05s 起 ⇒ 范围=前 0.050s ⇒ cut(0.208s) 峰值 0.5000 → 0.5000，
    #      报告却只说「未命中」⇒ **静默漏治**）。
    #    `cut` 之后才是新段内容 ⇒ 「上一段语音在哪」不该用来关掉它。
    #    ⚠️ 只抬**下界**：`cut + guard` 之后仍由 `quiet_dbfs` 闸兜底 ⇒ 不会为了抑制孤立瞬态去削新段台词。
    if cut_n > 0:                                                            # ④
        _lim = max(int(_lim), cut_n + int(round(AUDIO_DECLICK_CUT_GUARD_S * sr)))
    return int(_lim), bool(is_fallback), cut_n


def declick_on_segment(audio: Any, ratio: Any, quiet_dbfs: Any = None,
                       cut_head_frames: Any = 0,
                       max_len_ms: Any = None) -> Tuple[Any, str]:
    """**节点层入口**：范围闸政策 + 「未命中也要报一行」。返回 ``(audio, note)``。

    ``note == ""`` 当且仅当 `ratio <= 0`（declick 关）—— 保持「没启用就不说话」的语义。

    🔴 未命中时**必须**给一行（铁律 15：安全网 / 退化分支必须可观测）：否则
       「declick 没执行」与「执行了但没命中」在日志里**完全一样**
       （2026-10-03 为此查了三轮：图上参数对、宿主也加载了新定义，就是看不到输出）。
    🔴 `quiet_dbfs=None` ⇒ 用 `AUDIO_DECLICK_QUIET_DBFS`（**常量**）。原 `nodes.py` 里
       硬写了一个 `-50.0` 字面量 ⇒ 同一个默认值有两个出处，改一处漏一处察觉不到。
    """
    if float(ratio or 0.0) <= 0.0:
        return audio, ""
    q = float(AUDIO_DECLICK_QUIET_DBFS if quiet_dbfs is None else quiet_dbfs)
    ml = float(AUDIO_DECLICK_MAX_LEN_MS if max_len_ms is None else max_len_ms)
    wf, sr, _, _ = _audio_parts(audio)
    lim, is_fb, cut_n = declick_gate(wf, int(sr), cut_head_frames)
    out, rep = declick_transients(audio, ratio=float(ratio), quiet_dbfs=q,
                                  limit_n=lim, cut_head_n=cut_n, limit_is_fallback=is_fb,
                                  max_len_ms=ml)
    if not rep:
        rep = ("[H3 Relay] 音频缝：孤立瞬态抑制已启用"
               "（ratio=%g，背景<%g dBFS，事件≤%g ms，范围=前 %.3fs%s）⇒ **本段未命中事件** ⇒ 逐位直通"
               % (float(ratio), q, ml, lim / float(sr),
                  "（⚠ 台词起点不可测 ⇒ 退回纪律上界）" if is_fb else ""))
    return out, rep


def save_audio(audio: Any, path: str, note: str = "") -> str:
    """把 AUDIO 落盘（safetensors，原子写），供后段当床源读回。

    存**原始波形张量**（任意前导维，通常 [B,C,T]）而不是展平后的 [C,T]——
    这样 load 回来与存之前逐位、逐形状都一致（单测 22.12 锁这个）。
    """
    from safetensors.torch import save_file

    _, sr, _, _ = _audio_parts(audio)
    wf = audio["waveform"]
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    meta = {"format": 1, "sample_rate": sr, "shape": [int(v) for v in wf.shape],
            "note": str(note)}
    meta_bytes = json.dumps(meta, ensure_ascii=False).encode("utf-8")
    tensors = {"waveform": wf.detach().cpu().contiguous(),
               _AUDIO_META_KEY: torch.frombuffer(bytearray(meta_bytes), dtype=torch.uint8)}
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


def load_audio(path: str) -> Dict[str, Any]:
    """读回落盘的 AUDIO。"""
    from safetensors.torch import load_file

    if not os.path.isfile(path):
        raise FileNotFoundError("读不到落盘音频：%s" % path)
    raw = load_file(path)
    meta_t = raw.pop(_AUDIO_META_KEY, None)
    if meta_t is None:
        raise ValueError("%s 不是本工具写的 AUDIO（缺少元数据）。" % path)
    meta = json.loads(bytes(meta_t.tolist()).decode("utf-8"))
    wf = raw["waveform"]
    shape = meta.get("shape")
    if shape and list(wf.shape) != list(shape):
        wf = wf.reshape(*[int(v) for v in shape])
    return {"waveform": wf, "sample_rate": int(meta["sample_rate"])}


def audio_to_fp32(audio: Any) -> Any:
    """把 AUDIO 的波形统一成 **float32** —— 本包把音频交给图里下游之前必须做。

    为什么必须（真 bug，修于 0.6.11）：本包音频一路是 fp16（`VAEDecodeAudio` 按底模精度出，
    落盘/读回也保持），而 `VHS_VideoCombine` 合成音轨时**写死 `-f f32le` 且不做 dtype 转换**
    （`waveform.squeeze(0).transpose(0,1).numpy().tobytes()`）⇒ fp16 字节被当 f32 解读
    ⇒ 编码后**逐样本全零**。画面 / 边车 / 日志全都正常，只有成片没声音，极难排查。

    为什么改在**我们这侧**：`float32` 是 ComfyUI 里 AUDIO 的**事实约定**
    （VHS 自己的 `get_audio` 也返回 f32），fp16 是**我们**的偏离；且下游消费方不止一个。
    转 f32 **无损**（纯加宽，不改值），已是 f32 时**返回原对象**（零拷贝、零开销），
    代价只是波形内存翻倍（音频量级，可忽略）。

    形状与其余键**原样保留**（含 `sample_rate` / 前导维）；``None`` 原样返回（图中 audio 可选）。
    """
    if audio is None:
        return None
    wf = audio.get("waveform")
    if not isinstance(wf, torch.Tensor) or wf.dtype == torch.float32:
        return audio                                  # 已是 f32 / 形状不对（交给下游自己报）
    out = dict(audio)
    out["waveform"] = wf.to(torch.float32)
    return out


# —— 音频拼接（0.5.0 第 9 节点用；2026-09-19 立）——
# 为什么放进**节点层**（本仓作者 2026-09-19 指令「功能完整地在节点层面实现」）：
#   缝上的两个音频病**都不在单段节点的可达范围内** ——
#     · 编码器 priming：每段 mp4 的 AAC 流头 ~33 ms 近静音（实测 @32k = 1056 样本；
#       同位置节点落盘有内容 −22.9 dBFS、mp4 解码 −66.8 dBFS，差 44 dB）。它在**编码之后**产生。
#     · 交叉曲线：两段内容不相关时，线性淡变（ffmpeg acrossfade 默认 tri）中缝掉 −4.7 dB。
#   两件事都只能由「拼接方」做 ⇒ 那就让**包自己当拼接方**，用户不必碰任何 ffmpeg 命令。
AUDIO_ENCODER_PRIME_MS: float = 33.0     # AAC 编码器 priming 实测值（@32k ≈ 1056 样本）


def join_audio_segments(audios, prime_samples: int = 0, cross_samples: int = 0,
                        curve: str = "qsin", segment_seconds: float = 0.0,
                        align_seconds: float = 0.0):
    """把多段音频按序接成**一条**，返回 ``(audio, report)``。两种语义：

    **J-cut 时间轴守恒（``align_seconds > 0``，推荐）**——``align`` = 每缝的**画面裁量**
    （秒，= 裁帧数/fps）；``segment_seconds`` = 每段音频有效时长（秒，= 该段视频帧数/fps；
    节点 PCM 通常比 mp4 长，必须截）。第 i≥1 段从其 ``align`` 处进入成片时间轴（与裁后
    画面第 0 帧对齐），其前 ``cross`` 秒素材作**渐入前奏**：先**电平对齐**到前段尾
    （±6 dB 限幅 + 0.995 峰值护栏），再按 ``curve`` 权重**叠加**到前段尾部（前段原位保留）
    —— 电影业 J-cut：声音先走、时间轴不缩短、缝上无电平凹陷：
    输出总长 = n·segment_seconds − (n−1)·align，**与裁后视频严格等长**。

    **旧缩短语义（``align_seconds = 0`` 且 ``segment_seconds = 0``，仅兼容/对照）**：
    等功率交叉且 cross 计入时间轴（输出 = Σ(段长−prime) − (n−1)·cross）——
    ⚠ 每缝使后段音频相对画面**提前 cross 秒**、片尾需补 cross 静音。

    ``prime_samples``：编码器 priming（节点 PCM 源 = 0；mp4 源每段头 ~33 ms）。
    采样率与声道以**第一段**为准。
    """
    if not audios:
        raise ValueError("join_audio_segments：至少需要一段音频。")
    al_s = float(align_seconds)
    if al_s < 0:
        raise ValueError("align_seconds 不能为负（%r）。" % al_s)
    ws = []
    sr0 = ch0 = None
    dt0 = None
    for i, a in enumerate(audios):
        wf, sr, _lead, dt = _audio_parts(a)
        if sr0 is None:
            sr0, ch0, dt0 = sr, int(wf.shape[0]), dt
        wf = _match_channels(_resample_to(wf, sr, sr0), ch0).float()
        k = int(prime_samples)
        if k > 0:
            if k >= int(wf.shape[-1]):
                raise ValueError("去 priming %d 样本 ≥ 第 %d 段长度 %d 样本，段太短。"
                                 % (k, i + 1, int(wf.shape[-1])))
            wf = wf[..., k:]
        if segment_seconds > 0:
            seg_n = int(round(float(segment_seconds) * sr0))
            if seg_n <= 0 or seg_n > int(wf.shape[-1]):
                raise ValueError("segment_seconds=%.3fs（%d 样本）超出第 %d 段实际 %d 样本。"
                                 % (segment_seconds, seg_n, i + 1, int(wf.shape[-1])))
            wf = wf[..., :seg_n]
        ws.append(wf)
    n = int(cross_samples)
    align_n = int(round(al_s * sr0)) if al_s > 0 else 0
    out = ws[0]
    notes_extra = ""
    # 🔴 跨段响度匹配（2026-09-19 第三轮迭代）：各段 BGM 独立生成，段间整体电平差可达
    #    ~8 dB（实测 A 中位 −14 vs B −22）—— 拼接点上表现为「瞬时卡顿」的真正来源之一。
    #    参考电平 = 第 1 段常态 RMS；第 i≥1 段**整段**缩放（限幅 ±6 dB，峰值护栏 0.995）。
    _ref_rms = float(out.pow(2).mean().sqrt())
    for _wi in range(1, len(ws)):
        _w = ws[_wi]
        _cur = float(_w.pow(2).mean().sqrt())
        if _cur > 0.0 and _ref_rms > 0.0:
            _g_db = 20.0 * math.log10(_ref_rms / _cur)
            _g = 10.0 ** (max(-6.0, min(6.0, _g_db)) / 20.0)
            if abs(_g_db) > 6.0:
                notes_extra += "｜ 第 %d 段响度匹配限幅 %+.1f dB" % (_wi + 1, _g_db)
            _pk = float(_w.abs().max())
            if _pk > 0.0:
                _g = min(_g, 0.995 / _pk)          # 峰值护栏
            ws[_wi] = _w * _g
    for w in ws[1:]:
        if align_n > 0:
            # —— J-cut 守恒：前奏（电平对齐后渐入叠加）+ 本段从 align 起全量 ——
            ent = min(align_n, int(w.shape[-1]) - 1)
            m = min(n, ent, int(out.shape[-1]))
            if m > 0:
                pre = w[..., ent - m:ent]                       # 前奏素材（进入点之前）
                t = torch.linspace(0.0, 1.0, m, device=out.device, dtype=torch.float32)
                f2 = torch.sin(t * math.pi / 2.0) if curve == "qsin" else t
                if curve not in ("qsin", "tri"):
                    raise ValueError("未知交叉曲线 %r（只认 qsin / tri）。" % (curve,))
                tail = out[..., -m:]
                # 🔴 对齐目标 = max(前段尾, 本段常态中位)：只对齐到「已衰落的 A 尾」会让
                #    缝上仍是两个低电平相加（jA 首轮实测缝上 −8.2 dB 更深）⇒ 取两者较大者，
                #    保证交叉窗电平不低于任一侧的常态。
                _w_med = float(w.pow(2).mean().sqrt())           # 本段（进入段）整体 RMS = 常态电平
                tgt = max(float(tail.pow(2).mean().sqrt()), _w_med)
                cur = float(pre.pow(2).mean().sqrt())
                if cur > 0.0 and tgt > 0.0:
                    g_db = 20.0 * math.log10(tgt / cur)
                    g = 10.0 ** (max(-6.0, min(6.0, g_db)) / 20.0)
                    if abs(g_db) > 6.0:
                        notes_extra += "｜ 前奏电平对齐限幅 %+.1f dB" % g_db
                    pre = pre * g
                tail = tail + pre * f2                           # 前段原位保留 + 前奏渐入
                pk = float(tail.abs().max())
                if pk > 0.995:
                    tail = tail * (0.995 / pk)
                    notes_extra += "｜ 交叉窗峰值 %.3f 已按护栏回退" % pk
                out = torch.cat([out[..., :-m], tail], dim=-1)
            out = torch.cat([out, w[..., ent:]], dim=-1)
        else:
            # —— 旧缩短语义（兼容/对照；⚠ 每缝使后段音频提前 cross 秒）——
            if n > 0:
                m = min(n, int(out.shape[-1]), int(w.shape[-1]))
                t = torch.linspace(0.0, 1.0, m, device=out.device, dtype=torch.float32)
                if curve == "qsin":                       # 等功率（四分之一正弦）
                    f1, f2 = torch.sin(t * math.pi / 2.0), torch.cos(t * math.pi / 2.0)
                elif curve == "tri":                      # 线性（旧口径，仅对照；不相关内容掉 −4.7 dB）
                    f1, f2 = 1.0 - t, t
                else:
                    raise ValueError("未知交叉曲线 %r（只认 qsin / tri）。" % (curve,))
                out = torch.cat([out[..., :-m],
                                 out[..., -m:] * f1 + w[..., :m] * f2,
                                 w[..., m:]], dim=-1)
            else:
                out = torch.cat([out, w], dim=-1)
    lens = [int(w.shape[-1]) for w in ws]
    total = int(out.shape[-1])
    shaped = out.reshape(1, ch0, total).to(dt0) if dt0 is not None else out
    if align_n > 0:
        exp_total = int(round(float(segment_seconds) * sr0)) * len(ws) - (len(ws) - 1) * align_n
        mode = "J-cut 守恒（align=%.3fs/缝，与裁后视频等长）" % al_s
    elif n == 0:
        # 🔴 2026-09-21：cross=0 ⇒ 不存在任何缩短。旧文案把它一并叫作「⚠ 缝后音画错位」
        #   是**假警告**，会让用户以为拼坏了 —— 这条正是「默认档」，必须如实说清。
        exp_total = sum(lens)
        mode = "等长拼接（不做交叉；等效于段文件首尾相接，零 A/V 位移）"
    else:
        exp_total = sum(lens) - (len(ws) - 1) * n
        mode = "旧缩短语义（⚠ 缝后音画错位 cross 秒，仅兼容/对照）"
    rep = ("[H3 Relay] 音频拼接：%d 段 → %d 样本（%.3fs）｜ %s\n"
           "           去 priming %.1f ms/段 ｜ 段有效时长 %s ｜ %s 交叉 %.1f ms/缝 ｜ "
           "段长(样本) %s ｜ 采样率 %d ｜ 声道 %d ｜ 长度%s%s"
           % (len(ws), total, total / float(sr0), mode,
              int(prime_samples) / float(sr0) * 1000.0,
              ("%.3fs" % segment_seconds) if segment_seconds > 0 else "全量",
              curve, n / float(sr0) * 1000.0, lens, sr0, ch0,
              ("守恒 OK" if total == exp_total else "⚠ 不守恒（期望 %d）" % exp_total),
              notes_extra))
    return {"waveform": shaped, "sample_rate": sr0}, rep
