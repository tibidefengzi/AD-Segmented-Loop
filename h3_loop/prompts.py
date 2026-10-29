"""独立的行首 # 多行段落解析；序号只由生成游标决定。"""
import re


def select_prompt(text, index, prefix="", suffix=""):
    if not isinstance(text, str):
        raise ValueError("分段提示词必须是文字")
    if isinstance(index, bool) or not isinstance(index, int) or index < 0:
        raise ValueError("生成段号必须是从 0 开始的整数")
    # 只把行首（允许缩进）的 # 当作分隔符；句中的 # 保留。
    paragraphs = [part.strip() for part in re.split(r"(?m)^[ \t]*#[ \t]*", text) if part.strip()]
    if not paragraphs:
        raise ValueError("分段提示词为空，请至少填写一段内容")
    return "\n".join(part for part in (prefix, paragraphs[index % len(paragraphs)], suffix) if part)
