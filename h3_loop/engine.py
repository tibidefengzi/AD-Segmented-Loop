from __future__ import annotations

import asyncio
import copy
import json
from pathlib import Path
import shutil
import sys
import time
import uuid

from .storage import STORE, atomic_json, contained, digest
from .spec import REVISION, validate_graph


def input_asset(node):
    fields={"LoadImage":"image","LoadVideo":"file","LoadAudio":"audio"}
    field=fields.get(node["class_type"])
    name=node.get("inputs",{}).get(field) if field else None
    if not isinstance(name,str) or not name:return None
    try:
        import folder_paths
        path=Path(folder_paths.get_annotated_filepath(name))
        stat=path.stat()
        return {"size":stat.st_size,"mtime_ns":stat.st_mtime_ns}
    except (ImportError,OSError):
        return None


def generation_graph(prompt, segment_node):
    """Only the connected generation graph participates in cache identity."""
    commits = [key for key, node in prompt.items()
               if node["class_type"] == "AidenH3Commit"
               and node.get("inputs", {}).get("context") == [str(segment_node), 0]]
    if not commits:
        commits = [key for key, node in prompt.items()
                   if node["class_type"] == "AidenH3VideoPreview"
                   and node.get("inputs", {}).get("context") == [str(segment_node), 0]]
    if len(commits) > 1:
        raise ValueError("一个循环取材节点只能连接一个结果记录节点（context 端口）")
    commit = commits[0] if commits else None
    seen = set()

    def visit(key):
        if key in seen:
            return
        seen.add(key)
        for value in prompt[key].get("inputs", {}).values():
            if isinstance(value, list) and len(value) == 2 and isinstance(value[0], str) and value[0] in prompt:
                visit(value[0])

    visit(commit if commit is not None else str(segment_node))
    validate_graph(prompt,seen)
    shape = {key: {"class_type": prompt[key]["class_type"],
                   "inputs": copy.deepcopy(prompt[key].get("inputs", {}))} for key in sorted(seen)}
    for node in shape.values():
        asset=input_asset(node)
        if asset is not None:node["source_file"]=asset
        node.pop("_meta", None)
        if node["class_type"] == "AidenH3Segment":
            node["inputs"].pop("run_token", None)
            node["inputs"].pop("session_id", None)
        elif node["class_type"] == "AidenH3AudioQueue":
            node["inputs"].pop("queue_state", None)  # Per-entry identity is added separately.
            node["inputs"].pop("audio_oasis_ui", None)
            node["inputs"].pop("h3_editor_state", None)
        elif node["class_type"] == "AidenH3VideoQueue":
            node["inputs"].pop("video_queue_state", None)  # Per-segment identity is added separately.
        elif node["class_type"] == "AidenH3VideoPreview":
            # 预览节点的播放器界面/保存下载配置只影响浏览与导出，不影响生成画面；
            # 不能因为改保存目录/模板/只看B 就把整轮进度判为失效。
            node["inputs"].pop("video_oasis_ui", None)
            node["inputs"].pop("save_config", None)
    return digest(shape), commit, seen


def template_for(prompt, segment_node, extra_pnginfo=None):
    signature, commit, ancestors = generation_graph(prompt, segment_node)
    # Retain only viewers downstream of this commit, not unrelated output nodes.
    keep = set(ancestors)
    targets = [commit] if commit is not None else [str(segment_node)]
    for key, node in prompt.items():
        if commit is not None and node["class_type"] in {"VideoOasisPreview","AidenH3VideoPreview"} and node.get("inputs", {}).get("video") == [commit, 0]:
            keep.add(key)
            targets.append(key)
    return signature, {"prompt": {key: copy.deepcopy(prompt[key]) for key in keep},
                       "segment_node": str(segment_node), "targets": targets,
                       "extra_pnginfo": extra_pnginfo or {}}


