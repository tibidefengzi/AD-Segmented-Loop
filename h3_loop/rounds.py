"""本轮素材与编辑草稿隔离；生成设置按当前提交生效，试跑保持独立会话。"""
from __future__ import annotations

import copy
import json
import os
import shutil
import uuid
import hashlib
from pathlib import Path

from .storage import STORE, atomic_json, contained, identifier


def session_for(prompt, node_id):
    inputs = prompt[str(node_id)].get("inputs", {})
    if inputs.get("session_id"):
        return identifier(inputs["session_id"])
    link = inputs.get("queue")
    if not isinstance(link, list) or len(link) != 2 or str(link[0]) not in prompt:
        return None
    queue = prompt[str(link[0])]
    if queue.get("class_type") != "AidenH3AudioQueue":
        return None
    raw = queue.get("inputs", {}).get("queue_state", {})
    raw = json.loads(raw) if isinstance(raw, str) else raw
    return identifier(raw.get("session_id") or f"AD-H3-{node_id}")


def component(prompt, node_id):
    """保留本循环全部下游预览及其上游，不把其它独立输出纳入本轮。"""
    downstream = {str(node_id)}
    def links(node):
        return [v[0] for v in node.get("inputs", {}).values()
                if isinstance(v, list) and len(v) == 2 and isinstance(v[0], str) and v[0] in prompt]
    changed = True
    while changed:
        changed = False
        for key, node in prompt.items():
            if key not in downstream and any(key2 in downstream for key2 in links(node)):
                downstream.add(key)
                changed = True
    keep = set()
    def visit(key):
        if key in keep:
            return
        keep.add(key)
        for parent in links(prompt[key]):
            visit(parent)
    for key in downstream:
        visit(key)
    return {key: copy.deepcopy(prompt[key]) for key in keep}


def read_snapshot(state):
    ref = state.get("execution_round")
    if not ref:
        return None
    path = contained(STORE.directory(state["id"]), ref["file"])
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def asset_manifest(graph):
    """记录素材版本，不复制大文件；用户替换参考图会生成新文件，不改原文件。"""
    from .assets import resolve_source
    paths = set()
    def visit(value):
        if isinstance(value, dict):
            if value.get("origin") in {"input", "output", "media", "session"} and value.get("file"):
                paths.add(resolve_source(value))
            for item in value.values():
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)
    for node in graph.values():
        if node["class_type"] == "AidenH3AudioQueue":
            raw = node.get("inputs", {}).get("queue_state", {})
            visit(json.loads(raw) if isinstance(raw, str) else raw)
        field = {"LoadImage": "image", "LoadAudio": "audio", "LoadVideo": "file"}.get(node["class_type"])
        if field and isinstance(node.get("inputs", {}).get(field), str):
            import folder_paths
            paths.add(Path(folder_paths.get_annotated_filepath(node["inputs"][field])))
    return [{"path": str(path.resolve()), "size": path.stat().st_size, "mtime_ns": path.stat().st_mtime_ns}
            for path in sorted(paths)]


def validate_assets(snapshot):
    for item in snapshot.get("assets", []):
        path = Path(item["path"])
        if not path.is_file() or (path.stat().st_size, path.stat().st_mtime_ns) != (item["size"], item["mtime_ns"]):
            raise ValueError("本轮原始素材已在磁盘上被修改或移走，请恢复原素材或清空进度开始新一轮：" + path.name)


def seed_policies(graph, workflow):
    """冻结种子变化方式；randomize/increment 等原生规则继续逐段生效。"""
    policies = []
    saved = {str(node["id"]): node for node in (workflow or {}).get("nodes", [])}
    for key, node in graph.items():
        values = saved.get(key, {}).get("widgets_values", [])
        values = values if isinstance(values, list) else []
        for field in ("seed", "noise_seed"):
            seed = node.get("inputs", {}).get(field)
            if type(seed) is not int:
                continue
            mode = node.get("inputs", {}).get("control_after_generate")
            for index in range(len(values) - 1):
                if values[index] == seed and values[index + 1] in ("fixed", "randomize", "increment", "decrement"):
                    mode = values[index + 1]
                    break
            if mode in ("randomize", "increment", "decrement"):
                policies.append({"node": key, "field": field, "seed": seed, "mode": mode})
    return policies


