"""插件存储统计、导入额度及仅针对可重建文件的缓存清理。

性能约定：
- 引用保护用“文件名/绝对路径集合”做字符串匹配，不再对每个字符串做 resolve+is_file；
- 引用集合按 progress.json 的 (路径, mtime, 大小) 签名缓存，仅在进度变化时重算；
- 导入额度校验每批并发 ImportReservation 共享基线与已提交用量，上传分块不再反复全盘扫描。
"""
from __future__ import annotations
import json, math, os, re, shutil, threading, time
from pathlib import Path
from .storage import ROOT, data_roots

CACHE_MIN_AGE = 600
_LOCK = threading.RLock()
_RESERVED = 0
_IMPORT_BASELINE = None
_IMPORT_ACTIVE = 0
_IMPORT_COMMITTED = 0
_PROTECTED_CACHE = None          # ((signature...), (names, absolute, media_relative))
_PROTECTED_LOCK = threading.Lock()

class StorageLimitError(ValueError):
    pass

def default_limit_gb():
    try: value = float(os.environ.get('AIDEN_H3_MEDIA_LIMIT_GB', '5'))
    except ValueError: value = 5.0
    return value if math.isfinite(value) and value > 0 else 5.0

def enforce_limit():
    return os.environ.get('AIDEN_H3_MEDIA_LIMIT_ENFORCE', '1').lower() not in {'0','false','off'}

def import_root():
    """导入素材库根（新目录优先；旧目录存在且新目录尚未创建时兼容统计旧库）。"""
    try:
        from . import layout
        libraries = layout.input_libraries()
    except Exception:
        return None
    for library in libraries:
        if library.is_dir():
            return library
    return libraries[0] if libraries else None

def _roots():
    # 指定独立数据根时，不清理兼容回退目录。
    return [ROOT] if os.environ.get('AIDEN_H3_DATA_DIR') else data_roots()

def _files(root):
    if not root.is_dir(): return
    pending = [root.resolve()]
    while pending:
        directory = pending.pop()
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    try:
                        info = entry.stat(follow_symlinks=False)
                        if entry.is_symlink() or getattr(info, 'st_file_attributes', 0) & 0x400: continue
                        if entry.is_dir(follow_symlinks=False): pending.append(Path(entry.path))
                        elif entry.is_file(follow_symlinks=False):
                            # Windows 的 DirEntry.stat 不返回真实 inode（为 0），
                            # 占用统计要按硬链接去重，必须用 Path.stat() 再取一次。
                            yield Path(entry.path), Path(entry.path).stat()
                    except OSError: continue
        except OSError: continue

def _dir_usage(root, seen=None):
    """按实际数据块统计：硬链接去重后的多个文件名只算一次占用。"""
    seen = set() if seen is None else seen
    total, count = 0, 0
    for _, info in _files(root):
        key = (getattr(info, 'st_dev', 0), getattr(info, 'st_ino', 0))
        if key[1] and key in seen:
            continue
        if key[1]:
            seen.add(key)
        total += info.st_size
        count += 1
    return total, count

def _session_signature():
    """progress.json 的轻量签名：只有进度文件变化时才需要重算引用集合。"""
    parts = []
    for root in _roots():
        sessions = root / 'sessions'
        if not sessions.is_dir(): continue
        try:
            with os.scandir(sessions) as entries:
                for entry in entries:
                    try:
                        if not entry.is_dir(follow_symlinks=False): continue
                        progress = Path(entry.path) / 'progress.json'
                        info = progress.stat()
                        parts.append((str(progress), info.st_mtime_ns, info.st_size))
                    except OSError: continue
        except OSError: continue
    return tuple(sorted(parts))

