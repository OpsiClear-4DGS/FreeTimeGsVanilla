"""The vanilla FreeTimeGS equations shared by training, export and viewing."""

import math

import torch
from torch import Tensor

MIN_DURATION = 0.02
SPLAT_KEYS = frozenset({
    "means", "scales", "quats", "opacities", "sh0", "shN",
    "times", "durations", "velocities",
})


def duration_bounds(
    n_frames: int, min_frames: float | None = None, max_frames: float | None = None,
) -> tuple[float, float | None]:
    """Convert temporal sigma bounds from frames to normalized time.

    None retains the legacy normalized minimum. Frame bounds use the same
    endpoint denominator as timestamps and velocities, including single frames.
    """
    if n_frames < 1:
        raise ValueError("n_frames must be positive")
    span = max(n_frames - 1, 1)
    minimum = MIN_DURATION if min_frames is None else min_frames / span
    maximum = None if max_frames is None else max_frames / span
    return validate_duration_bounds(minimum, maximum)


def validate_duration_bounds(minimum: float, maximum: float | None = None):
    if not math.isfinite(minimum) or minimum <= 0:
        raise ValueError("Minimum duration must be positive and finite")
    if maximum is not None and (not math.isfinite(maximum) or maximum < minimum):
        raise ValueError("Maximum duration must be finite and at least the minimum")
    return float(minimum), None if maximum is None else float(maximum)


def checkpoint_duration_bounds(checkpoint: dict) -> tuple[float, float | None]:
    spec = checkpoint.get("model_spec", {})
    return validate_duration_bounds(spec.get("min_duration", MIN_DURATION), spec.get("max_duration"))


def temporal_opacity(times: Tensor, log_durations: Tensor, t: float) -> Tensor:
    """Eq. 4; duration bounds are projected after optimization, not here."""
    return torch.exp(-0.5 * ((t - times) / log_durations.exp()) ** 2).squeeze(-1)


def positions_at_time(
    means: Tensor, velocities: Tensor, times: Tensor, t: float, use_velocity: bool = True
) -> Tensor:
    """Eq. 1, with the existing velocity ablation switch."""
    return means + velocities * (t - times) if use_velocity else means


def regularization_4d(base_opacity: Tensor, temporal: Tensor) -> Tensor:
    """Eq. 5: only base opacity receives the regularization gradient."""
    return (base_opacity * temporal.detach()).mean()


def validate_vanilla_checkpoint(checkpoint: dict) -> None:
    """Reject incompatible models instead of silently dropping parameters."""
    spec = checkpoint.get("model_spec", {})
    if (
        set(checkpoint["splats"]) != SPLAT_KEYS
        or checkpoint.get("color_correction")
        or spec.get("time_units", checkpoint.get("time_units", "normalized")) != "normalized"
        or spec.get("duration_activation", checkpoint.get("duration_activation", "exp")) != "exp"
        or spec.get("use_marginal_gating", checkpoint.get("use_marginal_gating", False))
        or spec.get("use_color_velocity", checkpoint.get("use_color_velocity", False))
    ):
        raise ValueError("Checkpoint is incompatible with the Vanilla FreeTimeGS model")
