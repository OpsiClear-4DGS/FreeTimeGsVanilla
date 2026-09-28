"""Shared NPZ time and coordinate conversions for the vanilla loaders."""

from typing import Optional

import numpy as np


def read_init_arrays(path: str, frame_start: int, frame_end: int) -> dict:
    """Read float32 arrays and restrict them to an exclusive frame range.

    New files record their time denominator explicitly. Older files from this
    repo's combiner used an inclusive ``frame_end`` and divided by the frame
    count; other NPZ producers use an exclusive end and divide by count minus
    one. Keeping that distinction here avoids changing physical times on load.
    """
    if frame_end <= frame_start:
        raise ValueError("end_frame must be greater than start_frame")
    with np.load(path) as data:
        arrays = {
            key: np.asarray(data[key], dtype=np.float32)
            for key in ("positions", "velocities", "colors", "times")
        }
        arrays["times"] = arrays["times"].reshape(-1)
        n = len(arrays["positions"])
        arrays["durations"] = (
            np.asarray(data["durations"], dtype=np.float32).reshape(-1)
            if "durations" in data else np.full(n, 0.1, dtype=np.float32)
        )
        source_start = int(data.get("frame_start", 0))
        source_end = int(data.get("frame_end", 300))
        legacy_combiner = str(data.get("mode", "")) == "keyframes_with_velocity"
        source_span = float(data.get(
            "time_denominator",
            max(source_end - source_start + (1 if legacy_combiner else -1), 1),
        ))
        keyframe_step = int(data.get("keyframe_step", 5))
        has_durations = "durations" in data

    for key, values in arrays.items():
        shape = (n, 3) if key in ("positions", "velocities", "colors") else (n,)
        if values.shape != shape or not np.isfinite(values).all():
            raise ValueError(f"{key} must have shape {shape} and contain finite values")
    if not np.isfinite(source_span) or source_span <= 0:
        raise ValueError("NPZ time_denominator must be positive and finite")

    frame_positions = arrays["times"].astype(np.float64) * source_span + source_start
    # Float32 fractions can land just below their original integer frame.
    near_integer = np.abs(frame_positions - np.rint(frame_positions)) < 1e-4
    frame_positions[near_integer] = np.rint(frame_positions[near_integer])
    keep = (frame_positions >= frame_start) & (frame_positions < frame_end)
    if not keep.any():
        raise ValueError(f"No initialization points in frames [{frame_start}, {frame_end})")
    arrays = {key: values[keep] for key, values in arrays.items()}
    target_span = max(frame_end - frame_start - 1, 1)
    arrays["times"] = ((frame_positions[keep] - frame_start) / target_span).astype(np.float32)
    arrays["durations"] *= np.float32(source_span / target_span)
    arrays["velocities"] *= np.float32(target_span)
    if arrays["colors"].max() > 1.0:
        arrays["colors"] /= 255.0
    arrays["keyframe_step"] = keyframe_step
    arrays["has_durations"] = has_durations
    return arrays


def apply_scene_transform(
    positions: np.ndarray, velocities: np.ndarray, transform: Optional[np.ndarray]
) -> tuple[np.ndarray, np.ndarray]:
    """Transform points and velocity differences without promoting float32."""
    if transform is not None:
        transform = np.asarray(transform, dtype=np.float32)
        positions = positions @ transform[:3, :3].T + transform[:3, 3]
        velocities = velocities @ transform[:3, :3].T
    return positions.astype(np.float32), velocities.astype(np.float32)
