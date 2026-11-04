# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 ComfyUI-H3-Latent-Relay contributors
# 第三方出处与许可见 THIRD-PARTY-NOTICES.md
"""relay_core.concat —— 多段拼接成片（PyAV）+ 输出挑选取回 + 提交图定序。

本模块由 `relay_core.py` 拆分而来（2026-10-07）；代码**逐字搬运**，未改任何算式。
对外仍由 `relay_core/__init__.py` 全量再导出 ⇒ 旧调用点（`relay_core.xxx`）不变。
"""

from __future__ import annotations

import ntpath                                      # 只当"Windows 路径判据"用，与宿主平台无关
import os
import torch

from .audio import AUDIO_ENCODER_PRIME_MS, load_audio

# ============================================================================
# —— 多段拼接成片（0.6.7）——
# ============================================================================
# 为什么在**包内**做（而不是让用户敲 ffmpeg）：见 CONTRIBUTING 铁律一·例外（0.6.7）。
# 依赖 PyAV（宿主 ComfyUI 自带 av>=17）—— 全部**延迟 import**，不用拼接的人不受影响。
#
# 口径全部沿用本包自己的既有定义，不另立新说、也不引外部实现：
#   · 段有效时长 = 帧数/fps（= `join_audio_segments` 的 segment_seconds）；
#   · 每段音频头部要去掉**编码器 priming**（`AUDIO_ENCODER_PRIME_MS` = AudioSeam.join_prime_ms 默认）；
#   · 验收四断言 = 本包 0.5.0 起的口径（帧数守恒 / PTS 无洞 / DTS 递增 / A·VΔ ≤ 1 帧）。
#   ⚠ demux 序 = DTS 序 ≠ 显示序 ⇒ 判「PTS 无洞」前必须排序。
#   ⚠ 段文件容器里的音频比视频**长**（实测 +0.0320s@32k = 1024 样本，裁过的段再 +629 尾部填充），
#     是编码器产物、不是错位 ⇒ 拼接必须显式处理，别指望流拷贝替我们校准。
AV_CONCAT_VIDEO_EXTS = (".mp4", ".m4v", ".mov", ".mkv", ".webm")

# 「Trim AV」回显 PCM 边车用的 ui 键（宿主把它原样收进 history.outputs[node_id]）。
#   用回显而不是"按命名约定去翻目录"：段文件的命名是用户定的（SaveVideo 的 filename_prefix），
#   本包不该猜；而节点自己写的文件由它自己报路径，最不容易错。
PCM_UI_KEY = "h3relay_pcm"


def _av_module():
    """延迟拿 PyAV；缺库时给**可照做**的报错。"""
    try:
        import av
        return av
    except Exception as exc:  # pragma: no cover - 取决于运行环境
        raise RuntimeError('多段拼接需要 PyAV（宿主 ComfyUI 的依赖 av>=17）：pip install "av>=17"；'
                           "原始错误：%s" % (exc,)) from exc


def _secs(stream):
    """流的容器时长（秒）；拿不到返回 0.0。"""
    return (float(stream.duration * stream.time_base)
            if (stream is not None and stream.duration) else 0.0)


def _start(stream):
    """流的**容器起始偏移**（秒）；拿不到返回 0.0。

    🔴 为什么需要它（2026-10-08 实测）：**落盘节点会自己决定时间戳**。第三方 Combine 类节点
    给视频轨打一个非零 `start`（实测 `start 0.125` = 3 帧 @24fps），同时**把音频补上等长的
    前置静音** ⇒ 音画尾部仍然对齐、内容也不错位。
    只看 `duration` 会把它算成「音频比视频长 0.125s ⇒ 音频绕过了 Trim AV」⇒ **好段被误判拒拼**。
    ⇒ 判据要用「**视频结束时刻**」（= `start + duration`）去比，不是只用 `duration`。
    """
    try:
        st, tb = stream.start_time, stream.time_base
        return float(st * tb) if (st is not None and tb is not None) else 0.0
    except Exception:          # 流没有时间戳信息（罕见）⇒ 当 0 处理，退回旧口径
        return 0.0


def probe_mp4(path):
    """读段文件的**容器层**事实（不解码画面）。

    ``a_prime_samples`` = 音频比**视频结束时刻**多出来的样本数（priming + 尾部填充），
    同时也是「这条音频线有没有绕过裁重叠」的判据（绕过时 ≈ 裁掉帧数/fps × 采样率）。

    🔴 口径（2026-10-08 改）：比的是**视频的结束时刻** ``v_start + v_seconds``，
    不是 `v_seconds` —— 有些落盘节点给视频轨打非零 `start`（见 `_start`）。
    """
    av = _av_module()
    with av.open(str(path)) as c:
        if not c.streams.video:
            raise RuntimeError("拼接只认带视频流的段文件；%s 里没有视频流。" % path)
        v = c.streams.video[0]
        a = c.streams.audio[0] if c.streams.audio else None
        fps = float(v.average_rate) if v.average_rate else 0.0
        frames, vd = int(v.frames or 0), _secs(v)
        if not frames:                      # nb_frames 缺失时退回逐包数（快，不解码）
            frames = sum(1 for p in c.demux(v) if p.pts is not None)
            vd = frames / fps if fps else vd
        v_start = _start(v)
        ad = _secs(a)
        rate = int(a.codec_context.sample_rate) if a is not None else 0
        # 期望的音频长度 = 视频**起始偏移 + 内容长度**（即视频结束时刻）。
        #   减掉 `v_start` 之后，`a_v_delta` 才只反映「音频真的多了多少」，不受落盘节点
        #   自定的时间戳原点影响。`v_start = 0` 时与旧口径**逐位相同**。
        delta = (ad - vd - v_start) if a is not None else 0.0
        return {"path": str(path), "frames": frames, "fps": fps, "v_seconds": vd,
                "v_start": v_start, "v_end": vd + v_start,
                "a_seconds": ad, "a_v_delta": delta,
                "has_audio": a is not None, "a_codec": a.codec_context.name if a else "",
                "a_rate": rate, "a_layout": str(a.codec_context.layout.name) if a else "",
                "width": int(v.codec_context.width), "height": int(v.codec_context.height),
                "a_prime_samples": int(round(delta * rate)) if (a and rate) else 0}


