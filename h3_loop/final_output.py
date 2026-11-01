"""已选成果读取与最终保存；独立于H3的取段、规范化、会话和审批。"""
from __future__ import annotations
import json
import math
import uuid
from fractions import Fraction
from pathlib import Path, PurePosixPath

from . import assets
from .storage import ROOT


def parse_record(value):
    record = json.loads(value) if isinstance(value, str) else dict(value or {})
    if not isinstance(record, dict):
        raise ValueError("成果记录格式无效")
    return record


def describe_record(value):
    record = parse_record(value)
    name = str(record.get("file") or "").replace("\\", "/")
    if not name or name.startswith("/") or ":" in name or ".." in PurePosixPath(name).parts \
            or record.get("origin") not in {"input","output","media","session"}:
        raise ValueError("成果路径无效，请从预览缩略图重新选择")
    path = assets.resolve_source(record)
    if not path.is_file():
        raise ValueError("选中的成果文件已不存在，请重新选择")
    from .save_target import kind_for_path
    kind = kind_for_path(path)
    if kind not in {"image", "audio", "video"}:
        raise ValueError("请选择图片、音频或视频成果")
    stat = path.stat()
    # 纳秒时间戳超过JavaScript安全整数范围；跨前端持久化必须用字符串。
    identity = [str(stat.st_size), str(stat.st_mtime_ns)]
    if record.get("file_identity") and record["file_identity"] != identity:
        raise ValueError("选中的成果文件已变更，停止加工以免使用错误版本")
    result = {**record, "kind": kind, "file_identity": identity}
    if kind in {"video", "audio"}:
        import av
        with av.open(str(path)) as container:
            if kind == "video":
                stream = container.streams.video[0]
                rate = stream.average_rate or stream.base_rate
                result.update(width=stream.width,height=stream.height,fps=float(rate) if rate else 0,
                              codec=stream.codec_context.name,pix_fmt=stream.codec_context.pix_fmt)
            if container.streams.audio:
                audio = container.streams.audio[0]
                result.update(sample_rate=audio.codec_context.sample_rate,channels=audio.codec_context.channels)
    return result


def read_selected(value):
    record = describe_record(value)
    path = assets.resolve_source(record)
    if record["kind"] == "image":
        return assets.load_image_tensor(record, normalize=False)
    if record["kind"] == "video":
        from comfy_api.input_impl.video_types import VideoFromFile
        return VideoFromFile(str(path))
    import av
    import numpy as np
    import torch
    arrays = []
    with av.open(str(path)) as container:
        stream = container.streams.audio[0]
        rate = stream.codec_context.sample_rate
        channels = stream.codec_context.channels
        for frame in container.decode(audio=0):
            array = frame.to_ndarray()
            if not frame.format.is_planar:
                array = array.reshape(-1, channels).T
            if np.issubdtype(array.dtype, np.integer):
                info = np.iinfo(array.dtype)
                if info.min == 0:
                    array = (array.astype(np.float32) - (info.max+1)/2) / ((info.max+1)/2)
                else:
                    array = array.astype(np.float32) / max(abs(info.min),info.max)
            arrays.append(array.astype(np.float32))
    if not arrays:
        raise ValueError("选中的音频没有可读取的采样")
    return {"waveform":torch.from_numpy(np.concatenate(arrays,axis=1)).unsqueeze(0),"sample_rate":rate}


class SelectedResult:
    """只出现在最终加工提交副本中，不依赖原生成图。"""
    CATEGORY = "AD-Segmented-Loop/内部节点"
    @classmethod
    def INPUT_TYPES(cls):
        return {"required":{"record":("STRING",{"default":"{}"})}}
    RETURN_TYPES = ("*",)
    RETURN_NAMES = ("已选成果",)
    FUNCTION = "read"
    @classmethod
    def IS_CHANGED(cls, record):
        described = describe_record(record)
        return json.dumps(described,sort_keys=True,ensure_ascii=False)
    def read(self, record):
        return (read_selected(record),)


def final_directory(value, kind):
    from .save_target import configured_directory
    names = {"image":"图片","audio":"音频","video":"视频"}
    if str(value or "").strip():
        directory = Path(str(value).strip().strip('"')).expanduser()
        if not directory.is_absolute():
            raise ValueError("保存目录请填写绝对路径")
        directory = directory/names[kind]
    else:
        directory = configured_directory("", "").parent / "AD-Segmented-Loop-final" / names[kind]
    directory.mkdir(parents=True,exist_ok=True)
    return directory


