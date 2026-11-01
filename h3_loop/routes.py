from __future__ import annotations

import asyncio
import hashlib
import os
from functools import wraps
import json
import math
from pathlib import Path
import re
import shutil
from urllib.parse import urlsplit
import uuid
from collections import OrderedDict
import copy

from aiohttp import web
from server import PromptServer

from .audio import EXTENSIONS, h3_window, prepare_entries
from .engine import enqueue, merge_session, segment_signatures, template_for
from . import layout
from .storage import ROOT, STORE, contained, digest, identifier
from .spec import validate_fps
from . import cleanup

routes = PromptServer.instance.routes


def _storage_busy():
    queue = getattr(PromptServer.instance, "prompt_queue", None)
    return bool(queue and any(queue.get_current_queue()))


@routes.get("/aiden_h3/storage")
async def storage_usage(request):
    cache_flag = str(request.rel_url.query.get("cache", "1")).lower()
    include_cache = cache_flag not in {"0", "false", "off", "no"}
    result = await asyncio.to_thread(cleanup.usage, include_cache)
    result["busy"] = _storage_busy()
    return web.json_response(result, headers={"Cache-Control":"no-store, max-age=0"})


async def startup_storage_cleanup(app):
    if not _storage_busy():
        await asyncio.to_thread(cleanup.auto_clean)


def register_startup_cleanup(app):
    """注册启动自动清理钩子。

    服务启动后 aiohttp 的 on_startup 会变成冻结列表；热重载再次导入本模块时
    直接 append 会抛 RuntimeError 并导致插件加载失败。这里检测并静默跳过：
    钩子只在完整重启时注册一次，不会重复添加，也不影响手动清理接口。
    """
    if app is None:
        return False
    try:
        hooks = app.on_startup
        if any(getattr(hook, "__name__", "") == "startup_storage_cleanup" for hook in hooks):
            return False
        hooks.append(startup_storage_cleanup)
        return True
    except (RuntimeError, AttributeError):
        return False


register_startup_cleanup(getattr(PromptServer.instance, "app", None))


def guarded(function):
    @wraps(function)
    async def wrapper(request):
        try:
            if request.method == "POST":
                source = request.headers.get("Origin") or request.headers.get("Referer")
                if not source or source == "null" or urlsplit(source).netloc.lower() != request.host.lower():
                    raise web.HTTPForbidden(text="仅允许从当前 ComfyUI 页面操作")
            return await function(request)
        except web.HTTPException:
            raise
        except FileNotFoundError:
            return web.json_response({"error": "尚无进度或文件不存在"}, status=404)
        except cleanup.StorageLimitError as exc:
            return web.json_response({"error": str(exc), "code": "storage_limit",
                                     "usage": await asyncio.to_thread(cleanup.usage)}, status=409)
        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            return web.json_response({"error": str(exc)}, status=400)
        except Exception as exc:
            import logging
            logging.getLogger(__name__).exception("H3 loop request failed")
            return web.json_response({"error": str(exc)}, status=500)
    return wrapper


@routes.post("/aiden_h3/storage/cleanup")
@guarded
async def clear_storage_cache(request):
    data = await request.json()
    confirmed = data.get("confirmed") is True
    if _storage_busy():
        return web.json_response({"error": "当前有任务运行或排队，请完成后再清理缓存"}, status=409)
    if confirmed:
        from .assets import PREPARED_MEDIA
        PREPARED_MEDIA.clear()
    result = await asyncio.to_thread(cleanup.clean_cache, confirmed)
    if result["blocked"]:
        return web.json_response({"error": "有任务进行中或进度无法核对，已保留缓存"}, status=409)
    result["usage"] = await asyncio.to_thread(cleanup.usage)
    return web.json_response(result)


@routes.post("/aiden_h3/upload")
@guarded
async def upload(request):
    from .assets import AUDIO_EXTS, IMAGE_EXTS, VIDEO_EXTS, probe_duration
    reader = await request.multipart()
    field = await reader.next()
    if field is None or not field.filename:
        raise ValueError("请选择素材文件")
    name = Path(field.filename.replace("\\", "/")).name
    suffix = Path(name).suffix.lower()
    if suffix in AUDIO_EXTS:
        kind = "audio"
    elif suffix in VIDEO_EXTS:
        kind = "video"
    elif suffix in IMAGE_EXTS:
        kind = "image"
    else:
        raise ValueError(f"不支持的素材格式：{suffix}")
    target_input = True  # 导入素材固定放 input/AD-Segmented-Loop-input（与生成物 output 分开）
    media = import_media_dir(kind)
    media.mkdir(parents=True, exist_ok=True)
    key = uuid.uuid4().hex
    filename = key + suffix
    path = media / filename
    staging = ROOT / "import-staging" / (key + ".part")
    staging.parent.mkdir(parents=True, exist_ok=True)
    reservation = cleanup.ImportReservation()
    size = 0
    hasher = hashlib.sha256()
    try:
        await asyncio.to_thread(reservation.grow, 0)
        with staging.open("xb") as handle:
            while chunk := await field.read_chunk(8 * 1024 * 1024):
                size += len(chunk)
                if size > 1024 * 1024 * 1024:
                    raise ValueError("单个素材文件超过 1 GB，请先分段")
                await asyncio.to_thread(reservation.grow, len(chunk))
                def write_chunk(block=chunk):
                    handle.write(block)
                    hasher.update(block)
                await asyncio.to_thread(write_chunk)
        # 内容哈希查重：同内容直接复用库内已有文件，不再每次新增副本。
        content_hash = hasher.hexdigest()
        _duplicate, existing = await asyncio.to_thread(_reuse_duplicate, kind, size, content_hash, name)
        if _duplicate is not None:
            result = {"source": existing, "reused": True}
            if kind == "audio":  # 旧音频编辑界面仍读取 entry 形状
                result["entry"] = {**existing, "seconds_float": existing.get("duration") or 0.0,
                                   "seconds_int": math.ceil(existing.get("duration") or 0.0)}
            return web.json_response(result)
        # 额度检查、提交文件与释放预留在同一锁内，不覆盖任何已有素材。
        def commit_upload():
            with cleanup._LOCK:
                reservation.grow(0)
                created = False
                try:
                    try:
                        os.link(staging, path)  # 同卷提交不再复制整份视频；已有文件不覆盖。
                        created = True
                    except FileExistsError:
                        raise
                    except OSError:
                        with path.open("xb") as output:
                            created = True
                            with staging.open("rb") as incoming:
                                shutil.copyfileobj(incoming, output)
                except Exception:
                    if created: path.unlink(missing_ok=True)
                    raise
                reservation.close(committed=True)
        await asyncio.to_thread(commit_upload)
        duration = await asyncio.to_thread(probe_duration, path) if kind != "image" else None
        source = {"id": key, "kind": kind, "origin": "input",
                  "file": f"{layout.INPUT_LIBRARY}/{layout.KIND_DIRS[kind]}/{filename}",
                  "name": name, "duration": duration}
        result = {"source": source}
        if kind == "audio":  # 旧音频编辑界面仍读取 entry 形状
            result["entry"] = {**source, "seconds_float": duration or 0.0, "seconds_int": math.ceil(duration or 0)}
        return web.json_response(result)
    finally:
        reservation.close()
        staging.unlink(missing_ok=True)  # 只清理本次上传独占的未提交暂存文件。



