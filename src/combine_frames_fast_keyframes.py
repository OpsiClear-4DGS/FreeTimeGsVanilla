#!/usr/bin/env python3
"""
Combine Keyframes with Velocity - Efficient version for keyframe-based training

This script:
1. Loads only KEYFRAME point clouds (not intermediate frames)
2. Computes velocity using the next frame (t → t+1)
3. Optionally applies SMART IMPORTANCE SAMPLING to reduce point count
4. Outputs keyframe data with accurate velocity vectors

Why this is efficient:
- For 300 frames with keyframe_step=5:
  - Full combine: 300 frames × ~800k points = ~240M points
  - Keyframe combine: 60 keyframes × ~800k points = ~48M points (5x smaller!)
- Velocity is computed from consecutive pairs (t, t+1) for accuracy

Smart Sampling (--use-smart-sampling):
- Reduces 48M+ points to a budget (e.g., 6M) while preserving:
  - Sparse background (inverse-density weighting)
  - Moving objects (velocity-magnitude weighting)
  - Foreground subject (center-distance weighting)
- Voxel size auto-estimated from first frame's point cloud statistics

Usage:
    # Basic (no sampling)
    python src/combine_frames_fast_keyframes.py \\
        --input-dir /path/to/triangulation/output \\
        --output-path /path/to/keyframes_with_velocity.npz \\
        --frame-start 0 --frame-end 299 \\
        --keyframe-step 5

    # With smart sampling (recommended for large datasets)
    python src/combine_frames_fast_keyframes.py \\
        --input-dir /path/to/triangulation/output \\
        --output-path /path/to/keyframes_smart_6M.npz \\
        --frame-start 0 --frame-end 299 \\
        --keyframe-step 5 \\
        --use-smart-sampling \\
        --total-budget 6000000

Output NPZ contains:
- positions: [N, 3] - 3D positions from keyframes only
- velocities: [N, 3] - velocity in meters/frame (t → t+1)
- colors: [N, 3] - RGB colors (normalized to [0, 1])
- times: [N, 1] - normalized time [0, 1]
- durations: [N, 1] - temporal duration (auto-computed based on keyframe_step)
- has_velocity: [N] - boolean mask for points with valid velocity
"""

import numpy as np
import argparse
from pathlib import Path
from scipy.spatial import cKDTree
from tqdm import tqdm
import os


def load_frame_data(input_dir: Path, frame_idx: int) -> tuple:
    """
    Load positions and colors for a single frame.

    Returns:
        positions: [N, 3] numpy array or None if file doesn't exist
        colors: [N, 3] numpy array or None
    """
    points_path = input_dir / f"points3d_frame{frame_idx:06d}.npy"
    colors_path = input_dir / f"colors_frame{frame_idx:06d}.npy"

    if not points_path.exists():
        return None, None

    positions = np.load(points_path).astype(np.float32)

    if colors_path.exists():
        colors = np.load(colors_path).astype(np.float32)
    else:
        colors = np.ones((len(positions), 3), dtype=np.float32) * 128

    return positions, colors


def compute_velocity_knn(
    pos_t: np.ndarray,
    pos_t1: np.ndarray,
    max_distance: float = 0.5,
    k: int = 1,
    n_workers: int = -1
) -> tuple:
    """
    Compute velocity for points at time t by finding nearest neighbors at t+1.

    Velocity = (P_{t+1} - P_t) / dt, where dt = 1 frame

    Args:
        pos_t: Positions at keyframe t [N, 3]
        pos_t1: Positions at frame t+1 [M, 3]
        max_distance: Maximum distance for valid velocity match
        k: Number of nearest neighbors
        n_workers: Number of workers for KDTree query (-1 = all cores)

    Returns:
        velocities: [N, 3] velocity vectors in meters/frame
        valid_mask: [N] boolean mask for points with valid velocity
    """
    if len(pos_t) == 0 or len(pos_t1) == 0:
        return np.zeros_like(pos_t), np.zeros(len(pos_t), dtype=bool)

    if k < 1:
        raise ValueError("k must be positive")
    k = min(k, len(pos_t1))

    # Build KDTree for t+1 frame
    tree = cKDTree(pos_t1, balanced_tree=True, compact_nodes=True)

    # Find nearest neighbors
    distances, indices = tree.query(pos_t, k=k, workers=n_workers)

    velocities = np.zeros_like(pos_t)

    if k > 1:
        # Average the displacement over the k nearest matches rather than trusting
        # a single one. A lone nearest neighbour across a multi-frame baseline is
        # frequently a different piece of surface, so its displacement is largely
        # triangulation noise; averaging k of them cuts that by ~sqrt(k) while
        # leaving genuine coherent motion intact. This previously computed k
        # neighbours and then discarded all but the nearest, making --k-neighbors
        # a no-op.
        valid_mask = distances[:, 0] < max_distance
        if valid_mask.any():
            disp = pos_t1[indices[valid_mask]] - pos_t[valid_mask][:, None, :]
            near = distances[valid_mask] < max_distance
            w = near.astype(np.float32)
            w /= np.maximum(w.sum(axis=1, keepdims=True), 1.0)
            velocities[valid_mask] = (disp * w[..., None]).sum(axis=1)
    else:
        valid_mask = distances < max_distance
        if valid_mask.any():
            matched_positions = pos_t1[indices[valid_mask]]
            displacement = matched_positions - pos_t[valid_mask]
            velocities[valid_mask] = displacement  # dt = 1 frame

    return velocities, valid_mask


