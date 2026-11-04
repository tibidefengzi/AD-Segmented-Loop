"""ComfyUI 私有接口的集中适配层：ComfyUI 升级改路径时只改这里，不散落到各节点。"""
from __future__ import annotations


def _load_attribute(name, candidates):
    """按给定顺序尝试导入（模块, 属性）；全部失败时抛带中文说明的 RuntimeError。"""
    errors = []
    for module, attribute in candidates:
        try:
            imported = __import__(module, fromlist=[attribute])
            return getattr(imported, attribute)
        except (ImportError, AttributeError) as exc:
            errors.append(f"{module}.{attribute}：{exc}")
    detail = "；".join(errors)
    raise RuntimeError(
        f"未能加载 ComfyUI 接口 {name}，当前 ComfyUI 版本可能不兼容，"
        f"请升级 ComfyUI 或反馈该问题（{detail}）"
    )


def video_from_file(path):
    """VIDEO 文件对象；依次兼容不同 ComfyUI 版本的私有模块路径。"""
    factory = _load_attribute("VideoFromFile", (
        ("comfy_api.input_impl.video_types", "VideoFromFile"),
        ("comfy_api.input_impl", "VideoFromFile"),
        ("comfy_api.latest._input_impl", "VideoFromFile"),
    ))
    return factory(str(path))


def video_components():
    """VideoComponents 类型；依次兼容不同 ComfyUI 版本的私有模块路径。"""
    return _load_attribute("VideoComponents", (
        ("comfy_api.util", "VideoComponents"),
        ("comfy_api.latest._util", "VideoComponents"),
    ))


def video_from_components(components, **options):
    """由 VideoComponents 构造 VIDEO 对象；兼容不同 ComfyUI 版本的私有模块路径。"""
    factory = _load_attribute("VideoFromComponents", (
        ("comfy_api.input_impl.video_types", "VideoFromComponents"),
        ("comfy_api.input_impl", "VideoFromComponents"),
        ("comfy_api.latest._input_impl", "VideoFromComponents"),
    ))
    return factory(components, **options)


def executing_context():
    """当前节点执行上下文；接口不存在时返回 None（调用方跳过进度监听即可）。"""
    try:
        from comfy_execution.utils import get_executing_context
    except (ImportError, AttributeError):
        return None
    try:
        return get_executing_context()
    except Exception:
        return None
