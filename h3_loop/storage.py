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
        return ("本轮全部段已完成，不能再次开始；要重新开始请点「清空重置」（二次确认，保留文件与分段），"
                "或对某段点「本段抽卡」；不会自动清空进度")
    if status in {"queued", "running"}:
        return "当前段有任务正在运行或排队，请等待本段完成或点「停止」；不会自动清空进度"
    if status == "review":
        return "当前段已有生成结果：点「开始」会接受该结果并继续下一段，或点「分段循环」连续生成；不会自动清空进度"
    if status == "merging":
        return "片段正在合并，请等待合并完成；不会自动清空进度"
    return f"当前进度状态异常（{status or '未知'}），无法开始新的分段；请在「分段预览」点「清空重置」或重新打开工作流后再试；不会自动清空进度"
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
        # 迁移只做一次；无论成功失败都必须复位，否则后续调用会静默跳过。
        _MIGRATING = False
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

    def state_path(self, session_id):
        path = self.directory(session_id) / "progress.json"
        if not path.is_file():
            for root in data_roots()[1:]:
                candidate = root / "sessions" / identifier(session_id) / "progress.json"
                if candidate.is_file():
                    path = candidate
                    break
        return path

    def read_if_changed(self, session_id, etag=None):
        with self.lock(session_id):
            path = self.state_path(session_id)
            stamp = path.stat()
            revision = '"' + digest([str(path), stamp.st_mtime_ns, stamp.st_size]) + '"'
            if etag == revision:
                return None, revision
            return self._load(session_id), revision

    def _load(self, session_id):
        with self.state_path(session_id).open(encoding="utf-8") as handle:
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

    @staticmethod
    def _entry_span(entry):
        try:
            start = float(entry.get("source_start") or 0.0)
            length = float(entry.get("seconds_float") or 0.0)
        except (TypeError, ValueError):
            start, length = 0.0, 0.0
        return start, start + length

    @staticmethod
    def _entry_sources(entry):
        """段内容的源时间范围：基础视频 + 本段音频轨道；参考素材不参与（改参考不失效）。"""
        def piece_key(piece):
            if not isinstance(piece, dict):
                return None
            source = piece.get("source") if isinstance(piece.get("source"), dict) else piece
            if not isinstance(source, dict):
                return None
            return (source.get("origin"), source.get("file"),
                    round(float(piece.get("src_in") or 0.0), 6),
                    round(float(piece.get("src_out") or 0.0), 6))

        keys = []
        video = entry.get("video_source")
        if isinstance(video, dict):
            keys.append(piece_key(video))
        for lane in (entry.get("timeline_audio") or {}).get("tracks") or []:
            for piece in lane or []:
                keys.append(piece_key(piece))
        return tuple(sorted(key for key in keys if key))

    @classmethod
    def _entry_range(cls, entry):
        entry = entry or {}
        pieces = cls._entry_sources(entry)
        if pieces:
            return ("source", pieces)
        start, end = cls._entry_span(entry)
        return ("span", (round(start, 6), round(end, 6)))

    @staticmethod
    def _range_overlap(left, right):
        if left[0] == right[0] == "source":
            total = 0.0
            for _, _, a1, a2 in left[1]:
                for _, _, b1, b2 in right[1]:
                    total += max(0.0, min(a2, b2) - max(a1, b1))
            return total
        if left[0] == right[0] == "span":
            return max(0.0, min(left[1][1], right[1][1]) - max(left[1][0], right[1][0]))
        return 0.0

    @staticmethod
    def _carry_row(row, entry, signature, stale=False):
        return {"entry": entry, "signature": signature,
                "segment_key": uuid.uuid4().hex if stale else row["segment_key"],
                "versions": list(row.get("versions", [])),
                "reroll": row.get("reroll", 0),
                "selected": row.get("selected"),
                "valid": False if stale else bool(row.get("valid", False)),
                "accepted": False if stale else bool(row.get("accepted", False)),
                "boundary_changed": bool(stale or row.get("boundary_changed"))}

    @staticmethod
    def _position(state, index):
        row = state["segments"][index]
        return {"cursor": index, "entry_id": row["entry"].get("id"),
                "segment_key": row["segment_key"]}

    @staticmethod
    def _bind_active(state):
        active = state.get("active")
        if active and not active.get("segment_key") and 0 <= active.get("index", -1) < len(state["segments"]):
            row = state["segments"][active["index"]]
            active.update(segment_key=row["segment_key"], entry_snapshot=copy.deepcopy(row["entry"]))
        if active and active.get("segment_key"):
            origins = state.setdefault("run_origins", {})
            origins[active["token"]] = {key: copy.deepcopy(active[key]) for key in
                ("segment_key", "entry_snapshot", "index", "signature", "parent") if key in active}
            if len(origins) > 128:
                state["run_origins"] = dict(list(origins.items())[-128:])

    @staticmethod
    def _result_target(state, origin):
        """Results follow the reserved content; removed/resized content stays archived."""
        key = origin.get("segment_key")
        if not key:
            index = int(origin["index"])
            if not 0 <= index < len(state["segments"]):
                raise ValueError("本次生成结果对应的分段已不存在（生成期间分段被修改）；文件已保留在会话目录，请对当前分段重新生成。")
            return index, state["segments"][index], True
        for index, row in enumerate(state["segments"]):
            if row.get("segment_key") == key:
                return index, row, True
        retired = state.setdefault("retired_segments", [])
        for row in retired:
            if row.get("segment_key") == key:
                return None, row, False
        row = {"segment_key": key, "entry": copy.deepcopy(origin.get("entry_snapshot") or {}),
               "signature": origin.get("signature", ""), "versions": [], "selected": None,
               "reroll": 0, "valid": False, "accepted": False, "boundary_changed": True}
        retired.append(row)
        return None, row, False

    @staticmethod
    def _saved_cursor(state, saved):
        """按段内容（entry id）找回原位置；切分导致段号变化时也能回对段。"""
        target_key = (saved or {}).get("segment_key")
        if target_key:
            for index, row in enumerate(state.get("segments") or []):
                if row.get("segment_key") == target_key:
                    return index
        target_id = (saved or {}).get("entry_id")
        if target_id:
            for index, row in enumerate(state.get("segments") or []):
                if ((row.get("entry") or {}).get("id")) == target_id:
                    return index
        size = len(state.get("segments") or [])
        if not size:
            return 0
        return max(0, min(int((saved or {}).get("cursor", 0)), size - 1))

    def sync(self, session_id, entries, signatures, template=None, keep_completed=False):
        if not entries:
            raise ValueError("请先在素材清单库加载素材，并设置总时长或段切分线")
        with self.lock(session_id):
            try:
                state = self._load(session_id)
            except FileNotFoundError:
                state = {"schema": 1, "id": session_id, "cursor": 0, "epoch": 0, "round_no": 1,
                         "status": "ready", "auto": False, "pause_after": False,
                         "segments": [], "active": None, "merged": None, "error": ""}
            if (state.get("execution_round") and not keep_completed) or state.get("draft_trial"):
                return copy.deepcopy(state)
            old = state["segments"]
            completed_prefix = 0
            if keep_completed:
                # 已完成前缀是历史成果，不因下一段素材/设置变化回写其配方或选用。
                for row in old:
                    if not (row.get("valid") and row.get("accepted") and row.get("selected")):
                        break
                    completed_prefix += 1
                entries = [copy.deepcopy(row["entry"]) for row in old[:completed_prefix]] + list(entries[completed_prefix:])
                signatures = [row["signature"] for row in old[:completed_prefix]] + list(signatures[completed_prefix:])
            # 完全未变化的同步不重写进度，也不进入范围匹配；关键变更仍原子持久化。
            if (len(old) == len(entries) == len(signatures) and all(row.get("segment_key") for row in old)
                    and (not state.get("active") or (state["active"].get("segment_key") and state["active"].get("entry_snapshot")))
                    and all(row.get("entry") == entry and row.get("signature") == signature
                            for row, entry, signature in zip(old, entries, signatures))
                    and (template is None or state.get("template") == template)):
                return copy.deepcopy(state)
            for row in old:
                row.setdefault("segment_key", uuid.uuid4().hex)
            self._bind_active(state)
            old_cursor = int(state.get("cursor", 0))
            saved_position = self._position(state, old_cursor) if old and 0 <= old_cursor < len(old) else {}
            old_ranges = [self._entry_range(row.get("entry") or {}) for row in old]
            rows = [None] * len(entries)
            used = set()
            # 按旧表顺序建立索引；重复ID/范围仍取第一个未使用的旧段。
            from collections import defaultdict, deque
            by_id, by_range = defaultdict(deque), defaultdict(deque)
            for j, row in enumerate(old):
                by_id[row.get("entry", {}).get("id")].append(j)
                by_range[old_ranges[j]].append(j)
            def take(candidates):
                while candidates and candidates[0] in used:
                    candidates.popleft()
                return candidates.popleft() if candidates else None
            # 1) 同素材 ID：内容没变，连版本/选用/批准一起保留。
            for i, entry in enumerate(entries):
                j = take(by_id[entry.get("id")])
                if j is not None:
                    used.add(j)
                    rows[i] = self._carry_row(old[j], entry, signatures[i])
            # 2) 源范围完全相同：段号移位但内容没变（例如前面插入别的素材）。
            for i, entry in enumerate(entries):
                if rows[i] is not None:
                    continue
                key = self._entry_range(entry)
                j = take(by_range[key])
                if j is not None:
                    used.add(j)
                    rows[i] = self._carry_row(old[j], entry, signatures[i])
            # 3) 源范围有重叠：边界被移动/拆分/合并，旧版本保留但标“待更新”。
            for i, entry in enumerate(entries):
                if rows[i] is not None:
                    continue
                key = self._entry_range(entry)
                best, best_overlap = None, 0.0
                for j in range(len(old)):
                    if j in used:
                        continue
                    overlap = self._range_overlap(key, old_ranges[j])
                    if overlap > best_overlap + 1e-9:
                        best, best_overlap = j, overlap
                if best is not None and best_overlap > 1e-6:
                    used.add(best)
                    rows[i] = self._carry_row(old[best], entry, signatures[i], stale=True)
            # 4) 全新段：无旧结果，等待生成。
            for i, entry in enumerate(entries):
                if rows[i] is None:
                    rows[i] = {"entry": entry, "signature": signatures[i], "versions": [], "reroll": 0,
                               "segment_key": uuid.uuid4().hex,
                               "selected": None, "valid": False, "accepted": False, "boundary_changed": False}
            # 5) 被删掉的旧段归档，记录与文件不丢；不参与生成/合并。
            retired = [row for j, row in enumerate(old) if j not in used]
            if retired:
                state["retired_segments"] = (list(state.get("retired_segments") or []) + retired)[-200:]
            state["segments"] = rows
            if rows:
                state["cursor"] = self._saved_cursor(state, saved_position)
                active = state.get("active")
                if active:
                    for index, row in enumerate(rows):
                        if row["segment_key"] == active.get("segment_key"):
                            active["index"] = index
                            break
                affected = state.get("affected_run")
                if affected and affected.get("keys"):
                    affected["remaining"] = [i for i, row in enumerate(rows) if row["segment_key"] in affected["keys"]]
                hold = state.get("preview_hold")
                if hold:
                    hold["cursor"] = self._saved_cursor(state, {"segment_key": hold.get("cursor_key"), "cursor": hold["cursor"]})
                    hold["target"] = self._saved_cursor(state, {"segment_key": hold.get("target_key"), "cursor": hold["target"]})
            if template is not None:
                state["template"] = template
            if keep_completed and completed_prefix == len(rows) and rows:
                state.update(status="completed", cursor=len(rows)-1, active=None)
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
            raise ValueError("前一段尚未生成或依赖已失效，请先处理前段")
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
            try:
                parent = self._parent(state, index)
            except ValueError:
                parent = None
            state["active"] = {"token": token, "index": index,
                               "reroll":state["segments"][index].get("reroll",0),
                               "parent": parent,
                               "signature": state["segments"][index]["signature"]}
            self._bind_active(state)
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
        old_reset = state.get("reset_id", "legacy")
        self._invalidate(state, 0)
        if state.get("execution_round"):
            state["resume_round"] = copy.deepcopy(state["execution_round"])
        state.pop("execution_round", None)
        state.pop("draft_trial", None)
        state.pop("preview_hold", None)
        state.pop("sequential_stop", None)
        state.pop("run_return", None)
        state.pop("affected_run", None)
        state.pop("run_origins", None)
        state.pop("draft_candidates", None)
        for row in state["segments"]:
            row["selected"] = None
            row["reroll"] = 0
            # 清空重置/新建一组后一切从零：旧版本归档，仅供预览/历史查看，不再参与新一轮生成与选用。
            for version in row.get("versions") or []:
                if isinstance(version, dict):
                    version.setdefault("reset_id", old_reset)
                    version["archived"] = True
        state.update(cursor=0, status="ready", auto=False, active=None, pause_after=False,
                     epoch=state["epoch"] + 1,
                     reset_id=uuid.uuid4().hex,
                     error="")

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
                try:
                    parent = self._parent(state, index)
                except ValueError:
                    parent = None
                state["active"] = {
                    "token": fresh,
                    "index": index,
                    "reroll": state["segments"][index].get("reroll", 0),
                    "parent": parent,
                    "signature": state["segments"][index]["signature"],
                }
                self._bind_active(state)
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
                # 进度只增不减：停止后再开始、改完参数再开始、上一轮中断残留的 active，
                # 都只在当前游标处重新预约，不清空结果、不回退游标。
                if state.get("status") == "completed":
                    self._reset_round(state)
                state["active"] = None
                index = max(0, min(int(state.get("cursor", 0)), len(state["segments"]) - 1))
                try:
                    parent = self._parent(state, index)
                except ValueError:
                    parent = None
                fresh = uuid.uuid4().hex
                state["active"] = {"token": fresh, "index": index,
                                   "reroll": state["segments"][index].get("reroll", 0),
                                   "parent": parent,
                                   "signature": state["segments"][index]["signature"]}
                self._bind_active(state)
                state["status"] = "queued"
                state["error"] = ""
                token = fresh
                active = state["active"]
                self._save(state)
            if state["status"] not in {"queued", "running"}:
                raise ValueError(
                    "当前段的任务上下文已结束（常见于刚点过停止或重做），本次提交已取消；请重新点「开始」。"
                )
            if active.get("segment_key") and not any(row.get("segment_key") == active["segment_key"] for row in state["segments"]):
                state.update(status="error", active=None, auto=False, error="预约分段范围已改变，请重新运行所选段。")
                self._save(state)
                raise ValueError(state["error"])
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
        if (0 <= target < len(rows) and
                (not hold.get("target_key") or rows[target].get("segment_key") == hold["target_key"])):
            row = rows[target]
            if successful and hold.get("select_on_success"):
                # 仅顺序运行主动停止后重抽停止段：保留成功的新选用，等待用户批准；
                # 后续段结果不再失效（进度只增不减）。
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
                raise ValueError("本次生成结果已过期：任务上下文已变化（常见于生成过程中点过停止或重做）。文件已保存在会话目录，请从当前段重新开始。")
            target, row, current = self._result_target(state, active)
            version = {"id": token, "file": filename, "parent": active["parent"],
                       "signature": active["signature"], "created_at": time.time(),
                       "round_no":int(state.get("round_no",1)),"segment_index":int(active.get("index",target) or 0)+1}
            version.update(details or {})
            version["reset_id"] = state.get("reset_id", "legacy")
            row["versions"].append(version)
            row["selected"] = token
            row["valid"] = True
            row["accepted"] = False
            row["boundary_changed"] = False
            state["active"] = None
            if not current:
                row["valid"] = False
                state.update(status="error", auto=False, error="本次生成范围已被切分或删除；结果已保留在旧段记录，请重新运行所选段。")
                return self._save(state)
            if self._restore_preview_hold(state, successful=True):
                return self._save(state)
            state["status"] = "review"
            if state.get("auto_accept") and not state.get("pause_after"):
                # 生成即通过：新界面不再需要「同意批准」步骤，完成后自动通过并前进一段。
                self._accept(state)
            elif state["auto"] and not state["pause_after"]:
                self._accept(state)
            elif state["pause_after"]:
                state["status"] = "paused"
                state["auto"] = False
                state["pause_after"] = False
            return self._save(state)

    def register_result(self, session_id, filename, details=None, context=None):
        """外部工作流产出的视频在此登记为当前轮结果（等于自动 Commit），供合并与 A/B 使用。

        进度只增不减：停止/改参数/中断后再跑，只要段号有效且不是显式清空后的旧结果，
        就作为该段的新版本追加，不再报“任务已过期”。
        """
        with self.lock(session_id):
            state = self._load(session_id)
            if not isinstance(context, dict) or not isinstance(context.get("index"), int):
                raise ValueError("插件内部错误：生成结果缺少段号上下文，本次结果未纳入进度（文件已保留）。请从当前段重新开始；若反复出现请反馈日志。")
            # 唯一真正的过期：期间被“清空全部进度/重启”重置过（epoch 变化）。
            if context.get("epoch") is not None and int(context["epoch"]) != int(state.get("epoch", 0)):
                raise ValueError("本次生成结果未纳入进度：生成期间进度被停止或清空重置。文件已保留，可在预览或会话目录查看。")
            active = state.get("active")
            matched = bool(active) and context.get("token") == active.get("token")
            reserved = (state.get("run_origins") or {}).get(context.get("token"), {})
            origin = {**reserved, **context, **active} if matched else {**reserved, **context}
            index, row, current = self._result_target(state, origin)
            signature = origin.get("signature") or row["signature"]
            try:
                parent = self._parent(state, index) if current else origin.get("parent")
            except ValueError:
                parent = None
            version = {"id": uuid.uuid4().hex, "file": filename,
                       "parent": parent,
                       "signature": signature or row["signature"], "created_at": time.time(),
                       "round_no":int(state.get("round_no",1)),"segment_index":int(origin.get("index",index) or 0)+1}
            version.update(details or {})
            version["reset_id"] = state.get("reset_id", "legacy")
            row["versions"].append(version)
            row["selected"] = version["id"]
            row["valid"] = True
            row["accepted"] = bool(state.get("auto_accept"))
            row["boundary_changed"] = False
            if not current:
                row["valid"] = False
                if matched:
                    state.update(active=None, status="error", auto=False,
                                 error="本次生成范围已被切分或删除；结果已保留在旧段记录，请重新运行所选段。")
                return self._save(state)
            if matched:
                state["active"] = None
                if not self._restore_preview_hold(state, successful=True):
                    if state.get("auto_accept"):
                        current_row = state["segments"][state["cursor"]]
                        if current_row.get("valid") and current_row.get("selected"):
                            self._accept(state)
                        else:
                            state["status"] = "ready"
                    else:
                        state["status"] = "review"
            else:
                # 停止/改参数/中断后才到达的结果：只追加版本并等待审核，不动当前运行状态。
                state["error"] = ""
                if not state.get("auto_accept") and state["status"] not in {"queued", "running"} and not state.get("preview_hold"):
                    state["status"] = "review"
                self._restore_preview_hold(state, successful=True)
            return self._save(state)

    def _accept(self, state):
        segments = state["segments"]
        if state["cursor"] >= len(segments):
            if state.get("status") == "completed":
                raise ValueError(blocked_state_message("completed"))
            state["cursor"] = max(0, len(segments) - 1)
        elif state["cursor"] < 0:
            state["cursor"] = 0
        row = segments[state["cursor"]]
        if not row["valid"] or not row["selected"]:
            raise ValueError("当前段还没有有效结果，无法继续；请先生成本段。")
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
                state.pop("run_return", None)
                state.pop("affected_run", None)
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
                if command == "new_group":
                    raise ValueError("请先停止当前任务，再新建一组")
                raise ValueError("当前任务正在运行，请等待本段完成，或先点「停止」再操作。")
            elif command == "settings":
                from .finishing import merge_options
                current = merge_options(state.get("merge_options"))
                updated = merge_options({**current, **options["merge_options"]})
                state["merge_options"] = updated
                if any(updated[key] != current[key] for key in ("mode","seams","color_match","grain","grain_seed")):
                    state["merged"] = None
                    state["manifest"] = None
            elif command == "approve":
                current = state["cursor"]
                self._accept(state)
                state.pop("sequential_stop", None)
                saved = state.pop("run_return", None)
                if saved:
                    # “运行此段”结束并批准后回到跳段前的位置（按段内容匹配，切分后也能回对）。
                    state.update(cursor=self._saved_cursor(state, saved),
                                 status=str(saved.get("status") or "ready"),
                                 auto=bool(saved.get("auto", False)),
                                 pause_after=bool(saved.get("pause_after", False)))
                affected = state.get("affected_run")
                if affected:
                    remaining = [int(index) for index in affected.get("remaining", []) if int(index) > current]
                    affected["keys"] = [state["segments"][i]["segment_key"] for i in remaining]
                    back = affected.get("return") or {}
                    if remaining:
                        state["affected_run"]["remaining"] = remaining
                        state.update(cursor=remaining[0], status="ready", auto=False, active=None, pause_after=False)
                    else:
                        state.pop("affected_run", None)
                        state.update(cursor=self._saved_cursor(state, back),
                                     status=str(back.get("status") or "ready"),
                                     auto=bool(back.get("auto", False)),
                                     pause_after=bool(back.get("pause_after", False)))
            elif command == "redo":
                target = state["cursor"]
                if options.get("index") is not None:
                    target = int(options["index"])
                if not 0 <= target < len(state["segments"]):
                    raise ValueError("无效段号或当前段已结束；请在预览中点选要重做的段。")
                # 重抽指定段：旧版本保留、后续段结果保留；只把目标段标为待重生成并切游标。
                row = state["segments"][target]
                row["reroll"] = row.get("reroll", 0) + 1
                row["valid"] = False
                row["accepted"] = False
                state.pop("run_return", None)
                state.pop("affected_run", None)
                state.update(cursor=target, status="ready", auto=False, active=None, pause_after=False)
            elif command in {"run_segment", "run_pending"}:
                state.pop("affected_run", None)
                # 方案 A：只跑指定段（新拆分出的段也能直接跑）。记住原位置，通过后回去。
                if command == "run_pending":
                    target = next((i for i, row in enumerate(state["segments"])
                                   if not (row.get("valid") and row.get("accepted"))), None)
                    if target is None:
                        raise ValueError("没有待生成或待更新的段")
                else:
                    target = state["cursor"]
                    if options.get("index") is not None:
                        target = int(options["index"])
                    if not 0 <= target < len(state["segments"]):
                        raise ValueError("无效段号或当前段已结束；请先点「清空重置」或选择要运行的段。")
                current_row = state["segments"][min(max(int(state["cursor"]), 0), len(state["segments"]) - 1)] if state["segments"] else {}
                state["run_return"] = {"cursor": state["cursor"], "status": state["status"],
                                       "segment_key": current_row["segment_key"],
                                       "auto": bool(state.get("auto", False)),
                                       "pause_after": bool(state.get("pause_after", False)),
                                       "entry_id": (current_row.get("entry") or {}).get("id")}
                row = state["segments"][target]
                row["reroll"] = row.get("reroll", 0) + 1
                row["valid"] = False
                row["accepted"] = False
                state.update(cursor=target, status="ready", auto=False, active=None, pause_after=False, error="")
            elif command == "run_affected":
                # 自动重跑所有受影响段（切分/拉伸/延续/换素材导致的新段或待更新段）。
                if "indices" in options:
                    indices = options["indices"]
                    if (not isinstance(indices, list) or not indices or any(type(i) is not int or
                            not 0 <= i < len(state["segments"]) for i in indices)):
                        raise ValueError("请选择有效的分段后运行")
                    affected = sorted(set(indices))
                else:
                    affected = [index for index, row in enumerate(state["segments"])
                                if row.get("boundary_changed") or not row.get("valid") or not row.get("versions")]
                if not affected:
                    raise ValueError("没有需要重跑的段（切分/素材没有变化，或所有段都已通过）")
                current_row = state["segments"][min(max(int(state["cursor"]), 0), len(state["segments"]) - 1)] if state["segments"] else {}
                state.pop("run_return", None)
                state["affected_run"] = {
                    "remaining": affected,
                    "keys": [state["segments"][i]["segment_key"] for i in affected],
                    "return": {"cursor": state["cursor"], "status": state["status"],
                               "segment_key": current_row["segment_key"],
                               "auto": bool(state.get("auto", False)),
                               "pause_after": bool(state.get("pause_after", False)),
                               "entry_id": (current_row.get("entry") or {}).get("id")},
                }
                for index in affected:
                    row = state["segments"][index]
                    row["reroll"] = row.get("reroll", 0) + 1
                    row["valid"] = False
                    row["accepted"] = False
                state.update(cursor=affected[0], status="ready", auto=False, active=None, pause_after=False, error="")
            elif command == "redo_preview":
                target = state["cursor"]
                if options.get("index") is not None:
                    target = int(options["index"])
                if not 0 <= target < len(state["segments"]):
                    raise ValueError("无效段号或当前段已结束；请在预览中点选要抽卡的段。")
                cursor_index = min(max(int(state["cursor"]), 0), len(state["segments"]) - 1)
                if state.get("preview_hold"):
                    raise ValueError("本段抽卡进行中，请等结果出来再抽。")
                # 本段抽卡只生成预览备选：不失效后续段、不动选用版本与循环游标，抽完恢复原循环位置。
                row = state["segments"][target]
                state["preview_hold"] = {
                    "target_key": row["segment_key"],
                    "cursor_key": state["segments"][cursor_index]["segment_key"],
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
                if state["cursor"] >= len(state["segments"]):
                    if state.get("status") == "completed":
                        raise ValueError(blocked_state_message("completed"))
                    state["cursor"] = max(0, len(state["segments"]) - 1)
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
            elif command == "new_group":
                if state.get("status") in {"queued", "running", "merging"}:
                    raise ValueError("请先停止当前任务，再新建一组")
                from .group_history import archive
                archive(self,state)
                next_group=max(int(state.get("group_seq",1)),int(state.get("round_no",1)))+1
                self._reset_round(state)
                for row in state["segments"]:row["versions"]=[]
                state.update(round_no=next_group,group_seq=next_group)
            elif command == "select":
                index = int(options["index"])
                if not 0 <= index < len(state["segments"]):
                    raise ValueError("无效段号")
                row = state["segments"][index]
                version = next((v for v in row["versions"] if v["id"] == options["version"]), None)
                if not version:
                    raise ValueError("此版本不存在，不能选用")
                # 进度只增不减：旧参数生成的版本仍可选用，后续段结果保留。
                if version.get("archived"):
                    raise ValueError("该版本属于已重置的轮次，仅作历史查看；请用新一轮生成结果。")
                row.update(selected=version["id"], valid=True, accepted=False)
                state.update(cursor=index, status="review", auto=False, active=None)
            elif command == "start":
                if state["status"] in {"queued", "running", "merging"} and state.get("active"):
                    raise ValueError("当前段正在运行，请等待完成或先点「停止」再开始。")
                if state["status"] == "completed":
                    if not self._batch_path(state):
                        raise ValueError(blocked_state_message("completed"))
                    self._reset_round(state)
                if state["cursor"] >= len(state["segments"]):
                    state["cursor"] = max(0, len(state["segments"]) - 1)
                row = state["segments"][state["cursor"]]
                if row["valid"] and row["selected"]:
                    raise ValueError("当前段已有生成结果：点「开始」会接受该结果并继续，或点「分段循环」连续生成。")
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
        for row in state["segments"]:
            selected = next((v for v in row["versions"] if v["id"] == row["selected"]), None)
            # 进度只增不减：允许不同参数下生成的版本继续合并，只要求该段有已通过的有效选用。
            if not row["valid"] or not row["accepted"] or not selected:
                raise ValueError("仍有未生成或依赖失效的片段，暂不能合并")
            path = self.session_file(state["id"], selected["file"])
            if not path.is_file():
                raise ValueError("已选用版本的文件缺失，请对对应段点「本段抽卡」重新生成。")
            files.append(path)
        return files


STORE = SessionStore()

