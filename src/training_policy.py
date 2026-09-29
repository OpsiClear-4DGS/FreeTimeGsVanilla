"""Explicit training phases and reproducible iteration sampling.

The Gaussian representation and rasterizer are unchanged. Phase transitions
adjust optimization without replacing parameters or resetting Adam momentum.
"""

from dataclasses import dataclass, fields, replace
import math
import random

import numpy as np
import torch
from torch.utils.data import Sampler


@dataclass
class TrainingPhase:
    start_step: int = 0
    freeze_times: bool | None = None
    freeze_durations: bool | None = None
    allow_relocation: bool | None = None
    position_lr_factor: float | None = None
    velocity_lr_factor: float | None = None
    appearance_lr_factor: float | None = None
    foreground_mse_weight: float | None = None
    lpips_interval: int | None = None


def validate_phases(cfg):
    if cfg.lpips_interval < 1 or not math.isfinite(cfg.lambda_foreground) or cfg.lambda_foreground < 0:
        raise ValueError("Invalid LPIPS interval or foreground MSE weight")
    previous = -1
    for phase in cfg.phases:
        if phase.start_step < 0 or phase.start_step <= previous:
            raise ValueError("Training phases must have strictly increasing nonnegative start steps")
        previous = phase.start_step
        for name in ("position_lr_factor", "velocity_lr_factor", "appearance_lr_factor", "foreground_mse_weight"):
            value = getattr(phase, name)
            if value is not None and (not math.isfinite(value) or value < 0):
                raise ValueError(f"Invalid phase {name}")
        if phase.lpips_interval is not None and phase.lpips_interval < 1:
            raise ValueError("Phase LPIPS interval must be positive")


def phase_at_step(cfg, step: int) -> TrainingPhase:
    phase = TrainingPhase(
        freeze_times=cfg.freeze_times, freeze_durations=cfg.freeze_durations,
        allow_relocation=True, position_lr_factor=1., velocity_lr_factor=1.,
        appearance_lr_factor=1., foreground_mse_weight=cfg.lambda_foreground,
        lpips_interval=cfg.lpips_interval,
    )
    for update in cfg.phases:
        if step < update.start_step:
            break
        phase = replace(phase, **{
            field.name: getattr(update, field.name) for field in fields(update)
            if getattr(update, field.name) is not None
        })
    return phase


def apply_phase(runner, step: int) -> TrainingPhase:
    cfg = runner.cfg
    phase = phase_at_step(cfg, step)
    horizon = runner.lr_schedule_steps
    progress = step / horizon
    rates = dict(
        means=cfg.position_lr * runner.scene_scale * .01 ** progress * phase.position_lr_factor,
        velocities=cfg.velocity_lr_start * (cfg.velocity_lr_end / cfg.velocity_lr_start) ** progress * phase.velocity_lr_factor,
        times=cfg.times_lr, durations=cfg.durations_lr,
    )
    for name in ("scales", "quats", "opacities", "sh0", "shN"):
        rates[name] = getattr(cfg, f"{name}_lr") * phase.appearance_lr_factor
    runner.splats["times"].requires_grad_(not phase.freeze_times)
    runner.splats["durations"].requires_grad_(not phase.freeze_durations)
    for name, optimizer in runner.optimizers.items():
        for group in optimizer.param_groups:
            group["lr"] = rates[name] * math.sqrt(cfg.batch_size)
    return phase


class IterationSampler(Sampler[int]):
    """A dedicated RNG and absolute iteration reproduce order after resume.

    DataLoader prefetch may advance its iterator, but checkpoint progress is
    defined by consumed training iterations rather than prefetched samples.
    """
    def __init__(self, size: int, start_step: int, stop_step: int, seed: int):
        if size < 1:
            raise ValueError("Training dataset must contain images")
        self.size, self.start, self.stop, self.seed = size, start_step, stop_step, seed

    def __len__(self):
        return max(0, self.stop - self.start)

    def __iter__(self):
        index = self.start
        while index < self.stop:
            epoch, offset = divmod(index, self.size)
            generator = torch.Generator().manual_seed(self.seed + epoch)
            order = torch.randperm(self.size, generator=generator).tolist()
            take = min(self.size - offset, self.stop - index)
            yield from order[offset:offset + take]
            index += take


def capture_rng_state(device=None):
    cuda = (device is not None and torch.device(device).type == "cuda")
    return dict(
        python=random.getstate(), numpy=np.random.get_state(), torch=torch.get_rng_state(),
        cuda=torch.cuda.get_rng_state(device) if cuda else None,
    )


def restore_rng_state(state, device=None):
    random.setstate(state["python"])
    np.random.set_state(state["numpy"])
    torch.set_rng_state(state["torch"].cpu())
    if state["cuda"] is not None and device is not None and torch.device(device).type == "cuda":
        torch.cuda.set_rng_state(state["cuda"].cpu(), device)
