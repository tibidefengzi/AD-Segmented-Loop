"""MiniMax H3's timing and canvas contract."""
import math
import re

FPS = 24
MAX_FRAMES = 3600
REVISION = 5  # 数据根、素材库与帧缓存格式变化参与生成身份
MULTIPLE = 32


def relay_reanchor_interval(raw):
    match = re.match(r"^\s*(\d+)", str(raw or ""))
    interval = int(match.group(1)) if match else 0
    return interval if interval in (0, 2, 3, 5, 7, 10) else 0


def relay_reanchors(interval, index):
    return bool(interval and index > 0 and (index + 1) % interval == 0)


def relay_parent_stage(index, reanchor):
    return (0 if reanchor else index - 1) if index > 0 else None


def frame_index(seconds):
    """秒 → 帧号；统一 half-up 四舍五入，与前端 Math.round 一致。"""
    return math.floor(float(seconds) * FPS + 0.5)


def snap_seconds(seconds):
    """把秒数吸到整帧（half-up），避免 Python round 的银行家舍入与前端不一致。"""
    return frame_index(seconds) / FPS


def validate_fps(value):
    if float(value) != FPS:
        raise ValueError("MiniMax H3 固定使用 24 FPS")


def snap_dim(value, multiple=MULTIPLE):
    """Largest positive multiple of `multiple` that does not exceed value."""
    value = int(value)
    snapped = value - (value % multiple)
    return snapped if snapped >= multiple else 0


def snap_resolution(width, height, multiple=MULTIPLE):
    """Floor each side to a valid H3 canvas. Returns (0,0) if either side cannot."""
    return snap_dim(width, multiple), snap_dim(height, multiple)


def is_valid_resolution(width, height, multiple=MULTIPLE):
    try:
        return all(int(v) == v and int(v) >= multiple and int(v) % multiple == 0 for v in (width, height))
    except (TypeError, ValueError):
        return False


def validate_resolution(width, height):
    w, h = int(width), int(height)
    if not is_valid_resolution(width, height):
        sw, sh = snap_resolution(w, h)
        hint = f"；可对齐为 {sw}×{sh}" if sw and sh else ""
        raise ValueError(f"MiniMax H3 的宽、高都必须是正的 {MULTIPLE} 倍数，当前为 {w}×{h}{hint}")


def target_frames(seconds):
    if not math.isfinite(seconds) or seconds <= 0:
        raise ValueError("音频段时长必须是正数")
    count = math.floor(seconds * FPS + .5)
    if count < 1:
        raise ValueError("音频段短于半帧，请合并或调整切分点")
    return count


def generation_frames(target, overlap=0, guard=0):
    needed = int(target) + int(overlap) + int(guard)
    result = max(5,5 + 17 * math.ceil((needed-5)/17))
    if result > MAX_FRAMES:
        raise ValueError("当前音频段超过 H3 单次生成上限，请先切分音频")
    return result


def validate_graph(prompt, node_ids):
    for key in node_ids:
        node=prompt[key]
        if not node["class_type"].startswith("MiniMaxH3"):
            continue
        values=node.get("inputs",{})
        if all(isinstance(values.get(name),(int,float)) for name in ("width","height")):
            validate_resolution(values["width"],values["height"])

ASPECT_CHOICES = ("1:1 (Square)", "16:9 (Widescreen)", "9:16 (Portrait)", "4:3 (Standard)", "3:4 (Portrait)", "3:2 (Photo)", "2:3 (Portrait)", "21:9 (Cinematic)",
                  # 旧名称兼容（旧工作流仍可运行）
                  "16:9 (Landscape)", "4:3", "3:4", "3:2", "2:3")
DEFAULT_ASPECT = "16:9 (Widescreen)"
MEGAPIXEL_CHOICES = ("0.3", "0.5", "0.8", "1.0", "1.2", "1.6", "2.0", "3.0", "4.0", "6.0", "8.0", "12.0", "16.0")
DEFAULT_MEGAPIXELS = "0.5"


def resolve_canvas(aspect_ratio=None, megapixels=None, multiple=MULTIPLE):
    ratios = {"1:1 (Square)": (1, 1), "16:9 (Widescreen)": (16, 9), "9:16 (Portrait)": (9, 16),
              "4:3 (Standard)": (4, 3), "3:4 (Portrait)": (3, 4), "3:2 (Photo)": (3, 2),
              "2:3 (Portrait)": (2, 3), "21:9 (Cinematic)": (21, 9),
              # 旧名称兼容
              "16:9 (Landscape)": (16, 9), "4:3": (4, 3), "3:4": (3, 4), "3:2": (3, 2), "2:3": (2, 3)}
    raw_aspect = str(aspect_ratio or DEFAULT_ASPECT)
    if raw_aspect in ratios:
        a, b = ratios[raw_aspect]
    else:
        import re as _re
        match = _re.search(r"(\d+)\s*[:：]\s*(\d+)", raw_aspect)
        a, b = (int(match.group(1)), int(match.group(2))) if match else (16, 9)
    try:
        mp = float(megapixels if megapixels is not None else DEFAULT_MEGAPIXELS)
    except (TypeError, ValueError):
        mp = 1.0
    mp = max(0.05, min(64.0, mp))
    ratio = a / b
    step = max(1, int(multiple or MULTIPLE))
    # 与官方“分辨率选择器”逐位一致：mp 按 1024*1024 换算，宽/高各自按比例缩放后取最近倍数（round 半偶）
    scale = math.sqrt((mp * 1024 * 1024) / (a * b))
    snapped_width = max(step, int(round((a * scale) / step)) * step)
    snapped_height = max(step, int(round((b * scale) / step)) * step)
    return snapped_width, snapped_height
