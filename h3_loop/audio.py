from __future__ import annotations

import math
from pathlib import Path
import re
from functools import lru_cache

from .storage import ROOT, contained, digest
from .spec import FPS, generation_frames, target_frames, validate_fps

EXTENSIONS = {".wav", ".mp3", ".flac", ".ogg", ".m4a", ".aac", ".opus", ".aiff", ".aif"}
TIMELINE_RATE = 48000  # 时间线段音频统一在此采样率下混音与补静音


def natural_key(value):
    return [int(part) if part.isdigit() else part.casefold() for part in re.split(r"(\d+)", value)]


def resolve_entry(entry):
    if entry.get("origin") == "input":
        from . import layout
        path = layout.resolve_input_file(str(entry.get("file") or ""))
        if path is not None:
            if path.suffix.lower() not in EXTENSIONS:
                raise ValueError(f"音频缺失或格式不支持：{path.name}")
            return path
        raise ValueError(f"音频缺失或格式不支持：{Path(str(entry.get('file') or '')).name}")
    elif entry.get("origin") == "media":
        root = ROOT / "media"
    else:
        raise ValueError("未知音频来源，请重新导入")
    path = contained(root, entry["file"])
    if path.suffix.lower() not in EXTENSIONS or not path.is_file():
        raise ValueError(f"音频缺失或格式不支持：{path.name}")
    return path


@lru_cache(maxsize=2)
def _decode(path, modified, size):
    # Reuse ComfyUI's own decoder; preserves the exact sample count.
    from comfy_extras.nodes_audio import load
    waveform, sample_rate = load(str(path))
    if waveform.shape[-1] == 0 or sample_rate <= 0:
        raise ValueError(f"音频没有有效采样：{Path(path).name}")
    return {"waveform": waveform.unsqueeze(0), "sample_rate": sample_rate}


def load_audio(path):
    path = Path(path)
    stat = path.stat()
    return _decode(str(path), stat.st_mtime_ns, stat.st_size)


@lru_cache(maxsize=2)
def _decode_range(path, modified, size, first, last):
    """Seek to the retained audio range; no resampling, re-encoding or temporary file."""
    import av
    import torch
    from comfy_extras.nodes_audio import f32_pcm
    chunks = []
    with av.open(path) as media:
        if not media.streams.audio:
            raise ValueError("素材没有音轨")
        stream = media.streams.audio[0]
        rate = int(stream.codec_context.sample_rate)
        channels = stream.codec_context.channels
        origin = (stream.start_time or 0) * stream.time_base
        lo, hi = round(first * rate), round(last * rate)
        if first > 0:
            media.seek(int(max(0, first - .1) / stream.time_base) + (stream.start_time or 0), stream=stream, backward=True)
        for frame in media.decode(audio=0):
            if frame.pts is None:
                raise ValueError("音频缺少定位时间戳")
            offset = round(float(frame.pts * frame.time_base - origin) * rate)
            if offset >= hi:
                break
            start, end = max(0, lo - offset), min(frame.samples, hi - offset)
            if end <= start:
                continue
            wave = torch.from_numpy(frame.to_ndarray())
            if wave.shape[0] != channels:
                wave = wave.view(-1, channels).t()
            chunks.append(f32_pcm(wave[:, start:end]))
    if not chunks:
        raise ValueError("裁后音频范围没有有效样本")
    return {"waveform": torch.cat(chunks, dim=-1).unsqueeze(0), "sample_rate": rate}


def load_audio_range(path, first, last):
    path = Path(path)
    stat = path.stat()
    from .assets import cached_media
    return cached_media("source-audio-range", path, [float(first), float(last)],
                        lambda: _decode_range(str(path), stat.st_mtime_ns, stat.st_size, float(first), float(last)))


def read_audio_entry(entry):
    if "timeline_audio" in entry:
        return read_timeline_audio(entry)
    audio = load_audio(resolve_entry(entry))
    if "start_sample" not in entry:
        return audio
    start, end = int(entry["start_sample"]), int(entry["end_sample"])
    if start < 0 or end <= start or end > audio["waveform"].shape[-1]:
        raise ValueError("音频裁剪范围无效，请检查起止位置")
    return {"waveform": audio["waveform"][..., start:end].clone(), "sample_rate": audio["sample_rate"]}


def _piece_chunk(piece):
    """把一个秒数范围的素材片段解码并统一到时间线采样率。"""
    audio = load_audio(resolve_entry(piece))
    wave = audio["waveform"]
    rate = int(audio["sample_rate"])
    first = max(0, min(round(float(piece["src_in"]) * rate), wave.shape[-1]))
    last = max(first, min(round(float(piece["src_out"]) * rate), wave.shape[-1]))
    chunk = wave[..., first:last]
    if chunk.shape[-1] == 0:
        return None
    if rate != TIMELINE_RATE:
        import torch
        size = max(1, round(chunk.shape[-1] * TIMELINE_RATE / rate))
        chunk = torch.nn.functional.interpolate(chunk, size=size, mode="linear", align_corners=False)
    return chunk


