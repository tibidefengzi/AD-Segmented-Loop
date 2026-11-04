# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 ComfyUI-H3-Latent-Relay contributors
# 第三方出处与许可见 THIRD-PARTY-NOTICES.md
"""relay_core.prompt —— 词分发（连跑时「第 k 段喂第 k 块词」）。

本模块由 `relay_core.py` 拆分而来（2026-10-07）；代码**逐字搬运**，未改任何算式。
对外仍由 `relay_core/__init__.py` 全量再导出 ⇒ 旧调用点（`relay_core.xxx`）不变。
"""

from __future__ import annotations

from typing import Any, List, Tuple

# ============================================================================
# 词分发（0.6.15）—— 连跑时「第 k 段喂第 k 块词」
# ============================================================================
# 🔴 这里是词分发的**唯一权威实现**（铁律一：UI 与 API 必须同一套实现）：
#    · 节点侧 —— `H3RelayChain.run()` 直接调它 ⇒ **API 提交图 JSON 也走这一段**；
#    · 工具侧 —— `tools/check_ui_workflow.py` 直接 import（不再自带第二份口径）；
#    · 前端 —— `web/relay_kit_prompt.js` 是画布专用副本（JS 不能 import Python），
#      口径由这里定义，并由 `tests/test_relay_core.py` 的**跨语言一致性断言**锁住。
# 纯字符串运算：不碰 torch / 不 import 任何 ComfyUI 模块，可离线单测。

PROMPT_SEPARATOR_MIN_DASHES = 3


def _lines_like_js(text: str) -> List[str]:
    """断行口径与前端 `text.split(/\\r?\\n/)` 逐样本一致。

    JS 只在 `\\n`（以及它前面紧邻的那**一个** `\\r`）处断行；Python 的 `splitlines()`
    还会在 `\\v \\f \\x1c \\x1d \\x1e \\x85 \\u2028 \\u2029` 处断 ⇒ 两者**不等价**。
    这里显式复刻 JS 的口径，免得跨语言一致性断言在边界字符上假红/假绿。
    """
    return [ln[:-1] if ln.endswith("\r") else ln for ln in text.split("\n")]


def split_prompt_blocks(text: Any) -> List[str]:
    """把多段词按「**单独一行**、≥3 个短横线」分块。

    返回**已去掉空块**的词列表（块内保留原始换行与缩进，只去整体首尾空白）；
    空文本 / 非字符串 ⇒ `[]`。
    与前端 `splitPromptBlocks()` 逐样本一致（由 test_relay_core 的一致性断言守）。
    """
    if not isinstance(text, str) or not text.strip():
        return []
    blocks: List[str] = []
    cur: List[str] = []
    for line in _lines_like_js(text):
        s = line.strip()
        if len(s) >= PROMPT_SEPARATOR_MIN_DASHES and set(s) == {"-"}:
            blocks.append("\n".join(cur))
            cur = []
        else:
            cur.append(line)
    blocks.append("\n".join(cur))
    return [b.strip() for b in blocks if b.strip()]


def pick_prompt_block(text: Any, stage: int) -> Tuple[str, int]:
    """取第 `stage` 块词（**0 起算**）。返回 `(block, total)`。

    `text` 为空（或没有词）⇒ 返回 `("", 0)`：调用方按"不换词"的老行为放行。
    越界 ⇒ **直接 raise**，绝不静默复用上一块 —— 那正是"以为换了词、其实没换"的坑。
    """
    blocks = split_prompt_blocks(text)
    if not blocks:
        return "", 0
    k = int(stage)
    if k < 0:
        raise ValueError("段号不能为负（得到 %d）。" % k)
    if k >= len(blocks):
        if len(blocks) == 1:
            # 🔴 单块词 + stage>0 是**最高频的误接法**（2026-10-12 追痕实盘：debug ~30 分钟）：
            #   母版把「prompt 节点 → Chain.prompts」接成连线后，一个 prompt 节点只喂一条词，
            #   而连跑按「第 k 段喂第 k 块」取 ⇒ 第 2 段起必炸。这里把**两种正确接法**直接写进报错。
            raise ValueError(
                "本段是第 %d 段，但 `prompts` 只有 1 块词 —— 连跑按「第 k 段喂第 k 块」取词，"
                "第 2 段起就没词可喂。\n"
                "    两种正确接法（任选其一）：\n"
                "      ① 母版：在 `prompts` 里用 `---` 把词分成 %d 块（第 k 块喂第 k 段）；\n"
                "      ② 脚本/API：每次提交自带**全量**词块 —— 只有一块词时它只对应第 1 段（stage=0）。\n"
                "    ⚠ 本包**不会**把唯一那块词复用给后面的段（那就是「以为换了词、其实没换」）。"
                % (k + 1, k + 1))
        raise ValueError(
            "prompts 只有 %d 块词，跑不到第 %d 段 ⇒ 补齐第 %d 块，"
            "或把 segments 改成 %d 或更小。" % (len(blocks), k + 1, k + 1, len(blocks))
        )
    return blocks[k], len(blocks)
