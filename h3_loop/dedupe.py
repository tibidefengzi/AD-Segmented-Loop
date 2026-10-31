"""内容哈希去重：同内容只占一份磁盘空间，文件名仍各自可见。

策略：
- 同卷优先硬链接（Windows NTFS 支持），跨卷或文件系统不支持时回退为复制；
- 索引文件 <数据根>/.content-index.json 只用于加速（sha256 → 路径），命中时校验文件仍在；
- 被去重的文件名全部保留（生成目录仍能看到每一次结果），只是共享同一份数据。
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import threading
from pathlib import Path

LOCK = threading.RLock()
_INDEX: dict | None = None
_INDEX_ROOT: Path | None = None
INDEX_NAME = ".content-index.json"


def file_hash(path: Path, chunk: int = 4 * 1024 * 1024) -> str:
    value = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while block := handle.read(chunk):
            value.update(block)
    return value.hexdigest()


def _index_path() -> Path | None:
    try:
        from .storage import ROOT
        return Path(ROOT) / INDEX_NAME
    except Exception:
        return None


def _load() -> dict:
    global _INDEX, _INDEX_ROOT
    path = _index_path()
    with LOCK:
        if _INDEX is not None and _INDEX_ROOT == path:
            return _INDEX
        data = {}
        if path is not None and path.is_file():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    data = {str(key): str(value) for key, value in loaded.items()}
            except (OSError, ValueError):
                data = {}
        _INDEX, _INDEX_ROOT = data, path
        return _INDEX


def _save(index: dict) -> None:
    path = _index_path()
    if path is None:
        return
    try:
        from .storage import atomic_json
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_json(path, index)
    except Exception:
        pass


def canonical_for(hash_value: str) -> Path | None:
    """查已存在同内容文件；索引失效（文件被删/改名）时清理后返回 None。"""
    index = _load()
    value = index.get(hash_value)
    if not value:
        return None
    path = Path(value)
    try:
        if path.is_file() and file_hash(path) == hash_value:
            return path
    except OSError:
        pass
    index.pop(hash_value, None)
    _save(index)
    return None


def remember(hash_value: str, path: Path) -> None:
    index = _load()
    if index.get(hash_value) == str(path):
        return
    index[hash_value] = str(path)
    # 简单上限，避免索引无限增长；超限时只保留最近 5000 条
    if len(index) > 20000:
        index = dict(list(index.items())[-5000:])
        global _INDEX
        _INDEX = index
    _save(index)


def _same_volume(left: Path, right: Path) -> bool:
    try:
        return os.stat(left).st_dev == os.stat(right.parent if not right.exists() else right).st_dev
    except OSError:
        try:
            return Path(left).anchor.lower() == Path(right).anchor.lower()
        except Exception:
            return True


def link_or_copy(source: Path, target: Path) -> Path:
    """尽量硬链接，失败退回复制；目标已存在则直接返回。"""
    source, target = Path(source), Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        return target
    try:
        os.link(source, target)
        return target
    except FileExistsError:
        return target
    except OSError:
        return _exclusive_copy(source, target)


def _exclusive_copy(source: Path, target: Path) -> Path:
    """跨卷回退也独占创建，不覆盖并发发布的已有文件。"""
    try:
        output = target.open("xb")
    except FileExistsError:
        return target
    try:
        with output, source.open("rb") as incoming:
            shutil.copyfileobj(incoming, output)
        shutil.copystat(source, target)
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    return target


def relink_to_canonical(source: Path, canonical: Path) -> bool:
    """把 source 原子替换成 canonical 的硬链接（同卷才做）；失败保持原文件。"""
    source, canonical = Path(source), Path(canonical)
    try:
        if source.resolve() == canonical.resolve():
            return True
        if source.stat().st_size != canonical.stat().st_size:
            return False
        if not _same_volume(source, canonical):
            return False
        temporary = source.with_name(source.name + ".dedupe.tmp")
        temporary.unlink(missing_ok=True)
        os.link(canonical, temporary)
        os.replace(temporary, source)
        return True
    except OSError:
        try:
            Path(str(source) + ".dedupe.tmp").unlink(missing_ok=True)
        except OSError:
            pass
        return False


def publish(source: Path, target: Path) -> Path:
    """把 source 的内容发布为 target 文件名；同内容优先复用已有文件的数据块。"""
    source, target = Path(source), Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        return target
    try:
        hash_value = file_hash(source)
    except OSError:
        return link_or_copy(source, target)
    canonical = canonical_for(hash_value)
    if canonical is not None:
        relink_to_canonical(source, canonical)
        try:
            os.link(canonical, target)
            return target
        except FileExistsError:
            return target
        except OSError:
            return _exclusive_copy(canonical, target)
    remember(hash_value, source)
    return link_or_copy(source, target)
