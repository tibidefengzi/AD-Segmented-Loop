"""同一成果的媒体/试运行/主会话路径别名，供前端统一去重。"""
def attach_identity(store, record, context, registered_name=None):
    if not record or not isinstance(context, dict) or not context.get("session_id"):
        return record
    state = store.read(context["session_id"])
    index = int(context.get("index", 0))
    rows = state.get("segments") or []
    if not 0 <= index < len(rows):
        return record
    name = registered_name
    if not name and record.get("origin") == "session":
        scope, _, relative = record["file"].partition("/")
        if scope == state["id"]:
            name = relative
    version = next((v for v in reversed(rows[index].get("versions") or []) if v.get("file") == name), None)
    if not version:
        return record
    parent = state.get("preview_parent") or {}
    record.update(version_id=version["id"], reset_id=version.get("reset_id", state.get("reset_id", "legacy")))
    aliases = {f'{record.get("origin", "session")}/{record["file"]}', f'session/{state["id"]}/{name}'}
    if parent.get("request_id"):
        record.update(trial_request=parent["request_id"], trial_session=state["id"], trial_file=name,
                      redraw_count=parent.get("redraw_count", 0))
        aliases.add(f'session/{parent["session_id"]}/trial_{parent["request_id"]}_{name}')
    record["result_aliases"] = sorted(aliases)
    return record