async def _import_one(path, target_input=False):
    import shutil
    import uuid as _uuid
    from .assets import AUDIO_EXTS, IMAGE_EXTS, VIDEO_EXTS, probe_duration
    suffix = path.suffix.lower()
    if suffix in AUDIO_EXTS:
        kind = "audio"
    elif suffix in VIDEO_EXTS:
        kind = "video"
    elif suffix in IMAGE_EXTS:
        kind = "image"
    else:
        raise ValueError(f"不支持的素材格式：{suffix}")
    # 内容哈希查重：库内已有同内容则直接复用，不复制新副本。
    size = await asyncio.to_thread(lambda: path.stat().st_size)
    content_hash = await asyncio.to_thread(_content_hash, path)
    _duplicate, existing = await asyncio.to_thread(_reuse_duplicate, kind, size, content_hash, path.name)
    if _duplicate is not None:
        return existing
    media = import_media_dir(kind)
    media.mkdir(parents=True, exist_ok=True)
    key = _uuid.uuid4().hex
    filename = key + suffix
    target = media / filename
    await asyncio.to_thread(cleanup.copy_import, path, target)
    duration = await asyncio.to_thread(probe_duration, target) if kind != "image" else None
    source = {"id": key, "kind": kind, "origin": "input",
              "file": f"{layout.INPUT_LIBRARY}/{layout.KIND_DIRS[kind]}/{filename}",
              "name": path.name, "duration": duration}
    return source


MEDIA_INFO_CACHE = {}


@routes.post("/aiden_h3/open_folder")
@guarded
async def open_folder(request):
    """在资源管理器里打开素材/生成目录。"""
    import os
    import subprocess
    from . import layout
    data = await request.json()
    mode = str(data.get("mode") or "input")
    libraries = layout.output_libraries() if mode == "generated" else layout.input_libraries()
    folder = libraries[0] if libraries else None
    if folder is None:
        raise ValueError("找不到 ComfyUI 的 input/output 目录")
    folder.mkdir(parents=True, exist_ok=True)
    try:
        if os.name == "nt":
            subprocess.Popen(["explorer", str(folder)])
        else:
            subprocess.Popen(["xdg-open", str(folder)])
    except Exception as error:
        raise ValueError(f"无法打开文件夹：{error}")
    return web.json_response({"path": str(folder), "mode": mode})


@routes.post("/aiden_h3/load_library_dir")
@guarded
async def load_library_dir(request):
    """重新加载素材库目录：input 素材库 或 output 生成结果（新目录优先，旧目录兼容）。"""
    import uuid as _uuid
    from . import layout
    from .assets import probe_duration
    data = await request.json()
    mode = str(data.get("mode") or "input")
    input_library = layout.input_library_dir()
    sources = []
    if library_roots := (layout.output_libraries() if mode == "generated" else layout.input_libraries()):
        for library in library_roots:
            if not library.is_dir():
                continue
            for folder_name, kind in layout.FOLDER_KINDS.items():
                folder = library / folder_name
                if not folder.is_dir():
                    continue
                for item in sorted(folder.iterdir()):
                    if not item.is_file():
                        continue
                    if mode == "generated":
                        target_dir = input_library / folder_name
                        target_dir.mkdir(parents=True, exist_ok=True)
                        target = target_dir / item.name
                        if not target.exists():
                            await asyncio.to_thread(cleanup.copy_import, item, target)
                        sources.append({"id": _uuid.uuid4().hex, "kind": kind, "origin": "input",
                                        "file": f"{layout.INPUT_LIBRARY}/{folder_name}/{target.name}",
                                        "name": item.name,
                                        "duration": None if kind == "image" else await asyncio.to_thread(probe_duration, target)})
                    else:
                        sources.append({"id": _uuid.uuid4().hex, "kind": kind, "origin": "input",
                                        "file": f"{library.name}/{folder_name}/{item.name}",
                                        "name": item.name,
                                        "duration": None if kind == "image" else await asyncio.to_thread(probe_duration, item)})
    return web.json_response({"sources": sources, "mode": mode})


LIBRARY_FOLDERS = {"图片": "image", "音频": "audio", "视频": "video"}


@routes.post("/aiden_h3/library/list")
@guarded
async def library_list(request):
    """列出素材库/生成目录中的可载入素材（只读，不复制；新旧目录都列出）。"""
    from . import layout
    data = await request.json()
    mode = str(data.get("mode") or "input")
    libraries = layout.output_libraries() if mode == "generated" else layout.input_libraries()
    items = []
    for library in libraries:
        if not library.is_dir():
            continue
        for folder_name, kind in layout.FOLDER_KINDS.items():
            folder = library / folder_name
            if not folder.is_dir():
                continue
            for item in sorted(folder.iterdir(), key=lambda path: path.name.lower()):
                try:
                    if not item.is_file():
                        info = None
                    else:
                        info = item.stat()
                except OSError:
                    continue
                if info is None:
                    continue
                items.append({"kind": kind, "name": item.name, "size": info.st_size,
                              "file": f"{library.name}/{folder_name}/{item.name}"})
    return web.json_response({"mode": mode, "items": items})


