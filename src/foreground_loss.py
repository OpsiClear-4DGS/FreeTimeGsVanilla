"""Object transparency supervision for straight RGBA training images.

Uses the transparent-alpha approach described by ArthurBrussee/brush:
premultiply the target once, composite both images on the same background,
and supervise rendered alpha over the entire image (including empty space).
This is an independent PyTorch implementation; no Brush source is bundled.
Reference: https://github.com/ArthurBrussee/brush/tree/6378a76add3b93501abb55c2dc08d71688537679
"""

from torch import Tensor
from torch.nn import functional as F


def composite_foreground(
    rendered_rgb: Tensor,
    rendered_alpha: Tensor,
    target_rgb: Tensor,
    target_alpha: Tensor,
    background: Tensor,
) -> tuple[Tensor, Tensor, Tensor]:
    """Return matched RGB images and an unweighted, full-image alpha L1 loss.

    All images are NHWC in [0, 1]. The rasterizer RGB is premultiplied;
    target RGB is straight. Never mask the prediction or discard background
    errors: opaque black floaters must still receive a removal gradient.
    """
    if rendered_rgb.shape != target_rgb.shape or rendered_rgb.shape[-1] != 3:
        raise ValueError("Rendered and target RGB must share an NHWC raster")
    expected = (*target_rgb.shape[:-1], 1)
    if rendered_alpha.shape != expected or target_alpha.shape != expected:
        raise ValueError("Rendered and target alpha must align with the RGB raster")
    predicted = rendered_rgb + (1 - rendered_alpha) * background
    target = target_rgb * target_alpha + (1 - target_alpha) * background
    return predicted, target, F.l1_loss(rendered_alpha, target_alpha)
