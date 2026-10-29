"""原生播放器的文件描述；已经入库的分段直接复用，不重复编码。"""
from pathlib import Path
import uuid

from .storage import ROOT, STORE, contained


def preview_record(video):
    record = getattr(video, "_ad_h3_preview_record", None)
    if record and record.get("origin") == "session" and contained(STORE.root, record["file"]).is_file():
        return dict(record)
    # 任意上游 VIDEO 都通过 save_to 保留裁剪/空间变换；不调用可能隐式编码的 get_stream_source。
    directory = ROOT / "media"
    directory.mkdir(parents=True, exist_ok=True)
    name = f"preview_{uuid.uuid4().hex}.mp4"
    video.save_to(str(directory / name))
    return {"origin": "media", "file": name}
