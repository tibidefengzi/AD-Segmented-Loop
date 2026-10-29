"""用户可见目录命名（v0.3.14 起统一）与旧目录只读兼容。

新目录：
- input/AD-Segmented-Loop-input/{图片,音频,视频,normalized}
- output/AD-Segmented-Loop-output/{图片,音频,视频}
- output/AD-Segmented-Loop-data/{sessions,media,frame-cache,import-staging,thumbs}

旧目录（仅兼容读取，不迁移、不删除）：
- input/aiden_h3_sources/...
- output/AD-Segmented-Loop/...
- output/AD-H3/...
"""
from __future__ import annotations

from pathlib import Path

INPUT_LIBRARY = "AD-Segmented-Loop-input"
OUTPUT_LIBRARY = "AD-Segmented-Loop-output"
DATA_ROOT = "AD-Segmented-Loop-data"
LEGACY_INPUT_LIBRARY = "aiden_h3_sources"
LEGACY_OUTPUT_LIBRARY = "AD-Segmented-Loop"
LEGACY_DATA_ROOT = "AD-H3"

KIND_DIRS = {"image": "图片", "audio": "音频", "video": "视频"}
FOLDER_KINDS = {folder: kind for kind, folder in KIND_DIRS.items()}


def _comfy_dir(getter_name: str):
    try:
        import folder_paths
        getter = getattr(folder_paths, getter_name, None)
        return Path(getter()) if getter is not None else None
    except Exception:
        return None


def input_base():
    return _comfy_dir("get_input_directory")


def output_base():
    return _comfy_dir("get_output_directory")


def input_libraries():
    """新目录在前，旧目录兜底；目录不一定存在。"""
    base = input_base()
    if base is None:
        return []
    return [base / INPUT_LIBRARY, base / LEGACY_INPUT_LIBRARY]


def output_libraries():
    base = output_base()
    if base is None:
        return []
    return [base / OUTPUT_LIBRARY, base / LEGACY_OUTPUT_LIBRARY]


def data_root_candidates():
    base = output_base()
    if base is None:
        return []
    return [base / DATA_ROOT, base / LEGACY_DATA_ROOT]


def input_library_dir(kind: str = "", legacy: bool = False) -> Path | None:
    base = input_base()
    if base is None:
        return None
    root = base / (LEGACY_INPUT_LIBRARY if legacy else INPUT_LIBRARY)
    return root / KIND_DIRS[kind] if kind else root


def active_input_library() -> Path | None:
    """已存在优先新目录；否则回退旧目录；都不存在时返回新目录（用于新建）。"""
    libraries = input_libraries()
    for library in libraries:
        if library.is_dir():
            return library
    return libraries[0] if libraries else None


def active_output_library() -> Path | None:
    libraries = output_libraries()
    for library in libraries:
        if library.is_dir():
            return library
    return libraries[0] if libraries else None


def _with_prefix(relative: str, prefix: str) -> str:
    relative = str(relative or "").replace("\\", "/").lstrip("/")
    for known in (INPUT_LIBRARY, LEGACY_INPUT_LIBRARY, OUTPUT_LIBRARY, LEGACY_OUTPUT_LIBRARY):
        if relative == known:
            return prefix
        if relative.startswith(known + "/"):
            return prefix + relative[len(known):]
    return prefix + "/" + relative


def _library_variants(relative: str, new: str, old: str):
    """同一相对路径在新/旧库名下的候选写法（保持子路径不变）。"""
    yield _with_prefix(relative, new)
    yield _with_prefix(relative, old)


def resolve_input_file(relative: str):
    """把工作流里的 input 素材路径解析成文件；新/旧库名与裸相对路径互相兜底。"""
    from .storage import contained
    base = input_base()
    if base is None or not relative:
        return None
    cleaned = str(relative).replace("\\", "/").lstrip("/")
    try:
        direct = contained(base, cleaned)
    except ValueError:
        direct = None
    if direct is not None and direct.is_file():
        return direct
    checked = set()
    for variant in _library_variants(cleaned, INPUT_LIBRARY, LEGACY_INPUT_LIBRARY):
        if variant in checked:
            continue
        checked.add(variant)
        try:
            path = contained(base, variant)
        except ValueError:
            continue
        if path.is_file():
            return path
        # 也允许不带库名前缀的裸相对路径
        stripped = variant.split("/", 1)[1] if "/" in variant else variant
        for library in (INPUT_LIBRARY, LEGACY_INPUT_LIBRARY):
            try:
                path = contained(base, f"{library}/{stripped}")
            except ValueError:
                continue
            if path.is_file():
                return path
    return None


def resolve_output_file(relative: str):
    """生成归档路径解析：output/AD-Segmented-Loop-output 优先，旧 AD-Segmented-Loop 兜底。"""
    from .storage import contained
    base = output_base()
    if base is None or not relative:
        return None
    cleaned = str(relative).replace("\\", "/").lstrip("/")
    try:
        direct = contained(base, cleaned)
    except ValueError:
        direct = None
    if direct is not None and direct.is_file():
        return direct
    for variant in _library_variants(cleaned, OUTPUT_LIBRARY, LEGACY_OUTPUT_LIBRARY):
        try:
            path = contained(base, variant)
        except ValueError:
            continue
        if path.is_file():
            return path
    return None