def find_next_frame(input_dir: Path, keyframe: int, keyframe_step: int,
                    frame_end: int) -> tuple:
    """Pick the frame to measure displacement against, and report the gap.

    The velocity estimate needs a *second* point cloud. Frame ``keyframe + 1`` is
    the ideal partner - the shorter the baseline, the less chance the nearest
    neighbour is a different piece of surface - but per-frame triangulation is
    expensive and is often only run on the keyframes themselves. Falling back to
    ``keyframe + keyframe_step`` keeps the estimate available at the cost of a
    longer baseline, which is what the README describes:

        v = (pos[t + step] - pos[t]) / step

    Returns ``(next_frame, gap)`` with ``gap`` in frames, or ``(None, 0)`` when no
    later point cloud exists at all. Dividing the displacement by ``gap`` keeps
    the result in metres per frame regardless of which partner was used, which is
    the unit the trainer's loader expects (it rescales by the frame count).
    """
    for candidate in (keyframe + 1, keyframe + keyframe_step, frame_end):
        if candidate <= keyframe or candidate > min(keyframe + keyframe_step, frame_end):
            continue
        if (input_dir / f"points3d_frame{candidate:06d}.npy").exists():
            return candidate, candidate - keyframe
    return None, 0


def estimate_scene_scale(
    positions: np.ndarray,
    sample_size: int = 10000,
    k_neighbors: int = 5,
    seed: int = 42,
) -> dict:
    """
    Estimate scene scale from point cloud statistics for auto voxel size.

    This function analyzes a point cloud to determine appropriate voxel size
    for density-based sampling. It uses both local (nearest-neighbor) and
    global (bounding box) metrics to handle different scene types.

    Args:
        positions: [N, 3] point positions (only first frame needed for speed)
        sample_size: Number of points to sample for NN computation (default: 10000)
        k_neighbors: Number of neighbors for NN distance (default: 5)
        seed: Random seed for reproducible sampling

    Returns:
        dict with:
        - bbox_diagonal: Bounding box diagonal length (meters)
        - median_nn_dist: Median nearest-neighbor distance (local density proxy)
        - percentile_90_nn: 90th percentile NN distance (outlier-robust)
        - voxel_from_nn: Suggested voxel from NN (15x median)
        - voxel_from_bbox: Suggested voxel from bbox (1.5% diagonal)
        - suggested_voxel_size: Final recommendation (max of both, clamped to [0.01, 1.0])

    Heuristics:
        - voxel_size ~ 15x median NN distance (captures local structure)
        - voxel_size ~ 1.5% of bbox diagonal (captures global scale)
        - Use MAX of both to avoid over-clustering in sparse regions
    """
    n_points = len(positions)

    if n_points == 0:
        return {
            'bbox_diagonal': 1.0,
            'median_nn_dist': 0.01,
            'suggested_voxel_size': 0.1,
        }

    # --- 1. Bounding Box Diagonal ---
    bbox_min = positions.min(axis=0)
    bbox_max = positions.max(axis=0)
    bbox_diagonal = np.linalg.norm(bbox_max - bbox_min)

    # --- 2. Sample-based NN Distance (for efficiency) ---
    np.random.seed(seed)
    if n_points > sample_size:
        sample_idx = np.random.choice(n_points, sample_size, replace=False)
        sample_points = positions[sample_idx]
    else:
        sample_points = positions

    # Build KDTree and find k nearest neighbors
    tree = cKDTree(sample_points)
    distances, _ = tree.query(sample_points, k=k_neighbors + 1)  # +1 because first is self

    # Skip self-distance (index 0), take mean of k neighbors
    nn_distances = distances[:, 1:].mean(axis=1)

    median_nn_dist = np.median(nn_distances)
    percentile_90_nn = np.percentile(nn_distances, 90)

    # --- 3. Suggested Voxel Size ---
    # Heuristic: voxel should be large enough to contain "local neighborhoods"
    # but small enough to distinguish density variations

    # Option A: Based on local density (15x median NN distance)
    voxel_from_nn = median_nn_dist * 15

    # Option B: Based on global scale (1.5% of diagonal)
    voxel_from_bbox = bbox_diagonal * 0.015

    # Use the larger of the two (more conservative, avoids over-clustering)
    suggested_voxel_size = max(voxel_from_nn, voxel_from_bbox)

    # Clamp to reasonable range
    suggested_voxel_size = np.clip(suggested_voxel_size, 0.01, 1.0)

    return {
        'bbox_diagonal': bbox_diagonal,
        'bbox_min': bbox_min,
        'bbox_max': bbox_max,
        'median_nn_dist': median_nn_dist,
        'percentile_90_nn': percentile_90_nn,
        'voxel_from_nn': voxel_from_nn,
        'voxel_from_bbox': voxel_from_bbox,
        'suggested_voxel_size': suggested_voxel_size,
    }


