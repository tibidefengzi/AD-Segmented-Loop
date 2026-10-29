"""帧缓存的压缩存取：tail/overlap 只需 8bit 精度，float32 存太占磁盘。

新写入用 uint8（体积约为原来的 1/4），读取时兼容旧 float32 文件。
"""
from __future__ import annotations

import numpy as np


def to_uint8(frames) -> np.ndarray:
    if hasattr(frames, "detach"):
        frames = frames.detach().cpu().numpy()
    array = np.asarray(frames)
    if array.dtype == np.uint8:
        return array
    return np.rint(np.clip(array.astype(np.float32), 0.0, 1.0) * 255).astype(np.uint8)


def save_frames(handle, frames) -> None:
    """把帧数组写成 uint8 npy；handle 为已打开的二进制文件或路径。"""
    np.save(handle, to_uint8(frames), allow_pickle=False)


def load_frames(source) -> np.ndarray:
    """读回帧数组并统一成 0..1 的 float32；兼容旧的 float32 文件与新的 uint8 文件。"""
    array = np.load(source, allow_pickle=False)
    if array.dtype == np.uint8:
        return array.astype(np.float32) / 255.0
    return array.astype(np.float32, copy=False)