@routes.post("/aiden_h3/library/load")
@guarded
async def library_load(request):
    """按勾选载入素材：素材目录只引用已有文件；生成目录只复制选中项（同内容复用）。"""
    from . import layout
    from .assets import probe_duration
    data = await request.json()
    mode = str(data.get("mode") or "input")
    input_base = layout.input_base()
    input_library = layout.input_library_dir()
    source_base = input_base
    sources = []
    for entry in data.get("items") or []:
        kind = str(entry.get("kind") or "")
        if kind not in layout.KIND_DIRS:
            raise ValueError("素材类型无效")
        raw = str(entry.get("file") or "").strip().replace("\\", "/")
        if not raw:
            raise ValueError("素材路径无效")
        if mode == "generated":
            generated = layout.resolve_output_file(raw)
            if generated is None:
                raise ValueError(f"生成物不存在：{raw}")
            target = input_library / layout.KIND_DIRS[kind] / generated.name
            if not target.is_file():
                size = generated.stat().st_size
                content_hash = await asyncio.to_thread(_content_hash, generated)
                duplicate, existing = await asyncio.to_thread(_reuse_duplicate, kind, size, content_hash, generated.name)
                if duplicate is not None:
                    sources.append(existing)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                await asyncio.to_thread(cleanup.copy_import, generated, target)
            duration = None if kind == "image" else await asyncio.to_thread(probe_duration, target)
            sources.append({"id": target.stem, "kind": kind, "origin": "input",
                            "file": target.relative_to(source_base).as_posix(),
                            "name": generated.name, "duration": duration})
        else:
            path = layout.resolve_input_file(raw)
            if path is None:
                raise ValueError(f"素材不存在：{raw}")
            duration = None if kind == "image" else await asyncio.to_thread(probe_duration, path)
            sources.append({"id": path.stem, "kind": kind, "origin": "input",
                            "file": path.relative_to(source_base).as_posix(),
                            "name": path.name, "duration": duration})
    return web.json_response({"mode": mode, "sources": sources})


@routes.post("/aiden_h3/library/pick")
@guarded
async def library_pick(request):
    """打开系统文件选择框（素材库/生成目录），选完直接载入，不用再点“载入选中”。"""
    from . import dedupe, layout
    from .assets import AUDIO_EXTS, IMAGE_EXTS, VIDEO_EXTS, probe_duration
    data = await request.json()
    mode = str(data.get("mode") or "input")
    libraries = layout.output_libraries() if mode == "generated" else layout.input_libraries()
    libraries = [library for library in libraries if library is not None]
    if not libraries:
        raise ValueError("找不到 ComfyUI 的素材目录")
    folder = libraries[0]
    folder.mkdir(parents=True, exist_ok=True)
    patterns = tuple(f"*{suffix}" for suffix in sorted(IMAGE_EXTS | AUDIO_EXTS | VIDEO_EXTS))
    from .dialogs import pick_files
    selected = await asyncio.to_thread(pick_files, str(folder),
                                       "打开", patterns)
    selected = [item for item in selected if item]
    if not selected:
        return web.json_response({"sources": [], "mode": mode, "cancelled": True})
    input_base = layout.input_base()
    input_library = layout.input_library_dir()
    if input_base is None or input_library is None:
        raise ValueError("找不到 ComfyUI 的 input 目录")
    sources = []
    for raw in selected:
        path = Path(raw)
        suffix = path.suffix.lower()
        if suffix in IMAGE_EXTS:
            kind = "image"
        elif suffix in AUDIO_EXTS:
            kind = "audio"
        elif suffix in VIDEO_EXTS:
            kind = "video"
        else:
            continue
        allowed = False
        for library in libraries:
            try:
                path.relative_to(library)
                allowed = True
                break
            except ValueError:
                continue
        if not allowed:
            raise ValueError(f"所选文件不在素材目录内：{path.name}")
        if mode == "generated":
            target = input_library / layout.KIND_DIRS[kind] / path.name
            if not target.is_file():
                target.parent.mkdir(parents=True, exist_ok=True)
                dedupe.publish(path, target)
            duration = None if kind == "image" else await asyncio.to_thread(probe_duration, target)
            sources.append({"id": target.stem, "kind": kind, "origin": "input",
                            "file": target.relative_to(input_base).as_posix(),
                            "name": path.name, "duration": duration})
        else:
            duration = None if kind == "image" else await asyncio.to_thread(probe_duration, path)
            sources.append({"id": path.stem, "kind": kind, "origin": "input",
                            "file": path.relative_to(input_base).as_posix(),
                            "name": path.name, "duration": duration})
    return web.json_response({"sources": sources, "mode": mode, "cancelled": False})


@routes.post("/aiden_h3/scan_path")
@guarded
async def scan_path(request):
    """只统计路径中的实际媒体文件，不导入、不解码、不占用导入额度。"""
    from .assets import AUDIO_EXTS, IMAGE_EXTS, VIDEO_EXTS
    data = await request.json()
    raw = str(data.get("path") or "").strip().strip('"')
    if not raw:
        raise ValueError("请填写素材文件或文件夹路径")
    path = Path(raw).expanduser()
    if not path.is_absolute():
        import folder_paths
        path = Path(folder_paths.get_input_directory()) / path
    def scan():
        if not path.exists():
            raise ValueError(f"找不到素材文件或文件夹：{path}")
        files = sorted(path.iterdir(), key=lambda item: item.name) if path.is_dir() else [path]
        sources = []
        for item in files:
            if not item.is_file(): continue
            suffix = item.suffix.lower()
            kind = "image" if suffix in IMAGE_EXTS else "audio" if suffix in AUDIO_EXTS else "video" if suffix in VIDEO_EXTS else None
            if kind: sources.append({"name": item.name, "kind": kind})
        return sources
    sources = await asyncio.to_thread(scan)
    return web.json_response({"sources": sources, "count": len(sources)})


@routes.post("/aiden_h3/import_path")
@guarded
async def import_path(request):
    import shutil
    import uuid as _uuid
    from .assets import AUDIO_EXTS, IMAGE_EXTS, VIDEO_EXTS, probe_duration
    data = await request.json()
    raw = str(data.get("path") or "").strip().strip('"')
    if not raw:
        raise ValueError("请先在输入框填写素材文件的完整路径")
    path = Path(raw).expanduser()
    if not path.is_absolute():
        import folder_paths
        path = Path(folder_paths.get_input_directory()) / path
    if not path.exists():
        raise ValueError(f"找不到素材文件或文件夹：{path}")
    if path.is_dir():
        # 文件夹导入：按文件名顺序导入该目录下所有支持的素材
        supported = AUDIO_EXTS | VIDEO_EXTS | IMAGE_EXTS
        files = sorted((item for item in path.iterdir() if item.is_file() and item.suffix.lower() in supported),
                       key=lambda item: item.name)
        if not files:
            raise ValueError(f"文件夹里没有可导入的素材：{path}")
        def preflight():
            reservation = cleanup.ImportReservation()
            try: reservation.grow(sum(item.stat().st_size for item in files))
            finally: reservation.close()
        await asyncio.to_thread(preflight)
        sources = []
        for item in files:
            sources.append(await _import_one(item, False))
        return web.json_response({"sources": sources, "source": sources[0]})
    if not path.is_file():
        raise ValueError(f"找不到素材文件：{path}")
    source = await _import_one(path)
    result = {"source": source}
    if source["kind"] == "audio":
        duration = source.get("duration") or 0.0
        result["entry"] = {**source, "seconds_float": duration, "seconds_int": math.ceil(duration)}
    return web.json_response(result)


