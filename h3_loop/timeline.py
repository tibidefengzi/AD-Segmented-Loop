"""剪映式素材清单库的数据模型：轨道、片段、段线、图片池与段推导。

纯 Python 模块（不依赖 torch / ComfyUI），可独立单测；
片段音频的实际解码混音在 audio.py 的 read_timeline_audio 中完成。

状态结构（version 3）：
{
  "version": 3,
  "duration": float | None,          # None = 自动取最远片段末端
  "sources": [ {"id","kind":"audio|video|image","origin","file","name","duration":float|None} ],
  "video_tracks": [ [clip]×0～3，校验时补齐到3 ],  # clip = {"id","source","tl_start","src_in","src_out"}
  "audio_tracks": [ [clip]×0～3，校验时补齐到3 ],
  "images": [ {"id","source","segment"} ],  # segment: 0 仅参考口；-1 全局参考；N>=1 第 N 段
  "split_points": [秒, ...]          # 段切分线（不含 0 与总时长）
}
"""
from __future__ import annotations

import json
import math

from .spec import FPS, REVISION, snap_seconds, target_frames
from .storage import digest

TIMELINE_VERSION = 3
TRACK_COUNT = 3
MAX_IMAGE_PORTS = 9
MAX_VIDEO_PORTS = 3
MAX_AUDIO_PORTS = 3
HALF_FRAME = 0.5 / FPS
KINDS = {"audio", "video", "image"}

# 新节点默认：无素材、2×124 帧 @24fps（总 248 帧 ≈ 10.333s）
_DEFAULT_SEG_FRAMES = 124
_DEFAULT_SEG_COUNT = 2
EMPTY_TIMELINE = json.dumps({
    "version": TIMELINE_VERSION,
    "segmentation": "clips",
    "duration": _DEFAULT_SEG_FRAMES * _DEFAULT_SEG_COUNT / FPS,
    "sources": [],
    "video_tracks": [[], [], []],
    "audio_tracks": [[], [], []],
    "images": [],
    "split_points": [_DEFAULT_SEG_FRAMES / FPS],
    "segment_recipe": {"frames": _DEFAULT_SEG_FRAMES, "count": _DEFAULT_SEG_COUNT, "unit": "f"},
}, separators=(",", ":"), ensure_ascii=False)


def timeline_config(raw):
    import json
    try:
        value = json.loads(raw or EMPTY_TIMELINE)
    except (json.JSONDecodeError, TypeError) as error:
        raise ValueError("素材清单格式无效，无法解析") from error
    if not isinstance(value, dict):
        raise ValueError("素材清单必须是对象")
    return value


def load_timeline(raw):
    config = timeline_config(raw)
    version = config.get("version")
    if version == TIMELINE_VERSION:
        return validate_timeline(config)
    if version in (1, 2) or "tracks" in config or "entries" in config:
        return migrate_legacy(config)
    raise ValueError("未知的素材清单版本，请使用最新版插件重新打开素材清单库")


def _finite(value, positive=True):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return False
    return value > 0 if positive else value >= 0


def clip_span(clip):
    return float(clip["tl_start"]), float(clip["tl_start"]) + (float(clip["src_out"]) - float(clip["src_in"]))


def path_sources(raw_path):
    """「素材路径」输入框的内容：直接把该文件/文件夹作为唯一素材来源。"""
    import hashlib
    import shutil
    from pathlib import Path
    from .storage import ROOT
    from .assets import AUDIO_EXTS, IMAGE_EXTS, VIDEO_EXTS, probe_duration
    raw = str(raw_path or "").strip().strip('"')
    if not raw:
        return []
    path = Path(raw).expanduser()
    if not path.exists():
        return []
    supported = AUDIO_EXTS | VIDEO_EXTS | IMAGE_EXTS
    files = sorted([p for p in path.iterdir() if p.is_file() and p.suffix.lower() in supported],
                   key=lambda p: p.name) if path.is_dir() else ([path] if path.suffix.lower() in supported else [])
    if not files:
        return []
    from . import layout
    media = layout.input_library_dir()
    if media is None:
        import folder_paths
        media = Path(folder_paths.get_input_directory()) / layout.INPUT_LIBRARY
    media.mkdir(parents=True, exist_ok=True)
    sources = []
    for item in files:
        kind = "audio" if item.suffix.lower() in AUDIO_EXTS else ("video" if item.suffix.lower() in VIDEO_EXTS else "image")
        key = hashlib.sha1(f"{item.resolve()}|{item.stat().st_mtime_ns}".encode("utf-8")).hexdigest()[:16]
        target = media / (key + item.suffix.lower())
        kind_dir = layout.KIND_DIRS[kind]
        kind_target = media / kind_dir
        kind_target.mkdir(parents=True, exist_ok=True)
        final = kind_target / target.name
        if not final.exists():
            from .cleanup import copy_import
            copy_import(target if target.exists() else item, final)
        sources.append({"id": key, "kind": kind, "origin": "input",
                        "file": f"{layout.INPUT_LIBRARY}/{kind_dir}/{final.name}",
                        "name": item.name, "priority": True,
                        "duration": None if kind == "image" else probe_duration(final)})
    return sources