def smart_density_velocity_sampling(
    positions: np.ndarray,
    velocities: np.ndarray,
    colors: np.ndarray,
    target_count: int,
    voxel_size: float = 0.05,
    velocity_weight: float = 5.0,
    center_weight: float = 2.0,
    seed: int = None,
) -> tuple:
    """
    Smart Importance Sampling for 4D Gaussian Splatting point clouds.

    Reduces millions of points to a budget while preserving:
    - Sparse background (inverse-density weighting)
    - Moving objects (velocity-magnitude weighting)
    - Foreground subject (center-distance weighting)

    Algorithm:
        1. Density via Voxel Hashing (O(N), fast):
           - Quantize points to 3D grid cells
           - Count points per voxel
           - w_density = 1 / sqrt(count)  # Sparse voxels get higher weight

        2. Velocity Magnitude:
           - w_velocity = 1 + (normalized_velocity * velocity_weight)
           - Moving points get up to (1 + velocity_weight)x boost

        3. Center Focus:
           - Distance from scene median (robust to outliers)
           - w_center = 1 + exp(-d²/2σ²) * center_weight
           - Points near center get up to (1 + center_weight)x boost

        4. Final probability: p = w_density * w_velocity * w_center (normalized)

    Args:
        positions: [N, 3] point positions
        velocities: [N, 3] velocity vectors (meters/frame)
        colors: [N, 3] RGB colors
        target_count: Number of points to sample
        voxel_size: Grid size for density estimation (auto-estimated if None)
        velocity_weight: Multiplier for moving points (default: 5.0)
        center_weight: Multiplier for center/foreground (default: 2.0)
        seed: Random seed for reproducibility

    Returns:
        Tuple of (sampled_positions, sampled_velocities, sampled_colors, selected_indices)
    """
    n_points = len(positions)

    # If we have fewer points than budget, return all
    if n_points <= target_count:
        return positions, velocities, colors, np.arange(n_points)

    if seed is not None:
        np.random.seed(seed)

    # --- 1. Compute Density Weights (Voxel Hashing) ---
    # Quantize points to integer grid cells - O(N) and much faster than KDTree
    voxel_indices = np.floor(positions / voxel_size).astype(np.int64)

    # Pack 3D coords into 1D hashable keys using spatial hashing
    # These are large primes that reduce collision probability
    voxel_keys = (voxel_indices[:, 0] * 73856093 ^
                  voxel_indices[:, 1] * 19349663 ^
                  voxel_indices[:, 2] * 83492791)

    # Count points per voxel
    unique_keys, inverse_indices, counts = np.unique(
        voxel_keys, return_inverse=True, return_counts=True
    )

    # Assign density weight: proportional to 1 / sqrt(count)
    # Points in dense voxels get low weight; points in sparse voxels get high weight
    # sqrt() smooths the transition so dense areas aren't completely ignored
    point_density_counts = counts[inverse_indices]
    w_density = 1.0 / np.sqrt(point_density_counts.astype(np.float32))

    # --- 2. Compute Velocity Weights ---
    vel_mags = np.linalg.norm(velocities, axis=1)

    # Normalize velocity to 0-1 range for weighting
    vel_max = vel_mags.max()
    if vel_max > 0:
        vel_norm = vel_mags / (vel_max + 1e-6)
    else:
        vel_norm = np.zeros_like(vel_mags)

    # Moving points get boosted probability
    w_velocity = 1.0 + (vel_norm * velocity_weight)

    # --- 3. Compute Center Focus Weights ---
    # Use median as robust center estimate (less affected by outliers)
    scene_center = np.median(positions, axis=0)
    dists = np.linalg.norm(positions - scene_center, axis=1)

    # Normalize distances: closer points get higher weight
    # Use exponential falloff for soft focus
    sigma = np.mean(dists) + 1e-6  # dynamic scale
    w_center = np.exp(-0.5 * (dists ** 2) / (sigma ** 2))
    w_center = 1.0 + (w_center * center_weight)

    # --- 4. Combine Weights ---
    # Total Probability Score = density × velocity × center
    probs = w_density * w_velocity * w_center

    # Normalize to sum to 1
    probs = probs / probs.sum()

    # --- 5. Sample ---
    # Use choice with p=probs. replace=False ensures unique points.
    selected_indices = np.random.choice(
        n_points,
        size=target_count,
        replace=False,
        p=probs
    )

    return (
        positions[selected_indices],
        velocities[selected_indices],
        colors[selected_indices],
        selected_indices
    )