@routes.post("/aiden_h3/separate")
@guarded
async def separate(request):
    from .assets import extract_audio, has_audio_stream, probe_duration, resolve_source
    data = await request.json()
    source = {"origin": data["origin"], "file": data["file"]}
    path = resolve_source(source)
    if not path.is_file():
        raise ValueError("视频素材缺失，请重新导入")
    if not await asyncio.to_thread(has_audio_stream, path):
        raise ValueError(f"视频 {data.get('name', path.name)} 没有音轨，无法分离")
    key = uuid.uuid4().hex
    target = ROOT / "media" / f"{key}.wav"
    if not await asyncio.to_thread(extract_audio, path, target):
        raise ValueError("音轨提取失败，请检查视频文件是否完好")
    duration = await asyncio.to_thread(probe_duration, target)
    return web.json_response({"source": {"id": key, "kind": "audio", "origin": "media",
                                         "file": f"{key}.wav", "name": data.get("name", path.stem) + "·分离音轨",
                                         "duration": duration}})


@routes.post("/aiden_h3/merge_clips")
@guarded
async def merge_retained_clips(request):
    from .assets import merge_clips
    data = await request.json()
    kind = data.get("kind")
    if kind not in {"audio", "video"}:
        raise ValueError("只能合并视频或音频片段")
    key = uuid.uuid4().hex
    filename = f"merged_{key}.{'wav' if kind == 'audio' else 'mp4'}"
    details = await asyncio.to_thread(merge_clips, data.get("clips"), kind, ROOT / "media" / filename)
    return web.json_response({"source": {"id": key, "kind": kind, "origin": "media", "file": filename,
                                         "name": "合并片段", **details}})


@routes.post("/aiden_h3/migrate_track")
@guarded
async def migrate_track(request):
    import folder_paths
    from .audio import resolve_entry
    data=await request.json()
    entry=data["entry"]
    source=resolve_entry(entry)
    if entry["origin"]=="input":return web.json_response({"entry":prepare_entries([entry])[0]})
    key=uuid.uuid4().hex
    relative=f"{layout.INPUT_LIBRARY}/"+key+source.suffix.lower()
    destination=contained(Path(folder_paths.get_input_directory()),relative)
    destination.parent.mkdir(parents=True,exist_ok=True)
    await asyncio.to_thread(cleanup.copy_import, source, destination)
    migrated={"id":entry.get("id",key),"origin":"input","file":relative,"name":entry.get("name",source.name)}
    return web.json_response({"entry":(await asyncio.to_thread(prepare_entries,[migrated]))[0]})


@routes.post("/aiden_h3/manifest")
@guarded
async def manifest(request):
    import folder_paths
    data = await request.json()
    track = data.get("track", "")
    if not track or "/" in track or "\\" in track:
        raise ValueError("请选择已保存的 Audio Oasis 分段清单")
    path = contained(Path(folder_paths.get_input_directory()) / "audio_oasis", track + "/manifest.json")
    with path.open(encoding="utf-8") as handle:
        saved = json.load(handle)
    entries = []
    for row in saved.get("segments", []):
        file = row.get("qualified") or row.get("subfolder", "") + "/" + row["filename"]
        entries.append({"id": digest([track, file])[:24], "origin": "input", "file": file,
                        "name": row.get("filename", Path(file).name), "source_start": row["start"]})
    prepared = await asyncio.to_thread(prepare_entries, entries)
    return web.json_response({"entries": prepared, "fps": saved.get("fps")})


def public_state(state):
    result = {key: value for key, value in state.items() if key not in {"template", "active", "retired_segments", "run_origins"}}
    # 旧会话没有 round_no：按第 1 轮处理，前端据此编号 R1-xx。
    result.setdefault("round_no", 1)
    active = state.get("active") or {}
    result["execution"] = {"index": active.get("index"), "prompt_id": active.get("prompt_id")}
    pending = state.get("draft_trial")
    if pending:
        try:
            child = STORE.read(pending["session_id"])
            active = child.get("active") or {}
            result["status"] = "running" if active else "queued"
            result["preview_hold"] = {"request_id": pending["request_id"], "select_on_success": False}
            result["execution"] = {"index": pending["target"], "prompt_id": active.get("prompt_id")}
        except FileNotFoundError:
            pass
    return result


@routes.get("/aiden_h3/session/{session_id}")
@guarded
async def session(request):
    state, etag = await asyncio.to_thread(STORE.read_if_changed, request.match_info["session_id"], request.headers.get("If-None-Match"))
    headers = {"ETag": etag, "Cache-Control": "no-cache"}
    if state is None:
        return web.Response(status=304, headers=headers)
    if state.get("draft_trial"):
        headers.pop("ETag", None)
    return web.json_response(public_state(state), headers=headers)


async def sync_from_graph(data):
    return await asyncio.to_thread(sync_graph_now, data)


def _enable_auto_accept(session_id):
    """新界面“生成即通过”：给会话打上自动通过标记，旧会话首次同步/操作时升级。"""
    with STORE.lock(session_id):
        state = STORE._load(session_id)
        if not state.get("auto_accept"):
            state["auto_accept"] = True
            return STORE._save(state)
        return state


