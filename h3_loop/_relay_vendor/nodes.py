# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 ComfyUI-H3-Latent-Relay contributors
# 第三方出处与许可见 THIRD-PARTY-NOTICES.md
"""H3 Latent Relay · 节点层

十一个节点（0.6.8 起八件 + 0.6.37 一件 + 0.6.38 两件）：续接七件套 + 一个画质域潜空间放大适配器
+ 视频起段 + 图内拼条 + 无头连跑，覆盖"用作者的续接方式"所需的全部接线：

  🔗 H3 Relay · Latent Save   —— 把本段的 AV latent 落盘，供下一段读
  🔗 H3 Relay · Latent Load   —— 读回上一段的 AV latent
  🔗 H3 Relay · Copy Bridge      —— 上一段尾部**逐位拷贝**进本段初始 latent + 噪声掩码（钉住区不重绘）；
                             0.6.0 起复合桥：接其第 4 路（conditioning）即原「Latent 桥」钉帧路线，二路可并联
  🔗 H3 Relay · Trim AV        —— 裁掉续接段头部的重叠帧（视频 + 音频同裁）+ 交接 prev_tail
  🔗 H3 Relay · Post   —— 画质域后处理（0.5.0 拆出；不动时间轴/帧数/音频）
  🔗 H3 Relay · Audio Seam        —— 音频域：上一段环境声补本段头（长度守恒，零 A/V 位移）
  🔗 H3 Relay · Chain    —— 同分组框内自动推进「桥 + 落盘」段号并排队连跑

时间轴 / 画质域 / 音频域分工（0.5.0 起）：
  · H3RelayTrimAV    = 时间轴（裁重叠 / 沉降 / 重影），**冻结不再长 widget**；
  · H3RelayPost      = 画质域（色档对齐 / 补高频 / 锐化）；
  · H3RelayAudioSeam = 音频域（跨段环境声补头 / 床环铺）。
  三条纪律同源：**新功能归到对应域，不往 TrimAV 上挂**。

0.6.0 起只有**一个桥**（复合 CopyBridge），钉法按接法由同一节点提供，不可同图串两个桥实例：
  · 接第 4 路（conditioning）→ 上一段尾段钉进本段 conditioning（管取景/构图，原「Latent 桥」路线）；
  · 接第 0 路（latent）→ 上一段尾部逐位拷贝 + 噪声掩码（管运动，钉住区零重绘）；
  · 两路并联（复合桥）→ conditioning 钉帧 + latent 拷贝**同时**生效，是最稳的续接方式。

接线（替换像素续接时）：
    CSGlideCastCS[0] ─ conditioning ─┐
    CSGlideCastCS[1] ─ latent ───────┤
    H3 Latent Load ─ context ─────┤→ 🔗 H3 Relay · Copy Bridge [3]（conditioning）→ 采样器 positive
    （上一段：采样器 latent → 🔗 H3 Relay · Latent Save）

注意：走任一桥时，上游的**像素续接字段（如 H3 Studio 的 cont）必须留空**，
否则两套续接都会往 minimax_keyframes 里塞锚，画面会打架。
"""

from __future__ import annotations

import functools
import inspect
import json
import math
import os
import time

import folder_paths

from . import relay_core as CORE
from . import layout_contract as CONTRACT
# 本插件只内嵌“桥接 / 潜桥存盘 / 裁头”三块运行时，上游的 exp 实验特性（TIHA/VA）
# 不参与任何节点，本包已从 vendor 中裁掉其源码；保留占位以兼容上游其余类的方法引用。
TIHA_ADAPTER = None
VA_ADAPTER = None


# ============================================================================
# 节点层异常上下文（#3）
# ============================================================================
# 节点抛错时，用户只看到 relay_core 内部的一句话（如「list index out of range」），
# 不知道是哪个节点、哪一段、什么入参 ⇒ 排查困难。这里包一层，把
# 「节点名 + 段号 + 关键入参实值」拼到消息前，**原始错误全文原样带上**。
#
# 两条硬约束（照做，否则门槛红）：
#   A. **保留原始异常的完整文本**（不许只取第一行）—— 有断言的 needle 落在异常文本第 2 行。
#   B. **节点自己 raise 的中文错误一律原样放行** —— 判据 = 异常的**最深来源帧**是否在本文件。
#      本包节点层的错误文案是精心写的（含操作指引），再包一层只会把好信息变成噪音。
_ERRCTX_VAL_MAX = 60
# 本文件绝对路径（模块加载时算一次）：约束 B 靠它判断异常来源是不是「本节点自己」
_THIS_FILE = os.path.abspath(__file__)


def _errctx_val(v):
    """入参实值 → 短字符串。只放标量；张量/路径一律只印类型，免得把消息灌爆。"""
    if v is None or isinstance(v, (int, float, bool)):
        return str(v)
    if isinstance(v, str):
        return v if len(v) <= _ERRCTX_VAL_MAX else v[:_ERRCTX_VAL_MAX] + "…"
    return "<%s>" % type(v).__name__


def _errctx_origin_is_self(exc) -> bool:
    """异常**最深来源帧**是否在本文件 ⇒ 是本节点自己 raise 的（约束 B 的机器判据）。"""
    tb = exc.__traceback__
    if tb is None:
        return False
    while tb.tb_next is not None:
        tb = tb.tb_next
    return os.path.abspath(tb.tb_frame.f_code.co_filename) == _THIS_FILE


def _node_errors(*keys):
    """节点方法装饰器：异常时补「节点名 + 段号 + 关键入参」，并原样带上原始错误全文。

    签名与返回形状**零变化**：functools.wraps 保留 __wrapped__ ⇒ 宿主按 FUNCTION
    取到的方法、以及 inspect.signature 看到的签名，都跟没装饰时一样。
    """
    def deco(fn):
        # 装饰时把默认值算好（运行期不再反射），免得每段执行都付一次参数表成本
        try:
            _defaults = {k: p.default for k, p in inspect.signature(fn).parameters.items()
                         if k != "self" and p.default is not inspect.Parameter.empty}
        except (TypeError, ValueError):
            _defaults = {}

        @functools.wraps(fn)
        def wrapper(self, *args, **kwargs):
            try:
                return fn(self, *args, **kwargs)
            except Exception as exc:
                if _errctx_origin_is_self(exc):
                    raise                  # 约束 B：本节点自己的中文错误 → 原样放行
                try:
                    bound = inspect.signature(fn).bind_partial(self, *args, **kwargs).arguments
                except Exception:
                    bound = {}
                # 段号：能取到就写「第 N 段」，取不到就明说没有 —— **不伪造**（本仓纪律）
                stage = bound.get("stage_index")
                if stage is None:
                    seg = "段号未提供（本节点无 stage_index 入参）"
                else:
                    try:
                        seg = "第 %d 段" % int(stage)
                    except (TypeError, ValueError):
                        seg = "段号=%r" % (stage,)
                vals = []
                for k in keys:
                    if k in bound:
                        vals.append("%s=%s" % (k, _errctx_val(bound[k])))
                    elif k in _defaults:
                        vals.append("%s=%s" % (k, _errctx_val(_defaults[k])))
                # 约束 A：str(exc) **全文**嵌进去（不许 splitlines()[0]）
                raise RuntimeError(
                    "【%s】处理失败：%s；入参 %s。\n"
                    "原因：%s\n"
                    "（原始异常：%s）"
                    % (type(self).__name__, seg, "、".join(vals) or "无",
                       exc, type(exc).__name__)
                ) from exc

        return wrapper

    return deco


CATEGORY = "H3 Latent Relay"

# 落盘根目录：ComfyUI/output/relay_kit/
_RELAY_ROOT = os.path.join(folder_paths.get_output_directory(), "relay_kit")

# Windows 保留设备名：作为目录名会让 makedirs 抛裸 OSError
_WIN_RESERVED = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {"COM%d" % i for i in range(1, 10)}
    | {"LPT%d" % i for i in range(1, 10)}
)


def _stage_path(run_id: str, stage_index: int) -> str:
    """按 run 标识 + 段号推导落盘路径。段号从 0 开始。"""
    # 非法字符替换为 _（而不是删除）—— 否则 "my/film" 与 "myfilm" 会静默撞进同一目录
    rid = "".join(ch if (ch.isalnum() or ch in "-_") else "_" for ch in str(run_id))
    if not rid.strip("_"):
        raise RuntimeError("run_id 不能为空（用来把同一部片子的各段归到一个目录）。")
    if rid.upper() in _WIN_RESERVED:
        rid += "_"
    idx = int(stage_index)
    if idx < 0:
        raise RuntimeError("stage_index 不能为负（段号从 0 开始，第 1 段=0）。")
    return os.path.join(_RELAY_ROOT, rid, "stage_%05d.safetensors" % idx)


def _audio_stage_path(run_id: str, stage_index: int) -> str:
    """音频落盘路径：与 latent 同目录，文件名前缀 audio_（互不覆盖）。"""
    p = _stage_path(run_id, stage_index)
    return os.path.join(os.path.dirname(p), "audio_%05d.safetensors" % int(stage_index))


def _stage_lineage(run_id: str, stage_index: int) -> dict:
    """边车头**谱系簿记**（B1）：run 内相对谱系 + 包版本。

    只记本节点确实知道的事实：run_id / 段号 / 父段（= 段号−1；首段记空串，不编造 −1）。
    pin / settle / 提示词在别的节点上，本期不猜（v2 待 optional 输入或 Chain 清单）。
    """
    try:
        from . import __version__ as _pack_ver
    except Exception:
        _pack_ver = "unknown"
    idx = int(stage_index)
    return {
        "run_id": str(run_id),
        "stage_index": idx,
        "parent_stage": (idx - 1) if idx > 0 else "",
        "pack": str(_pack_ver),
    }


def _progress_path(run_id: str) -> str:
    """连跑进度文件：与段文件**同目录**（`output/relay_kit/<run_id>/_progress.json`）。

    复用 `_stage_path` 取目录 ⇒ run_id 的清洗/保留名校验与段文件**完全一致**（不会两套规则）。
    """
    return os.path.join(os.path.dirname(_stage_path(run_id, 0)), "_progress.json")