def _protected_refs():
    """返回引用集合 (文件名, 绝对路径, media 相对路径)；None 表示有任务在跑/进度不可读。"""
    global _PROTECTED_CACHE
    signature = _session_signature()
    with _PROTECTED_LOCK:
        cached = _PROTECTED_CACHE
    if cached is not None and cached[0] == signature:
        return cached[1]
    names, absolute, media_relative = set(), set(), set()
    def visit(value):
        if isinstance(value, dict):
            for item in value.values(): visit(item)
        elif isinstance(value, list):
            for item in value: visit(item)
        elif isinstance(value, str):
            if value.startswith(('{', '[')):
                try: visit(json.loads(value))
                except (ValueError, RecursionError): pass
            text = value.replace('\\', '/').strip()
            if not text: return
            normalized = os.path.normpath(text).replace('\\', '/')
            if os.path.isabs(text) or re.match(r'^[A-Za-z]:/', text):
                absolute.add(normalized)
                names.add(text.rsplit('/', 1)[-1])
            else:
                names.add(text.rsplit('/', 1)[-1])
                if '/' in text: media_relative.add(text.lstrip('./'))
    for root in _roots():
        sessions = root / 'sessions'
        if not sessions.is_dir(): continue
        try:
            with os.scandir(sessions) as entries:
                for entry in entries:
                    try:
                        if not entry.is_dir(follow_symlinks=False): continue
                        progress = Path(entry.path) / 'progress.json'
                        if not progress.is_file(): continue
                        state = json.loads(progress.read_text(encoding='utf-8'))
                    except OSError: continue
                    except (ValueError, AttributeError, RecursionError): return None
                    if not isinstance(state, dict): return None
                    if state.get('status') in {'queued', 'running', 'merging'}: return None
                    visit(state)
        except OSError: continue
    refs = (frozenset(names), frozenset(absolute), frozenset(media_relative))
    with _PROTECTED_LOCK:
        _PROTECTED_CACHE = (signature, refs)
    return refs

def _protected():
    """兼容入口：返回被引用的绝对路径集合（不做逐字符串文件系统探测）。"""
    refs = _protected_refs()
    if refs is None: return None
    names, absolute, _media_relative = refs
    protected = {Path(value) for value in absolute}
    for root in _roots():
        sessions = root / 'sessions'
        if not sessions.is_dir(): continue
        for session in sessions.iterdir():
            if not session.is_dir(): continue
            for name in names:
                candidate = session / name
                if candidate.is_file(): protected.add(candidate)
    return protected

def _is_protected(path, root, refs):
    names, absolute, media_relative = refs
    if str(path).replace('\\', '/') in absolute: return True
    try: relative = path.relative_to(root).as_posix()
    except ValueError: return False
    if relative.startswith('media/'):
        return relative[len('media/'):] in media_relative or path.name in names
    return path.name in names

def _allowed_relative(relative: str) -> bool:
    parent, _, name = relative.rpartition('/')
    if parent == 'media/derived':
        return bool(re.fullmatch(r'muted_[0-9a-f]{24}\.(mp4|mkv|mov|webm|avi|m4v)', name)
                    or re.fullmatch(r'[A-Za-z0-9_.-]+_[0-9]+\.wav', name))
    if parent == 'frame-cache': return bool(re.fullmatch(r'frames_[A-Za-z0-9_-]+\.npy', name))
    if parent == 'import-staging': return bool(re.fullmatch(r'[0-9a-f]{32}\.part', name))
    parts = relative.split('/')
    if len(parts) == 3 and parts[0] == 'sessions':
        return bool(re.fullmatch(r'(picture_|soundtrack_)[A-Za-z0-9_.-]+\.(mp4|m4a|wav|log)', name)
                    or re.fullmatch(r'progress\.json\.[0-9a-f]+\.tmp', name))
    return False

def _is_allowed_cache_file(path):
    path = Path(path)
    try:
        if path.is_symlink(): return False
    except OSError: return False
    for root in _roots():
        try: relative = path.relative_to(root).as_posix()
        except ValueError: continue
        return _allowed_relative(relative)
    return False

def plan_cache(min_age=CACHE_MIN_AGE):
    refs = _protected_refs()
    if refs is None: return {'entries':[],'bytes':0,'files':0,'blocked':True}
    entries = []; now = time.time()
    for root in _roots():
        for folder in [root/'media'/'derived', root/'frame-cache', root/'sessions', root/'import-staging']:
            for path, info in _files(folder):
                if now - info.st_mtime < min_age: continue
                try: relative = path.relative_to(root).as_posix()
                except ValueError: continue
                if not _allowed_relative(relative): continue
                if _is_protected(path, root, refs): continue
                entries.append({'path':str(path),'bytes':info.st_size,'mtime_ns':info.st_mtime_ns})
    return {'entries':entries,'bytes':sum(item['bytes'] for item in entries),'files':len(entries),'blocked':False}