def sync_graph_now(data):
    # A 方案：不再因已有 execution_round 直接返回旧快照；每次都用当前画布重新同步，
    # 连线/参数变化从下一次生成起生效。进度由 STORE.sync 按源范围保留。
    prompt = data["prompt"]
    node_id = str(data["segment_node"])
    node = prompt[node_id]
    if node["class_type"] != "AidenH3Segment":
        raise ValueError("未找到循环取材节点")
    inputs = node["inputs"]
    link = inputs.get("queue")
    session_raw = inputs.get("session_id")
    if not session_raw and link:
        try:
            queue_raw = prompt[link[0]]["inputs"].get("queue_state")
            state_data = json.loads(queue_raw) if isinstance(queue_raw, str) else (queue_raw or {})
            session_raw = state_data.get("session_id")
        except Exception:
            session_raw = None
    session_id = identifier(session_raw or f"AD-H3-{node_id}")
    try:
        existing = STORE.read(session_id)
        if existing.get("execution_round"):
            return existing
    except FileNotFoundError:
        pass
    if not link:
        raise ValueError("独立模式请用「分段循环」逐段执行；连分段清单后才能用开始/分段循环。")
    queue_node = prompt[link[0]]
    if queue_node["class_type"] != "AidenH3AudioQueue" or link[1] != 0:
        raise ValueError("当前段的 queue 需要连接 H3 多段音频队列")
    raw_state = queue_node["inputs"]["queue_state"]
    from .timeline import timeline_plan
    plan = timeline_plan(raw_state)
    entries = plan["entries"]
    if not entries:raise ValueError("请设置每段长度和分段数，或在时间线上切分视频/音频后再生成。")
    from .spec import FPS
    from .timeline import loop_settings
    fps = float(FPS)
    validate_fps(fps)
    settings = loop_settings(plan.get("timeline"))
    try:
        settings["reanchor_every"] = int(inputs.get("reanchor_every", settings["reanchor_every"]))
    except (TypeError, ValueError):
        pass
    settings["overlap_frames"] = 0
    settings["guard_frames"] = 0
    for index in range(len(entries)):
        if not entries[index].get("batch_source"): h3_window(entries,index,**settings)
    signature, template = template_for(prompt, node_id, {"workflow": data.get("workflow", {})})
    template["client_id"] = data.get("client_id")
    signatures = segment_signatures(prompt, node_id, entries, fps)
    state = STORE.sync(session_id, entries, signatures, template)
    if data.get("auto_accept"):
        state = _enable_auto_accept(session_id)
    return state


@routes.post("/aiden_h3/save_to_desktop")
@guarded
async def save_to_desktop(request):
    """兼容旧前端的“保存”入口：保存路径留空时按媒体类型归档到生成目录。"""
    import shutil
    from pathlib import Path
    data = await request.json()
    origin = str(data.get("origin") or "media")
    name = str(data.get("file") or "")
    if not name:
        raise ValueError("缺少文件名")
    import folder_paths
    input_root = Path(folder_paths.get_input_directory())
    output_root = Path(folder_paths.get_output_directory())
    roots={"input":[input_root,input_root/layout.INPUT_LIBRARY,input_root/layout.LEGACY_INPUT_LIBRARY],"output":[output_root],
           "media":[Path(ROOT)/"media",input_root/layout.INPUT_LIBRARY,input_root/layout.LEGACY_INPUT_LIBRARY],"session":[STORE.root]}
    if origin not in roots:
        raise ValueError("未知素材来源")
    candidates=[contained(root,name) for root in roots[origin]]
    source = next((path for path in candidates if path.is_file()), None)
    if source is None:
        raise ValueError(f"找不到素材文件：{name}")
    from .save_target import default_directory, kind_for_path
    target_dir = default_directory(kind_for_path(source))
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / source.name
    counter = 1
    while target.exists():
        target = target_dir / f"{source.stem}_{counter}{source.suffix}"
        counter += 1
    await asyncio.to_thread(shutil.copy2, source, target)
    return web.json_response({"path": str(target), "directory": str(target_dir)})


KIND_DIRS = dict(layout.KIND_DIRS)


def import_media_dir(kind: str):
    """导入素材目录：ComfyUI/input/AD-Segmented-Loop-input/<图片|音频|视频>。"""
    folder = layout.input_library_dir(kind)
    if folder is not None:
        return folder
    import folder_paths
    return Path(folder_paths.get_input_directory()) / layout.INPUT_LIBRARY / KIND_DIRS.get(kind, kind)


def _content_hash(path: Path, chunk: int = 4 * 1024 * 1024) -> str:
    """流式计算文件内容哈希（用于导入去重）。"""
    import hashlib
    value = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while block := handle.read(chunk):
            value.update(block)
    return value.hexdigest()


def _duplicate_import(kind: str, size: int, content_hash: str):
    """按大小预筛、再按内容哈希确认；返回库内已存在的同内容文件路径或 None。"""
    try:
        folder = import_media_dir(kind)
    except Exception:
        return None
    if not folder.is_dir():
        return None
    for item in folder.iterdir():
        try:
            if not item.is_file() or item.stat().st_size != size:
                continue
            if _content_hash(item) == content_hash:
                return item
        except OSError:
            continue
    return None


def _existing_source(kind: str, path: Path, original_name: str = ""):
    """把库内已有文件转成素材 source（origin=input）。"""
    import folder_paths
    from .assets import probe_duration
    try:
        relative = path.relative_to(Path(folder_paths.get_input_directory())).as_posix()
    except ValueError:
        relative = f"{layout.INPUT_LIBRARY}/{KIND_DIRS.get(kind, kind)}/{path.name}"
    duration = None
    if kind != "image":
        try:
            duration = probe_duration(path)
        except Exception:
            duration = None
    return {"id": path.stem, "kind": kind, "origin": "input", "file": relative,
            "name": original_name or path.name, "duration": duration}


def _reuse_duplicate(kind: str, size: int, content_hash: str, original_name: str = ""):
    """同内容已存在则直接复用；返回 (路径, source) 或 (None, None)。"""
    path = _duplicate_import(kind, size, content_hash)
    return (path, _existing_source(kind, path, original_name)) if path is not None else (None, None)


@routes.post("/aiden_h3/sync")
@guarded
async def sync_graph(request):
    data = await request.json()
    state = await sync_from_graph(data)
    if data.get("freeze_round"):
        from .rounds import freeze
        state = await asyncio.to_thread(freeze, state, data["prompt"], str(data["segment_node"]), data.get("workflow"), True)
    return web.json_response(public_state(state))


def _interrupt_owned_prompt(prompt_id):
    """原子定向中断/撤销本次任务，绝不退回全局中断。"""
    if not prompt_id:
        return
    queue = PromptServer.instance.prompt_queue
    queue.interrupt_if_running(prompt_id)
    delete = getattr(queue, "delete_queue_item", None)
    if callable(delete):
        delete(lambda item: item[1] == prompt_id)


