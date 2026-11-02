"""插件存储统计、导入额度及仅针对可重建文件的缓存清理。

性能约定：
- 引用保护用“文件名/绝对路径集合”做字符串匹配，不再对每个字符串做 resolve+is_file；
- 引用集合按 progress.json 的 (路径, mtime, 大小) 签名缓存，仅在进度变化时重算；
- 导入额度校验每批并发 ImportReservation 共享基线与已提交用量，上传分块不再反复全盘扫描。
"""
from __future__ import annotations
import json, math, os, re, shutil, stat, threading, time
from pathlib import Path
from .storage import ROOT, data_roots

CACHE_MIN_AGE = 600
_TRIAL_MIN_AGE = 60
_USAGE_CACHE = {}
_USAGE_CACHE_TTL = 2.0
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
                        if entry.is_symlink(): continue
                        # 一次 os.stat 同时拿到大小和 inode：Windows 的 DirEntry.stat 没有真实 inode，
                        # 原先“entry.stat + Path.stat”两次系统调用可以合并成一次。
                        info = os.stat(entry.path, follow_symlinks=False)
                        if getattr(info, 'st_file_attributes', 0) & 0x400: continue
                        if stat.S_ISDIR(info.st_mode): pending.append(Path(entry.path))
                        elif stat.S_ISREG(info.st_mode):
                            yield Path(entry.path), info
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
        # 抽卡试运行会话的进度/快照在完成后可回收；媒体文件保留供黄色预览卡继续播放。
        if parts[1].startswith('trial_') and parts[2] == 'progress.json':
            return bool(re.fullmatch(r'trial_[0-9a-f]+', parts[1]))
        return bool(re.fullmatch(r'(picture_|soundtrack_)[A-Za-z0-9_.-]+\.(mp4|m4a|wav|log)', name)
                    or re.fullmatch(r'progress\.json\.[0-9a-f]+\.tmp', name))
    if len(parts) == 4 and parts[0] == 'sessions':
        if parts[2] == 'rounds':
            return bool(re.fullmatch(r'[0-9a-f]{32}\.json', parts[3]))
        if parts[2].startswith('finishing_'):
            # 合并中间产物：正常合并结束会主动删除；崩溃/中断残留按可重建缓存清理。
            return bool(re.fullmatch(r'finished_\d+\.mp4', parts[3]))
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

def _invalidate_usage():
    with _LOCK:
        _USAGE_CACHE.clear()


def _active_trial_ids():
    """所有父会话 draft_trial 指向的试运行会话：清理时必须保留。"""
    active = set()
    for root in _roots():
        sessions = root / 'sessions'
        if not sessions.is_dir(): continue
        for parent in sessions.iterdir():
            if not parent.is_dir(): continue
            progress = parent / 'progress.json'
            if not progress.is_file(): continue
            try: state = json.loads(progress.read_text(encoding='utf-8'))
            except (OSError, ValueError): continue
            pending = state.get('draft_trial') or {}
            if pending.get('session_id'): active.add(str(pending['session_id']))
    return active


def plan_cache(min_age=CACHE_MIN_AGE):
    refs = _protected_refs()
    if refs is None: return {'entries':[],'bytes':0,'files':0,'blocked':True}
    active_trials = _active_trial_ids()
    entries = []; now = time.time()
    for root in _roots():
        for folder in [root/'media'/'derived', root/'frame-cache', root/'sessions', root/'import-staging']:
            for path, info in _files(folder):
                if now - info.st_mtime < min_age: continue
                try: relative = path.relative_to(root).as_posix()
                except ValueError: continue
                if not _allowed_relative(relative): continue
                parts = relative.split('/')
                if len(parts) >= 3 and parts[0] == 'sessions' and parts[1] in active_trials: continue
                if _is_protected(path, root, refs): continue
                entries.append({'path':str(path),'bytes':info.st_size,'mtime_ns':info.st_mtime_ns})
    return {'entries':entries,'bytes':sum(item['bytes'] for item in entries),'files':len(entries),'blocked':False}