def repair_source_instances(config):
    """同一文件可有多个使用实例；修复旧去重导入生成的重复ID，不复制文件。"""
    raw_sources = config.get("sources") or []
    raw_images = config.get("images") or []
    groups = {}
    for source in raw_sources:
        if isinstance(source,dict) and isinstance(source.get("id"),str) and source["id"]:
            groups.setdefault(source["id"],[]).append(source)
    safe = {sid for sid,items in groups.items() if len({(s.get("kind"),s.get("origin"),s.get("file")) for s in items}) == 1}
    reserved = set(groups)
    def fresh(base):
        number = 2
        while f"{base}__use_{number}" in reserved: number += 1
        value = f"{base}__use_{number}"
        reserved.add(value)
        return value
    instances = {}
    sources = []
    for source in raw_sources:
        if not isinstance(source,dict) or source.get("id") not in safe:
            sources.append(source); continue
        sid = source["id"]
        ids = instances.setdefault(sid,[])
        instance = dict(source)
        if ids: instance["id"] = fresh(sid)
        ids.append(instance["id"])
        sources.append(instance)
    image_uses = {}
    image_ids = {item.get("id") for item in raw_images if isinstance(item,dict) and item.get("id")}
    images = []
    seen_images = set()
    for raw in raw_images:
        if not isinstance(raw,dict): images.append(raw); continue
        image = dict(raw)
        sid = image.get("source")
        if sid in safe and groups[sid][0].get("kind") == "image":
            offset = image_uses.get(sid,0)
            ids = instances[sid]
            if offset >= len(ids):
                source = {**groups[sid][0],"id":fresh(sid)}
                sources.append(source); ids.append(source["id"])
            image["source"] = ids[offset]
            image_uses[sid] = offset+1
            iid = str(image.get("id") or image["source"])
            if iid in seen_images:
                number = 2
                while f"{iid}__ref_{number}" in image_ids: number += 1
                iid = f"{iid}__ref_{number}"
                image_ids.add(iid)
            image["id"] = iid
        seen_images.add(image.get("id"))
        images.append(image)
    return {**config,"sources":sources,"images":images}