@routes.post("/aiden_h3/interrupt_prompt")
@guarded
async def interrupt_prompt(request):
    data = await request.json()
    prompt_id = identifier(data.get("prompt_id"))
    _interrupt_owned_prompt(prompt_id)
    return web.json_response({"stopped": True})


@routes.post("/aiden_h3/action")
@guarded
async def action(request):
    data = await request.json()
    command = data["command"]
    session_id = identifier(data["session_id"])
    from . import rounds
    try:
        existing = STORE.read(session_id)
    except FileNotFoundError:
        existing = {}
    if data.get("auto_accept") and existing and not existing.get("auto_accept"):
        existing = _enable_auto_accept(session_id)
    if command == "redo_preview" and not data.get("prompt") and existing.get("execution_round"):
        raise ValueError("本段抽卡需要当前编辑内容，请从分段预览按钮操作")
    if command == "redo_preview" and data.get("prompt"):
        if not existing:
            existing = await sync_from_graph(data)
        state = await asyncio.to_thread(rounds.start_trial, data)
        return web.json_response(public_state(state))
    if existing.get("draft_trial"):
        if command not in {"stop", "cancel_preview"}:
            raise ValueError("本段抽卡尚未结束，请完成后再继续原循环")
        pending = existing["draft_trial"]
        child = STORE.read(pending["session_id"])
        active_child = child.get("active") or {}
        if command == "stop" and active_child.get("prompt_id"):
            _interrupt_owned_prompt(active_child["prompt_id"])
        state = rounds.cancel_trial(session_id, data.get("preview_id"))
        return web.json_response(public_state(state))
    if command in {"resume_round", "resume_after"}:
        # 分段循环续跑：用当前最新编辑重新冻结本轮，保留前面结果，从指定段继续。
        if not data.get("prompt") or data.get("segment_node") is None:
            raise ValueError("缺少当前编辑内容，无法继续本轮；请从分段预览的分段循环重试")
        if not existing:
            raise ValueError("尚未开始本轮，无法续跑")
        if existing.get("status") in {"queued", "running", "merging"}:
            raise ValueError("当前有任务运行，请先停止再续跑")
        anchor = 0
        if command == "resume_round":
            if existing.get("status") != "error":
                raise ValueError("当前不是报错状态，无需从报错段继续")
        else:
            try:
                anchor = int(data.get("index") or 0)
            except (TypeError, ValueError):
                anchor = 0
            if anchor <= 0:
                raise ValueError("请先在「分段生成」里选中要从其后续跑的预览卡")
        if command == "resume_after":
            # 保留进度与清空重置都能选卡续跑，恢复本轮素材，不把下一轮草稿带进来。
            with STORE.lock(session_id):
                original = STORE._load(session_id)
                candidate = copy.deepcopy(original)
                anchor = rounds.resolve_resume_anchor(candidate, data.get("preview"), anchor)
                rounds.accept_through(candidate, anchor)
                reference = original.get("execution_round") or original.get("resume_round")
                snapshot = rounds.read_snapshot({**original, "execution_round": reference}) if reference else None
                source = (snapshot or {}).get("prompt") or (original.get("template") or {}).get("prompt") or data["prompt"]
                materials = (snapshot or {}).get("materials") or rounds.capture_materials(source, str(data["segment_node"]))
                rounds.validate_assets({"assets": materials.get("assets", [])})
                prepared = rounds.apply_materials(data["prompt"], str(data["segment_node"]), materials, source)
                candidate.update(auto_accept=True, auto=False, pause_after=False, active=None, error="")
                if reference:
                    candidate["execution_round"] = copy.deepcopy(reference)
                STORE._save(candidate)
                try:
                    state = rounds.freeze(candidate, prepared, str(data["segment_node"]), data.get("workflow"), True, materials=materials)
                except Exception:
                    STORE._save(original)
                    raise
            return web.json_response(public_state(state))
        # 先解除旧轮快照与错误状态，再用最新编辑同步分段并重新冻结。
        with STORE.lock(session_id):
            current = STORE._load(session_id)
            current.pop("execution_round", None)
            current.pop("draft_trial", None)
            current.update(status="ready", auto=False, pause_after=False, active=None, error="")
            STORE._save(current)
        state = await sync_from_graph(data)
        if state["id"] != session_id:
            raise ValueError("进度名称与工作流不一致")
        state = await asyncio.to_thread(rounds.freeze, state, data["prompt"],
                                        str(data["segment_node"]), data.get("workflow"), True)
        return web.json_response(public_state(state))
    if command not in {"pause", "stop", "clear", "select", "merge", "settings"} and data.get("prompt"):
        state = await sync_from_graph(data)
        if state["id"] != session_id:
            raise ValueError("进度名称与工作流不一致")
    if command == "merge":
        state = await asyncio.to_thread(merge_session, session_id)
    elif command == "stop":
        active = {}
        try:
            active = STORE.read(session_id).get("active") or {}
        except FileNotFoundError:
            pass
        try:
            state = STORE.action(session_id, "stop")
        except FileNotFoundError:
            state = {"id": session_id, "status": "paused", "cursor": 0, "segments": []}
        _interrupt_owned_prompt(active.get("prompt_id"))
    else:
        state = STORE.action(session_id, command, **{k: data[k] for k in ("confirmed", "index", "indices", "version", "merge_options", "reason", "preview_id") if k in data})
        if command in {"start", "redo", "approve", "auto"} and data.get("frontend_queue") is not True:
            template = state.get("template") or {}
            has_commit = any(node.get("class_type") == "AidenH3Commit" for node in (template.get("prompt") or {}).values())
            # 纯预览/外部工作流：没有结果记录节点时不在后端重复排队，由预览侧“开始/分段循环”驱动
            if state["status"] == "ready" and has_commit:
                await enqueue(session_id)
                state = STORE.read(session_id)
            elif state["status"] == "completed" and (command == "auto" or state["auto"]) and has_commit:
                state = await asyncio.to_thread(merge_session, session_id)
    return web.json_response(public_state(state))


@routes.get("/aiden_h3/export/{session_id}")
@guarded
async def export(request):
    from .finishing import export_manifest, merge_options
    state = STORE.read(request.match_info["session_id"])
    STORE.selected_files(state)
    result = export_manifest(state,merge_options(state.get("merge_options")),state.get("merged") or "pending.mp4")
    return web.json_response(result,headers={"Content-Disposition":f'attachment; filename="{state["id"]}-manifest.json"'})