def publish_final(path, kind, source, directory="", prefix="最终", suffix=""):
    from .save_target import BAD_CHARS
    from . import dedupe
    label = str(source.get("label") or Path(source.get("file") or "成果").stem)
    stem = BAD_CHARS.sub("_", f"{prefix}_{label}{suffix}").strip(" .")[:160] or "最终成果"
    target = final_directory(directory,kind)/f"{stem}_{uuid.uuid4().hex[:10]}{path.suffix}"
    # UUID命名；不覆盖以前的最终成果。
    dedupe.publish(path,target)
    record = {"origin":"media","file":path.name,"kind":kind,"label":label+suffix,
              "archive":str(target),"source_record":source,"final_id":uuid.uuid4().hex}
    if kind in {"video","audio"}:
        record.update({k:v for k,v in describe_record(record).items() if k in {"fps","width","height","codec","pix_fmt","sample_rate","channels"}})
    return record


class FinalSave:
    CATEGORY = "AD-Segmented-Loop"
    DESCRIPTION = "逐个加工选中的已保存成果，按图片/音频/视频保存；不应用H3帧率、尺寸和帧数限制。"
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "保存目录":("STRING",{"default":""}),
            "文件名前缀":("STRING",{"default":"最终"}),
            "帧序列帧率":("FLOAT",{"default":0.0,"min":0.0,"step":0.001}),
            "source_record":("STRING",{"default":"{}"})},
            "optional":{"media":("*",),"audio":("AUDIO",)}}
    RETURN_TYPES = ("*",)
    RETURN_NAMES = ("最终媒体",)
    FUNCTION = "save"
    OUTPUT_NODE = True
    def save(self, 保存目录="", 文件名前缀="最终", 帧序列帧率=0.0, source_record="{}", media=None, audio=None):
        source = parse_record(source_record)
        if media is None:
            raise ValueError("请连接处理后的图片、音频或视频到媒体输入")
        directory = ROOT/"media"
        directory.mkdir(parents=True,exist_ok=True)
        records = []
        identifier = "final_"+uuid.uuid4().hex
        if hasattr(media,"save_to"):
            # VideoFromFile原样保存；组件视频保持其帧率和实际画幅。
            extension = ".mp4"
            from comfy_api.input_impl.video_types import VideoFromFile
            if isinstance(media,VideoFromFile):
                original = media.get_stream_source()
                if isinstance(original,str) and Path(original).suffix.lower() in {".mp4",".mkv",".mov",".webm",".avi",".m4v"}:
                    extension = Path(original).suffix.lower()
            path = directory/(identifier+extension)
            width,height = media.get_dimensions()
            kwargs = {}
            if not isinstance(media,VideoFromFile):
                # 已经是帧组件，采用无损编码，奇数画幅用444，绝不改尺寸凑倍数。
                kwargs.update(crf=0,chroma_subsampling="444")
            media.save_to(str(path),**kwargs)
            records.append(publish_final(path,"video",source,保存目录,文件名前缀))
        elif isinstance(media,dict) and "waveform" in media:
            from scipy.io import wavfile
            import numpy as np
            waveform = media["waveform"]
            array = waveform.detach().cpu().float().numpy() if hasattr(waveform,"detach") else np.asarray(waveform,dtype=np.float32)
            if array.ndim == 3:
                if array.shape[0] != 1:
                    raise ValueError("单项音频请输入一份批次，多个成果请用逐个加工")
                array = array[0]
            if array.ndim == 1: array = array[None,:]
            rate = int(media["sample_rate"])
            if rate <= 0 or array.ndim != 2 or not array.shape[-1]:
                raise ValueError("音频采样率或数据无效")
            path = directory/(identifier+".wav")
            wavfile.write(str(path),rate,array.T.astype(np.float32))
            records.append(publish_final(path,"audio",source,保存目录,文件名前缀))
        elif hasattr(media,"detach"):
            import numpy as np
            from PIL import Image
            images = media if media.ndim == 4 else media.unsqueeze(0)
            if len(images) < 1: raise ValueError("没有可保存的图片帧")
            if source.get("kind") == "video":
                from comfy_api.input_impl.video_types import VideoFromComponents
                from comfy_api.util import VideoComponents
                fps = float(帧序列帧率 or source.get("fps") or 0)
                if not math.isfinite(fps) or fps <= 0:
                    raise ValueError("视频帧序列需要有效帧率；请连接视频合成节点或填写帧序列帧率")
                video = VideoFromComponents(VideoComponents(images=images,audio=audio,frame_rate=Fraction(str(fps))))
                return self.save(保存目录,文件名前缀,帧序列帧率,source_record,video)
            for index,frame in enumerate(images):
                array = (frame.detach().cpu().float().numpy()*255).clip(0,255).astype(np.uint8)
                if array.shape[-1] == 1: array=array[...,0]
                path = directory/f"{identifier}_{index+1:04d}.png"
                Image.fromarray(array).save(path)
                records.append(publish_final(path,"image",source,保存目录,文件名前缀,f"-{index+1}" if len(images)>1 else ""))
        else:
            raise ValueError("最终保存支持IMAGE、AUDIO或VIDEO数据")
        return {"ui":{"ad_final":records},"result":(media,)}