def _write_progress(run_id: str, stage_index: int, segments: int) -> str:
    """把连跑进度落盘（断点续跑用）。返回文件路径。

    🔴 语义 = **「第 `stage_index` 段已经开始」**，**不是**「已完成」：
    Chain 在**段首**执行（它的活是交词），所以这里记的就是"正在跑这一段"。
    ⇒ 宿主中途卡死/被杀之后重启，从这一段**重跑**即可 —— **宁可重跑一段，绝不跳段**
    （跳段会拼出中间缺一段的成片，而且很难发现）。

    原子写（临时文件 + `os.replace`）：断电也不会留半截 JSON。
    """
    path = _progress_path(run_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    payload = {
        "run_id": str(run_id),
        "stage_index": int(stage_index),
        "segments": int(segments),
        "updated": time.time(),
        "note": "第 stage_index 段**已开始**（不是已完成）⇒ 续跑从这一段重跑",
    }
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    return path


def _pcm_ui(path: str):
    """把一份 PCM 边车文件变成 **ui 回显条目**（路径相对 output 目录），供拼成片时取回。

    靠**回显**而不是"按命名约定去翻目录"：段文件名是用户定的（SaveVideo 的 filename_prefix），
    本包不该猜；节点自己写的文件由它自己报路径，最不容易错。
    拼接方拿到后用 `os.path.join(output_dir, subfolder, filename)` 还原。
    """
    try:
        if not os.path.isfile(path):
            return None
        rel = os.path.relpath(os.path.dirname(os.path.abspath(path)),
                              folder_paths.get_output_directory())
        # 同时带上绝对路径：拼接方**优先用它**（相对路径在不同平台上分隔符/根目录都可能对不上）。
        return {"filename": os.path.basename(path), "subfolder": rel, "type": "output",
                "abs_path": os.path.abspath(path)}
    except Exception:
        return None


def _pcm_sidecar(audio, enabled: bool):
    """把本段（裁后）音频落一份 **PCM 边车**，供拼成片时直读（无损、音频只编码一代）。

    为什么落在「Trim AV」上：整条链上只有它手里那份音频**既与画面等长、又还没经过任何有损编码**
    （它是"音视频同裁"的产物）。落到这里，任何用户的图只要接了这个必备节点就自动受益。

    路径用宿主同款计数器命名（`get_save_image_path`）⇒ 同名不撞车；**不依赖 run_id/段号**
    （那个由拼接方从 history 的节点回显里取回，见 `relay_core.pick_pcm_outputs`）。
    失败一律吞掉并写进 report —— 边车是提质手段，**不该因为它写不成就让整段渲染失败**。
    返回 ``(ui 条目 或 None, 追加到 report 的一行)``。
    """
    if not enabled or audio is None:
        return None, ""
    try:
        folder, filename, counter, subfolder, _ = folder_paths.get_save_image_path(
            "relay_kit/pcm/seg", folder_paths.get_output_directory(), 1, 1)
        path = os.path.join(folder, "%s_%05d_.safetensors" % (filename, counter))
        CORE.save_audio(audio, path, note="pcm_sidecar")
        mb = os.path.getsize(path) / 1024 ** 2
        return (_pcm_ui(path),
                "\n           ♪ **PCM 边车**：%s（%.1f MB）—— 拼成片时直读它，"
                "音频不再二次编码（代际 2 → 1）。" % (path, mb))
    except Exception as exc:
        return None, ("\n           ⚠ PCM 边车写入失败（**不影响本段产物**，拼成片会自动"
                      "退回 mp4 解码）：%r" % (exc,))


# ==================== 画质域 · AV latent 分块放大（0.6.8）====================

_UPSCALER_NODE = "MinimaxH3LatentUpscaler3D"
_UPSCALER_MODELS_URL = "https://huggingface.co/LBH-123-AI/Minimax_h3_latent_upscaler"




def _comfy_registry() -> dict:
    """ComfyUI 根 `nodes` 模块的 `NODE_CLASS_MAPPINGS`。

    ⚠ 不能直接 `import nodes` 就算数：ComfyUI 装载自定义节点时会把**本包目录**插进
    ``sys.path[0]``，而本包自己就有 ``nodes.py`` ⇒ 早期/测试环境下 `import nodes`
    可能绑到**我们自己这份**（影子模块），拿到的注册表里没有上游节点，就会假报「未装上游」。
    这里按「模块文件是否真在 ComfyUI 根」验身，验不上就退到已加载模块里找真正那份
    （**绝不重复 exec 宿主源码** —— 那玩意副作用不可控）。
    """
    import os
    import sys

    root = os.path.normcase(os.path.abspath(str(getattr(folder_paths, "base_path", "") or "")))
    target = os.path.join(root, "nodes.py")

    def _at_root(mod):
        f = os.path.normcase(os.path.abspath(str(getattr(mod, "__file__", "") or "")))
        return f == target and getattr(mod, "NODE_CLASS_MAPPINGS", None) is not None

    # 🔴 2026-10-07 改：**先扫已加载模块，再 `import nodes`**（原来反过来）。
    #   原因：在线下手持 `sys.path` 上有**本包目录**的环境里，`import nodes` 会 bind 到
    #   **我们自己这份同名 `nodes.py`** 并**重新 exec 一遍** —— 而它开头的
    #   `from . import relay_core as CORE` 在没有父包时直接 **ImportError**。
    #   实证：`tools/review_050.py` 校验示例图时求 `H3RelayLatentUpscale.INPUT_TYPES()`
    #   ⇒ `_upscaler_cls() → _comfy_registry() → import nodes` ⇒ 整脚本崩在这里，
    #   而它此前**只查一张图**（那张图里没有 Upscale）⇒ 这个坑一直没被踩到。
    #   宿主里 `nodes` 必然已在 `sys.modules` ⇒ 先扫既更准（拿到的就是宿主那份），
    #   又**不会**触发那次危险的重复 exec。
    for _name, mod in list(sys.modules.items()):
        if _at_root(mod):
            return mod.NODE_CLASS_MAPPINGS or {}
    try:
        import nodes as _n
    except Exception:
        _n = None
    if _n is not None and _at_root(_n):
        return _n.NODE_CLASS_MAPPINGS or {}
    raise RuntimeError(
        "取不到 ComfyUI 的节点注册表（ComfyUI 根的 nodes 模块没加载？）。\n"
        "    本节点要在 ComfyUI 进程内运行，不能脱离宿主单跑。")


def _stored_int(value, default, name):
    """把存档里的 widget 值读成 int —— **读不出来就退回默认值并出声**。

    为什么需要它（开源用户实测会踩）：`widgets_values` 是**按位置**存的（见 `CONTRIBUTING.md` 铁律），
    而**别的前端件会往末尾追加 widget** —— 汉化插件、`control_after_generate`、以及任何
    `serialize` 不为 false 的扩展。本包一旦给节点新增参数，**老图**里那个末尾的值
    （常常是空串）就会落到新参数上 ⇒ 直接 `int("")` 当场炸、而且报错与"参数"毫无关系。
    这里退回默认值 + 打一行说明（铁律 15：退化分支必须可观测），**绝不**静默改成别的数。

    ⚠️ 2026-10-13 更正一处**前提写错**：本文原先写「前端会把自己加的 widget（`h3_i18n_bar`）
    追加在末尾」—— 但本包那个语言条是 `serialize: false`（见 `web/relay_kit_i18n_ui.js`），
    **它自己不落盘**。所以真实来源是**别家**扩展 / 宿主自己的 widget，不是我们的。
    这条兜底仍然保留（理由换成上面那句），只是别再把锅记在自己头上。
    """
    try:
        return int(value)
    except (TypeError, ValueError):
        print("[H3 Relay] ⚠ %s 的存档值 %r 读不成整数（老图按位置存 ⇒ 本版新增参数后槽位错位）"
              "⇒ 本次按默认值 %d 走；请在画布上重设一次再保存。" % (name, value, int(default)))
        return int(default)


#: 🧪 实验层（都改 conditioning 的 `minimax_refs`）总开关 `exp_anchor` 的取值。
#:   🔴 `auto`（默认）**必须放在第一位且作为默认值**：它 = 旧行为（只看配置文件），
#:      保证**存量 `_va.json` / `_tiha.json` 用户升级后零行为变化**。
EXP_ANCHOR_MODES = ("auto", "off", "voice_accum", "history_anchor", "both")


def _exp_gate(mode: str, run_id: str, stage_path_fn) -> tuple:
    """把 `exp_anchor` 与配置文件的存在性合成 `(want_va, want_ti, notes)`。

    三档语义（**缺一即不跑，绝不静默合成**）：
      - `auto`（默认）= 照旧只看配置文件：文件在就跑，不在就不跑。
      - `off` = 显式停用：文件在也不跑，并在 notes 里点名（否则"设了没反应"）。
      - `voice_accum` / `history_anchor` / `both` = 显式启用：**文件缺失 ⇒ 直接 raise**
        （那是用户显式要的功能，不许静默变成没发生）。
    """
    m = str(mode or "auto").strip().lower()
    if m not in EXP_ANCHOR_MODES:
        raise ValueError("exp_anchor=%r 不认识（可选：%s）。"
                         % (mode, " / ".join(EXP_ANCHOR_MODES)))
    try:
        p_va = VA_ADAPTER.config_path(stage_path_fn, run_id)
        p_ti = TIHA_ADAPTER.config_path(stage_path_fn, run_id)
        has_va, has_ti = os.path.isfile(p_va), os.path.isfile(p_ti)
    except Exception:
        # 🔴 run_id 为空 / 路径推不出来（**第 1 段合法**：独立段不填片名）⇒
        #    视作「没有配置文件」，**不许**在这里炸掉合法路径。
        #    显式模式仍然报错（那是用户显式要的功能），只是错因说得更准。
        if m not in ("auto", "off"):
            raise RuntimeError(
                "实验开关显式启用了 %s，但 run_id 是空 ⇒ 推不出 %s / %s 的路径。"
                "    先填片名（run_id），或把实验开关改回 auto / off。" %
                (m, VA_ADAPTER.CONFIG_NAME, TIHA_ADAPTER.CONFIG_NAME)) from None
        p_va = p_ti = ""
        has_va = has_ti = False
    notes: list = []
    if m == "auto":
        return has_va, has_ti, notes
    if m == "off":
        for name, has, path in (("voice_accum", has_va, p_va), ("history_anchor", has_ti, p_ti)):
            if has:
                notes.append("实验开关 off ⇒ %s（在 %s）**未生效**；要启用改成 auto 或 %s。"
                             % (name, os.path.basename(path), name))
        return False, False, notes
    want_va, want_ti = m in ("voice_accum", "both"), m in ("history_anchor", "both")
    for want, has, path, name in ((want_va, has_va, p_va, "voice_accum"),
                                 (want_ti, has_ti, p_ti, "history_anchor")):
        if want and not has:
            raise RuntimeError(
                "实验开关显式启用了 %s，但 %s 不存在。\n"
                "    那个文件不只是开关，还是**数据**（段号 → 说话人开口顺序 / 历史锚参数）;\n"
                "    先生成它，或把实验开关改回 auto（只看文件）或 off。" % (name, path))
    return want_va, want_ti, notes


def _upscaler_cls():
    """取上游学习式 3D 放大节点类（MIT，LBH-123-AI/Comfyui_Minimax_h3_latent_Upscaler）。

    走 ComfyUI 的节点注册表而不是 import 它的包 —— 那个包内有 `nodes/` 子包，
    与 ComfyUI 根 `nodes` 模块**同名**，按路径 import 迟早撞车。
    """
    cls = _comfy_registry().get(_UPSCALER_NODE)
    if cls is None:
        raise RuntimeError(
            "本节点是「%s」的 AV 打包 latent 适配器，需要先装作者的节点包（MIT）：\n"
            "    git clone https://github.com/LBH-123-AI/Comfyui_Minimax_h3_latent_Upscaler.git\n"
            "    放进 ComfyUI/custom_nodes/ 后重启 ComfyUI。\n"
            "权重与上面那个包共用同一目录 models/latent_upscale_models/，从作者处下载：\n"
            "    %s" % (_UPSCALER_NODE, _UPSCALER_MODELS_URL))
    return cls


def _upscaler_module():
    try:
        cls = _upscaler_cls()
    except RuntimeError:
        return None
    import sys as _sys

    return _sys.modules.get(getattr(cls, "__module__", None))


def _upscale_model_names() -> list:
    """放大权重清单 —— **优先直接调原作者的 `scan_models()`**。

    作者的目录就是 ComfyUI 标准的 `models/latent_upscale_models/`，过滤规则是
    `.pth/.safetensors` ⇒ 装了上游节点就**共用同一份权重文件**，本包不另立目录、不复制第二份。
    没装上游（本包照常加载）时按同一条规则自查。
    """
    mod = _upscaler_module()
    scan = getattr(mod, "scan_models", None)
    if callable(scan):
        try:
            names = [str(n) for n in (scan() or [])]
            if names:
                return names
        except Exception as e:
            print("[H3 Relay] 上游 scan_models() 失败，退回本包自查：%r" % e)
    names = [str(n) for n in (folder_paths.get_filename_list("latent_upscale_models") or [])
             if os.path.splitext(str(n))[1].lower() in (".pth", ".safetensors")]
    return names or ["(权重缺失：见 model_name 提示行的下载指引)"]


def _upscale_model_hint() -> str:
    return ("【填什么】H3 潜空间放大权重。\n"
            "放在 models/latent_upscale_models/ 下，重启后可选。\n"
            "下载：%s\n"
            "⚠ 普通 ESRGAN 放大模型不能用。" % _UPSCALER_MODELS_URL)


class H3RelayLatentUpscale:
    """🔍 画质域 · H3 AV latent **分块放大**（学习式 3D 潜空间放大，**零去噪**，块数自选）。

    为什么要有这个节点（2026-09-22，全流程 PREVIEW 真跑撞出来的）：
      上游 `MinimaxH3LatentUpscaler3D` ① **只吃普通 [B,C,T,H,W]**，直接喂 H3 的
      **AV 打包 latent（NestedTensor）会当场炸** `'NestedTensor' object has no attribute 'dim'`
      （实测：一采+二采全跑完，到 SR 那步才死）；② 内部分块**写死 32 帧**，用户没法按显存调。
      本节点 = 「拆包 → 逐块调上游 → 回包」的适配层，**零 patch 第三方包**。

    性质（与上游一致，实测核过源码）：
      · **不产生任何去噪/重采样** —— 只放大空间，**时间维原样不动**（`target_size=(t, H2, W2)`）
        ⇒ 钉住区逐位连续、口型、表演都不被改写；帧数域不变 ⇒ TrimAV 的 22 帧裁量照旧。
      · **音频流完全不碰** ⇒ 回包时原样带过，「音频不走 SR」是结构性成立，不是靠旁路接线。
      · 分块只是**显存/时间**的取舍：块内是模型的全部工作集 ⇒ 峰值显存 = 一块。
        `chunks=1` = 整段一次过（最快、最省计算，最吃显存）。
      · 拼接数学与上游同口径（两侧 replicate 填充 + 线性渐变权重 + 按累计权重归一），
        并有单测锁「分块结果 == 整段结果」（见 tests 第 27 组）。
        🔴 **那锁的是拼接数学，不是画面**：切块 = 换**跨块时间上下文**
        （`TemporalConv` 时间核 5；⚠️ 机理更正 2026-10-12：**不是**注意力块 —— 上游
        `_detect_arch()` 恒 `attn=False`，且 `load_state_dict(strict=True)` 能过）
        ⇒ `chunks>1` **会改画面**（实测逐帧 MAE 5.62/255，见 docs/05 排障表与 docs/02）。
        默认锁 1；只在显存真放不下时才升，**升完必须重看缝**。

    两个显存旋钮（2026-10-12 加第二个，低显存优先用第二个）：
      · `chunks`（**时间维**分块，旧）—— 省得最多，但**换掉跨块时间上下文** ⇒ 最伤画面。
      · `spatial_tiles`（**空间维**沿长边切条，新）—— 每块的**时间轴完整** ⇒ 时间上下文不丢；
        代价换成空间边界变窄，用 `spatial_overlap` + `seam_feather` 把接缝压进过渡带。
        🔴 它**同样会改画面**（边界处感受野变窄），只是"改在哪"不同 ⇒ 与 `chunks` 同一性质，
        不是"无副作用"档。单测锁的是**几何与拼接**（比例可精确表示时与整段**逐位相同**；
        除不尽时缝带误差不大于非缝带 ⇒ 没有额外阶跃），**画面必须人工目检**。
      · 两者可叠加：**时间分块在外、空间分块在内** ⇒ 峰值显存 = 一个时间块 × 一条空间块。
      · `spatial_tiles=1` ⇒ **完全走旧路径**（连上游调用方式都不变），逐位同旧版。

    ⚠ 域纪律：本节点属**画质域**（与 `H3RelayPost` 同域，一个动 latent 一个动像素）；
      时间轴（裁重叠）仍归 `H3RelayTrimAV`，音频仍归 `H3RelayAudioSeam`。
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "latent": ("LATENT", {
                    "tooltip": "【接法】接本段采样器的 latent 输出。\n⚠ 存给下一段的那份要取放大前的，否则下一段会再放大一次。",
                }),
                "model_name": (_upscale_model_names(), {"tooltip": _upscale_model_hint()}),
                "mode": (["scale by multiplier", "target dimensions", "megapixels"], {
                    "tooltip": "【怎么选】三种定尺寸方式，只生效选中的那一种：\n· scale by multiplier = 给倍数（最常用）；\n· target dimensions = 直填宽 × 高；\n· megapixels = 按像素预算。",
                }),
                "scale": ("FLOAT", {
                    "default": 2.0, "min": 1.0, "max": 4.0, "step": 0.05,
                    "tooltip": "【倍数模式】放大系数。1.0 = 不变，2.0 = 边长翻倍。\n⚠ 只放大不缩小，小于 1 会报错。",
                }),
                "width": ("INT", {
                    "default": 1280, "min": 64, "max": 8192, "step": 32,
                    "tooltip": "【目标尺寸模式】目标宽（像素），会自动对齐到 align 的倍数。",
                }),
                "height": ("INT", {
                    "default": 704, "min": 64, "max": 8192, "step": 32,
                    "tooltip": "【目标尺寸模式】目标高（像素），会自动对齐到 align 的倍数。",
                }),
                "megapixels": ("FLOAT", {
                    "default": 1.0, "min": 0.06, "max": 16.0, "step": 0.05,
                    "tooltip": "【兆像素模式】目标总像素。1.0 约 720p 量级；12 GB 显存建议 ≤1.5。",
                }),
                "chunks": ("INT", {
                    "default": 1, "min": 1, "max": 64, "step": 1,
                    "tooltip": "【显存·切时间】沿时间轴分几块跑。1 = 整段一次过（最快、最吃显存）。\n⚠ 切了会改画面 ⇒ 切完必须重看缝。上限由重叠决定，超了报错。",
                }),
                "overlap": ("INT", {
                    "default": CORE.UPSCALE_OVERLAP_DEFAULT, "min": 0, "max": 16, "step": 1,
                    "tooltip": "【块间重叠】分块时两侧各留几帧做渐变混合。默认 5。\n只在 chunks 大于 1 时起作用；0 = 硬切不混合。",
                }),
                "align": ("INT", {
                    "default": 32, "min": 1, "max": 512, "step": 1,
                    "tooltip": "【对齐网格】输出宽高取这个数的倍数。32 是放大模型的硬要求，别改小。",
                }),
                "precision": (["bf16", "fp16", "fp32"], {
                    "default": "bf16",
                    "tooltip": "【精度】bf16 / fp16 省显存；fp32 最稳但最慢。与权重自身精度一致最保险。",
                }),
                "device": (["cuda", "rocm", "cpu"], {
                    "default": "cuda",
                    "tooltip": "【算在哪】cuda / rocm 正常跑；cpu 只用于排查，实际慢得多。",
                }),
                "force_unload": ("BOOLEAN", {
                    "default": False,
                    "tooltip": "【跑完卸载】开 = 把放大模型退回内存腾显存；关（默认）= 留在显存。\n⚠ 分块时每块都要装卸一次，明显更慢。",
                }),
                "spatial_tiles": ("INT", {
                    "default": 1, "min": 1, "max": 16, "step": 1,
                    "tooltip": "【显存·切空间】沿长边切几块。1 = 不切（默认）。\n小显存：8G 跑 2MP 先试 2 块；不够再 3~4，通常到 4 就够。\n每块时间轴完整 ⇒ 比切时间（chunks）伤得轻；但切了会改画面，切完重看缝。",
                }),
                "spatial_overlap": ("INT", {
                    "advanced": True,
                    "default": CORE.UPSCALE_TILE_OVERLAP_DEFAULT, "min": 1, "max": 64, "step": 1,
                    "tooltip": "【空间重叠】相邻两条的重叠宽度（latent 格，1 格约 16 像素）。默认 8。\n重叠只当上下文用，核心区独占 ⇒ 不会重影。\n它换算力：长边 80 格、重叠 8 时 3 块多算约 40%。至少要装得下接缝羽化。",
                }),
                "seam_feather": ("INT", {
                    "advanced": True,
                    "default": CORE.UPSCALE_TILE_FEATHER_DEFAULT, "min": 0, "max": 16, "step": 2,
                    "tooltip": "【接缝羽化】过渡带宽度（latent 格）。默认 4；0 = 硬过渡。\n成片出现淡色差线时调到 8~12，并同步调大空间重叠。",
                }),
            },
        }

    RETURN_TYPES = ("LATENT", "STRING")
    RETURN_NAMES = ("latent", "report")
    FUNCTION = "upscale"
    CATEGORY = CATEGORY
    DESCRIPTION = ("H3 AV latent 分块放大：拆包→逐块调上游学习式 3D 放大器（零去噪、只放大空间、时间维不动）"
                   "→回包保留原音频；**两个显存旋钮**：`chunks`（时间维，最省但最伤画面）与 "
                   "`spatial_tiles`（空间维沿长边切条，**时间上下文不丢**，低显存优先用它）。"
                   "⚠ 两者都会改画面，见 docs/02 与 docs/05 排障表；切完必须重看缝。")

    @_node_errors("chunks", "overlap", "align", "spatial_tiles", "spatial_overlap", "seam_feather")
    def upscale(self, latent, model_name, mode, scale, width, height, megapixels,
                chunks, overlap, align, precision, device, force_unload,
                spatial_tiles=1, spatial_overlap=CORE.UPSCALE_TILE_OVERLAP_DEFAULT,
                seam_feather=CORE.UPSCALE_TILE_FEATHER_DEFAULT):
        import time
        import torch

        # 存档槽位错位的兜底（见 `_stored_int`）：老图的末尾空串可能落到这三个新参数上。
        spatial_tiles = _stored_int(spatial_tiles, 1, "spatial_tiles")
        spatial_overlap = _stored_int(spatial_overlap, CORE.UPSCALE_TILE_OVERLAP_DEFAULT,
                                      "spatial_overlap")
        seam_feather = _stored_int(seam_feather, CORE.UPSCALE_TILE_FEATHER_DEFAULT, "seam_feather")

        up_cls = _upscaler_cls()
        src = latent["samples"] if isinstance(latent, dict) else latent
        parts = CORE.streams_from_latent(latent)
        video = parts[0]
        audio = parts[1] if len(parts) > 1 else None
        if video.dim() == 4:
            n_frames = 1
        elif video.dim() == 5:
            n_frames = int(video.shape[2])
        else:
            raise ValueError("期望 latent [B,C,T,H,W]（或单帧 [B,C,H,W]），得到 %s。" % (tuple(video.shape),))

        plan = CORE.upscale_chunk_plan(n_frames, chunks=int(chunks), overlap=int(overlap))
        cfg = {"mode": mode}
        if mode == "scale by multiplier":
            cfg["scale"] = float(scale)
        elif mode == "target dimensions":
            cfg["width"] = int(width)
            cfg["height"] = int(height)
        elif mode == "megapixels":
            cfg["megapixels"] = float(megapixels)
        else:
            raise ValueError("未知 mode=%r（可选：scale by multiplier / target dimensions / megapixels）。" % mode)

        # latent→像素 的倍数由上游定义，我们不自已猜一个（空间分块的请求尺寸也要用它）
        import sys as _sys
        _mod = _sys.modules.get(type(up_cls).__module__)
        vae_ds = int(getattr(_mod, "VAE_DOWNSAMPLE", 0) or 0)
        ds = vae_ds if vae_ds > 0 else 16

        # —— 空间分块计划：**只在 spatial_tiles>1 时启用**（=1 完全走旧路径 ⇒ 逐位同旧版）——
        #    目标尺寸必须**自己先算出来**（与上游 execute 同口径）：让每块按自己的尺寸四舍五入
        #    ⇒ 各块拼不回整段尺寸。
        #    ⚠ 4D 单帧 latent 也吃这条（`temporal_tile_upscale` 会先补成 T=1 的 5D 再交给 `_one`）
        #      —— 不许静默跳过用户显式设的旋钮（铁律 15）。
        tile_plan = None
        if int(spatial_tiles) > 1:
            h_in, w_in = int(video.shape[-2]), int(video.shape[-1])
            if mode == "scale by multiplier":
                h_out, w_out = CORE.upscale_out_size(h_in, w_in, mode, scale=float(scale),
                                                     align=int(align), downsample=ds)
            elif mode == "target dimensions":
                h_out, w_out = CORE.upscale_out_size(h_in, w_in, mode, width=int(width),
                                                     height=int(height), align=int(align),
                                                     downsample=ds)
            else:
                h_out, w_out = CORE.upscale_out_size(h_in, w_in, mode, megapixels=float(megapixels),
                                                     align=int(align), downsample=ds)
            if (h_out, w_out) != (h_in, w_in):      # 尺寸不变 ⇒ 上游会原样直通，切了也没意义
                tile_plan = CORE.spatial_tile_plan(
                    h_in, w_in, h_out, w_out, tiles=int(spatial_tiles),
                    overlap=int(spatial_overlap), feather=int(seam_feather),
                    align=int(align), downsample=ds)

        if device == "cuda" and torch.cuda.is_available():
            torch.cuda.reset_peak_memory_stats()

        t0 = time.time()
        calls = [0]

        def _call(seg, mode_cfg):
            calls[0] += 1
            r = up_cls.execute(latent={"samples": seg}, model_name=model_name, mode=mode_cfg,
                               align=int(align), enable_temporal_chunking=False,
                               force_unload=bool(force_unload), device=device, precision=precision)
            r = getattr(r, "result", r)
            d = r[0] if isinstance(r, (tuple, list)) else r
            out = d["samples"] if isinstance(d, dict) else d
            if not torch.is_tensor(out):
                raise ValueError("上游放大器没返回张量（得到 %s）—— 检查权重与 models/latent_upscale_models/。" % type(out))
            return out.unsqueeze(2) if out.dim() == 4 else out

        def _one(seg):
            """一个**时间块**的放大：空间分块开着就在这一块内部再切空间条。"""
            if tile_plan is None:
                return _call(seg, cfg)
            return CORE.spatial_tile_upscale(
                seg,
                lambda s, th, tw: _call(s, {"mode": "target dimensions",
                                            "width": int(tw) * ds, "height": int(th) * ds}),
                tile_plan)

        v2 = CORE.temporal_tile_upscale(video, _one, plan)
        dt = time.time() - t0

        if int(v2.shape[-1]) == int(video.shape[-1]) and int(v2.shape[-2]) == int(video.shape[-2]):
            print("[H3 Relay] 潜空间放大：目标尺寸与输入相同 ⇒ 原样直通（T=%d，latent %dx%d 未变）"
                  % (n_frames, int(video.shape[-1]), int(video.shape[-2])))

        samples_out = v2 if audio is None else CORE._nested_pair(v2, audio, template=src)
        packed = {"samples": samples_out}

        tile_txt = ""
        if tile_plan is not None:
            tile_txt = (" | 空间分块 长边%s 共%d条（请求%d）重叠%d格 羽化%d格 输出%d×%d（网格%d）"
                        % (tile_plan["axis"], tile_plan["n_tiles"], tile_plan["tiles_requested"],
                           tile_plan["overlap"], tile_plan["feather"],
                           tile_plan["h_out"], tile_plan["w_out"], tile_plan["grid"]))
        px = (" | 像素域 %dx%d → %dx%d" % (int(video.shape[-1]) * vae_ds, int(video.shape[-2]) * vae_ds,
                                           int(v2.shape[-1]) * vae_ds, int(v2.shape[-2]) * vae_ds)) if vae_ds else ""
        peak = ""
        if device == "cuda" and torch.cuda.is_available():
            peak = " | 显存峰值 %.1f MB" % (torch.cuda.max_memory_allocated() / 1024 ** 2)
        report = ("放大 %dx%d → %dx%d latent（align=%d）%s | 时间维不动 T=%d | "
                  "块数 请求%d→实际%d（每块 %d 帧，overlap %d）| 音频流原样 %s | 上游调用 %d 次 | "
                  "精度 %s/%s | force_unload=%s | 合法块数上限 %d%s%s | %.1fs"
                  % (int(video.shape[-1]), int(video.shape[-2]), int(v2.shape[-1]), int(v2.shape[-2]),
                     int(align), px, n_frames, int(chunks), plan["n_chunks"], plan["chunk_frames"],
                     int(overlap),
                     tuple(audio.shape) if audio is not None else "无",
                     calls[0], precision, device, bool(force_unload),
                     CORE.max_upscale_chunks(n_frames, int(overlap)), tile_txt, peak, dt))
        print("[H3 Relay] " + report)
        return (packed, report)


class H3RelayLatentSave:
    """把本段采样器输出的 AV latent 落盘。"""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "latent": ("LATENT", {
                    "tooltip": "【接法】接本段采样器的 latent 输出，存成文件给下一段接力。\n⚠ 走「采样 → 放大 → 再采样」时接第一次采样的输出，别接放大后的。",
                }),
                "run_id": ("STRING", {
                    "default": "relay",
                    "tooltip": "【填什么】片名，例如 myfilm。\n⚠ 必须与 Copy Bridge 一字不差，否则找不到文件。换片必须换名（同名会覆盖旧文件）。",
                }),
                "stage_index": ("INT", {
                    "default": 0, "min": 0, "max": 9999, "step": 1,
                    "tooltip": "【填什么】第 1 段填 0，第 2 段填 1……\n⚠ 必须与 Copy Bridge 相同（用 Chain 节点可自动改）。",
                }),
                "note": ("STRING", {
                    "default": "",
                    "tooltip": "【可留空】随手写个备注，便于事后分辨版本。",
                }),
            },
        }

    RETURN_TYPES = ("LATENT", "STRING")
    RETURN_NAMES = ("latent", "path")
    FUNCTION = "save"
    CATEGORY = CATEGORY
    # 没有下游消费时也必须执行 —— 落盘本身就是它的产物。
    OUTPUT_NODE = True
    DESCRIPTION = "把本段 AV latent 落盘，供下一段做 latent 续接（零重编码）。"

    @_node_errors("run_id")
    def save(self, latent, run_id, stage_index, note=""):
        path = _stage_path(run_id, stage_index)
        lineage = _stage_lineage(run_id, stage_index)
        CORE.save_av_latent(latent, path, note=note, lineage=lineage)
        size_mb = os.path.getsize(path) / 1024 ** 2
        print(
            "[H3 Relay] 已存 stage %d → %s (%.1f MB)\n            %s\n            头：run=%s 父段=%s pack=%s"
            % (int(stage_index), path, size_mb, CORE.describe_latent(latent),
               lineage["run_id"],
               ("stage %d" % lineage["parent_stage"]) if lineage["parent_stage"] != "" else "无（首段）",
               lineage["pack"])
        )
        return (latent, path)


class H3RelayLatentLoad:
    """读回上一段的 AV latent，供续接。"""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "run_id": ("STRING", {
                    "default": "relay",
                    "tooltip": "【填什么】与 Latent Save 一致的片名。不一致 = 找不到文件，直接报错。",
                }),
                "stage_index": ("INT", {
                    "default": 0, "min": 0, "max": 9999, "step": 1,
                    "tooltip": "【填什么】填本段段号（与桥、保存一致）。节点读「本段 − 1」那一份。\n填 0 = 第 1 段，没有上一段 ⇒ 交空上下文，桥自动直通。这是正常的。",
                }),
            },
            "optional": {
                "explicit_path": ("STRING", {
                    "default": "",
                    "tooltip": "【可留空】留空 = 按片名 + 段号自动找；填了 = 直接读这个文件。",
                }),
            },
        }

    RETURN_TYPES = ("LATENT", "STRING")
    RETURN_NAMES = ("context_latent", "info")
    FUNCTION = "load"
    CATEGORY = CATEGORY
    DESCRIPTION = ("读回上一段的 AV latent，交给「Copy Bridge」做续接。\n"
                   "第 1 段（stage_index = 0）没有上一段可读 ⇒ 本节点交一个「空上下文」，\n"
                   "桥收到后自动直通（不续接、不裁帧）；第 2 段起读上一段的落盘文件、真续接。")

    @_node_errors("run_id", "explicit_path")
    def load(self, run_id, stage_index, explicit_path=""):
        idx = int(stage_index) - 1
        explicit = (explicit_path or "").strip()
        # 必须先判段号再进 _stage_path：否则 stage_index=0 会先撞上
        # 「stage_index 不能为负」的误导性报错，下面的引导文案永远到不了。
        if idx < 0 and not explicit:
            # 第 1 段没有上一段可读 —— 这是**正常情况**，不是错误。
            #
            # 🔴 为什么这里**不能 raise**（2026-09-28 改，原为 RuntimeError）：
            #   宿主在提交前会把 **bypass 的节点"溶解"掉**、把它的输入直接接到下游。
            #   本节点没有 LATENT 输入 ⇒ 一旦被旁路，桥的 required `context_latent`
            #   会**直接从 prompt 里消失** ⇒ 整个 prompt 被
            #   `prompt_outputs_failed_validation / Required input is missing` 拒掉（实测）。
            #   ⇒ 正确做法是**交一个「空上下文」**，由桥识别后走它自己的直通分支
            #     （桥会打日志、`trim_frames` 输出 0）。
            # ⚠️ 「该续接却没有来源」的硬拦**没有丢** —— 它在桥里：
            #   `stage_index >= 1` 且上下文为空 ⇒ 仍然 raise（宁可不跑，也不出无续接的哑片）。
            print("[H3 Relay] 读上段 latent：第 1 段无上一段 → 交空上下文（桥会直通、不裁帧）",
                  flush=True)
            return ({"samples": None}, "stage 0：无上一段（空上下文）")
        path = explicit or _stage_path(run_id, idx)
        latent = CORE.load_av_latent(path)
        info = CORE.describe_latent(latent)
        print("[H3 Relay] 已读 stage %d ← %s\n            %s" % (idx, path, info))
        return (latent, info)


class H3RelayTrimAV:
    """裁掉续接段头部的重叠帧（视频 + 音频同裁，A/V 不失步）。

    为什么必须裁：钉住区的前 ``trim_frames`` 帧是模型对上一段尾部的**重生成**
    （实测与原帧逐帧 MAE ≈ 6/255），属于过渡产物。不裁就拼，接缝处会看到
    约 0.9 秒的重播。

    口径与作者一致：裁 **decode 之后的像素帧**（不是裁 latent）——
    latent 的帧跨度按 token 在序列里的相位（k%5）决定，砍头会让相位错位。

    为什么还要多裁几帧（沉降）：钉住区之后模型还会先**复现**上一段若干帧，
    然后才切到本段 prompt；切换点**逐段不同**，可能落在钉住区之外。
    `settle_frames=0`（0.5.0 起默认）时**一帧沉降都不裁** —— 实测「裁沉降」才是缝处
    跳帧的源头（裁 0 帧跳 0.020「几乎无感」／裁 8 帧 0.044／裁 16 帧 0.055），
    留下的只是**清晰度的渐变**。想自动量出切换点并裁掉：填 `-1`。
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "images": ("IMAGE", {
                    "tooltip": "【接法】接本段画面解码（VAEDecode）的输出。\n收到的是含重复开头的完整画面，它把重复部分裁掉。",
                }),
                "trim_frames": ("INT", {
                    # 0.6.36：默认 -1 = **未接线**。旧默认 0 让「忘接线」与「明确不裁」
                    #   在服务端完全同形 ⇒ 漏接只能表现为静默裁 0（接缝重播）。
                    "default": -1, "min": -1, "max": 362, "step": 1,
                    "tooltip": "【不用填】从 Copy Bridge 第 3 路输出接线。\n"
                               "接上线：第 1 段自动 0，第 2 段起自动等于拷贝帧数。\n"
                               "-1（默认）= 未接线，运行时报错并说明怎么接；\n"
                               "0 = 明确不裁（首段 / 独立段）。",
                }),
                "fps": ("FLOAT", {
                    "default": 24.0, "min": 1.0, "max": 120.0, "step": 0.001,
                    "tooltip": "【不用动】H3 固定 24 帧/秒，音频按这个换算一起裁。",
                }),
            },
            "optional": {
                "audio": ("AUDIO", {
                    "tooltip": "【务必接线】从音频解码（VAEDecodeAudio）接过来。\n画面和声音按同一帧数一起裁，音画才不会串位。\n不接的话声音会比画面长出一截。",
                }),
                # ⚠ 新 widget 必须追加在**最后一个**：widgets_values 按位置对应，
                #    插在中间会让旧工作流里它后面的取值整体错位（见 CHANGES 0.2.1）。
                "settle_frames": ("INT", {
                    "default": 0, "min": -1, "max": 36, "step": 1,
                    "tooltip": "【保持 0】裁不裁「沉降区」：复制区之后模型还会先复现上一段几帧。\n那几帧略糊，但裁它们正是缝处跳帧的源头（实测裁 0 帧跳 0.020、裁 16 帧 0.055）。\n· 0（默认）= 只裁复制区，缝处无跳；-1 = 自动；N = 固定多裁 N 帧。",
                }),
                # ⚠ 继续追加在**最后**（同上铁律）：缝帧重影，2026-09-15 新增。
                #    🔴 默认 0（关）：数值上能压平最大单帧跳，但**观感实测更差**——
                #    总位移守恒（只 −7%）、异常帧数 1→2（一跳变两跳 = 卡两下），
                #    且重影帧本身是「鬼影」（内容不属于任何一段）。详见 relay_core 注释。
                "seam_ghost": ("INT", {"advanced": True,
                    "default": 0, "min": 0, "max": 6, "step": 1,
                    "tooltip": "【保持 0】把裁后首帧换成「上段末帧 ⊕ 本段首帧」的混合。\n实测它只把一跳拆成两跳、总位移没减 ⇒ 观感是「卡两下」，比单帧瞬跳更明显。\n· 0（默认）= 关；大于 0 = 开（确知有收益时用）。",
                }),
                "seam_ghost_alpha": ("FLOAT", {"advanced": True,
                    "default": 0.5, "min": 0.0, "max": 1.0, "step": 0.05,
                    "tooltip": "【仅重影档】上段末帧的权重（0.5 = 对半）。默认档下无效。",
                }),
                # ⚠ 继续追加在**最后**（同上铁律）：画质域修复，2026-09-16 新增。
                #    背景：settle_frames 默认改 0（不裁沉降）后，成片段头会保留几帧「重绘糊」。
                #    裁它 → 引入跳帧；不裁 → 留糊。**第三条路 = 画质域修**（不裁、不动时间轴）。
                "settle_sharpen": ("FLOAT", {"advanced": True,
                    "default": 0.0, "min": 0.0, "max": 1.5, "step": 0.05,
                    "tooltip": "【按需开】糊区锐化：对裁后开头几帧做锐化，强度从缝端最强衰减到 0。\n· 0（默认）= 关；0.4~1.0 推荐（先试 0.6，再看有没有白边或噪点）。\n⚠ 只能恢复对比度，救不回丢掉的细节。",
                }),
                "settle_sharpen_frames": ("INT", {"advanced": True,
                    "default": 24, "min": 0, "max": 64, "step": 1,
                    "tooltip": "【配合糊区锐化】作用帧数（从裁后首帧起，强度衰减到 0）。\n默认 24 帧约 1 秒；实测糊区 16~20 帧内回到基准。",
                }),
                # ⚠ 继续追加在**最后**（同上铁律）：低频残差传递，2026-09-16 新增。
                #    借鉴 ComfyUI_MiniMaxH3_Director 的 `match_export_opening_grade`
                #    （`_lowfreq_appearance_pull`）：只吸收上段的低频色档/布光，保留本段细节。
                "lowfreq_pull": ("FLOAT", {"advanced": True,
                    "default": 0.0, "min": 0.0, "max": 1.0, "step": 0.05,
                    "tooltip": "【按需开】低频残差：把本段开头几帧的色档/亮度拉向上段末帧。\n只动低频 ⇒ 不复制姿态、无重影。\n0（默认）= 关；0.7 = 中等；1.0 = 最强（缝点阶跃可到 0.0011）。\n⚠ 与「跨段统计匹配」作用重叠，二选一。",
                }),
                "lowfreq_frames": ("INT", {"advanced": True,
                    "default": 12, "min": 0, "max": 48, "step": 1,
                    "tooltip": "【配合低频残差】作用帧数（权重衰减到 0）。\n参考实现用 12 帧，实测缝区影响就在前 12 帧内。",
                }),
                "lowfreq_blur": ("INT", {"advanced": True,
                    "default": 16, "min": 8, "max": 128, "step": 8,
                    "tooltip": "【低频尺度】默认 16 就够。\n16 → 12 帧约 1.6 秒；64 → 同一段约 22 秒，而缝点阶跃只从 0.0024 降到 0.0011。\n⇒ 除非专治大面积色档漂移，别加到 64；32 是折中。",
                }),
                # ⚠ 继续追加在**最后**（同上铁律）：后处理层补全，2026-09-16。
                #    以下四个都是**画质域修复**：帧数守恒、不动音频、不动时间轴
                #    ⇒ 结构上不可能引入跳帧。默认全 0（关），旧行为逐位不变。
                "deconv_strength": ("FLOAT", {"advanced": True,
                    "default": 0.0, "min": 0.0, "max": 1.0, "step": 0.05,
                    "tooltip": "【按需开】本段开头反卷积去模糊：按假设的模糊核逆推原始信号。\n与锐化的区别：锐化只是放大高频（噪声一起放大），反卷积理论上能还原细节。\n0（默认）= 关；0.5~1.0 = 混合比。太大易出振铃。",
                }),
                "deconv_radius": ("FLOAT", {"advanced": True,
                    "default": 1.5, "min": 0.5, "max": 6.0, "step": 0.5,
                    "tooltip": "【配合反卷积】假设的模糊半径（像素）。糊得越重越大；太大易出振铃。",
                }),
                "detail_borrow": ("FLOAT", {"advanced": True,
                    "default": 0.0, "min": 0.0, "max": 1.0, "step": 0.05,
                    "tooltip": "【按需开】段体高频迁移：本段开头保留自己的低频，高频换成段体的结构。\n与低频残差互补。0（默认）= 关。\n⚠ 本段开头与段体差异大时会带出纹理错位。",
                }),
                "detail_blur": ("INT", {"advanced": True,
                    "default": 9, "min": 3, "max": 64, "step": 2,
                    "tooltip": "【配合高频迁移】高频分离尺度：越大被搬走的高频越粗。",
                }),
                "hist_match": ("FLOAT", {"advanced": True,
                    "default": 0.0, "min": 0.0, "max": 1.0, "step": 0.05,
                    "tooltip": "【按需开】直方图匹配：把本段开头的色阶分布对齐到段体。\n比低频残差更强 —— 那个只对齐均值，这个对齐整条分布。\n0（默认）= 关；1 = 完全对齐。",
                }),
                "wb_match": ("FLOAT", {"advanced": True,
                    "default": 0.0, "min": 0.0, "max": 1.0, "step": 0.05,
                    "tooltip": "【按需开】灰世界白平衡：把本段开头的 R:G:B 比例（色温）对齐到段体。\n与低频残差正交（那个管亮度总量）。对症色温滑档；增益限幅 ±25%。\n0（默认）= 关。",
                }),
                # 🔴 2026-09-17 新增（RESEARCH_seam_frontier §2 方向二）：**跨段**统计匹配。
                #   与上面三个的区别：hist_match / wb_match / deconv / detail_borrow 都是
                #   「段头 ↔ **本段段体**」（段内）；本组是「段头 ↔ **上段末帧**」（**段间**），
                #   治的正是成片缝上那个亮度阶跃（copy 桥实测 0.0402）。
                #   ⚠ 铁律：新 widget 一律**追加在 optional 末位**（旧工作流取值不前移）。
                "match_prev": ("FLOAT", {"advanced": True,
                    "default": 0.0, "min": 0.0, "max": 1.0, "step": 0.05,
                    "tooltip": "【按需开】跨段统计匹配：把本段开头的亮度/色档对齐上段尾部走向。\n只对齐统计量、不复制姿态 ⇒ 无重影；带噪声闸，不会把好帧修坏。\n0（默认）= 关；建议 0.5。\n⚠ 目前只有负向证据：近似参照路线实测更差（0.0007→0.0039）；\n拷贝桥下尚无正向实测 ⇒ 别默认开。",
                }),
                "match_prev_frames": ("INT", {"advanced": True,
                    "default": 12, "min": 1, "max": 96, "step": 1,
                    "tooltip": "【配合跨段统计匹配】作用帧数：从裁后首帧起，权重衰减到 0。",
                }),
                "match_prev_gain_max": ("FLOAT", {"advanced": True,
                    "default": 1.15, "min": 1.0, "max": 2.0, "step": 0.05,
                    "tooltip": "【护栏】逐通道对比度增益上限，防止把已对好的内容改坏。",
                }),
                "match_prev_offset_max": ("FLOAT", {"advanced": True,
                    "default": 0.06, "min": 0.0, "max": 0.3, "step": 0.01,
                    "tooltip": "【护栏】逐通道亮度/色度偏移上限，防止整段换色。",
                }),
                # 🔴 2026-09-21 新增（E3/E4 可达性改造）：观测层的 run 标识。
                #   E3（DTW）/ E4（漂移）都是**只读观测**，不该依赖裁量开关（settle<0）才能执行。
                #   填了 ⇒ E4 把本段外观三元组追加进 <run>/appearance_log.jsonl 并读历史算漂移曲线；
                #   留空（默认）⇒ 只算本段三元组、不写盘，行为与加此 widget 之前一致。
                "run_id": ("STRING", {"default": "",
                    "tooltip": "【观测】可留空：与 Latent Save 一致的片名。\n填了才会把每段的外观统计写进 <run>/appearance_log.jsonl。",
                }),
                # 🔴 2026-09-22 新增（音频代际 2 → 1）：本段音频的 **PCM 边车**。
                #    默认**开**：它只多写一个文件，节点输出与成片**逐位不变**（不是"改行为"），
                #    所以不受"新功能默认关"的约束 —— 关掉它只是让拼接退回逐段解 mp4 音频。
                "save_pcm": ("BOOLEAN", {
                    "default": True,
                    "tooltip": "【建议保持开】开（默认）= 额外存一份无损 PCM。\n拼接直读它 ⇒ 音频只编码一代；关 = 不写（每段约 2 MB），退回 mp4 解码。",
                }),
                # 🔴 2026-09-25 新增（本仓作者 拍板）：只读观测的总闸。**默认关**。
                #    本地跑批要常看这几行 ⇒ 入口显式传 True（l1_api / chain_auto.sh）。
                "diagnostics": ("BOOLEAN", {
                    "default": False,
                    "label_on": "🧪 诊断开（+3 路观测）", "label_off": "诊断关（默认）",
                    "tooltip": "【默认关】三路只读观测，都不参与裁量，关掉不改变任何输出（纯打印）：\n· 段间对齐代价；· 裁量→跳跃曲线；· 外观三元组 + 漂移曲线（需填 run_id）。\n开 = 多约 0.4 秒/段 CPU，零 GPU。",
                }),
            },
        }

    RETURN_TYPES = ("IMAGE", "AUDIO", "STRING", "IMAGE")
    RETURN_NAMES = ("images", "audio", "report", "prev_tail")
    FUNCTION = "trim"
    CATEGORY = CATEGORY
    DESCRIPTION = (
        "裁掉续接段头部的重叠帧，并自动把钉住区之后那段「复现帧」一并裁掉"
        "（默认 settle_frames=0，只裁钉住区）；视频与音频同裁，避免重播与音画失步。"
    )

    @_node_errors("trim_frames", "fps", "settle_frames", "save_pcm")
    def trim(self, images, trim_frames=-1, fps=24.0, audio=None, settle_frames=0,
             seam_ghost=0, seam_ghost_alpha=0.5,
             settle_sharpen=0.0, settle_sharpen_frames=24,
             lowfreq_pull=0.0, lowfreq_frames=12, lowfreq_blur=16,
             deconv_strength=0.0, deconv_radius=1.5,
             detail_borrow=0.0, detail_blur=9,
             hist_match=0.0, wb_match=0.0,
             match_prev=0.0, match_prev_frames=12,
             match_prev_gain_max=1.15, match_prev_offset_max=0.06,
             run_id="", save_pcm=True, diagnostics=False):
        CONTRACT.enforce()   # 裁帧算术同样依赖上游网格，先过契约
        # 服务端防线：widget 的 min=1.0 只挡 UI，API 提交 fps=0/NaN 会一路除到底
        try:
            fps = float(fps)
        except (TypeError, ValueError):
            fps = float("nan")
        if not math.isfinite(fps) or fps <= 0:
            raise RuntimeError(
                "fps 必须是 (0, +∞) 内的有限正数，得到 %r。\n"
                "    H3 固定 24 帧/秒——把「Trim AV」的 fps 改回 24 即可（音频按它换算着一起裁）。"
                % (fps,)
            )
        pin = int(trim_frames)
        # 0.6.36 防漏连：未接线时 widget 停在 -1。不拦就静默裁 0 ⇒ 接缝重播上一段开头，
        #   而帧数、音频、四条断言全都正常（静默坏片）。V1 / V3 / API 三个出口都走这里。
        if pin < 0:
            raise RuntimeError(
                "「Trim AV」的 trim_frames 未接线（%d）：本段不会裁掉重叠的开头。\n"
                "    · 续接段：把「Copy Bridge」第 3 路（trim_frames）接到这个输入；\n"
                "    · 首段 / 独立段确实不裁：把它改成 0。\n"
                "    为什么拦：漏接时这里取 0 ⇒ 接缝处重播上一段开头（22 帧窗约 0.9 秒），\n"
                "    而帧数、音频与四条断言全都正常，看不出问题。" % pin
            )
        before = int(images.shape[0])
        if pin <= 0:
            msg = "[H3 Relay] 裁 0 帧 → 不裁（独立段或纯首段）。"
            print(msg)
            # 第 1 段（也是本段音频最完整的一段）同样要落边车 —— 否则成片第一段白丢一代。
            _u, _n = _pcm_sidecar(audio, save_pcm)
            if _n:
                # ⚠ 2026-09-28 修：原先写 `print(msg + _n)`，而 `msg` 上面已 print 过一次
                #   ⇒ 日志里同一句出现两遍（对比下面主路径的 `print(_n.strip("\n"))`）。
                #   边车成功时才多这一行，所以只在 pin>0 的日志里看不见这个重复。
                print(_n.strip("\n"))
            _r = (images, CORE.audio_to_fp32(audio), msg, images[:1])
            return {"ui": {CORE.PCM_UI_KEY: [_u]}, "result": _r} if _u else _r

        # 沉降帧：钉住区之后模型还会先复现上一段若干帧才切到本段 prompt。
        # 切换点是**逐段不同的量**，所以默认让它自己量（-1），而不是让用户猜。
        want = int(settle_frames)
        bj_note = ""
        if want < 0:
            settle, jump, base = CORE.detect_settle(images, pin)
            why = ("自动检测：切换信号 %.1f / 基准 %.1f（帧差突变/锐度塌陷/色档收敛）" % (jump, base)) if settle \
                else ("自动检测：未检出切换点（帧差 %.1f / 基线 %.1f，无突变亦无锐度塌陷）" % (jump, base))
            # 🔴 2026-09-15 新增：**边界跳帧观测**。沉降公式在「跳变正好落在钉住区边界」
            #   （j == pin-1）时返回 0 —— 语义没错，但**掩盖了「边界本身是断的」**。
            #   故单独量出来报给用户：**裁切治不了跳变，但至少要看得见。**
            bj, d_edge, d_base = CORE.boundary_jump_ratio(images, pin)
            if bj > CORE.BOUNDARY_JUMP_WARN:
                bj_note = ("\n           ⚠ **边界跳帧 %.1f×**（钉住区末帧→首帧新内容：帧差 %.4f / 段内基线 %.4f）"
                           "—— 这是模型接续处的断点；**多裁只是把断点往后挪，治不了它**。"
                           % (bj, d_edge, d_base))
            # 多通道观测剖面：**节点实测的 raw decode 数据**，成片里看不到
            # （H.264 编码会改变锐度/色阶基准 → 离线用成片反推必错）。
            # 本仓作者 2026-09-15：「不只是锐度，色阶、明暗等都需要」——沉降检测本就三路，只报一路=只开三分之一窗。
            prof = CORE.observation_profile(images, pin)
            if prof["sharp"]:
                n_sh = 18
                bj_note += (
                    "\n           观测剖面（钉住区后逐帧，节点实测 raw decode）\n"
                    "             锐度÷基准 : %s%s\n"
                    "             亮度      : %s%s\n"
                    "             色阶 R/G/B: %s / %s / %s\n"
                    "             帧差      : %s%s\n"
                    "             基准：锐度 %.6f；体区色档 %.4f / %.4f / %.4f"
                    % (" ".join("%.2f" % v for v in prof["sharp"][:n_sh]),
                       " …" if len(prof["sharp"]) > n_sh else "",
                       " ".join("%.4f" % v for v in prof["luma"][:12]),
                       " …" if len(prof["luma"]) > 12 else "",
                       " ".join("%.4f" % v for v in prof["rgb"][0][:10]),
                       " ".join("%.4f" % v for v in prof["rgb"][1][:10]),
                       " ".join("%.4f" % v for v in prof["rgb"][2][:10]),
                       " ".join("%.4f" % v for v in prof["diff"][:18]),
                       " …" if len(prof["diff"]) > n_sh else "",
                       prof["ref_sharp"], prof["ref_rgb"][0], prof["ref_rgb"][1], prof["ref_rgb"][2]))
        else:
            settle, why = want, "手动指定"

        # 🧪 E3 DTW 残留量（调研 §11-A）：只读观测，**不进裁量契约**。
        #   🔴 2026-09-21 移出 `want<0` 分支 —— 原位置使产线 settle=0（默认）时**永不执行**
        #      ⇒ 功能等于不存在（《开发验收纪律》：产线路径可达 + 有消费者才叫完成）。
        #      观测层不该依赖裁量开关（观测 ≠ 裁量）。
        #   与 0.5.0 第 4 路（scan_head_repeat 的最小 MAE 单点）互补：
        #   那个答「从 pin 起连续复现几帧」，这个答「整段对齐下来
        #   平均每步多贵」⇒ 能看出残留强度与不连续形态。
        #   ⚠ 故意不喂给 detect_settle：DTW 路把「运动中的相似姿态」
        #     也给低代价，单独当裁量会多吃内容（2026-09-20 定）。
        # 🔴 2026-09-25 本仓作者 拍板：三路只读观测**默认关**（`diagnostics=False`）。
        #   实测三路合计 **0.361 s/段**（0.796MP/90 帧）—— 对外是纯开销；本地要常看 ⇒ 入口传 True。
        #   注意它们**不参与任何裁量**（纯打印）⇒ 关掉不改变任何帧/latent/音频。
        #   （`want<0` 分支里的 detect_settle / boundary_jump_ratio / observation_profile
        #    属**裁量链路**，不受本开关影响。）
        _diag = bool(diagnostics)
        _dtw = CORE.head_repeat_dtw(images, pin) if _diag else None
        if _dtw:
            bj_note += (
                "\n           🧪 DTW 对齐代价（只读观测，不改裁量）："
                "窗→钉住区 %.5f｜比较 %d vs %d 帧"
                % (_dtw["align_cost"],
                   _dtw["frames"][0], _dtw["frames"][1]))
        # 裁量→跳跃曲线：成片缝 = raw[pin-1] → raw[pin+settle]（相隔 settle+1 帧），
        # **裁得越多、跳得越大**（本仓作者：裁切=时间跳跃=跳切）。裁之前就把它算出来供权衡。
        curve = CORE.trim_jump_curve(images, pin) if _diag else None
        if curve:
            bj_note += ("\n           裁量→跳跃曲线（settle : 归一跳跃）：%s"
                        % "  ".join("%d:%.1f×" % (s, r) for s, r in curve[:13]))
        # 🧪 E4 漂移曲线（调研 §11-B）：逐段外观三元组 + 跨段斜率，**只读观测**。
        #   🔴 2026-09-21 接进产线路径（此前 `nodes.py` 零引用 = 死代码）。
        #   run_id 空（默认）⇒ 只算本段三元组、不写盘；填了 ⇒ 追加 <run>/appearance_log.jsonl
        #   并读全部历史算 drift_curve。**同一把尺子逐段量**（segment_appearance_stats 统一口径）。
        if _diag:
            try:
                _st = CORE.segment_appearance_stats(images)
                bj_note += ("\n           🧪 外观三元组（E4 只读）：mean %.5f ｜ std %.5f ｜ 锐度 %.5f"
                            % (float(_st[0]), float(_st[1]), float(_st[2])))
                if run_id:
                    _rd = os.path.join(_RELAY_ROOT, str(run_id))
                    os.makedirs(_rd, exist_ok=True)
                    _lg = os.path.join(_rd, "appearance_log.jsonl")
                    _hist = []
                    if os.path.isfile(_lg):
                        with open(_lg, "r", encoding="utf-8") as _fh:
                            for _ln in _fh:
                                if _ln.strip():
                                    _hist.append(tuple(json.loads(_ln)["stats"]))
                    _hist.append(tuple(float(v) for v in _st))
                    with open(_lg, "a", encoding="utf-8") as _fh:
                        _fh.write(json.dumps({"stats": [float(v) for v in _st]},
                                             ensure_ascii=False) + "\n")
                    if len(_hist) >= 2:
                        _dc = CORE.drift_curve(_hist)
                        bj_note += ("\n           🧪 漂移曲线（E4，累计 %d 段）：亮度斜率 %+.5f ｜ "
                                    "对比度 %+.5f ｜ 锐度 %+.5f（后两项无量纲、可跨片比）"
                                    % (len(_hist), _dc["slope"], _dc["std_slope"], _dc["sharp_slope"]))
            except Exception as _e:      # 观测失败**绝不阻断渲染**，但必须可见（不静默）
                bj_note += "\n           🧪 E4 观测失败（不影响裁切）：%r" % (_e,)
        if pin + settle >= before:
            settle = max(0, before - 1 - pin)
            why += "（已夹到本段长度上限）"

        n = pin + settle
        out = CORE.trim_head_frames(images, n)
        # 🔴 2026-09-15 新增：**缝帧重影**（极短交叉溶，本仓作者 认可的解法）。
        #   裁后首帧换成「上段末帧 ⊕ 本段首帧」的混合 → 把缝处一跳**拆成两个半跳跨两格**，
        #   一闪而过 → 体感约等于一镜到底。**帧数守恒、不动音频、零采样开销。**
        #   上段末帧 = `images[pin-1]`（钉住区最后一帧 = 上段尾的复现）——节点手里就有，无需额外输入。
        # 🔴 2026-09-16 新增：后处理层补全（P3/P5/P6/P7）—— 画质域修复，不碰时间轴。
        #   执行顺序：先对齐色调（直方图/白平衡），再补高频（反卷积/段体迁移），最后锐化收口。
        post_note = ""
        if float(hist_match) > 0.0:
            out = CORE.match_hist_head_to_body(out, int(settle_sharpen_frames),
                                               float(hist_match), body_start=40)
            post_note += "\n           ✦ **直方图匹配**：段头色阶分布对齐段体（强度 %.2f）。" % float(hist_match)
        if float(wb_match) > 0.0:
            out = CORE.match_white_balance(out, int(settle_sharpen_frames),
                                           float(wb_match), body_start=40)
            post_note += "\n           ✦ **白平衡校正**：段头 R:G:B 比例对齐段体（强度 %.2f）。" % float(wb_match)
        if float(deconv_strength) > 0.0:
            out = CORE.deconv_head_zone(out, int(settle_sharpen_frames),
                                        float(deconv_strength), float(deconv_radius))
            post_note += ("\n           ✦ **反卷积去模糊**：段头 Wiener 逆滤波（强度 %.2f / 半径 %.1f）。"
                          % (float(deconv_strength), float(deconv_radius)))
        if float(detail_borrow) > 0.0:
            out = CORE.borrow_detail_from_body(out, int(settle_sharpen_frames),
                                               float(detail_borrow), int(detail_blur), body_start=40)
            post_note += ("\n           ✦ **段体高频迁移**：段头高频换用段体结构（强度 %.2f / 尺度 %d）。"
                          % (float(detail_borrow), int(detail_blur)))

        # 🔴 2026-09-16 新增：**低频残差传递**（借鉴 Director 的段间引导低频对齐）。
        #   只吸收上段末帧的低频色档/布光，**保留本段细节与姿态** ⇒ 无重影。
        #   与「全 RGB 混合」有本质区别：后者实测把作用区锐度砍掉 49%（画面花）。
        lowfreq_note = ""
        # 🔴 2026-09-17 新增（RESEARCH_seam_frontier §2 方向二）：**跨段统计匹配**。
        #   作用域 = 段头 ↔ **上段末帧**（缝的另一侧），治成片缝上的亮度阶跃。
        #   与下面 lowfreq_pull 的区别：那个只做低频**加性**（一阶矩）；本组补**二阶矩 + 逐通道色度**。
        #   ⚠ 两者**作用域重叠**（都是段头↔上段末帧）⇒ 建议二选一，同时开需自行确认不过修。
        #   ⚠ 必须放在 lowfreq_pull **之前**：先对齐分布，再修低频残差。
        if float(match_prev) > 0.0 and pin >= 1:
            # guide 取钉住区末尾最多 FIT 帧：拷贝桥下 = 上段**真实**尾部 ⇒ 可按「走向」
            # 外推一帧当目标（父段合法走进阴影不被修回旧色档）；cond 桥下是本段重画
            # ⇒ 该路线本项应保持 0（见 match_prev tooltip）。
            out = CORE.match_prev_stats(
                out, images[max(0, pin - CORE.MATCH_PREV_GUIDE_FIT):pin],
                int(match_prev_frames), float(match_prev),
                float(match_prev_gain_max), float(match_prev_offset_max))
            post_note += ("\n           ✦ **跨段统计匹配**：开头 %d 帧的色档/曝光分布对齐上段尾部走向"
                          "（强度 %.2f / 增益上限 %.2f / 偏移上限 %.3f）——只对齐统计量，不复制姿态 ⇒ 无重影。"
                          % (int(match_prev_frames), float(match_prev), float(match_prev_gain_max),
                             float(match_prev_offset_max)))

        if float(lowfreq_pull) > 0.0 and pin >= 1:
            out = CORE.lowfreq_pull(out, images[pin - 1], int(lowfreq_frames),
                                    float(lowfreq_pull), int(lowfreq_blur))
            lowfreq_note = (
                "\n           ✦ **低频残差传递**：开头 %d 帧对齐上段末帧的低频"
                "（强度 %.2f / 尺度 %d）——只动低频，不复制姿态 ⇒ 无重影。"
                % (int(lowfreq_frames), float(lowfreq_pull), int(lowfreq_blur)))

        # 🔴 2026-09-16 新增：**画质域修复**——糊区锐化（本仓作者 定方向：先试零 GPU 传统锐化）。
        #   默认 settle_frames=0（不裁沉降）后，成片段头保留几帧「重绘糊」；
        #   裁它 → 跳帧（裁 16 帧跳 0.055）；不裁 → 留糊。
        #   **第三条路 = 画质域修**：不裁、不动时间轴，只提升糊区高频（故不可能引入跳帧）。
        sharpen_note = ""
        if float(settle_sharpen) > 0.0 and int(out.shape[0]) > 0:
            out = CORE.sharpen_head_zone(out, int(settle_sharpen_frames), float(settle_sharpen))
            sharpen_note = (
                "\n           ✦ **糊区锐化**：开头 %d 帧 unsharp（强度 %.2f，缝端最强 → 尾端 0）"
                "——不裁、不动时间轴，故不引入跳帧。"
                % (int(settle_sharpen_frames), float(settle_sharpen)))
        # 🔴 2026-09-16 重做：改用 `seam_crossfade`（两侧都是**连续运动序列**）。
        #   旧实现把「上段末帧」**复制 N 次**去混合 → k>1 时内容冻结（重复帧）、
        #   k=1 时只是"一跳拆两跳"（异常帧数 1→2）——**素材用错了**，故观感更差。
        #   正解：上段**末尾 k 帧** 对 本段**开头 k 帧** 逐帧渐变（t 从 1/(k+1) 递进到 k/(k+1)）。
        #   `seam_crossfade` 自带裁切（cut = pin + settle），故它直接产出裁后序列。
        ghost_note = ""
        if int(seam_ghost) > 0 and pin >= 1:
            out = CORE.seam_crossfade(images, n, pin, int(seam_ghost))
            ghost_note = ("\n           ✦ **极短交叉溶 %d 帧**（上段末尾 %d 帧 ⊕ 本段开头 %d 帧，"
                          "权重按 1/%d 逐帧递进）——两侧都是连续运动序列，帧数守恒、不动音频。"
                          % (int(seam_ghost), int(seam_ghost), int(seam_ghost), int(seam_ghost) + 1))
        audio_out = CORE.trim_audio_head(audio, n, float(fps)) if audio is not None else None
        after = int(out.shape[0])
        line = ("[H3 Relay] 裁首 %d 帧 = 钉住 %d + 沉降 %d ｜ %s\n"
                "           画面 %d → %d 帧（%.3fs → %.3fs）"
                % (n, pin, settle, why, before, after, before / float(fps), after / float(fps))) + bj_note + lowfreq_note + post_note + sharpen_note + ghost_note
        if audio is not None:
            line += "；音频 %d → %d 采样点" % (
                int(audio["waveform"].shape[-1]), int(audio_out["waveform"].shape[-1]))
        else:
            line += "；⚠ 未接 audio，画面裁了但音频没裁 → 可能音画不同步"
        # 🔴 2026-09-21 铁律·音画同步：把「音频拼接该填多少」**直接算给用户**。
        #   以前要用户自己从「裁首 N 帧」换算 fps，于是没人填对 ⇒ 默认落在「旧缩短语义」上
        #   ⇒ 每缝后段音频提前 cross 秒。少一次心算 = 少一类错位片。
        if n > 0:
            line += ("\n           ▶ 音频拼接（AudioSeam 的 joined）：J-cut 守恒路的 "
                     "`join_align_seconds` 填 **%.4f**（= 裁首 %d 帧 ÷ %g fps）；"
                     "若只想等长拼接不做交叉 ⇒ `join_cross_ms` 填 0（默认）。"
                     % (n / float(fps), n, float(fps)))
        print(line)
        # 接缝自检：裁后起点若仍有突变，说明沉降量不够
        check = CORE.describe_head_jump(out)
        print(check)
        # 🔴 2026-09-17 新增第 4 路输出 `prev_tail`（**追加在末位**，旧工作流不受影响）：
        #   = 钉住区最后一帧 = `images[pin-1]`（节点手里本来就有，无需额外输入）。
        #   ⚠ **口径（2026-09-19 补正）**：它是否等于"上一段的末帧"取决于走哪条桥——
        #     拷贝桥下前 pin 帧是逐位拷贝的上段尾 ⇒ 就是上段末帧；
        #     复合桥的 conditioning 钉帧路线（原 Latent 桥 cond 路线）下前 pin 帧是本段重画的 ⇒ 只是近似（代理误差 0.006，
        #     比要修的缝阶跃 0.0007 还大 8 倍）。故这条路线（仅接第 4 路不接第 0 路）上别拿它当参照去对齐。
        #   用途：喂给 `H3RelayPost` 的 `guide`，让跨段统计匹配 / 低频残差传递有"缝的另一侧"可对齐。
        tail = images[pin - 1: pin] if pin >= 1 else images[:1]
        _u, _n = _pcm_sidecar(audio_out, save_pcm)
        if _n:
            print(_n.strip("\n"))
        # 🔴 交出去的那份音频统一转 **float32**（边车仍存原 dtype = fp16，省一半空间；
        #   组装层读边车时自己会转 f32）。不转的后果见 `CORE.audio_to_fp32` ——
        #   `VHS_VideoCombine` 按 f32le 送 ffmpeg 且不做 dtype 转换 ⇒ fp16 进去 = 成片静音。
        _r = (out, CORE.audio_to_fp32(audio_out), line + "\n" + check + _n, tail)
        return {"ui": {CORE.PCM_UI_KEY: [_u]}, "result": _r} if _u else _r