@routes.post("/aiden_h3/recommend_cuts")
@guarded
async def recommend_cuts(request):
    from .audio import load_audio, quiet_boundaries, resolve_entry
    data=await request.json()
    audio=await asyncio.to_thread(load_audio,resolve_entry({"origin":"input","file":data["file"]}))
    suggestions=await asyncio.to_thread(quiet_boundaries,audio,data.get("points",[]),data.get("start",0),data.get("end"))
    return web.json_response({"suggestions":suggestions})


def _resolve_media_path(origin: str, name: str):
    from . import layout
    if origin == "input":
        path = layout.resolve_input_file(name)
        if path is not None:
            return path
    elif origin == "output":
        path = layout.resolve_output_file(name)
        if path is not None:
            return path
    import folder_paths
    from pathlib import Path
    input_root = Path(folder_paths.get_input_directory())
    output_root = Path(folder_paths.get_output_directory())
    roots = {"input": [input_root, input_root / layout.INPUT_LIBRARY, input_root / layout.LEGACY_INPUT_LIBRARY],
             "output": [output_root],
             "media": [Path(ROOT) / "media", input_root / layout.INPUT_LIBRARY, input_root / layout.LEGACY_INPUT_LIBRARY],
             "session": [STORE.root]}
    if origin not in roots:
        raise ValueError("未知素材来源")
    source = next((path for path in (contained(root, name) for root in roots[origin]) if path.is_file()), None)
    if source is None:
        raise ValueError(f"找不到素材文件：{name}")
    return source


@routes.post("/aiden_h3/save_media")
@guarded
async def save_media(request):
    """按自定义目录 + 文件名模板保存预览素材；提供 target 时按系统另存为路径保存。

    目录留空时按媒体类型归档到 ComfyUI/output/AD-Segmented-Loop-output/{图片,音频,视频}。
    """
    from .save_target import kind_for_path, save_to_path, save_with_template
    data = await request.json()
    source = _resolve_media_path(str(data.get("origin") or "media"), str(data.get("file") or ""))
    target = str(data.get("target") or "").strip()
    if target:
        target_path = await asyncio.to_thread(save_to_path, source, target)
        return web.json_response({"path": str(target_path)})
    config = {"dir": str(data.get("dir") or ""), "template": str(data.get("template") or "")}
    target = await asyncio.to_thread(save_with_template, source, config, int(data.get("index") or 1), "",
                                     kind_for_path(source))
    return web.json_response({"path": str(target)})


@routes.post("/aiden_h3/pick_save_path")
@guarded
async def pick_save_path(request):
    """弹出标准 Windows「另存为」框，返回用户选择的绝对路径（取消为空）。"""
    from .dialogs import save_file as choose_save_file
    data = await request.json()
    from .save_target import configured_directory
    initial_dir = configured_directory(data.get("initial_dir"), str(data.get("kind") or ""))
    initial_dir.mkdir(parents=True, exist_ok=True)
    path = await asyncio.to_thread(choose_save_file, str(data.get("default_name") or ""),
                                   str(initial_dir), "另存为",
                                   (".mp4", ".png", ".jpg", ".jpeg", ".webp", ".wav", ".mp3", ".flac"))
    return web.json_response({"path": path or ""})


@routes.post("/aiden_h3/pick_folder")
@guarded
async def pick_folder(request):
    """弹出系统文件夹选择框（服务端桌面会话；不依赖 tkinter）。"""
    from .dialogs import pick_folder as choose_folder_dialog
    data = await request.json()
    from .save_target import configured_directory
    initial_dir = configured_directory(data.get("initial_dir"), str(data.get("kind") or ""))
    initial_dir.mkdir(parents=True, exist_ok=True)
    path = await asyncio.to_thread(choose_folder_dialog, str(initial_dir), "选择保存文件夹")
    return web.json_response({"path": path or ""})


@routes.get("/aiden_h3/media_info/{origin}/{file:.*}")
@guarded
async def media_info(request):
    """返回视频帧率/帧数/时长（用于 A/B 补帧对比时各自按实际帧率逐帧）。"""
    from .assets import resolve_source
    origin = request.match_info["origin"]
    source = {"origin": origin, "file": request.match_info["file"]}
    path = resolve_source(source)
    if not path.is_file():
        raise ValueError("素材文件不存在")
    key = f"{path.resolve()}|{path.stat().st_mtime_ns}"
    cached = MEDIA_INFO_CACHE.get(key)
    if cached:
        return web.json_response(cached)
    info = {"fps": 24.0, "frames": 0, "duration": 0.0}
    try:
        import av
        with av.open(str(path)) as container:
            stream = container.streams.video[0] if container.streams.video else None
            if stream is not None:
                fps = float(stream.average_rate or 24.0) or 24.0
                frames = int(stream.frames or 0)
                duration = 0.0
                if stream.duration is not None and stream.time_base is not None:
                    duration = float(stream.duration * stream.time_base)
                if not frames and duration > 0:
                    frames = round(duration * fps)
                if not duration and frames:
                    duration = frames / fps
                info = {"fps": round(fps, 3), "frames": frames, "duration": round(duration, 3)}
    except Exception:
        pass
    MEDIA_INFO_CACHE[key] = info
    return web.json_response(info)


_THUMB_TASKS = {}
_THUMB_SEMAPHORE = asyncio.Semaphore(2)

def _write_thumbnail(path, target):
    import io, os, subprocess, tempfile
    from PIL import Image
    from .assets import VIDEO_EXTS, ffmpeg_executable
    if path.suffix.lower() in VIDEO_EXTS:
        result = subprocess.run([ffmpeg_executable(), "-v", "error", "-nostdin", "-i", str(path),
            "-frames:v", "1", "-vf", "scale=320:320:force_original_aspect_ratio=decrease", "-f", "image2pipe", "-vcodec", "mjpeg", "pipe:1"],
            capture_output=True, check=True, timeout=30)
        handle = io.BytesIO(result.stdout)
    else:
        handle = path
    with Image.open(handle) as source:
        image = source.convert("RGB")
        image.thumbnail((320, 320))
    with tempfile.NamedTemporaryFile(dir=target.parent, suffix=".webp", delete=False) as temporary:
        temporary_path = Path(temporary.name)
    try:
        image.save(temporary_path, format="WEBP", quality=75, method=4)
        os.replace(temporary_path, target)
    finally:
        image.close()
        temporary_path.unlink(missing_ok=True)

