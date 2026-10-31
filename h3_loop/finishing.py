"""自有有界内存接缝处理、FFmpeg 画面合并与原始音轨封装。"""
from fractions import Fraction
import math
from pathlib import Path
import shutil
import subprocess

import numpy as np

from .media import DEFAULT_THRESHOLDS, small_frame
from . import frames
from .spec import FPS, target_frames, validate_resolution
from .storage import contained

MODES = ("hard_cut", "crossfade")
DEFAULT_OPTIONS = {"mode": "hard_cut", "seams": {}, "color_match": False,
                   "grain": 0.0, "grain_seed": 0,
                   "thresholds": DEFAULT_THRESHOLDS}


def merge_options(raw=None):
    raw = raw or {}
    result = {**DEFAULT_OPTIONS, **raw}
    if result["mode"] not in MODES:
        raise ValueError("未知拼接模式")
    if type(result["color_match"]) is not bool:
        raise ValueError("色彩匹配需要布尔值")
    result["grain"] = float(result["grain"])
    if not math.isfinite(result["grain"]) or not 0 <= result["grain"] <= .03:
        raise ValueError("颗粒强度应在 0～0.03 之间")
    result["grain_seed"] = int(result["grain_seed"]) % (2**32)
    seams = result["seams"]
    if not isinstance(seams, dict) or any(not str(k).isdigit() or int(k) < 1 or v not in MODES for k,v in seams.items()):
        raise ValueError("逐缝设置需要有效段号和模式")
    result["seams"] = {str(k):v for k,v in seams.items()}
    thresholds = {**DEFAULT_THRESHOLDS, **result.get("thresholds", {})}
    for key in DEFAULT_THRESHOLDS:
        thresholds[key] = float(thresholds[key])
        if not math.isfinite(thresholds[key]) or thresholds[key] < 0:
            raise ValueError("接缝阈值必须是非负有限数值")
    result["thresholds"] = thresholds
    return result


def _selected_version(row, index):
    selected = row.get("selected")
    version = next((v for v in (row.get("versions") or []) if selected and v.get("id") == selected), None)
    if version is None:
        raise ValueError(f"第 {index+1} 段没有可用的选中版本，请先生成该段")
    return version


def selected_versions(state):
    return [_selected_version(row, index) for index, row in enumerate(state["segments"])]


def validate_inputs(paths, versions):
    """Probe actual streams before invoking a backend that may resize inputs."""
    import av
    reference = None
    for index, (path, version) in enumerate(zip(paths, versions)):
        with av.open(str(path)) as source:
            if not source.streams.video:
                raise ValueError(f"第 {index+1} 段没有视频流")
            stream = source.streams.video[0]
            width, height = stream.codec_context.width, stream.codec_context.height
            validate_resolution(width, height)
            fps = stream.average_rate
            if fps != Fraction(FPS):
                raise ValueError(f"第 {index+1} 段帧率不是 24 FPS，禁止合并")
            shape = (width, height)
            if reference is not None and shape != reference:
                raise ValueError(f"第 {index+1} 段分辨率不一致，禁止合并")
            reference = shape
            if stream.frames and version.get("frames") != stream.frames:
                raise ValueError(f"第 {index+1} 段文件帧数与记录不一致")
    return reference


def color_transform(left, right):
    a = np.concatenate([small_frame(f).reshape(-1,3) for f in left])
    b = np.concatenate([small_frame(f).reshape(-1,3) for f in right])
    gain = np.clip(a.std(0) / np.maximum(b.std(0), .02), .8, 1.25)
    offset = np.clip(a.mean(0) - b.mean(0)*gain, -.12, .12)
    return gain, offset


def apply_color(image, transform):
    return np.clip(np.asarray(image, dtype=np.float32)*transform[0]+transform[1], 0, 1)




def overlap_blend(left, right, weight, mode):
    """Overlap blend for crossfade mode; hard_cut keeps the left sample."""
    left, right = np.asarray(left,dtype=np.float32), np.asarray(right,dtype=np.float32)
    if left.shape != right.shape:
        raise ValueError("重叠区尺寸不一致，不能融合")
    if mode == "hard_cut":
        return left
    return np.clip(left*(1-weight)+right*weight,0,1)