class H3RelayChain:
    """🔗 H3 Relay · Chain—— 负责"按按钮"与"交词"，本体不接画面线。

    它做的事：找到同一张图里的「桥 + 落盘 + 读上段 latent」，
    自动推进它们的段号并排队，让你不用每段手动改数字。

        ▶ Run      按当前段号跑一次（不满意再点一次，覆盖同段号的文件）
        ✔ Approve  段号 +1，排队跑下一段
        ⏩ 连跑     自动一段段跑，**跑到第 `segments` 段为止**（0 = 一直跑，直到点 Stop）
        ⏭ 续跑     读 `run_id` 的进度文件，从上次跑到的地方接着跑（宿主卡死/重启后用）
        ⏹ Stop     当前这段跑完后不再推进
        ↺ Reset    段号归 0，从第 1 段重来
        🧩 拼成一条 把已经跑完的几段拼成一条成片

    两个便利功能（**都默认关，不影响老工作流**）：
      · **自动换词**（`prompts`）：把每段的词用单独一行 `---` 分开粘进来。
        🔴 **0.6.15 起词分发是节点能力**：本节点按 `stage_index` 取出第 k 块，
        从 **`prompt` 输出口**交出去 —— 把它连到出词节点的 `prompt` 输入即可。
        这样**画布手点与 API 提交图 JSON 走的是同一个节点**（铁律一：UI/API 同一套实现）。
        留空 = 不换词。词块不够要跑的段数时**直接报错**，不会静默复用上一块。
      · **自动拼成片**（`auto_concat`）：连跑跑完自动把 N 段拼成一条。

    分组**不是前提**：没有分组框时，本节点在整张图里找「桥 / 落盘 / 读上段 latent」。
    只有一张图放多套链路时，才需要各画一个分组框把它们隔开（否则会互相推进段号）。
    ⇒ 新用户不用先画框；`status` 会写明当前范围是「本组」还是「全图」。

    **第 1 段什么都不用做**：「Latent Load」在 `stage_index == 0` 时会交一个「空上下文」，
    桥识别后自动直通（latent 原样过、裁剪帧数输出 0）；段号 ≥ 1 时读上一段、真续接。
    ⇒ 直接点「⏩ 连跑」就能从第 1 段一路跑下去。
    ⚠ **别旁路「Latent Load」**（会让桥的 required 输入从 prompt 里消失 ⇒ 整个 prompt 被拒）；
    **也别旁路桥**（它没有 INT 输入可透传，`trim_frames` 会变成空值）。

    按钮点了没反应时，看 `status` 格子 —— 它会说明卡在哪一步、该怎么改。
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "segments": ("INT", {
                    "default": 5, "min": 0, "max": 9999, "step": 1,
                    "tooltip": "【跑到第几段】只对连跑 / 续跑有效。\n填 3 = 跑到第 3 段就停（不是「再跑 3 段」）；填 0 = 一直跑到你点停止。\n填了 prompts 时不要超过词块数。",
                }),
            },
            "optional": {
                # 只用于**显示**：前端把状态写进这一格。
                # 必须是**最后一个** widget —— 这样旧工作流里少这一格时只走默认值，
                # 不会让它前面的取值错位（见 CHANGES 0.2.1 的槽位错位说明）。
                "status": ("STRING", {
                    "default": "",
                    "tooltip": "【不用填】显示连跑状态：跑到第几段 / 有没有在排队 / 出错原因。\n按钮点了没反应时先看这一格。",
                }),
                # —— 0.6.7：词分发 + 自动拼接。**一律追加在 status 之后**，
                #    旧工作流少这几格只走默认值，位置不错位。
                "prompts": ("STRING", {
                    "default": "", "multiline": True,
                    "tooltip": "【可选】每段各自的词。\n用单独一行 --- 把词分成几块：第 1 块给第 1 段，依次类推。留空 = 不换词。\n⚠ 词块数不够要跑的段数时不排队，并告诉你。也可以从别的节点连线进来。",
                }),
                # 🔴 2026-10-15：**折进 advanced 区**（`advanced: True`）。
                #   这一格早已失效（0.6.15 起词由节点的 `prompt` 输出口给），但**槽位不能删** ——
                #   `widgets_values` 按位存，删它会让后面所有格的取值整体前移（旧图静默错位）。
                #   ⇒ 官方机制 `advanced` 正好：**留槽位、不占主界面**（与那 54 个 advanced 项同一待遇），
                #   展开「advanced inputs」仍看得到，tooltip 说明它为什么还在。
                "prompt_target": ("STRING", {
                    "advanced": True, "default": "",
                    "tooltip": "【已废弃】旧版「前端替你写词」那条路已删除，这一格不再起作用。\n现在词从这个节点的 prompt 输出口给。\n保留它只是不让旧工作流的槽位错位。",
                }),
                # 2026-10-14：默认 False → True。连跑的意义就是"出成片"，默认关等于让每个新用户
                #   跑完还得再点一次「拼成一条」；拼接本身画面无损、只多几秒。
                "auto_concat": ("BOOLEAN", {
                    "default": True,
                    "tooltip": "【默认开】开 = 连跑跑完自动把这几段拼成一条成片（画面无损，几秒钟）。\n关 = 只得到 N 个分段 mp4，想拼时点「拼成一条」。\n⚠ 拼接要有保存视频的节点（段文件从它那儿找）。",
                }),
                "concat_name": ("STRING", {
                    "default": "",
                    "tooltip": "【可选】成片叫什么名字（不用写 .mp4）。留空 = 用保存时的 run_id。\n成片存在 ComfyUI 的 output/ 目录。",
                }),
                # —— 2026-09-22 追加（成片音轨档 / 画质档）：**继续追加在末位**，槽位安全同上。
                "audio_out": (["aac_256k", "aac_192k", "pcm_lossless"], {
                    "default": "aac_256k",
                    "tooltip": "【成片音轨】只影响拼出来的成片，不影响分段文件。\n· aac_256k（默认）= 通用档：浏览器能放、剪辑软件都认。\n· aac_192k = 一样能放，文件小约 25%。\n· pcm_lossless = 无损音轨，给剪辑/归档用。⚠ 文件大很多，且浏览器预览没声音。",
                }),
                "video_crf": ("INT", {
                    "default": 16, "min": 0, "max": 51, "step": 1,
                    "tooltip": "【成片画面质量】默认 16，平时用不到。\n成片默认直接拷贝画面（无损、不重新编码）；只有各段规格对不上必须重编码时才生效：数字越小越清晰。",
                }),
                # —— 0.6.15：连跑的**段号**搬进节点（词分发节点化的配套）。**继续追加在末位**。
                "stage_index": ("INT", {
                    "default": 0, "min": 0, "max": 9999, "step": 1,
                    "tooltip": "【现在跑第几段】段号从 0 开始（第 1 段 = 0）。\n它决定输出 prompts 里的第几块词。\n· 画布上：点运行按钮会自动和桥、保存、读上段 latent 一起改。\n· 脚本提交：循环里把这一格和桥/保存的段号改成同一个数。\n⚠ 填得比词块数大 ⇒ 执行时直接报错。",
                }),
                # —— 0.6.15：断点续跑用的 run 标识。**继续追加在末位**（槽位安全）。
                "run_id": ("STRING", {
                    "default": "",
                    "tooltip": "【可选】续跑用：和桥 / 保存上填的 run_id 一样。\n填了之后每跑一段会把进度记到 _progress.json ⇒ 中途卡死或重启后点续跑能接着跑。\n留空 = 不记进度。",
                }),
                # —— 0.6.15：拼接结果显示位。**继续追加在末位**。
                #    为什么单开一格：成片路径写在 `status` 里会被后一条状态消息冲掉，
                #    而这个路径是用户**跑完之后最想拿去用**的东西 ⇒ 给它一个**不会被覆盖**的位置。
                "concat_result": ("STRING", {
                    "default": "",
                    "tooltip": "【不用填】成片路径：拼接成功后这里显示 output/ 下的成片文件。\n拼接失败时显示失败原因（完整报告在控制台）。它不会被状态消息覆盖。",
                }),
            },
        }

    RETURN_TYPES = ("STRING", "STRING")
    RETURN_NAMES = ("prompt", "report")
    FUNCTION = "run"
    CATEGORY = CATEGORY
    DESCRIPTION = (
        "🔗 H3 Relay · Chain：帮你自动改段号、自动换词、自动拼成片。\n"
        "它控制同一分组里的「桥 / 落盘 / 读上段 latent」，另有一个 `prompt` 输出口。\n"
        "\n"
        "怎么用：\n"
        "1. 分组框**不是必需的**：没有分组时自动在整张图里找。\n"
        "   同一张图有多套链路时，才需要各自框进一个分组，避免互相推进段号。\n"
        "2. 要「每段用各自的词」：把 `prompts` 用**单独一行 `---`** 分成几块粘进来，\n"
        "   再把本节点的 **`prompt` 输出连到出词节点的 `prompt` 输入**（这一步是关键，别漏）。\n"
        "   取第几块由本节点的 `stage_index` 决定：画布上会自动跟着按钮改；\n"
        "   脚本提交 JSON 时在循环里自己改（见 docs/10 §7.4 的循环模板）。\n"
        "3. 直接点「⏩ 连跑」（或「▶ Run」）—— **第 1 段不用你管**：\n"
        "   「Latent Load」在 stage_index=0 时会自己交「空上下文」，桥自动直通；\n"
        "   从第 2 段起它读上一段、桥真续接。段号一路自动加、词自动换。\n"
        "4. 成片：连跑跑完**自动拼**（`auto_concat` 默认开）；也可以随时点「🧩 拼成一条」。\n"
        "\n"
        "按钮点了没反应？看 status 格子，它会说明原因。"
    )

    @_node_errors("segments", "stage_index")
    def run(self, segments=5, prompts="", stage_index=0, run_id="", **kwargs):
        """Chain 节点的**执行**部分：只做一件事 —— 交出现在这一段该用的词。

        本体的活（推进段号、排队下一段、拼接）都在画布按钮 / 前端那条路上；
        但**「第 k 段喂第 k 块词」必须是节点能力**，否则 API 提交图 JSON 的用户拿不到
        （铁律一：UI 与 API 必须同一套实现，且必须基于节点）。
        所以这里按 `stage_index` 从 `prompts` 取出第 k 块，从 `prompt` 输出口交出去 ——
        把它连到出词节点的 `prompt` 输入即可，UI 与脚本**走的是同一个节点**。

        `run_id` 非空时顺带把「跑到第几段」落盘（断点续跑）：
        **写的是「第 k 段已开始」**，所以宿主卡死重启后从这一段重跑，**不会跳段**。

        **kwargs 吞掉 status / prompt_target / auto_concat / concat_name / audio_out /
        video_crf 这类由前端读走或只给显示的输入（它们不参与本节点的执行）。
        """
        if str(run_id).strip():
            try:
                _write_progress(run_id, int(stage_index), int(segments))
            except Exception as err:
                # 记进度失败**不能**拖垮这一段的执行（磁盘满/权限问题都不该让片子跑不出来）。
                print("[H3 Relay] ⚠ 写连跑进度失败（不影响本段执行）：%s" % err)
        block, total = CORE.pick_prompt_block(prompts, stage_index)
        if total == 0:
            return "", "未填 prompts ⇒ 不换词（沿用画布上原来那份）。"
        return block, "第 %d/%d 块词（stage_index=%d）" % (int(stage_index) + 1, total, int(stage_index))


class H3RelayCopyBridge:
    """0.4.0 拷贝桥：上一段尾部 AV latent **逐位拷贝**进本段初始 latent + 噪声掩码。

    0.6.0 起本节点是**唯一桥**：conditioning 钉帧路径已折叠进第 4 路（复合桥，见下），不再另设节点。
      · Latent 桥（钉帧）：模型重绘上一段尾段 → 有复现漂移/发糊风险（0.3.x 实测），
        观测端沉降检测兜底；
      · 拷贝桥（本节点）：``mask_mode="hard"`` 时钉住区不重绘（掩码 0 区每步被钉回
        拷贝 latent），复现伪影这一类从机制上消失；掩码消费走 ComfyUI 原生 H3 契约与
        SelfLift 的 noise_mask 支持——**不绑定任何特定采样器**。
        ⚠️ ``mask_mode="taper"`` **不钉住**（每帧留 seam_min~100% 重绘自由度），只作对照实验档。
        ``mask_mode="ramp"``（0.4.3）是"软证据"档：连续掩码按原生 H3 契约就是逐 token 的
        sigma 标签（sigma_row = m·sigma_video），远端硬钉、缝端以 ramp_top 强度 harmonize，
        每一步都被 (1−m) 锚回拷贝尾——治硬接缝的色档/曝光台阶，全程有锚（与 taper 相反）。
    输出 INT = 应裁帧数（=拷贝跨度），接 H3RelayTrimAV 的 trim_frames；
    TrimAV 的 settle_frames 保持 0（0.5.0 起默认不裁沉降），观测端继续守接管帧。
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "latent": ("LATENT", {
                    "tooltip": "【接法】本段的初始 AV latent（潜空间构造节点的输出）。\n它把上一段的尾部写进开头，并附上噪声掩码。",
                }),
                "context_frames": ("INT", {
                    "default": 22, "min": 5, "max": 124, "step": 17,
                    "tooltip": "【拷贝窗帧数】写进本段开头的帧数。\n只认 5+17k（5/22/39/56/73/90/107/124），必须小于本段帧数。\n窗越长接缝越稳，留给新内容的帧越少。"
                }),
            },
            "optional": {
                "mask_mode": (["hard", "taper", "ramp", "blend", "window"], {
                    "default": "hard",
                    "tooltip": "【掩码模式】每个像素用多少模型生成、多少用上一段。\n· hard（推荐，默认）= 整窗都用上一段、不重绘 —— 真续接用它。\n· ramp / window = 接缝处松一点，取景可能被改写。\n· taper / blend = 仅对照。\n· m = 0 才是全用上一段；四个非 hard 档都会跳。"
                }),
                "taper_tokens": ("INT", {"advanced": True,
                    "default": 4, "min": 1, "max": 12, "step": 1,
                    "tooltip": "【渐缩档】只在 taper 档生效：接缝前多少个 token 参与线性过渡。"
                }),
                "seam_min": ("FLOAT", {"advanced": True,
                    "default": 0.10, "min": 0.0, "max": 1.0, "step": 0.05,
                    "tooltip": "【渐缩档】只在 taper 档生效：接缝端的掩码下限。\n0 = 完全用上一段；0.3 = 这里留 30% 给模型重绘。\n本段开头始终全重绘，这一档管不到它。"
                }),
                "pin_audio": ("BOOLEAN", {
                    "default": True,
                    "tooltip": "【复制音频】开（默认）= 上一段的声音也复制过来。\n只想复制画面时关掉；能听的拼接仍由 Trim AV 与拼接步骤负责。"
                }),
                "ramp_top": ("FLOAT", {"advanced": True,
                    "default": 0.25, "min": 0.0, "max": 1.0, "step": 0.05,
                    "tooltip": "【斜坡档】只在 ramp 档生效：接缝处最多让模型改多少。\n0 = 变成 hard；越大接缝越协调，但噪声落差越陡。\n想调大就同时加长拷贝窗。"
                }),
                "ramp_tokens": ("INT", {"advanced": True,
                    "default": 0, "min": 0, "max": 12, "step": 1,
                    "tooltip": "【斜坡档】只在 ramp 档生效：接缝处参与斜坡的 token 数。\n0 = 铺满整个拷贝窗；2~3 = 只松接缝、锁住运动。"
                }),
                "anchor_latent": ("LATENT", {
                    "tooltip": "【统计纠偏，可选】全局外观参考段（通常第 1 段）的 AV latent。\n接上后：被裁掉的前缀会被拉向参考段的色档与方差 ⇒ 本段新内容回到全局色档。治「越跑越偏色」。",
                }),
                "anchor_blend": ("FLOAT", {
                    "default": 1.0, "min": 0.0, "max": 1.0, "step": 0.05,
                    "tooltip": "【纠偏强度】0~1：把开头拉向参考段的比例。\n1 = 全量（默认），0.5 = 一半；参考段与本段允许有差异时调低。\n不接参考 latent 时无效。"
                }),
                # 🔴 2026-09-17 新增（RESEARCH_seam_frontier §4 方向四）：**重叠区双向融合**。
                #   与 ramp 同语义、不同曲线：ramp 线性升，blend 用**窗形**升（两端导数 0）。
                #   依据 FlowLong / Unified Long Video Inpainting 的滑窗 Hamming 混合
                #   （arXiv:2511.03272）：重叠区由两个独立估计加权平均，权重取窗函数。
                #   ⚠ 本节点**无存量 UI 工作流**引用（已核），故新 widget 可紧邻同族项放。
                "blend_top": ("FLOAT", {"advanced": True,
                    "default": 0.50, "min": 0.0, "max": 1.0, "step": 0.05,
                    "tooltip": "【混合档】只在 blend 档生效：接缝处模型占比的上限。\n0 = 变成 hard；越大越信任本段自己的生成。\n建议从 0.5 起。"
                }),
                "blend_tokens": ("INT", {"advanced": True,
                    "default": 0, "min": 0, "max": 12, "step": 1,
                    "tooltip": "【混合档】只在 blend 档生效：接缝处参与混合的 token 数。\n0 = 整个拷贝窗；2~3 = 只混接缝。"
                }),
                "blend_shape": (list(CORE.BLEND_SHAPES), {"advanced": True,
                    "default": CORE.BLEND_SHAPE_DEFAULT,
                    "tooltip": "【混合档】只在 blend 档生效：混合窗形。\nsmoothstep = 多项式（默认）；hann = 余弦。\n两者两端变化率都为 0 ⇒ 过渡更柔和。"
                }),
                # ⚠ 追加在**最后**（保护既有 widgets_values 的按位对槽）
                "window_top": ("FLOAT", {"advanced": True,
                    "default": CORE.SEAM_WINDOW_TOP, "min": 0.0, "max": 1.0, "step": 0.05,
                    "tooltip": "【窗口档】只在 window 档生效：窗的峰值（中间最松）。0 = 变成 hard。\n与斜坡相反：接缝两端回落到低位 ⇒ 接缝保持复制。\n太大会把取景交还给模型。"
                }),
                "window_shape": (list(CORE.WINDOW_SHAPES), {"advanced": True,
                    "default": CORE.WINDOW_SHAPE_DEFAULT,
                    "tooltip": "【窗口档】只在 window 档生效：窗形。\nsine = 两端低但不是 0（默认）；hann = 两端严格为 0（两端都复制）。"
                }),
                # ⚠ 以下为 0.6.0「复合桥」折叠槽位——**一律追加在末尾**（守 widgets_values 按位对槽）
                "conditioning": ("CONDITIONING", {
                    "tooltip": "【可选】接上后，采样条件里也追加这些复制帧。\n它管取景和构图，与 latent 复制窗同时生效；不接 = 只做拷贝桥。\n⚠ 第 4 路输出要接到采样器的 positive。"
                }),
                "run_id": ("STRING", {
                    "default": "relay",
                    "tooltip": "【填什么】片名，与 Latent Save 一致。\n填了才能用下面两个「只填段号」的参考（它们靠它找段文件）。",
                }),
                "stage_index": ("INT", {
                    "advanced": True, "default": 0, "min": 0, "max": 9999, "step": 1,
                    "tooltip": "【填什么】本段段号（第 1 段 = 0）。\n与 run_id 一起用于自动读参考，也要与保存一致。",
                }),
                "ref_anchor_latent": ("LATENT", {
                    "tooltip": "【外观参考·接线】全局外观参考（通常第 1 段）的 AV latent，直接接线喂进来。\n每一步都拿它当几乎无噪的基准来对齐，压住长程漂移（越跑越偏色、偏构图）。",
                }),
                "ref_anchor_stage": ("INT", {
                    "advanced": True, "default": -1, "min": -1, "max": 9999, "step": 1,
                    "tooltip": "【外观参考·免接线】只填段号，不用接线。\n≥ 0 = 没接 ref_anchor_latent 时自动读 stage_<该值>；-1（默认）= 关。\n⚠ 必须小于本段段号（参考得是跑完的段）。",
                }),
                "ref_anchor_frames": ("INT", {
                    "advanced": True, "default": 5, "min": 1, "max": 64, "step": 1,
                    "tooltip": "【外观参考·取多少】从参考段开头取几帧。\n默认 5；取多了会把参考段的动作也拖进来。",
                }),
                # 🆕 0.6.25 音频参考窗（秒）—— 必须排在 `voice_anchor` **之前**
                #（voice_anchor 恒为最后一个槽：widgets_values 按位对槽，见 0.6.1 的约定）
                "audio_ref_seconds": ("FLOAT", {
                    "advanced": True, "default": 0.0, "min": 0.0, "max": 30.0, "step": 0.5,
                    "tooltip": "【音频参考窗】0（默认）= 自动。\n从上一段尾部往前累计够 2 秒有声就停（上限 6 秒）。\n窗里可含多个说话人的音色；只占官方 3 个音频槽里的 1 个。\n标题的 `refs n/3` = 额度：`⚠` 越界、`?` 读不到。\n⚠ 参考里就是上一段的台词，可能被复述；接了声音参考时本项不生效。"
                }),
                # ⚠ 0.6.1 声锚 —— 追加在**最后**（守 widgets_values 按位对槽）
                "voice_anchor": ("LATENT", {
                    "tooltip": "【声音参考·接线】接说话人的音频（LoadAudio → 音频编码）。\n接上后：复制过来的音频开头和音频参考都改用它的尾部，不再取上一段。\n· 谁接：接缝后第一个开口的人；同一个人不必接，换人才接。\n⚠ 复制音频关闭时无效；全零 / 常量 / 静音的参考会当场报错。"
                }),
                # 🆕 0.6.34 声锚「免接线」档 —— **追加在最末**（继续守「`voice_anchor` 是最后一个槽」
                #    这条老约定：`widgets_values` 按位存，插在中间会让存量图的槽位整体错位）。
                #    与外观锚的 `ref_anchor_stage` 对称：填个段号就自动读盘，
                #    省掉 LoadAudio + VAEEncodeAudio 两个节点加一根线。
                "voice_anchor_stage": ("INT", {
                    "default": -1, "min": -1, "max": 9999, "step": 1,
                    "tooltip": "【声音参考·免接线】只填段号，不用接线。\n≥ 0 = 没接 voice_anchor 时自动读 stage_<该值> 的音频；-1（默认）= 关。\n⚠ 必须小于本段段号；接了 voice_anchor 时本档不生效。",
                }),
                # 🆕 0.6.34 实验层总开关（**追加在最末**，继续守 widgets_values 按位对槽）。
                #    原先两个实验（VA / 历史锚）只能靠 run 目录的配置文件开关 ⇒ 画布上看不见；
                #    现在做成**一个看得见的选择器**。`auto`（默认）= 只看文件 ⇒ 存量用户零行为变化。
                "exp_anchor": (list(EXP_ANCHOR_MODES), {
                    "default": "auto",
                    "tooltip": "【实验·默认 auto】两个实验的总开关：按人累积音色 / 时不变历史参考。\n"
                               "· auto（默认）= 与旧版一致：看 run 目录的配置文件，有就开。\n"
                               "· off = 显式关（文件在也不开，报告会点名）。\n"
                               "· 其余 = 显式开；缺配置文件直接报错。\n"
                               "⚠ 两者都是未实测的实验件。",
                }),
                # 🆕 0.6.37：**不接也行** —— 留空时桥按 run_id + 段号−1 自己从磁盘取
                #    （与 `voice_anchor_stage` / `ref_anchor_stage` 那两处「免接线」对称）。
                #    接了则以接线为准 ⇒ 存量工作流与全部离线断言逐位不变。
                #    ⚠ 追加在 optional **末位**（守「新参数只追加」；它是链接型，不占 widgets 槽）。
                "context_latent": ("LATENT", {
                    "tooltip": "【接法】上一段的完整 AV latent。\n不接也行：留空 = 桥按片名与段号−1 自己从磁盘取（少接一根线）。\n接了 = 以接线为准；第 1 段空包照旧直通。",
                }),
            },
        }

    RETURN_TYPES = ("LATENT", "STRING", "INT", "CONDITIONING")
    RETURN_NAMES = ("latent", "report", "trim_frames", "conditioning")
    FUNCTION = "bridge"
    CATEGORY = CATEGORY
    DESCRIPTION = (
        "把上一段尾部 AV latent 逐位拷进本段开头并附噪声掩码（钉住区不重绘），\n"
        "消除「复现发糊/漂移」这一类接缝伪影。输出 trim_frames 接「Trim AV」。\n"
        "⚠️ 与「Latent 桥」（conditioning 钉帧经第 4 路折叠进复合桥，不另设节点）。"
    )

    @_node_errors("context_frames", "mask_mode", "run_id", "voice_anchor_stage", "exp_anchor")
    def bridge(self, latent, context_latent=None, context_frames=22,
               mask_mode="hard", taper_tokens=4, seam_min=0.10, pin_audio=True,
               ramp_top=0.25, ramp_tokens=0,
               blend_top=CORE.SEAM_BLEND_TOP, blend_tokens=0,
               blend_shape=CORE.BLEND_SHAPE_DEFAULT,
               window_top=CORE.SEAM_WINDOW_TOP,
               window_shape=CORE.WINDOW_SHAPE_DEFAULT,
               anchor_latent=None, anchor_blend=1.0,
               conditioning=None, run_id="relay", stage_index=0,
               ref_anchor_latent=None, ref_anchor_stage=-1, ref_anchor_frames=5,
               voice_anchor=None, voice_anchor_stage=-1, audio_ref_seconds=0.0,
               exp_anchor="auto"):
        CONTRACT.enforce()
        # 存档槽位错位的兜底（见 `_stored_int`）：本版给本节点新增了 `voice_anchor_stage`，
        # 老图按位置回填时末尾可能落到一个非整数值上 ⇒ 退回默认（−1 = 关）并出声，
        # 而不是当场 `int("")` 炸在一条与"参数"毫无关系的报错上。
        voice_anchor_stage = _stored_int(voice_anchor_stage, -1, "voice_anchor_stage")
        # 🧪 实验开关（0.6.34）：把「配置文件在不在」与「用户显式要哪个实验」合成两个布尔。
        #    `auto`（默认）= 只看文件 ⇒ 存量 `_va.json` / `_tiha.json` 用户**零行为变化**。
        _want_va, _want_ti, _exp_notes = _exp_gate(
            exp_anchor, run_id, _stage_path)
        # 0.6.0：Latent 桥（H3RelayMotionContext）已删除，本节点成为**唯一桥**。
        # 原 Latent 桥「段号>=1 却无来源 -> 必须 raise（不得静默直通）」是反坏片关键守卫，
        # 路由收敛后必须保留在此：
        _idx = int(stage_index)
        # 「空上下文」的两种形态：
        #   · `None` —— context_latent 没接线；
        #   · `{"samples": None}` —— 「Latent Load」在第 1 段交回来的**占位空包**
        #     （它必须留在链上，否则宿主提交校验会报 required 缺失；见 `H3RelayLatentLoad.load`）。
        _ctx_empty = context_latent is None or not (
            isinstance(context_latent, dict) and context_latent.get("samples") is not None
        )
        # 🆕 0.6.37 免接线自取：**真的没接线**（None）且段号 ≥1 时，桥自己按 run_id + 段号−1 取盘。
        #    ⚠ 只在 `None` 时自取：`{"samples": None}`（Latent Load 第 1 段交的空包）**不走这里**，
        #      它保持原本的直通/守卫语义 ⇒ 存量图与全部离线断言逐位不变。
        if context_latent is None and _idx >= 1 and (run_id or "").strip():
            _auto_path = _stage_path(run_id, _idx - 1)
            if os.path.isfile(_auto_path):
                context_latent = CORE.load_av_latent(_auto_path)
                _ctx_empty = False
                print("[H3 Relay] 复合桥：context_latent 未接线 ⇒ 自取上一段 ← %s\n"
                      "            %s" % (_auto_path, CORE.describe_latent(context_latent)),
                      flush=True)
        if _ctx_empty and _idx >= 1:
            raise RuntimeError(
                "stage_index=%d 表示本段是第 %d 段，但没有可续接的上一段 latent：\n"
                "    · context_latent 没接线，且按 run_id 自取也失败；或\n"
                "    · 「Latent Load」接了但读不到文件；或\n"
                "    · run_id 是空的（或只有空白）⇒ 自取无从推导路径\n"
                "再跑下去会「静默直通」—— 产出的是独立段而不是续接段，\n"
                "但界面与日志都显示成功。\n"
                "    第 2 段起请把 run_id 填成与「Latent Save」完全一致的名字；\n"
                "    若这确实是独立段，把 stage_index 改回 0。"
                % (_idx, _idx + 1))
        if _ctx_empty:
            # 直通（独立段，不续接）：latent 原样返回，conditioning 不钉帧原样返回。
            _msg = "[H3 Relay] 复合桥：无 context_latent -> 直通（独立段，不续接）。"
            if int(voice_anchor_stage) >= 0:
                # 直通档不跑 build_continue_latent ⇒ 声锚没有可用之处。
                # 用户显式设了档位就**必须出声**（否则"设了没反应"= 铁律 15 的静默分支）。
                _msg += ("｜ ⚠ voice_anchor_stage=%d 本段不生效（直通档不钉音频）；"
                         "第 1 段本就没有上一段，从第 2 段起才需要声锚。" % int(voice_anchor_stage))
            print(_msg, flush=True)
            return (latent, _msg, 0, conditioning)
        # 🆕 0.6.34 声锚「免接线」：只填段号，锚自动从磁盘读（与外观锚的 `ref_anchor_stage` 对称）。
        #    形态直接复用 `_voice_anchor_audio` 认的那三种 —— stage 文件是 AV 联合 latent
        #    ⇒ 它取第 2 条流（音频），正是声锚要的东西，不用另做适配。
        _va_auto_note = None
        if voice_anchor is None and int(voice_anchor_stage) >= 0:
            _va_idx = int(voice_anchor_stage)
            if not (run_id or "").strip():
                raise RuntimeError(
                    "voice_anchor_stage=%d 需要 run_id（靠它推导段文件路径）：把 run_id 填成"
                    "与「Latent Save」一致的名字，或把 voice_anchor_stage 改回 -1。" % _va_idx)
            if _va_idx >= _idx:
                raise RuntimeError(
                    "voice_anchor_stage=%d 必须小于本段段号 %d —— 声锚要取**更早**已跑完的段"
                    "（通常是「缝上第一个开口说话的人」所在的那段）。" % (_va_idx, _idx))
            if not pin_audio:
                # 声锚只在「钉音频」时才有落点（`pin_audio=False` 时下游整个忽略它）
                # ⇒ **不读盘**（省一次整文件 IO），并说明为什么没生效。
                _va_auto_note = ("[H3 Relay] 复合桥：⚠ pin_audio=False ⇒ voice_anchor_stage=%d "
                                 "不生效（本段不钉音频，声锚无处可用），未读盘。" % _va_idx)
                print(_va_auto_note, flush=True)
            else:
                try:
                    voice_anchor = CORE.load_av_latent(_stage_path(run_id, _va_idx))
                    _va_auto_note = ("[H3 Relay] 复合桥：声锚由 voice_anchor_stage=%d 自动读盘"
                                     "（第 %d 段的音频），未使用外部接线。" % (_va_idx, _va_idx + 1))
                    print(_va_auto_note, flush=True)
                except FileNotFoundError as e:
                    raise RuntimeError(
                        "voice_anchor_stage=%d 的段文件不存在：%s\n"
                        "先把该段跑完，或把 voice_anchor_stage 改回 -1。"
                        % (_va_idx, _stage_path(run_id, _va_idx))) from e
        elif voice_anchor is not None and int(voice_anchor_stage) >= 0:
            _va_auto_note = ("[H3 Relay] 复合桥：⚠ voice_anchor 已接线 ⇒ voice_anchor_stage=%d "
                             "不生效（接线优先）。" % int(voice_anchor_stage))
            print(_va_auto_note, flush=True)

        out, covered, report = CORE.build_continue_latent(
            latent, context_latent, int(context_frames),
            mask_mode=mask_mode, taper=int(taper_tokens),
            seam_min=float(seam_min), pin_audio=bool(pin_audio),
            ramp_top=float(ramp_top), ramp_tokens=int(ramp_tokens),
            window_top=float(window_top), window_shape=window_shape,
            blend_top=float(blend_top), blend_tokens=int(blend_tokens),
            blend_shape=str(blend_shape),
            anchor_latent=anchor_latent, anchor_blend=float(anchor_blend),
            voice_anchor=voice_anchor,
        )

        # —— 0.6.0 复合桥：接上 conditioning 时，**同时**在 conditioning 上追加钉帧 ——
        # 两条路径改的是不同对象（latent vs conditioning）⇒ 并联不冲突。
        # 不接 conditioning = 行为与旧版完全一致（第 4 路返回 None）。
        cond_out = None
        if conditioning is not None:
            a_idx = int(ref_anchor_stage)
            if ref_anchor_latent is None and a_idx >= 0 and (run_id or "").strip():
                if a_idx == int(stage_index):
                    raise RuntimeError(
                        "ref_anchor_stage=%d 与本段 stage_index 相同——外观锚必须是**更早**的"
                        "已落盘段（通常是 0，即第 1 段）。" % a_idx)
                try:
                    ref_anchor_latent = CORE.load_av_latent(_stage_path(run_id, a_idx))
                    print("[H3 Relay] 复合桥：自动读外观锚（第 %d 段）" % (a_idx + 1),
                          flush=True)
                except FileNotFoundError as e:
                    raise RuntimeError(
                        "ref_anchor_stage=%d 的落盘文件不存在：%s\n"
                        "先把锚段跑完，或把 ref_anchor_stage 改回 -1。"
                        % (a_idx, _stage_path(run_id, a_idx))) from e
            plan = CORE.plan_relay(
                latent, context_latent,
                trim_frames=int(context_frames),
                audio_frames=None,
                # 🆕 0.6.25：0 = 自动（`audio_ref_seconds` 传 0 时 plan 内走素材自决）
                audio_ref_seconds=float(audio_ref_seconds or 0.0),
                anchor_latent=ref_anchor_latent,
                anchor_frames=int(ref_anchor_frames),
                voice_anchor=voice_anchor,
            )
            # 🧪 E1'（TIHA）时不变历史锚 —— **默认关**。
            #
            # 【域归属】本机制改的是 **conditioning 的 `minimax_refs`**，
            #   **不属于**「三代域纪律」里的时间轴 `TrimAV` / 画质域 `Post` / 音频域 `AudioSeam`
            #   任何一域 —— 它与三域**正交**：不动时间轴、不动像素、不动音频，
            #   只往 conditioning 追加参考块。以后新增 conditioning 层机制都归这一类。
            #
            # 开关由 **run 目录下的 `_tiha.json`** 决定（不存在 / depth<=0 ⇒ 返回 []，
            # 主干逐位不变 —— 唯一的额外动作是 1 次 `os.path.isfile`）。
            # 为什么用配置文件而不是节点入参：
            #   ① 不动 INPUT_TYPES ⇒ **零 schema 变更**（加 widget 会牵动示例图 widgets_values、
            #      机检 input 计数、多处文档数字 —— 见 LOCAL-维护规范 §二·D「加 widget 牵动一串」）；
            #   ② 天然按 run 隔离，A/B 两臂只需各写一次文件。
            # 代价：失去 /object_info 校验 ⇒ 用「提交前预检 precheck_tiha.py」+ 服务端探针日志补上。
            if not _want_ti:
                # 开关关着（`off`，或 `auto` 且无 `_tiha.json`）⇒ 不读盘、不追加历史锚块。
                _tiha_refs = []
            else:
                _tiha_refs = TIHA_ADAPTER.build_refs(
                    run_id, int(stage_index), int(ref_anchor_stage),
                    stage_path_fn=_stage_path,        # 路径推导只此一份，适配层不重复实现
                    load_fn=CORE.load_av_latent,
                    video_fn=CORE.video_from_latent,
                    anchor_latent=ref_anchor_latent,  # 复用已加载的锚，避免二次读盘
                    log=lambda m: print("[H3 Relay] " + m, flush=True),
                )
            # 🧪 VA（按人累积音色）—— **默认关**，开关 = run 目录下的 `_va.json`。
            #
            # 【域归属】与 TIHA 同类：改的是 **conditioning 的 `minimax_refs`**（经 `plan.audio_ref`），
            #   **不属于**三代域（时间轴 `TrimAV` / 画质域 `Post` / 音频域 `AudioSeam`），与之**正交**。
            #
            # 【它做什么】跨段累积**同一个说话人**在各历史段里的语音片段，拼成**一段**，
            #   **替换**桥自己那个音频参考块的内容 ⇒ 回答「段 3 说话的人，段 1 段 2 都说过，
            #   能不能综合参考」。⚠️ **不追加新块**（2026-10-04 三臂实测：追加 N 块 ⇒ 无 `<Audio j>`
            #   标签 ⇒ 模型无法对应谁是谁 ⇒ 开/关两臂音频余弦只有 0.0497 = 打乱生成）。
            # 🔴【2026-10-05 三处改造（前沿理论赋能）】每条的理由写在它落地的那一行附近：
            #   ① **同源不变式**：新参考 = `[历史素材…][上一段音频尾]` —— **必须以 pin 的来源结尾**。
            #      旧口径只改 `audio_ref` 而不动 pin ⇒ 与 `relay_core/bridge.py::build_continue_latent` 自己写明的
            #      「两处读的都是"上段尾"、**只改一处会打架**」冲突 ⇒ 软条件进不去、只剩扰动。
            #   ② **默认只给缝上那个人一块**（`config.max_speakers`，默认 1）：本包的块没有
            #      `<Audio j>` 标签 ⇒ 一槽多身份 = 条件歧义（两轮实测都更糟）。其余人走官方 3 槽。
            #   ③ **预算先行、整片丢弃**：拆掉旧口径的"先拼满再截断"（那会砍掉尾部一片、
            #      切点落在音节中间）。⇒ 下游的截断闸变成**防御性**的，正常不再触发。
            # ⚠️ 接了「声锚」时**跳过** VA：两者解同一个问题，且声锚的同源不变式是完好的。
            # 【原料】`stage_%05d.safetensors` 里的**音频 latent**（40 Hz）——
            #   ⚠ 不是 `audio_%05d.safetensors`（那是 32 kHz 波形，用它就得过音频 VAE ⇒
            #   要加 `audio_vae` 入参 = schema 变更）。⇒ 本机制**零 schema 变更、零新依赖**。
            # 【零依赖纪律】**绝不 import ASR/funasr**（944 MB，用户不该装）。
            #   台账 `_va.json` 是纯数据，可由提示词正则抽（ASR 只是我们本地的精度增强）。
            # 🔴 必须在 `apply_relay` **之前**替换 `plan.audio_ref` —— 否则块已经注入完了。
            # 本段音频栅格步数（硬上限）—— **先算**：合成参考块要按它分配预算。
            # （旧写法是等 VA 给完再截断 ⇒ 必然砍掉尾部那一片、切点落在音节中）
            try:
                _dst = int(CORE.audio_from_latent(latent).shape[-1])
            except Exception:
                _dst = None
            # 钉住前缀的音频步数（= pin 的来源长度）。参考块**必须保住**它的下限 ——
            # 否则硬约束（pin，钉进输出流头部）与软条件（ref）打架：
            # `relay_core/bridge.py::build_continue_latent` 自己写着「两处读的都是"上段尾"，**只改一处会打架**」。
            _pin_steps = int(math.ceil(int(context_frames) / float(CORE.FPS) * CORE.AUDIO_HZ - 1e-9))
            _va_audio = None
            _va_notes = []
            if not plan.applied:
                # 没有前序可接（首段 / 未接 `context_latent`）⇒ 参考块**根本不会被注入**
                # （`apply_relay` 对未应用的 plan 原样返回）⇒ **不必读盘**（省掉整轮 stage 文件 IO）。
                print("[H3 Relay] VA（按人累积音色）：本段没有前序可接（plan 未应用）⇒ 跳过",
                      flush=True)
            else:
                if not _want_va:
                    # 开关关着（`off`，或 `auto` 且无配置文件）⇒ **不读盘**，
                    # 并把「为什么没生效」写进 notes（否则"设了没反应"）。
                    _va_audio, _va_notes = None, list(_exp_notes)
                else:
                    _va_audio, _va_notes = VA_ADAPTER.build_audio(
                        run_id, int(stage_index),
                        stage_path_fn=_stage_path,    # 路径推导只此一份，适配层不重复实现
                        load_fn=CORE.load_av_latent,
                        audio_fn=CORE.audio_from_latent,
                        # 🔴 桥自己那一段：**新参考必须以它结尾** ⇒ 与钉住前缀同源（= R2 的正解）。
                        #   声锚路径下它**已经是声锚尾窗** ⇒ 叠加时同源自动保持。
                        tail_latent=(plan.audio_ref or {}).get("audio_latent"),
                        pin_steps=_pin_steps,
                        max_steps=_dst,
                        has_anchor=voice_anchor is not None,
                        log=lambda m: print("[H3 Relay] " + m, flush=True),
                    )
            if _va_audio is not None:
                # 防御闸：合成时已按 `max_steps` 分配预算 ⇒ 正常**不该**再触发。
                # 真触发了说明预算口径漂了 ⇒ 出声（旧口径在这里静默砍掉一整片）。
                if _dst is not None and int(_va_audio.shape[-1]) > _dst:
                    _va_notes.append("⚠ VA 参考 %d 步 > 本段音频栅格 %d 步 ⇒ 已截断"
                                     "（**正常情况下不该发生** —— 请报告：预算口径漂了）"
                                     % (int(_va_audio.shape[-1]), _dst))
                    _va_audio = _va_audio[..., :_dst].clone()
                plan.audio_ref = {"kind": "audio",
                                  "ref_audio_t": int(_va_audio.shape[-1]),
                                  "audio_latent": _va_audio}
                print("[H3 Relay] VA（按人累积音色）：已**替换**音频参考块内容（%d 步 ≈ %.2f s）"
                      % (int(_va_audio.shape[-1]), int(_va_audio.shape[-1]) / 40.0),
                      flush=True)
            cond_out = CORE.apply_relay(conditioning, plan)
            if _tiha_refs:
                import node_helpers
                cond_out = node_helpers.conditioning_set_values(
                    cond_out, {"minimax_refs": _tiha_refs}, append=True)
                print("[H3 Relay] E1'（TIHA）：已追加 %d 个时不变历史锚块" % len(_tiha_refs),
                      flush=True)
            extra = ["[H3 Relay] 复合桥·钉帧路径：" + plan.summary()]
            for n in plan.notes:
                extra.append("    注记：" + n)
            for n in _va_notes:
                extra.append("    注记·VA：" + n)
            extra.append("    ⚠ 第 4 路 conditioning 必须接到采样器的 positive；"
                         "不接 = 只做拷贝桥。")
            report = report + "\n" + "\n".join(extra)

        for _n in _exp_notes:
            report = report + "\n" + "[H3 Relay] " + _n
        if _va_auto_note:
            report = report + "\n" + _va_auto_note
        print(report, flush=True)
        return (out, report, covered, cond_out)