def validate_timeline(config):
    # 素材路径优先：路径里有可用素材时，完全接管本次输出
    override = path_sources(config.get("path_source"))
    if override:
        images = [{"id": f"path-{source['id']}", "source": source["id"], "segment": -1}
                  for source in override if source["kind"] == "image"]
        config = {**config, "sources": override, "video_tracks": [[], [], []],
                  "audio_tracks": [[], [], []], "images": images,
                  "split_points": [], "duration": None}
    config = repair_source_instances(config)
    sources = {}
    for source in config.get("sources") or []:
        if not isinstance(source, dict):
            raise ValueError("素材条目必须是对象")
        sid = source.get("id")
        if not isinstance(sid, str) or not sid or sid in sources:
            raise ValueError("素材标识重复或缺失，请重新导入")
        if source.get("kind") not in KINDS or source.get("origin") not in {"input", "media"}:
            raise ValueError(f"素材 {source.get('name', sid)} 的类型或来源无效")
        if not isinstance(source.get("file"), str) or not source["file"]:
            raise ValueError(f"素材 {source.get('name', sid)} 缺少文件路径")
        duration = source.get("duration")
        if duration is not None and not _finite(duration):
            raise ValueError(f"素材 {source.get('name', sid)} 的时长无效")
        sources[sid] = {"id": sid, "kind": source["kind"], "origin": source["origin"],
                        "file": source["file"], "name": str(source.get("name") or source["file"]),
                        "duration": None if duration is None else float(duration)}
        for field in ("priority", "original", "normalization", "preview", "width", "height", "fps", "frames", "has_audio", "muted", "crop_target"):
            if field in source:
                sources[sid][field] = source[field]

    tracks = {}
    for kind, key in (("video", "video_tracks"), ("audio", "audio_tracks")):
        lanes = config.get(key)
        if not isinstance(lanes, list):
            raise ValueError(f"{key} 必须是轨道列表")
        # 前端可只保存有片段的轨；补齐到三条，视频与音频合计最多六轨。
        if len(lanes) > TRACK_COUNT:
            raise ValueError(f"{key} 最多 {TRACK_COUNT} 条轨道，当前 {len(lanes)} 条")
        lanes = list(lanes) + [[] for _ in range(max(0, 3 - len(lanes)))]
        normalized = []
        for lane_index, lane in enumerate(lanes, start=1):
            if not isinstance(lane, list):
                raise ValueError(f"{key} 第 {lane_index} 轨必须是列表")
            clips = []
            for clip in lane:
                if not isinstance(clip, dict):
                    raise ValueError("片段必须是对象")
                source = sources.get(clip.get("source"))
                if source is None or source["kind"] != kind:
                    raise ValueError("片段引用的素材不存在或类型与轨道不符")
                if not _finite(clip.get("tl_start"), positive=False) \
                        or not _finite(clip.get("src_in"), positive=False) \
                        or not _finite(clip.get("src_out")) \
                        or float(clip["src_out"]) <= float(clip["src_in"]):
                    raise ValueError("片段的时间范围无效，请重新拖动或裁剪")
                if source["duration"] is not None and float(clip["src_out"]) > source["duration"] + HALF_FRAME:
                    raise ValueError(f"片段超出素材 {source['name']} 的长度")
                clips.append({"id": str(clip.get("id") or digest([source["id"], clip])[0:16]),
                              "source": source["id"], "tl_start": float(clip["tl_start"]),
                              "src_in": float(clip["src_in"]), "src_out": float(clip["src_out"])})
                if clip.get("audio_muted"):
                    clips[-1]["audio_muted"] = True
                if "retained_src_in" in clip or "retained_src_out" in clip:
                    lo, hi = clip.get("retained_src_in"), clip.get("retained_src_out")
                    if not _finite(lo, positive=False) or not _finite(hi) or hi <= lo \
                            or clip["src_in"] < lo - 1e-7 or clip["src_out"] > hi + 1e-7 \
                            or (source["duration"] is not None and hi > source["duration"] + HALF_FRAME):
                        raise ValueError("片段超出已裁剪保留范围，请撤销裁剪后再调整")
                    clips[-1].update(retained_src_in=float(lo), retained_src_out=float(hi))
            clips.sort(key=lambda item: item["tl_start"])
            for left, right in zip(clips, clips[1:]):
                if clip_span(right)[0] - clip_span(left)[1] < -HALF_FRAME:
                    raise ValueError("同一轨道的片段重叠，请先挪开再进行其他操作")
            normalized.append(clips)
        tracks[key] = normalized

    images = []
    seen_images = set()
    for image in config.get("images") or []:
        if not isinstance(image, dict):
            raise ValueError("图片池条目必须是对象")
        source = sources.get(image.get("source"))
        if source is None or source["kind"] != "image":
            raise ValueError("图片池引用的素材不存在或不是图片")
        segment = image.get("segment", 0)
        if isinstance(segment, bool) or not isinstance(segment, int) or segment < -2:
            raise ValueError("图片的参考段指定无效")
        iid = str(image.get("id") or source["id"])
        if iid in seen_images:
            raise ValueError("图片池条目重复")
        seen_images.add(iid)
        images.append({"id": iid, "source": source["id"], "segment": segment})

    generation = None
    if "generation_clips" in config:
        if not isinstance(config["generation_clips"], list):
            raise ValueError("生成片段必须为列表")
        generation = []
        seen = set()
        for clip in config["generation_clips"]:
            if not isinstance(clip, dict) or not _finite(clip.get("tl_start"), False) or not _finite(clip.get("src_out")) or float(clip.get("src_in", 0)) != 0:
                raise ValueError("生成片段范围无效")
            key = str(clip.get("id") or digest(clip)[:16])
            if key in seen: raise ValueError("生成片段标识重复")
            seen.add(key)
            generation.append({"id": key, "tl_start": snap_seconds(clip["tl_start"]), "src_in": 0.0, "src_out": snap_seconds(clip["src_out"])})
        generation.sort(key=lambda item: item["tl_start"])
        if any(clip_span(left)[1] > clip_span(right)[0] + HALF_FRAME for left, right in zip(generation, generation[1:])):
            raise ValueError("生成片段不能覆盖其它片段，请顺延后块")
    duration = config.get("duration")
    if duration is not None and not _finite(duration) and not (generation is not None and duration == 0):
        raise ValueError("总时长必须是正数；留空则自动取最远片段末端")
    injected_points = []
    has_clips = any(lane for lane in (*tracks["video_tracks"], *tracks["audio_tracks"]))
    image_sources = [source for source in sources.values() if source["kind"] == "image"]
    image_only = (not has_clips and bool(image_sources)
                  and all(source["kind"] == "image" for source in sources.values())
                  and not any(source.get("priority") for source in sources.values()))
    forced_points = None
    if image_only:
        # 纯图片（v0.4.23 用户新规则）：生成段数量不再强制等于图片数量，由前端「分段数」决定。
        # 有 generation_clips 时完全按给定数量和顺序使用（保留各段长度，允许与图片数量不同）；
        # 很旧的存档没有 generation_clips 时，才按图片数量补等长默认段（兼容旧行为）。
        recipe_frames = 243
        recipe = config.get("segment_recipe")
        if isinstance(recipe, dict):
            try:
                recipe_frames = max(1, int(recipe.get("frames") or 243))
            except (TypeError, ValueError):
                recipe_frames = 243
        default_length = snap_seconds(recipe_frames / FPS)
        if generation is None:
            next_start = 0.0
            generation = []
            for index in range(len(image_sources)):
                generation.append({"id": f"image-clip-{index + 1}", "tl_start": snap_seconds(next_start),
                                   "src_in": 0.0, "src_out": default_length})
                next_start = snap_seconds(next_start + default_length)
        else:
            generation = [clip for clip in generation if clip_span(clip)[1] - clip_span(clip)[0] > HALF_FRAME]
        forced_points = []
        injected_points = []
    else:
        injected_points = []

    split_source = forced_points if forced_points is not None else [*(config.get("split_points") or []), *injected_points]
    state = {"version": TIMELINE_VERSION, "duration": None if duration is None else float(duration),
             "sources": list(sources.values()), "video_tracks": tracks["video_tracks"],
             "audio_tracks": tracks["audio_tracks"], "images": images,
             "split_points": snap_points(split_source),
             "segmentation": config.get("segmentation", "markers")}
    if forced_points is not None:
        state["segmentation"] = "markers"
    if image_only:
        state["segmentation"] = "clips"
    if generation is not None:
        state["generation_clips"] = generation
        if not has_clips: state["duration"] = max((clip_span(clip)[1] for clip in generation), default=0.0)
    state["muted_tracks"] = {
        key: [bool(value) for value in ((config.get("muted_tracks") or {}).get(key) or [])[:TRACK_COUNT]]
        for key in ("video_tracks", "audio_tracks")
    }
    disabled = config.get("disabled_tracks") or {}
    if not isinstance(disabled, dict) or any(not isinstance(disabled.get(key, []), list)
                                          for key in ("video_tracks", "audio_tracks")):
        raise ValueError("轨道开关状态无效")
    state["disabled_tracks"] = {key: [bool(value) for value in disabled.get(key, [])[:TRACK_COUNT]]
                                for key in ("video_tracks", "audio_tracks")}
    recipe = config.get("segment_recipe")
    if isinstance(recipe, dict):
        state["segment_recipe"] = recipe
    # 工作台所需数据：会话名、提示词列表、画布参数
    try:
        state["max_frames"] = int(config.get("max_frames") or 3600)
    except (TypeError, ValueError):
        state["max_frames"] = 3600
    # 会话名不再强行写死 "AD-H3-session"：未显式指定时由 nodes.py 按当前段节点号生成（AD-H3-<节点号>），
    # 与前端/ sync 接口的 AD-H3-{segment.id} 保持一致，避免批量处理读写不同会话。
    if config.get("session_id"):
        state["session_id"] = str(config["session_id"])
    prompts = [str(item) for item in (config.get("prompts") or []) if str(item).strip()]
    state["prompts"] = prompts
    if config.get("aspect_ratio") is not None:
        state["aspect_ratio"] = str(config.get("aspect_ratio"))
    if config.get("megapixels") is not None:
        state["megapixels"] = str(config.get("megapixels"))
    if config.get("sequence_mode") is not None:
        state["sequence_mode"] = bool(config.get("sequence_mode"))
    if config.get("reanchor_every") is not None:
        state["reanchor_every"] = int(config.get("reanchor_every") or 0)
    return state


