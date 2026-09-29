"""Export the complete Vanilla model as a binary .ftgs.ply (see README.md)."""

import argparse
import math
import os
from pathlib import Path
import tempfile
from typing import Mapping

import numpy as np
import torch

from freetime_ops import (
    MIN_DURATION, validate_vanilla_checkpoint, validate_duration_bounds,
    checkpoint_duration_bounds,
)


@torch.no_grad()
def save_ftgs_ply(
    path: str | Path,
    splats: Mapping[str, torch.Tensor],
    *,
    use_velocity: bool = True,
    n_frames: int | None = None,
    min_duration: float = MIN_DURATION,
    max_duration: float | None = None,
    chunk_size: int = 65536,
) -> Path:
    """Write all Gaussians and SH bands without per-frame filtering.

    Spatial fields follow the usual 3DGS PLY layout (raw log-scales, opacity
    logits, wxyz rotations, channel-major SH). Extra fields are ``time``,
    ``log_duration`` and ``velocity_0..2`` in normalized-time units. Duration
    uses the same minimum as training/checkpoint loading. A header comment
    records whether motion is enabled, preserving static-position ablations.

    Chunked staging avoids copying a multi-million-splat model all at once.
    Replace the destination only after the complete file has been written.
    """
    path = Path(path)
    if not path.name.endswith(".ftgs.ply"):
        raise ValueError("Output path must end with .ftgs.ply")
    if chunk_size < 1:
        raise ValueError("chunk_size must be positive")
    if n_frames is not None and n_frames < 1:
        raise ValueError("n_frames must be positive")
    min_duration, max_duration = validate_duration_bounds(min_duration, max_duration)
    validate_vanilla_checkpoint({"splats": splats})

    n = len(splats["means"])
    rest = splats["shN"]
    if rest.ndim != 3 or rest.shape[2] != 3:
        raise ValueError("shN must have shape [N, K, 3]")
    k = rest.shape[1]
    bands = math.isqrt(k + 1)
    if bands * bands != k + 1:
        raise ValueError("shN must contain complete spherical harmonic bands")
    shapes = {
        "means": (n, 3), "scales": (n, 3), "quats": (n, 4),
        "opacities": (n,), "sh0": (n, 1, 3), "shN": (n, k, 3),
        "times": (n, 1), "durations": (n, 1), "velocities": (n, 3),
    }
    for name, shape in shapes.items():
        value = splats[name]
        if value.shape != shape or not value.is_floating_point():
            raise ValueError(f"{name} must be a floating-point tensor of shape {shape}")

    fields = (
        ["x", "y", "z", "nx", "ny", "nz"]
        + [f"f_dc_{i}" for i in range(3)]
        + [f"f_rest_{i}" for i in range(3 * k)]
        + ["opacity", "scale_0", "scale_1", "scale_2"]
        + [f"rot_{i}" for i in range(4)]
        + ["time", "log_duration", "velocity_0", "velocity_1", "velocity_2"]
    )
    comments = [
        "ftgs_version 1", "time_units normalized",
        f"sh_degree {bands - 1}", f"use_velocity {int(use_velocity)}",
        f"min_duration {min_duration}", "opacity_floor 0.0001",
    ]
    if n_frames is not None:
        comments.append(f"n_frames {int(n_frames)}")
    header = "\n".join([
        "ply", "format binary_little_endian 1.0",
        *(f"comment {comment}" for comment in comments),
        f"element vertex {n}", *(f"property float {name}" for name in fields),
        "end_header", "",
    ]).encode("ascii")

    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", delete=False) as file:
            temp_path = Path(file.name)
            file.write(header)
            for start in range(0, n, chunk_size):
                stop = min(start + chunk_size, n)
                arrays = {
                    name: value[start:stop].detach().to(device="cpu", dtype=torch.float32).numpy()
                    for name, value in splats.items()
                }
                for name, value in arrays.items():
                    if not np.isfinite(value).all():
                        raise ValueError(f"{name} contains non-finite values in rows [{start}, {stop})")
                rows = np.zeros((stop - start, len(fields)), dtype="<f4")
                rows[:, :3] = arrays["means"]
                rows[:, 6:9] = arrays["sh0"][:, 0, :]
                end_sh = 9 + 3 * k
                rows[:, 9:end_sh] = arrays["shN"].transpose(0, 2, 1).reshape(stop - start, 3 * k)
                rows[:, end_sh] = arrays["opacities"]
                rows[:, end_sh + 1:end_sh + 4] = arrays["scales"]
                rows[:, end_sh + 4:end_sh + 8] = arrays["quats"]
                rows[:, end_sh + 8] = arrays["times"][:, 0]
                rows[:, end_sh + 9] = np.clip(
                    arrays["durations"][:, 0], math.log(min_duration),
                    None if max_duration is None else math.log(max_duration),
                )
                rows[:, end_sh + 10:end_sh + 13] = arrays["velocities"]
                file.write(rows.tobytes())
        os.replace(temp_path, path)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
    return path


def export_checkpoint(
    ckpt_path: str | Path,
    output_path: str | Path | None = None,
    *,
    n_frames: int | None = None,
    use_velocity: bool | None = None,
) -> Path:
    """Export on CPU without constructing a trainer or loading the dataset."""
    checkpoint = torch.load(ckpt_path, map_location="cpu", weights_only=False)
    validate_vanilla_checkpoint(checkpoint)
    minimum, maximum = checkpoint_duration_bounds(checkpoint)
    path = save_ftgs_ply(
        output_path if output_path is not None else Path(ckpt_path).with_suffix(".ftgs.ply"),
        checkpoint["splats"],
        n_frames=n_frames if n_frames is not None else checkpoint.get("n_frames"),
        use_velocity=use_velocity if use_velocity is not None else checkpoint.get("use_velocity", True),
        min_duration=minimum, max_duration=maximum,
    )
    print(f"Saved {len(checkpoint['splats']['means']):,} Gaussians to {path}")
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ckpt", required=True, type=Path, help="Vanilla training checkpoint (.pt)")
    parser.add_argument("--output", type=Path, help="Output .ftgs.ply; defaults to the checkpoint's basename")
    parser.add_argument("--n-frames", type=int, help="Frame count for legacy checkpoints, or override recorded count")
    parser.add_argument("--velocity", action=argparse.BooleanOptionalAction, default=None,
                        help="Override checkpoint motion setting; legacy checkpoints default to enabled")
    args = parser.parse_args()
    export_checkpoint(args.ckpt, args.output, n_frames=args.n_frames, use_velocity=args.velocity)


if __name__ == "__main__":
    main()