@routes.get("/aiden_h3/thumbnail/{origin}/{file:.*}")
@guarded
async def thumb(request):
    """按需静态缩略图；视频仅后台解码首帧，浏览器不为每张卡片创建视频。"""
    from .assets import IMAGE_EXTS, VIDEO_EXTS, resolve_source
    path = resolve_source({"origin": request.match_info["origin"], "file": request.match_info["file"]})
    if not path.is_file() or path.suffix.lower() not in IMAGE_EXTS | VIDEO_EXTS:
        raise ValueError("缩略图仅支持图片或视频素材")
    cache_dir = Path(ROOT) / "thumbs"
    cache_dir.mkdir(parents=True, exist_ok=True)
    stamp = path.stat()
    key = digest([str(path.resolve()), stamp.st_mtime_ns, stamp.st_size])
    target = cache_dir / f"{key}.webp"
    if not target.is_file():
        if key not in _THUMB_TASKS:
            async def generate():
                async with _THUMB_SEMAPHORE:
                    if not target.is_file():
                        await asyncio.to_thread(_write_thumbnail, path, target)
            task = asyncio.create_task(generate())
            _THUMB_TASKS[key] = task
            task.add_done_callback(lambda done: _THUMB_TASKS.pop(key, None) if _THUMB_TASKS.get(key) is done else None)
        await asyncio.shield(_THUMB_TASKS[key])
    response = web.FileResponse(target)
    response.headers["Content-Type"] = "image/webp"
    response.headers["Cache-Control"] = "public, max-age=86400"
    return response


_DETECTION_CACHE = OrderedDict()
_DETECTION_TASKS = {}


async def _cached_detection(path, kind, options, retained, worker, *args, **kwargs):
    stamp = path.stat()
    key = digest([str(path.resolve()), stamp.st_mtime_ns, stamp.st_size, kind, options, retained])
    if key in _DETECTION_CACHE:
        _DETECTION_CACHE.move_to_end(key)
        return copy.deepcopy(_DETECTION_CACHE[key])
    task = _DETECTION_TASKS.get(key)
    if task is None:
        task = asyncio.create_task(asyncio.to_thread(worker, *args, **kwargs))
        _DETECTION_TASKS[key] = task
    try:
        result = await asyncio.shield(task)
        _DETECTION_CACHE[key] = copy.deepcopy(result)
        while len(_DETECTION_CACHE) > 16:
            _DETECTION_CACHE.popitem(last=False)
        return copy.deepcopy(result)
    finally:
        if task.done():
            _DETECTION_TASKS.pop(key, None)


@routes.post("/aiden_h3/detect_cuts")
@guarded
async def detect_cuts(request):
    """智能切分：节拍 / 镜头 / 气口。返回候选切点帧号。"""
    from .assets import resolve_source
    from .detect import detect_beats, detect_breaths, detect_shots
    data = await request.json()
    kind = str(data.get("kind") or "beats")
    source = {"origin": str(data.get("origin") or "input"), "file": str(data.get("file") or "")}
    if not source["file"]:
        raise ValueError("请先选择素材")
    path = resolve_source(source)
    if not path.is_file():
        raise ValueError("素材文件不存在，请重新导入")
    options = data.get("options") or {}
    retained = data.get("range") or {}
    range_options = {"start_time": retained.get("start", 0), "end_time": retained.get("end")}
    if kind == "beats":
        mode = str(options.get("mode") or "beat")
        default_gap = 0.4 if mode == "beat" else 1.0
        result = await _cached_detection(path, kind, options, retained, detect_beats, path, mode,
                                         float(options.get("min_gap") or default_gap),
                                         float(options.get("max_gap") or 15.0), **range_options)
    elif kind == "shots":
        result = await _cached_detection(path, kind, options, retained, detect_shots, path, float(options.get("threshold") or 0.3),
                                         float(options.get("min_shot") or 1.0),
                                         float(options.get("max_gap") or 15.0), **range_options)
    elif kind == "breaths":
        result = await _cached_detection(path, kind, options, retained, detect_breaths, path, float(options.get("sensitivity") or 0.5),
                                         float(options.get("min_seg") or 1.5),
                                         float(options.get("max_seg") or 15.0),
                                         float(options.get("min_breath") or 0.15),
                                         float(options.get("buffer") or 0.05),
                                         bool(options.get("vocals")), **range_options)
    else:
        raise ValueError("未知的检测类型")
    result["kind"] = kind
    return web.json_response(result)


@routes.get("/aiden_h3/media/{origin}/{file:.*}")
@guarded
async def media(request):
    from .assets import AUDIO_EXTS, IMAGE_EXTS, VIDEO_EXTS
    origin = request.match_info["origin"]
    if origin == "input":
        import folder_paths
        root = Path(folder_paths.get_input_directory())
    elif origin == "media":
        root = ROOT / "media"
    elif origin == "session":
        root = STORE.root
    else:
        raise ValueError("未知素材来源")
    path = contained(root, request.match_info["file"])
    if path.suffix.lower() not in AUDIO_EXTS | VIDEO_EXTS | IMAGE_EXTS:
        raise ValueError("仅提供音频、视频与图片素材")
    from urllib.parse import quote
    response = web.FileResponse(path)
    # 以内联方式播放：避免被迅雷等下载监控组件当作“下载链接”接管
    response.headers["Content-Disposition"] = f"inline; filename*=UTF-8''{quote(path.name)}"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cache-Control"] = "no-store"
    return response


# An unfinished process cannot be resumed as if the GPU job were still alive.
if STORE.root.is_dir():
    for progress in STORE.root.glob("*/progress.json"):
        try:
            existing = STORE.read(progress.parent.name)
            if existing["status"] in {"queued", "running", "merging"}:
                STORE.fail(existing["id"], "已恢复进度：上次任务未完成，请开始/恢复当前段")
        except (OSError, ValueError, KeyError):
            pass



@routes.post("/aiden_h3/round/prepare")
@guarded
async def prepare_round(request):
    from .rounds import prepare
    data = await request.json()
    return web.json_response(await asyncio.to_thread(prepare, data))


from .rounds import prompt_handler as _round_prompt_handler
if hasattr(PromptServer.instance, "add_on_prompt_handler"):
    PromptServer.instance.add_on_prompt_handler(_round_prompt_handler)