def snap_points(raw):
    points = set()
    for value in raw or []:
        if not _finite(value, positive=False):
            raise ValueError("段切分线必须是有效秒数")
        points.add(snap_seconds(float(value)))  # 前后端统一半帧向上取整
    return sorted(points)


def effective_duration(state):
    if not any(lane for lane in (*state["video_tracks"], *state["audio_tracks"])) and "generation_clips" in state:
        return max((clip_span(clip)[1] for clip in state["generation_clips"]), default=0.0)
    if state.get("duration") is not None:
        return float(state["duration"])
    ends = [clip_span(clip)[1] for lane in (*state["video_tracks"], *state["audio_tracks"]) for clip in lane]
    if not ends:
        raise ValueError("时间线上还没有视频或音频片段；纯图片生成请在时间线设置总时长")
    return max(ends)


def asset_version(source):
    """File identity for generation dependencies, without decoding the asset."""
    version = {"origin": source["origin"], "file": source["file"]}
    try:
        from .assets import resolve_source
        stat = resolve_source(source).stat()
        version.update(size=stat.st_size, mtime_ns=stat.st_mtime_ns)
    except (OSError, ImportError):
        pass
    return version


def priority_sources(state, kind=None):
    """「素材路径」导入的优先素材；存在时只用它们输出，忽略其它素材与时间线。"""
    items = [source for source in state.get("sources", []) if source.get("priority")]
    return [source for source in items if kind is None or source.get("kind") == kind]


def retained_source_range(state, source_id, clip=None):
    if clip and "retained_src_in" in clip and "retained_src_out" in clip:
        return clip["retained_src_in"], clip["retained_src_out"]
    clips = [clip for key in ("video_tracks", "audio_tracks")
             for lane in state.get(key) or [] for clip in lane if clip["source"] == source_id]
    if not clips:
        return None
    return min(clip["src_in"] for clip in clips), max(clip["src_out"] for clip in clips)


