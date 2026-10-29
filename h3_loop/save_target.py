import re
import shutil
from datetime import datetime
from pathlib import Path

DEFAULT_DIR_NAME = "AD-H3下载"
BAD_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def default_directory() -> Path:
    home = Path.home()
    desktop = home / "Desktop"
    return (desktop if desktop.is_dir() else home) / DEFAULT_DIR_NAME


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


def save_with_template(source: Path, config: dict, index: int, prompt_text: str = "") -> Path:
    raw_dir = str(config.get("dir") or "").strip().strip('"').strip("'").strip()
    directory = Path(raw_dir).expanduser()
    if not raw_dir:
        directory = default_directory()
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
    shutil.copy2(source, target_path)
    return target_path