def clean_frame_cache(min_age=_TRIAL_MIN_AGE):
    """任务边界回收超过 min_age 的参考帧临时文件；被内存映射占用时保留到下次。"""
    removed = freed = 0
    now = time.time()
    for root in _roots():
        folder = root / 'frame-cache'
        if not folder.is_dir(): continue
        for path, info in list(_files(folder)):
            if now - info.st_mtime < min_age: continue
            try: relative = path.relative_to(root).as_posix()
            except ValueError: continue
            if not _allowed_relative(relative): continue
            try:
                path.unlink()
                removed += 1; freed += info.st_size
            except OSError:
                continue
    if removed: _invalidate_usage()
    return {'removed_files': removed, 'freed_bytes': freed}


def clean_trial_progress(min_age=_TRIAL_MIN_AGE):
    """回收已完成抽卡试运行会话的进度与轮次快照；媒体文件保留，黄色预览卡仍可播放。"""
    removed = freed = 0
    now = time.time()
    for root in _roots():
        sessions = root / 'sessions'
        if not sessions.is_dir(): continue
        protected = _active_trial_ids()
        for trial in sessions.iterdir():
            if not trial.is_dir() or not re.fullmatch(r'trial_[0-9a-f]+', trial.name): continue
            if trial.name in protected: continue
            progress = trial / 'progress.json'
            if not progress.is_file(): continue
            try: state = json.loads(progress.read_text(encoding='utf-8'))
            except (OSError, ValueError): state = {}
            if state.get('status') in {'queued', 'running', 'merging'}: continue
            targets = [progress]
            rounds = trial / 'rounds'
            if rounds.is_dir(): targets.extend(rounds.glob('*.json'))
            for path in targets:
                if not path.is_file(): continue
                try: info = path.stat()
                except OSError: continue
                if now - info.st_mtime < min_age: continue
                try: relative = path.relative_to(root).as_posix()
                except ValueError: continue
                if not _allowed_relative(relative): continue
                try:
                    path.unlink()
                    removed += 1; freed += info.st_size
                except OSError:
                    continue
    if removed: _invalidate_usage()
    return {'removed_files': removed, 'freed_bytes': freed}


def _session_dirs():
    for root in _roots():
        sessions = root / 'sessions'
        if not sessions.is_dir():
            continue
        try:
            for entry in sessions.iterdir():
                if entry.is_dir() and not entry.is_symlink():
                    yield entry
        except OSError:
            continue