def execution_prompt(state, snapshot):
    graph = copy.deepcopy(snapshot["prompt"])
    index = int((state.get("active") or {}).get("index", state["cursor"]))
    reroll = state["segments"][index].get("reroll", 0)
    step = index + int(reroll)
    for policy in snapshot.get("seed_policies", []):
        if policy["mode"] == "randomize":
            value = int(hashlib.sha256(f'{state["execution_round"]["id"]}/{policy["node"]}/{index}/{reroll}'.encode()).hexdigest()[:16], 16)
        else:
            value = (policy["seed"] + step * (1 if policy["mode"] == "increment" else -1)) % (2**64)
        graph[policy["node"]]["inputs"][policy["field"]] = value
    return graph


def capture_materials(prompt, node_id):
    """仅固定本轮素材及素材端口对应，生成参数仍取最新提交。"""
    node_id = str(node_id)
    graph = component(prompt, node_id)
    media_types = {"AidenH3AudioQueue", "AidenH3VideoQueue", "LoadImage", "LoadAudio", "LoadVideo"}
    nodes = {key: copy.deepcopy(node) for key, node in graph.items() if node.get("class_type") in media_types}
    segment = graph[node_id].get("inputs", {})
    fixed = {name: copy.deepcopy(segment[name]) for name in ("queue", "ref_counts") if name in segment}
    bindings = []
    for key, node in graph.items():
        for name, value in node.get("inputs", {}).items():
            if not isinstance(value, list) or len(value) != 2:
                continue
            origin, slot = str(value[0]), value[1]
            material_port = origin == node_id and isinstance(slot, int) and (slot in {2, 9, 10, 11, 12, 13} or slot >= 14)
            if origin in nodes or material_port:
                bindings.append({"node": key, "input": name, "link": copy.deepcopy(value)})
    return {"nodes": nodes, "segment_inputs": fixed, "bindings": bindings}


def apply_materials(prompt, node_id, materials, fallback=None):
    """把当前轮素材合入提交副本，不写回编辑器，不固定提示词/采样/尺寸等参数。"""
    result = copy.deepcopy(prompt)
    fallback = fallback or {}
    node_id = str(node_id)
    seen = set()
    def ensure(key):
        if key in result or key in seen or key not in fallback:
            return
        seen.add(key)
        result[key] = copy.deepcopy(fallback[key])
        for value in result[key].get("inputs", {}).values():
            if isinstance(value, list) and len(value) == 2:
                ensure(str(value[0]))
    ensure(node_id)
    live_queue_link = result.get(node_id, {}).get("inputs", {}).get("queue")
    live_queue = result.get(str(live_queue_link[0]), {}) if isinstance(live_queue_link, list) and live_queue_link else {}
    live_fields = {"prompts", "aspect_ratio", "megapixels", "sequence_mode", "reanchor_every", "max_frames", "overlap_frames", "guard_frames"}
    for key, original in materials.get("nodes", {}).items():
        frozen = copy.deepcopy(original)
        if original.get("class_type") == "AidenH3AudioQueue":
            latest = result.get(key) or live_queue
            for field in ("queue_state", "h3_editor_state"):
                try:
                    old_raw = frozen.get("inputs", {}).get(field)
                    new_raw = latest.get("inputs", {}).get(field)
                    old = json.loads(old_raw) if isinstance(old_raw, str) else copy.deepcopy(old_raw)
                    new = json.loads(new_raw) if isinstance(new_raw, str) else new_raw
                    if isinstance(old, dict) and isinstance(new, dict):
                        old.update({name: copy.deepcopy(new[name]) for name in live_fields if name in new})
                        frozen["inputs"][field] = json.dumps(old, ensure_ascii=False) if isinstance(old_raw, str) else old
                except (TypeError, ValueError):
                    pass
        result[key] = frozen
    segment = result[node_id].setdefault("inputs", {})
    for name in ("queue", "ref_counts"):
        if name in materials.get("segment_inputs", {}):
            segment[name] = copy.deepcopy(materials["segment_inputs"][name])
        else:
            segment.pop(name, None)
    for binding in materials.get("bindings", []):
        ensure(binding["node"])
        if binding["node"] in result:
            result[binding["node"]].setdefault("inputs", {})[binding["input"]] = copy.deepcopy(binding["link"])
    return result


