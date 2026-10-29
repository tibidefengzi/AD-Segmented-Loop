"""Bridge an external source encode and MASK to H3's native AV sampler."""


def apply_edit_mask(latent, source_latent, mask):
    import torch
    import torch.nn.functional as F
    from comfy.nested_tensor import NestedTensor
    samples = latent["samples"]
    if not samples.is_nested or len(samples.unbind()) != 2:
        raise ValueError("需要 H3 参考条件输出的音视频 LATENT")
    video, audio = samples.unbind()
    source = source_latent["samples"]
    if source.is_nested or source.shape != video.shape or source.ndim != 5 or source.shape[1] != 24:
        raise ValueError("源视频编码尺寸或帧数与 H3 目标不一致，请先按当前段取材并使用相同分辨率编码")
    if mask.ndim != 3 or not mask.shape[0] or not torch.isfinite(mask).all():
        raise ValueError("需要有效的外部 MASK，形状为帧数×高度×宽度")
    mask = F.interpolate(mask.float().clamp(0, 1).unsqueeze(1), size=video.shape[-2:], mode="bilinear", align_corners=False)
    # H3's video token groups cover 1,4,4,4,4 pixel frames per 17-frame cycle.
    groups, cursor = [], 0
    for index in range(video.shape[2]):
        width = (1, 4, 4, 4, 4)[index % 5]
        start = min(cursor, mask.shape[0]-1)
        groups.append(mask[start:min(cursor+width, mask.shape[0])].amax(dim=0))
        cursor += width
    video_mask = torch.stack(groups, dim=1).unsqueeze(0).expand(video.shape[0], -1, -1, -1, -1)
    return {**latent, "samples": NestedTensor([source.clone(), audio]),
            "noise_mask": NestedTensor([video_mask, torch.ones_like(audio)])}