def main():
    parser = argparse.ArgumentParser(
        description="Combine keyframes with velocity for efficient 4D training"
    )
    parser.add_argument(
        "--input-dir", type=str, required=True,
        help="Directory containing points3d_frameXXXXXX.npy files"
    )
    parser.add_argument(
        "--output-path", type=str, required=True,
        help="Output NPZ file path"
    )
    parser.add_argument(
        "--frame-start", type=int, default=0,
        help="First frame index"
    )
    parser.add_argument(
        "--frame-end", type=int, default=299,
        help="Last frame index"
    )
    parser.add_argument(
        "--keyframe-step", type=int, default=5,
        help="Step between keyframes (e.g., 5 means keyframes at 0, 5, 10, ...)"
    )
    parser.add_argument(
        "--max-velocity-distance", type=float, default=0.5,
        help="Maximum distance for k-NN velocity matching"
    )
    parser.add_argument(
        "--k-neighbors", type=int, default=1,
        help="Number of nearest neighbors for velocity matching"
    )
    parser.add_argument(
        "--sample-ratio", type=float, default=1.0,
        help="Sample ratio per keyframe (1.0 = keep all, 0.5 = keep 50%%)"
    )
    parser.add_argument(
        "--seed", type=int, default=42,
        help="Random seed for sampling"
    )
    # Smart sampling arguments
    parser.add_argument(
        "--total-budget", type=int, default=None,
        help="Total point budget for smart sampling (e.g., 6000000 for 6M points)"
    )
    parser.add_argument(
        "--use-smart-sampling", action="store_true",
        help="Enable smart importance sampling (inverse-density + velocity + center)"
    )
    parser.add_argument(
        "--voxel-size", type=float, default=None,
        help="Voxel size for density estimation in meters. If not set, auto-estimated from scene scale."
    )
    parser.add_argument(
        "--no-auto-voxel", action="store_true",
        help="Disable auto voxel size estimation (use fixed 0.1m if --voxel-size not provided)"
    )
    parser.add_argument(
        "--velocity-weight", type=float, default=5.0,
        help="Weight for moving points (default: 5.0 = 5x boost for fast motion)"
    )
    parser.add_argument(
        "--center-weight", type=float, default=2.0,
        help="Weight for center/foreground points (default: 2.0 = 2x boost)"
    )

    args = parser.parse_args()

    input_dir = Path(args.input_dir)
    output_path = Path(args.output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Calculate keyframe indices
    keyframes = list(range(args.frame_start, args.frame_end + 1, args.keyframe_step))
    endpoint = input_dir / f"points3d_frame{args.frame_end:06d}.npy"
    if args.frame_end not in keyframes and endpoint.exists():
        keyframes.append(args.frame_end)
    n_keyframes = len(keyframes)
    total_frames = args.frame_end - args.frame_start + 1

    # Normalized time step between frames
    time_denominator = max(total_frames - 1, 1)

    # Duration that bridges the keyframe gap (3x the gap for smooth overlap)
    gap_normalized = args.keyframe_step / time_denominator
    default_duration = gap_normalized * 3  # 3x overlap

    print("=" * 70)
    print("COMBINE KEYFRAMES WITH VELOCITY")
    print("=" * 70)
    print(f"Input directory: {input_dir}")
    print(f"Output path: {output_path}")
    print(f"\nFrame range: {args.frame_start} to {args.frame_end} ({total_frames} frames)")
    print(f"Keyframe step: {args.keyframe_step}")
    print(f"Number of keyframes: {n_keyframes}")
    print(f"Keyframes: {keyframes[:10]}..." if len(keyframes) > 10 else f"Keyframes: {keyframes}")
    print(f"\nVelocity Settings:")
    print(f"  Max distance: {args.max_velocity_distance}")
    print(f"  K-neighbors: {args.k_neighbors}")
    print(f"\nTemporal Settings:")
    print(f"  Keyframe gap (normalized): {gap_normalized:.4f}")
    print(f"  Auto duration (3x gap): {default_duration:.4f}")
    print(f"\nSampling:")
    print(f"  Sample ratio: {args.sample_ratio}")
    print("=" * 70)

    # Set random seed
    np.random.seed(args.seed)

    # Storage
    all_positions = []
    all_velocities = []
    all_colors = []
    all_times = []
    all_durations = []
    all_has_velocity = []

    # Stats
    total_points = 0
    total_valid_velocity = 0

    # Process each keyframe
    for i, keyframe in enumerate(tqdm(keyframes, desc="Processing keyframes")):
        next_frame, frame_gap = find_next_frame(
            input_dir, keyframe, args.keyframe_step, args.frame_end,
        )

        # Load keyframe data
        positions, colors = load_frame_data(input_dir, keyframe)

        if positions is None or len(positions) == 0:
            print(f"\n  Warning: Missing keyframe {keyframe}")
            continue

        n_points_original = len(positions)

        # Sample if requested
        if args.sample_ratio < 1.0:
            n_sample = int(len(positions) * args.sample_ratio)
            idx = np.random.choice(len(positions), n_sample, replace=False)
            positions = positions[idx]
            colors = colors[idx]

        n_points = len(positions)

        # Load next frame for velocity computation
        pos_next, _ = load_frame_data(input_dir, next_frame) if next_frame is not None else (None, None)

        if pos_next is not None and len(pos_next) > 0:
            # Compute velocity from t → t+1
            velocities, valid_mask = compute_velocity_knn(
                positions, pos_next,
                max_distance=args.max_velocity_distance * frame_gap,
                k=args.k_neighbors
            )
            velocities /= frame_gap
            n_valid = valid_mask.sum()
        else:
            # No next frame available (last keyframe or missing data)
            velocities = np.zeros_like(positions)
            valid_mask = np.zeros(n_points, dtype=bool)
            n_valid = 0

        # Compute normalized time for this keyframe
        t_normalized = (keyframe - args.frame_start) / time_denominator
        times = np.full((n_points, 1), t_normalized, dtype=np.float32)
        durations = np.full((n_points, 1), default_duration, dtype=np.float32)

        # Store
        all_positions.append(positions)
        all_velocities.append(velocities)
        all_colors.append(colors)
        all_times.append(times)
        all_durations.append(durations)
        all_has_velocity.append(valid_mask)

        total_points += n_points
        total_valid_velocity += n_valid

    if not all_positions:
        raise ValueError("No nonempty keyframe point clouds found in the requested range")

    # Concatenate
    print("\nConcatenating...")
    positions = np.concatenate(all_positions, axis=0)
    velocities = np.concatenate(all_velocities, axis=0)
    colors = np.concatenate(all_colors, axis=0)
    times = np.concatenate(all_times, axis=0)
    durations = np.concatenate(all_durations, axis=0)
    has_velocity = np.concatenate(all_has_velocity, axis=0)

    n_total_before_sampling = len(positions)

    # --- Pre-compute voxel size from FIRST frame only (fast!) ---
    voxel_size = args.voxel_size
    if args.use_smart_sampling and voxel_size is None and not args.no_auto_voxel and len(all_positions) > 0:
        first_frame_positions = all_positions[0]
        print(f"\n  Auto-estimating voxel size from first frame ({len(first_frame_positions):,} points)...")
        scale_stats = estimate_scene_scale(first_frame_positions, sample_size=10000, k_neighbors=5, seed=args.seed)
        voxel_size = scale_stats['suggested_voxel_size']
        print(f"    Bounding box diagonal: {scale_stats['bbox_diagonal']:.3f}m")
        print(f"    Median NN distance: {scale_stats['median_nn_dist']:.6f}m")
        print(f"    Voxel from NN (15x): {scale_stats['voxel_from_nn']:.4f}m")
        print(f"    Voxel from bbox (1.5%): {scale_stats['voxel_from_bbox']:.4f}m")
        print(f"    => Auto voxel size: {voxel_size:.4f}m")
    elif voxel_size is None:
        voxel_size = 0.1  # Default fallback

    # --- Smart Sampling (if enabled) ---
    if args.use_smart_sampling and args.total_budget is not None and n_total_before_sampling > args.total_budget:
        print("\n" + "=" * 70)
        print("SMART IMPORTANCE SAMPLING")
        print("=" * 70)
        print(f"  Before sampling: {n_total_before_sampling:,} points")
        print(f"  Target budget: {args.total_budget:,} points")
        print(f"  Reduction: {n_total_before_sampling / args.total_budget:.1f}x")
        print(f"\n  Sampling weights:")
        print(f"    Voxel size (density): {voxel_size:.4f}m")
        print(f"    Velocity weight: {args.velocity_weight}x (moving points boosted)")
        print(f"    Center weight: {args.center_weight}x (foreground boosted)")

        # Apply smart sampling
        positions, velocities, colors, selected_indices = smart_density_velocity_sampling(
            positions=positions,
            velocities=velocities,
            colors=colors,
            target_count=args.total_budget,
            voxel_size=voxel_size,
            velocity_weight=args.velocity_weight,
            center_weight=args.center_weight,
            seed=args.seed,
        )

        # Also subsample times, durations, and has_velocity
        times = times[selected_indices]
        durations = durations[selected_indices]
        has_velocity = has_velocity[selected_indices]

        print(f"\n  After sampling: {len(positions):,} points")
        print(f"  Points with velocity: {has_velocity.sum():,} ({100*has_velocity.sum()/len(positions):.1f}%)")

    # Normalize colors to [0, 1] if needed
    if colors.max() > 1.0:
        colors = colors / 255.0

    # Compute velocity statistics
    vel_mag = np.linalg.norm(velocities, axis=1)
    valid_vel_mag = vel_mag[has_velocity]

    print("\n" + "=" * 70)
    print("FINAL STATISTICS")
    print("=" * 70)
    print(f"Total keyframes processed: {len(all_positions)}/{n_keyframes}")
    print(f"Total points: {len(positions):,}")
    print(f"Points per keyframe: ~{len(positions) // len(all_positions):,}")
    print(f"\nVelocity Statistics:")
    print(f"  Points with valid velocity: {has_velocity.sum():,} ({100*has_velocity.sum()/len(positions):.1f}%)")
    if len(valid_vel_mag) > 0:
        print(f"  Valid velocity magnitude (m/frame):")
        print(f"    Mean: {valid_vel_mag.mean():.6f}")
        print(f"    Max:  {valid_vel_mag.max():.6f}")
        print(f"    Std:  {valid_vel_mag.std():.6f}")
    print(f"\nTime range: [{times.min():.4f}, {times.max():.4f}]")
    print(f"Unique time values: {len(np.unique(times))}")

    # Save NPZ
    print("\nSaving...")
    np.savez_compressed(
        output_path,
        # Main data
        positions=positions,
        velocities=velocities,  # In meters/frame (will be scaled by trainer)
        colors=colors,
        times=times,
        durations=durations,
        has_velocity=has_velocity,
        # Metadata
        frame_start=args.frame_start,
        frame_end=args.frame_end + 1,  # NPZ/trainer metadata uses an exclusive end.
        time_denominator=time_denominator,
        keyframe_step=args.keyframe_step,
        n_keyframes=n_keyframes,
        max_velocity_distance=args.max_velocity_distance,
        k_neighbors=args.k_neighbors,
        sample_ratio=args.sample_ratio,
        mode="keyframes_with_velocity",
    )

    file_size_mb = os.path.getsize(output_path) / (1024 * 1024)

    print("\n" + "=" * 70)
    print("COMPLETE")
    print("=" * 70)
    print(f"Saved to: {output_path}")
    print(f"File size: {file_size_mb:.1f} MB")
    print(f"\nNext step: Train with default_keyframe config:")
    print(f"  python src/simple_trainer_freetime_4d_pure_relocation.py default_keyframe \\")
    print(f"      --init-npz-path {output_path} \\")
    print(f"      --start-frame {args.frame_start} --end-frame {args.frame_end + 1}")
    print("=" * 70)


if __name__ == "__main__":
    main()