def seam_plan(directory, versions, options):
    transforms = [(np.ones(3,dtype=np.float32),np.zeros(3,dtype=np.float32))]
    prefixes = [None]
    modes = ["hard_cut"]
    for index in range(1,len(versions)):
        current, previous = versions[index], versions[index-1]
        prefix = frames.load_frames(contained(directory,current["overlap_file"])) if current.get("overlap_file") else None
        mode = options["seams"].get(str(index),options["mode"])
        if mode != "hard_cut" and prefix is None and not current.get("reanchor"):
            if current.get("overlap_frames",0):
                raise ValueError(f"第 {index+1} 段重叠数据缺失，请重做该段或选择硬切")
        transform = transforms[0]
        if options["color_match"] and previous.get("tail_file"):
            tail = frames.load_frames(contained(directory,previous["tail_file"]))
            # Reanchors and hard cuts without a pre-roll intentionally retain scene color.
            if prefix is not None and len(prefix):
                transform = color_transform(apply_color(tail[-len(prefix):],transforms[-1]),prefix)
        transforms.append(transform)
        prefixes.append(prefix)
        modes.append(mode)
    return transforms, prefixes, modes


def transform_frame(frame, frame_index, count, transform, next_prefix, next_transform, mode, options, rng):
    image = apply_color(frame,transform)
    overlap = len(next_prefix) if next_prefix is not None else 0
    offset = frame_index-(count-overlap)
    if overlap > count:
        raise ValueError("重叠帧数超过前段目标帧数")
    if offset >= 0 and overlap and mode != "hard_cut":
        right = apply_color(next_prefix[offset],next_transform)
        image = overlap_blend(image,right,(offset+1)/overlap,mode)
    if options["grain"]:
        image = np.clip(image+rng.normal(0,options["grain"],(*image.shape[:2],1)),0,1)
    return image


def render_segment(source_path, output_path, version, transform, next_prefix, next_transform, mode, options, rng):
    import av
    count = int(version["frames"])
    with av.open(str(source_path)) as source, av.open(str(output_path),"w") as output:
        video = source.streams.video[0]
        stream = output.add_stream("libx264",rate=FPS)
        stream.width, stream.height = video.codec_context.width,video.codec_context.height
        stream.pix_fmt = "yuv420p"
        stream.options = {"crf":"16","preset":"fast"}
        audio_map = {s.index:output.add_stream_from_template(s) for s in source.streams.audio}
        written = 0
        for packet in source.demux():
            if packet.stream.index in audio_map:
                if packet.dts is not None:
                    packet.stream = audio_map[packet.stream.index]
                    output.mux(packet)
            elif packet.stream.index == video.index:
                for frame in packet.decode():
                    if written >= count:
                        raise ValueError("视频文件多于记录帧数")
                    image = frame.to_ndarray(format="rgb24").astype(np.float32)/255
                    image = transform_frame(image,written,count,transform,next_prefix,next_transform,mode,options,rng)
                    result = av.VideoFrame.from_ndarray(np.rint(image*255).astype(np.uint8),format="rgb24")
                    result.pts, result.time_base = written,Fraction(1,FPS)
                    for encoded in stream.encode(result):output.mux(encoded)
                    written += 1
        if written != count:
            raise ValueError(f"视频实际帧数不足：记录 {count}，解码 {written}")
        for encoded in stream.encode():output.mux(encoded)




def prepare_merge(paths, versions, directory, output_dir, options):
    transforms,prefixes,modes = seam_plan(directory,versions,options)
    if not options["color_match"] and not options["grain"] and all(mode=="hard_cut" or prefix is None for mode,prefix in zip(modes,prefixes)):
        return paths
    output_dir.mkdir(parents=True,exist_ok=False)
    result = []
    rng = np.random.default_rng(options["grain_seed"])
    for index,(path,version) in enumerate(zip(paths,versions)):
        target = output_dir/f"finished_{index+1:04d}.mp4"
        following = index+1
        render_segment(path,target,version,transforms[index],
                       prefixes[following] if following<len(paths) else None,
                       transforms[following] if following<len(paths) else transforms[0],
                       modes[following] if following<len(paths) else "hard_cut",options,rng)
        result.append(target)
    return result