def segment_signatures(prompt, segment_node, entries, fps, video_segments=None):
    """A segment override must not invalidate unaffected earlier segments."""
    _, commit, _ = generation_graph(prompt, segment_node)

    def effective_references(link, index):
        override = global_entry = None
        visited = set()
        while isinstance(link, list) and len(link) == 2 and link[0] in prompt:
            key = link[0]
            if key in visited:
                raise ValueError("参考设置存在循环连线")
            visited.add(key)
            node = prompt[key]
            if node["class_type"] != "AidenH3References":
                return None
            inputs = node["inputs"]
            if not isinstance(inputs.get("segment"), int):
                return None  # Dynamic segment selectors use the full conservative fingerprint.
            mode = inputs["mode"]
            entry = {"mode": mode, "images": {k: inputs[k] for k in ("image1","image2","image3","image4") if k in inputs} if mode == "custom" else {}}
            if inputs["segment"] == index + 1 and override is None:
                override = entry
            if inputs["segment"] == 0 and mode != "inherit" and global_entry is None:
                global_entry = entry
            link = inputs.get("previous")
        if override and override["mode"] != "inherit":
            return override
        return global_entry or {"mode":"none", "images":{}}

    signatures = []
    for index, entry in enumerate(entries):
        shape = {}

        def visit(key):
            if key in shape:
                return
            node = prompt[key]
            inputs = copy.deepcopy(node.get("inputs", {}))
            # 随机种子每轮由 ComfyUI 自动变化，不代表结构/参数被改动；
            # 若把它算进分段签名，每跑一段都会把整轮进度重置回第 1 段。
            for name in [name for name in inputs if "seed" in str(name).lower()]:
                inputs.pop(name, None)
            if node["class_type"] == "AidenH3VideoPreview":
                inputs.pop("video_oasis_ui", None)
                inputs.pop("save_config", None)
            shape[key] = {"class_type": node["class_type"], "inputs": inputs}
            asset=input_asset(node)
            if asset is not None:shape[key]["source_file"]=asset
            if node["class_type"] == "AidenH3Segment":
                inputs.pop("run_token",None)
                inputs.pop("session_id",None)
            if node["class_type"] == "AidenH3AudioQueue":
                inputs.pop("queue_state",None)
                inputs.pop("audio_oasis_ui",None);inputs.pop("h3_editor_state",None)
            if node["class_type"] == "AidenH3VideoQueue":
                inputs.pop("video_queue_state",None)
            if node["class_type"] == "AidenH3PickReferences":
                resolved = effective_references(inputs.get("references"), index)
                interval = prompt[str(segment_node)].get("inputs",{}).get("reanchor_every",0)
                if isinstance(interval,int) and interval and index and index % interval == 0:
                    resolved = effective_references(inputs.get("references"), -1)
                if resolved is not None:
                    inputs["references"] = resolved
                    for source in resolved["images"].values():
                        if isinstance(source,list) and len(source)==2 and source[0] in prompt:
                            visit(source[0])
            for source in inputs.values():
                if isinstance(source,list) and len(source)==2 and isinstance(source[0],str) and source[0] in prompt:
                    visit(source[0])
        visit(commit if commit is not None else str(segment_node))
        # Compact material ports can feed conditioning directly, outside the timeline mix.
        from .timeline import asset_version, MAX_IMAGE_PORTS, MAX_VIDEO_PORTS, MAX_AUDIO_PORTS
        for key, node in shape.items():
            if node["class_type"] != "AidenH3AudioQueue":
                continue
            raw = json.loads(prompt[key].get("inputs", {}).get("queue_state", "{}"))
            if raw.get("version") != 3:
                continue
            sources = raw.get("sources", [])
            images = [source for source in sources if source["kind"] == "image"][:MAX_IMAGE_PORTS]
            videos = [source for source in sources if source["kind"] == "video"][:MAX_VIDEO_PORTS]
            audios = [source for source in sources if source["kind"] == "audio"][:MAX_AUDIO_PORTS]
            outputs = [None, *images, *videos, *videos, *audios]
            used = {value[1] for consumer in shape.values() for value in consumer["inputs"].values()
                    if isinstance(value, list) and len(value) == 2 and value[0] == key and isinstance(value[1], int) and 0 < value[1] < len(outputs)}
            node["inputs"]["material_references"] = {slot: asset_version(outputs[slot]) for slot in sorted(used)}
        signatures.append(digest([REVISION, shape, float(fps), entry,
                                  None if video_segments is None else video_segments[index]]))
    return signatures