def _match_channels(chunk, channels):
    if chunk.shape[1] >= channels:
        return chunk[:, :channels]
    if chunk.shape[1] == 1:
        return chunk.expand(-1, channels, -1)
    import torch
    return torch.cat((chunk, chunk[:, :1].expand(-1, channels - chunk.shape[1], -1)), dim=1)


def read_timeline_audio(entry):
    from .assets import cached_media
    tracks = entry["timeline_audio"]["tracks"]
    sources = [piece for lane in tracks for piece in lane]
    return cached_media("timeline-audio", sources, [tracks, entry["seconds_float"]],
                        lambda: _read_timeline_audio(entry))


def _read_timeline_audio(entry):
    """段音频：各音频轨在段区间内的片段按时间线位置混音，空缺补静音。"""
    import torch
    spec = entry["timeline_audio"]
    total = max(1, round(entry["seconds_float"] * TIMELINE_RATE))
    track_buffers = []
    for pieces in spec.get("tracks", []):
        placed, channels = [], 1
        for piece in pieces:
            chunk = _piece_chunk(piece)
            if chunk is None:
                continue
            channels = max(channels, chunk.shape[1])
            placed.append((max(0, min(round(float(piece.get("at", 0.0)) * TIMELINE_RATE), total)), chunk))
        if not placed:
            continue
        buffer = torch.zeros((1, channels, total))
        for start, chunk in placed:
            end = min(total, start + chunk.shape[-1])
            if end <= start:
                continue
            buffer[0, :, start:end] += _match_channels(chunk[..., :end - start], channels)[0]
        track_buffers.append(buffer)
    if not track_buffers:
        return {"waveform": torch.zeros((1, 1, total)), "sample_rate": TIMELINE_RATE}
    channels = max(buffer.shape[1] for buffer in track_buffers)
    mixed = torch.zeros((1, channels, total))
    for buffer in track_buffers:
        mixed += _match_channels(buffer, channels)
    peak = float(mixed.abs().max()) if mixed.numel() else 0.0
    if peak > 1.0:
        mixed = mixed / peak
    return {"waveform": mixed, "sample_rate": TIMELINE_RATE}


def read_pieces(pieces):
    from .assets import cached_media
    return cached_media("reference-audio-pieces", pieces, pieces, lambda: _read_pieces(pieces))


def _read_pieces(pieces):
    """参考口内容：片段按时间线顺序直接拼接，不补静音。"""
    import torch
    chunks = [chunk for chunk in (_piece_chunk(piece) for piece in pieces) if chunk is not None]
    if not chunks:
        return {"waveform": torch.zeros((1, 1, 0)), "sample_rate": TIMELINE_RATE}
    channels = max(chunk.shape[1] for chunk in chunks)
    return {"waveform": torch.cat(tuple(_match_channels(chunk, channels) for chunk in chunks), dim=-1),
            "sample_rate": TIMELINE_RATE}


def metadata(audio):
    samples = int(audio["waveform"].shape[-1])
    rate = int(audio["sample_rate"])
    return sample_metadata(samples,rate)