def _stream_copy_merge(paths, output):
    """所有分段编码参数一致时直接拼接视频流（不重编码，画质与速度最优）。"""
    import av
    from .assets import ffmpeg_executable
    executable = ffmpeg_executable()
    if executable is None or not paths or output.exists():
        return False
    try:
        infos, durations = [], []
        for path in paths:
            with av.open(str(path)) as container:
                if not container.streams.video:
                    return False
                stream = container.streams.video[0]
                if stream.average_rate != Fraction(FPS) or not stream.frames or stream.start_time not in (None, 0):
                    return False
                durations.append(Fraction(stream.frames, FPS))
                infos.append((stream.codec_context.name,
                              int(stream.codec_context.width), int(stream.codec_context.height),
                              bytes(stream.codec_context.extradata or b""),
                              round(float(stream.average_rate or 0), 3),
                              str(stream.codec_context.pix_fmt or ""), str(stream.time_base),
                              str(stream.sample_aspect_ratio),
                              tuple(getattr(stream.codec_context, key, None) for key in
                                    ("color_range", "color_primaries", "color_trc", "colorspace"))))
        first = infos[0]
        if any(info != first for info in infos[1:]):
            return False
        if first[0] not in {"h264", "hevc"}:
            return False
        list_file = output.with_suffix(".concat.txt")
        # concat 按容器时长推进；音轨尾包比画面长时，必须显式使用锁帧画面时长。
        quoted = "\n".join("file '" + str(path.resolve()).replace("'", "'\\''") + "'\nduration " + format(float(duration), ".12f")
                           for path, duration in zip(paths, durations))
        try:
            list_file.write_text(quoted, encoding="utf-8")
            result = subprocess.run([executable, "-v", "error", "-nostdin", "-n", "-copyts", "-f", "concat", "-safe", "0",
                                     "-i", str(list_file), "-map", "0:v:0", "-c", "copy", "-an",
                                     "-movflags", "+faststart", str(output)],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            if result.returncode or not output.is_file() or output.stat().st_size == 0:
                output.unlink(missing_ok=True)
                return False
            with av.open(str(output)) as merged:
                video = merged.streams.video[0]
                stamps = sorted(packet.pts * packet.time_base for packet in merged.demux(video) if packet.pts is not None)
                expected = sum(int(duration * FPS) for duration in durations)
                if len(stamps) != expected or any(stamp != Fraction(index, FPS) for index, stamp in enumerate(stamps)):
                    raise ValueError("快速拼接画面时间戳不连续，回退逐帧合并")
            return True
        finally:
            list_file.unlink(missing_ok=True)
    except Exception:
        output.unlink(missing_ok=True)
        return False


def concat_picture(paths, output):
    """逐帧管道输入 FFmpeg；不继承容器/AAC 时间戳，不增删任何画面帧。"""
    import av
    from .assets import ffmpeg_executable
    executable = ffmpeg_executable()
    if executable is None:
        raise ValueError("需要 FFmpeg 合并视频，请安装 imageio-ffmpeg 或将 FFmpeg 加入 PATH")
    if not paths:
        raise ValueError("没有可合并的片段")
    with av.open(str(paths[0])) as source:
        stream = source.streams.video[0]
        width, height = stream.codec_context.width, stream.codec_context.height
    if _stream_copy_merge(paths, output):
        return
    log = output.with_suffix(".log")
    with log.open("xb") as handle:
        encoder = subprocess.Popen([executable,"-v","error","-nostdin","-n","-f","rawvideo",
            "-pixel_format","rgb24","-video_size",f"{width}x{height}","-framerate",str(FPS),"-i","pipe:0",
            "-an","-c:v","libx264","-preset","fast","-crf","16","-pix_fmt","yuv420p",
            "-movflags","+faststart",str(output)], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
            stderr=handle, creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
        try:
            for path in paths:
                with av.open(str(path)) as source:
                    for frame in source.decode(video=0):
                        if (frame.width,frame.height) != (width,height):
                            raise ValueError("分段分辨率不一致，禁止合并")
                        encoder.stdin.write(frame.to_ndarray(format="rgb24").tobytes())
            encoder.stdin.close()
            if encoder.wait(timeout=120):
                raise ValueError(f"画面合并失败，请检查日志：{log.name}")
        except Exception as error:
            if encoder.poll() is None: encoder.kill()
            encoder.wait()
            raise ValueError(f"画面合并失败，请检查分段文件和日志：{log.name}") from error


def restore_audio(picture, output, directory, versions):
    """按顺序恢复保存的音频采样，不用画面网格裁剪原始音频。"""
    if not all(v.get("audio_file") for v in versions):
        raise ValueError("旧结果缺少原始音频采样，请重做后再合并；不会静默裁剪或补齐音频")
    from .assets import ffmpeg_executable
    executable = ffmpeg_executable()
    if executable is None:
        raise ValueError("需要 FFmpeg 来封装音轨，请将 FFmpeg 加入 PATH 或安装 imageio-ffmpeg")
    channels=max(v["audio_channels"] for v in versions)
    if any(v["audio_channels"] not in (1,channels) for v in versions):
        raise ValueError("各段声道数不兼容，请在外部音频节点统一声道")
    soundtrack=output.with_name("soundtrack_"+output.stem+".m4a")
    creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0)
    with soundtrack.with_suffix(".log").open("wb") as log:
        encoder=subprocess.Popen([executable,"-v","error","-nostdin","-n","-f","f32le","-ar","48000","-ac",str(channels),
                                  "-i","pipe:0","-c:a","aac","-b:a","192k",str(soundtrack)],
                                 stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,stderr=log,creationflags=creationflags)
        samples=0
        try:
            from scipy.signal import resample_poly
            for version in versions:
                wave=np.load(contained(directory,version["audio_file"]),allow_pickle=False,mmap_mode="r")
                if wave.ndim!=3 or wave.shape[0]!=1 or wave.shape[1]!=version["audio_channels"]:
                    raise ValueError("保存的音频采样格式不正确")
                wave=np.asarray(wave[0],dtype=np.float32)
                rate=version["audio_sample_rate"]
                if rate!=48000:
                    divisor=math.gcd(rate,48000)
                    wave=resample_poly(wave,48000//divisor,rate//divisor,axis=-1)
                if wave.shape[0]==1 and channels>1:wave=np.repeat(wave,channels,axis=0)
                samples+=wave.shape[-1]
                for start in range(0,wave.shape[-1],48000):
                    encoder.stdin.write(np.ascontiguousarray(wave[:,start:start+48000].T,dtype="<f4").tobytes())
            encoder.stdin.close()
            if encoder.wait(timeout=120):raise ValueError("原始音轨封装失败，请检查合并日志")
        except Exception:
            if encoder.poll() is None:encoder.kill()
            encoder.wait()
            raise
    result=subprocess.run([executable,"-v","error","-nostdin","-n","-i",str(picture),"-i",str(soundtrack),
                           "-map","0:v:0","-map","1:a:0","-c","copy","-movflags","+faststart",str(output)],
                          capture_output=True,creationflags=creationflags,timeout=120)
    if result.returncode:raise ValueError("最终音视频封装失败："+result.stderr.decode(errors="replace")[-1500:])
    return {"samples":samples,"sample_rate":48000,"channels":channels,"seconds":samples/48000}


def export_manifest(state, options, output):
    rows = []
    cumulative = 0.0
    for index,row in enumerate(state["segments"]):
        version = _selected_version(row, index)
        error = version.get("sync_error_ms",0)
        cumulative += error
        rows.append({"segment":index+1,"source":row["entry"],"selected_version":version["id"],
                     "file":version["file"],"frames":version.get("frames",target_frames(row["entry"]["seconds_float"])),
                     "video_seconds":version.get("frames",target_frames(row["entry"]["seconds_float"]))/FPS,
                     "sync_error_ms":error,"cumulative_error_ms":cumulative,
                     "prompt":version.get("prompt_text",""),"seeds":version.get("seeds",{}),
                     "references":version.get("references",{}),"reanchor":version.get("reanchor",False),
                     "seam_mode":options["seams"].get(str(index),options["mode"]) if index else None,
                     "strict_sync":version.get("strict_sync",False),
                     "dropped_frames":version.get("dropped_frames",[]),"overlap_frames":version.get("overlap_frames",0)})
    return {"schema":1,"product":"AD-Segmented-Loop","session_id":state["id"],"fps":FPS,
            "output":Path(output).name,"options":options,"thresholds_calibrated":False,
            "segments":rows,"total_frames":sum(r["frames"] for r in rows),"cumulative_error_ms":cumulative}
