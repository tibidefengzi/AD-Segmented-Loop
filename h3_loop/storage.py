from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import threading
import time
import uuid

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
LEGACY_ROOT = PLUGIN_ROOT / "runtime"


def default_root() -> Path:
    """数据根在 ComfyUI 的 output/AD-Segmented-Loop-data 下（跟随 --output-directory），插件目录只留源码。

    优先级：AIDEN_H3_DATA_DIR > 新数据根（若已存在）> 旧 output/AD-H3（兼容已有数据）
    > 插件目录/runtime（无 ComfyUI 的测试场景）。
    """
    configured = os.environ.get("AIDEN_H3_DATA_DIR")
    if configured:
        return Path(configured)
    try:
        import folder_paths
        getter = getattr(folder_paths, "get_output_directory", None)
        if getter is not None:
            base = Path(getter())
            new_root = base / "AD-Segmented-Loop-data"
            legacy_root = base / "AD-H3"
            if new_root.exists() or not legacy_root.is_dir():
                return new_root
            return legacy_root
    except Exception:
        pass
    return LEGACY_ROOT


ROOT = default_root()


def blocked_state_message(status) -> str:
    """按会话状态给出准确的中文阻止原因，避免把“本轮已完成”误报成“待审核”。"""
    if status == "completed":
        return ("本轮全部段已通过并完成，不能再次开始；若要重新划分轮次，请在预览审批点"
                "『清空全部进度』（二次确认，保留文件与分段），或重做其中一段；不会自动清空进度")
    if status in {"queued", "running"}:
        return "当前段有任务正在运行或排队，请等待本段完成或点停止；不会自动清空进度"
    if status == "review":
        return "当前段有待审核结果，请先在预览审批点通过或重做；不会自动清空进度"
    if status == "merging":
        return "片段正在合并，请等待合并完成；不会自动清空进度"
    return f"当前会话状态为 {status or '未知'}，无法开始新的分段；请在预览审批处理后再运行；不会自动清空进度"
_LOCKS: dict[str, threading.RLock] = {}
_GUARD = threading.Lock()
_MIGRATING = False


def data_roots() -> list[Path]:
    roots = [ROOT]
    try:
        if LEGACY_ROOT.resolve() != ROOT.resolve():
            roots.append(LEGACY_ROOT)
    except OSError:
        pass
    return roots