def reference_manifest(prompt, segment_node, index):
    """Resolve reference labels from the saved base graph, independent of reroll seeds."""
    interval = prompt[str(segment_node)].get("inputs",{}).get("reanchor_every",0)
    anchor = bool(isinstance(interval,int) and interval and index and index % interval == 0)
    result = {}
    for key,node in prompt.items():
        if node["class_type"] != "AidenH3PickReferences" or node.get("inputs",{}).get("context") != [str(segment_node),0]:
            continue
        link = node["inputs"].get("references")
        global_entry,override,seen = None,None,set()
        while isinstance(link,list) and len(link)==2 and link[0] in prompt:
            if link[0] in seen:raise ValueError("参考设置存在循环连线")
            seen.add(link[0])
            source = prompt[link[0]]
            if source["class_type"] != "AidenH3References":break
            values = source["inputs"]
            entry = {"mode":values["mode"],"images":{k:values[k] for k in ("image1","image2","image3","image4") if k in values} if values["mode"]=="custom" else {}}
            if values["segment"] == index+1 and override is None and not anchor:override=entry
            if values["segment"] == 0 and values["mode"] != "inherit" and global_entry is None:global_entry=entry
            link=values.get("previous")
        selected = override if override and override["mode"] != "inherit" else global_entry or {"mode":"none","images":{}}
        sources = {}
        def collect(link):
            if not isinstance(link,list) or len(link)!=2 or link[0] not in prompt or link[0] in sources:return
            node=prompt[link[0]]
            sources[link[0]]={"class_type":node["class_type"],"inputs":node.get("inputs",{})}
            asset=input_asset(node)
            if asset is not None:sources[link[0]]["source_file"]=asset
            for value in node.get("inputs",{}).values():collect(value)
        for value in selected["images"].values():collect(value)
        result[key]={**selected,"source_signature":digest(sources)}
    return result


async def enqueue(session_id):
    import execution
    from server import PromptServer
    server = PromptServer.instance
    state = STORE.read(session_id)
    template = state.get("template")
    if not template:
        raise ValueError("缺少工作流，请先从预览侧开始当前段")
    try:
        active = STORE.reserve(session_id)
        prompt = copy.deepcopy(template["prompt"])
        if state.get("execution_round"):
            from .rounds import read_snapshot, validate_assets, execution_prompt
            snapshot = read_snapshot(state)
            validate_assets(snapshot)
            prompt = execution_prompt(state, snapshot)
        prompt[template["segment_node"]]["inputs"]["run_token"] = active["token"]
        # 方案 A：把“当前段号”写进节点输入，缓存键才能区分不同段；
        # 同一段抽卡时段号不变 → 上游命中缓存，只重跑种子相关节点。
        try:
            prompt[template["segment_node"]]["inputs"]["段索引"] = int(active.get("index", 0))
        except (TypeError, ValueError):
            pass
        if active.get("reroll") and not state.get("execution_round"):
            for node in prompt.values():
                for name in ("seed","noise_seed"):
                    value=node.get("inputs",{}).get(name)
                    if isinstance(value,int):
                        node["inputs"][name]=(value+active["reroll"])%(2**64)
        prompt_id = str(uuid.uuid4())
        server.node_replace_manager.apply_replacements(prompt)
        valid = await execution.validate_prompt(prompt_id, prompt, template["targets"])
        if not valid[0]:
            raise ValueError("工作流接线校验未通过：" + json.dumps(valid[3], ensure_ascii=False))
        number = server.number
        server.number += 1
        extra = {"extra_pnginfo": template.get("extra_pnginfo", {}),"create_time":int(time.time()*1000)}
        if template.get("client_id"):
            extra["client_id"] = template["client_id"]
        item = (number, prompt_id, prompt, extra, valid[2])
        # ComfyUI added a sensitive-data tuple field; support both queue signatures.
        if hasattr(execution, "SENSITIVE_EXTRA_DATA_KEYS"):
            item += ({},)
        with STORE.lock(session_id):
            current = STORE._load(session_id)
            current["active"]["prompt_id"] = prompt_id
            STORE._save(current)
        server.prompt_queue.put(item)
        asyncio.create_task(watch_prompt(session_id, active["token"], prompt_id))
        return prompt_id
    except Exception as exc:
        STORE.fail(session_id, exc)
        raise