class H3RelayPost:
    """🔗 H3 Relay · Post—— 画质域修复，**不碰时间轴**。

    为什么单独一个节点（2026-09-17 拆出）：
      原来这 15 个旋钮全塞在 `H3RelayTrimAV` 里，而 ComfyUI 的 UI 工作流把
      ``widgets_values`` **按位置**存 ⇒ 「新 widget 只追加末位」成了硬约束 ⇒
      TrimAV 被顶到 22 个 widget、UI 不可读、且**每加一个后处理件都要往末尾塞**。
      拆出来之后：**TrimAV 冻结不再长**，本节点从零开始、widget 可逻辑分组，
      **以后的画质域功能一律加在这里**。

    分工：
      · ``H3RelayTrimAV`` = 时间轴（裁重叠 / 沉降 / 重影）+ 交接 ``prev_tail``
      · ``H3RelayPost``  = 画质域（色档对齐 / 高频补 / 锐化）——**不动帧数、不动音频**

    ``guide`` 接 ``H3RelayTrimAV`` 的第 4 路输出 ``prev_tail``（**是上段末帧还是它的近似，
    取决于走哪条桥**，见下面 ``guide`` 槽位的说明）：
    只有 ``match_prev`` 与 ``lowfreq_pull`` 需要它；不接时这两项自动失效（其余照常）。

    🔴 2026-09-19 口径补正：``prev_tail = images[pin-1]`` 是「本段自己解码序列的钉住区末帧」。
      · **拷贝桥**：前 pin 帧是**逐位拷贝**的上段尾 ⇒ 它**就是**上段末帧；
      · **Latent 桥（cond）**：前 pin 帧是**本段模型重画**的 ⇒ 只是**近似**（实测代理误差
        0.006，比要修的缝阶跃 0.0007 还大 8 倍）⇒ **对错参照，越对齐越糟**。
      故 cond 路线上 ``match_prev`` 保持 0；本节点的用武之地是拷贝桥。
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "images": ("IMAGE", {
                    "tooltip": "【接法】接 Trim AV 的 images 输出（裁后的画面）。\n它只改画质，不改帧数、不动音频。",
                }),
            },
            "optional": {
                "guide": ("IMAGE", {
                    "tooltip": "【接法】接 Trim AV 第 4 路输出 prev_tail。\n= 复制区最后一帧，也就是缝的另一侧；只有跨段统计匹配与低频残差需要它。\n⚠ 它是不是「上一段末帧」取决于走哪条桥：拷贝桥下是，Latent 桥下只是近似。",
                }),
                # —— 组 1：跨段色档/曝光对齐（缝的另一侧 = guide）——
                "match_prev": ("FLOAT", {
                    "default": 0.0, "min": 0.0, "max": 1.0, "step": 0.05,
                    "tooltip": "【按需开，默认 0】跨段统计匹配：把本段开头亮度/色档对齐 guide。\n只对齐统计量、不复制姿态 ⇒ 无重影；带噪声闸，阶跃小就不动手。\n⚠ 走 Latent 桥请保持 0：guide 只是近似，对齐它反而更差（0.0007→0.0039）。\n⚠ 拷贝桥下尚无正向实测 ⇒ 别当万能药。",
                }),
                "match_prev_frames": ("INT", {"advanced": True,
                    "default": 12, "min": 1, "max": 96, "step": 1,
                    "tooltip": "【配合跨段统计匹配】作用帧数：从首帧起，权重衰减到 0。",
                }),
                "match_prev_gain_max": ("FLOAT", {"advanced": True,
                    "default": 1.15, "min": 1.0, "max": 2.0, "step": 0.05,
                    "tooltip": "【护栏】逐通道对比度增益上限，防止把已对好的内容改坏。",
                }),
                "match_prev_offset_max": ("FLOAT", {"advanced": True,
                    "default": 0.06, "min": 0.0, "max": 0.3, "step": 0.01,
                    "tooltip": "【护栏】逐通道亮度/色度偏移上限，防止整段换色。",
                }),
                "lowfreq_pull": ("FLOAT", {
                    "default": 0.0, "min": 0.0, "max": 1.0, "step": 0.05,
                    "tooltip": "【按需开，默认 0】低频残差传递。\n只把本段开头的低频色档对齐 guide，不动细节与姿态 ⇒ 无重影。\n与跨段统计匹配作用域重叠，建议二选一。\n⚠ 需要 guide，也受跨段总闸约束。",
                }),
                "lowfreq_frames": ("INT", {"advanced": True, "default": 12, "min": 1, "max": 96, "step": 1,
                                           "tooltip": "【配合低频残差】作用帧数（权重衰减到 0）。"}),
                "lowfreq_blur": ("INT", {"advanced": True, "default": 16, "min": 4, "max": 256, "step": 4,
                                         "tooltip": "【低频尺度】默认 16 就够。\n16 → 12 帧约 1.6 秒；64 → 同一段约 22 秒，而缝点阶跃只从 0.0024 降到 0.0011。\n⇒ 除非专治大面积色档漂移，别加到 64；32 是折中。"}),
                # —— 组 2/3 共用：段头作用帧数 ——（2026-09-19 参数收口）
                #   问题：组 2（色档对齐）与组 3（高频补）的**四个**强度旋钮，
                #   作用区长度一直借的是组 4 的 `settle_sharpen_frames`
                #   —— 名字是「糊区锐化帧数」，用户在 UI 上根本看不出这四个
                #   「作用多少帧」受哪个旋钮管。
                #   本节点 0.5.0 期内**无任何存量图**（已全扫 ComfyUI user 目录为零命中），
                #   故这一处**就地插入**而不是追加末位；默认值与 `settle_sharpen_frames`
                #   相同（24）⇒ 没显式设过值的图行为逐位不变。
                "head_zone_frames": ("INT", {
                    "default": 24, "min": 1, "max": 96, "step": 1,
                    "tooltip": "【本段开头帧数】下面四项都按这个帧数作用。\n直方图匹配 / 白平衡 / 反卷积 / 段体高频迁移。\n⚠ 糊区锐化不看这个，它用自己的帧数。",
                }),
                # —— 组 2：段内色档对齐（基准 = 本段段体）——
                "hist_match": ("FLOAT", {
                    "default": 0.0, "min": 0.0, "max": 1.0, "step": 0.05,
                    "tooltip": "【段内】直方图匹配：把本段开头的色阶分布对齐到段体。\n治本段开头与段体之间的色阶漂移（段内，不需要 guide）。\n按需开、默认 0；作用帧数 = head_zone_frames。",
                }),
                "wb_match": ("FLOAT", {
                    "default": 0.0, "min": 0.0, "max": 1.0, "step": 0.05,
                    "tooltip": "【段内】灰世界白平衡：把本段开头 R:G:B 比例对齐段体。\n与直方图匹配正交（那个管亮度总量，这个管色温）。\n按需开、默认 0。",
                }),
                # —— 组 3：高频补（治糊）——
                "deconv_strength": ("FLOAT", {
                    "default": 0.0, "min": 0.0, "max": 1.0, "step": 0.05,
                    "tooltip": "【补高频】反卷积去模糊：提升本段开头高频，按需开、默认 0。\n太大易出振铃（此时把强度降下来）。作用帧数 = head_zone_frames。",
                }),
                "deconv_radius": ("FLOAT", {"advanced": True, "default": 1.5, "min": 0.5, "max": 4.0, "step": 0.1,
                                            "tooltip": "【配合反卷积】模糊核半径。"}),
                "detail_borrow": ("FLOAT", {
                    "default": 0.0, "min": 0.0, "max": 1.0, "step": 0.05,
                    "tooltip": "【补高频】段体高频迁移：把本段开头的高频换成段体的结构。\n比反卷积更像真的，但可能与本段开头内容不符。\n按需开、默认 0。",
                }),
                "detail_blur": ("INT", {"advanced": True, "default": 9, "min": 3, "max": 64, "step": 2,
                                        "tooltip": "【配合高频迁移】高频分离尺度。"}),
                # —— 组 4：收口 ——
                "settle_sharpen": ("FLOAT", {
                    "default": 0.0, "min": 0.0, "max": 1.5, "step": 0.05,
                    "tooltip": "【收口】糊区锐化：对开头几帧做渐变锐化，按需开、默认 0。\n不裁、不动时间轴 ⇒ 不会引入跳帧。建议 0.4~1.0。",
                }),
                # 2026-10-14：默认 0 → 0.5。它是「重绘糊」的正解，且**自弃权**（已达标的帧与段体不动）
                #   ⇒ 对不糊的段是空操作；0.5 是温和档（推荐区间 0.5~1.0 的下沿）。
                "settle_auto": ("FLOAT", {"default": 0.5, "min": 0.0, "max": 1.0, "step": 0.05,
                    "tooltip": "【推荐】自适应糊区补偿（默认 0.5 = 开）。\n节点当场量出每帧清晰度对段体基线的亏空，按亏空比例锐化；已达标的帧与段体一律不动。\n· 0 = 关；0.5~1.0 是推荐区间。\n· 与固定糊区锐化同时开 ⇒ 只作用本项（另一个弃权并点名）。\n⚠ 只能恢复对比度，救不回丢掉的细节。",
                }),
                "settle_sharpen_frames": ("INT", {"advanced": True, "default": 24, "min": 1, "max": 96, "step": 1,
                                                  "tooltip": "【配合糊区锐化】作用帧数（渐变衰减到 0）。\n⚠ 只管糊区锐化；上面四项用 head_zone_frames。"}),
                # ⚠ 继续追加在**最后**（同上铁律）。
                "match_prev_stats_frames": ("INT", {"advanced": True,
                    "default": 1, "min": 0, "max": 24, "step": 1,
                    "tooltip": "【配合跨段统计匹配】统计量取几帧 —— 这一格最容易写错。\n· 1（默认）= 只取紧贴缝的那一帧，与 guide 同一基准 ⇒ 修正量正好是缝上的阶跃。\n· 0 = 对整个作用区聚合 ⇒ 本段开头有亮度梯度时首帧会被推过 guide、凭空多出阶跃。仅供对照。",
                }),
                # ⚠ 继续追加在**最后**（同上铁律）。2026-09-19 新增两项：
                "baseline": (["robust", "legacy"], {
                    "advanced": True,
                    "default": "robust",
                    "tooltip": "【段体参考怎么取】\n· robust（默认）= 逐帧亮度取中央 50% 的帧再算统计，并报出离散度。\n  离散度大于 0.08 判基准不可信，相关项自动弃权。\n· legacy = 旧算法（整段均值），仅供对照。",
                }),
                "cross_seg_ack": ("BOOLEAN", {
                    "default": False,
                    "tooltip": "【跨段总闸】确认 guide 是真参照才打勾。\n· 开 = 跨段统计匹配与低频残差传递可以生效（前提是它们自己也不为 0）。\n· 关（默认）= 两项自动弃权，并在报告里说明。\n· 为什么默认关：走 Latent 桥时 prev_tail 只是近似，对齐它反而把首帧推离真参照。",
                }),
            },
        }

    RETURN_TYPES = ("IMAGE", "STRING")
    RETURN_NAMES = ("images", "report")
    FUNCTION = "apply"
    CATEGORY = CATEGORY
    DESCRIPTION = (
        "续接段的画质域后处理（不碰时间轴、不动帧数、不动音频）："
        "跨段统计匹配 / 低频残差 / 段内直方图与白平衡 / 反卷积与段体高频迁移 / 糊区锐化。"
        "全部默认关闭；guide 接 Trim AV 节点的 prev_tail 才有「缝的另一侧」可对齐。"
    )

    @_node_errors("match_prev", "hist_match", "wb_match")
    def apply(self, images, guide=None,
              match_prev=0.0, match_prev_frames=12,
              match_prev_gain_max=1.15, match_prev_offset_max=0.06,
              lowfreq_pull=0.0, lowfreq_frames=12, lowfreq_blur=16,
              head_zone_frames=24,
              hist_match=0.0, wb_match=0.0,
              deconv_strength=0.0, deconv_radius=1.5,
              detail_borrow=0.0, detail_blur=9,
              settle_sharpen=0.0, settle_auto=0.5, settle_sharpen_frames=24,
              match_prev_stats_frames=CORE.MATCH_PREV_STATS_FRAMES,
              baseline="robust", cross_seg_ack=False):
        n0 = int(images.shape[0])
        out = images
        notes = []
        robust = (baseline != "legacy")
        hz = int(head_zone_frames)

        # 段体基准的**离散度**（一次算完，组 2/组 3 共用；只读统计，零帧数代价）
        disp = 0.0
        if n0 > 40:
            _, disp = CORE.robust_body(images[40:].float(), robust)
        abstain = bool(robust and disp > CORE.POST_BODY_DISP_MAX)

        def _snap():
            return CORE.head_metrics(out, hz)

        def _audit(tag, before):
            lum, hf = CORE.head_metrics(out, hz)
            notes.append("　↳ %s 后：段头亮度 %.5f→%.5f（%+.5f）｜高频 %.6f→%.6f（%+.1f%%）"
                         % (tag, before[0], lum, lum - before[0], before[1], hf,
                            (hf / before[1] - 1.0) * 100.0 if before[1] > 0 else 0.0))

        # 执行顺序：先对齐色调（跨段 → 段内），再补高频，最后锐化收口。
        # —— 跨段两项：**默认弃权**（R4 代理量打标，见 cross_seg_ack 的 tooltip）——
        if float(match_prev) > 0.0 or float(lowfreq_pull) > 0.0:
            if not cross_seg_ack:
                notes.append("跨段两项（match_prev=%.2f / lowfreq_pull=%.2f）：**自动弃权** —— "
                             "guide 未确认是真参照（cond 桥下只是近似，实测对齐它把缝阶跃放大 ×12.6）；"
                             "确认真参照（拷贝桥）请把 `cross_seg_ack` 打勾"
                             % (float(match_prev), float(lowfreq_pull)))
            else:
                if float(match_prev) > 0.0:
                    if guide is None:
                        notes.append("跨段统计匹配：**跳过**（未接 guide）")
                    else:
                        out = CORE.match_prev_stats(out, guide, int(match_prev_frames),
                                                    float(match_prev), float(match_prev_gain_max),
                                                    float(match_prev_offset_max),
                                                    int(match_prev_stats_frames))
                        notes.append("跨段统计匹配 %.2f（%d 帧，对齐上段末帧；统计量取 %s）"
                                     % (float(match_prev), int(match_prev_frames),
                                        "紧贴缝那帧" if int(match_prev_stats_frames) == 1
                                        else "整个作用区聚合（旧口径，仅对照）"
                                        if int(match_prev_stats_frames) <= 0
                                        else "前 %d 帧" % int(match_prev_stats_frames)))
                if float(lowfreq_pull) > 0.0:
                    if guide is None:
                        notes.append("低频残差传递：**跳过**（未接 guide）")
                    else:
                        out = CORE.lowfreq_pull(out, guide, int(lowfreq_frames),
                                                float(lowfreq_pull), int(lowfreq_blur))
                        notes.append("低频残差传递 %.2f（%d 帧 / 尺度 %d）"
                                     % (float(lowfreq_pull), int(lowfreq_frames), int(lowfreq_blur)))

        # —— 组 2 / 组 3：段内（基准 = 段体）——
        hm, wb = float(hist_match), float(wb_match)
        dc, db_ = float(deconv_strength), float(detail_borrow)
        if abstain:
            if hm > 0.0 or wb > 0.0 or dc > 0.0 or db_ > 0.0:
                notes.append("组 2/组 3 **全部弃权**：段体基准离散度 %.3f > %.2f"
                             "（基准不可信 ⇒ 宁可不动；要强制旧口径请把 baseline 设 legacy）"
                             % (disp, CORE.POST_BODY_DISP_MAX))
        else:
            if (hm > 0.0 or wb > 0.0 or db_ > 0.0) and n0 <= 40:
                notes.append("组 2/组 3（段内对齐类）**跳过**：段长 %d ≤ body_start=40，段体为空、无可对齐的基准"
                             % n0)
                hm = wb = db_ = 0.0
            if hm > 0.0 and wb > 0.0:
                notes.append("⚠ 组 2 互斥：直方图匹配与白平衡**同时开了** ⇒ 只作用**直方图匹配**"
                             "，白平衡弃权（两者作用域重叠：一个管总量、一个管比例）")
                wb = 0.0
            if dc > 0.0 and db_ > 0.0:
                notes.append("⚠ 组 3 互斥：反卷积与段体高频迁移**同时开了** ⇒ 只作用**反卷积**"
                             "，高频迁移弃权（两者都是「把高频换成段体的」，叠加会让后一层"
                             "的基准失真、不可归因）")
                db_ = 0.0
            if hm > 0.0:
                b = _snap()
                out = CORE.match_hist_head_to_body(out, hz, hm, body_start=40, robust=robust)
                notes.append("直方图匹配 %.2f（段头↔段体，前 %d 帧，baseline=%s）"
                             % (hm, hz, baseline))
                _audit("直方图匹配", b)
            if wb > 0.0:
                b = _snap()
                out = CORE.match_white_balance(out, hz, wb, body_start=40, robust=robust)
                notes.append("白平衡校正 %.2f（段头↔段体，前 %d 帧，baseline=%s）" % (wb, hz, baseline))
                _audit("白平衡校正", b)
            if dc > 0.0:
                b = _snap()
                out = CORE.deconv_head_zone(out, hz, dc, float(deconv_radius))
                notes.append("反卷积去模糊 %.2f / 半径 %.1f（前 %d 帧）"
                             % (dc, float(deconv_radius), hz))
                _audit("反卷积去模糊", b)
            if db_ > 0.0:
                b = _snap()
                out = CORE.borrow_detail_from_body(out, hz, db_, int(detail_blur),
                                                   body_start=40, robust=robust)
                notes.append("段体高频迁移 %.2f / 尺度 %d（前 %d 帧，baseline=%s）"
                             % (db_, int(detail_blur), hz, baseline))
                _audit("段体高频迁移", b)
        if float(settle_auto) > 0.0:
            b = _snap()
            if float(settle_sharpen) > 0.0:
                notes.append("⚠ 互斥：settle_auto 与 settle_sharpen 同时开 ⇒ 只作用自适应版，固定锐化弃权")
            out, _srep = CORE.settle_compensate(out, body_start=40, strength=float(settle_auto))
            notes.append(_srep or "自适应糊区补偿 %.2f（段长不足，未触发）" % float(settle_auto))
            _audit("自适应糊区补偿", b)
        elif float(settle_sharpen) > 0.0:
            b = _snap()
            out = CORE.sharpen_head_zone(out, int(settle_sharpen_frames), float(settle_sharpen))
            notes.append("糊区锐化 %.2f（%d 帧）" % (float(settle_sharpen), int(settle_sharpen_frames)))
            _audit("糊区锐化", b)

        assert int(out.shape[0]) == n0, "后处理必须帧数守恒"
        line = "[H3 Relay] 后处理：%s" % ("；".join(notes) if notes else "全部关闭（直通）")
        print(line)
        return (out, line)


class H3RelayAudioSeam:
    """音频缝：把上一段的环境声补进本段头部（**长度守恒**，零 A/V 位移）。

    治的现象：段首音频带 ~32ms 解码 priming 近静默 + 生成瞬态，裁重叠把这段放到
    裁剪点上 ⇒ 成片缝处先"抽一下"再起乐（实测缝起 20ms 掉 12–13 dB，邻域最静 −72 dB）。

    做法：把本段头部 ``patch_seconds`` 秒整段换成**上一段的最静窗环境声**
    （stationary 噪声，拼接不可闻），到 ``patch_seconds`` 处交叉淡变回本段自身音频，
    之后**逐位不动**。帧数/音频长度都不变 ⇒ 不消耗时间轴。

    ⚠️ 真 crossfade（三角窗叠化）做不到"单段内长度守恒"——它要裁掉**上一段**的尾部，
    而本节点只能改本段。所以 crossfade 仍归组装层；等长路线用本节点。
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "audio": ("AUDIO", {
                    "tooltip": "【接法】从 Trim AV 的 audio 输出接过来。\n第 1 段没有 Trim AV 时，直接接音频解码。",
                }),
                "run_id": ("STRING", {
                    "default": "relay",
                    "tooltip": "【填什么】片名，与 Latent Save / 桥一字不差。\n它靠这个找上一段存下来的音频，当环境声来源。"
                }),
                "stage_index": ("INT", {
                    "default": 0, "min": 0, "max": 9999, "step": 1,
                    "tooltip": "【填什么】本段段号（第 1 段 = 0）。\n第 1 段无缝可补，会直接直通（音频仍会保存，供第 2 段当环境声来源）。"
                }),
            },
            "optional": {
                "patch_seconds": ("FLOAT", {
                    "default": 1.2, "min": 0.0, "max": 4.0, "step": 0.05,
                    "tooltip": "【头部补丁】把段首这么多秒的内容整段换成上一段的环境声。\n默认 1.2：段与段之间的环境声、底噪不会每段重启一次。\n· 调大覆盖更多开头（超过 1.2 有吞掉开头台词的风险）；调小更保守。\n⚠ 它会替换这 N 秒的全部内容，所以段首本来就不该有台词。0 = 关。",
                }),
                "tile_seconds": ("FLOAT", {"advanced": True,
                    "default": 0.0, "min": 0.0, "max": 4.0, "step": 0.05,
                    "tooltip": "【环境声瓦片秒数】把环境声切成这么长的瓦片，交叠淡化后铺满补丁。\n0（默认）= 从整窗直接取最静的一段。上一段干净环境声比补丁短时用它，建议 1.2。"
                }),
                "fade_seconds": ("FLOAT", {
                    "default": 0.25, "min": 0.0, "max": 0.5, "step": 0.01,
                    "tooltip": "【补丁边界】补丁末端与后面的内容之间做多宽的交叉淡变。\n0 = 硬切（会有可闻的接点）；0.25 是实测可用的值。",
                }),
                "bed_stage": ("INT", {"advanced": True,
                    "default": 0, "min": 0, "max": 9999, "step": 1,
                    "tooltip": "【环境声来源】用第几段的音频（0 = 第 1 段）。\n同一场景环境声变化不大，取第 1 段最稳。⚠ 必须小于本段段号。"
                }),
                "note": ("STRING", {"advanced": True,
                    "default": "",
                    "tooltip": "【可留空】备注，存进文件的元数据里方便事后分辨版本。",
                }),
                # ⚠ 铁律：新 widget 一律**追加在 optional 末位**（旧工作流取值不前移）。
                #   🔴 2026-09-19 真渲染阴性结果驱动（见 CHANGES「音频缝床声选择」）：
                #   旧行为「取床源全局最静窗」会把补丁换成一段**更静**的东西
                #   （实测床声 −42.0 dBFS vs 缝前 −12.8 dBFS）⇒ 缝上从「凹陷」变「静音洞」、
                #   还把本该有的起拍压平。默认档改为**取尾部窗 + 电平对齐**（带峰值护栏）。
                "bed_select": (["tail", "quiet"], {
                    "default": CORE.AUDIO_SEAM_BED_SELECT,
                    "tooltip": "【环境声从哪取】\n· tail（默认）= 从环境声来源的尾部取与补丁等长的一段：紧挨接缝，电平和音色自然接上。\n· quiet = 取全局最静的一段。实测会把补丁换得更静，接缝从凹陷变成静音洞 ⇒ 仅供对照。"
                }),
                # —— 第 3 路输出 joined（拼接）的三个细分参数：都折叠，别占画布 ——
                "join_curve": (["qsin", "tri"], {
                    "advanced": True,
                    "default": "qsin",
                    "tooltip": "【拼接曲线】缝处交叉淡变的曲线。\n· qsin（默认，等功率）= 两段内容不相关时不掉电平。\n· tri（线性）= 实测中缝掉 4.7 dB，听感是音量先小再恢复 ⇒ 仅供对照。",
                }),
                "join_prime_ms": ("FLOAT", {
                    "advanced": True,
                    "default": CORE.AUDIO_ENCODER_PRIME_MS, "min": 0.0, "max": 200.0, "step": 1.0,
                    "tooltip": "【拼接用】每段开头要丢掉的编码器预热（毫秒）。\n默认 33 毫秒（AAC 实测值）。它不是内容，丢掉只是对齐；0 = 不丢。"
                }),
                "join_cross_ms": ("FLOAT", {
                    "advanced": True,
                    "default": 0.0, "min": 0.0, "max": 2000.0, "step": 5.0,
                    "tooltip": "【拼接用】缝处交叉淡变长度（毫秒）。默认 0 = 不做交叉。\n交叉淡变必然缩短时间轴 ⇒ 后段音频比画面提前，且逐段累积。\n所以只有同时填了 join_align_seconds 时才有意义。\n⚠ 只填这一格会直接报错，不会静默拼出一条错位音轨。",
                }),
                "join_segment_seconds": ("FLOAT", {
                    "advanced": True,
                    "default": 0.0, "min": 0.0, "max": 60.0, "step": 0.001,
                    "tooltip": "【拼接用】每段音频的有效时长（秒）= 该段帧数 ÷ 帧率。\n节点 PCM 通常比 mp4 长，不截断拼接就会超长。\n0 = 不截。例如 90 帧 / 24 = 3.75。",
                }),
                "join_align_seconds": ("FLOAT", {
                    "advanced": True,
                    "default": 0.0, "min": 0.0, "max": 10.0, "step": 0.001,
                    "tooltip": "【拼接用】每缝的画面裁量（秒）= 裁掉的帧数 ÷ 帧率。\n大于 0 = 时间轴守恒：输出与裁后画面严格等长，缝上没有电平凹陷。\n0 = 旧的缩短语义（仅对照）。要求不小于 join_cross_ms。",
                }),
                "patch_guard": ("BOOLEAN", {"advanced": True, "default": True,
                    "label_on": "🛡 台词守卫开", "label_off": "守卫关（旧行为）",
                    "tooltip": "【台词守卫】开（默认）= 补丁前先探测本段开头有没有台词。\n有 ⇒ 补丁收缩到台词前 0.40 秒；台词太靠前 ⇒ 整个关掉补丁。\n关 = 旧行为，补丁可能把段首台词一起换掉。"
                }),
                "patch_guard_layers": ("INT", {
                    "advanced": True,
                    "default": CORE.AUDIO_SEAM_PATCH_GUARD_LAYERS, "min": 0, "max": 3, "step": 1,
                    "tooltip": "【台词守卫档位】0（默认）= 只用能量判断。\n1 = 加能量阶跃；2 = 加谱质心；3 = 加谱平坦度（后三档都降低灵敏度）。\n⚠ 1~3 档与守卫「宁可误判也不漏」的意图相反，且标定样本少 ⇒ 默认关。\n⚠ 别把台词安全寄托在这一格：主防线是 patch_seconds 不超过 1.2。",
                }),
                "declick_ratio": ("FLOAT", {
                    "default": CORE.AUDIO_DECLICK_RATIO, "min": 0.0, "max": 50.0, "step": 0.5,
                    "tooltip": "【去咔判断】局部峰值 ÷ 背景底噪。0 = 关；建议 4.0。\n做什么：掐掉「背景极静处冒出来的一声短脉冲」。\n⚠ 看的是它周围有多静，不是它本身多响 —— 只看响度会连台词辅音一起掐。\n⚠ 只动那一处（两端各 2 毫秒淡变），其余内容逐位不动，长度不变。"
                }),
                "declick_quiet_dbfs": ("FLOAT", {"advanced": True,
                    "default": CORE.AUDIO_DECLICK_QUIET_DBFS,
                    "min": -80.0, "max": -20.0, "step": 1.0,
                    "tooltip": "【孤立瞬态抑制】背景必须低于这个电平（dBFS）才动手。默认 −50。\n实测：孤立瞬态处背景 −56 ⇒ 命中；语音辅音处约 −35 ⇒ 不命中（所以不误伤台词）。\n⚠ 调高更激进、可能碰到轻辅音；调低更保守。",
                }),
                "declick_max_len_ms": ("FLOAT", {
                    "default": CORE.AUDIO_DECLICK_MAX_LEN_MS,
                    "min": 1.0, "max": 200.0, "step": 1.0,
                    "tooltip": "【孤立瞬态抑制】多宽算「一声」（毫秒）。默认 50。\n· 10 = 只治 10 毫秒以内的极短脉冲（实测孤立瞬态宽 8 毫秒，仍治得到）。\n· 50（默认）= 连 15~30 毫秒的「嗒 / 啪」也治，代价是可能削到真实的短促音效。\n· 100 以上 = 激进，不建议。",
                }),
                "cut_head_frames": ("INT", {"advanced": True,
                    "default": 0, "min": 0, "max": 9999, "step": 1,
                    "tooltip": "【孤立瞬态抑制】本段会被裁掉多少帧（= 桥的 context_frames）。\n为什么需要：这个节点工作在未裁音频上，而产物是裁后的 ⇒ 缝处那个孤立峰在这里「不孤立」⇒ 不命中，但在成片里它前面什么都没有。\n填了它，抑制器就把该处当「新文件头」来判。\n⚠ 0 = 不启用该修正；一般由脚本自动填。",
                }),
                # —— 2026-10-08：**响度归一**（不接声锚也要）。**继续追加在末位**，槽位安全。
                "loudness_normalize": ("BOOLEAN", {"advanced": True,
                    "default": True,
                    "tooltip": "【响度均衡】开（默认）= 把本段人声响度对齐到统一目标。\n为什么默认开：模型跟随声音参考的响度 —— 接参考时参考已归一，不接参考时没人管。\n每段电平于是由模型自己决定（实测三段差 7.97 dB）。关 = 恢复旧行为。",
                }),
                "loudness_target_dbfs": ("FLOAT", {"advanced": True,
                    "default": -23.0, "min": -60.0, "max": 0.0, "step": 0.5,
                    "tooltip": "【响度均衡】目标电平（有声窗 RMS 中位，dBFS）。默认 −23.0。\n⚠ 不是整段 RMS、也不是峰值 —— 静音与环境声不计入，所以描述的是人声的响度。",
                }),
            },
        }
    RETURN_TYPES = ("AUDIO", "STRING", "AUDIO")
    RETURN_NAMES = ("audio", "report", "joined")
    FUNCTION = "seam"
    CATEGORY = CATEGORY
    # 落盘本身就是产物：没有下游消费也要执行（否则第 1 段的床源文件永远不生成）
    OUTPUT_NODE = True
    DESCRIPTION = ("把上一段的环境声补进本段头部，去掉裁切点上的解码静默与生成瞬态；"
                   "长度守恒、零 A/V 位移。**并顺手把 0..本段 的音频接成一条连续音轨"
                   "（去编码器 priming + 等功率交叉），从第 3 路 `joined` 输出——"
                   "拼接这件事本身也在节点内完成，用户不需要外部 ffmpeg。**")

    def _joined(self, run_id, idx, curve="qsin",
                prime_ms=CORE.AUDIO_ENCODER_PRIME_MS, cross_ms=50.0,
                segment_seconds=0.0, align_seconds=0.0):
        """把 0..本段 的落盘音频接成**一条**（复合进本节点，不新增轮子）。

        为什么拼接必须由本包做（两个病都不在**单段**可达范围内）：
          · **编码器 priming**：每段 mp4 的 AAC 流头 ~33 ms 近静音（实测 @32k = 1056 样本；
            同位置本节点落盘的音频有内容 −22.9 dBFS、mp4 解码 −66.8 dBFS，差 44 dB）——
            它在**编码之后**才产生，单段节点看不见、删不掉。
          · **交叉曲线**：两段内容不相关时，ffmpeg `acrossfade` 默认 **tri（线性）中缝掉 −4.7 dB**
            —— 就是听感「音量先小再恢复」；等功率 `qsin` 掉 −1.85 dB，配去 priming 只掉 **−0.36 dB**。

        返回 ``(audio|None, note)``；段文件不全或读失败 ⇒ 返回 None 并说明（不抛，避免断链）。
        """
        _raw_dir = os.path.dirname(_audio_stage_path(run_id, 0))
        paths = [os.path.join(_raw_dir, "audio_raw_%05d.safetensors" % i)
                 for i in range(int(idx) + 1)]
        _fallback = [_audio_stage_path(run_id, i) for i in range(int(idx) + 1)]
        paths = [p if os.path.isfile(p) else f        # raw 缺失回退裁后（前奏不可得，退化为无前奏）
                 for p, f in zip(paths, _fallback, strict=False)]
        miss = [i for i, p in enumerate(paths) if not os.path.isfile(p)]
        if miss:
            return None, "（joined 跳过：缺第 %s 段落盘音频）" % miss
        try:
            segs = [CORE.load_audio(p) for p in paths]
            sr = int(segs[0]["sample_rate"])
            j, rep = CORE.join_audio_segments(
                segs,
                prime_samples=int(round(float(prime_ms) / 1000.0 * sr)),
                cross_samples=int(round(float(cross_ms) / 1000.0 * sr)),
                curve=str(curve or "qsin"),
                segment_seconds=float(segment_seconds or 0.0),
                align_seconds=float(align_seconds or 0.0))
            # J-cut 守恒结果落盘（组装层 --audio-pcm 直接取用；覆盖写，最后一次执行为准）
            if float(align_seconds or 0.0) > 0:
                try:
                    jp = os.path.join(os.path.dirname(paths[-1]), "audio_joined.safetensors")
                    CORE.save_audio(j, jp, note="joined align=%.3f" % float(align_seconds))
                    rep += "\n           已落盘：%s" % jp
                except Exception as _e:                                   # 落盘失败不断链
                    rep += "\n           ⚠ joined 落盘失败：%r" % (_e,)
        except Exception as e:
            return None, "（joined 失败：%r）" % (e,)
        return j, rep

    @_node_errors("patch_seconds", "tile_seconds", "bed_stage")
    def seam(self, audio, run_id, stage_index, patch_seconds=0.0, tile_seconds=0.0,
             fade_seconds=0.25, bed_stage=0, note="",
             bed_select=CORE.AUDIO_SEAM_BED_SELECT,
             join_curve="qsin", join_prime_ms=CORE.AUDIO_ENCODER_PRIME_MS,
             join_cross_ms=0.0, join_segment_seconds=0.0, join_align_seconds=0.0,
             patch_guard=True, patch_guard_layers=CORE.AUDIO_SEAM_PATCH_GUARD_LAYERS,
             declick_ratio=4.0, declick_quiet_dbfs=-50.0, cut_head_frames=0,
             declick_max_len_ms=None,
             loudness_normalize=True,
             loudness_target_dbfs=CORE.AUDIO_LOUDNESS_TARGET_DBFS):
        me = _audio_stage_path(run_id, int(stage_index))
        idx = int(stage_index)
        patch = float(patch_seconds or 0.0)
        # 🔴 2026-10-08：**响度归一**（不接声锚也要）。放在**最前面**，三条理由：
        #   ① 它是**线性增益** ⇒ 与后续的 J-cut 视图切换 / patch / declick 顺序无关；
        #   ② 落盘的 `audio_0000i`（供后段当**床源**）与第 3 路 `joined` 取的都是归一后的音频
        #      ⇒ 缝处两侧电平一致，不会在缝上再出现台阶；
        #   ③ 与**声锚**不冲突：锚（`voice_bank.normalize_anchor`）已把锚对齐到**同一个**
        #      目标 −23.0 dBFS，模型跟随锚 ⇒ 本段本就接近目标 ⇒ 这里增益落在容忍带内 ⇒ **不动**。
        #      （即"锚归一是把上游拉齐、这里是给没有锚的情况兜底"，两者幂等叠加，不是两套标准。）
        _ld_note = ""
        if loudness_normalize:
            try:
                audio, _ld_note = CORE.normalize_loudness(
                    audio, float(loudness_target_dbfs))
            except Exception as _ld_err:       # 归一出错**不能**拖垮这一段（与写进度同一条纪律）
                audio, _ld_note = audio, "⚠ 响度归一失败（原样送出）：%r" % (_ld_err,)
            if _ld_note:
                print("[H3 Relay] 响度归一（本段）：%s" % _ld_note)
        # 🔴 2026-09-19 J-cut：原始（未裁）音频另落盘，供 _joined 取「被裁掉的前奏」；
        #   工作视图切到 [align:]（= 裁后音频）—— patch/落盘 audio_0000i/第 1 路输出
        #   的语义全部保持现状（都基于裁后视图）。
        _al_s = float(join_align_seconds or 0.0)
        # 🔴 2026-09-21 铁律·音画同步：`joined` 只有在**给了画面裁量**时才能做交叉淡变。
        #   交叉淡变是 overlap-add ⇒ **必然缩短时间轴** ⇒ 每缝后段音频相对画面提前 cross 秒
        #   （第 3 段起口型明显对不上，且逐段累积）。这类「静默产出一条错位音轨」按包内纪律
        #   **必须 raise**，不给"忽略参数"的中间态（R5：无断言即未验收；硬错误不静默降级）。
        _cross_ms = float(join_cross_ms or 0.0)
        if _cross_ms > 0.0 and _al_s <= 0.0:
            raise ValueError(
                "音频缝：给了 join_cross_ms=%.0f ms，但 join_align_seconds=0。\n"
                "    交叉淡变是 overlap-add ⇒ **必然缩短时间轴** ⇒ 每缝后段音频相对画面"
                "提前 %.0f ms，逐段累积到口型对不上。\n"
                "    二选一（两条路都不会错位）：\n"
                "      ① J-cut 守恒路（缝上更平滑）：join_align_seconds 填「Trim AV」"
                "报告里那行建议值（= 裁首帧数 ÷ fps），且 join_cross_ms ≤ 它；\n"
                "      ② 等长拼接（不做交叉，与段文件首尾相接等价）：join_cross_ms 填 0（默认）。"
                % (_cross_ms, _cross_ms))
        _raw_wf, _raw_sr, _raw_lead, _raw_dt = CORE._audio_parts(audio)
        _raw_path = os.path.join(os.path.dirname(me), "audio_raw_%05d.safetensors" % idx)
        try:
            CORE.save_audio(audio, _raw_path, note=note)
        except Exception:
            pass                                                  # 原始落盘失败不断链
        if _al_s > 0:
            _an = int(round(_al_s * _raw_sr))
            if 0 < _an < int(_raw_wf.shape[-1]):
                _shaped = _raw_wf[..., _an:].reshape(*_raw_lead, _raw_wf.shape[0],
                                                     _raw_wf.shape[-1] - _an) if _raw_lead else \
                    _raw_wf[..., _an:]
                audio = {"waveform": _shaped.to(_raw_dt), "sample_rate": _raw_sr}
        # 🆕 组 4 · 孤立瞬态抑制（declick，2026-10-03）
        # 动机：模型会在**无台词区**生成孤立短脉冲（实测段2 @0.930s，峰值 0.0095 = −40 dBFS，
        #   而背景仅 −60 dBFS ⇒ 在安静背景里突出成「无缘无故的孤立瞬态」，本仓作者 耳检定性为伪影）。
        # 🔴 与 `patch_seconds` 的关系：**独立但必须排在 patch 之后**（2026-10-04 修正）：
        #   patch 是「整段替换头部 N 秒」⇒ 若 declick 先做，**它对头部的处理会被 patch 整段盖掉**
        #   （实测「孤立瞬态」在 0.930s，正好落在 patch 窗内 ⇒ 白做，且.report 看着像治了）。
        #   ⇒ 两个分支都在**最终送出的那份音频**上做（直通档 = `audio`；补丁档 = `out`）。
        #   实现在 `CORE.declick_on_segment`（核心层，可单测）。
        if idx <= 0 or patch <= 0.0:
            # 政策（范围闸 / fallback / 裁帧边界换算）在 `CORE.declick_gate`，
            # 这里只接线 —— 目的是让那三条分支进得了单测（原先在闭包里是零覆盖）。
            audio, _dc_rep = CORE.declick_on_segment(
                audio, declick_ratio, declick_quiet_dbfs, cut_head_frames,
                declick_max_len_ms)
            _dc_note = ("\n" + _dc_rep) if _dc_rep else ""
            CORE.save_audio(audio, me, note=note)
            why = "第 1 段无缝可补" if idx <= 0 else "补丁关（patch_seconds=0）"
            line = ("[H3 Relay] 音频缝：%s → 直通｜本段音频已落盘（供后段当床源）：%s%s"
                    % (why, me, _dc_note))
            _j, _jn = self._joined(run_id, idx, join_curve, join_prime_ms, join_cross_ms,
                                  join_segment_seconds, join_align_seconds)
            if _jn.startswith("（joined"):
                line += "｜ " + _jn
            else:
                line += "｜ " + _jn.splitlines()[0]
            print(line)
            # ♪ 边车回显：本节点刚落的那份 `me` **就是送进落盘节点的同一份音频**
            #   （直通档也是它）⇒ 拼接直读它，既无损又不会绕过任何音频缝处理。
            _u = _pcm_ui(me)
            # 🔴 两路 AUDIO 出口转 float32（见 `CORE.audio_to_fp32`：下游 `VHS_VideoCombine`
            #   按 f32le 送 ffmpeg 且不转 dtype，fp16 进去 ⇒ 成片整轨静音）。
            #   落盘那份（`save_audio(audio, me)`）保持原 dtype —— 只被本包读回，省一半磁盘。
            #   ⚠ `joined` 缺失时**复用同一个结果**，别对同一份波形转两遍（白多一次整块拷贝）。
            _a_out = CORE.audio_to_fp32(audio)
            _r = (_a_out, line, CORE.audio_to_fp32(_j) if _j is not None else _a_out)
            return {"ui": {CORE.PCM_UI_KEY: [_u]}, "result": _r} if _u else _r

        b_idx = int(bed_stage)
        if b_idx >= idx:
            raise RuntimeError(
                "床源段号（%d）必须小于本段段号（%d）——床源得是**已经渲染完**的那一段。\n"
                "    想用上一段当床源就填 0（或用默认值）。" % (b_idx, idx)
            )
        bed_path = _audio_stage_path(run_id, b_idx)
        if not os.path.isfile(bed_path):
            raise FileNotFoundError(
                "床源音频不存在：%s\n"
                "    第 %d 段还没跑过（本节点会顺手把每段音频落盘）。\n"
                "    先按段号顺序跑一次第 %d 段，再来跑本段。" % (bed_path, b_idx, b_idx)
            )
        # 电平目标 = **缝前**那段音频的尾部（= 上一段，真值）；拿不到才退回床源自身尾部。
        #   为什么单独取上一段：床源默认可选第 1 段（stationary 环境声更稳），
        #   但「缝前电平」只由**上一段**决定——两者不是一回事（0.5.0 把水平目标错当床源了）。
        target = None
        prev_path = _audio_stage_path(run_id, idx - 1)
        if os.path.isfile(prev_path):
            try:
                target = CORE.load_audio(prev_path)
            except Exception as _e:
                print("[H3 Relay] 音频缝：读上一段音频失败（%r）→ 电平目标退回床源尾部" % (_e,))
        out, rep = CORE.audio_seam_patch(audio, CORE.load_audio(bed_path),
                                         patch, float(tile_seconds or 0.0),
                                         float(fade_seconds),
                                         select=str(bed_select or CORE.AUDIO_SEAM_BED_SELECT),
                                         target_audio=target,
                                         stage_index=idx, patch_guard=bool(patch_guard),
                                         patch_guard_layers=int(patch_guard_layers or 0))
        # 🔴 declick 排在 patch **之后**：patch 会整段替换头部 ⇒ 先做 declick 会被它盖掉。
        out, _dc_rep = CORE.declick_on_segment(
            out, declick_ratio, declick_quiet_dbfs, cut_head_frames,
            declick_max_len_ms)
        _dc_note = ("\n" + _dc_rep) if _dc_rep else ""
        CORE.save_audio(out, me, note=note)
        line = rep + ("｜已落盘（供后段当床源）：%s" % me) + _dc_note
        if target is None:
            line += "｜ ⚠ 上一段音频文件缺失 ⇒ 电平目标退回床源尾部（不够准）"
        _j, _jn = self._joined(run_id, idx, join_curve, join_prime_ms, join_cross_ms,
                                  join_segment_seconds, join_align_seconds)
        if _jn.startswith("（joined"):
            line += "｜ " + _jn
        else:
            line += "｜ " + _jn.splitlines()[0]
        print(line)
        # ♪ 边车回显（同上）：`me` = 本节点输出 `out` = 落盘节点拿到的音频。
        _u = _pcm_ui(me)
        # 🔴 两路 AUDIO 出口都转 float32（理由同 `CORE.audio_to_fp32`：下游 `VHS_VideoCombine`
        #   按 f32le 送 ffmpeg 且不转 dtype，fp16 进去 ⇒ 成片整轨静音）。
        #   落盘用的 `out`（`save_audio` 那一份）**保持原 dtype** —— 它只被本包读回，
        #   且组装层 `load_pcm_sidecar` 自己会转 f32；存 fp16 省一半磁盘。
        #   ⚠ `joined` 缺失时复用同一份结果（别转两遍）。
        _a_out = CORE.audio_to_fp32(out)
        _r = (_a_out, line, CORE.audio_to_fp32(_j) if _j is not None else _a_out)
        return {"ui": {CORE.PCM_UI_KEY: [_u]}, "result": _r} if _u else _r

