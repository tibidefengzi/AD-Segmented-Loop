"""Thin Continuum interop — soft-detect; never vendor Continuum source.

Continuum >=3.10 hides V1 Join/Finish from public NODE_CLASS_MAPPINGS but keeps
the classes on its nodes module. Soft-import those classes by file path + isclass.
"""
from __future__ import annotations

import inspect
import re
import sys
from pathlib import Path
from typing import Any


def _continuum_package_roots() -> list[Path]:
    roots: list[Path] = []
    try:
        import folder_paths
        base = Path(getattr(folder_paths, "base_path", "") or "")
        if base.is_dir():
            roots.append(base / "custom_nodes" / "ComfyUI-H3-Continuum")
    except Exception:
        pass
    for mod in list(sys.modules.values()):
        file = getattr(mod, "__file__", None) or ""
        norm = file.replace("\\", "/")
        if "/ComfyUI-H3-Continuum/" in norm:
            roots.append(Path(file).resolve().parent)
    seen: set[str] = set()
    out: list[Path] = []
    for root in roots:
        root = Path(root)
        if root.is_file():
            root = root.parent
        if not (root / "nodes.py").exists() and (root.parent / "nodes.py").exists():
            root = root.parent
        key = str(root.resolve()) if root.exists() else str(root)
        if key not in seen and (root / "nodes.py").exists():
            seen.add(key)
            out.append(root)
    return out


def _continuum_nodes_module():
    for name, mod in list(sys.modules.items()):
        if mod is None:
            continue
        file = (getattr(mod, "__file__", None) or "").replace("\\", "/")
        if not file.endswith("/ComfyUI-H3-Continuum/nodes.py"):
            continue
        join = getattr(mod, "H3ContinuumJoin", None)
        finish = getattr(mod, "H3ContinuumFinish", None)
        if inspect.isclass(join) and inspect.isclass(finish):
            return mod
    for name, mod in list(sys.modules.items()):
        file = (getattr(mod, "__file__", None) or "").replace("\\", "/")
        if not file.endswith("/ComfyUI-H3-Continuum/__init__.py"):
            continue
        nodes_mod = sys.modules.get(f"{name}.nodes") or getattr(mod, "nodes", None)
        if nodes_mod is None:
            continue
        join = getattr(nodes_mod, "H3ContinuumJoin", None)
        finish = getattr(nodes_mod, "H3ContinuumFinish", None)
        if inspect.isclass(join) and inspect.isclass(finish):
            return nodes_mod
    return None


def continuum_available() -> bool:
    return continuum_loaded() or bool(_continuum_package_roots())


def continuum_loaded() -> bool:
    return _continuum_nodes_module() is not None


def continuum_status_report() -> str:
    if continuum_loaded():
        return (
            "Continuum 已就绪（软导入 Join/Finish 类；>=3.10 可不在节点菜单显示它们）。"
            "用「AD-H3-Continuum续段准备 / 入库」走潜空间续段；未接时仍可像素交接。"
        )
    if _continuum_package_roots():
        return (
            "已找到 ComfyUI-H3-Continuum 目录，但进程内尚未加载 Join/Finish 类。"
            "请重启 ComfyUI；若仍失败，检查 Continuum 是否加载报错。"
        )
    return (
        "未检测到 ComfyUI-H3-Continuum。请安装到 custom_nodes 后重启；当前仅能使用像素级交接。"
    )


def require_continuum() -> None:
    if not continuum_loaded():
        raise ValueError(continuum_status_report())


def continuum_node(class_id: str):
    require_continuum()
    mod = _continuum_nodes_module()
    cls = getattr(mod, class_id, None)
    if not inspect.isclass(cls):
        try:
            import nodes as comfy_nodes
            cls = (getattr(comfy_nodes, "NODE_CLASS_MAPPINGS", {}) or {}).get(class_id)
        except Exception:
            cls = None
    if not inspect.isclass(cls):
        raise ValueError(
            f"Continuum 已加载但找不到类 {class_id}。"
            "请确认 ComfyUI-H3-Continuum 源码仍包含 V1 Join/Finish。"
        )
    return cls


def session_continuum_dir(session_dir: Path) -> Path:
    path = Path(session_dir) / "continuum"
    path.mkdir(parents=True, exist_ok=True)
    return path


def state_path(session_dir: Path, segment_index: int) -> Path:
    return session_continuum_dir(session_dir) / f"segment_{int(segment_index) + 1:04d}.pt"


def capture_metadata(session_dir: Path, context: dict, progress: dict) -> dict | None:
    """Trust only a capture made by this exact reserved run, never infer from frame count."""
    import json
    from .storage import contained
    token = context.get("token", "")
    if not token or not re.fullmatch(r"[A-Za-z0-9_-]{1,96}", token):
        return None
    name = f"continuum/segment_{int(context.get('index', 0)) + 1:04d}_{token}.pt"
    path = contained(Path(session_dir), name)
    try:
        record = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    active = progress.get("active") or {}
    if not path.is_file() or active.get("token") != token:
        return None
    if any(record.get(key) != value for key, value in {
        "token": token, "index": context.get("index"), "epoch": progress.get("epoch", 0),
        "signature": active.get("signature"), "parent": active.get("parent"),
    }.items()):
        return None
    return {**record, "file": name}