async def watch_prompt(session_id, token, prompt_id):
    from server import PromptServer
    server = PromptServer.instance
    missing_history = 0
    while True:
        await asyncio.sleep(0.5)
        history = server.prompt_queue.get_history(prompt_id=prompt_id)
        state = STORE.read(session_id)
        current = state.get("active")
        if current and current["token"] != token:
            return
        if not current and not state.get("preview_parent") and not any(row["selected"] == token for row in state["segments"]):
            return
        if prompt_id not in history:
            running, pending = server.prompt_queue.get_current_queue()
            if any(item[1] == prompt_id for item in running + pending):
                continue
            # Queue removal can precede history publication. A committed version
            # is not enough to infer success: the downstream encoder can still fail.
            missing_history += 1
            if missing_history < (6 if current else 120):
                continue
            if (state.get("active") or {}).get("token") == token:
                STORE.fail(session_id, "任务已取消或队列被清空，可恢复当前段")
            else:
                STORE.fail(session_id, "任务结束后没有收到执行记录，请检查队列后恢复或合并")
            if state.get("preview_parent"):
                from .rounds import finish_trial
                finish_trial(session_id, success=False)
            return
        entry = history[prompt_id]
        if entry.get("status", {}).get("status_str") != "success":
            messages = entry.get("status", {}).get("messages", [])
            text = next((data.get("exception_message", "生成中断") for event, data in reversed(messages)
                         if event in {"execution_error", "execution_interrupted"}), "生成任务未完成")
            STORE.fail(session_id, text)
            if state.get("preview_parent"):
                from .rounds import finish_trial
                finish_trial(session_id, success=False)
            return
        # 已确认任务成功；只释放插件缓存，不卸载模型、不重置执行器。
        try:
            running, pending = server.prompt_queue.get_current_queue()
            if not running and not pending:
                from .assets import PREPARED_MEDIA
                PREPARED_MEDIA.trim()
                from . import cleanup
                await asyncio.to_thread(cleanup.clean_frame_cache, 60)
                await asyncio.to_thread(cleanup.clean_trial_progress, 60)
        except Exception:
            pass
        if current:
            # 纯预览没有 Commit/登记结果；成功历史确认后必须回收预约。
            # 令牌、任务号及段号在锁内复核，避免晚到的完成事件结束新任务。
            STORE.finish_preview(session_id, token, prompt_id)
            if state.get("preview_parent"):
                from .rounds import finish_trial
                finish_trial(session_id, success=False)
            return
        if state.get("preview_parent"):
            from .rounds import finish_trial
            finish_trial(session_id)
            return
        if state["auto"] and not state["pause_after"]:
            try:
                if state["status"] == "ready":
                    await enqueue(session_id)
                elif state["status"] == "completed":
                    if not any(row.get("entry", {}).get("batch_source") for row in state["segments"]):
                        await asyncio.to_thread(merge_session, session_id)
            except Exception as exc:
                STORE.fail(session_id, exc)
        return


def merge_session(session_id):
    from .finishing import concat_picture, export_manifest, merge_options, prepare_merge, restore_audio, selected_versions, validate_inputs
    with STORE.lock(session_id):
        state = STORE._load(session_id)
        if state["status"] in {"queued", "running", "merging"}:
            raise ValueError("当前有任务运行，请等待完成")
        paths = STORE.selected_files(state)
        if not paths:
            raise ValueError("没有可合并的片段")
        versions = selected_versions(state)
        if any(version.get("media_kind", "video") != "video" for version in versions):
            raise ValueError("仅视频分段支持合并成片；图片与音频请从预览中分别保存")
        options = merge_options(state.get("merge_options"))
        validate_inputs(paths, versions)
        state["status"] = "merging"
        STORE._save(state)
    try:
        directory = STORE.directory(session_id)
        merge_id = uuid.uuid4().hex
        paths = prepare_merge(paths, versions, directory, directory/("finishing_"+merge_id), options)
        out = directory / ("merged_" + merge_id + ".mp4")
        picture = directory / ("picture_" + merge_id + ".mp4")
        if len(paths) == 1:
            shutil.copyfile(paths[0], picture)
        else:
            concat_picture(paths, picture)
        audio_info=restore_audio(picture,out,directory,versions)
        # Inspect the encoded result too: backend success alone is not proof of timing.
        import av
        with av.open(str(out)) as merged:
            stream = merged.streams.video[0]
            expected = sum(v["frames"] for v in versions)
            if stream.frames != expected or stream.average_rate != 24:
                raise ValueError("合并结果帧数或帧率不符合逐段锁帧记录")
        manifest_name = "manifest_"+merge_id+".json"
        manifest=export_manifest(state,options,out)
        manifest["final_audio"]=audio_info
        atomic_json(directory/manifest_name, manifest)
        with STORE.lock(session_id):
            state = STORE._load(session_id)
            state.update(status="completed", auto=False, merged=out.name, manifest=manifest_name, error="")
            return STORE._save(state)
    except Exception as exc:
        STORE.fail(session_id, exc)
        raise