def freeze(state, prompt, node_id, workflow=None, batch=False, materials=None):
    sid = state["id"]
    with STORE.lock(sid):
        current = STORE._load(sid)
        previous = read_snapshot(current)
        if materials is None and previous:
            materials = previous.get("materials") or capture_materials(previous["prompt"], node_id)
        if materials is not None:
            validate_assets({"assets": materials.get("assets", [])})
            prompt = apply_materials(prompt, node_id, materials, (previous or {}).get("prompt"))
        # 每次生成参数仍使用最新提交；素材绑定沿用本轮，普通素材编辑留给下一轮。
        token = uuid.uuid4().hex
        graph = component(prompt, node_id)
        # 快照内固定会话名，后续画布更换清单/连线不会改变当前轮归属。
        graph[str(node_id)]["inputs"]["session_id"] = sid
        graph[str(node_id)]["inputs"].pop("run_token", None)
        material_state = copy.deepcopy(materials) if materials is not None else capture_materials(graph, node_id)
        material_state.setdefault("assets", asset_manifest(graph))
        snapshot = {"prompt": graph, "segment_node": str(node_id), "workflow": workflow or {},
                    "assets": asset_manifest(graph), "seed_policies": seed_policies(graph, workflow),
                    "materials": material_state}
        rounds_dir = STORE.directory(sid) / "rounds"
        rounds_dir.mkdir(parents=True, exist_ok=True)
        for stale in rounds_dir.glob("*.json"):
            try:
                stale.unlink()
            except OSError:
                pass
        atomic_json(rounds_dir / (token + ".json"), snapshot)
        current["execution_round"] = {"id": token, "file": "rounds/" + token + ".json",
                                      "segment_node": str(node_id), "batch": bool(batch)}
        current.pop("resume_round", None)
        return STORE._save(current)


def resolve_resume_anchor(state, preview, fallback):
    """优先按实际结果文件定位，显示编号不是段落身份。"""
    if not preview:
        return int(fallback)
    sid = state["id"]
    named = preview.get("session_id")
    name = str(preview.get("file") or "").replace("\\", "/")
    scope = ""
    if preview.get("origin") == "session":
        scope, separator, name = name.partition("/")
        if not separator:
            raise ValueError("所选预览缺少本轮文件身份，请重新选择分段预览")
    normal = scope in {"", sid} and named in {None, "", sid}
    child = scope if scope.startswith("trial_") else (named if str(named or "").startswith("trial_") else "")
    matches = []
    for index, row in enumerate(state.get("segments") or []):
        for version in row.get("versions") or []:
            file = str(version.get("file") or "").replace("\\", "/")
            matching = bool(file) and (file == name or file.rsplit("/", 1)[-1].endswith("_" + name.rsplit("/", 1)[-1]))
            trial_match = (child and version.get("preview_only") and version.get("trial_request")
                           and (not version.get("trial_session") or version["trial_session"] == child)
                           and file == "trial_" + str(version["trial_request"]) + "_" + name)
            if (normal and matching) or trial_match:
                matches.append(index + 1)
                break
    if len(matches) == 1:
        return matches[0]
    key = preview.get("segment_key")
    if normal and named == sid and key:
        matches = [i + 1 for i, row in enumerate(state.get("segments") or []) if row.get("segment_key") == key]
        if len(matches) == 1:
            return matches[0]
    if normal and named == sid and preview.get("segment_index"):
        return int(preview["segment_index"])
    if not normal:
        raise ValueError("所选预览不属于当前这一轮，请选择本轮分段预览")
    raise ValueError("无法在本轮进度中找到所选预览，请选择本轮已有结果")