def segment_intervals(state):
    """clips 模式按素材片段计段（不含时间线空隙）；否则按 split_points 连续切。"""
    priority = priority_sources(state)
    if priority:
        recipe_frames = 243
        recipe = state.get("segment_recipe")
        if isinstance(recipe, dict):
            try:
                recipe_frames = max(5, int(recipe.get("frames") or 243))
            except (TypeError, ValueError):
                recipe_frames = 243
        step = snap_seconds(recipe_frames / FPS)
        count = max(1, len(priority))
        return [(snap_seconds(step * index), snap_seconds(step * (index + 1))) for index in range(count)], snap_seconds(step * count)
    duration = effective_duration(state)
    has_av = any(lane for key in ("video_tracks", "audio_tracks") for lane in state[key])
    active = output_timeline(state)
    videos = [clip for lane in active["video_tracks"] for clip in lane]
    audios = [clip for lane in active["audio_tracks"] for clip in lane]
    if has_av:
        if not videos and not audios:
            return [], 0.0
        if any(any(flags) for flags in (state.get("disabled_tracks") or {}).values()):
            duration = min(duration, max(clip_span(clip)[1] for clip in videos + audios))
    if not videos and not audios and "generation_clips" in state:
        return [clip_span(clip) for clip in state["generation_clips"]], duration
    if state.get("segmentation") == "clips" and (videos or audios):
        clips = videos if videos else audios
        points = sorted({max(0, min(snap_seconds(t), duration)) for clip in clips for t in clip_span(clip)})
        intervals, previous_owner = [], None
        ordered = videos if videos else clips
        for start, end in zip(points, points[1:]):
            if end - start <= HALF_FRAME:
                continue
            owner = next((i for i, clip in enumerate(ordered) if clip_span(clip)[0] <= (start + end) / 2 < clip_span(clip)[1]), None)
            if owner is None:
                previous_owner = None
                continue
            if videos and intervals and owner == previous_owner and abs(intervals[-1][1] - start) < HALF_FRAME:
                intervals[-1] = (intervals[-1][0], end)
            else:
                intervals.append((start, end))
            previous_owner = owner
        return intervals, duration
    points = sorted({snap_seconds(point) for point in state["split_points"]
                     if HALF_FRAME < point < duration - HALF_FRAME})
    if not points:
        # 未分段：整条素材作为 1 段（用户可自行调整单段上限或分段）
        return ([(0.0, duration)] if duration > HALF_FRAME else []), duration
    bounds = [0.0, *points, duration]
    return list(zip(bounds, bounds[1:])), duration


def output_timeline(state):
    """输出视图：禁用轨道保留槽位，源数据与分段范围不变，不回退到被隐藏素材。"""
    result = dict(state)
    placed, enabled = set(), set()
    for key in ("video_tracks", "audio_tracks"):
        flags = (state.get("disabled_tracks") or {}).get(key) or []
        result[key] = []
        for index, lane in enumerate(state.get(key) or []):
            placed.update(clip["source"] for clip in lane)
            active = not (index < len(flags) and flags[index])
            if active:
                enabled.update(clip["source"] for clip in lane)
            result[key].append(lane if active else [])
    excluded = placed - enabled
    result["sources"] = [source for source in state.get("sources") or [] if source["id"] not in excluded]
    return result


