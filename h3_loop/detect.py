"""智能切分：踩节拍 / 智能镜头 / 智能气口。

统一输出：候选切点（秒 + 24FPS 帧号），由前端画线、试听、手动调整后应用。
"""
from __future__ import annotations

import math
import subprocess
import tempfile
from pathlib import Path

FPS = 24


def _frame(time_value: float) -> int:
    return int(math.floor(float(time_value) * FPS + 0.5))


def _time(frame: int) -> float:
    return frame / FPS


def _snap(times, duration, min_gap=1.5, max_gap=15.0):
    """逐个比较上一个保留点；首尾也满足最小间隔，最长区间不超过15秒。"""
    if not math.isfinite(float(duration)) or duration <= 0:
        raise ValueError("素材时长无效")
    if not math.isfinite(float(min_gap)) or not math.isfinite(float(max_gap)):
        raise ValueError("切分间隔必须是有限数值")
    minimum=max(1,math.ceil(max(1/FPS,float(min_gap))*FPS))
    maximum=max(1,math.floor(min(15.0,max(1/FPS,float(max_gap)))*FPS))
    if maximum < minimum:
        raise ValueError("最大段长不能小于最小间隔")
    total=_frame(duration)
    candidates=sorted({_frame(float(t)) for t in times if math.isfinite(float(t)) and 0<float(t)<duration})
    kept=[0]
    for frame in candidates:
        if frame-kept[-1]>=minimum and total-frame>=minimum:kept.append(frame)
    kept.append(total)
    result=[]
    for left,right in zip(kept,kept[1:]):
        span=right-left
        parts=max(1,math.ceil(span/maximum))
        if parts>1 and span//parts<minimum:
            raise ValueError("当前最小间隔与最大段长无法同时满足，请放宽切分条件")
        result.extend(left+round(span*i/parts) for i in range(1,parts))
        if right<total:result.append(right)
    return sorted(set(result))


def _decode_wav(path: Path, vocals: bool = False, start_time=0.0, end_time=None):
    """解码缓存留在会话数据目录；关闭临时文件句柄后再交给FFmpeg。"""
    import os
    import av
    from .assets import ffmpeg_executable
    from .storage import ROOT
    directory=ROOT/"detect-temp";directory.mkdir(parents=True,exist_ok=True)
    fd,name=tempfile.mkstemp(suffix=".wav",dir=directory);os.close(fd);tmp=Path(name)
    filters=[]
    if vocals:
        with av.open(str(path)) as media:
            channels=media.streams.audio[0].codec_context.channels if media.streams.audio else 0
        if channels>=2:
            # 同相中置人声需要相加；相减会把它抵消掉。
            filters=["-af","pan=mono|c0=0.5*c0+0.5*c1"]
    span = _range(path, start_time, end_time)
    command=[ffmpeg_executable(),"-v","error","-nostdin","-y","-ss",str(span[0]),"-i",str(path),"-t",str(span[1]-span[0]),*filters,"-ac","1","-ar","22050",str(tmp)]
    finished=subprocess.run(command,capture_output=True)
    if finished.returncode!=0 or not tmp.is_file():
        tmp.unlink(missing_ok=True)
        raise ValueError("音频解码失败："+finished.stderr.decode("utf-8","replace")[:200])
    return tmp


def _probe_duration(path: Path) -> float:
    from .assets import probe_duration
    return probe_duration(path)


def _range(path, start_time=0.0, end_time=None):
    full = _probe_duration(path)
    start, end = float(start_time or 0), float(end_time) if end_time is not None else full
    if not full or not all(math.isfinite(value) for value in (start, end)) or not 0 <= start < end <= full + 1 / 48:
        raise ValueError("检测范围无效")
    return start, min(end, full)


def detect_beats(path: Path, mode: str = "beat", min_gap: float = 1.0, max_gap: float = 15.0, start_time=0.0, end_time=None):
    try:
        import librosa
    except ImportError as exc:
        raise ValueError("智能音频切分需要 librosa，请使用 ComfyUI 的 Python 安装 librosa 后重试") from exc
    import numpy as np
    wav = _decode_wav(path, start_time=start_time, end_time=end_time)
    try:
        y, sr = librosa.load(str(wav), sr=22050, mono=True)
        tempo, beat_frames = librosa.beat.beat_track(y=y, sr=sr, units="frames")
        beat_times = librosa.frames_to_time(beat_frames, sr=sr).tolist()
        duration = float(len(y)) / sr
    finally:
        try:
            wav.unlink(missing_ok=True)
        except OSError:
            pass
    if mode == "bar":
        beat_times = beat_times[::4]
    frames = _snap(beat_times, duration, min_gap, max_gap)
    frames = [frame + _frame(start_time) for frame in frames]
    return {"cuts": frames, "times": [_time(f) for f in frames],
            "meta": {"bpm": round(float(np.asarray(tempo).reshape(-1)[0]), 2), "mode": mode,
                     "raw_beats": len(beat_times), "duration": round(duration, 2)}}