def accept_through(state, count):
    """清空重置后从选中预览续跑：前 count 段按各自最后一次生成结果补成已通过，
    第 count 段起标记待更新（旧版本保留为历史），游标停在第 count 段。"""
    rows = state.get("segments") or []
    count = int(count)
    if count <= 0:
        raise ValueError("请先在「分段生成」里选中要从其后续跑的预览卡")
    if count > len(rows):
        raise ValueError(f"本轮没有第 {count} 段，不能继续")
    latest_prefix = []
    for index, row in enumerate(rows[:count]):
        versions = row.get("versions") or []
        if not versions:
            raise ValueError(f"第 {index + 1} 段没有已生成结果，不能从第 {count + 1} 段继续")
        latest_prefix.append(max(versions, key=lambda version: float(version.get("created_at") or 0)))
    for index, row in enumerate(rows):
        if index < count:
            latest = latest_prefix[index]
            row["selected"] = latest.get("id")
            row["valid"] = True
            row["accepted"] = True
            row["boundary_changed"] = False
        else:
            row["selected"] = None
            row["valid"] = False
            row["accepted"] = False
    state.update(cursor=count, status="completed" if count == len(rows) else "ready", auto=False, pause_after=False, active=None, error="")
    return state


def prepare(data):
    """由原生排队入口调用；只替换提交副本，从不写回画布。"""
    from .routes import sync_graph_now
    prompt = copy.deepcopy(data.get("prompt") or {})
    bindings = data.get("round_bindings") or []
    bound = {}
    for item in bindings:
        try:
            if STORE.read(identifier(item["session_id"])).get("execution_round"):
                bound[str(item["segment_node"])] = item["session_id"]
        except FileNotFoundError:
            pass
    candidates = [(str(key), bound.get(str(key)) or session_for(prompt, key)) for key, node in prompt.items()
                  if node.get("class_type") == "AidenH3Segment"]
    for node_id, sid in bound.items():
        if not any(existing_sid == sid for _, existing_sid in candidates):
            candidates.append((node_id, sid))
    rounds = []
    draft = copy.deepcopy(prompt)
    replacements, removed = {}, set()
    workflow = data.get("workflow")
    for node_id, sid in candidates:
        if not sid:
            continue  # 无清单的独立模式保持原有行为。
        try:
            state = STORE.read(sid)
        except FileNotFoundError:
            state = None
        trial = (state or {}).get("draft_trial")
        if trial:
            state = STORE.read(trial["session_id"])
        else:
            if node_id not in prompt:
                continue  # 已拆掉连线：本轮不生成这一段。
            previous = read_snapshot(state) if state else None
            materials = None
            if previous:
                materials = previous.get("materials") or capture_materials(previous["prompt"], node_id)
                validate_assets({"assets": materials.get("assets", previous.get("assets", []))})
                draft = apply_materials(draft, node_id, materials, previous["prompt"])
            # 更新参数快照前先恢复本轮素材，防止下一轮素材草稿改写当前段表或参考口。
            with STORE.lock(sid):
                try:
                    current = STORE._load(sid)
                except FileNotFoundError:
                    current = None
                if current is not None:
                    current.pop("execution_round", None)
                    STORE._save(current)
            # 本轮绑定优先于画布 queue_state 里的旧会话名，避免改线后串到别的会话。
            queue_link = (draft.get(node_id) or {}).get("inputs", {}).get("queue")
            if isinstance(queue_link, list) and queue_link and str(queue_link[0]) in draft:
                queue_node = draft[str(queue_link[0])]
                try:
                    queue_state = json.loads(queue_node.get("inputs", {}).get("queue_state") or "{}")
                except (TypeError, ValueError):
                    queue_state = {}
                if isinstance(queue_state, dict):
                    queue_state["session_id"] = sid
                    queue_node.setdefault("inputs", {})["queue_state"] = json.dumps(queue_state, ensure_ascii=False)
            else:
                draft.setdefault(node_id, {}).setdefault("inputs", {})["session_id"] = sid
            state = sync_graph_now({**data, "prompt": draft, "segment_node": node_id})
            state = freeze(state, draft, node_id, data.get("workflow"), materials=materials)
        snapshot = read_snapshot(state)
        if not snapshot:
            continue
        validate_assets(snapshot)
        # 当前连线即使已拆开，也使用被冻结的端口编号和全部节点参数。
        if node_id in draft:
            removed.update(component(draft, node_id))
        frozen = execution_prompt(state, snapshot)
        for key in set(frozen) & set(replacements):
            if frozen[key] != replacements[key]:
                raise ValueError("两套循环共享的节点设置不同，请分别运行，避免其中一轮被另一轮覆盖")
        replacements.update(frozen)
        workflow = snapshot.get("workflow", workflow)
        rounds.append({"session_id": sid, "segment_node": node_id,
                       "id": (STORE.read(sid).get("execution_round") or {}).get("id")})
    prompt = {key: value for key, value in draft.items() if key not in removed}
    prompt.update(replacements)
    return {"prompt": prompt, "rounds": rounds, "workflow": workflow}