def derive_segments(state):
    batch = priority_sources(state)
    if batch:
        # 路径批处理一文件一项，完整原素材；不使用时间线分段配方。
        from .assets import resolve_source
        from .normalization import video_info
        entries = []; cursor = 0.0
        for index, source in enumerate(batch):
            seconds = float(source.get("duration") or 0)
            metadata = video_info(resolve_source(source)) if source["kind"] == "video" else {}
            if metadata: seconds = float(metadata["duration"])
            if seconds <= 0: seconds = 1.0 if source["kind"] == "image" else 0.0
            if seconds <= 0: raise ValueError(f"无法读取批处理素材时长：{source['name']}")
            selected = {**source, "src_in": 0.0, "src_out": seconds} if source["kind"] == "video" else None
            version = asset_version(source)
            entries.append({"id": digest(["source-batch", version])[:24], "name": source["name"],
                "seconds_float": seconds, "seconds_int": math.ceil(seconds), "source_start": cursor,
                "batch_source": source, "batch_metadata": metadata, "timeline_audio": {"tracks": []},
                "video_source": selected, "source_video": source if selected else None,
                "images": [], "reference_images": [], "source_versions": [version]})
            cursor += seconds
        return {"entries": entries, "duration": cursor, "bounds": [], "intervals": []}
    intervals, duration = segment_intervals(state)
    state = output_timeline(state)
    bounds = [t for pair in intervals for t in pair]  # [s0,e0,s1,e1,…]；空隙不入清单
    sources = {source["id"]: source for source in state["sources"]}
    entries = []
    versions = {(source["origin"], source["file"]): asset_version(source) for source in state["sources"]}
    for index, (start, end) in enumerate(intervals):
        seconds = end - start
        target_frames(seconds)  # 不足半帧的段在这里报错，提示合并
        selected_video = video_range(state["video_tracks"], sources, start, end, index)
        whole_video = None
        if selected_video:
            whole_video = next((dict(source) for source in state["sources"] if source["kind"] == "video"
                                and source["origin"] == selected_video["origin"] and source["file"] == selected_video["file"]), None)
            if whole_video and selected_video.get("retained_range"):
                whole_video["retained_range"] = selected_video["retained_range"]
        selected_lane = next((number for number, lane in enumerate(state["video_tracks"])
                              if selected_video and any(sources[clip["source"]]["file"] == selected_video["file"]
                              and min(end, clip_span(clip)[1]) - max(start, clip_span(clip)[0]) > HALF_FRAME
                              for clip in lane)), None)
        video_mutes = (state.get("muted_tracks") or {}).get("video_tracks") or []
        entries.append({
            "id": "", "name": f"第{index + 1}段", "seconds_float": seconds,
            "seconds_int": math.ceil(seconds - 1e-9), "source_start": start,
            "timeline_audio": {"tracks": [([] if lane_index < len((state.get("muted_tracks") or {}).get("audio_tracks", []))
                                               and state["muted_tracks"]["audio_tracks"][lane_index]
                                               else audio_pieces(lane, sources, start, end))
                                             for lane_index, lane in enumerate(state["audio_tracks"])]},
            "video_source": selected_video,
            "source_video": whole_video,
            "reference_video_sources": [audio_pieces(lane, sources, start, end) for lane in state["video_tracks"] if lane],
            "muted_tracks": state.get("muted_tracks") or {},
            "video_audio_muted": bool(selected_video and selected_video.get("audio_muted")) or (bool(video_mutes[selected_lane]) if selected_lane is not None and selected_lane < len(video_mutes)
                                  else bool(whole_video and whole_video.get("muted"))),
            "images": [{"origin": sources[image["source"]]["origin"], "file": sources[image["source"]]["file"],
                        "name": sources[image["source"]]["name"], "segment": image["segment"]}
                       for image in state["images"] if image["segment"] == index + 1],
        })
    for index, entry in enumerate(entries):
        references = [sources[image["source"]] for image in state["images"] if image["segment"] in (-1, index + 1)]
        entry["reference_images"] = [{"segment": image["segment"], **versions[(sources[image["source"]]["origin"], sources[image["source"]]["file"])]}
                                     for image in state["images"] if image["segment"] in (-1, index + 1)]
        used = [piece for lane in entry["timeline_audio"]["tracks"] for piece in lane] + references
        used += [source for lane in entry["reference_video_sources"] for source in lane]
        if entry["video_source"] is not None:
            used.append(entry["video_source"])
        keys = sorted({(source["origin"], source["file"]) for source in used})
        entry["source_versions"] = [versions[key] for key in keys]
    for image in state["images"]:
        if entries and image["segment"] > len(entries):
            raise ValueError(f"图片指定了第 {image['segment']} 段参考，但当前只有 {len(entries)} 段；请调整段线或图片指定")
    for index, entry in enumerate(entries):
        start, end = intervals[index]
        content = [REVISION, index, start, end,
                   entry["timeline_audio"], entry["video_source"], entry["images"], entry["reference_images"], entry["source_versions"],
                   entry["muted_tracks"], entry["video_audio_muted"]]
        content.append(entry["reference_video_sources"])
        entry["id"] = digest(content)[:24]
    return {"entries": entries, "duration": duration, "bounds": bounds, "intervals": intervals}


def audio_pieces(lane, sources, start, end):
    pieces = []
    for clip in lane:
        clip_start, clip_end = clip_span(clip)
        lo, hi = max(start, clip_start), min(end, clip_end)
        if hi - lo <= HALF_FRAME:
            continue
        source = sources[clip["source"]]
        offset = float(clip["src_in"]) - clip_start
        pieces.append({"origin": source["origin"], "file": source["file"], "at": lo - start,
                       "src_in": offset + lo, "src_out": offset + hi,
                       **({"retained_range": [clip["retained_src_in"], clip["retained_src_out"]]} if "retained_src_in" in clip else {})})
    return pieces


def video_range(lanes, sources, start, end, index):
    # 轨道优先级：视频1 覆盖 视频2 覆盖 视频3——同段时间上层轨道的画面优先；
    # 同轨内一个段横跨两个片段仍视为歧义，要求对齐段线。
    for lane in lanes:
        candidates = []
        for clip in lane:
            clip_start, clip_end = clip_span(clip)
            lo, hi = max(start, clip_start), min(end, clip_end)
            span = hi - lo
            if span <= HALF_FRAME:
                continue
            source = sources[clip["source"]]
            offset = float(clip["src_in"]) - clip_start
            candidates.append((span, {"origin": source["origin"], "file": source["file"],
                                      "name": source["name"], "src_in": offset + lo, "src_out": offset + hi,
                                      **({"audio_muted": True} if clip.get("audio_muted") else {}),
                                      **({"retained_range": [clip["retained_src_in"], clip["retained_src_out"]]} if "retained_src_in" in clip else {})}))
        if not candidates:
            continue
        if len(candidates) > 1:
            raise ValueError(f"第 {index + 1} 段在同一条视频轨道上横跨多个片段，请把段切分线对齐片段边界或删除多余片段")
        return candidates[0][1]
    return None