def usage(include_cache=True):
    media_bytes=sessions_bytes=frame_bytes=staging_bytes=media_files=sessions_files=0
    seen = set()
    for root in _roots():
        size,count=_dir_usage(root/'media', seen); media_bytes+=size; media_files+=count
        size,count=_dir_usage(root/'sessions', seen); sessions_bytes+=size; sessions_files+=count
        size,_=_dir_usage(root/'frame-cache', seen); frame_bytes+=size
        for _, info in _files(root/'import-staging'):
            if time.time()-info.st_mtime < CACHE_MIN_AGE: continue
            key = (getattr(info, 'st_dev', 0), getattr(info, 'st_ino', 0))
            if key[1] and key in seen: continue
            if key[1]: seen.add(key)
            staging_bytes += info.st_size
    imported=import_root()
    import_bytes,import_files=_dir_usage(imported, seen) if imported else (0,0)
    used=media_bytes+sessions_bytes+frame_bytes+staging_bytes+import_bytes
    limit=int(default_limit_gb()*1024**3)
    return {'used_bytes':used,'limit_bytes':limit,'percent':round(used/limit,4),
        'over_limit':used>=limit,'enforce':enforce_limit(),'import_bytes':import_bytes,'import_files':import_files,
        'media_bytes':media_bytes,'media_files':media_files,'sessions_bytes':sessions_bytes,'sessions_files':sessions_files,
        'frame_cache_bytes':frame_bytes,'cache_bytes':plan_cache()['bytes'] if include_cache else 0,
        'pending_bytes':_RESERVED,'auto_clean':os.environ.get('AIDEN_H3_AUTO_CLEAN','safe')}

class ImportReservation:
    """并发批次共享基线；已提交字节仍计入额度，分块上传不重复扫描。"""
    def __init__(self):
        self.bytes = 0
        self.closed = False
        self.started = False

    def grow(self, count):
        global _RESERVED, _IMPORT_BASELINE, _IMPORT_ACTIVE
        count = max(0, int(count))
        with _LOCK:
            if self.closed:
                raise ValueError('导入额度已释放')
            current = _IMPORT_BASELINE if _IMPORT_ACTIVE else usage(False)
            if current['enforce'] and (current['over_limit'] or
                    current['used_bytes'] + _IMPORT_COMMITTED + _RESERVED + count > current['limit_bytes']):
                raise StorageLimitError(f'素材占用将超过 {default_limit_gb():g} GB 上限，请先清理缓存或整理素材后再导入')
            if not self.started:
                _IMPORT_BASELINE = current
                _IMPORT_ACTIVE += 1
                self.started = True
            _RESERVED += count
            self.bytes += count

    def close(self, *, committed=False):
        global _RESERVED, _IMPORT_BASELINE, _IMPORT_ACTIVE, _IMPORT_COMMITTED
        with _LOCK:
            if self.closed:
                return
            if self.started:
                _RESERVED -= self.bytes
                if committed:
                    _IMPORT_COMMITTED += self.bytes
                _IMPORT_ACTIVE -= 1
                if not _IMPORT_ACTIVE:
                    _IMPORT_BASELINE = None
                    _IMPORT_COMMITTED = 0
            self.closed = True


def copy_import(source,target):
    """失败只移除本次新建文件，不覆盖已有素材。"""
    source,target=Path(source),Path(target)
    reservation=ImportReservation(); created=False
    try:
        reservation.grow(source.stat().st_size)
        target.parent.mkdir(parents=True,exist_ok=True)
        with _LOCK:
            with target.open('xb') as output:
                created=True
                with source.open('rb') as incoming: shutil.copyfileobj(incoming,output)
            if target.stat().st_size != reservation.bytes:
                raise ValueError('导入时源素材大小发生变化，请稍后重试')
            shutil.copystat(source,target)
            reservation.close(committed=True)
    except Exception:
        if created: target.unlink(missing_ok=True)
        raise
    finally: reservation.close()

def clean_cache(confirmed=False,min_age=CACHE_MIN_AGE):
    with _LOCK:
        plan=plan_cache(min_age); removed=freed=0
        if confirmed and not plan['blocked']:
            for entry in plan['entries']:
                path=Path(entry['path'])
                try:
                    info=path.stat()
                    if info.st_mtime_ns!=entry['mtime_ns'] or not _is_allowed_cache_file(path): continue
                    path.unlink(); removed+=1; freed+=entry['bytes']
                except OSError: continue
        return {'dry_run':not confirmed,'files':plan['files'],'bytes':plan['bytes'],
            'removed_files':removed,'freed_bytes':freed,'blocked':plan['blocked']}

def auto_clean():
    if os.environ.get('AIDEN_H3_AUTO_CLEAN','safe').lower() in {'off','0','false','none'}: return None
    try: return clean_cache(True)
    except Exception as error:
        print(f'AD-Segmented-Loop: 自动清理缓存未执行：{error}')
        return None