def start_trial(data):
    from .routes import sync_graph_now
    sid = identifier(data["session_id"])
    with STORE.lock(sid):
        parent = STORE._load(sid)
        if parent["status"] in {"queued", "running", "merging"} or parent.get("draft_trial"):
            raise ValueError("请先停止分段循环，并等待本段抽卡结束")
        target = int(data.get("index", parent["cursor"]))
        if target < 0 or target >= len(parent["segments"]) and not data.get("draft_target"):
            raise ValueError("无效段号")
        child_id = "trial_" + uuid.uuid4().hex
        node_id = str(data["segment_node"])
        graph = copy.deepcopy(data["prompt"])
        graph[node_id]["inputs"]["session_id"] = child_id
        graph[node_id]["inputs"].pop("run_token", None)
        queue_id = str(graph[node_id]["inputs"]["queue"][0])
        for name in ("queue_state", "h3_editor_state"):
            if name in graph[queue_id]["inputs"]:
                value = graph[queue_id]["inputs"][name]
                value = json.loads(value) if isinstance(value, str) else copy.deepcopy(value)
                value["session_id"] = child_id
                graph[queue_id]["inputs"][name] = json.dumps(value, ensure_ascii=False)
        child = sync_graph_now({**data, "prompt": graph, "session_id": child_id})
        if target >= len(child["segments"]):
            raise ValueError("当前编辑内容中已没有这个段号，请选择仍然存在的片段试跑")
        request = uuid.uuid4().hex
        # 前段尾帧只读原轮选用版本；将元数据拷入独立会话，文件仍由原轮保管。
        for index in range(target):
            row = parent["segments"][index] if index < len(parent["segments"]) else None
            if row is None:
                continue
            child["segments"][index].update(versions=copy.deepcopy(row["versions"]),
                                            selected=row["selected"], valid=row["valid"], accepted=row["accepted"])
        child.update(cursor=target, auto=False, status="ready",
                     preview_parent={"session_id": sid, "epoch": parent["epoch"], "request_id": request,
                                     "index": target, "segment_key": parent["segments"][target]["segment_key"] if target < len(parent["segments"]) else None})
        STORE._save(child)
        freeze(child, graph, node_id, data.get("workflow"))
        parent["draft_trial"] = {"session_id": child_id, "request_id": request, "target": target}
        return STORE._save(parent)