# 🔴 段间 fps 比较用**相对容差**（0.5%，带绝对下限 0.02 fps），不是逐位比（2026-10-12 实测）。
#
# 为什么要容差：探针里的 `fps` 是容器的 `average_rate` = 帧数 ÷ 容器时长，而时长是被**导出**的量
#   （容器时间基量化 + muxer 的时长写法）⇒ **同一台机、同一张图**跑出来的两段会得到
#   24.0113 与 24.0126 这种差别（它们的 `base_rate` / `guessed_rate` 都恒为 24 —— 那才是"规格"）。
#   逐位比较 ⇒ 好段被判"规格不一致"拒拼（实测：旁路该闸后拼装四项断言全过、192/192 帧守恒、A·VΔ=0）。
#
# 为什么是**相对**量而不是某一个固定数字：量化噪声随 fps 等比例放大（29.97 的绝对噪声是 23.976 的
#   1.25 倍）。固定 0.02 对高帧率段是收紧、对低帧率段是放松；相对 0.5% 在任何帧率上都是"同一族"的量级。
#
# 放行 / 拒绝的边界（都在单测里锁着）：
#   · 同族差异（23.976 vs 24 = 0.1%、实测那对 = 0.005%）⇒ **放行**。
#     两条路都会按**每段自己的 fr** 重排时间戳（copy 路 `v_off += n_in/fr`；encode 路按段重建 CFR），
#     0.1% 的漂移在 8 s 上只有 8 ms，且退路那条有四项断言把关。
#   · 真不同规格（24 vs 25/30/60；8 vs 8.5 这种低帧率差）⇒ **拒绝**（差 ≫ 容差）。
# 绝对下限 0.02 fps：保住低帧率段（如 8 fps 时 0.5% 只有 0.04 fps）不被缩到量不出来。
_FPS_TOL_ABS = 0.02
_FPS_TOL_REL = 0.005


def fps_tolerance(ref_fps):
    """段间 fps 比较的容差（由**参照段的帧率**导出，见上面那段实测记录）。"""
    try:
        ref = float(ref_fps or 0.0)
    except (TypeError, ValueError):
        ref = 0.0
    return max(_FPS_TOL_ABS, _FPS_TOL_REL * ref)


def assert_segments_joinable(clips):
    """拼接前的**逐段体检** → ``{"ok","problems","warnings"}``。查三件：

      ① 没有视频流 / 帧数为 0；② 各段分辨率·fps·音频规格不一致；③ 音频比**视频结束时刻**
      （``v_start + v_seconds``）长**超过 1 帧**（= 该段音频很可能**绕过了「Trim AV」**：
      画面裁了、音频没裁 ⇒ 每段差 ~0.9s 且逐段累积）。

    ⚠️ ③ 的口径**必须减掉视频轨自己的起始偏移**（见 `_start`）：落盘节点可以自由决定时间戳
    原点，拿 `duration` 硬比会把「音频补了等长前置静音」的好段误判成绕过 Trim AV。
    ⚠️ ② 的 fps 比较**必须带容差**（`fps_tolerance`）—— 容器里的 `average_rate` 是导出的量，
    不是规格（见 `_FPS_TOL_ABS` 处的实测记录）。
    """
    clips = list(clips)
    if not clips:
        return {"ok": False, "problems": ["没有任何段文件。"], "warnings": []}
    ref, problems = clips[0], []
    tol_fps = fps_tolerance(ref.get("fps"))
    for i, c in enumerate(clips):
        n = i + 1
        if not c.get("frames"):
            problems.append("第 %d 段没有可读的视频帧：%s" % (n, c.get("path")))
        if i:
            _d_fps = abs(float(c["fps"]) - float(ref["fps"]))
            if ((c["width"], c["height"]) != (ref["width"], ref["height"])
                    or _d_fps > tol_fps):
                problems.append("第 %d 段规格与第 1 段不一致：%dx%d@%.4f vs %dx%d@%.4f"
                                "（fps 差 %.4f / 容差 %.4f）—— 段文件必须同规格。"
                                % (n, c["width"], c["height"], c["fps"],
                                   ref["width"], ref["height"], ref["fps"],
                                   _d_fps, tol_fps))
            if c.get("has_audio") != ref.get("has_audio"):
                problems.append("第 %d 段的音频流有无与第 1 段不一致，拼出来必错位。" % n)
            for k, label in (("a_rate", "采样率"), ("a_layout", "声道"), ("a_codec", "音频编码")):
                if ref.get("has_audio") and c.get("has_audio") and c.get(k) != ref.get(k):
                    problems.append("第 %d 段的%s(%s)与第 1 段(%s)不一致。"
                                    % (n, label, c.get(k), ref.get(k)))
        if c.get("has_audio") and c.get("fps") and c["a_v_delta"] > 1.0 / c["fps"] + 0.02:
            problems.append(
                "第 %d 段音频比视频长 %.4fs（> 1 帧 %.4fs）——该段音频很可能**绕过了「Trim AV」**："
                "画面裁了头部重叠帧、音频没裁。请把落盘节点的 audio 改接「裁重叠 [1] audio」"
                "（或经「音频缝 [0] audio」），见主 README「使用方法」。"
                % (n, c["a_v_delta"], 1.0 / c["fps"]))
    # 0.001~1 帧之间的差是**编码器 priming**（正常），不在这里刷警告 —— 逐段数字已在 report 里打印。
    return {"ok": not problems, "problems": problems, "warnings": []}