def detect_shots(path: Path, threshold: float = 0.3, min_shot: float = 1.0, max_gap: float = 15.0, start_time=0.0, end_time=None):
    from .assets import ffmpeg_executable
    first, last = _range(path, start_time, end_time)
    duration = last - first
    cuts = []
    try:
        from scenedetect import ContentDetector, SceneManager, open_video
        video = open_video(str(path))
        if first:
            video.seek(float(first))
        manager = SceneManager()
        manager.add_detector(ContentDetector(threshold=threshold))
        manager.detect_scenes(video, duration=float(duration))
        scenes = manager.get_scene_list()
        cuts = [scene[0].get_seconds() - first for scene in scenes[1:]]
        source = "scenedetect"
    except Exception:
        cuts = []
        source = "scenedetect-failed"
    if not cuts:
        command = [ffmpeg_executable(), "-v", "info", "-nostdin", "-ss", str(first), "-i", str(path), "-t", str(duration),
                   "-vf", f"select='gt(scene,{threshold})',metadata=print", "-an", "-f", "null", "-"]
        finished = subprocess.run(command, capture_output=True)
        text = (finished.stderr or b"").decode("utf-8", "replace")
        for line in text.splitlines():
            if "pts_time:" in line:
                try:
                    cuts.append(float(line.split("pts_time:")[1].split()[0]))
                except (IndexError, ValueError):
                    continue
        source = "ffmpeg"
    frames = _snap(cuts, duration or (max(cuts) + 1 if cuts else 0), max(1.0, min_shot), max_gap)
    frames = [frame + _frame(first) for frame in frames]
    return {"cuts": frames, "times": [_time(f) for f in frames],
            "meta": {"threshold": threshold, "min_shot": min_shot, "duration": round(duration, 2),
                     "source": source, "raw": len(cuts), "range_start": first, "range_end": last}}


def detect_breaths(path: Path, sensitivity: float = 0.5, min_seg: float = 1.5,
                   max_seg: float = 15.0, min_breath: float = 0.15, buffer: float = 0.05,
                   vocals: bool = False, start_time=0.0, end_time=None):
    try:
        import librosa
    except ImportError as exc:
        raise ValueError("智能音频切分需要 librosa，请使用 ComfyUI 的 Python 安装 librosa 后重试") from exc
    import numpy as np
    wav = _decode_wav(path, vocals=vocals, start_time=start_time, end_time=end_time)
    try:
        y, sr = librosa.load(str(wav), sr=22050, mono=True)
    finally:
        try:
            wav.unlink(missing_ok=True)
        except OSError:
            pass
    duration = len(y) / sr
    hop = 512
    rms = librosa.feature.rms(y=y, frame_length=2048, hop_length=hop)[0]
    flatness = librosa.feature.spectral_flatness(y=y, hop_length=hop)[0]
    if rms.size == 0:
        return {"cuts": [], "times": [], "meta": {"duration": duration}}
    times = librosa.frames_to_time(np.arange(rms.size), sr=sr, hop_length=hop)
    percent = max(5.0, min(40.0, 40.0 - float(sensitivity) * 35.0))
    low = np.percentile(rms, percent)
    quiet = rms <= low * 1.10
    # 频谱平坦度高的短窗更像换气（宽带噪声），作为加分线索
    breathish = flatness >= np.percentile(flatness, 80)
    score = quiet.astype(float) + breathish.astype(float) * 0.5
    threshold = score.mean() + 0.15
    candidates = []
    index = 0
    while index < rms.size:
        if score[index] < threshold:
            index += 1
            continue
        start = index
        while index < rms.size and score[index] >= threshold:
            index += 1
        end = index
        length = times[end - 1] - times[start] if end > start else 0
        if length >= min_breath:
            center = (times[start] + times[end - 1]) / 2
            candidates.append(center - buffer if center - buffer > 0 else center)
    frames = _snap(candidates, duration, max(0.5, min_seg), max_seg)
    frames = [frame + _frame(start_time) for frame in frames]
    return {"cuts": frames, "times": [_time(f) for f in frames],
            "meta": {"sensitivity": sensitivity, "min_seg": min_seg, "max_seg": max_seg,
                     "min_breath": min_breath, "vocals": vocals, "duration": round(duration, 2),
                     "raw": len(candidates)}}
