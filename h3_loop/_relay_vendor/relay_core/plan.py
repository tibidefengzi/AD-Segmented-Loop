# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 ComfyUI-H3-Latent-Relay contributors
# 第三方出处与许可见 THIRD-PARTY-NOTICES.md
"""relay_core.plan —— 续接计划（RelayPlan / plan_relay / apply_relay）· 音频参考窗自动定长 · 漂移对策。

本模块由 `relay_core.py` 拆分而来（2026-10-07）；代码**逐字搬运**，未改任何算式。
对外仍由 `relay_core/__init__.py` 全量再导出 ⇒ 旧调用点（`relay_core.xxx`）不变。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
import math
import torch

from ._grid import AUDIO_HZ, FPS, GUIDE_RUNS, pixel_frames, snap_guide_run, steps_for_frames
from .latent import audio_from_latent, audio_tail_from_latent, video_from_latent, video_tail_from_latent

#: 音频参考窗**自动**算法的阈值（0.6.25）。语义见 `auto_audio_ref_seconds`。
#:
#: 🔵 **官方口径：音频参考建议 2~12 秒**（本仓作者 2026-10-06 确认）。本包这两个数**故意**这样取：
#:   · `MIN_VOICED_S = 2.0` —— **对齐官方下限**（"从尾往前累计够 2 秒**有声**内容就停"的起步量）。
#:   · `MAX_S = 6.0` —— **低于官方上限 12 秒**，理由两条，都不是"音色不需要更长"：
#:     ① 参考行是 `PackedLayout` 里的**常驻行**、随**每一步采样**参与 ⇒ 越长越吃**显存**；
#:     ② 同一机制决定**速度**（report 文案原文：「参考行随每一步采样，长参考=慢」）。
#:     ⇒ 取 6 s 是"音色够用"与"显存/速度"的**折中**，不是官方建议值。
#:   ⇒ 想要更长：显式填 `audio_ref_seconds`（本包不拦，代价由使用者承担）；官方上限 12 s 也在此列。
#: ⚠️ 改这两个默认值前先读 `auto_audio_ref_seconds` 与 `AUDIO_REF_VOICE_REL` 处的横评实测。
AUDIO_REF_MIN_VOICED_S: float = 2.0
AUDIO_REF_MAX_S: float = 6.0
#: 「有声」判据 = `P99(每格 RMS 的一阶差分) × 本值` —— **相对量**（2026-10-04 的教训：
#: 绝对 dBFS 阈值在底噪高的素材上会整段静默失效，见 `AUDIO_DECLICK_QUIET_REL`）。
#: ⚠️ **必须是「差分」而不是 RMS**：真实路径拿到的是 **VAE 音频 latent**（已归一化），
#:    静音段的 RMS 与语音段**同量级** ⇒ 用 RMS 判在真实素材上得到「有声格 = 0」。
#: 🔴 **必须用高分位（P99）而不是 `P50`/`max`**（2026-10-04 四组判据横评实测，见下表）：
#:    · `P50×2`：阈值被**静音格的差分尾巴**压低 ⇒ 静音格大量误判成有声。实测同一段
#:      「尾部 2 秒有声 + 8 秒静默」素材，窗从真值 2.00 s 漂到 **6.69 s**（静默占比越大越糟）。
#:    · `max×0.05`：**一个离群脉冲就把阈值顶到天花板**。实测在真实 latent 上注入一个
#:      「1 ms × 30 倍」的脉冲（真实音频里本来就有瞬态）⇒ 有声格从 215 掉到 **2**、窗 6.53→3.30 s。
#:    · **Otsu（log 域）**：抗离群好，但真实波形上抓得过紧（2.0~3.0 s），达不到
#:      `min_voiced_s` 的目标（那正是本算法存在的理由）。
#:    · **`P99×0.20`：唯一全场景不崩**。横评实测（真值 2.00 s 的一栏）：
#:      合成「有声2s+静默 1/3/8/18s」四档 ⇒ 全 **2.00 s**（`P50×2` 分别漂到 2.83/4.98/6.69）；
#:      真实 latent 注入 1ms×30、1ms×100、10ms×20、50ms×20 四种离群 ⇒ 全 **6.53 s**（`max×0.05` 全崩到 3.30）；
#:      真实波形 6 份 ⇒ 2.04~6.51 s（`min_voiced_s=2` 都能满足）。
#:    P99 本身就**裁掉最高 1%** ⇒ 离群脉冲进不了基准，这是它抗噪的原因。
AUDIO_REF_VOICE_REL: float = 0.20


def _voiced_grids(wf: Any, sr: int) -> Tuple[Any, int, int, float, float, str]:
    """算「有声格」下标 —— **本文件唯一的有声判据**（单一真相源）。

    判据 = 每格 RMS 的**一阶差分** > `P99(差分) × AUDIO_REF_VOICE_REL`。

    返回 ``(idx, n1, w1, thr, peak, reason)``：

      · ``idx``     有声格下标（numpy int 数组，升序）；判不了时是**空数组**
      · ``n1``      格数（``n // w1``）｜ ``w1`` 每格样本数 ｜ ``thr`` 用的阈值
      · ``peak``    每格 RMS 的峰值（给「绝对静音」那道**物理**闸用）
      · ``reason``  **空字符串 = 判据可用**；非空 = 为什么用不了（**不准静默当"安静"**）

    🔴 **为什么抽成本函数**（2026-10-05）：`auto_audio_ref_seconds`（原料 = 上一段音频尾）
       与 `anchor_window_start`（原料 = 声锚）**必须**用同一套判据 —— 两处各写一份就是
       "改一处漏一处"的经典病（本仓明文禁止）。本函数是**纯提取**，数值与失败分支逐字保留。
    """
    import numpy as np

    # ⚠️ **显式求积**，不用 `reshape(-1, T)`：`T == 0`（空音频）时 `-1` 与 0 长维**歧义**
    #    ⇒ 抛 `RuntimeError` 炸链。2026-10-04 已在同文件修过三处同款写法，这里是第四处。
    _t = int(wf.shape[-1])
    _c = 1
    for _d in tuple(int(v) for v in wf.shape[:-1]):
        _c *= _d
    _wf = wf.reshape(_c, _t) if _c else wf.reshape(1, _t)
    n = _t
    _none = np.zeros(0, dtype=np.int64)
    # 🔴 `sr <= 0` 会让 `int(0.001 * sr)` 之后的 `_gps = sr / w1` **除零** ⇒ 显式挡掉
    #    （病态输入不该炸链，也不该静默给一个数）。
    if n <= 0 or int(sr) <= 0:
        return _none, 0, 1, 0.0, 0.0, "采样率或长度为 0 ⇒ 无从判断"
    w1 = max(1, int(0.001 * int(sr)))
    n1 = n // w1
    if n1 <= 1:
        return _none, int(n1), int(w1), 0.0, 0.0, "素材太短"
    x = _wf.detach().to(torch.float32)
    # 🔴 **非有限输入 fail-closed**（与 `declick_transients` 同款纪律，2026-10-04）：
    #    含 NaN/Inf 时 `quantile` 返回 NaN ⇒ 阈值 NaN ⇒ 后面一律落空，而"落空"会被
    #    读成"素材很安静" ⇒ **静默降级**。这里显式判掉，并说清是哪一种。
    if not bool(torch.isfinite(x).all()):
        return _none, int(n1), int(w1), 0.0, 0.0, "音频含 NaN/Inf ⇒ 拒绝据此决定窗长"
    t = x[..., :n1 * w1].reshape(int(x.shape[0]), n1, w1)
    rms = (t ** 2).mean(dim=2).sqrt().max(dim=0).values       # 每格 RMS，跨声道取最大
    r = rms.detach().to("cpu").numpy()
    peak = float(np.max(r))
    # 🔴 判据必须用**一阶差分**而不是 RMS（2026-10-24 实测踩出来的）：真实路径拿到的是
    #    **VAE 音频 latent，不是波形** —— latent 经过归一化，**静音段的 RMS 与语音段同量级**
    #    ⇒ 用 `RMS > P50×N` 判，真实素材上得到「有声格 = 0、阈值 0.00000」**整段判不出声音**。
    #    唯一在 latent 空间也成立的区分是**变化率**：语音段的 latent 逐格变化快。
    #    （差分判据对真实波形同样有效 —— 语音处波形变化快、静处变化小 ⇒ 一套代码两条路都覆盖。）
    d = np.abs(np.diff(r, prepend=r[:1]))
    # 🔴 基准取 **P99** 而非 max/P50（横评实测，理由见 `AUDIO_REF_VOICE_REL` 的注释）：
    #    max 会被单个离群脉冲顶死；P50 会被静音格的差分尾巴压低。
    thr = float(np.quantile(d, 0.99)) * float(AUDIO_REF_VOICE_REL)
    idx = np.flatnonzero(d > thr)                              # 有声格（升序）
    if not len(idx):
        return _none, int(n1), int(w1), thr, peak, "整段低于有声阈值（静默素材）⇒ 不给参考"
    return idx, int(n1), int(w1), thr, peak, ""


def auto_audio_ref_seconds(wf: Any, sr: int,
                           min_voiced_s: float = AUDIO_REF_MIN_VOICED_S,
                           max_s: float = AUDIO_REF_MAX_S) -> Tuple[float, Dict[str, Any]]:
    """**自动**决定音频参考窗长（秒）—— 让素材自己说话，而不是让人猜一个数。

    🔵 **为什么自动**（0.6.25，本仓作者 的原话：「参考音频的时长太短会对下一段音色还原度不足」）：
       手填秒数是在**两个坏结果之间猜** —— 填短了音色还原不足；填长了模型可能**复述**
       上一段的台词（参考音频里就是那些台词，见 `plan_relay` 的 `audio_ref_seconds`）。
       本函数把「该多长」交给素材回答：**从上一段尾部往前，累计够 `min_voiced_s` 秒的
       有声内容就停**，`max_s` 封顶。

    🔵 **判据全是相对量**（`P99(每格 RMS 的一阶差分) × AUDIO_REF_VOICE_REL`）⇒ 与素材绝对
       电平无关。为什么是 P99 而不是 P50 / max，见 `AUDIO_REF_VOICE_REL` 的横评实测。

    返回 ``(秒数, 明细)``。**明细要进 `plan.notes``**（铁律 15）——
    否则用户无法判断「自动」是按什么定的，也就无法信任它。
    """
    idx, n1, w1, thr, peak, _reason = _voiced_grids(wf, sr)
    empty: Dict[str, Any] = {"grids": int(n1), "voiced_grids": 0, "threshold": float(thr),
                             "clipped": False, "reason": "素材太短"}
    if _reason:
        return 0.0, dict(empty, reason=_reason)
    # 🔴 **绝对静音**是物理事实、不是相对判断（上面所有判据都是相对量）⇒ 单独一道：
    #    峰值 < 1e-6（−120 dBFS）时，参考窗里**没有任何音色信息**，给了只是噪声。
    if float(peak) < 1e-6:
        return 0.0, dict(empty, reason="整段近乎无声（峰值 < 1e-6）⇒ 不给参考")
    # ⚠️ **格长不是恒等于 1 ms**：`w1 = max(1, int(0.001*sr))`，而 latent 路的 `sr` 是
    #    **AUDIO_HZ = 40** ⇒ `int(0.04) = 0` ⇒ 被 `max` 兜成 **1** ⇒ **1 格 = 25 ms**。
    #    换算若写死「格数 / 1000 = 秒」就会**差 25 倍**（实测 latent 上把 6.5 s 算成 0.26 s）。
    _gps = float(sr) / float(w1)                                  # 格 / 秒
    need = max(1, int(round(float(min_voiced_s) * _gps)))
    k = min(need, len(idx)) - 1                               # 从尾往前第 k 个有声格
    start = int(idx[-1 - k])
    sec = (n1 - start) / _gps
    clipped = bool(sec > float(max_s))
    if clipped:
        sec = float(max_s)
    return sec, {"grids": int(n1), "voiced_grids": int(len(idx)), "threshold": float(thr),
                 "clipped": clipped, "reason": ""}


# ---------------------------------------------------------------- 续接计划
@dataclass
class RelayPlan:
    """一次续接的完整计划（可打印、可序列化，便于留痕排障）。"""

    applied: bool = False
    span: int = 0                      # 被钉住的像素帧数
    steps: int = 0                     # 被钉住的 latent token 数
    trim: int = 0                      # 下游应裁掉的首部**像素帧**数 = span + settle
    settle: int = 0                    # 其中属于「沉降区」的帧数（不受 5+17k 网格约束）
    indices: List[int] = field(default_factory=list)
    keyframes: List[Dict[str, Any]] = field(default_factory=list)
    audio_ref: Optional[Dict[str, Any]] = None
    anchor_ref: Optional[Dict[str, Any]] = None   # 0.5.0 全局外观锚（refs 路线）
    clipped_channels: Optional[int] = None
    notes: List[str] = field(default_factory=list)

    def summary(self) -> str:
        if not self.applied:
            return "未应用（context_latent 未接）"
        return (
            "钉住 %d 帧 / %d 步，锚位 %d..%d，裁首 %d 帧（含沉降 %d），音频 %s%s"
            % (
                self.span, self.steps,
                self.indices[0] if self.indices else -1,
                self.indices[-1] if self.indices else -1,
                self.trim,
                self.settle,
                ("%d 步" % self.audio_ref["ref_audio_t"]) if self.audio_ref else "关",
                ("，外观锚 %d token" % self.anchor_ref["latent_t"]) if self.anchor_ref else "",
            )
        )


#: 声锚尾窗的**有声中占比下限** —— 低于它 ⇒ 这个窗里大部分不是人声 ⇒ 试用「往前挪」修。
#:
#: 🔵 为什么（2026-10-05，动机与实测见 `anchor_window_start` 的 docstring）：
#:    `_voice_anchor_tail` 一直**盲取文件尾**；而 `auto_audio_ref_seconds` 算长度时是
#:    `sec = (n1 - start) / _gps` —— **一直算到文件末尾** ⇒ 素材末尾有停顿时**停顿留在窗里**。
#:    生产声库实测三段**全部** `tail_clean=false`（尾部 0.925 s 含停顿）。
#:    前沿共识是「参考要落在**真实语音**上」（CosyVoice 先做静音检测再取关键片段）；
#:    而 pin 是**硬约束**（钉进输出流头部）⇒ 把它钉成静音，比软条件不准更糟。
#: ⚠️ **「尾部有停顿」本身不是 bug**（那等于自然句末）⇒ 本闸只治**窗里大部分不是人声**。
ANCHOR_VOICED_MIN_FRAC: float = 0.5
#: 往前挪之后，窗的终点落在「最后一个有声格」之后**留多少余量**（秒）—— 给自然收音留一点。
#: 取 0.10 s：够盖住句末的短促收音，又短到不会把下一个人的话头拉进来。
ANCHOR_VOICED_MARGIN_S: float = 0.10

#: 声锚尾窗的**时域标准差下限** —— 低于它 = 这个锚没有内容（静音 / 常量 / 空）。
#:
#: 判据为什么是"时域方差"而不是"幅值"：`MiniMaxH3AudioVAE.encode` 返回的是**归一化**
#: latent（`(z − mean) / std`）⇒ 静音/常量输入编码出来是**常量场** ⇒ 时域 std ≈ 0。
#: 🔴 **零 ≠ 静音**：归一化空间的原点是 VAE 的**均值点**（"平均音色"），不是"无声"；
#:    所以"接一个全零 latent 当空锚"在语义上并不成立（2026-10-02 实测踩到过）。
#:
#: 标定（2026-10-02，零 GPU，`safetensors.torch.load_file`）：**40 份真实产物** stage 文件的
#: 尾 37 步实测 `std_max ∈ [0.391, 1.332]`（中位 0.675）；全零 latent = **0.0000**。
#: ⇒ 取 `1e-2` = 比真实最小值低 **39×**，与"零"之间有足够判别带（真声锚不可能误伤）。
#: ⚠️ 换底模/换音频 VAE 后若发现误伤，按同法重新标定，别凭感觉改。
VOICE_ANCHOR_MIN_STD = 1e-2


def _voice_anchor_audio(voice_anchor: Any) -> torch.Tensor:
    """从声锚输入取音频流，规范成 [B,C,2,T]。

    接受三种形态（对齐用户最自然的接线方式）：
      - ``VAEEncodeAudio`` 输出：``{'samples': [B,C,2,T]}`` 纯张量（**主流**）
      - AV 联合 latent（NestedTensor / streams）：取第 2 条流
      - list/tuple 已拆流
    """
    samples = voice_anchor.get("samples") if isinstance(voice_anchor, dict) else voice_anchor
    nested = getattr(samples, "tensors", None)
    if nested is not None:
        parts = [t for t in nested if torch.is_tensor(t)]
    elif isinstance(samples, (tuple, list)):
        parts = [t for t in samples if torch.is_tensor(t)]
    elif torch.is_tensor(samples):
        parts = [samples]
    else:
        raise ValueError("voice_anchor 形态不认识：%r" % type(samples))
    if len(parts) >= 2:
        a = parts[1]
    elif len(parts) == 1 and parts[0].ndim == 4:
        a = parts[0]                      # 纯音频 latent（VAEEncodeAudio 的 [B,C,2,T]）
    else:
        raise ValueError("voice_anchor 里找不到音频流（拿到 %d 条流）。" % len(parts))
    if a.ndim == 3:
        a = a.unsqueeze(0)
    if a.ndim != 4:
        raise ValueError("voice_anchor 音频流期望 [B,C,2,T]，得到 %s。" % (tuple(a.shape),))
    return a


def anchor_window_start(a: torch.Tensor, take: int, *,
                        sr: float = AUDIO_HZ,
                        min_frac: float = ANCHOR_VOICED_MIN_FRAC,
                        margin_s: float = ANCHOR_VOICED_MARGIN_S) -> Tuple[int, Dict[str, Any]]:
    """声锚尾窗的**起点**：默认「文件尾往前 `take` 步」；窗里大部分不是人声时**往前挪**。

    返回 ``(start, info)``。info 键：`repaired` / `voiced_frac` / `frac_before` /
    `last_voiced` / `reason` / `note` / `grids` / `voiced_grids`。

    为什么要有它（2026-10-05）：动机与实测见 `ANCHOR_VOICED_MIN_FRAC` 的注释。

    纪律（三条，都是"别把修复变成新的不确定性"）：

    · **只在真的更好时才动** —— 挪完之后的有声中占比必须**严格变大**；否则保持原样。
      素材正常时（窗里大部分是人声）**逐位同旧版**。
    · **判不出有声格**（静默 / 含 NaN/Inf / 太短）⇒ **不动**，把 `reason` 交给调用方；
      真正的坏锚（常量场）仍由 `VOICE_ANCHOR_MIN_STD` 那道闸挡住。
    · **不 raise** —— 这里只做修复与报告。新加硬闸会把"以前能跑"的配置变成炸链。
    """
    total = int(a.shape[-1])
    take = int(take)
    info: Dict[str, Any] = {"repaired": False, "voiced_frac": 0.0, "frac_before": 0.0,
                            "last_voiced": -1, "reason": "", "note": "",
                            "grids": 0, "voiced_grids": 0}
    default = max(0, total - take)
    if take <= 0 or take >= total:
        return default, info                      # 窗 = 整段 ⇒ 没有"挪"的余地
    idx, n1, w1, _thr, _peak, reason = _voiced_grids(a, sr)
    info["grids"] = int(n1)
    info["voiced_grids"] = int(len(idx))
    if reason:
        info["reason"] = reason
        return default, info
    # ⚠️ 本函数只用于**音频 latent**（`sr = AUDIO_HZ = 40`）⇒ `w1 = max(1, int(0.04)) = 1`
    #    ⇒ **1 格 = 1 步**，网格下标与步下标可以直接比。换别的 sr 之前先复核这一行。
    if int(w1) != 1:
        info["reason"] = "网格长 ≠ 1 步（sr=%s）⇒ 本函数不适用，保持原样" % sr
        return default, info
    _in_win = int(((idx >= default) & (idx < default + take)).sum())
    info["frac_before"] = float(_in_win) / float(take)
    info["voiced_frac"] = info["frac_before"]
    if info["frac_before"] >= float(min_frac):
        return default, info                      # ✅ 窗里大部分是人声 ⇒ **不动**（逐位同旧版）
    last = int(idx[-1])
    info["last_voiced"] = last
    margin = max(0, int(round(float(margin_s) * float(sr))))
    start = max(0, min(total - take, last + 1 + margin - take))
    if start == default:
        info["note"] = ("⚠ 声锚尾窗里只有 %.0f%% 是人声（但没有可挪的余地 ⇒ 保持原样）"
                        % (info["frac_before"] * 100.0))
        return default, info
    after = float(int(((idx >= start) & (idx < start + take)).sum())) / float(take)
    info["voiced_frac"] = after
    if after <= info["frac_before"]:
        info["note"] = ("⚠ 声锚尾窗里只有 %.0f%% 是人声（往前挪不会更好 ⇒ 保持原样）"
                        % (info["frac_before"] * 100.0))
        return default, info
    info["repaired"] = True
    info["note"] = ("🔧 声锚尾窗里原本只有 %.0f%% 是人声 ⇒ 终点吸附到「最后一个有声格 + %.2f s」，"
                    "挪到 %.0f%%（原来钉进去的**大部分是停顿**）"
                    % (info["frac_before"] * 100.0, float(margin_s), after * 100.0))
    return start, info


def _voice_anchor_tail(voice_anchor: Any, a_frames: int
                       ) -> Tuple[torch.Tensor, int, float, Dict[str, Any]]:
    """切声锚的尾窗，返回 ``(tail, take, raw_steps, win_info)``。

    取窗逻辑与 ``audio_tail_from_latent`` 相同（向上拓宽到整步、要多少给多少），
    但**直接作用于音频张量** —— 不走 ``audio_from_latent``（那会把 VAEEncodeAudio
    的纯音频 latent 误判成 video-only）。

    🆕 **0.6.28：窗里大部分不是人声时往前挪**（`anchor_window_start`）。
    旧口径是 **盲取文件尾**（`narrow(-1, total-take, take)`），而 `auto_audio_ref_seconds`
    算长度时是 `sec = (n1 - start) / _gps` —— **一直算到文件末尾** ⇒ 素材末尾有停顿时
    **停顿留在窗里**。生产声库实测三段全部 `tail_clean=false`（尾部 0.925 s 含停顿）。
    ⇒ pin 是**硬约束**（钉进输出流头部），把"已说过的声音"钉成静音，比软条件不准更糟。
    ⚠️ 「尾部有停顿」**本身不是 bug**（= 自然句末）⇒ 本闸只治**窗里大部分不是人声**。

    🔴 **内容守卫（0.6.20 补）**：切出来的尾窗还要过一道 fail-closed 检查 ——
    含 `NaN/Inf`、或**时域标准差 < `VOICE_ANCHOR_MIN_STD`**（= 常量场 ⇒ 静音/空锚）一律 raise。
    为什么必须有：声锚的作用是「把**本段说话人**的嗓音当条件」，一个没有内容的锚钉进去 =
    既丢掉音频连续性、又什么都没换来，而**模型不会报错**（症状只在成片里）。
    实测踩点（2026-10-02）：把出词节点的 `LATENT`（空的 AV latent）接到 `voice_anchor`
    ⇒ 全零锚静默生效。⚠️ 想**关**声锚请**拔线**，不要接空 latent。
    """
    a = _voice_anchor_audio(voice_anchor)
    total_t = int(a.shape[-1])
    raw_steps = int(a_frames) / float(FPS) * AUDIO_HZ
    want = int(math.ceil(raw_steps - 1e-9))
    take = min(want, total_t)
    if take < 1:
        raise ValueError("声锚音频为空（总长 %d 步）。" % total_t)
    _start, _win = anchor_window_start(a, take)
    tail = a[:1].narrow(-1, _start, take).clone()
    if not bool(torch.isfinite(tail).all()):
        raise ValueError(
            "声锚含 NaN/Inf —— 不是可用的音频 latent。请检查上游（LoadAudio → VAEEncodeAudio）。")
    # `unbiased=False`：take=1 时无偏 std 是 NaN（会静默漏过），无偏口径下它是 0 ⇒ 正确地被拒。
    spread = float(tail.std(dim=-1, unbiased=False).max())
    if spread < VOICE_ANCHOR_MIN_STD:
        _why = ("这个张量**全为零** —— 很可能是把出词节点的 `LATENT` 输出接到了 `voice_anchor`："
                "那是**空的 AV latent**，不含任何语音。"
                if float(tail.abs().max()) == 0.0 else
                "这个张量在时域上是常量 ⇒ 静音/无声内容，钉进去等于白丢音频连续性。")
        raise ValueError(
            "声锚没有内容：尾 %d 步的时域标准差 = %.5f < %.5f（下限）。\n"
            "    · 声锚要的是**本段说话人的真实语音**（`LoadAudio → VAEEncodeAudio` 的 latent，"
            "≥0.9 秒干净语音）；\n"
            "    · %s\n"
            "    · 想**关掉**声锚 ⇒ 把 `voice_anchor` 这根线**拔掉**（不接 = 逐位同旧版），"
            "不要接一个空的 latent。"
            % (take, spread, VOICE_ANCHOR_MIN_STD, _why))
    return tail, take, raw_steps, _win


def plan_relay(
    latent: Any,
    context_latent: Any,
    trim_frames: int = 22,
    audio_frames: Optional[int] = None,
    audio_ref_seconds: Optional[float] = None,
    settle_frames: int = 0,
    anchor_latent: Optional[Any] = None,
    anchor_frames: int = 5,
    voice_anchor: Optional[Any] = None,
) -> RelayPlan:
    """生成续接计划。

    参数
      latent          本段的目标 latent（提供分辨率 / 步数 / 帧数）
      context_latent  上一段的 AV latent（提供被钉住的尾段）
      trim_frames     钉住的像素帧数，必须在 GUIDE_RUNS 上
      audio_frames    音频参考窗（**像素帧**口径）；默认与视频同窗
      audio_ref_seconds
                      音频参考窗（**秒**口径，用户旋钮）；`>0` 时**覆盖** `audio_frames`。
                      这是「**一段多人只占一个音频参考槽**」的实现入口 —— 见下方 notes。
      settle_frames   沉降帧数（默认 0 = 只裁钉住区）。钉住区之后模型还会先
                      **复现**上一段若干帧才切到本段 prompt，那几帧一并裁掉
                      才能保证拼接处不跳变、上段文字不串入。不受 5+17k 网格
                      约束 —— 裁的是 decode 之后的像素帧，不动 latent 相位。
                      （UI 上的自动检测由 H3RelayTrimAV 做，见 detect_settle）

    分辨率必须一致 —— latent 无法缩放，不一致只能重跑上一段或从本段重启链。
    """
    plan = RelayPlan()
    if context_latent is None:
        plan.notes.append("未接 context_latent：本段不续接（按独立段处理）。")
        return plan

    trim_frames = int(trim_frames)
    settle_frames = max(0, int(settle_frames))
    dst = video_from_latent(latent)
    src = video_from_latent(context_latent)
    w, h = int(dst.shape[4]) * 16, int(dst.shape[3]) * 16
    sw, sh = int(src.shape[4]) * 16, int(src.shape[3]) * 16
    if (sw, sh) != (w, h):
        raise ValueError(
            "context_latent 是 %dx%d，本段是 %dx%d。latent 无法缩放，"
            "续接要求两段同分辨率。\n"
            "    解决：让上一段用同分辨率重跑，或把链从这里重启。"
            % (sw, sh, w, h)
        )
    if int(src.shape[1]) != int(dst.shape[1]):
        raise ValueError(
            "context_latent 有 %d 个通道，本段有 %d 个 —— 不是同一底模产出的 H3 视频 latent。"
            % (int(src.shape[1]), int(dst.shape[1]))
        )

    frame_count = pixel_frames(int(dst.shape[2]))
    if trim_frames not in GUIDE_RUNS:
        # 不静默吸附：改了帧数就是改了续接窗口，必须让你知道。
        near = snap_guide_run(trim_frames)
        hint = ("最接近的合法窗口是 %d。" % near) if near else "没有比它更小的合法窗口。"
        raise ValueError(
            "%d 帧不是 H3 的整步续接窗口。\n"
            "    合法值：%s（= 5 + 17k，配 17k+5 帧段长时尾段起点正好落在 5 步周期边界）。\n"
            "    %s"
            % (trim_frames, ", ".join(str(g) for g in sorted(GUIDE_RUNS) if g >= 5), hint)
        )
    if trim_frames >= frame_count:
        raise ValueError(
            "钉住 %d 帧、本段只有 %d 帧 —— 没有新内容可生成。\n"
            "    缩短窗口或加长本段。" % (trim_frames, frame_count)
        )
    if trim_frames + settle_frames >= frame_count:
        raise ValueError(
            "钉住 %d 帧 + 沉降 %d 帧 = %d 帧 ≥ 本段 %d 帧 —— 裁完就没画面了。\n"
            "    调小沉降帧数，或加长本段。"
            % (trim_frames, settle_frames, trim_frames + settle_frames, frame_count)
        )

    blocks, offsets, covered = video_tail_from_latent(context_latent, trim_frames)
    plan.span = covered
    plan.steps = len(blocks)
    plan.indices = list(offsets)
    plan.settle = settle_frames
    # pin 受 5+17k 网格约束（上面已硬校验）；settle 裁的是像素帧，不影响 latent 相位。
    plan.trim = covered + settle_frames
    if settle_frames:
        plan.notes.append(
            "沉降 %d 帧：钉住区之后模型还会先复现若干帧才切到本段 prompt，"
            "这几帧一并裁掉，避免拼接处跳变与上段文字串入。" % settle_frames
        )

    plan.keyframes = [
        {"resolved_frame_index": int(p), "latent": blk}
        for p, blk in zip(plan.indices, blocks, strict=False)
    ]

    # 🔵 **音频参考窗**（0.6.25）：优先级 `audio_ref_seconds`（秒 / 用户旋钮）> `audio_frames`（帧 / 兼容）
    #    > 视频钉住窗（默认 ⇒ **逐位保持旧行为**）。
    #
    #    动机 —— 「**一段多人只占一个音频参考槽**」（2026-10-04 端到端实测 + 零 GPU 复现）：
    #    旧口径下窗长 = 视频钉住窗（实测 22 帧 ⇒ **0.925 s**）⇒ 只够装**最后一个人**的一句，
    #    段里其余人**没有任何音色参考**。而 ① 窗长是**独立参数**（不与视频窗绑死）、
    #    ② 原料**就是上一段的整段音频**（`KEY_EXPORT_TAIL_AUDIO` 在宿主里**并不存在**
    #    ⇒ `audio_tail_from_latent` 走 `audio_from_latent` 分支，实测能取到**整段 6.575 s**）
    #    ⇒ **拉长就能让一个槽装下多人的音色**，且**不多占额度**。
    #
    #    ⚠️ **代价必须实测确认**（不要凭推理落地）：参考音频里**就是上一段的台词**
    #    ⇒ 模型可能顺着**复述**。本包只负责把窗拉长，复述与否由模型决定。
    _a_sec = float(audio_ref_seconds or 0.0)
    _a_auto: Any = None
    _a_auto_reason = ""
    _a_auto_from = ""
    if _a_sec > 0.0:
        audio_frames = int(math.ceil(_a_sec * FPS))
    elif _a_sec == 0.0 and audio_frames is None:
        # 🔵 **自动**（0.6.25 的默认，`0` = 让素材自己决定窗长）。用户原话：
        #    「参考音频的时长太短会对下一段音色还原度不足」—— 手填秒数是在两个坏结果之间猜。
        # 🔵 **接了声锚也自动（0.6.27）** —— 自动算法只认「**给它的那段音频**的有声尾巴」，
        #    与"原料是谁"无关 ⇒ 直接喂**声锚自身**即可。此前接了声锚就退回旧口径
        #    （= 视频钉住窗 0.925 s）⇒ 用户把一段**多人录音**接进声锚时**只有最后一个人的
        #    音色进得来**。这同时是「锚太短」（0.917 s vs 业界 3–10 s）在**节点内**的修法。
        # ⚠️ 与旧口径比 **步数**（`trim_frames` 换算的音频步 = 地板）：**短锚逐位同旧版**，
        #    只有更长的锚才会把参考窗拉长 ⇒ 老用户零行为变化。
        if voice_anchor is not None:
            # 🔵 **锚路径取满上限**（本仓作者 2026-10-04：「单人参考越长，音色保持越好」）：
            #    锚是**用户自己给的参考素材**（不是上一段的台词）⇒ 复述风险低、没有"够用就停"
            #    的理由 ⇒ 把 `min_voiced_s` 直接抬到上限，让窗长**只受 `AUDIO_REF_MAX_S` 封顶**。
            #    （与"上一段音频尾"那条路**故意不同**：那条越长越可能复述 ⇒ 保持 2 秒地板。）
            try:
                _auto, _ai = auto_audio_ref_seconds(
                    _voice_anchor_audio(voice_anchor), AUDIO_HZ, min_voiced_s=AUDIO_REF_MAX_S)
            except Exception as _e:                            # 读不出声锚 ⇒ 退回旧行为
                _auto, _ai = 0.0, {"reason": "读不出声锚音频（%s）" % type(_e).__name__}
            _a_auto_from = "声锚"
            _a_min_s = float(AUDIO_REF_MAX_S)                  # 锚路径取满上限（见上）
        else:
            try:
                _auto, _ai = auto_audio_ref_seconds(audio_from_latent(context_latent), AUDIO_HZ)
            except Exception as _e:                            # 读不出上一段音频 ⇒ 退回旧行为
                _auto, _ai = 0.0, {"reason": "读不出上一段音频（%s）" % type(_e).__name__}
            _a_auto_from = "上一段音频尾"
            _a_min_s = float(AUDIO_REF_MIN_VOICED_S)
        _pin_steps = int(math.ceil(trim_frames / float(FPS) * AUDIO_HZ - 1e-9))
        _auto_steps = int(math.ceil(_auto * AUDIO_HZ - 1e-9)) if _auto > 0.0 else 0
        if _auto_steps > _pin_steps:
            audio_frames = int(math.ceil(_auto * FPS))
            _a_auto = (_auto, _ai, _a_auto_from, _a_min_s)
        else:
            _a_auto_reason = (
                "%s 只有 %.2f 秒（≤ 视频钉住窗 %.3f 秒）⇒ 保持旧口径"
                % (_a_auto_from, _auto, trim_frames / float(FPS)) if _auto > 0.0
                else str(_ai.get("reason") or "自动定长失败"))
    a_frames = int(audio_frames) if audio_frames else trim_frames
    src_total_frames = pixel_frames(int(src.shape[2]))
    overhang, grid_off = 0.0, False
    if voice_anchor is not None:
        # 0.6.1 声锚（voice anchor）：audio_ref 改用**指定说话人**的音频尾窗。
        # 动机（2026-10-02 实测）：audio_ref = 上段音频尾 ⇒ 上段说话人的嗓音
        # 成为**本段嗓音的生成条件** —— 台词逐段换人时音色交叉污染
        # （实测：某角色 F0 114→131、谱质心 1028→1308）。给声锚后离基准距离缩到 1/5。
        # 静默降级纪律：声锚短于窗就取全长，并把「用了声锚」写进 notes 让 report 可见。
        tail, rt, raw_steps, _vwin = _voice_anchor_tail(voice_anchor, a_frames)
        if rt < int(math.ceil(a_frames / float(FPS) * AUDIO_HZ - 1e-9)):
            plan.notes.append(
                "⚠ 声锚音频只有 %d 步（窗口要 %d 步）⇒ 已按全长取用；"
                "建议声锚 ≥ %.2f 秒。" % (rt, int(math.ceil(
                    a_frames / float(FPS) * AUDIO_HZ)), a_frames / float(FPS)))
        plan.notes.append(
            "声锚生效：audio_ref 改用 voice_anchor 尾 %d 步（约 %.2f 秒），"
            "不再取上一段音频尾 —— 用于跨说话人续接时锁定本段说话人音色。"
            % (int(rt), int(rt) / AUDIO_HZ))
        # 🆕 0.6.28 窗尾吸附：窗里大部分不是人声时往前挪（见 `anchor_window_start`）。
        #    修了就要说；没修但有话要说（比如"挪不动"）也要说 —— 铁律 15：不许静默。
        if _vwin.get("note"):
            plan.notes.append(_vwin["note"])
    else:
        tail, rt, overhang, raw_steps, grid_off = audio_tail_from_latent(
            context_latent, a_frames, src_total_frames)
    if grid_off:
        plan.notes.append(
            "⚠ 音频栅格与视频帧数偏差超出半整步（上段 %d 帧，音频栅格换算后与之对不上），"
            "已按无外溢处理——输入段可能不是标准 H3 网格，请检查上一段来源。"
            % src_total_frames
        )
    if rt > raw_steps + 1e-6:
        plan.notes.append(
            "音频窗 %d 帧换算 %.2f 步，已拓宽到 %d 整步（宁多带、不截短）。"
            % (a_frames, raw_steps, rt)
        )
    dst_audio_t = None
    try:
        dst_audio_t = int(audio_from_latent(latent).shape[-1])
    except ValueError:
        dst_audio_t = None
    if dst_audio_t is not None and rt > dst_audio_t:
        plan.notes.append(
            "音频窗口 %d 步超过本段音频栅格 %d 步，已截断。" % (rt, dst_audio_t)
        )
        tail = tail[..., :dst_audio_t].clone()
        rt = dst_audio_t
    plan.audio_ref = {"kind": "audio", "ref_audio_t": int(rt), "audio_latent": tail}
    # 🔵 音频参考窗的**诚实口径**（铁律 15：退化分支与自动决策都必须可观测）：
    #    设了旋钮却**没生效**（被声锚接管 / 素材本身不够长）、以及**自动**到底按什么定的，
    #    都要说出来 —— 否则用户以为"配了多人音色"，实际参考里只有最后一个说话人。
    if _a_sec > 0.0:
        if voice_anchor is not None:
            _want = int(math.ceil(_a_sec * AUDIO_HZ))
            _tail_note = ("声锚比它短 ⇒ 已按声锚全长取用。" if int(rt) < _want
                          else "声锚够长 ⇒ 按旋钮取用。")
            plan.notes.append(
                "音频参考窗：已接声锚 ⇒ 参考音频取自**声锚**（实际长 %.2f 秒；"
                "旋钮 `audio_ref_seconds=%.2f` 要 %.2f 秒）—— %s"
                % (int(rt) / float(AUDIO_HZ), _a_sec, _a_sec, _tail_note))
        else:
            plan.notes.append(
                "音频参考窗 = **%.2f 秒**（%d 步；旋钮 `audio_ref_seconds`）—— 取自**上一段音频尾**"
                "⇒ 一个参考槽里可含**多个说话人**的音色；"
                "⚠️ 参考音频含上一段的台词，模型**可能复述** —— （2026-10-04 端到端 GPU 实测：窗 0.93→4.18 秒（4.4×，覆盖说话片段 4→17 个）⇒ ASR 判据下**未复述**；对照实验同一判据对 seg1 样本 3/3 命中 ⇒ 判据灵敏。⚠️ 仅一段素材一次生成，宿主非确定性）。"
                % (int(rt) / float(AUDIO_HZ), int(rt)))
    elif _a_auto is not None:
        _asec, _ai, _src, _mins = _a_auto
        _clip = ("，⚠ 已达上限 %.1fs" % float(AUDIO_REF_MAX_S)) if _ai.get("clipped") else ""
        _grow = ((int(rt) / float(AUDIO_HZ)) / max(1e-9, trim_frames / float(FPS)))
        # 复述风险按**原料**分别陈述（铁律 15：不许把一种原料的风险说成另一种的）。
        _rep = ("⚠️ 参考音频**就是该说话人自己的语音** ⇒ 复述风险远低于「上一段台词」，"
                "所以窗长**取满上限**（%.1f s）—— 单人参考越长、音色保持越好。"
                % float(AUDIO_REF_MAX_S)
                if _src == "声锚" else
                "⚠️ 参考音频含上一段的台词，模型**可能复述** —— （2026-10-04 端到端 GPU 实测："
                "窗 0.93→4.18 秒（4.4×，覆盖说话片段 4→17 个）⇒ ASR 判据下**未复述**；"
                "对照实验同一判据对 seg1 样本 3/3 命中 ⇒ 判据灵敏。⚠️ 仅一段素材一次生成，宿主非确定性）。")
        plan.notes.append(
            "音频参考窗 = **自动 %.2f 秒**（%d 步）—— 从**%s**往前累计够 %.1f 秒**有声**内容"
            "（有声判据 = 每格 RMS 一阶差分 > P99×%.2f，%d/%d 格有声%s）。"
            "⇒ 比旧口径（= 视频钉住窗）长 %.2fx，**一个参考槽里可含多个说话人**的音色。%s"
            % (_asec, int(rt), _src, _mins, float(AUDIO_REF_VOICE_REL),
               int(_ai.get("voiced_grids", 0)), int(_ai.get("grids", 0)), _clip, _grow, _rep))
    elif _a_auto_reason:
        plan.notes.append("音频参考窗：自动定长**未生效**（%s）⇒ 退回旧口径 = 视频钉住窗"
                          "（%.3f 秒）。" % (_a_auto_reason, trim_frames / float(FPS)))
    if overhang:
        plan.notes.append("音频栅格外溢 %.3f 步（已在放置时对齐）。" % overhang)

    if anchor_latent is not None:
        # 0.5.0 全局外观锚：refs 路线（异分辨率合法，见 build_anchor_ref 注释）。
        # 锚失败绝不静默降级——refs 丢了 = 长程一致性悄悄失效，必须让用户看见。
        try:
            plan.anchor_ref = build_anchor_ref(anchor_latent, int(anchor_frames))
        except RuntimeError as e:
            raise ValueError(
                "外观锚构建失败（anchor_frames=%d）：%s" % (int(anchor_frames), e)
            ) from e
        aw, ah = int(plan.anchor_ref["latent_w"]) * 16, int(plan.anchor_ref["latent_h"]) * 16
        plan.notes.append(
            "全局外观锚：%d 帧 refs 块（%dx%d%s）——全程骑乘每步，长程身份/色档基准。"
            % (int(anchor_frames), aw, ah,
               "，异分辨率" if (aw, ah) != (w, h) else ""))

    plan.applied = True
    return plan


def _anchor_position(anchor):
    """一个锚在目标时间轴上的落点（ComfyUI 原生字段；缺失按 0 处理）。"""
    return int(anchor.get("resolved_frame_index", 0))


def _partition_anchors(anchors, zone_end):
    """按落点把锚切成 (钉住区内, 钉住区外) 两组，两组都是副本。

    钉住区 = 本段开头那 ``zone_end`` 帧。它由上一段的 latent 逐位决定，
    详见 ``build_continue_latent`` 的掩码语义。
    """
    inside, outside = [], []
    for anchor in anchors:
        bucket = inside if _anchor_position(anchor) < zone_end else outside
        bucket.append(dict(anchor))
    return inside, outside


def count_official_audio_refs(conditioning) -> int:
    """数 conditioning 里**已存在的**音频参考块（官方 ``ref_audios`` 通道写进来的）。

    🔴 为什么后端也要数：前端提示只覆盖**画布**用法（脚本提交 / API 用户看不到画布）⇒
    report 是**权威口径**，前端是**显性补充**。两边说同一件事、数字同源（都按 `kind="audio"` 数）。
    多个 conditioning 条目时取**最多**的那个（官方节点只写一个条目，取 max 最不容易低估）。
    """
    best = 0
    for _emb, extra in (conditioning or ()):
        for blk in ((extra or {}).get("minimax_refs") or ()):
            if isinstance(blk, dict) and blk.get("kind") == "audio":
                best += 1
    return best


def apply_relay(conditioning, plan: RelayPlan):
    """把续接计划写进 conditioning —— 本包与 ComfyUI 原生协议之间的唯一出口。

    出口按顺序只做三件事：

    1. **锚位合成**：conditioning 上可能已经挂着别人放的锚（官方 AddGuide、
       上游末帧锚）。本段不替换它们，而是把旧锚与本次新增的锚合成一张新表，
       旧锚保持原有相对次序排在前面。
    2. **钉住区去重**：本段开头 ``plan.span`` 帧由上一段的 latent 逐位决定，
       任何人再往这段区间里放锚，都是对同一批帧的重复声明。重复声明在此出局，
       落点记进 report —— 静默丢会让「少了一个锚」事后无从查起。
    3. **ref 追加**：音频与外观锚走 ``minimax_refs``，追加写回，
       上游已有的 ref 不受影响。

    ``plan.applied`` 为假时原样返回（首段没有前序可接）。
    """
    import node_helpers

    if not plan.applied:
        return conditioning

    zone_end = int(plan.span)
    out, redundant = [], []

    for emb, extra in conditioning:
        merged = dict(extra)
        inside, outside = _partition_anchors(
            merged.get("minimax_keyframes") or [], zone_end)
        redundant.extend(_anchor_position(a) for a in inside)
        merged["minimax_keyframes"] = outside + [dict(a) for a in plan.keyframes]
        out.append([emb, merged])

    if redundant:
        plan.notes.append(
            "钉住区（0..%d）内的既有锚 %s 是重复声明，已出局（共 %d 个）。"
            % (zone_end - 1, sorted(set(redundant)), len(set(redundant)))
        )

    if plan.audio_ref is not None or plan.anchor_ref is not None:
        refs = [r for r in (plan.audio_ref, plan.anchor_ref) if r is not None]
        # 📌 音频额度**在注入之前**数（官方块此刻还在 conditioning 里）⇒ 报告里的数字是「本段总共几个」。
        #    外观锚是 `kind="video"`，不占音频额度 ⇒ 只数 `kind="audio"`。
        _off = count_official_audio_refs(conditioning)
        _own = 1 if plan.audio_ref is not None else 0
        _tot = _off + _own
        if _own:
            # 🔴 2026-10-04 调研结论（读码确证，正文见 docs/07 §声锚·一段内多人）：官方那条路
            #    （`ref_audios`）的每个音频参考都会在**文本呈现**里拿到一个标签 `<Audio j>`
            #    （`comfy/text_encoders/minimax.py:178`；标签与 DiT 块**同序同数**，
            #    两个第三方包 T8 / csglide 也是这个范式，且提示词**不引用**就不送参考）。
            #    而本包这块是 `append=True` **事后追加**到 DiT 侧 `minimax_refs` 的 ——
            #    tokenize 早就发生过 ⇒ **文本侧没有它的标签、prompt 引不到它**。
            #    ⇒ 不写这一行，用户会以为"我给了锚 ⇒ prompt 里能引用它"，而那是错的。
            plan.notes.append(
                "本包注入的音频参考是**事后追加**的（DiT 侧第 %d 个，排在官方 %d 个之后）："
                "文本侧**没有**对应的 `<Audio j>` 标签 ⇒ **prompt 引不到它**，"
                "它只作为条件行被模型看到。\n"
                "           要让某个声音变成**可被 prompt 引用**的参考（一段内多人的正路），"
                "把该音频接**官方** `ref_audios` 槽 —— 编号按**已接线顺序**数 1..N（**不是槽号**），"
                "长度先用官方 `TrimAudioDuration` 裁到 ~0.9 s 再接（参考行随每一步采样，长参考=慢）。"
                % (_off + 1, _off))
        if _tot > AUDIO_REF_OFFICIAL_MAX:
            plan.notes.append(
                "🔴 音频参考 **%d 个**，超过官方 `ref_audios` 上限 %d（官方 %d + 本包 %d）。\n"
                "           模型层不拦（refs 是变长累加），但**超出官方口径、我们未实测** ⇒ "
                "画面/音色可能与官方建议范围不同。\n"
                "           要腾额度：把参考节点的 `ref_audio_*` 槽位减下来 —— "
                "本包这 1 个是**续接音频**（声锚 / 上一段尾窗，二选一，**没有「完全不用」的开关**）。"
                % (_tot, AUDIO_REF_OFFICIAL_MAX, _off, _own))
        elif _tot == AUDIO_REF_OFFICIAL_MAX and _own:
            plan.notes.append("音频参考 %d 个，正好用满官方上限 %d（官方 %d + 本包 %d）。"
                              % (_tot, AUDIO_REF_OFFICIAL_MAX, _off, _own))
        out = node_helpers.conditioning_set_values(
            out, {"minimax_refs": refs}, append=True
        )
    return out


# ---------------------------------------------------------- 0.5.0 漂移对策
def match_stats(
    src: torch.Tensor,
    ref: torch.Tensor,
    blend: float = 1.0,
    gain_min: float = 0.5,
    gain_max: float = 2.0,
) -> torch.Tensor:
    """逐通道一阶+二阶矩对齐（Reinhard 2001 统计迁移的 latent 域版）。

    把 ``src`` 的每通道均值/标准差拉向 ``ref``：
    ``out = (src - μ_src) * clamp(σ_ref/σ_src, 0.5, 2.0) + μ_ref``，
    再按 blend 与原文混（blend=1 全量校正，0 = 原样）。

    用途是**纠偏被裁掉的钉住前缀**（消耗品条件，不伤上段成片）：模型看到
    的全局统计被拉回锚段色档 → 本段生成内容随Conditioning harmonize 复位漂移。
    增益限幅防呆：σ 比超出 [0.5, 2] 时截断，宁欠矫不炸图（对比：不限幅时
    一段黑场会把 σ_src≈0 的通道增益推到 ∞）。
    """
    if int(src.shape[1]) != int(ref.shape[1]):
        raise ValueError(
            "矩对齐要求通道数一致：src %d ≠ ref %d（不是同一底模的 latent？）"
            % (int(src.shape[1]), int(ref.shape[1]))
        )
    blend = max(0.0, min(1.0, float(blend)))
    if blend == 0.0:
        return src
    dims = tuple(range(2, src.ndim))
    m_s = src.mean(dim=dims, keepdim=True)
    s_s = src.std(dim=dims, keepdim=True)
    m_r = ref.mean(dim=dims, keepdim=True).to(m_s.dtype)
    s_r = ref.std(dim=dims, keepdim=True).to(s_s.dtype)
    gain = (s_r / s_s.clamp(min=1e-6)).clamp(gain_min, gain_max)
    matched = (src - m_s) * gain + m_r
    return src + blend * (matched - src)


def build_anchor_ref(anchor_latent: Any, frames: int) -> Dict[str, Any]:
    """0.5.0 全局外观锚：把锚段（通常第 0 段）**开头** ``frames`` 帧构建为原生
    ``minimax_refs`` 的 ``kind="video"`` 参考块。

    取头不取尾：refs 块在宿主里按"自身第 0 token 起"的相位约定重建时间格
    （model.py ``_video_t_spans`` 从 k%5=0 开始），切片只有从 0 起才天然满足
    该约定；取尾则受锚段总步数相位约束（``video_tail_from_latent`` 的
    start%5==0 检查），换个段长就报"相位错位"。开头切片恒合法，且语义
    更正——第 1 段开场 = 全片的视觉圣经（身份/色档/布光基准）。

    依据宿主源码（2026-09-15 逐行核实）：refs 块打包进 payload 后，其 cond 行
    以近零噪声（VISUAL_COND_TIMESTEP）全程骑乘每一个采样步
    （comfy/model_base.py extra_conds → comfy/ldm/minimax/model.py
    ``_cond_video_rows``/``img_update=zeros``），正对应 LLM 流式生成的
    attention sink——永不退出的外观锚（StreamingT2V/LongLive 的长程一致性
    在黑盒协议层的等价物）。refs 自带独立空间网格（``_frame_grid``），
    **允许与目标段不同分辨率**——这是与 keyframes 路线的关键区别，也是
    「拿第 0 段当锚」跨段可用的前提。
    """
    video = video_from_latent(anchor_latent)
    steps = steps_for_frames(int(frames))
    if steps is None:
        raise ValueError(
            "%d 帧不是 H3 的整步窗口，无法从锚段 latent 切 refs 块。合法值：%s。"
            % (int(frames), ", ".join(str(g) for g in sorted(GUIDE_RUNS) if g >= 5))
        )
    total = int(video.shape[2])
    if steps > total:
        raise ValueError(
            "锚窗 %d 帧（%d 步）超过锚段 latent 的 %d 步——锚段太短，换更小的锚窗。"
            % (int(frames), steps, total)
        )
    z = video[:1, :, 0:steps].contiguous()
    return {
        "kind": "video",
        "latent": z,
        "latent_t": int(z.shape[2]),
        "latent_h": int(z.shape[3]),
        "latent_w": int(z.shape[4]),
        "ref_audio_t": 0,
        "audio_latent": None,
    }


#: 官方 `MiniMaxH3ReferenceToVideo` 的 `ref_audios` 槽数（Autogrow `min=0, max=3`，
#: 见 `comfy_extras/nodes_minimax_h3.py`）。🔴 **本包在 `stage_index ≥ 1` 时恒定占 1 个**
#: （`plan.audio_ref`：声锚尾窗 **或** 上一段音频尾，二选一）⇒ 用户把 3 个槽填满时，
#: 模型侧会看到 **4 个**音频参考。模型层**不拦**（`comfy/ldm/minimax/model.py` 是变长累加、
#: 不校验个数）⇒ 我们**主动在 report 与前端标题上点明**，不静默超口径。
AUDIO_REF_OFFICIAL_MAX: int = 3


# ─────────────────────────── run_id 跨节点同步（0.6.31） ───────────────────────────
# 🔴 为什么后端也要有（2026-10-07：「前端和 API 都要实现」）：
#   `run_id` 决定段文件落在 `output/relay_kit/<run_id>/`。六个节点各存一份，
#   不一致 ⇒ 桥去**错的目录**找上一段 ⇒ 报错（好情况）或**读到上一轮的旧段**（坏情况）。
#   前端有广播（改一处同步全组），**API / 脚本用户没有那条路** —— 只能手改六处。
#   ⇒ 这里给出与前端**同一套语义**的实现，让脚本用户也能"改一处、跑一次"。
#
# ⚠️ 与前端 `web/relay_kit_sync.js` 是**两份实现**（跨层孪生，§二·D）⇒ 语义必须逐条对齐：
#   ① **空值不扩散**（清空一格 ≠ 想清空全组）；② **冲突不猜**（≥2 个不同的非空值 ⇒ 拒绝自动处理）；
#   ③ **被连线接管的格子不广播**（`run_id` 可以是 `[上游id, 槽位]`，那格是"死值"）。
#   对账 = `tests/parity/run_id_cases.json`（**一份 fixture、两侧各跑一遍**）。

#: 带 `run_id` 的节点类型 —— 与前端 `relay_kit_sync.js::RUN_ID_TYPES` **逐项一致**。
#: 🔴 漏一类 = 那一类永远用别的目录名，且**不报错**（与 `STAGE_TYPES` 同一个坑，勿手工维护）。
RUN_ID_TYPES: Tuple[str, ...] = (
    "H3RelayChain",
    "H3RelayCopyBridge",
    "H3RelayLatentSave",
    "H3RelayLatentLoad",
    "H3RelayTrimAV",
    "H3RelayAudioSeam",
    "H3RelayFiniteRun",
    "H3RelaySeedFromVideo",
)

_RUN_ID_TYPE_SET = frozenset(RUN_ID_TYPES)


def _is_run_id_type(class_type: Any) -> bool:
    return str(class_type) in _RUN_ID_TYPE_SET


def _rid(value: Any) -> str:
    """节点的 `run_id` 取值：容忍 `None` / 数字 / 前后空格。"""
    if isinstance(value, (list, tuple)):
        return ""          # 连线（`[上游id, 槽位]`）—— 由 linked 标记，不是值
    return str(value or "").strip()


def collect_run_ids(prompt: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """从 **API 格式**的 prompt 里收集带 `run_id` 的节点。

    @param prompt `{节点id: {"class_type":…, "inputs": {…}}}`
    @return `[{id, type, run_id, linked}]`，`id` 一律 `str()`（前端 id 是字符串，
            数字/字符串混用会让下游 `==` 恒 False —— 同款坑在 2026-09-28 踩过）。
            `linked: True` = 那一格接了线（值由上游决定）⇒ **不许被改写**。
    """
    out: List[Dict[str, Any]] = []
    for nid, spec in (prompt or {}).items():
        if not isinstance(spec, dict):
            continue
        ctype = spec.get("class_type")
        if not _is_run_id_type(ctype):
            continue
        inputs = spec.get("inputs")
        inputs = inputs if isinstance(inputs, dict) else {}
        raw = inputs.get("run_id", "")
        linked = isinstance(raw, (list, tuple))
        out.append({"id": str(nid), "type": str(ctype), "run_id": _rid(raw), "linked": linked})
    return out


def resolve_run_id(members: Optional[List[Dict[str, Any]]]) -> Dict[str, Any]:
    """裁决 `run_id` 是否一致。**不猜**（与前端 `resolveRunId` 同字段、同三态）。

    @return `{state, value, values, ids, empty, by_value}`
      · `state="ok"`       —— 非空值只有一个；`value` 是它，`ids` 是**已有该值**的 id，
                             `empty` 是**还空着**的 id（这些格该补齐）
      · `state="empty"`    —— 全空（后端 `_stage_path` 会 raise「run_id 不能为空」）
      · `state="conflict"` —— ≥2 个不同的非空值；`by_value` = 值 → id 清单
    """
    by_value: Dict[str, List[str]] = {}
    empty: List[str] = []
    for m in (members or []):
        if not m or m.get("id") is None:
            continue
        mid = str(m["id"])
        val = _rid(m.get("run_id"))
        if not val:
            empty.append(mid)
            continue
        by_value.setdefault(val, []).append(mid)
    values = list(by_value.keys())
    if not values:
        return {"state": "empty", "value": "", "values": [], "ids": [], "empty": empty, "by_value": {}}
    if len(values) == 1:
        return {"state": "ok", "value": values[0], "values": values,
                "ids": list(by_value[values[0]]), "empty": empty, "by_value": by_value}
    return {"state": "conflict", "value": "", "values": values,
            "ids": [], "empty": empty, "by_value": by_value}


def plan_run_id_sync(members: Optional[List[Dict[str, Any]]],
                     origin_id: Any, value: Any) -> Dict[str, Any]:
    """规划一次广播（与前端 `planRunIdSync` 同语义）。

    ⚠️ 两条硬规则：① **空值不扩散**（`value` 为空 ⇒ 谁都不改）；
       ② **被连线接管的格子跳过**（改它不生效，写了就是"假同步"，比不改更坏）。
    @return `{targets, value, reason, skipped}`；`reason` ∈ `empty` / `sync` / `none` / `linked`
    """
    v = str(value or "").strip()
    if not v:
        return {"targets": [], "value": "", "reason": "empty", "skipped": []}
    origin = None if origin_id is None else str(origin_id)
    rest = [m for m in (members or []) if m and m.get("id") is not None and str(m["id"]) != origin]
    skipped = [str(m["id"]) for m in rest if m.get("linked") and _rid(m.get("run_id")) != v]
    targets = [str(m["id"]) for m in rest
               if not m.get("linked") and _rid(m.get("run_id")) != v]
    if not targets and skipped:
        return {"targets": targets, "value": v, "reason": "linked", "skipped": skipped}
    return {"targets": targets, "value": v,
            "reason": "sync" if targets else "none", "skipped": skipped}


def sync_run_id(prompt: Optional[Dict[str, Any]], value: Optional[str] = None,
                origin_id: Any = None) -> Dict[str, Any]:
    """端到端：收集 → 裁决 → （必要时）**改写 prompt**。

    给 API / 脚本用户用：把一份 prompt 里的 `run_id` 统一成一个名字。

    @param value 目标名；**省略**时 = 自动取场上唯一的非空值（裁决为 `ok` 时）。
                给了但场上是 `conflict` ⇒ **拒绝改写**（不猜），只在报告里点名冲突。
    @return `{verdict, plan, changed, prompt, report}`
      · `prompt` **只在 `changed` 为真时**是新 dict（`changed=False` 时原样返回，不复制）
      · `report` 是给人看的中文说明（可直接打进跑批日志）

    🔴 为什么"冲突时不猜"：自动挑错的那次会让段文件落进**错的目录**，
      症状是"读到上一轮的旧段" —— 比直接报错难查得多（与前端同一条纪律）。
    """
    members = collect_run_ids(prompt)
    verdict = resolve_run_id(members)
    target = str(value).strip() if value is not None else verdict["value"]

    if verdict["state"] == "conflict" and (value is None or not target):
        lines = ["⚠ 本图 `run_id` 不一致（有 %d 个不同的名字），**没有自动改写**："
                 % len(verdict["values"])]
        for v in verdict["values"]:
            lines.append("  · 「%s」← %s" % (v, "、".join(verdict["by_value"].get(v, []))))
        lines.append("  ⇒ 请显式给一个 `value`，或把它们改成同一个名字。")
        return {"verdict": verdict, "plan": None, "changed": False,
                "prompt": prompt, "report": "\n".join(lines)}

    if not target:
        lines = ["⚠ 本图所有 `run_id` 都是空的 —— 后端落盘时会直接 raise「run_id 不能为空」。",
                 "  ⇒ 用法：`sync_run_id(prompt, value=\"myFilm\")`。"]
        return {"verdict": verdict, "plan": None, "changed": False,
                "prompt": prompt, "report": "\n".join(lines)}

    plan = plan_run_id_sync(members, origin_id, target)
    if plan["reason"] != "sync":
        note = {"none": "已经全是「%s」，无需改。" % target,
                "linked": "目标格子全被连线接管，没有可写之处。",
                "empty": "空值不扩散。"}[plan["reason"]]
        return {"verdict": verdict, "plan": plan, "changed": False,
                "prompt": prompt, "report": note}

    new_prompt = {k: (dict(v) if isinstance(v, dict) else v) for k, v in (prompt or {}).items()}
    for nid in plan["targets"]:
        spec = new_prompt.get(nid)
        if not isinstance(spec, dict):
            continue
        inputs = spec.get("inputs")
        inputs = dict(inputs) if isinstance(inputs, dict) else {}
        inputs["run_id"] = plan["value"]
        spec["inputs"] = inputs
    report = "`run_id` 已统一为「%s」（改了 %d 个节点：%s）。" % (
        plan["value"], len(plan["targets"]), "、".join(plan["targets"]))
    if plan["skipped"]:
        report += "\n  · 跳过（接了线，改本机格子不生效）：%s" % "、".join(plan["skipped"])
    return {"verdict": verdict, "plan": plan, "changed": True,
            "prompt": new_prompt, "report": report}