def migrate_legacy() -> dict:
    """把旧版插件目录里的 runtime/ 数据搬到新数据根，只做一次，失败不删源。"""
    global _MIGRATING
    summary = {"moved": 0, "skipped": 0, "failed": 0, "from": str(LEGACY_ROOT), "to": str(ROOT)}
    with _GUARD:
        if _MIGRATING:
            return summary
        _MIGRATING = True
    try:
        if LEGACY_ROOT.resolve() == ROOT.resolve() or not LEGACY_ROOT.is_dir():
            return summary
        for sub in ("sessions", "media"):
            source = LEGACY_ROOT / sub
            if not source.is_dir():
                continue
            for item in list(source.iterdir()):
                target = ROOT / sub / item.name
                try:
                    if target.exists():
                        summary["skipped"] += 1
                        continue
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(item), str(target))
                    summary["moved"] += 1
                except OSError as error:
                    summary["failed"] += 1
                    print(f"AidenH3: 迁移失败 {item}: {error}")
        if summary["failed"] == 0:
            for leftover in (LEGACY_ROOT / "sessions", LEGACY_ROOT / "media"):
                try:
                    leftover.rmdir()
                except OSError:
                    pass
        try:
            (ROOT / ".migrated-from-plugin-runtime").write_text(json.dumps(summary, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass
        print(f"AidenH3: 旧数据迁移完成 {summary}")
    finally:
        return summary


def digest(value) -> str:
    def canonical(item):
        if isinstance(item,float) and item.is_integer():
            return int(item)
        if isinstance(item,dict):
            return {key:canonical(val) for key,val in item.items()}
        if isinstance(item,(list,tuple)):
            return [canonical(val) for val in item]
        return item
    return hashlib.sha256(json.dumps(canonical(value), sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def identifier(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,96}", value):
        raise ValueError("进度名称只允许字母、数字、下划线和短横线（最多 96 个字符）")
    return value


def contained(root: Path, name: str) -> Path:
    root = root.resolve()
    path = (root / name).resolve()
    if path == root or not path.is_relative_to(root):
        raise ValueError("文件路径超出允许范围")
    return path


def atomic_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temp.open("x", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()  # Only our own uncommitted atomic-write temporary file.


class SessionStore:
    def __init__(self, root: Path | None = None):
        self.root = Path(root or ROOT) / "sessions"

    def directory(self, session_id):
        return self.root / identifier(session_id)

    def session_file(self, session_id, relative):
        """会话文件优先新根，迁移完成前回退到旧插件 runtime。"""
        primary = contained(self.directory(session_id), relative)
        if primary.is_file():
            return primary
        for root in data_roots()[1:]:
            try:
                candidate = contained(root / "sessions" / identifier(session_id), relative)
            except ValueError:
                continue
            if candidate.is_file():
                return candidate
        return primary

    def lock(self, session_id):
        key = str(self.directory(session_id).resolve())
        with _GUARD:
            return _LOCKS.setdefault(key, threading.RLock())

    def _load(self, session_id):
        path = self.directory(session_id) / "progress.json"
        if not path.is_file():
            for root in data_roots()[1:]:
                candidate = root / "sessions" / identifier(session_id) / "progress.json"
                if candidate.is_file():
                    path = candidate
                    break
        with path.open(encoding="utf-8") as handle:
            state = json.load(handle)
        if state.get("schema") != 1:
            raise ValueError("不支持的进度格式，请使用对应版本插件")
        return state

    def _save(self, state):
        state["updated_at"] = time.time()
        atomic_json(self.directory(state["id"]) / "progress.json", state)
        return copy.deepcopy(state)

    def read(self, session_id):
        with self.lock(session_id):
            return self._load(session_id)

    @staticmethod
    def _reference_only_update(old, entries, template):
        if len(old) != len(entries) or not old:
            return False
        def base(entry):
            image_keys = {(item.get("origin"),item.get("file")) for key in ("images","reference_images") for item in entry.get(key,[])}
            value = {key:item for key,item in entry.items() if key not in {"id","images","reference_images","source_versions"}}
            if "source_versions" in entry:
                value["source_versions"] = [item for item in entry["source_versions"] if (item.get("origin"),item.get("file")) not in image_keys]
            return value
        has_images = any(row["entry"].get("images") or row["entry"].get("reference_images") for row in old) or any(entry.get("images") or entry.get("reference_images") for entry in entries)
        has_reference_nodes = any(node.get("class_type") in {"AidenH3References","AidenH3PickReferences"} for node in (template or {}).get("prompt",{}).values())
        return (has_images or has_reference_nodes) and all(base(row["entry"]) == base(entry) for row,entry in zip(old,entries))

    def sync(self, session_id, entries, signatures, template=None):
        if not entries:
            raise ValueError("请先在素材清单库加载素材，并设置总时长或段切分线")
        with self.lock(session_id):
            try:
                state = self._load(session_id)
            except FileNotFoundError:
                state = {"schema": 1, "id": session_id, "cursor": 0, "epoch": 0,
                         "status": "ready", "auto": False, "pause_after": False,
                         "segments": [], "active": None, "merged": None, "error": ""}
            old = state["segments"]
            changed = next((i for i, sig in enumerate(signatures)
                            if i >= len(old) or old[i]["signature"] != sig), None)
            if len(old) != len(entries) and changed is None:
                changed = len(entries)
            if changed is not None and state["status"] in {"queued", "running", "merging"}:
                raise ValueError("当前段运行中，请等待完成后再修改素材或生成参数")
            reference_only = self._reference_only_update(old, entries, template or state.get("template"))
            by_id = {s["entry"]["id"]: s for s in old}
            rows = []
            for i, (entry, sig) in enumerate(zip(entries, signatures)):
                previous = old[i] if reference_only else by_id.get(entry["id"], {})
                same = i < len(old) and old[i]["entry"]["id"] == entry["id"] and old[i]["signature"] == sig
                rows.append({"entry": entry, "signature": sig,
                             "versions": previous.get("versions", []),
                             "reroll":previous.get("reroll",0) if same else 0,
                             "selected": previous.get("selected") if same else None,
                             "valid": previous.get("valid", False) if same else False,
                             "accepted": previous.get("accepted", False) if same else False})
            state["segments"] = rows
            if changed is not None:
                if reference_only:
                    # 未变更段保留选用、批准和预览；改变参考的段由上面的 same 判断标记待重生成。
                    state["merged"] = None
                    state["manifest"] = None
                else:
                    self._invalidate(state, changed)
                state.pop("preview_hold", None)
                state.pop("sequential_stop", None)
                state["cursor"] = min(changed, len(rows) - 1)
                state["status"] = "ready"
                state["active"] = None
            if template is not None:
                state["template"] = template
            return self._save(state)

    @staticmethod
    def _invalidate(state, start):
        for row in state["segments"][start:]:
            row["valid"] = False
            row["accepted"] = False
        state["merged"] = None
        state["manifest"] = None

    @staticmethod
    def _parent(state, index):
        if index == 0:
            return None
        row = state["segments"][index - 1]
        if state.get("preview_hold"):
            # 预览备选独立预约，可借用前段当前版本，但不要求已批准，也不推进循环。
            return row.get("selected")
        if not row["valid"] or not row["accepted"] or not row["selected"]:
            # 纯预览模式（没有生成版本）不做前段依赖阻断，按轮依次预览素材
            if not row.get("versions"):
                return None
            raise ValueError("前一段尚未通过或依赖已失效，请先处理前段")
        return row["selected"]

    def reserve(self, session_id):
        with self.lock(session_id):
            state = self._load(session_id)
            if state["status"] not in {"ready", "paused", "error"}:
                raise ValueError(blocked_state_message(state["status"]))
            index = state["cursor"]
            if not state.get("preview_hold"):
                state.pop("sequential_stop", None)
            token = uuid.uuid4().hex
            state["active"] = {"token": token, "index": index,
                               "reroll":state["segments"][index].get("reroll",0),
                               "parent": self._parent(state, index),
                               "signature": state["segments"][index]["signature"]}
            state["status"] = "queued"
            state["error"] = ""
            self._save(state)
            return copy.deepcopy(state["active"])


    @staticmethod
    def _batch_path(state):
        """素材路径批处理会话：各段 entry 带 batch_source。"""
        return any((row.get("entry") or {}).get("batch_source") for row in (state.get("segments") or []))

    def _reset_round(self, state):
        """清空本轮进度（保留分段、素材、提示词与已生成文件），回到第 1 段待生成。"""
        self._invalidate(state, 0)
        state.pop("preview_hold", None)
        state.pop("sequential_stop", None)
        for row in state["segments"]:
            row["selected"] = None
            row["reroll"] = 0
        state.update(cursor=0, status="ready", auto=False, active=None, pause_after=False,
                     epoch=state["epoch"] + 1, error="")

    def ensure_run_token(self, session_id, token=""):
        """Return a token that matches an active reservation.

        Caller must pass _normalize_run_token() output (dirty leaks like 0.8 → "").
        - Matching queued/running token: keep.
        - ready/paused/error: reserve at current cursor (no progress wipe).
        - queued native execution: reuse its reservation.
        - busy/review/completed with a wrong token: reject without erasing progress.
        """
        token = (token or "").strip()
        with self.lock(session_id):
            state = self._load(session_id)
            active = state.get("active") or None
            status = state.get("status")
            if (
                token
                and active
                and active.get("token") == token
                and status in {"queued", "running"}
            ):
                return token

            def _reserve_at_cursor(state):
                index = state["cursor"]
                if not state.get("preview_hold"):
                    state.pop("sequential_stop", None)
                fresh = uuid.uuid4().hex
                state["active"] = {
                    "token": fresh,
                    "index": index,
                    "reroll": state["segments"][index].get("reroll", 0),
                    "parent": self._parent(state, index),
                    "signature": state["segments"][index]["signature"],
                }
                state["status"] = "queued"
                state["error"] = ""
                self._save(state)
                return fresh

            if status == "completed" and self._batch_path(state):
                # 素材路径批处理完成：不要求手动清空，直接自动清空本轮并从第 1 段开始新一轮。
                self._reset_round(state)
                return _reserve_at_cursor(state)
            if status in {"ready", "paused", "error"}:
                state["active"] = None
                return _reserve_at_cursor(state)
            if status == "queued" and active and not token:
                return active["token"]

            raise ValueError(blocked_state_message(status))

    def begin(self, session_id, token):
        with self.lock(session_id):
            state = self._load(session_id)
            active = state.get("active")
            token = (token or "").strip()
            # auto-clear dirty token: empty / numeric megapixels leak / non-hex
            dirty = (not token) or bool(re.fullmatch(r"[-+]?\d+(?:\.\d+)?", token)) or (":" in token) or (not re.fullmatch(r"[0-9a-fA-F]{16,64}", token))
            if dirty or not active or active.get("token") != token:
                # ready/paused/error + no matching active → reserve (wipe if busy mismatch)
                # Aiden: 直接清掉重新开始 for dirty tokens.
                if dirty or state.get("status") in {"ready", "paused", "error"} or not active:
                    # release lock path: call ensure logic inline
                    if state.get("status") not in {"ready", "paused", "error"}:
                        self._invalidate(state, 0)
                        for row in state["segments"]:
                            row["selected"] = None
                            row["reroll"] = 0
                        state.update(cursor=0, status="ready", auto=False, active=None, pause_after=False,
                                     epoch=state["epoch"] + 1, error="")
                    state["active"] = None
                    index = state["cursor"]
                    fresh = uuid.uuid4().hex
                    state["active"] = {"token": fresh, "index": index,
                                       "reroll": state["segments"][index].get("reroll", 0),
                                       "parent": self._parent(state, index),
                                       "signature": state["segments"][index]["signature"]}
                    state["status"] = "queued"
                    state["error"] = ""
                    token = fresh
                    active = state["active"]
                    self._save(state)
                else:
                    expected = (active or {}).get("token")
                    raise ValueError(
                        "运行令牌已过期：请清空队列后点 Segment 再点 Queue（需要 HotReload 管道一轮）；"
                        f" session={session_id} status={state.get('status')}"
                        f" got_token={token[:8] if token else None}…"
                        f" expected_token={(expected[:8] + '…') if expected else None}"
                        f" active_index={(active or {}).get('index')}"
                    )
            if state["status"] not in {"queued", "running"}:
                raise ValueError(
                    f"运行令牌已结束或未就绪，勿重复提交可结束；session={session_id} status={state.get('status')}"
                )
            state["status"] = "running"
            self._save(state)
            return copy.deepcopy(state)


    def finish_preview(self, session_id, token, prompt_id):
        """释放已成功结束、但未登记分段结果的预览任务，不批准或推进段号。"""
        with self.lock(session_id):
            state = self._load(session_id)
            active = state.get("active") or {}
            if (state["status"] not in {"queued", "running"}
                    or active.get("token") != token
                    or active.get("prompt_id") != prompt_id
                    or active.get("index") != state["cursor"]):
                return False
            if self._restore_preview_hold(state):
                state["error"] = ""
                self._save(state)
                return True
            state.update(active=None, status="paused" if state["pause_after"] else "ready",
                         auto=False, pause_after=False, error="")
            self._save(state)
            return True

    @staticmethod
    def _restore_preview_hold(state, successful=False):
        """本段抽卡（预览备选）完成后恢复循环进度：新版本保留但不替换选用、不推进游标。"""
        hold = state.pop("preview_hold", None)
        if not hold:
            return False
        rows = state.get("segments") or []
        target = int(hold.get("target", 0))
        if 0 <= target < len(rows):
            row = rows[target]
            if successful and hold.get("select_on_success"):
                # 仅顺序运行主动停止后重抽停止段：保留成功的新选用，等待用户批准。
                SessionStore._invalidate(state, target + 1)
                row["accepted"] = False
            else:
                row["selected"] = hold.get("selected")
                row["valid"] = bool(hold.get("valid"))
                row["accepted"] = bool(hold.get("accepted"))
        state.update(cursor=hold.get("cursor", state.get("cursor", 0)),
                     status=hold.get("status", "ready"),
                     auto=bool(hold.get("auto")),
                     active=None,
                     pause_after=bool(hold.get("pause_after")),
                     error="" if successful else hold.get("error", ""))
        return True

    def commit(self, session_id, token, filename, details=None, index=None):
        with self.lock(session_id):
            state = self._load(session_id)
            active = state["active"]
            if not active or active["token"] != token or state["status"] != "running":
                raise ValueError("生成结果所属任务已过期；文件保留，不纳入当前进度。请从当前段重新开始。")
            row = state["segments"][active["index"]]
            version = {"id": token, "file": filename, "parent": active["parent"],
                       "signature": active["signature"], "created_at": time.time()}
            version.update(details or {})
            row["versions"].append(version)
            row["selected"] = token
            row["valid"] = True
            row["accepted"] = False
            state["active"] = None
            if self._restore_preview_hold(state, successful=True):
                return self._save(state)
            state["status"] = "review"
            if state["auto"] and not state["pause_after"]:
                self._accept(state)
            elif state["pause_after"]:
                state["status"] = "paused"
                state["auto"] = False
                state["pause_after"] = False
            return self._save(state)

    def register_result(self, session_id, filename, details=None, context=None):
        """外部工作流产出的视频在此登记为当前轮结果（等于自动 Commit），供合并与 A/B 使用。"""
        with self.lock(session_id):
            state = self._load(session_id)
            active = state.get("active")
            if (not context or not active or context.get("token") != active.get("token")
                    or context.get("index") != active.get("index")
                    or context.get("index") != state["cursor"] or state["status"] != "running"):
                raise ValueError("生成结果所属任务已过期；文件保留，不纳入当前进度")
            index = state["cursor"]
            if index >= len(state["segments"]):
                raise ValueError("当前轮次已结束，无法登记结果")
            row = state["segments"][index]
            version = {"id": uuid.uuid4().hex, "file": filename,
                       "parent": self._parent(state, index),
                       "signature": row["signature"], "created_at": time.time()}
            version.update(details or {})
            row["versions"].append(version)
            row["selected"] = version["id"]
            row["valid"] = True
            row["accepted"] = False
            state["active"] = None
            if not self._restore_preview_hold(state, successful=True):
                state["status"] = "review"
            return self._save(state)

    def _accept(self, state):
        row = state["segments"][state["cursor"]]
        if not row["valid"] or not row["selected"]:
            raise ValueError("当前段没有有效结果，不能通过")
        row["accepted"] = True
        if state["cursor"] + 1 == len(state["segments"]):
            state["status"] = "completed"
        else:
            state["cursor"] += 1
            state["status"] = "ready"

    def action(self, session_id, command, **options):
        with self.lock(session_id):
            state = self._load(session_id)
            # 除本段抽卡本身外，任何其它操作都会取消尚未完成的抽卡占位，避免旧快照覆盖新进度。
            if command == "stop":
                sequential_stop = (not state.get("preview_hold") and
                                   state["status"] in {"queued", "running", "review"})
                self._restore_preview_hold(state)
            elif command not in {"redo_preview", "cancel_preview"}:
                state.pop("preview_hold", None)
            busy = state["status"] in {"queued", "running", "merging"}
            if command == "stop":
                # 立即停止：终止当前一轮，后续轮次不再继续
                if sequential_stop:
                    state["sequential_stop"] = state["cursor"]
                state.update(auto=False, pause_after=False, active=None, status="paused", error="")
                state["epoch"] = int(state.get("epoch", 0)) + 1
            elif command == "cancel_preview":
                hold=state.get("preview_hold") or {}
                if options.get("preview_id") and options["preview_id"] != hold.get("request_id"):
                    return copy.deepcopy(state)
                if self._restore_preview_hold(state):
                    state["epoch"] = int(state.get("epoch", 0)) + 1
                    state["error"] = "本段抽卡未完成，已恢复原循环位置：" + str(options.get("reason") or "未成功排队或任务已取消")
            elif command == "pause":
                if state["status"] == "merging":
                    raise ValueError("片段已完成，正在合并，请等待完成")
                state["auto"] = False
                state["pause_after"] = busy
                if not busy:
                    state["status"] = "paused"
            elif busy:
                raise ValueError("当前任务正在运行，请等待本段完成；可选择本段完成后暂停")
            elif command == "settings":
                from .finishing import merge_options
                current = merge_options(state.get("merge_options"))
                updated = merge_options({**current, **options["merge_options"]})
                state["merge_options"] = updated
                if any(updated[key] != current[key] for key in ("mode","seams","color_match","grain","grain_seed")):
                    state["merged"] = None
                    state["manifest"] = None
            elif command == "approve":
                self._accept(state)
                state.pop("sequential_stop", None)
            elif command == "redo":
                target = state["cursor"]
                if options.get("index") is not None:
                    target = int(options["index"])
                    if not 0 <= target < len(state["segments"]):
                        raise ValueError("无效段号")
                # 重抽指定段：该段及后续依赖段标记失效（旧版本文件保留），生成/审批游标切到该段。
                self._invalidate(state, target)
                row = state["segments"][target]
                row["reroll"] = row.get("reroll", 0) + 1
                state.update(cursor=target, status="ready", auto=False, active=None, pause_after=False)
            elif command == "redo_preview":
                target = state["cursor"]
                if options.get("index") is not None:
                    target = int(options["index"])
                    if not 0 <= target < len(state["segments"]):
                        raise ValueError("无效段号")
                # 本段抽卡只生成预览备选：不失效后续段、不动选用版本与循环游标，抽完恢复原循环位置。
                row = state["segments"][target]
                state["preview_hold"] = {
                    "request_id": uuid.uuid4().hex,
                    "select_on_success": state["status"] == "paused" and state.get("sequential_stop") == target == state["cursor"],
                    "target": target,
                    "selected": row.get("selected"),
                    "valid": bool(row.get("valid", False)),
                    "accepted": bool(row.get("accepted", False)),
                    "cursor": state["cursor"],
                    "status": state["status"],
                    "auto": bool(state.get("auto", False)),
                    "pause_after": bool(state.get("pause_after", False)),
                    "error": state.get("error", ""),
                }
                state.update(cursor=target, status="ready", auto=False, active=None, pause_after=False, error="")
            elif command == "auto":
                state.update(auto=True, pause_after=False)
                row = state["segments"][state["cursor"]]
                if row["valid"] and row["selected"]:
                    self._accept(state)
                else:
                    state["status"] = "ready"
            elif command == "clear":
                if options.get("confirmed") is not True:
                    raise ValueError("清空进度需要二次确认")
                self._reset_round(state)
            elif command == "select":
                index = int(options["index"])
                if not 0 <= index < len(state["segments"]):
                    raise ValueError("无效段号")
                row = state["segments"][index]
                version = next((v for v in row["versions"] if v["id"] == options["version"]), None)
                if not version or version["signature"] != row["signature"] or version["parent"] != self._parent(state, index):
                    raise ValueError("此版本的素材或前段依赖已变化，不能直接选用")
                self._invalidate(state, index + 1)
                row.update(selected=version["id"], valid=True, accepted=False)
                state.update(cursor=index, status="review", auto=False, active=None)
            elif command == "start":
                if state["status"] == "completed":
                    if not self._batch_path(state):
                        raise ValueError(blocked_state_message("completed"))
                    self._reset_round(state)
                row = state["segments"][state["cursor"]]
                if row["valid"] and row["selected"]:
                    raise ValueError("当前段已有待审核结果，请选择通过、重做或自动完成")
                state.update(status="ready", auto=False, pause_after=False)
            else:
                raise ValueError("未知操作")
            return self._save(state)

    def fail(self, session_id, message):
        with self.lock(session_id):
            state = self._load(session_id)
            if state.get("preview_hold"):
                # 本段抽卡失败：恢复抽卡前的循环进度，不影响分段循环继续。
                self._restore_preview_hold(state)
                state["error"] = "本段抽卡失败，已恢复原循环位置：" + str(message)
                return self._save(state)
            state.update(status="error", auto=False, error=str(message), active=None)
            return self._save(state)

    def selected_files(self, state):
        files = []
        parent = None
        for row in state["segments"]:
            selected = next((v for v in row["versions"] if v["id"] == row["selected"]), None)
            if not row["valid"] or not row["accepted"] or not selected or selected["parent"] != parent or selected["signature"] != row["signature"]:
                raise ValueError("仍有未通过或依赖失效的片段，暂不能合并")
            path = self.session_file(state["id"], selected["file"])
            if not path.is_file():
                raise ValueError("选用版本文件缺失，请重做对应段")
            files.append(path)
            parent = selected["id"]
        return files


STORE = SessionStore()