def timeline_plan(raw):
    state = load_timeline(raw)
    derived = derive_segments(state)
    return {"entries": derived["entries"], "timeline": state,
            "duration": derived["duration"], "bounds": derived["bounds"]}


def _source_root(origin):
    if origin == "input":
        import folder_paths
        from pathlib import Path
        return Path(folder_paths.get_input_directory())
    from pathlib import Path
    from .storage import ROOT
    return ROOT / "media"


def timeline_identity(raw):
    config = timeline_config(raw)
    if config.get("version") != TIMELINE_VERSION:
        config = load_timeline(raw)
    files = []
    batch_path = str(config.get("path_source") or "").strip().strip('"')
    if batch_path:
        from pathlib import Path
        from .assets import AUDIO_EXTS, IMAGE_EXTS, VIDEO_EXTS
        directory = Path(batch_path).expanduser()
        supported = AUDIO_EXTS | IMAGE_EXTS | VIDEO_EXTS
        items = sorted(directory.iterdir()) if directory.is_dir() else [directory]
        for item in items:
            if item.is_file() and item.suffix.lower() in supported:
                info = item.stat(); files.append((str(item.resolve()), info.st_size, info.st_mtime_ns))
        return digest([REVISION, "source-batch", batch_path, files])
    for source in config.get("sources") or []:
        try:
            stat = (_source_root(source.get("origin")) / (source.get("file") or "")).stat()
            files.append((source.get("id"), source.get("file"), stat.st_size, stat.st_mtime_ns))
        except Exception:
            files.append((source.get("id"), source.get("file")))
    return digest([REVISION, files, config.get("video_tracks"), config.get("audio_tracks"),
                   config.get("images"), config.get("split_points"), config.get("duration"), config.get("segmentation"),
                   config.get("muted_tracks"), config.get("disabled_tracks"), [source.get("muted", False) for source in config.get("sources") or []],
                   config.get("generation_clips")])


def source_pieces(state, source_id):
    """音频素材的参考口内容：它在时间线上被使用的全部片段按时间线顺序拼接；
    从未放置到轨道时回退为整条素材。"""
    sources = {source["id"]: source for source in state["sources"]}
    source = sources[source_id]
    pieces = []
    for lane in state["audio_tracks"]:
        for clip in sorted(lane, key=lambda item: item["tl_start"]):
            if clip["source"] != source_id:
                continue
            pieces.append({"origin": source["origin"], "file": source["file"],
                           "src_in": float(clip["src_in"]), "src_out": float(clip["src_out"])})
    if not pieces and source["duration"]:
        pieces = [{"origin": source["origin"], "file": source["file"],
                   "src_in": 0.0, "src_out": float(source["duration"])}]
    return pieces


def reference_pack(state):
    """图片池 → 参考包：-1 全局参考；N 第 N 段参考；0 仅参考口输出不进包。"""
    sources = {source["id"]: source for source in state["sources"]}
    pack = {"global": [], "overrides": {}}
    for image in state["images"]:
        source = sources[image["source"]]
        resolved = {"origin": source["origin"], "file": source["file"], "name": source["name"]}
        if image["segment"] == -1:
            pack["global"].append(resolved)
        elif image["segment"] >= 1:
            pack["overrides"].setdefault(image["segment"] - 1, []).append(resolved)
    return pack


# ---------- 旧版清单自动迁移（v1 entries / v2 tracks → v3 时间线） ----------

def _legacy_cuts(track):
    state = track.get("editor", {}).get("exec", {})
    file = state.get("track_file")
    duration = float(state.get("duration") or 0)
    if not file or duration <= 0:
        raise ValueError(f"音轨 {track.get('name', '?')} 缺少文件或时长，无法自动迁移")
    start = float(track.get("trim_start", 0) or 0)
    end = float(track.get("trim_end") or duration)
    if not 0 <= start < end <= duration + HALF_FRAME:
        raise ValueError(f"音轨 {track.get('name', file)} 的裁剪范围无效，无法自动迁移")
    points = sorted({float(p) for p in state.get("points", []) if start < float(p) < end})
    bounds = [start, *points, end]
    cuts = [(a, b) for a, b in zip(bounds, bounds[1:])]
    selected = track.get("output_ranges")
    if selected is not None:
        ranges = []
        for pair in selected:
            if not isinstance(pair, (list, tuple)) or len(pair) != 2:
                raise ValueError("旧音轨输出片段格式无效")
            a, b = map(float, pair)
            if not all(math.isfinite(v) for v in (a, b)) or not 0 <= a < b <= duration + HALF_FRAME:
                raise ValueError("旧音轨输出片段范围无效")
            lo, hi = max(start, a), min(end, b)
            if lo < hi:
                ranges.append((lo, hi))
        bounds = sorted(set(bounds + [v for pair in ranges for v in pair]))
        cuts = [(a, b) for a, b in zip(bounds, bounds[1:]) if any(lo <= a + 1e-7 and hi >= b - 1e-7 for lo, hi in ranges)]
    cuts = [(a, b) for a, b in cuts if b - a > HALF_FRAME]
    return file, duration, cuts, (selected is not None or bool(points))


