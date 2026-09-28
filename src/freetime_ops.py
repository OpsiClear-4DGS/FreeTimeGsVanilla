"""The vanilla FreeTimeGS equations shared by training, export and viewing."""

import torch
from torch import Tensor

MIN_DURATION = 0.02
SPLAT_KEYS = frozenset({
    "means", "scales", "quats", "opacities", "sh0", "shN",
    "times", "durations", "velocities",
})


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