def save_continuum_state(state: dict[str, Any], session_dir: Path, segment_index: int, context: dict) -> str:
    import torch
    from .storage import STORE, atomic_json, contained, identifier
    progress = STORE.read(context["session_id"])
    active = progress.get("active") or {}
    if active.get("token") != context["token"] or active.get("index") != segment_index or progress["status"] != "running":
        raise ValueError("Continuum 入库任务已过期，请从当前段重新执行")
    token = identifier(context["token"])
    path = contained(session_continuum_dir(session_dir), f"segment_{segment_index + 1:04d}_{token}.pt")
    payload = {
        key: (value.detach().cpu().contiguous() if hasattr(value, "detach") else value)
        for key, value in dict(state).items()
    }
    with path.open("xb") as handle:
        torch.save(payload, handle)
    atomic_json(path.with_suffix(".json"), {"token": token, "index": segment_index,
                "epoch": progress.get("epoch", 0), "signature": active["signature"], "parent": active["parent"]})
    return str(path)



def _align_frame_count_up(frame_count: int) -> int:
    """Match ComfyUI-H3-Continuum.temporal.align_frame_count_up (17k+5)."""
    n = max(5, int(frame_count))
    remainder = (n - 5) % 17
    return n if remainder == 0 else n + (17 - remainder)


def _align_frame_count_nearest(frame_count: int, *, minimum: int = 5) -> int:
    """Match ComfyUI-H3-Continuum.temporal.align_frame_count_nearest."""
    target = max(int(frame_count), int(minimum), 5)
    min_valid = _align_frame_count_up(minimum)
    k_floor = max(0, (target - 5) // 17)
    low = max(5 + 17 * k_floor, min_valid)
    high = max(low + 17, min_valid)
    return low if abs(target - low) < abs(high - target) else high


def extension_net_frames(context_frames: int, extend_seconds: float) -> int:
    """Net new frames after Continuum Join+Finish trim (same math as make_extension_shape)."""
    from .spec import FPS
    context_frames = int(context_frames)
    requested_new = max(1, int(round(float(extend_seconds) * FPS)))
    total = _align_frame_count_nearest(context_frames + requested_new, minimum=context_frames + 1)
    net = total - context_frames
    if net < 1:
        raise RuntimeError("computed continuation contains no new frames")
    return net


def ceil_extend_seconds(context_frames: int, extend_seconds: float, min_net_frames: int) -> tuple[float, int, bool]:
    """Bump extend_seconds so Continuum net_new_frames >= min_net_frames.

    Continuum make_extension_shape uses align_frame_count_nearest, which can round
    DOWN. Wiring Segment.seconds_float -> extend_seconds then undershoots Commit
    target_frames (e.g. continuity=22, ~5.17s -> net 119 < 124). Prefer aligning
    UP so automatic multi-segment Continuum loops need no manual extend tuning.
    Returns (seconds, net_frames, bumped).
    """
    from .spec import FPS
    context_frames = int(context_frames)
    min_net = max(1, int(min_net_frames))
    seconds = float(extend_seconds)
    net = extension_net_frames(context_frames, seconds)
    if net >= min_net:
        return seconds, net, False
    needed_total = _align_frame_count_up(context_frames + min_net)
    needed_net = needed_total - context_frames
    bumped = needed_net / FPS
    net2 = extension_net_frames(context_frames, bumped)
    # Safety: nearest must not undershoot after the up-align bump.
    while net2 < min_net:
        bumped += 1.0 / FPS
        net2 = extension_net_frames(context_frames, bumped)
        if bumped > 15.0 + 1e-9:
            raise ValueError(
                f"Continuum extend_seconds raised still short of target frames: need >= {min_net}, "
                f"continuity={context_frames}, tried up to {bumped:.4f}s (cap 15s)"
            )
    return bumped, net2, True


def load_continuum_state(session_dir: Path, segment_index: int) -> dict[str, Any] | None:
    import json
    from .storage import contained
    try:
        progress = json.loads((Path(session_dir) / "progress.json").read_text(encoding="utf-8"))
        row = progress["segments"][segment_index]
        version = next((v for v in row["versions"] if v["id"] == row["selected"]), None)
        if not row["valid"] or not row["accepted"] or not version or version["signature"] != row["signature"]:
            return None
        if segment_index and version["parent"] != progress["segments"][segment_index - 1]["selected"]:
            return None
        if not version.get("continuum_file"):
            return None  # Legacy unbound states remain on disk, but cannot silently seed a new run.
        path = contained(Path(session_dir), version["continuum_file"])
        record = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
        if record["token"] != version["id"] or record["signature"] != row["signature"] or record["parent"] != version["parent"] or record["epoch"] != progress.get("epoch", 0):
            return None
    except (OSError, ValueError, KeyError, IndexError):
        return None
    if not path.is_file():
        return None
    import torch
    return torch.load(path, map_location="cpu", weights_only=False)