def sample_metadata(samples,rate):
    seconds = samples / rate
    return {"samples": samples, "sample_rate": rate,
            "seconds_float": seconds, "seconds_int": (samples + rate - 1) // rate}


def prepare_entries(raw_entries):
    entries = []
    seen = set()
    for raw in raw_entries:
        path = resolve_entry(raw)
        item = {k: raw[k] for k in ("id", "origin", "file", "start_sample", "end_sample") if k in raw}
        item.setdefault("id", digest([item["origin"], item["file"]])[:24])
        if item["id"] in seen:
            raise ValueError("段标识重复，请重新导入或使用独立文件")
        seen.add(item["id"])
        stat = path.stat()
        item.update(name=raw.get("name", path.name), size=stat.st_size, mtime_ns=stat.st_mtime_ns)
        full=load_audio(path)
        samples=full["waveform"].shape[-1]
        if "start_sample" in item:
            first,last=int(item["start_sample"]),int(item["end_sample"])
            if not 0<=first<last<=samples:raise ValueError("音频裁剪范围无效")
            samples=last-first
        item.update(sample_metadata(samples,int(full["sample_rate"])))
        if "source_start" in raw:
            item["source_start"] = float(raw["source_start"])
        entries.append(item)
    if not entries:
        raise ValueError("音频队列为空，请批量导入文件或载入 Audio Oasis 已保存的分段")
    return entries


def queue_identity(raw_entries):
    result = []
    for entry in raw_entries:
        path = resolve_entry(entry)
        stat = path.stat()
        result.append((entry.get("id"), entry["origin"], entry["file"], entry.get("source_start"), stat.st_size, stat.st_mtime_ns))
    return digest(result)


def frame_range(entries, index, fps):
    validate_fps(fps)
    entry = entries[index]
    start = entry.get("source_start", sum(e["seconds_float"] for e in entries[:index]))
    start_frame = round(start * fps)
    native_frames = target_frames(entry["seconds_float"])
    h3_frames = generation_frames(native_frames)
    return start_frame, native_frames, h3_frames


def h3_window(entries,index,overlap_frames=0,guard_frames=0,reanchor_every=0,max_frames=362):
    overlap_frames,guard_frames,reanchor_every,max_frames=map(int,(overlap_frames,guard_frames,reanchor_every,max_frames))
    if overlap_frames not in (0,1) and (overlap_frames<5 or (overlap_frames-5)%17):
        raise ValueError("H3 重叠帧数使用 0、1 或 17k+5（5、22、39…）")
    if not 0<=guard_frames<=34 or reanchor_every<0 or not 5<=max_frames<=3600:
        raise ValueError("H3 调度参数超出范围")
    start,count,_=frame_range(entries,index,FPS)
    reanchor=bool(index and reanchor_every and index%reanchor_every==0)
    overlap=0
    if index and not reanchor:
        available=target_frames(entries[index-1]["seconds_float"])
        overlap=min(overlap_frames,available)
        if 1<overlap<5:overlap=1
        elif overlap>=5:overlap=5+17*((overlap-5)//17)
    generated=generation_frames(count,overlap,guard_frames)
    if generated>max_frames:
        raise ValueError(f"第 {index+1} 段需要 {generated} 帧，超过设置的 H3 单段上限 {max_frames} 帧；请先切短音频")
    return {"start_frame":max(0,start-overlap),"source_frames":count+overlap,
            "target_frames":count,"frame_count":generated,"overlap_frames":overlap,
            "history_frames":max(1, overlap_frames) if overlap_frames else 5,"reanchor":reanchor}


def guide_audio(entries,index,overlap):
    import torch
    current=read_audio_entry(entries[index])
    if not index or not overlap:return current
    previous=read_audio_entry(entries[index-1])
    wave=previous["waveform"]
    if previous["sample_rate"]!=current["sample_rate"]:
        size=max(1,round(wave.shape[-1]*current["sample_rate"]/previous["sample_rate"]))
        wave=torch.nn.functional.interpolate(wave,size=size,mode="linear",align_corners=False)
    if wave.shape[1]!=current["waveform"].shape[1]:
        wave=wave.mean(dim=1,keepdim=True).expand(-1,current["waveform"].shape[1],-1)
    count=round(overlap/FPS*current["sample_rate"])
    prefix=wave[...,-count:]
    if prefix.shape[-1]<count:prefix=torch.nn.functional.pad(prefix,(count-prefix.shape[-1],0))
    return {**current,"waveform":torch.cat((prefix,current["waveform"]),dim=-1)}


def audio_plan(config):
    """Sources own their edit boundaries; an output port belongs to a source."""
    if "tracks" not in config:
        entries = prepare_entries(config.get("entries", []))
        return {"entries": entries, "tracks": None}
    tracks = []
    for track in config["tracks"]:
        editor = track.get("editor", {})
        state = editor.get("exec", editor)
        file = state.get("track_file")
        if not file:
            continue
        base = {"origin":"input", "file":file, "name":track.get("name",Path(file).name)}
        full = load_audio(resolve_entry(base))
        rate = full["sample_rate"]
        duration = full["waveform"].shape[-1] / rate
        start = float(track.get("trim_start", 0) or 0)
        end = float(track.get("trim_end") or duration)
        if not all(math.isfinite(v) for v in (start,end)) or not 0 <= start < end <= duration + 1/rate:
            raise ValueError(f"{base['name']} 的裁剪范围无效")
        points = sorted({float(p) for p in state.get("points",[]) if start < float(p) < end})
        if any(not math.isfinite(p) for p in points):
            raise ValueError("切分点必须是有效秒数")
        bounds = [start,*points,min(end,duration)]
        selected = track.get("output_ranges")
        if selected is not None:
            if not isinstance(selected,list):
                raise ValueError("输出片段范围必须是列表")
            ranges = []
            for pair in selected:
                if not isinstance(pair,(list,tuple)) or len(pair)!=2:
                    raise ValueError("输出片段范围需要起止秒数")
                a,b = map(float,pair)
                if not all(math.isfinite(v) for v in (a,b)) or not 0<=a<b<=duration+1/rate:
                    raise ValueError("输出片段范围无效")
                ranges.append((a,min(b,duration)))
            ranges.sort()
            if any(a<previous[1]-1e-7 for previous,(a,b) in zip(ranges,ranges[1:])):
                raise ValueError("输出片段范围不能重叠")
            bounds = sorted(set(bounds+[v for pair in ranges for v in pair if start<v<end]))
        raw = []
        for a,b in zip(bounds,bounds[1:]):
            if selected is not None and not any(x<=a+1e-7 and y>=b-1e-7 for x,y in ranges):
                continue
            first,last = round(a*rate),round(b*rate)
            if last <= first:
                raise ValueError("切分产生了空音频，请拉开切分点")
            raw.append({**base,"id":digest([track["id"],first,last])[:24],
                        "start_sample":first,"end_sample":last,"source_start":first/rate})
        cuts = prepare_entries(raw) if raw else []
        tracks.append({"id":track["id"],"name":base["name"],"reference":not points and selected is None,
                       "cuts":cuts,"sample_rate":rate,"channels":full["waveform"].shape[1]})
    if not tracks:
        raise ValueError("请先导入至少一条音频")
    requested = config.get("main_track")
    primary = next((t for t in tracks if t["id"]==requested),None)
    if primary is not None and not primary["cuts"]:
        raise ValueError("循环主音频没有输出片段，请选中至少一个片段")
    if primary is None:
        primary = next((t for t in tracks if not t["reference"] and t["cuts"]),None)
    if primary is None and any(t.get("output_ranges") is not None for t in config["tracks"]):
        raise ValueError("请选择至少一个输出音频片段")
    entries = []
    if primary is not None:
        for index,entry in enumerate(primary["cuts"]):
            generation_frames(target_frames(entry["seconds_float"]))
            companions = []
            for track in tracks:
                cut = track["cuts"][0] if track["reference"] else (track["cuts"][index] if index<len(track["cuts"]) else None)
                companions.append({"track":track["id"],"reference":track["reference"],"cut":cut,
                                   "pad_to":None if track["reference"] else entry["seconds_float"]})
            entries.append({**entry,"companions":companions})
    return {"entries":entries,"tracks":tracks,"main_track":primary["id"] if primary else None}


def source_outputs(plan,index):
    """Uncut sources repeat unchanged; cut sources advance, then pad silence."""
    import torch
    target = plan["entries"][min(index,len(plan["entries"])-1)]["seconds_float"] if plan["entries"] else 0
    result = []
    for track in plan["tracks"]:
        if track["reference"]:
            audio = read_audio_entry(track["cuts"][0])
        elif index < len(track["cuts"]):
            audio = read_audio_entry(track["cuts"][index])
        else:
            audio = {"sample_rate":track["sample_rate"],"waveform":torch.zeros((1,track["channels"],0))}
        if not track["reference"]:
            needed = math.ceil(target*audio["sample_rate"]-1e-9)
            missing = needed-audio["waveform"].shape[-1]
            if missing>0:
                audio = {**audio,"waveform":torch.cat((audio["waveform"],audio["waveform"].new_zeros((*audio["waveform"].shape[:-1],missing))),dim=-1)}
        result.append(audio)
    return result


def quiet_boundaries(audio, points, start=0, end=None, radius=.5):
    """Recommend nearby low-energy frame positions without changing any audio."""
    import numpy as np
    wave=audio["waveform"].detach().cpu().numpy().mean(axis=(0,1))
    rate=audio["sample_rate"]
    end=min(float(end) if end is not None else len(wave)/rate,len(wave)/rate)
    start=float(start)
    if not 0<=start<end or len(points)>10000:raise ValueError("静音分析范围或切分点无效")
    points=sorted(float(point) for point in points)
    result=[]
    for index,point in enumerate(points):
        if not math.isfinite(point) or not start<point<end:continue
        left=max(start+1/FPS,point-radius,points[index-1]+1/FPS if index else start+1/FPS)
        right=min(end-1/FPS,point+radius,points[index+1]-1/FPS if index+1<len(points) else end-1/FPS)
        candidates=[]
        for frame in range(math.ceil(left*FPS),math.floor(right*FPS)+1):
            sample=round(frame/FPS*rate)
            half=max(1,round(rate*.02))
            window=wave[max(0,sample-half):min(len(wave),sample+half)]
            rms=float(np.sqrt(np.mean(window.astype(np.float64)**2))) if len(window) else 0
            candidates.append((rms+abs(frame/FPS-point)*.0001,frame/FPS,rms))
        if candidates:
            _,suggested,rms=min(candidates)
            result.append({"from_seconds":point,"to_seconds":suggested,"rms":rms})
    return result