def _session_progress(session_dir):
    try:
        return json.loads((session_dir / 'progress.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


def _keep_version_ids(row, keep_recent):
    keep = set()
    if row.get('selected'):
        keep.add(str(row['selected']))
    ordered = sorted(row.get('versions') or [], key=lambda version: float(version.get('created_at') or 0), reverse=True)
    extra = 0
    for version in ordered:
        if str(version.get('id')) == str(row.get('selected')):
            continue
        if extra >= keep_recent:
            break
        keep.add(str(version.get('id')))
        extra += 1
    return keep


def version_plan(keep_recent=3, min_age=0.0, group_limit=10):
    """列出可回收的旧版本文件与超限组快照；只统计，不删除。"""
    now = time.time()
    files, groups, seen = [], [], set()
    versions = 0
    for session in _session_dirs():
        state = _session_progress(session)
        if not state or state.get('status') in {'queued', 'running', 'merging'}:
            continue
        rows = list(state.get('segments') or []) + list(state.get('retired_segments') or [])
        for row in rows:
            keep = _keep_version_ids(row, keep_recent)
            for version in row.get('versions') or []:
                if str(version.get('id')) in keep:
                    continue
                versions += 1
                for key in ('file', 'tail_file', 'audio_file'):
                    name = version.get(key)
                    if not name:
                        continue
                    path = session / str(name)
                    try:
                        info = path.stat()
                    except OSError:
                        continue
                    if min_age and now - info.st_mtime < min_age:
                        continue
                    marker = str(path)
                    if marker in seen:
                        continue
                    seen.add(marker)
                    files.append({'path': marker, 'bytes': info.st_size, 'mtime_ns': info.st_mtime_ns})
        snapshots = state.get('group_snapshots') or {}
        if len(snapshots) > group_limit:
            ordered = sorted(((int(number), str(name)) for number, name in snapshots.items()
                              if str(number).isdigit()), reverse=True)
            for _number, name in ordered[group_limit:]:
                path = session / name
                try:
                    info = path.stat()
                except OSError:
                    continue
                marker = str(path)
                if marker in seen:
                    continue
                seen.add(marker)
                groups.append({'path': marker, 'bytes': info.st_size, 'mtime_ns': info.st_mtime_ns})
    return {'files': files, 'groups': groups, 'versions': versions,
            'bytes': sum(item['bytes'] for item in files) + sum(item['bytes'] for item in groups)}


def _version_cache_bytes():
    try:
        return version_plan()['bytes']
    except Exception:
        return 0


def _prune_group_snapshots(group_limit=10):
    # 清理接口只在没有任务运行时调用，直接原子重写该会话进度，避免依赖内存中的 STORE 根目录。
    from .storage import atomic_json
    for session in list(_session_dirs()):
        state = _session_progress(session)
        if not state:
            continue
        snapshots = state.get('group_snapshots') or {}
        if len(snapshots) <= group_limit:
            continue
        ordered = sorted(((int(number), str(name)) for number, name in snapshots.items()
                          if str(number).isdigit()), reverse=True)
        for number, name in ordered[group_limit:]:
            try:
                (session / name).unlink()
            except OSError:
                pass
            snapshots.pop(str(number), None)
        state['group_snapshots'] = snapshots
        try:
            atomic_json(session / 'progress.json', state)
        except OSError:
            continue


def clean_versions(confirmed, keep_recent=3, min_age=0.0, group_limit=10):
    """删除非选用旧版本的成片/尾帧/音频与超限组快照；只保留选用版本和每段最近 N 个版本。"""
    plan = version_plan(keep_recent, min_age, group_limit)
    summary = {'dry_run': not confirmed, 'versions': plan['versions'],
               'files': len(plan['files']) + len(plan['groups']),
               'bytes': plan['bytes'], 'groups': len(plan['groups'])}
    if not confirmed:
        return summary
    removed = freed = 0
    for item in plan['files'] + plan['groups']:
        path = Path(item['path'])
        try:
            info = path.stat()
            if info.st_mtime_ns != item['mtime_ns']:
                continue
            path.unlink()
            removed += 1
            freed += item['bytes']
        except OSError:
            continue
    _prune_group_snapshots(group_limit)
    if removed:
        _invalidate_usage()
    summary.update(dry_run=False, removed_files=removed, freed_bytes=freed)
    return summary


def usage(include_cache=True, use_cache=True):
    limit = int(default_limit_gb()*1024**3)
    roots = tuple(str(root) for root in _roots())
    cache_key = (bool(include_cache), limit, roots)
    now = time.time()
    if use_cache:
        with _LOCK:
            cached = _USAGE_CACHE.get(cache_key)
            if cached is not None and now - cached[0] < _USAGE_CACHE_TTL:
                result = dict(cached[1])
                result['pending_bytes'] = _RESERVED
                return result
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
    result = {'used_bytes':used,'limit_bytes':limit,'percent':round(used/limit,4),
        'over_limit':used>=limit,'enforce':enforce_limit(),'import_bytes':import_bytes,'import_files':import_files,
        'media_bytes':media_bytes,'media_files':media_files,'sessions_bytes':sessions_bytes,'sessions_files':sessions_files,
        'frame_cache_bytes':frame_bytes,'cache_bytes':plan_cache()['bytes'] if include_cache else 0,
        'version_cache_bytes':_version_cache_bytes() if include_cache else 0,
        'pending_bytes':_RESERVED,'auto_clean':os.environ.get('AIDEN_H3_AUTO_CLEAN','safe')}
    if use_cache:
        with _LOCK:
            _USAGE_CACHE[cache_key] = (now, {key: value for key, value in result.items() if key != 'pending_bytes'})
    return result

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
            current = _IMPORT_BASELINE if _IMPORT_ACTIVE else usage(False, use_cache=False)
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
            if removed:
                _invalidate_usage()
        return {'dry_run':not confirmed,'files':plan['files'],'bytes':plan['bytes'],
            'removed_files':removed,'freed_bytes':freed,'blocked':plan['blocked']}

def auto_clean():
    if os.environ.get('AIDEN_H3_AUTO_CLEAN','safe').lower() in {'off','0','false','none'}: return None
    try: return clean_cache(True)
    except Exception as error:
        print(f'AD-Segmented-Loop: 自动清理缓存未执行：{error}')
        return None
