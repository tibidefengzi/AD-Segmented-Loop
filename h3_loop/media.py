"""Original H3 timing, boundary inspection and lossless history storage."""
from fractions import Fraction
import math
from pathlib import Path

import numpy as np

from .audio import read_audio_entry
from . import frames
from .spec import FPS, snap_resolution, target_frames, validate_resolution

DEFAULT_THRESHOLDS={"frame_difference":.07,"brightness_jump":3.0,"histogram_distance":.19,"motion_jump":.015}


def small_frame(frame):
    if hasattr(frame,"detach"):
        frame=frame.detach().cpu().numpy()
    frame=np.asarray(frame)[...,:3]
    return frame[::max(1,frame.shape[0]//64),::max(1,frame.shape[1]//64)].astype(np.float32)


def boundary_bad(frame):
    image=small_frame(frame)
    if not np.isfinite(image).all():return True
    mean,std=float(image.mean()),float(image.std())
    return std<.004 and (mean<.006 or mean>.994)


def best_handoff(frames):
    results=[]
    for index,frame in enumerate(frames):
        image=small_frame(frame).mean(axis=2)
        lap=image[1:-1,:-2]+image[1:-1,2:]+image[:-2,1:-1]+image[2:,1:-1]-4*image[1:-1,1:-1]
        sharp=float(lap.var())
        motion=float(np.abs(small_frame(frame)-small_frame(frames[index-1])).mean()) if index else 0
        score=sharp/(sharp+.0001)-motion
        results.append({"index":index,"sharpness":sharp,"motion":motion,"score":score})
    return max(results,key=lambda item:(item["score"],item["index"])) if results else None


def snap_images_to_h3(images, alpha=None):
    """Center-crop frames to the largest H3-valid (32-multiple) canvas.

    Prefer crop over pad so committed history/tails never gain letterbox bars.
    Returns (images, alpha, note_or_None).
    """
    height, width = int(images.shape[1]), int(images.shape[2])
    target_w, target_h = snap_resolution(width, height)
    if target_w == width and target_h == height:
        return images, alpha, None
    if not target_w or not target_h:
        validate_resolution(width, height)  # raises with exact dims
    top = (height - target_h) // 2
    left = (width - target_w) // 2
    images = images[:, top:top + target_h, left:left + target_w]
    if alpha is not None:
        alpha = alpha[:, top:top + target_h, left:left + target_w]
    note = f"AidenH3: 解码分辨率 {width}×{height} 非 32 倍数，已中心裁切为 {target_w}×{target_h}"
    print(note)
    return images, alpha, {"from": [width, height], "to": [target_w, target_h],
                           "crop_xy": [left, top]}


def normalize_video(video,context,state,directory,detect_bad=True,strict_sync=False):
    """Keep exactly the independent target frames; never freeze missing frames."""
    import torch
    from comfy_api.input_impl import VideoFromComponents
    from comfy_api.latest._util import VideoComponents
    components=video.get_components()
    images=components.images
    if float(components.frame_rate)!=FPS:raise ValueError("H3 结果必须是 24 FPS")
    images, alpha_pre, snap_info = snap_images_to_h3(images, components.alpha)
    validate_resolution(images.shape[2],images.shape[1])
    entry=state["segments"][context["index"]]["entry"]
    target=target_frames(entry["seconds_float"])
    overlap=int(context.get("overlap_frames",0))
    window=min(8,len(images))
    bad={i for i in [*range(window),*range(max(window,len(images)-window),len(images))]
         if detect_bad and boundary_bad(images[i])}
    good=[i for i in range(len(images)) if i not in bad]
    # Continuum Finish already strips Join context. Commit must keep the first
    # `target` frames and set overlap_frames=0 — never treat Continuum output as
    # pixel-handoff (prefix+body). The old length heuristic only caught the
    # target..target+overlap-1 band; extend_seconds>target falsely fell through
    # to the pixel path, discarded the best continuity frames, and left merge
    # crossfading ghost seams + restarted audio.
    from .continuum import capture_metadata
    continuum_record = capture_metadata(directory, context, state)
    continuum_trimmed = continuum_record is not None
    # Continuum Finish already removed continuity. Continuation Commit body is
    # (segment target - trim): e.g. 124 - 22 = 102, not a second full 124.
    trim = int((continuum_record or {}).get("trim_frames") or 0)
    if continuum_trimmed and trim > 0 and target > trim:
        commit_target = target - trim
    else:
        commit_target = target
    if len(good) < commit_target:
        raise ValueError(f"有效生成帧不足：需要至少 {commit_target} 帧，实际 {len(good)} 帧。请加大 Continuum extend_seconds 或重做；不会冻结补帧。")
    if not continuum_trimmed and len(good) < target + overlap:
        raise ValueError(f"有效生成帧不足：需要 {target+overlap} 帧（含重叠），实际 {len(good)} 帧。请重做；不会冻结补帧。")
    if continuum_trimmed:
        why = "本次任务绑定的 Continuum 状态"
        print(f"AidenH3: Continuum 已裁重叠（{why}），按目标 {commit_target} 帧入库（收到 {len(good)}）")
        prefix = None
        overlap = 0
        indices = good[:commit_target]
    else:
        prefix=images[good[:overlap]] if overlap else None
        indices=good[overlap:overlap+target]
    body=images[indices]
    audio=components.audio or read_audio_entry(entry)
    if audio is not None:
        rate=int(audio["sample_rate"])
        # Continuum continuation body is shorter by trim; keep A/V length matched.
        body_seconds = commit_target / FPS
        original_samples=round(body_seconds*rate) if continuum_trimmed else round(entry["seconds_float"]*rate)
        waveform=audio["waveform"]
        # A generated soundtrack may contain the guide pre-roll; a supplied
        # target soundtrack already starts at the current segment boundary.
        start=round(overlap/FPS*rate) if waveform.shape[-1]>original_samples+rate/FPS else 0
        waveform=waveform[...,start:start+original_samples]
        if waveform.shape[-1]<original_samples:
            waveform=torch.nn.functional.pad(waveform,(0,original_samples-waveform.shape[-1]))
        if strict_sync:
            if rate!=48000:
                import torchaudio.functional
                waveform=torchaudio.functional.resample(waveform,rate,48000)
                rate=48000
            needed=commit_target*2000
            waveform=waveform[...,:needed]
            if waveform.shape[-1]<needed:waveform=torch.nn.functional.pad(waveform,(0,needed-waveform.shape[-1]))
        audio={"waveform":waveform,"sample_rate":rate}
    base_alpha = alpha_pre if alpha_pre is not None else components.alpha
    alpha=base_alpha[indices] if base_alpha is not None else None
    # 新版 comfy_api 移除了 bit_depth/color_space 参数；按当前签名按需传递。
    import inspect
    accepted=inspect.signature(VideoFromComponents.__init__).parameters
    extras={key:getattr(video,key,None) for key in ("bit_depth","color_space")}
    extras={key:value for key,value in extras.items() if key in accepted and value is not None}
    normalized=VideoFromComponents(VideoComponents(images=body,audio=audio,frame_rate=Fraction(FPS),metadata=components.metadata,alpha=alpha),
                                   **extras)
    keep=max(1,int(context.get("history_frames") or 5))
    token=context["token"]
    tail_name=f"tail_{token}.npy"
    tail=body[-keep:].detach().cpu().numpy()
    with (directory/tail_name).open("xb") as handle:frames.save_frames(handle,tail)
    details={"frames":commit_target,"fps":FPS,"width":int(body.shape[2]),"height":int(body.shape[1]),
             "tail_file":tail_name,"tail_frames":len(tail),"dropped_frames":sorted(bad),
             "overlap_frames":overlap,"strict_sync":bool(strict_sync),"handoff":best_handoff(tail)}
    if continuum_record:
        details["continuum_file"] = continuum_record["file"]
    if snap_info:
        details["resolution_snap"]=snap_info
    details["reanchor"]=bool(context.get("reanchor"))
    details["references"]=context.get("references",{})
    if prefix is not None:
        name=f"overlap_{token}.npy"
        with (directory/name).open("xb") as handle:frames.save_frames(handle,prefix.detach().cpu().numpy())
        details["overlap_file"]=name
    if audio:
        audio_name=f"audio_{token}.npy"
        with (directory/audio_name).open("xb") as handle:
            np.save(handle,audio["waveform"].detach().cpu().numpy(),allow_pickle=False)
        details.update(audio_file=audio_name,audio_sample_rate=int(audio["sample_rate"]),
                       audio_channels=int(audio["waveform"].shape[1]))
        details["audio_seconds"]=audio["waveform"].shape[-1]/audio["sample_rate"]
        details["sync_error_ms"]=(target/FPS-details["audio_seconds"])*1000
    else:details["sync_error_ms"]=0
    if context["index"]:
        previous=state["segments"][context["index"]-1]
        selected=previous.get("selected")
        version=next((v for v in (previous.get("versions") or []) if selected and v.get("id")==selected),None)
        if version is not None:
            details["reference_transition"]=version.get("references",{})!=details["references"]
    return normalized,details