# 无损音轨编码（拼接用）：加进容器即**不再引入新代际**。`flac` 在 mp4/mov/mkv 里
#   实测本机 PyAV 17 一律 EINVAL（只能进原生 .flac 容器）⇒ 不进清单；PCM 三种都实测可写。
LOSSLESS_AUDIO_CODECS = ("pcm_s16le", "pcm_s24le", "pcm_f32le")


def _load_pcm_sidecar(path, rate, layout):
    """读「Trim AV」落的 PCM 边车 → ``numpy (channels, samples) float32``；不可用返回 None。

    **不可用就返回 None、由调用方退回 mp4 解码路**（不 raise）——边车是加速/提质手段，
    不该因为它被删了/换了采样率就整条链拼不出来。
    裁剪 vs 校准：采两种输入形状（``[B,C,T]`` / ``[C,T]``）；采样率或声道与成片音轨对不上就弃用。
    """
    try:
        a = load_audio(str(path))
    except Exception:      # 文件被删 / 不是本工具写的 / safetensors 缺失
        return None
    wf = a.get("waveform")
    if not torch.is_tensor(wf) or int(a.get("sample_rate") or 0) != int(rate):
        return None
    if wf.dim() == 1:
        wf = wf.reshape(1, -1)
    if wf.dim() < 2:
        return None
    w = wf.reshape(-1, int(wf.shape[-2]), int(wf.shape[-1]))[0].to(torch.float32).numpy()
    want_ch = {"mono": 1, "stereo": 2}.get(str(layout))
    if want_ch is not None and int(w.shape[0]) != want_ch:
        return None
    return w


def _decode_pcm(path, av, rate, layout):
    """把段文件音频解成 ``numpy (channels, samples)``；无音频返回 None。"""
    import numpy as np
    with av.open(str(path)) as c:
        if not c.streams.audio:
            return None
        rs = av.audio.resampler.AudioResampler(format="fltp", layout=layout, rate=rate)
        chunks = [o.to_ndarray() for f in c.decode(c.streams.audio[0]) for o in rs.resample(f)]
        chunks += [o.to_ndarray() for o in (rs.resample(None) or [])]
    return np.concatenate(chunks, axis=1) if chunks else None


def _passthrough_video_stream(out, vin, av):
    """建一条**与源同规格**的输出视频流（画面流拷贝 = 逐位无损路的入口）。

    用 PyAV 的**模板流**：连 extradata（h264 的 SPS/PPS）一起复制 ⇒ 真 mux 直通、零重编码。
    ⚠ 老版本 PyAV 没有这个 API。这里**故意不写"手工复制参数"的退路** ——
    那等于在无法验证的机器上换一条 remux 实现，属"无断言即未验收"（容易产出能播但**内容错了**的片）。
    改成：给**一句话的修法**，并让 `assemble_mp4_segments` 自动退回重编码路（有断言把关）。
    """
    maker = getattr(out, "add_stream_from_template", None)
    if not callable(maker):
        raise RuntimeError(
            "本机 PyAV 太旧（缺 add_stream_from_template）⇒ 画面**流拷贝（无损路）**用不了。（PyAV %s）\n"
            "    修法：pip install -U \"av>=17\"（宿主 ComfyUI 的依赖里就是这个版本）。"
            % (getattr(av, "__version__", "?"),))
    return maker(vin)


def _video_codec(av):
    """生产用 libx264；精简 FFmpeg（如 CI 的 av 轮子）没有它时退 mpeg4 —— 只影响离线单测。"""
    return "libx264" if "libx264" in set(av.codecs_available) else "mpeg4"