class H3RelaySeedFromVideo:
    """🔗 H3 Relay · Seed from Video —— 把**一段已有视频**变成第 0 段（种子段）。

    为什么需要它：本仓整条链都从「上一段的 latent 边车」出发 ⇒ 第 1 段必须由本仓生成。
    本节点让**任意来源的视频**（实拍 / 别人的产物 / 早先跑的成片）成为链的起点，
    之后每一段都走本仓的近完美接缝（逐位钉住 + 裁重叠）。

    它替你挡三处坑（不挡就是静默出坏片，或桥当场 raise）：

      ① **时序栅格**：H3 只认 5+17k 帧（⇔ ``latent_t ≡ 2 (mod 5)``）。源帧数不在档上时，
         桥切尾段的起点会落在周期位置非 0 处 ⇒ 相位错位 ⇒ 桥直接拒。
         本节点按**帧尾**裁到最近的档（裁掉几帧写进报告与边车头，不悄悄丢）。
      ② **画布栅格**：宽高必须是 16 的倍数，且**要与后续各段一致**（桥跨分辨率直接拒）。
         尺寸不符时按你填的 width/height **居中裁切 + lanczos 重采样**（不拉伸变形）。
      ③ **音画等长**（铁律二）：源音轨与视频帧数对不上时，按视频长度**裁尾 / 补零**；
         源没有音轨则给**静音种子**（缝处的音频由本段生成接管），
         而不是让桥去报「context_latent 没有音频流」。

    产出：``output/relay_kit/<run_id>/stage_NNNNN.safetensors``（含谱系头 + ``source=video``）。
    后续接法与本仓任何一段**完全相同**：Latent Load / Copy Bridge / Trim AV 原样用。
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "video": ("IMAGE", {
                    "tooltip": "【接法】已有视频的画面帧（LoadVideo 的画面输出）。\n它会变成链的第 1 段，之后各段照常往外接。",
                }),
                "video_vae": ("VAE", {
                    "tooltip": "【接什么】H3 的视频 VAE（与出词节点用的同一个）。",
                }),
                "audio_vae": ("VAE", {
                    "tooltip": "【接什么】H3 的音频 VAE（与音频解码用的同一个）。",
                }),
                "width": ("INT", {
                    "default": 0, "min": 0, "max": 8192, "step": 16,
                    "tooltip": "【画面宽】0 = 跟随源视频（默认，源尺寸向下取到 16 的倍数，不动内容）。\n填具体值 = 按这里居中裁切重采样，且必须与后续各段、出词节点一致（不一致桥会拒）。\n⚠ 两个都要么填 0、要么都填值。",
                }),
                "height": ("INT", {
                    "default": 0, "min": 0, "max": 8192, "step": 16,
                    "tooltip": "【画面高】0 = 跟随源视频（默认，源尺寸向下取到 16 的倍数，不动内容）。\n填具体值 = 按这里居中裁切重采样，且必须与后续各段、出词节点一致（不一致桥会拒）。\n⚠ 两个都要么填 0、要么都填值。",
                }),
                "run_id": ("STRING", {
                    "default": "relay",
                    "tooltip": "【填什么】片名。\n源视频会存成这一段的边车，必须与后面各段一字不差。",
                }),
                "stage_index": ("INT", {
                    "default": 0, "min": 0, "max": 9999, "step": 1,
                    "tooltip": "【填什么】源视频是第几段。\n通常填 0（当第 1 段），后面从 1 起往外接。",
                }),
            },
            "optional": {
                # ⚠ 新 widget 必须追加在**最后一个**：widgets_values 按位置对应。
                "audio": ("AUDIO", {
                    "tooltip": "【接法】源视频的音轨（LoadVideo 的音频输出）。\n不接 = 静音种子，缝处音频由本段生成接管。",
                }),
                "snap": (["trim", "error"], {
                    "default": "trim",
                    "tooltip": "【帧数不在档上怎么办】trim = 按帧尾裁到最近的合法档（默认）。\nerror = 直接报错，由你自己先裁好再喂进来。",
                }),
                "note": ("STRING", {
                    "default": "",
                    "tooltip": "【可留空】写进边车头的备注，比如「源片 v2」。",
                }),
            },
        }

    RETURN_TYPES = ("LATENT", "STRING")
    RETURN_NAMES = ("latent", "report")
    FUNCTION = "seed"
    CATEGORY = CATEGORY
    OUTPUT_NODE = True          # 落盘本身就是它的产物，没有下游也必须执行
    DESCRIPTION = ("把一段已有视频编码成第 0 段（种子段）的 AV latent 边车，"
                   "之后各段即可用本插件的续接链往外接。"
                   "自动处理时序栅格（5+17k）、画布栅格（16 的倍数）与音画等长。")

    @_node_errors("run_id", "stage_index")
    def seed(self, video, video_vae, audio_vae, width, height, run_id, stage_index,
             audio=None, snap="trim", note=""):
        import comfy.audio
        import comfy.nested_tensor
        import comfy.utils
        import torch

        if video is None or not torch.is_tensor(video) or video.dim() != 4:
            raise ValueError("video 需要是画面帧张量 [帧, 高, 宽, 通道]（接 LoadVideo 的画面输出）。")
        n_in = int(video.shape[0])

        # —— ① 时序栅格：按帧尾裁到粗档 5+17k（细档会让桥的尾段相位错位）
        n = CORE.frame_grid_floor(n_in)
        if n < 5:
            raise ValueError(
                "源视频只有 %d 帧，短于 H3 的最短片段 5 帧 ⇒ 没有合法档可裁。\n"
                "    至少要 5 帧（5+17k：5 / 22 / 39 / 56 …）。" % n_in)
        dropped = n_in - n
        if dropped and str(snap) == "error":
            raise ValueError(
                "源视频 %d 帧不在 H3 的 5+17k 档上（最近的合法档 = %d 帧，要裁掉 %d 帧）。\n"
                "    把 snap 改成 trim 让它自己裁，或先自己裁好再喂进来。" % (n_in, n, dropped))
        frames = video[:n] if dropped else video

        # —— ② 画布栅格：16 的倍数（latent 用 //16 开方），且必须与后续各段一致
        w, h = int(width), int(height)
        # 🆕 2026-10-20：**0 = 跟随源视频**。源本身就带尺寸，不该逼用户把它抄三处
        #    （本节点 / 出词节点 / 后续各段）——抄错一处要等到桥那里才报，是首次使用最常踩的坑。
        #    向下取到 16 的倍数 ⇒ 只做亚像素级缩小，**不放大、不裁内容**。
        auto_canvas = (w == 0 and h == 0)
        if auto_canvas:
            h = max(64, (int(video.shape[1]) // 16) * 16)
            w = max(64, (int(video.shape[2]) // 16) * 16)
        elif w == 0 or h == 0:
            raise ValueError(
                "width / height 必须**同时**填 0（跟随源视频）或**同时**填具体值；"
                "现在 width=%d height=%d。\n"
                "    只填一个 = 另一个没有依据，不猜。" % (w, h))
        for _nm, _v in (("width", w), ("height", h)):
            if _v < 32 or _v % 16 != 0:
                raise ValueError(
                    "%s 必须是 ≥32 且 16 的倍数（H3 画布栅格；得到 %d）。" % (_nm, _v))
        # 只取前 3 通道（与核心「VAE Encode」同款）：带 alpha 的 4 通道直接喂进去会喂错通道
        frames = frames[..., :3]
        resized = (int(frames.shape[1]) != h) or (int(frames.shape[2]) != w)
        if resized:
            px = comfy.utils.common_upscale(frames.movedim(-1, 1), w, h, "lanczos", "center")
            frames = px.movedim(1, -1)

        # —— ③ 编码：视频流 + 音频流（音频按视频长度对齐，缺则静音种子）
        v = video_vae.encode(frames)
        if not torch.is_tensor(v) or v.dim() != 5 or int(v.shape[1]) != 24:
            # ⚠️ 先拼成字符串再插：`% (tuple(...))` 在值是 tuple 时会被当成**多个**参数，
            #    与单个 %r 不匹配 ⇒ 抛 TypeError，把真正的错因盖掉（本行就是这么被抓出来的）。
            _got = "%r" % (tuple(v.shape) if torch.is_tensor(v) else type(v),)
            raise ValueError(
                "video_vae 编出来的不是 H3 视频 latent"
                "（期望 [B,24,T,H/16,W/16]，得到 %s）。\n"
                "    接错 VAE 了？请接 H3 的视频 VAE。" % _got)
        lt = int(v.shape[2])
        fc_check = CORE.pixel_frames(lt)
        if fc_check != n:
            raise ValueError(
                "栅格对不上：源视频 %d 帧编出 %d 步 latent，而 H3 的时序网格说这该是 %d 帧。\n"
                "    ⇒ 这个视频 VAE 的时序压缩与 H3 不一致，不能继续"
                "（继续会得到帧位错开的片子）。" % (n, lt, fc_check))
        at = CORE.audio_steps(n)
        a_note = "静音种子（源没有音轨）"
        bed_wf = None                     # 供⑤落床边车的波形（None = 源没有音轨）
        # 本段音频应有的样本数（= n 帧 @24fps 的秒数 × 采样率）。**编码前**就要它 ——
        #   🔴 2026-10-20：原来是把**整条源音轨**编完再 `a[..., :at]` 截断 ⇒ 60 s 的源
        #   只为留 8 s 就白烧 7.5 倍音频 VAE 时间与显存。先截再编，结果逐位等价。
        _sr_bed = int(getattr(audio_vae, "audio_sample_rate", 32000))
        _want = int(round(n / CORE.FPS * _sr_bed))      # n 帧 @24fps 的秒数 × 采样率
        if audio is None:
            a = torch.zeros([1, 32, 2, at], dtype=v.dtype, device=v.device)
        else:
            wf = audio.get("waveform") if isinstance(audio, dict) else None
            sr = int(audio.get("sample_rate") or 0) if isinstance(audio, dict) else 0
            if wf is None:
                raise ValueError("audio 需要是波形字典 {'waveform': [B,C,L], 'sample_rate': int}。")
            if not sr:
                raise ValueError(
                    "audio 里没有 sample_rate（收到 %r）⇒ 无法确定要不要重采样，不猜。" % (sr,))
            vae_sr = int(getattr(audio_vae, "audio_sample_rate", 32000))
            if sr != vae_sr:
                wf = comfy.audio.resample(wf, sr, vae_sr)
            # 先按本段长度裁/补，**再**编码（省掉整条源音轨的无效编码）
            _n_in = int(wf.shape[-1])
            if _n_in > _want:
                wf = wf[..., :_want]
                _len_note = "源音轨 %d → 截到本段 %d 样本（省掉 %.1f× 无效编码）" % (
                    _n_in, _want, _n_in / float(_want))
            elif _n_in < _want:
                wf = torch.nn.functional.pad(wf, (0, _want - _n_in))
                _len_note = "源音轨 %d → 补零到本段 %d 样本" % (_n_in, _want)
            else:
                _len_note = "源音轨 %d 样本，长度本来就对" % _n_in
            bed_wf = wf[:1]
            a = audio_vae.encode(wf[:1].movedim(1, -1))
            if not torch.is_tensor(a) or a.dim() != 4 or int(a.shape[1]) != 32:
                _gota = "%r" % (tuple(a.shape) if torch.is_tensor(a) else type(a),)
                raise ValueError(
                    "audio_vae 编出来的不是 H3 音频 latent"
                    "（期望 [B,32,2,T]，得到 %s）。\n"
                    "    接错 VAE 了？请接 H3 的音频 VAE。" % _gota)
            got = int(a.shape[-1])
            # 下面两支是**护栏**（VAE 步长粒度可能让 got 差 1 步）；主路径已在上游截好。
            if got > at:
                a = a[..., :at]
                a_note = "%s；编码 %d 步 → 裁到 %d 步（@%d Hz）" % (_len_note, got, at, vae_sr)
            elif got < at:
                a = torch.nn.functional.pad(a, (0, at - got))
                a_note = "%s；编码 %d 步 → 补零到 %d 步（@%d Hz）" % (_len_note, got, at, vae_sr)
            else:
                a_note = "%s；编码 %d 步（@%d Hz）" % (_len_note, got, vae_sr)
        # 两条流必须同 dtype/同设备：NestedTensor 只存列表、**不校验**，
        # 混着进去会在采样器里才炸（且报错指向不明）。音频流很小，搬一次可以忽略。
        a = a.to(dtype=v.dtype, device=v.device)

        latent = {"samples": comfy.nested_tensor.NestedTensor((v, a))}

        # —— ④ 落盘 + 谱系（`source=video` 让事后看得出这一段不是本仓生成的）
        path = _stage_path(run_id, stage_index)
        lineage = _stage_lineage(run_id, stage_index)
        # 种子段的来源是**一段视频**，不是链里的上一段 ⇒ 父段记空串（沿用「段号−1」会是假谱系）
        lineage["parent_stage"] = ""
        lineage.update({"source": "video", "source_frames": n_in, "frames": n,
                        "dropped_frames": dropped, "canvas": [w, h], "latent_steps": lt})
        CORE.save_av_latent(latent, path, note=note, lineage=lineage)
        # —— ⑤ 音频床边车（`audio_%05d.safetensors`）：让后续段的「音频缝」有床源可读 ——
        #    🔴 为什么必须在这里补（2026-10-19）：那个床文件**只有音频缝节点自己会写**，
        #    而视频起段不经过音频缝 ⇒ 不补的话，起段链里音频缝永远报「床源音频不存在」，
        #    且后续段也补不上（bed_stage 只能指向不存在的 stage 0）⇒ 结构性死锁。
        #    起段手里就有原始音轨（零解码成本）⇒ 缺口在这里补上，音频缝的契约不变。
        if bed_wf is None:
            _bed_wf = torch.zeros([1, 1, _want], dtype=torch.float32)
            _bed_note = ("静音床边车（源没有音轨）。要让音频缝只在有环境声的段之间起作用，"
                         "把它的 patch_seconds 填 0 或把 bed_stage 改指向有音轨的段")
        else:
            _bed_wf = bed_wf
            if int(_bed_wf.shape[-1]) > _want:
                _bed_wf = _bed_wf[..., :_want]
            elif int(_bed_wf.shape[-1]) < _want:
                _bed_wf = torch.nn.functional.pad(
                    _bed_wf, (0, _want - int(_bed_wf.shape[-1])))
            _bed_note = "床边车来自源音轨（已重采样到 %d Hz，裁/补到与视频等长 %d 样本）" % (
                _sr_bed, _want)
        _bed_path = _audio_stage_path(run_id, stage_index)
        CORE.save_audio({"waveform": _bed_wf, "sample_rate": _sr_bed},
                        _bed_path, note="seed bed")
        if auto_canvas:
            _canvas_note = "（跟随源视频，已居中裁切重采样）" if resized else "（跟随源视频）"
        else:
            _canvas_note = "（已居中裁切重采样）" if resized else "（尺寸本来就对）"
        lines = [
            "[H3 Relay] 已把源视频写成第 %d 段（种子）→ %s" % (int(stage_index), path),
            "  帧：源 %d → 用 %d%s ｜ 画布 %dx%d%s"
            % (n_in, n, ("（按帧尾裁掉 %d 帧）" % dropped) if dropped else "（本来就在档上）",
               w, h, _canvas_note),
            "  latent：视频 %s 步（覆盖 %d 帧）｜ 音频 %d 步"
            % (lt, fc_check, at),
            "  时间轴：按 %.0f fps 解释帧序列（本节点拿不到源的 fps）—— 源若不是 %.0f fps，"
            "画面时长会跟着变，请先把源转成 %.0f fps 再喂进来。"
            % (CORE.FPS, CORE.FPS, CORE.FPS),
            "  音频：%s" % a_note,
            "  床边车：%s（%s）" % (_bed_path, _bed_note),
            "  头：run=%s 父段=无（来源是视频）pack=%s source=video"
            % (lineage["run_id"], lineage["pack"]),
        ]
        report = "\n".join(lines)
        print(report, flush=True)
        return (latent, report)


class H3RelayJoinAV:
    """🧩 H3 Relay · Join AV——把「本段裁后画面/音频」接到累计后面，拼成一条。
    ⚠ **定位 = 内部节点，请勿手接**（2026-10-22 按「不新增节点」设计意图梳理）：它只是
    `torch.cat` + 严格规格校验，**不是底层技术**；唯一技术必然性是 ⏩ Finite Run 展开图由宿主
    执行、而宿主只跑注册表里的节点 ⇒ 张量域拼接必须以节点形式存在。平时用 ⏩ Finite Run
    一次提交，或 🔗 Chain 的「🧩 拼成一条」（文件路，不驻留内存）。
    为什么不用核心自带的批处理节点：那个节点规格不符时**静默拉伸/补边**（把小图放大去凑大图）
    ⇒ 看到的是「被拉变形的接缝」而不是报错。本节点规格不符直接报错（宁可不拼）。
    ⚠ 显存账：逐段 `torch.cat` ⇒ 驻留 = Σ各段帧、拷贝 = O(N²)。按参数算 **37 段 16.2 GiB**
    （12GB 卡必爆）⇒ 长片分批或走 Chain 文件路（⏩ Finite Run 的 report 会按参数算好并提示）。
    """

    # 🔴 2026-10-22：**从前端节点菜单里隐去**（不是删掉、不是不注册）。
    #   用户不该在菜单里看到一个「不知道干什么、接了也没用」的节点。
    #   宿主语义（实测 ComfyUI 前端 1.57.1 的注册代码）：
    #       dev_only && (r.skip_list = !DevMode)
    #   ⇒ 注册照旧（API 提交、Finite Run 展开图、已存工作流**全部照常**），只是**不进菜单**；
    #     设置里勾上 `Comfy.DevMode` 才看得到 —— 给"我就是想手接"的人留了门。
    #   ⚠ 用 DEV_ONLY 而不是 DEPRECATED：后者会挂一个「已弃用」红标，语义是错的
    #     （本节点不是被替代品淘汰，它是**内部件**）。
    #   ⚠ V3 出口要在 `v3/_compat.schema_from_v1` 里同步 `is_dev_only`，否则默认出口下白标。
    DEV_ONLY = True

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "images": ("IMAGE", {
                    "tooltip": "【内部节点勿手接】⏩ Finite Run 自动接线；手动连拼接 Trim AV 画面。",
                }),
                "audio": ("AUDIO", {
                    "tooltip": "【内部节点勿手接】⏩ Finite Run 自动接线；手动连拼接 Trim AV 音频。",
                }),
            },
            "optional": {
                "images_acc": ("IMAGE", {
                    "tooltip": "【累计画面】上一段拼到现在的画面。\n首段不接；第 2 段起接上一个 Join AV 的 images 输出。",
                }),
                "audio_acc": ("AUDIO", {
                    "tooltip": "【累计音频】上一段拼到现在的音频，与累计画面成对使用。",
                }),
            },
        }

    RETURN_TYPES = ("IMAGE", "AUDIO", "STRING")
    RETURN_NAMES = ("images", "audio", "report")
    FUNCTION = "join"
    CATEGORY = CATEGORY
    DESCRIPTION = (
        "内部节点，请勿手接 —— ⏩ Finite Run 展开图自动用它做张量域拼接；"
        "人要拼片请用 🔗 Chain 的「🧩 拼成一条」（文件路，不驻留内存）。\n"
        "把「本段裁后画面/音频」拼到累计后面，出一条完整音画（张量域，零重编码）。\n"
        "规格不符（分辨率/帧率/采样率/声道）直接报错，不静默拉伸。"
    )

    def join(self, images, audio, images_acc=None, audio_acc=None):
        import torch

        # —— 画面：严格规格校验后拼接（与核心批处理节点的差别就在这一句）——
        if images_acc is None:
            out_images = images
        else:
            if images_acc.shape[1:] != images.shape[1:]:
                raise RuntimeError(
                    "画面规格不符，拒绝拼接：累计 %s vs 本段 %s。\n"
                    "    各段必须同一分辨率/帧率（拼在一起才能是一条）。\n"
                    "    核心自带的批处理节点会把小图放大凑数 —— 本节点不那么做。"
                    % (tuple(images_acc.shape[1:]), tuple(images.shape[1:])))
            out_images = torch.cat((images_acc, images), dim=0)

        # —— 音频：采样率/声道校验后沿时间拼 ——
        if audio_acc is None:
            out_audio = audio
            aud_note = "首段（无累计输入，原样透传）"
        else:
            sr_a, sr_b = int(audio_acc["sample_rate"]), int(audio["sample_rate"])
            if sr_a != sr_b:
                raise RuntimeError(
                    "音频采样率不符，拒绝拼接：累计 %d Hz vs 本段 %d Hz。"
                    % (sr_a, sr_b))
            w_a, w_b = audio_acc["waveform"], audio["waveform"]
            if w_a.shape[0] != w_b.shape[0] or w_a.shape[1] != w_b.shape[1]:
                raise RuntimeError(
                    "音频规格不符，拒绝拼接：累计 %s vs 本段 %s（批/声道必须一致）。"
                    % (tuple(w_a.shape[:2]), tuple(w_b.shape[:2])))
            out_audio = {"waveform": torch.cat((w_a, w_b), dim=-1),
                         "sample_rate": sr_a}
            aud_note = "累计 %d 样本 + 本段 %d 样本 = %d 样本 @ %d Hz" % (
                int(w_a.shape[-1]), int(w_b.shape[-1]),
                int(out_audio["waveform"].shape[-1]), sr_a)

        report = ("[H3 Relay] Join AV：画面 累计 %d 帧 + 本段 %d 帧 = %d 帧；音频 %s。"
                  % (int(images_acc.shape[0]) if images_acc is not None else 0,
                     int(images.shape[0]), int(out_images.shape[0]), aud_note))
        print(report, flush=True)
        return (out_images, out_audio, report)


class H3RelayFiniteRun:
    """⏩ H3 Relay · Finite Run——**一次提交**跑完 N 段（服务端展开，无头可用）。

    把「第 1..N 段」整条连跑展开成一张无环图，宿主一次执行完：不用前端按钮、不用
    逐段手改段号（脚本/API 与画布同一套实现），每段读上一段落盘文件续接（和手动
    连跑同一条链、同一批节点）。展开图里全部是已有节点，算法零重写。

    ⚠ 边界（诚实声明）：各段用**同一组参考图 / 同一张首帧图、同一个种子、同一段长**
    （与手动连跑同语义）；不含音频缝补丁与画质域后处理节点；合并输出是**一条驻留内存的张量**。
    沉降档（``settle_frames``）是**裁量**不是后处理 ⇒ 随段下传，换镜续接时脚本侧也能设。

    🔴 **续接的正确用法**（2026-10-22 补）：本节点组主打续接，所以
      ① 用**一镜到底**的片段（每段词里不要塞多个 `[Shot]` —— 多镜 = 段内硬切，
         判据 C3/C4/C6 的前提直接失效）；
      ② 用**参考图**锁人/景（`ref_image_1..3`），**不要**用首帧图；
      ③ 参考图必须**全片同一组** —— 每段换参考图 ⇒ 拷贝窗一过模型就按新图重新认人 ⇒ 硬切。
    """

    # V3 外壳据此开启 enable_expand（V1 出口无需声明，返回 expand 字典即生效）。
    V3_EXPAND = True

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "model": ("MODEL", {
                    "tooltip": "【接法】接你图里模型链的最后一级（放大器/LoRA 都在它之后接）。",
                }),
                "clip": ("CLIP", {
                    "tooltip": "【接法】接 CLIP 读取节点的输出，各段的词都靠它编码。",
                }),
                "vae": ("VAE", {
                    "tooltip": "【接法】接视频 VAE，画面编解码用它。",
                }),
                "audio_vae": ("VAE", {
                    "tooltip": "【接法】接音频 VAE，声音编解码用它。",
                }),
                "sampler": ("SAMPLER", {
                    "tooltip": "【接法】接采样器选择节点（如 euler），各段共用。",
                }),
                "sigmas": ("SIGMAS", {
                    "tooltip": "【接法】接噪声表节点（步数与调度），各段共用同一个。",
                }),
                "prompt": ("STRING", {
                    "default": "", "multiline": True,
                    "tooltip": "【每段共用的一份词】只跑一种内容时填这里。\n想每段不同词，用下面的 prompts 格。",
                }),
                "segments": ("INT", {
                    "default": 2, "min": 1, "max": 64, "step": 1,
                    "tooltip": "【跑几段】一次提交跑完几段。\n填 3 = 从第 1 段跑到第 3 段。要配合 prompts 的词块数。",
                }),
                "seed": ("INT", {
                    "default": 0, "min": 0, "max": 0xFFFFFFFFFFFFFFFF, "step": 1,
                    # 🔴 显式声明 seed 控件：ComfyUI 前端会给名为 `seed` 的 INT 输入**额外插一个**
                    #   `control_after_generate` widget（fixed / increment / decrement / randomize），
                    #   它**不在后端 schema 里** ⇒ 保存的工作流 `widgets_values` 必须比 schema 的
                    #   widget 数多一个，否则其后每个值都错位一格（2026-10-23 实测：随包示例因此
                    #   提交被拒 HTTP 400，`from_segment` 收到 'match'）。声明它 = 把这个事实写进
                    #   schema，示例生成器据此插入对应值（见 examples/make_finite_run_workflow.py）。
                    "control_after_generate": True,
                    "tooltip": "【随机种子】各段用同一个种子。\n0 是常用的固定值；想各段不同请分次提交。",
                }),
                "run_id": ("STRING", {
                    "default": "relay",
                    "tooltip": "【片子名】这部片子的名字，比如 myfilm、ep01。\n各段中间文件与读上一段都靠它定位，多段必须填。",
                }),
                "width": ("INT", {
                    "default": 1344, "min": 64, "max": 8192, "step": 16,
                    "tooltip": "【画面宽】16 的倍数，和出词节点填同一个数。",
                }),
                "height": ("INT", {
                    "default": 768, "min": 64, "max": 8192, "step": 16,
                    "tooltip": "【画面高】16 的倍数，和出词节点填同一个数。",
                }),
                "length": ("INT", {
                    "default": 124, "min": 5, "max": 3600, "step": 17,
                    "tooltip": "【每段帧数】24 帧每秒，124 帧约 5 秒。所有段一样长（与手动连跑一致）。\n只认 5+17k 档：5/22/39/56/73/90/107/124/158/192/226… 非档值直接报错，不静默吸附。",
                }),
                "context_frames": ("INT", {
                    "default": 22, "min": 5, "max": 124, "step": 17,
                    "tooltip": "【复制窗】从上一段尾部钉多少帧进本段。\n只认 5/22/39/56/73/90/107/124，窗越长缝越稳。",
                }),
                "mask_mode": (list(CORE.MASK_MODES), {
                    "default": "hard",
                    "tooltip": "【复制档位】hard = 复制区每步钉回不动（推荐，默认）。\ntaper / ramp / blend / window 是实验档，只在与旧结果对照时用。",
                }),
                "pin_audio": ("BOOLEAN", {
                    "default": True,
                    "tooltip": "【复制音频】开（默认）= 上一段尾部音频一起钉进本段。\n关 = 音频各自生成，缝处可能不连贯。",
                }),
            },
            "optional": {
                # ⚠ 链接型输入一律放 optional：必填链接 = 强制接线，
                #   会把「纯文生成视频」「不换词」「不用声音参考」这些合法用法堵死。
                "image": ("IMAGE", {
                    "tooltip": "【首帧图】第 1 段的第一帧，各段用同一张。不接 = 纯文生成视频。\n"
                              "与下面三个参考图槽不能同时接：官方两个节点各缺对方那一槽。\n"
                              "续接要人物与场景稳定请用参考图，别用首帧图。",
                }),
                # 🔴 参考图路（官方 ReferenceToVideo）：每段接**同一批图** —— 逐段换图会在
                # 钉住区过后重新认人 ⇒ 硬切。3 槽 = 「人物 2 + 场景 1」，要更多用画布模式。
                "ref_image_1": ("IMAGE", {
                    "tooltip": "【参考图 1】外观参考，对应词里的 <Picture 1>。人物或场景，全片共用。\n"
                              "接了任意一张就走官方参考图生视频，与首帧图不能同时接。",
                }),
                "ref_image_2": ("IMAGE", {
                    "tooltip": "【参考图 2】第二张外观参考，常见用法是第二个人的身份与服装。\n不接 = 只用一张。",
                }),
                "ref_image_3": ("IMAGE", {
                    "tooltip": "【参考图 3】第三张外观参考，常见用法是场景空间与材质。\n不接 = 只用前两张。",
                }),
                "prompts": ("STRING", {
                    "default": "", "multiline": True,
                    "tooltip": "【每段各自的词】用单独一行三个以上短横线分块，第 1 块喂第 1 段。\n块数不够会直接报错，不会悄悄少跑。",
                }),
                "voice_anchor": ("LATENT", {
                    "tooltip": "【声音参考】本段说话人的音频锚，各段共用同一个。\n不接 = 用上一段自己的音频尾。",
                }),
                "note": ("STRING", {
                    "default": "",
                    "tooltip": "【备注】写进每段中间文件的备注，比如「22 帧窗 v2」。",
                }),
                # ⚠ 新 widget 追加在**最后一个**（widgets_values 按位置对应，插中间会整体错位）。
                #   它是**裁量**不是后处理 —— 画布链的「裁重叠」一直能设，脚本/API 侧漏了它
                #   就等于「换镜续接时拿不到那个旋钮」（铁律一：UI 与 API 同一套能力）。
                "settle_frames": ("INT", {
                    "default": 0, "min": -1, "max": CORE.SETTLE_CAP, "step": 1,
                    "tooltip": "【按镜位选】同机位续接保持 0；换机位/换镜填 -1。\n"
                              "沉降区 = 复制区之后模型复现上一段的那几帧。\n"
                              "· 同机位：裁它们正是缝处跳帧的源头（实测 0 帧 0.020、16 帧 0.055）⇒ 保持 0；\n"
                              "· 换机位：-1 自动把硬切退回段边界（实测三档全过）。",
                }),
                # ⚠ 继续追加在**最后一个**（widgets_values 按位置对应，插中间会整体错位）。
                #   from_segment 把段号整体平移：0 = 从第 1 段起跑；N = 从第 N+1 段起跑，
                #   首段的桥自取盘上第 N 段续接 ⇒ 与画布 Chain 填 stage_index 同义。
                "from_segment": ("INT", {
                    "default": 0, "min": 0, "max": 9999, "step": 1,
                    "tooltip": "【从第几段之后起跑】0 = 从第 1 段起跑（默认）。\n"
                              "填 N = 接第 N 段往外跑；视频起段后填 1。\n"
                              "⚠ 盘上必须有该段的边车文件，\n"
                              "没有会在开跑前就报错，不等模型上卡。\n"
                              "接续已有段时别接首帧图 image。",
                }),
                # ⚠ 以下 4 个新 widget 一律追加在**末尾**（widgets_values 按位置对应，插中间整体错位）。
                "ref_image_size": (["match", "max"], {
                    "default": "match",
                    "tooltip": "【参考图尺寸】match（默认）= 按画面尺寸缩每张参考图（只缩不放，保持比例）。\n"
                              "max = 保留更大尺寸。只在接了参考图时生效。",
                }),
                "refs_mode": (list(CORE.REFS_MODES), {
                    "default": "lock",
                    "tooltip": "【定妆照用法】lock（默认）= 全片同一组，一镜到底的唯一无冲突解。\n"
                              "first_only = 只喂本批第 1 段，后续段靠外观参考跟随镜头。\n"
                              "没有「每段换一组」这一档：那是换镜，不是续接。",
                }),
                "ref_anchor_mode": (list(CORE.ANCHOR_MODES), {
                    "default": "off",
                    "tooltip": "【外观参考指向】off 不用；first 指第 1 段；roll 指上一段。\n"
                              "first 身份最稳，但进新环境会过时。\n"
                              "roll 跟随镜头，但长片会漂。\n"
                              "参考与定妆照同层，不一致会互相抢。\n"
                              "建议配对：first 配 lock，roll 配 first_only。",
                }),
                "ref_anchor_frames": ("INT", {
                    "default": 5, "min": 1, "max": 64, "step": 1,
                    "tooltip": "【外观参考帧数】从参考段开头取几帧当基准，默认 5。\n取多了会把参考段的动作也拖进来。\n只在 ref_anchor_mode 不是 off 时生效。",
                }),
            },
        }

    RETURN_TYPES = ("LATENT", "IMAGE", "AUDIO", "STRING")
    RETURN_NAMES = ("last_latent", "images", "audio", "report")
    FUNCTION = "run"
    CATEGORY = CATEGORY
    DESCRIPTION = (
        "一次提交跑完 N 段：服务端展开整条连跑图（无需前端按钮、无头可用）。\n"
        "每段链：ImageToVideo/ReferenceToVideo → CopyBridge → 采样 → LatentSave → 解码 → TrimAV → JoinAV"
        "（上一段由桥自取，不手接 Latent Load）。\n"
        "续接推荐接参考图（全片同一组）锁人/景，并用一镜到底的片段。"
    )

    # ⚠️ 参数表**必须**与 v3/nodes_v3.py 里 H3RelayFiniteRun.validate_inputs 同名同集
    #    （宿主用 getfullargspec 读名字；两套不一致 ⇒ 一侧静默失效）。机检：
    #    tests/test_v3_schema.py 的签名逐字比对。
    #    这里只列 4 项，且每一项都**不放松**宿主自带校验：
    #      · prompt / prompts / run_id 是 STRING —— 宿主对 STRING 没有值检查；
    #      · segments 宿主是 min 1 / max 64，本包校验逐字相同（FINITE_MAX_SEGMENTS）。
    #    被声明的入参会**跳过**宿主自带的 min/max/枚举检查 ⇒ width / height / length /
    #    context_frames / mask_mode 一律不在此列（它们的边界继续由宿主把关）。
    @classmethod
    def VALIDATE_INPUTS(cls, prompt=None, prompts=None, segments=None, run_id=None):
        """入队前的尽力校验：能在 ``/prompt`` 阶段说清的错误不拖到执行期。

        执行期才报错的话，上游加载器已经先跑过了（冷启动 = 一次完整的模型上卡）。
        可见性规则见 ``CORE.validate_finite_inputs_early``：看不到的（链接型入参在
        校验期是 ``None``）一律不猜。
        """
        msg = CORE.validate_finite_inputs_early(
            prompt=prompt, prompts=prompts, segments=segments, run_id=run_id)
        return True if msg is None else msg

    @_node_errors("run_id", "segments")
    def run(self, model, clip, vae, audio_vae, sampler, sigmas, image=None,
            prompt="", prompts="", segments=2, seed=0, run_id="relay",
            width=1344, height=768, length=124, context_frames=22,
            mask_mode="hard", pin_audio=True, voice_anchor=None, note="",
            settle_frames=0, from_segment=0,
            ref_image_1=None, ref_image_2=None, ref_image_3=None,
            ref_image_size="match", refs_mode="lock",
            ref_anchor_mode="off", ref_anchor_frames=5):
        """建图并返回展开图（本方法**不采样**，只编排；执行由宿主对展开图进行）。

        可选链接槽（image / voice_anchor）不接线时宿主整个键都不传 ⇒ 必须给默认值。
        校验三道门：入队前 ``VALIDATE_INPUTS``（只判字面值可见的项）→ 起始段**文件**预检
        （``from_segment>0`` 时首段要读的那份必须在盘上，否则等模型上卡后才炸）→
        本函数里 ``build_finite_graph``（唯一真相源）。哪一项归哪道门见 docs/07-chain.md。
        """
        blocks, pnote = CORE.resolve_finite_prompts(prompt, prompts, segments)
        # from_segment>0 ⇒ 首段桥要自取盘上第 from_segment-1 段。文件不在就让它在
        # **这里**炸（上游加载器还没跑），而不是等采样器接到空 latent 才炸。
        _fs = int(from_segment or 0)
        if _fs > 0:
            # 🔴 2026-10-20：`image` 与 `from_segment>0` 是**互斥**的 —— 从第 N 段起跑意味着
            #   首段的起点由盘上第 N 段的尾巴决定，首帧图没有位置可用。原来只在 tooltip 里写，
            #   接了就是静默错配 ⇒ 改成硬错误（铁律七：硬错误必须 raise，绝不静默降级）。
            if image is not None:
                raise RuntimeError(
                    "from_segment=%d 与首帧图 image **不能同时用**：\n"
                    "    · from_segment>0 = 接着盘上第 %d 段往外跑，起点来自那一段的尾巴；\n"
                    "    · image = 第 1 段的第一帧，只有 from_segment=0 时才用得上。\n"
                    "    ⇒ 二选一：要么把 from_segment 改回 0，要么把 image 断开。" % (_fs, _fs - 1))
            _need = _stage_path(run_id, _fs - 1)
            if not os.path.isfile(_need):
                raise RuntimeError(
                    "from_segment=%d ⇒ 首段要读第 %d 段当上一段，但盘上没有它的边车：%s\n"
                    "    ⇒ 三种原因：① run_id 与起段时填的**不是一字不差**；② 那段还没跑；"
                    "③ 你其实想从第 1 段起跑（把 from_segment 改回 0）。\n"
                    "    已存在的段文件列在 output/relay_kit/%s/ 下（stage_NNNNN.safetensors）。"
                    % (_fs, _fs - 1, _need, str(run_id)))
        # 前缀由宿主在执行本节点前设好（以本节点 id 为根）⇒ 唯一
        from comfy_execution.graph_utils import GraphBuilder
        # 参考图按 1/2/3 顺序剔除空槽。⚠ 顺序即语义：`ref_image_1` 必须对应 `<Picture 1>`，
        # 错位 = 静默换人（官方按**已接线顺序** 1-based 发标签，见 text_encoders/minimax.py）。
        _refs = [r for r in (ref_image_1, ref_image_2, ref_image_3) if r is not None]
        graph, outputs, report = CORE.build_finite_graph(
            prefix=GraphBuilder.alloc_prefix(), segments=segments, blocks=blocks,
            seed=seed, run_id=run_id, note=note, width=width, height=height,
            length=length, context_frames=context_frames, mask_mode=mask_mode,
            settle_frames=settle_frames, from_segment=from_segment, pin_audio=pin_audio,
            model=model, clip=clip, vae=vae, audio_vae=audio_vae,
            image=image, sampler=sampler, sigmas=sigmas, voice_anchor=voice_anchor,
            ref_images=_refs, ref_image_size=ref_image_size, refs_mode=refs_mode,
            ref_anchor_mode=ref_anchor_mode, ref_anchor_frames=ref_anchor_frames)
        outputs[3] = report + "\n" + pnote
        # V1 出口：expand 图 + result（输出可以是 [展开图节点 id, 输出位] 链接）
        return {"expand": graph, "result": tuple(outputs)}


NODE_CLASS_MAPPINGS = {
    "H3RelayLatentSave": H3RelayLatentSave,
    "H3RelayLatentLoad": H3RelayLatentLoad,
    "H3RelayCopyBridge": H3RelayCopyBridge,
    "H3RelayTrimAV": H3RelayTrimAV,
    "H3RelayPost": H3RelayPost,
    "H3RelayAudioSeam": H3RelayAudioSeam,
    "H3RelayChain": H3RelayChain,
    "H3RelayLatentUpscale": H3RelayLatentUpscale,
    "H3RelaySeedFromVideo": H3RelaySeedFromVideo,
    "H3RelayJoinAV": H3RelayJoinAV,
    "H3RelayFiniteRun": H3RelayFiniteRun,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "H3RelayLatentSave": "🔗 H3 Relay · Latent Save",
    "H3RelayLatentLoad": "🔗 H3 Relay · Latent Load",
    "H3RelayCopyBridge": "🔗 H3 Relay · Copy Bridge",
    "H3RelayTrimAV": "🔗 H3 Relay · Trim AV",
    "H3RelayPost": "🔗 H3 Relay · Post",
    "H3RelayAudioSeam": "🔗 H3 Relay · Audio Seam",
    "H3RelayChain": "🔗 H3 Relay · Chain",
    "H3RelayLatentUpscale": "🔍 H3 Relay · Latent Upscale",
    "H3RelaySeedFromVideo": "🔗 H3 Relay · Seed from Video",
    "H3RelayJoinAV": "🧩 H3 Relay · Join AV",
    "H3RelayFiniteRun": "⏩ H3 Relay · Finite Run",
}