class MergeSpecificationError(ValueError):
    pass


def merge_final(records, directory="", prefix="最终合并", normalize=False):
    """新的最终合并路径；不复用固定24FPS的时间线合并。"""
    import tempfile
    sources = [describe_record(r) for r in records]
    if len(sources)<2: raise ValueError("请至少选择两份同类型最终成果")
    kinds = {r['kind'] for r in sources}
    if len(kinds)!=1 or sources[0]['kind'] not in {'video','audio'}:
        raise ValueError("图片按张保存；合并请选择同一分类的视频或音频")
    kind = sources[0]['kind']
    paths = [assets.resolve_source(r) for r in sources]
    # 使用中立MKV承载视频流，保留帧率/奇数尺寸；音频统一无损PCM容器。
    target = ROOT/'media'/('final_merge_'+uuid.uuid4().hex+('.mkv' if kind=='video' else '.wav'))
    target.parent.mkdir(parents=True,exist_ok=True)
    keys = ('width','height','fps','codec','pix_fmt','sample_rate','channels') if kind=='video' else ('sample_rate','channels')
    def signature(r): return tuple(r.get(k) for k in keys)
    same = all(signature(r)==signature(sources[0]) for r in sources)
    if not same and not normalize:
        raise MergeSpecificationError("所选成果的尺寸、帧率、音轨或编码不同。是否按第一条成果的规格统一后合并？")
    if same:
        with tempfile.NamedTemporaryFile(mode='w',suffix='.txt',dir=ROOT/'media',encoding='utf-8',delete=False) as listing:
            list_path=Path(listing.name)
            for path in paths:
                listing.write("file '"+str(path).replace('\\','/').replace("'","'\\''")+"'\n")
        try:
            arguments=['-v','error','-nostdin','-n','-f','concat','-safe','0','-i',str(list_path),'-map','0','-c','copy']
            if kind=='audio': arguments=['-v','error','-nostdin','-n','-f','concat','-safe','0','-i',str(list_path),'-vn','-c:a','pcm_f32le']
            result=assets._exec(arguments+[str(target)])
            if result.returncode: raise ValueError('最终合并失败：'+result.stderr.decode('utf-8','replace')[-600:])
        finally:
            list_path.unlink(missing_ok=True)  # 仅删除本函数刚创建的可重建concat清单。
    else:
        arguments=['-v','error','-nostdin','-n']
        for path in paths: arguments += ['-i',str(path)]
        filters=[]; inputs=[]
        first=sources[0]
        audio_enabled=kind=='audio' or any(r.get('sample_rate') for r in sources)
        for i,(record,path) in enumerate(zip(sources,paths)):
            duration=assets.probe_duration(path)
            if not duration: raise ValueError('无法读取成果时长')
            if kind=='video':
                rate=Fraction(str(first['fps'])).limit_denominator(100000)
                filters.append(f"[{i}:v]scale={first['width']}:{first['height']}:force_original_aspect_ratio=decrease,pad={first['width']}:{first['height']}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={rate.numerator}/{rate.denominator},format=yuv444p,setpts=PTS-STARTPTS[v{i}]")
                inputs.append(f'[v{i}]')
            if audio_enabled:
                rate=int(first.get('sample_rate') or 48000)
                channels=int(first.get('channels') or 2)
                layout='mono' if channels==1 else 'stereo' if channels==2 else f'{channels}c'
                chain=f'[{i}:a]aresample={rate},aformat=channel_layouts={layout},apad' if record.get('sample_rate') else f'anullsrc=r={rate}:cl={layout}'
                filters.append(f'{chain},atrim=duration={duration},asetpts=PTS-STARTPTS[a{i}]')
                inputs.append(f'[a{i}]')
        outputs=('[vout]' if kind=='video' else '')+('[aout]' if audio_enabled else '')
        filters.append(''.join(inputs)+f"concat=n={len(sources)}:v={int(kind=='video')}:a={int(audio_enabled)}"+outputs)
        arguments+=['-filter_complex',';'.join(filters)]
        if kind=='video': arguments+=['-map','[vout]','-c:v','libx264','-crf','0','-pix_fmt','yuv444p']
        if audio_enabled: arguments+=['-map','[aout]','-c:a','pcm_f32le']
        result=assets._exec(arguments+[str(target)])
        if result.returncode: raise ValueError('最终合并失败：'+result.stderr.decode('utf-8','replace')[-600:])
    return publish_final(target,kind,{'label':'合并成果','kind':kind,'items':sources},directory,prefix)