def finish_trial(child_id, success=True):
    child = STORE.read(child_id)
    source = child.get("preview_parent")
    if not source:
        return
    sid = source["session_id"]
    with STORE.lock(sid):
        parent = STORE._load(sid)
        pending = parent.get("draft_trial") or {}
        if pending.get("request_id") != source["request_id"]:
            return  # 已停止/清空的迟到试跑只留在自己的会话中。
        if parent["epoch"] != source["epoch"]:
            return
        row = parent["segments"][source["index"]] if source.get("segment_key") else None
        if row is not None and row["segment_key"] != source["segment_key"]:
            raise ValueError("试跑对应的原段已改变；试跑文件已保留")
        if success:
            trial_row = child["segments"][source["index"]]
            version = next((v for v in trial_row["versions"] if v["id"] == trial_row["selected"]), None)
            if version:
                version = copy.deepcopy(version)
                trial_file = version.get("file")
                # 备选文件留在独立会话，用限定相对路径供历史/合并统一读取。
                # 原会话另建唯一硬链接（跨盘则独占创建），不覆盖任何历史文件。
                for key in ("file", "tail_file", "overlap_file", "audio_file", "continuum_file"):
                    if not version.get(key):
                        continue
                    src = contained(STORE.directory(child_id), version[key])
                    name = "trial_" + source["request_id"] + "_" + src.name
                    dst = contained(STORE.directory(sid), name)
                    if not dst.exists():
                        try:
                            os.link(src, dst)
                        except FileExistsError:
                            pass
                        except OSError:
                            with src.open("rb") as incoming, dst.open("xb") as output:
                                shutil.copyfileobj(incoming, output)
                    version[key] = name
                    if key == "continuum_file" and src.with_suffix(".json").is_file():
                        metadata = dst.with_suffix(".json")
                        if not metadata.exists():
                            with src.with_suffix(".json").open("rb") as incoming, metadata.open("xb") as output:
                                shutil.copyfileobj(incoming, output)
                version.update(preview_only=True, trial_request=source["request_id"],
                               trial_session=child_id, trial_file=trial_file)
                versions = row["versions"] if row is not None else parent.setdefault("draft_candidates", [])
                version["draft_index"] = source["index"]
                if not any(v.get("trial_request") == source["request_id"] for v in versions):
                    versions.append(version)
                if parent.get("auto_accept") and row is not None:
                    # 新界面生成即通过：最新抽卡结果直接成为该段当前选用/通过版本，不改变游标。
                    row["selected"] = version["id"]
                    row["valid"] = True
                    row["accepted"] = True
        parent.pop("draft_trial", None)
        STORE._save(parent)


def cancel_trial(sid, request_id=None):
    with STORE.lock(sid):
        parent = STORE._load(sid)
        pending = parent.get("draft_trial")
        if not pending or request_id and request_id != pending["request_id"]:
            return parent
        parent.pop("draft_trial", None)
        STORE._save(parent)
    STORE.action(pending["session_id"], "stop")
    return STORE.read(sid)


def prompt_handler(data):
    if data.pop("ad_round_prepared", False):
        return data
    if not any(n.get("class_type") == "AidenH3Segment" for n in (data.get("prompt") or {}).values()):
        return data
    try:
        prepared = prepare({"prompt": data["prompt"],
                            "workflow": (data.get("extra_data") or {}).get("extra_pnginfo", {}).get("workflow", {})})
        data["prompt"] = prepared["prompt"]
        if prepared["rounds"] and prepared.get("workflow"):
            data.setdefault("extra_data", {}).setdefault("extra_pnginfo", {})["workflow"] = prepared["workflow"]
    except Exception as exc:
        # ComfyUI 的 hook 会吞异常：用已注册的“排队保护”节点阻断执行，
        # 让用户在本轮错误里看到真实原因；不能悄悄使用新编辑。
        data["prompt"] = {"AD_round_error": {"class_type": "AD_RoundBlocked",
                                             "inputs": {"reason": str(exc)}}}
    return data