def concat_mp4_segments(paths, out_path, *, crf=16, preset="medium", audio_bitrate="256k",
                        audio_codec="aac", video_mode="copy", pcm_paths=None, on_log=None):
    """把 N 个段文件拼成一条（**不做体检、不做断言** —— 那是另两个函数的事）。

    ``video_mode="copy"``（默认，无损）：画面**流拷贝**（逐包重定位时间戳，零重编码），
    音频**重编码**（必须 —— 去 priming + 截到本段有效时长只能在解码侧做）；
    ``"encode"``：画面也单遍重编码（段规格不一致时的唯一可行路）。

    ``audio_codec``：``"aac"``（默认，配 ``audio_bitrate``，如 256k/192k）或
    无损档 ``"pcm_s16le"`` / ``"pcm_s24le"`` / ``"pcm_f32le"``（不加码率参数）。

    ``video_mode="copy"`` 需要 PyAV 的模板流 API（`add_stream_from_template`，av>=17 有）；
    ``assemble_mp4_segments`` 会把"本机不支持"变成**自动退回重编码**（有四项断言把关）。

    ``pcm_paths``：**逐段对齐**的 PCM 边车路径（`裁重叠` 落的无损音频）；给了就**直读 PCM**，
    既不用解 AAC、也不需要去 priming ⇒ 音频代际从 2 降到 1，且无损档下**零新增代际**。
    🔴 **每段内部必须按优先级排好**（越靠前 = 提交图里越下游 = 越可能真进 mp4）——
    本函数取**第一个过长度护栏**的那份；缺失/不匹配（采样率、声道、长度）自动退回 mp4 解码路，
    并在 ``audio_alignment[].source`` 标注；**其余未采用的候选**（读不出 / 不同源 / 并列落选）
    记在 ``audio_alignment[].pcm_others`` 并写进报告 —— 选错时不再是静默的。

    返回 ``{"out","mode","clips","audio_alignment","warnings","seconds"}``。
    """
    import fractions
    import time as _time
    import numpy as np
    av = _av_module()
    log = on_log or (lambda s: None)
    if video_mode not in ("copy", "encode"):
        raise ValueError("video_mode 只认 'copy' / 'encode'，得到 %r" % (video_mode,))
    paths = [str(p) for p in paths]
    # 逐段对齐的 PCM 边车（可缺项）。长度不足时补 None —— 后面按段取，不靠"顺序正好对上"。
    pcms = list(pcm_paths or [])
    pcms += [None] * (len(paths) - len(pcms))
    lossless_a = str(audio_codec).lower() in LOSSLESS_AUDIO_CODECS
    # 容器：按扩展名选（.mkv → matroska）。**默认 mp4**；PCM 音轨塞进 mp4 实测可写，
    # 但那是非标准作法（部分播放器会忽略音轨）⇒ 无损档的意义是"给剪辑/归档的母版"，不是预览档。
    fmt = "matroska" if str(out_path).lower().endswith((".mkv", ".mka")) else "mp4"
    t0, align, warnings = _time.time(), [], []
    out = av.open(str(out_path), "w", format=fmt)
    v_out = a_out = None
    v_pos = a_pos = 0          # 全片帧序号 / 音频累计样本数（跨段**不许归零**）
    v_off = 0.0                # copy 路：画面累计秒（给每段包加同一偏移）
    try:
        for idx, p in enumerate(paths):
            with av.open(p) as cin:
                vin = cin.streams.video[0]
                ain = cin.streams.audio[0] if cin.streams.audio else None
                fr = vin.average_rate
                if v_out is None:
                    if video_mode == "copy":
                        v_out = _passthrough_video_stream(out, vin, av)
                    else:
                        vc = _video_codec(av)
                        v_out = out.add_stream(vc, rate=fr, options=(
                            {"crf": str(int(crf)), "preset": str(preset)} if vc == "libx264"
                            else {"qscale": str(max(1, int(crf) // 8))}))
                        v_out.width = int(vin.codec_context.width)
                        v_out.height = int(vin.codec_context.height)
                        v_out.pix_fmt = "yuv420p"
                    if ain is not None:
                        a_rate = int(ain.codec_context.sample_rate)
                        if lossless_a:
                            a_out = out.add_stream(str(audio_codec), rate=a_rate)
                        else:
                            a_out = out.add_stream("aac", rate=a_rate,
                                                   options={"b": str(audio_bitrate)})
                        a_out.layout = str(ain.codec_context.layout.name)
                if video_mode == "copy":
                    n_in, base = 0, None
                    for pkt in cin.demux(vin):
                        if pkt.pts is None or pkt.dts is None:
                            continue
                        if base is None:
                            base = pkt.pts
                        # ⚠ 用**包自己的 time_base** 换算：copy 路的输出流来自模板，
                        #   `v_out.time_base` 读出来是 None ⇒ 拿它换算会写出天文数字 pts、封装报 EINVAL。
                        shift = int(fractions.Fraction(v_off) / (pkt.time_base or vin.time_base))
                        pkt.pts, pkt.dts = int(pkt.pts - base + shift), int(pkt.dts - base + shift)
                        pkt.stream = v_out
                        n_in += 1
                        out.mux(pkt)
                    v_off += n_in / float(fr)
                else:
                    n_in = 0
                    tb = fractions.Fraction(1, 1) / fractions.Fraction(fr)
                    for frame in cin.decode(vin):
                        if frame.format.name != "yuv420p":
                            frame = frame.reformat(format="yuv420p")
                        frame.pts, frame.time_base = v_pos, tb     # CFR 重建：天然无洞且跨段单调
                        v_pos += 1
                        n_in += 1
                        for pkt in v_out.encode(frame):
                            out.mux(pkt)
                if ain is None or a_out is None:
                    align.append({"index": idx, "file": os.path.basename(p), "seg_samples": 0,
                                  "decoded_samples": 0, "prime_drop": 0, "tail_drop": 0,
                                  "source": "none"})
                    continue
                # 声音：**优先读 PCM 边车**（无损、与画面等长、不需要去 priming）；
                #       没有才退回 mp4 解码：去 priming → 截到本段有效时长（帧数/fps）。
                rate, layout = int(a_out.rate), str(a_out.layout.name)
                want = int(round(n_in * rate / float(fr)))
                # 容器里那份音频的样本数 —— 用来判「边车与落盘音频同源吗」（护栏，见下）。
                n_cont = int(round(_secs(ain) * rate)) if ain is not None else 0
                # 🔴 护栏容差：边车必须与**本段落盘音频同源**。
                #   为什么必须有：音频链上「Trim AV」之后可能还有本包别的音频节点
                #   （典型 = 音频缝：patch 长度守恒、**J-cut 会缩短 align 秒**）。
                #   拿链上更靠前的边车去拼，成片音轨就会**绕过那次处理**（甚至错位）。
                #   容差怎么定（按容器事实）：段容器音频 = 视频 + priming(~33–53ms) + 尾填充
                #   ⇒ 100 ms 足够放过正常段；而"链上更靠前的节点"差的是裁量/align（典型 0.9 s）
                #   ⇒ 100 ms 能干净分开。
                #   ⚠ 它是**同源**判据、**不是"更准"判据** —— 见下面挑候选的注释。
                # 🔴 2026-10-08：容差必须**加上视频轨与音频轨起始偏移之差**。落盘节点给视频轨打
                #   `start`（实测 0.125s）时，音频容器时长会比「视频内容长度」多出那么多 ⇒
                #   边车（长度 = 视频内容）会被误判「与 mp4 音频不同源」而**整段拒收**
                #   （实测差 4000 样本 > 容差 3200 ⇒ 3/3 段全走 AAC 路）。
                #   ⚠️ 用 `abs`：两边谁晚开始都可能（节点也可以给音频轨打偏移）。
                v_shift = abs(_start(vin) - _start(ain)) if ain is not None else 0.0
                tol_len = (max(int(round(0.1 * rate)), int(round(rate / float(fr))))
                           + int(round(v_shift * rate)))
                bad = []
                cands = pcms[idx] if idx < len(pcms) else None
                if isinstance(cands, str):
                    cands = [cands]
                src, head, tail, pick, others = "aac", 0, 0, "", []
                pcm, pool = None, []
                for _c in (cands or []):
                    if not _c:
                        continue
                    _w = _load_pcm_sidecar(_c, rate, layout)
                    pool.append((_w, os.path.basename(str(_c))))
                # 每个**未采用**的候选都要说得出原因 —— 静默丢一个候选等于把"配错段"的
                # 线索抹掉（铁律 15：退化分支必须可观测）。两类原因分开写，读报告时一眼可分。
                for _w, _n in pool:
                    if _w is None:
                        others.append("%s（读不出／采样率或声道不符）" % _n)
                _ok = [x for x in pool if x[0] is not None]
                # 过护栏的候选（= 与本段落盘音频同源）。`not n_cont` = 段里压根没有音轨，
                # 长度无从判 ⇒ 全放行（与旧行为一致，别在这里改变无关语义）。
                _in = [x for x in _ok
                       if not n_cont or abs(int(x[0].shape[1]) - n_cont) <= tol_len]
                if _in:
                    # 🔴 2026-09-30 定案：取**列表第一个**，不再取"与 mp4 音频长度最接近"的那个。
                    #   契约 = `pcm_paths` 每段内**已按优先级排好**（越靠前 = 提交图里越下游
                    #   = 越可能真进 mp4，由 `__init__._pcm_candidates` 用 `graph_downstream_rank` 算）。
                    #   为什么必须换（真实事故）：两份候选可以**长度完全相同**（段 2 的未裁音频把
                    #   段 1 的床文件覆盖了，都是 210400 样本）⇒ 护栏全放行 ⇒ 旧的
                    #   `min(|len − n_cont|)` 于是按"更接近"选中了**错的**那份 ⇒ 成片音画错段。
                    #   长度不是证据，**数据流才是**。
                    pcm, src, pick = _in[0][0], "pcm", _in[0][1]
                    others += ["%s（同源并列，未采用）" % x[1] for x in _in[1:]]
                    _tol = {id(x) for x in _in}          # 按对象身份判，别拿 ndarray 做 `in` 比较
                    others += ["%s（与 mp4 音频差 %.0f ms）"
                               % (x[1], (int(x[0].shape[1]) - n_cont) / rate * 1000.0)
                               for x in _ok if id(x) not in _tol]
                else:
                    bad += ["%s: 与 mp4 音频差 %.0f ms"
                            % (x[1], (int(x[0].shape[1]) - n_cont) / rate * 1000.0) for x in _ok]
                if bad and pcm is None:
                    warnings.append("第 %d 段的 PCM 边车被拒收（%s）；该段退回 mp4 解码。"
                                    % (idx + 1, "；".join(bad)))
                # 采用了某份之后，**其余候选**（读不出 / 不同源 / 并列落选）仍要写进该段报告 ——
                # 选错时这就是唯一的线索（铁律 15：退化分支必须可观测）。
                _also = ("；未采用：%s" % "、".join(others)) if (pcm is not None and others) else ""
                if pcm is not None:
                    got = int(pcm.shape[1])
                    if got >= want:                      # 边车比视频长（沉降帧没裁完）⇒ 从尾截
                        tail = got - want
                        pcm = pcm[:, :want]
                        note = "PCM 边车（无损源，零新增代际）｜ 尾截 %d 样本%s" % (tail, _also)
                    else:
                        note = "⚠ PCM 边车比视频短 %d 样本，已补静音%s" % (want - got, _also)
                        warnings.append("第 %d 段 PCM 边车比视频短 %d 样本，已补静音。"
                                        % (idx + 1, want - got))
                        pcm = np.concatenate([pcm, np.zeros((pcm.shape[0], want - got),
                                                            dtype=pcm.dtype)], axis=1)
                else:
                    prime = int(round(AUDIO_ENCODER_PRIME_MS / 1000.0 * rate))
                    pcm = _decode_pcm(p, av, rate, layout)
                    got = 0 if pcm is None else int(pcm.shape[1])
                    if pcm is not None and got >= want:
                        head = min(prime, got - want)
                        tail = got - head - want
                        pcm = pcm[:, head:head + want]
                        note = "去 priming %d + 尾丢 %d 样本（mp4 解码路）" % (head, tail)
                    else:
                        head = tail = 0
                        note = "⚠ 不足 %d 样本，已补静音" % max(0, want - got)
                        warnings.append("第 %d 段音频比视频短 %d 样本，已补静音。" % (idx + 1, want - got))
                        if pcm is not None:
                            pcm = np.concatenate([pcm, np.zeros((pcm.shape[0], want - got),
                                                                dtype=pcm.dtype)], axis=1)
                align.append({"index": idx, "file": os.path.basename(p), "seg_samples": want,
                              "decoded_samples": got, "prime_drop": head, "tail_drop": tail,
                              "source": src, "pcm_file": pick, "pcm_others": others})
                log("[H3 Relay] 拼接：段 %d 有效时长 %d 帧 = %d 样本，源 %s%s（%d 样本）⇒ %s"
                    % (idx + 1, n_in, want, src,
                       (" " + pick) if pick else "", got, note))
                if pcm is None:
                    continue
                a_tb = fractions.Fraction(1, 1) / fractions.Fraction(rate)
                for s in range(0, pcm.shape[1], 1024):
                    af = av.AudioFrame.from_ndarray(np.ascontiguousarray(pcm[:, s:s + 1024]),
                                                    format="fltp", layout=layout)
                    af.sample_rate, af.pts, af.time_base = rate, a_pos, a_tb
                    a_pos += af.samples
                    for pkt in a_out.encode(af):
                        out.mux(pkt)
        for st in (v_out, a_out):
            if st is not None:
                for pkt in st.encode(None):
                    out.mux(pkt)
    finally:
        out.close()
    return {"out": str(out_path), "mode": video_mode, "clips": len(paths),
            "audio_alignment": align, "warnings": warnings,
            "seconds": round(_time.time() - t0, 3)}


def assert_assembled(out_path, expected_frames, fps, deep=False):
    """成片四断言（与包内组装层同口径）。

    **快路（默认）**：单次 demux 取包级 pts/dts，不整片解码；判「PTS 无洞」前排序到**显示序**
    （demux 序 = DTS 序 ≠ 显示序，本仓踩过）。``deep=True`` 走逐帧解码计数（慢；单测用它交叉验证）。
    """
    av = _av_module()
    with av.open(str(out_path)) as c:
        v = c.streams.video[0]
        vd, ad = _secs(v), (_secs(c.streams.audio[0]) if c.streams.audio else 0.0)
        if deep:
            pts = [float(f.pts) * float(f.time_base or v.time_base) for f in c.decode(v)]
            dts = []
        else:
            pk = [(p.pts, p.dts, p.time_base) for p in c.demux(v)
                  if p.pts is not None and p.dts is not None]
            pts = sorted(float(p * float(tb or v.time_base)) for p, _, tb in pk)
            dts = [int(d) for _, d, _ in pk]
    if deep:
        with av.open(str(out_path)) as c:
            dts = [int(p.dts) for p in c.demux(c.streams.video[0]) if p.dts is not None]
    holes = [i for i in range(1, len(pts)) if pts[i] - pts[i - 1] > 1.5 / fps]
    dl = (abs(ad - vd) if ad else None)
    dts_ok = all(b > a for a, b in zip(dts, dts[1:], strict=False))
    av_ok = dl is None or dl <= 1.0 / fps + 0.02
    return {"expected_frames": int(expected_frames), "frames": len(pts),
            "frames_conserved": len(pts) == int(expected_frames), "pts_holes": holes[:10],
            "pts_contiguous": not holes, "dts_strictly_increasing": dts_ok,
            "video_seconds": round(vd, 4), "audio_seconds": round(ad, 4),
            "av_delta_seconds": round(dl, 4) if dl is not None else None, "av_delta_ok": av_ok,
            "assembled_ok": bool(len(pts) == int(expected_frames) and not holes and dts_ok and av_ok)}


def assemble_mp4_segments(paths, out_path, *, video="auto", audio_codec="aac",
                          audio_bitrate="256k", crf=16, pcm_paths=None, on_log=None, **kw):
    """**一步到位**：体检 → 拼接 → 四断言。``video="auto"`` = 先流拷贝，断言不过退单遍重编码。

    ``audio_codec``/``audio_bitrate`` = 成片音轨档（见 ``concat_mp4_segments``）；
    ``pcm_paths`` = 逐段的 PCM 边车（有则直读，音频只编码一代）；``crf`` = 重编码路的画质。

    ``ok=False`` 时成片**可能已经写出来了**（断言不过就是不过，不悄悄删文件）——
    调用方按 ``report`` 里的处置建议决定要不要留：坏片留着才能取证。
    """
    import time as _time
    _av_module()
    log = on_log or (lambda s: None)
    paths = [str(p) for p in paths]
    t0 = _time.time()
    clips = [probe_mp4(p) for p in paths]
    fps = clips[0]["fps"] if clips else 0.0
    expected = sum(c["frames"] for c in clips)
    health = assert_segments_joinable(clips)
    lossless = str(audio_codec).lower() in LOSSLESS_AUDIO_CODECS
    lines = ["[H3 Relay] 多段拼接：%d 段 → %s" % (len(clips), out_path)] + [
        "           · %s：%d 帧 %.3fs%s ｜ 音频 %.3fs（差 %+.4fs / %+d 样本）"
        % (os.path.basename(c["path"]), c["frames"], c["v_seconds"],
           ("（视频轨起于 %.3fs）" % c["v_start"]) if c.get("v_start") else "",
           c["a_seconds"], c["a_v_delta"], c["a_prime_samples"]) for c in clips]
    lines.append("           · 音轨档：%s%s ｜ 画面：%s"
                 % (audio_codec, "" if lossless else " @" + str(audio_bitrate),
                    "流拷贝（无损）" if video != "encode" else "重编码 crf=%d" % int(crf)))
    rep = {"ok": False, "out": str(out_path), "clips": clips, "health": health,
           "expected_frames": expected, "asserts": None, "report": "", "mode": ""}
    if not health["ok"]:
        lines.append("           🔴 体检不过，**没有拼**：")
        lines += ["              · " + p for p in health["problems"]]
        rep["report"] = "\n".join(lines)
        return rep
    mode = "encode" if video == "encode" else "copy"
    opt = dict(video_mode=mode, audio_codec=audio_codec, audio_bitrate=audio_bitrate,
               crf=crf, pcm_paths=pcm_paths, on_log=log, **kw)
    res = None
    try:
        res = concat_mp4_segments(paths, out_path, **opt)
        chk = assert_assembled(out_path, expected, fps)
    except Exception as exc:
        if mode != "copy":
            raise
        # 流拷贝路炸了（段规格不一致、包时间基怪异…）也要有条**能走通**的路，而不是把异常甩给调用方。
        lines.append("           ⚠ 画面流拷贝路不可用（%s：%s）⇒ **退回单遍重编码**重拼。"
                     % (type(exc).__name__, exc))
        mode = "encode"
        opt["video_mode"] = mode
        res = concat_mp4_segments(paths, out_path, **opt)
        chk = assert_assembled(out_path, expected, fps)
    if res is not None and not chk["assembled_ok"] and video == "auto" and mode == "copy":
        lines.append("           ⚠ 画面流拷贝路的成片断言不过 ⇒ **退回单遍重编码**重拼"
                     "（时间轴最干净的路）。")
        mode = "encode"
        opt["video_mode"] = mode
        res = concat_mp4_segments(paths, out_path, **opt)
        chk = assert_assembled(out_path, expected, fps)
    lines += ["           · 段 %d 音频源=%s%s：有效 %d 样本 ｜ 去 priming %d / 尾丢 %d"
              % (a["index"] + 1, a.get("source", "?").upper(),
                 ("（%s）" % a["pcm_file"]) if a.get("pcm_file") else "",
                 a["seg_samples"], a["prime_drop"], a["tail_drop"])
              for a in res["audio_alignment"]]
    _via = sum(1 for a in res["audio_alignment"] if a.get("source") == "pcm")
    lines.append("           · 音频代际：%s"
                 % ("**零新增**（%d/%d 段直读 PCM 边车%s）"
                    % (_via, len(res["audio_alignment"]),
                       "，其余段退回 mp4 解码（该段本身已是 1 代）"
                       if _via < len(res["audio_alignment"]) else "")
                    if lossless else
                    "2 → **1**（%d/%d 段直读 PCM 边车，不再二次 AAC）"
                    % (_via, len(res["audio_alignment"]))))
    lines.append("           ✅ 画面=%s ｜ 帧数 %d/%d 守恒=%s ｜ PTS 无洞=%s ｜ DTS 递增=%s ｜ "
                 "A·VΔ=%.4fs ｜ 总耗时 %.2fs（拼接 %.2fs）"
                 % ("流拷贝（无损）" if res["mode"] == "copy" else "重编码",
                    chk["frames"], expected, chk["frames_conserved"], chk["pts_contiguous"],
                    chk["dts_strictly_increasing"],
                    chk["av_delta_seconds"] if chk["av_delta_seconds"] is not None else -1,
                    round(_time.time() - t0, 3), res["seconds"]))
    if not chk["assembled_ok"]:
        lines.append("           🔴 成片断言不过 —— 片已写出（留作取证），但**别当成品用**。")
    rep.update({"asserts": chk, "alignment": res["audio_alignment"], "mode": res["mode"],
                "seconds": round(_time.time() - t0, 3), "concat_seconds": res["seconds"],
                "warnings": res["warnings"], "ok": bool(chk["assembled_ok"]),
                "pcm_segments": _via, "report": "\n".join(lines)})
    return rep


# 各家落盘节点回显"文件在哪"的字段名不统一 —— 全部认，**不认节点名、不认键名**。
#   实测样本（2026-09-22）：宿主 SaveVideo 给 `filename`+`subfolder`+`type`；
#   第三方 banzhangVideoCombine 给 `filename`+`type`+**`abs_path`**（且存到 output 之外）。
#   再宽一点收下"只给一个路径字符串"的写法（`path`/`file`/`filepath`…）。
_FILE_PATH_KEYS = ("abs_path", "path", "filepath", "file_path", "file", "filename", "file_name")
# 文件名字符集之外的符号（Windows 非法）——用来把"**看着像文件名的说明文本**"挡掉。
#   实测坑：第三方节点把状态文本写在 `detail_info` 里
#   （`📂 文件名称 : au4_s1_N709_0001.mp4\n📏 物理尺寸 : …`），末尾也是 `.mp4`
#   ⇒ 若不加这道判据，会被当成一条"文件记录"，而且**可能排在真记录前面**（拿错路径 ⇒ 白跑一轮）。
_ILLEGAL_IN_NAME = set(':<>"|?*')


def _looks_like_path(s: str) -> bool:
    """这像不像一个**文件路径**：路径里不会有换行，也不会有文件名字符集之外的符号。

    ⚠ 判据必须落在**整串**上（不是 basename）—— 实测反例：
    `📂 文件名称 : au4_s1_N709_0001.mp4` 是**单行**说明文本，
    可它的 basename 恰好是 `au4_s1_N709_0001.mp4` ⇒ 只看 basename 会放行。
    唯一放行的冒号 = **盘符**（`X:` 在第 2 个字符），其余冒号一律视为标签文本。
    """
    if not s or "\n" in s or "\r" in s or len(s) > 1024:
        return False
    rest = s[2:] if (len(s) > 1 and s[1] == ":") else s
    return not (_ILLEGAL_IN_NAME & set(rest))


def _path_is_abs(s: str) -> bool:
    """判"绝对路径"，**与宿主平台无关**。

    ⚠ 绝不能用 `os.path.isabs`：宿主跑在 Linux 时它不认 Windows 盘符式
    （`X:/videos/out.mp4` 这类盘符式被判成相对路径 ⇒ `abs_path` 丢空 ⇒ 拼接拿错文件），
    反之在 Windows 时它不认 `/x/out.mp4`。落盘节点回显的是**哪个 OS 的路径**
    由节点决定，不由跑这段代码的机器决定 ⇒ 两种风格都得认。
    """
    return s.startswith(("/", "\\")) or ntpath.isabs(s)


def _path_split(s: str):
    r"""按 `\` 与 `/` 两种分隔符切 (目录, 文件名)；目录取**原串前缀**（不改写分隔符）。"""
    norm = s.replace("\\", "/")
    i = norm.rfind("/")
    if i < 0:
        return "", s
    return s[:i], s[i + 1:]


def _file_record(item):
    """把一条候选归一化成 ``{filename, subfolder, type, abs_path}``；不像文件就返回 None。

    **为什么归一化而不是写死键名**：本包不猜"用户用的是哪个落盘节点"，
    只判两件事 —— ① 这条东西里能不能读出一个路径；② 像不像文件。
    有绝对路径就用绝对路径（节点自己报的最可信，且能覆盖"存到 ComfyUI output 之外"的情况）；
    只有相对名时才退回 `type` + `subfolder` 拼。
    """
    if isinstance(item, str):                       # 有的节点直接回显一个路径字符串
        item = {"path": item}
    if not isinstance(item, dict):
        return None
    raw = None
    for k in _FILE_PATH_KEYS:
        v = item.get(k)
        if isinstance(v, str) and v.strip() and _looks_like_path(v.strip()):
            raw = v.strip()                         # 分隔符原样带出：路径属于哪个 OS 由节点决定
            break
    if not raw:
        return None
    sub = str(item.get("subfolder") or "").strip()
    if _path_is_abs(raw):
        return {"filename": _path_split(raw)[1], "subfolder": sub,
                "type": str(item.get("type") or "output"), "abs_path": raw}
    # 相对路径：`dir/name.mp4` 这种把目录当 subfolder（有些节点这么给）
    d, base = _path_split(raw)
    if d and not sub:
        sub = d
    return {"filename": base or raw, "subfolder": sub,
            "type": str(item.get("type") or "output"), "abs_path": ""}


def _iter_file_records(node_out):
    """遍历一个节点 output dict 里**所有**像"文件记录"的条目（**键名不设白名单**）。

    为什么不做键名白名单：实测（2026-09-22 真实跑）宿主 `SaveVideo` 写 `images`，
    而第三方落盘节点（`banzhangVideoCombine`）写的是 `painter_output` ——
    只认白名单会变成"图跑得好好的，拼接却报一段视频都没找到"（本仓踩过）。
    """
    for val in (node_out or {}).values():
        if isinstance(val, list):
            for item in val:
                rec = _file_record(item)
                if rec:
                    yield rec


def pick_video_outputs(outputs):
    """从 ComfyUI history 的 ``outputs`` 里挑出**视频类**落盘结果（纯函数，便于单测）。

    **按扩展名扫全部键**（键名不设白名单，见 `_iter_file_records` 的原因）；
    非视频（png…）与畸形项一律跳过；带 `abs_path` 的一并带出 ——
    有些落盘节点把文件存到 ComfyUI output **之外**（如自定义保存路径），
    这时 `type`+`subfolder` 拼出来的路径是错的，必须用节点自己给的绝对路径。
    返回 ``[{"node","filename","subfolder","type","abs_path"}]``（按 history 里的出现顺序）。
    """
    found, seen = [], set()
    for node_id, node_out in (outputs or {}).items():
        if not isinstance(node_out, dict):
            continue
        for rec in _iter_file_records(node_out):
            if os.path.splitext(rec["filename"])[1].lower() not in AV_CONCAT_VIDEO_EXTS:
                continue
            # 同一个文件被节点在两个键下各报一次时**去重**（否则"第 1 个"可能不稳定）
            key = rec["abs_path"] or (rec["type"], rec["subfolder"], rec["filename"])
            if key in seen:
                continue
            seen.add(key)
            found.append({"node": str(node_id), **rec})
    return found


def pick_pcm_outputs(outputs):
    """从 history 的 ``outputs`` 里挑出**「Trim AV」落的 PCM 边车**（纯函数，便于单测）。

    边车靠节点自己回显（ui 键 ``h3relay_pcm``）带出来 —— **不猜文件名**，也不用去翻目录：
    只要那一段真的是本包节点跑的，路径就在这里；不是（旧版本/被删）就当没有，退回 mp4 解码。
    返回 ``[{"node","filename","subfolder","type"}]``。
    """
    found = []
    for node_id, node_out in (outputs or {}).items():
        if not isinstance(node_out, dict):
            continue
        for item in (node_out.get(PCM_UI_KEY) or []):
            if not isinstance(item, dict):
                continue
            fn = str(item.get("filename") or "")
            if fn.lower().endswith(".safetensors"):
                found.append({"node": str(node_id), "filename": fn,
                              "subfolder": str(item.get("subfolder") or ""),
                              "type": str(item.get("type") or "output")})
    return found


def _link_source(val):
    """ComfyUI 提交图里的**连线值** = ``[上游节点 id, 输出槽号]``；不是连线就返回 ``None``。

    判据要窄：同一个 dict 里还有 `["a.png", "b.png"]` 这类**真值列表**，
    只按"长度为 2"放行会把它们当成连线、凭空造出边。槽号必须是 int（连线才有槽号）。
    """
    if (isinstance(val, (list, tuple)) and len(val) == 2
            and isinstance(val[0], (str, int)) and not isinstance(val[0], bool)
            and isinstance(val[1], int) and not isinstance(val[1], bool)):
        return str(val[0])
    return None


def graph_downstream_rank(graph, node_ids):
    """按**提交图的数据流**给一组节点排名：**越下游 = 排名越小 = 越优先**。

    【为什么需要它】拼接要挑「哪份 PCM 边车真进了 mp4」。原先靠 `class_type` 白名单
    **猜**顺序（假设"音频缝在裁重叠之后"）—— 用户接线与假设相反时它就猜错，
    而且**不报错**：2026-09-30 真实事故里，段 1 的成片音轨是段 2 的（音画错段）。
    改读提交图：谁在数据流下游谁优先，接线怎么变都跟着变。

    【算法】入度拓扑 + 最长路：`depth[n]` = 从任一源点到 `n` 的最长路径长度。
    并列（两条独立分支）时**保持入参顺序**（稳定排序）—— 没有证据就不编造顺序。

    【返回】``{node_id: rank}``；`rank` 是负整数（越小越靠前），不在图里的给 ``1`` 排最后。
    图不是 dict / 空 / 与候选完全不相交 / **有环** ⇒ 返回 ``None``（**没有证据**，
    调用方必须按"拒收"处理，不许退回静态猜测 —— 那正是本函数要取代的东西）。
    **不抛异常**：畸形节点（`inputs` 不是 dict）按"没有连线"处理。
    """
    if not isinstance(graph, dict) or not graph:
        return None
    ids = [str(n) for n in (node_ids or [])]
    if not any(n in graph for n in ids):
        return None
    indeg = dict.fromkeys(graph, 0)
    out = {n: [] for n in graph}
    for nid, node in graph.items():
        ins = node.get("inputs") if isinstance(node, dict) else None
        for val in (ins if isinstance(ins, dict) else {}).values():
            src = _link_source(val)
            if src is not None and src in indeg and src != nid:
                out[src].append(nid)
                indeg[nid] += 1
    depth = dict.fromkeys(graph, 0)
    queue = [n for n, d in indeg.items() if not d]
    seen = 0
    while queue:
        n = queue.pop()
        seen += 1
        for m in out[n]:
            if depth[n] + 1 > depth[m]:
                depth[m] = depth[n] + 1
            indeg[m] -= 1
            if not indeg[m]:
                queue.append(m)
    if seen != len(graph):
        return None                       # 有环 ⇒ 这张图读不出"下游"，不给假证据
    return {n: (-depth[n] if n in depth else 1) for n in ids}