def migrate_legacy(config):
    # 迁移骨架必须无默认时长，避免覆盖旧清单的真实片段总长
    empty = {"version": TIMELINE_VERSION, "duration": None, "sources": [],
             "video_tracks": [[], [], []], "audio_tracks": [[], [], []], "images": [], "split_points": []}

    if "entries" in config and "tracks" not in config:  # v1：单条已切分清单
        raw_entries = [item for item in (config.get("entries") or []) if isinstance(item, dict)]
        if not raw_entries:
            return empty
        for item in raw_entries:
            if not _finite(item.get("seconds_float")) or not _finite(item.get("source_start", 0), positive=False):
                raise ValueError("旧清单条目缺少时长信息，无法自动迁移；请在旧节点重新保存")
        sources, source_ids = [], {}
        clips, splits, cursor = [], [], 0.0
        for position, item in enumerate(raw_entries):
            key = (item.get("origin", "input"), item["file"])
            if key not in source_ids:
                sid = f"m{len(sources)}"
                source_ids[key] = sid
                sources.append({"id": sid, "kind": "audio", "origin": key[0], "file": key[1],
                                "name": item.get("name", key[1]), "duration": None})
            length = float(item["seconds_float"])
            src_in = (float(item["start_sample"])/float(item["sample_rate"]) if item.get("sample_rate") else float(item.get("source_start", 0.0))) if "start_sample" in item else 0.0
            clips.append({"id": f"c{position}", "source": source_ids[key], "tl_start": cursor,
                          "src_in": src_in, "src_out": src_in + length})
            cursor += length
            splits.append(snap_seconds(cursor))
        state = dict(empty, sources=sources, audio_tracks=[clips, [], []], split_points=splits[:-1])
        return validate_timeline(state)

    tracks = [item for item in (config.get("tracks") or []) if isinstance(item, dict)]
    if not tracks:
        return empty
    if len(tracks) > TRACK_COUNT:
        raise ValueError(f"旧清单有 {len(tracks)} 条音轨，超过新时间线的 {TRACK_COUNT} 条上限；请先在旧节点删除多余音轨再迁移")
    parsed = [_legacy_cuts(track) for track in tracks]
    requested = config.get("main_track")
    primary = next((i for i, track in enumerate(tracks) if track.get("id") == requested and parsed[i][3]), None)
    if primary is None:
        primary = next((i for i, cut_flag in enumerate(p[3] for p in parsed) if cut_flag), None)
    if primary is None:
        primary = 0  # 全部未切分：整条作为一段，交给用户继续编辑
    grid, cursor = [], 0.0
    for a, b in parsed[primary][2]:
        cursor += b - a
        grid.append(snap_seconds(cursor))
    lanes = [[] for _ in range(max(3, len(tracks)))]
    companion_lane = 0
    for index, track in enumerate(tracks):
        file, duration, cuts, cut_flag = parsed[index]
        sid = f"m{index}"
        if index == primary:
            lane_index = 0
        else:
            lane_index = 1 + companion_lane
            companion_lane += 1
        if cut_flag:  # 各切分轨按自身切分顺序排放；主轨累计边界即段线
            position = 0.0
            for slot, (a, b) in enumerate(cuts):
                lanes[lane_index].append({"id": f"{sid}-{slot}", "source": sid, "tl_start": position,
                                          "src_in": a, "src_out": b})
                position += b - a
        else:
            lanes[lane_index].append({"id": f"{sid}-0", "source": sid, "tl_start": 0.0,
                                      "src_in": float(track.get("trim_start", 0) or 0),
                                      "src_out": float(track.get("trim_end") or duration)})
    sources = [{"id": f"m{index}", "kind": "audio", "origin": "input", "file": parsed[index][0],
                "name": tracks[index].get("name", parsed[index][0]), "duration": parsed[index][1]}
               for index in range(len(tracks))]
    state = dict(empty, sources=sources, audio_tracks=lanes, split_points=grid[:-1])
    return validate_timeline(state)


def loop_settings(state):
    """时间线里的循环默认参数（新版前端把 fps/宽高交给段控件，这里只保留重叠/保护/重锚定/上限）。"""
    try:
        state = json.loads(state) if isinstance(state, str) else (state or {})
    except (json.JSONDecodeError, TypeError):
        state = {}
    if not isinstance(state, dict):
        state = {}
    def number(name, default):
        try:
            return int(float(state.get(name) if state.get(name) is not None else default))
        except (TypeError, ValueError):
            return default
    return {"overlap_frames": number("overlap_frames", 0),
            "guard_frames": number("guard_frames", 0),
            "reanchor_every": number("reanchor_every", 0),
            "max_frames": number("max_frames", DEFAULT_SEGMENT_MAX_FRAMES if "DEFAULT_SEGMENT_MAX_FRAMES" in globals() else 362)}
