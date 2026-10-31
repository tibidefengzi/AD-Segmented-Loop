"""保存目标解析。

保存路径留空时按媒体类型归档到 ComfyUI/output/AD-Segmented-Loop-output/{图片,音频,视频}；
用户填写路径后一律以填写内容为准。仅在无法解析 ComfyUI output 目录的环境下，
才回退到当前用户桌面的 AD-生成 文件夹。
"""
import os
import re
import shutil
import tempfile
from datetime import datetime
from pathlib import Path

from . import layout

DEFAULT_DIR_NAME = "AD-生成"
BAD_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff", ".gif"}
AUDIO_EXTS = {".wav", ".mp3", ".flac", ".ogg", ".m4a", ".aac", ".opus", ".aiff", ".aif"}
VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".wmv", ".flv", ".mpg", ".mpeg", ".ts"}


def _windows_desktop() -> Path | None:
    """Resolve the actual desktop, including redirected/OneDrive desktops."""
    if os.name != "nt":
        return None
    try:
        import ctypes
        buffer = ctypes.create_unicode_buffer(32768)
        if ctypes.windll.shell32.SHGetFolderPathW(None, 0x10, None, 0, buffer) == 0 and buffer.value:
            return Path(buffer.value)
    except (OSError, AttributeError):
        pass
    return None


def kind_for_path(source) -> str:
    """按扩展名判断媒体类型；未知类型返回空串。"""
    suffix = Path(source).suffix.lower()
    if suffix in VIDEO_EXTS:
        return "video"
    if suffix in AUDIO_EXTS:
        return "audio"
    if suffix in IMAGE_EXTS:
        return "image"
    return ""


def output_library_directory(kind: str = "") -> Path | None:
    """ComfyUI/output/AD-Segmented-Loop-output[/图片|音频|视频]；解析不到时返回 None。"""
    base = layout.output_base()
    if base is None:
        return None
    root = base / layout.OUTPUT_LIBRARY
    folder = layout.KIND_DIRS.get(str(kind or "").strip())
    return root / folder if folder else root


def legacy_desktop_directory() -> Path:
    return (_windows_desktop() or Path.home() / "Desktop") / DEFAULT_DIR_NAME


def default_directory(kind: str = "") -> Path:
    """空保存路径的目标目录：生成目录按类型归档优先，解析不到才回退桌面 AD-生成。"""
    target = output_library_directory(kind)
    return target if target is not None else legacy_desktop_directory()


def configured_directory(value, kind: str = "") -> Path:
    raw = str(value or "").strip().strip('"').strip("'").strip()
    return Path(raw).expanduser() if raw else default_directory(kind)


def resolve_template(template: str, index: int, source: Path, prompt_text: str = "") -> str:
    stamp = datetime.now()
    text = (template or "").strip() or "AD-{index}-{date}"
    mapping = {
        "index": f"{int(index):02d}",
        "name": source.stem,
        "date": stamp.strftime("%Y%m%d-%H%M%S"),
        "time": stamp.strftime("%H%M%S"),
        "prompt": (prompt_text or "").strip()[:40],
    }
    for key, value in mapping.items():
        text = text.replace("{" + key + "}", value)
    text = BAD_CHARS.sub("_", text).strip(" .") or f"AD-{index:02d}-{stamp.strftime('%Y%m%d-%H%M%S')}"
    suffix = source.suffix or ""
    if suffix and not text.lower().endswith(suffix.lower()):
        text += suffix
    return text


def save_with_template(source: Path, config: dict, index: int, prompt_text: str = "", kind: str = "") -> Path:
    """按目录 + 文件名模板保存；目录留空时按媒体类型归档到生成目录。"""
    directory = configured_directory(config.get("dir"), kind or kind_for_path(source))
    directory.mkdir(parents=True, exist_ok=True)
    name = resolve_template(str(config.get("template") or ""), index, source, prompt_text)
    target = directory / name
    counter = 1
    while target.exists():
        target = directory / f"{Path(name).stem}-{counter}{Path(name).suffix}"
        counter += 1
    from .dedupe import publish
    # 另存目录与插件数据同卷时用硬链接复用数据块；跨卷自动回退为复制。
    publish(Path(source), target)
    return target


def save_to_path(source: Path, target: str) -> Path:
    """按用户在系统「另存为」框中选择的绝对路径保存（同名覆盖由系统框确认）。"""
    source, target_path = Path(source), Path(target or "").expanduser()
    if not str(target_path).strip() or not target_path.is_absolute():
        raise ValueError("请选择保存的绝对路径")
    if target_path.is_dir():
        raise ValueError("请选择保存文件名，不能只选文件夹")
    # 系统另存为框已确认最终文件名；此处不得改后缀后覆盖另一个未确认的文件。
    target_path.parent.mkdir(parents=True, exist_ok=True)
    if target_path.exists() and os.path.samefile(source, target_path):
        return target_path
    # 只替换目标目录项，避免原地覆盖其硬链接共享的其它结果。
    descriptor, temporary_name = tempfile.mkstemp(prefix=".ad-save-", suffix=target_path.suffix, dir=target_path.parent)
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        shutil.copy2(source, temporary)
        os.replace(temporary, target_path)
    finally:
        temporary.unlink(missing_ok=True)
    return target_path
