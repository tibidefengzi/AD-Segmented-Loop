"""纯素材摘要与运行上下文的缓存边界。

segment_cache_key只对段号、素材、提示词及尺寸等纯数据生成稳定摘要，
不包含执行任务的token/epoch，不能用它缓存整个AidenH3Segment输出。
select还负责预约任务、绑定本次执行并产生上下文，segment_changed因此返回nan，
要求ComfyUI每次执行该节点。素材解码仍通过assets内部缓存复用。
依赖上下文节点的条件/VAE等下游编码可能重新执行，不承诺仅改种子就全部命中。
"""

from __future__ import annotations

import hashlib
import json

# 这些字段每次排队/每次抽卡都可能变，且不影响"当前段"的数据内容。
_VOLATILE_KEYS = frozenset({
    "session_id", "run_token", "token", "prompt_id",
    "request_id", "created_at", "timestamp", "client_id",
})

# 时间线里会影响分段结果的字段；未列出的字段不参与缓存键（避免误把易变字段算进来）。
_TIMELINE_FIELDS = (
    "split_points", "generation_clips", "segment_recipe", "segmentation", "duration",
    "sources", "images", "video_tracks", "audio_tracks",
    "muted_tracks", "disabled_tracks", "max_frames",
)


def _normalize(value, depth=0):
    """递归规范化：排序字典键、剔除易变字段、浮点定长，保证跨进程稳定。"""
    if depth > 12:
        return "<deep>"
    if isinstance(value, dict):
        items = []
        for key in sorted(value, key=lambda item: str(item)):
            name = str(key)
            if name in _VOLATILE_KEYS:
                continue
            items.append([name, _normalize(value[key], depth + 1)])
        return items
    if isinstance(value, (list, tuple)):
        return [_normalize(item, depth + 1) for item in value]
    if isinstance(value, float):
        return round(float(value), 9)
    if isinstance(value, (str, int, bool)) or value is None:
        return value
    return str(value)


def digest(value) -> str:
    """规范化后取 sha256（十六进制），供 IS_CHANGED 直接返回。"""
    payload = json.dumps(_normalize(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def segment_cache_key(queue=None, aspect_ratio=None, megapixels=None, multiple=None, inputs=None) -> str:
    """AidenH3Segment 的稳定缓存键。

    ``inputs`` 为节点其余输入（含中文名，如 ``段索引`` / ``正向提示词``）。
    任何会影响当前段输出的变化都会换 key；同一段抽卡时 key 不变。
    """
    queue = queue if isinstance(queue, dict) else {}
    timeline = queue.get("timeline")
    timeline = timeline if isinstance(timeline, dict) else {}
    names = dict(inputs or {})
    payload = {
        "schema": 1,
        "index": names.get("段索引"),
        "aspect_ratio": aspect_ratio,
        "megapixels": megapixels,
        "multiple": multiple,
        "positive": names.get("正向提示词"),
        "prefix": names.get("统一提示词前缀"),
        "negative": names.get("反向提示词"),
        "ref_counts": names.get("ref_counts"),
        "entries": queue.get("entries"),
        "references": queue.get("references"),
        "timeline": {key: timeline.get(key) for key in _TIMELINE_FIELDS},
    }
    return digest(payload)


def segment_changed(queue=None, aspect_ratio=None, megapixels=None, multiple=None, inputs=None):
    """每次建立新运行上下文，禁止缓存整节点的token/epoch和任务预约副作用。"""
    return float("nan")
