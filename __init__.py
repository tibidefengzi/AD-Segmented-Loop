"""分段循环：围绕独立 ComfyUI 节点的素材与预览控制。"""
from .h3_loop.nodes import NODE_CLASS_MAPPINGS, NODE_DISPLAY_NAME_MAPPINGS
from .h3_loop import routes  # noqa: F401

__version__ = "0.4.37"

WEB_DIRECTORY = "./web_glass"
__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]




