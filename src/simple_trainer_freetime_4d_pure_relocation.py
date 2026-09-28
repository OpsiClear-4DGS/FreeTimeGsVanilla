"""
FreeTimeGS: 4D Gaussian Splatting Implementation
================================================

A complete implementation of 4D Gaussian Splatting for dynamic scene reconstruction,
based on the FreeTimeGS paper methodology.

Core Methodology
----------------
Each Gaussian has 8 learnable parameter groups:
    1. Position (µx): [N, 3] - Canonical 3D position
    2. Time (µt): [N, 1] - Canonical time (when Gaussian is most visible)
    3. Duration (s): [N, 1] - Temporal width (how long Gaussian is visible)
    4. Velocity (v): [N, 3] - Linear velocity vector
    5. Scale: [N, 3] - 3D scale (log space)
    6. Quaternion: [N, 4] - Rotation orientation
    7. Opacity (σ): [N] - Base opacity (logit space)
    8. Spherical Harmonics: [N, K, 3] - View-dependent color

Key Equations:
    - Motion: µx(t) = µx + v·(t - µt)
    - Temporal opacity: σ(t) = exp(-0.5 * ((t - µt) / s)²)
    - Combined opacity: σ_final = σ · σ(t)
    - 4D Regularization: Lreg(t) = (1/N) * Σ(σ · stop_gradient[σ(t)])

Training Phases (Annealing Strategy - per FreeTimeGS Paper)
----------------------------------------------------------
1. SETTLING PHASE (steps 0 to densification_start_step):
   - ALL 4D parameters enabled from step 0 (position, velocity, time, duration)
   - Motion enabled: µx(t) = µx + v·(t-µt)
   - High velocity LR (1e-2) to capture fast motion immediately
   - NO densification/pruning (let ROMA init settle into correct trajectories)
   - Temporal opacity active

2. REFINEMENT PHASE (steps densification_start_step+):
   - ALL 4D parameters continue optimizing
   - Velocity LR annealing (1e-2 → 1e-4) for fine-tuning
   - Densification/relocation enabled (MCMC or DefaultStrategy)
   - 4D regularization active
   - Periodic relocation of low-opacity Gaussians

Key Insight: Never freeze velocities! Freezing v=0 causes positions to drift
to average locations, destroying precise ROMA initialization.

Outputs
-------
At the end of training, the following are generated:

1. Checkpoints: {result_dir}/ckpts/ckpt_{step}.pt
   - Contains: splats state_dict, optimizer states, step number
   - Can be used to resume training or export

2. Trajectory Videos: {result_dir}/videos/
   - traj_4d_step{step}.mp4: RGB render with smooth camera + time progression
   - traj_duration_step{step}.mp4: Duration heatmap visualization
   - traj_velocity_step{step}.mp4: Velocity magnitude heatmap visualization

3. PLY Sequence: {result_dir}/ply_sequence_step{step}/
   - frame_000000.ply to frame_XXXXXX.ply
   - One PLY per frame with positions/opacities computed for that time

4. TensorBoard Logs: {result_dir}/tb/
   - Loss curves, metrics, histograms
   - Duration/velocity visualizations every 500 steps

Usage Examples
--------------
# Basic training with MCMC strategy
CUDA_VISIBLE_DEVICES=0 python simple_trainer_freetime_4d.py mcmc \\
    --data-dir /path/to/data \\
    --init-npz-path /path/to/init_points.npz \\
    --result-dir /path/to/results \\
    --start-frame 0 --end-frame 300

# Training with DefaultStrategy
CUDA_VISIBLE_DEVICES=0 python simple_trainer_freetime_4d.py default \\
    --data-dir /path/to/data \\
    --init-npz-path /path/to/init_points.npz \\
    --result-dir /path/to/results

# Resume training from checkpoint
CUDA_VISIBLE_DEVICES=0 python simple_trainer_freetime_4d.py mcmc \\
    --data-dir /path/to/data \\
    --init-npz-path /path/to/init_points.npz \\
    --result-dir /path/to/results \\
    --ckpt-path /path/to/results/ckpts/ckpt_29999.pt

# Export PLY sequence and videos from checkpoint (no training)
CUDA_VISIBLE_DEVICES=0 python simple_trainer_freetime_4d.py mcmc \\
    --data-dir /path/to/data \\
    --init-npz-path /path/to/init_points.npz \\
    --result-dir /path/to/results \\
    --ckpt-path /path/to/results/ckpts/ckpt_59999.pt \\
    --export-only

# Custom training parameters
CUDA_VISIBLE_DEVICES=0 python simple_trainer_freetime_4d.py mcmc \\
    --data-dir /path/to/data \\
    --init-npz-path /path/to/init_points.npz \\
    --result-dir /path/to/results \\
    --max-steps 60000 \\
    --velocity-lr-start 1e-2 \\
    --velocity-lr-end 5e-4 \\
    --init-duration 0.05 \\
    --render-traj-n-frames 240 \\
    --export-ply-format ply

Key Configuration Options
-------------------------
Data:
    --data-dir: Path to dataset with images and COLMAP sparse reconstruction
    --init-npz-path: Path to NPZ file with initial Gaussian data from ROMA
    --start-frame, --end-frame: Frame range to use

Training:
    --max-steps: Total training iterations (default: 60000)
    --warmup-steps: Warmup phase length (default: 500)
    --canonical-phase-steps: Canonical phase length (default: 2000)

Temporal:
    --init-duration: Initial duration for Gaussians (default: 0.1)
    --velocity-lr-start: Starting velocity learning rate (default: 5e-3)
    --velocity-lr-end: Ending velocity learning rate (default: 1e-4)

Regularization:
    --lambda-4d-reg: 4D regularization weight (default: 1e-3)
    --lambda-duration-reg: Duration regularization weight (default: 5e-4)

Checkpoint & Export:
    --ckpt-path: Path to checkpoint for resume/export
    --export-only: If set, only export PLY/videos from checkpoint
    --export-ply: Enable PLY sequence export (default: True)
    --export-ply-format: PLY format - "ply", "splat", or "ply_compressed"

Visualization:
    --render-traj-path: Trajectory type - "interp" or "ellipse"
    --render-traj-n-frames: Number of frames in trajectory video (default: 120)
    --render-traj-fps: Video FPS (default: 30)
"""

import json
import math
import os
import time
import shutil
from dataclasses import dataclass, field
from collections import defaultdict
from typing import Dict, List, Optional, Tuple, Union

import imageio
import numpy as np
import scipy.interpolate
import torch
import torch.nn.functional as F
import tqdm
import tyro
import yaml
from torch import Tensor
from torch.utils.tensorboard import SummaryWriter
from torchmetrics.image import PeakSignalNoiseRatio, StructuralSimilarityIndexMeasure
from fused_ssim import fused_ssim
from torchmetrics.image.lpip import LearnedPerceptualImagePatchSimilarity
from typing_extensions import Literal, assert_never

from gsplat.rendering import rasterization
from gsplat.strategy import DefaultStrategy, MCMCStrategy
from gsplat.strategy.ops import _update_param_with_optimizer, remove
from gsplat.distributed import cli
from gsplat.exporter import export_splats

import sys
# Add parent directory to path for datasets imports
# Add current directory (src/) to path for local imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from datasets.FreeTime_dataset import FreeTimeParser, FreeTimeDataset, skip_none_collate
from utils import knn, rgb_to_sh, set_random_seed
from init_common import read_init_arrays, apply_scene_transform
from freetime_ops import (
    MIN_DURATION, temporal_opacity, positions_at_time, regularization_4d,
    validate_vanilla_checkpoint,
)
from video_io import MP4Writer


# ============================================================
# Trajectory Generation Utilities (adapted from multinerf)
# ============================================================

def _normalize(x: np.ndarray) -> np.ndarray:
    """Normalization helper function."""
    return x / np.linalg.norm(x)


def _viewmatrix(lookdir: np.ndarray, up: np.ndarray, position: np.ndarray) -> np.ndarray:
    """Construct lookat view matrix."""
    vec2 = _normalize(lookdir)
    vec0 = _normalize(np.cross(up, vec2))
    vec1 = _normalize(np.cross(vec2, vec0))
    m = np.stack([vec0, vec1, vec2, position], axis=1)
    return m


def generate_interpolated_path(
    poses: np.ndarray,
    n_interp: int,
    spline_degree: int = 5,
    smoothness: float = 0.05,
    rot_weight: float = 0.1,
) -> np.ndarray:
    """Creates a smooth spline path between input keyframe camera poses.

    Args:
        poses: (n, 3, 4) array of input pose keyframes.
        n_interp: returned path will have n_interp * (n - 1) total poses.
        spline_degree: polynomial degree of B-spline.
        smoothness: parameter for spline smoothing, 0 forces exact interpolation.
        rot_weight: relative weighting of rotation/translation in spline solve.

    Returns:
        Array of new camera poses with shape (n_interp * (n - 1), 3, 4).
    """
    def poses_to_points(poses, dist):
        """Converts from pose matrices to (position, lookat, up) format."""
        pos = poses[:, :3, -1]
        lookat = poses[:, :3, -1] - dist * poses[:, :3, 2]
        up = poses[:, :3, -1] + dist * poses[:, :3, 1]
        return np.stack([pos, lookat, up], 1)

    def points_to_poses(points):
        """Converts from (position, lookat, up) format to pose matrices."""
        return np.array([_viewmatrix(p - l, u - p, p) for p, l, u in points])

    def interp(points, n, k, s):
        """Runs multidimensional B-spline interpolation on the input points."""
        sh = points.shape
        pts = np.reshape(points, (sh[0], -1))
        k = min(k, sh[0] - 1)
        tck, _ = scipy.interpolate.splprep(pts.T, k=k, s=s)
        u = np.linspace(0, 1, n, endpoint=False)
        new_points = np.array(scipy.interpolate.splev(u, tck))
        new_points = np.reshape(new_points.T, (n, sh[1], sh[2]))
        return new_points

    points = poses_to_points(poses, dist=rot_weight)
    new_points = interp(
        points, n_interp * (points.shape[0] - 1), k=spline_degree, s=smoothness
    )
    return points_to_poses(new_points)


@dataclass
class Config:
    """
    Configuration for FreeTimeGS 4D Gaussian Splatting Training.

    This configuration controls all aspects of training including data loading,
    optimization, regularization, checkpointing, and export options.
    """

    # ==================== Data Paths ====================
    data_dir: str = "data/4d_scene"
    """Path to dataset directory containing images and COLMAP sparse reconstruction."""

    result_dir: str = "results/freetime_4d"
    """Output directory for checkpoints, videos, PLY files, and tensorboard logs."""

    init_npz_path: Optional[str] = None
    """Path to NPZ file with initial Gaussian data. Contains positions,
    velocities, colors, times, and durations from ROMA triangulation."""

    data_factor: int = 1
    """Downsample factor for images. 1 = full resolution, 2 = half, etc."""

    test_every: int = 8
    """Use every N-th camera for validation (others used for training)."""

    # ==================== Frame Range ====================
    start_frame: int = 0
    """Starting frame index (inclusive). Time t=0 corresponds to this frame."""

    end_frame: int = 300
    """Ending frame index (exclusive). Time t=1 corresponds to end_frame - 1."""
    frame_step: int = 1
    """Step between frames when loading data."""

    # ==================== Sampling from Init NPZ ====================
    max_samples: int = 2_000_000
    """Maximum number of Gaussians to initialize from NPZ file."""

    sample_n_times: int = 10
    """Number of time points to sample from for better temporal coverage."""

    sample_high_velocity_ratio: float = 0.0
    """Ratio of high-velocity points to prioritize during sampling (0.0-1.0).
    WARNING: High values (0.8) tend to select noisy outlier points from triangulation
    errors rather than true motion. Use 0.0 for uniform spatial sampling."""

    use_stratified_sampling: bool = False
    """Use per-frame stratified sampling (Paper-Pure approach).
    When True: Samples equally from ALL frames for complete temporal coverage.
    When False: Uses standard sampling with high_velocity_ratio."""

    use_keyframe_sampling: bool = False
    """Use dense keyframe sampling (Budget-Efficient approach).
    When True: Samples densely from keyframes only (every keyframe_step frames).
    Velocity bridges the gaps between keyframes."""

    keyframe_step: int = -1
    """Step between keyframes when use_keyframe_sampling=True.
    Set to -1 to auto-read from NPZ metadata (recommended).
    E.g., keyframe_step=5 with 300 frames → 60 keyframes → ~133k points per keyframe for 8M budget."""

    # ==================== Smart Sampling (for downsampling NPZ) ====================
    use_smart_sampling: bool = True
    """Use smart importance sampling when downsampling from NPZ.
    Combines: (1) inverse-density weighting (preserve sparse background),
    (2) velocity boosting (preserve moving objects), (3) center focus (preserve foreground).
    Applied per-keyframe to maintain temporal coverage."""

    smart_sampling_voxel_size: float = -1.0
    """Voxel size for density estimation in smart sampling.
    Set to -1 to auto-estimate from scene scale (recommended)."""

    smart_sampling_velocity_weight: float = 5.0
    """Velocity weight for smart sampling. Moving points get up to (1 + weight)x boost."""

    smart_sampling_center_weight: float = 2.0
    """Center focus weight for smart sampling. Points near scene center get boosted."""

    # ==================== Budget Pruning (Pure Relocation Mode) ====================
    use_budget_pruning: bool = False
    """Enable manual budget pruning to maintain fixed Gaussian count.
    Used with pure relocation mode to free up slots for relocation."""

    budget_prune_every: int = 500
    """Prune to maintain budget every N steps."""

    budget_prune_threshold: float = 0.001
    """Prune Gaussians with opacity below this (more aggressive than relocation threshold)."""

    # ==================== Training ====================
    max_steps: int = 70_000
    """Total number of training iterations."""

    batch_size: int = 1
    """Batch size for training (number of images per iteration)."""

    steps_scaler: float = 1.0
    """Scale factor for all step-related parameters (for quick experiments)."""

    eval_steps: List[int] = field(default_factory=lambda: [15_000, 30_000, 45_000, 60_000])
    """Steps at which to run evaluation on validation set."""

    save_steps: List[int] = field(default_factory=lambda: [15_000, 30_000, 45_000, 60_000])
    """Steps at which to save checkpoints."""

    eval_sample_every: int = 60
    """Evaluate every N-th frame for faster validation (e.g., 300/60 = 5 frames)."""

    # ==================== Model ====================
    sh_degree: int = 3
    """Maximum spherical harmonics degree for view-dependent color."""

    sh_degree_interval: int = 1000
    """Steps between increasing SH degree (starts at 0, increases to sh_degree)."""

    init_opacity: float = 0.5
    """Initial opacity for Gaussians (before sigmoid)."""

    init_scale: float = 1.0
    """Scale factor for initial Gaussian sizes (computed from KNN distances)."""

    init_duration: float = -1.0
    """Initial temporal duration for Gaussians. Smaller = sharper temporal profiles.
    A duration of 0.2 means Gaussian is visible for ~20% of the sequence.
    Set to -1.0 to auto-compute from NPZ keyframe_step metadata."""

    auto_init_duration: bool = True
    """When init_duration is automatic, preserve valid per-point NPZ durations.
    Fill missing durations using keyframe_step / (total_frames - 1) times
    init_duration_multiplier."""

    init_duration_multiplier: float = 2.0
    """Multiplier for keyframe gap when auto-computing init_duration.
    E.g., with keyframe_step=5, total_frames=60: gap=5/60=0.083, duration=0.083*2=0.167.
    Use 2.0 for double coverage (recommended), 3.0 for triple overlap."""

    # ==================== Loss Weights ====================
    lambda_img: float = 0.8
    """Weight for L1 image reconstruction loss."""

    lambda_ssim: float = 0.2
    """Weight for SSIM structural similarity loss."""

    lambda_perc: float = 0.01
    """Weight for LPIPS perceptual loss."""

    lambda_4d_reg: float = 1e-3
    """Weight for 4D regularization loss: Lreg = (1/N) * Σ(σ * stop_grad[σ(t)]).
    Paper value: λreg = 1e-2. Reduced to 1e-3 to prevent over-suppression."""

    lambda_duration_reg: float = 1e-3
    """Weight for duration regularization. Penalizes wide temporal windows
    that cause temporal blur. Reduced to 1e-3 to prevent excessively narrow profiles."""

    # ==================== Training Phases (Annealing Strategy) ====================
    # NOTE: Per FreeTimeGS paper, we do NOT use warmup/canonical phases that freeze velocity.
    # Freezing velocity destroys ROMA initialization by causing positions to drift to average.
    # Instead: enable all 4D params from step 0, disable densification until ROMA settles.

    densification_start_step: int = 1000
    """Start densification/relocation/pruning after this step.
    During steps 0-1000: Let ROMA-initialized points settle into correct trajectories.
    After step 1000: Enable MCMC/relocation to add/remove Gaussians."""

    reg_4d_start_step: int = 0
    """Start 4D regularization from step 0. Aggressive regularization from the start
    forces temporal sparsity - the 'secret sauce' of vanilla FreeTimeGS."""

    # ==================== Learning Rates ====================
    position_lr: float = 1.6e-4
    """Learning rate for Gaussian positions."""

    scales_lr: float = 5e-3
    """Learning rate for Gaussian scales."""

    quats_lr: float = 1e-3
    """Learning rate for Gaussian rotations (quaternions)."""

    opacities_lr: float = 5e-2
    """Learning rate for Gaussian opacities."""

    sh0_lr: float = 2.5e-3
    """Learning rate for DC spherical harmonics (base color)."""

    shN_lr: float = 2.5e-3 / 20
    """Learning rate for higher-order spherical harmonics."""

    times_lr: float = 1e-3
    """Learning rate for canonical times. Conservative, similar to quats_lr."""

    durations_lr: float = 5e-3
    """Learning rate for temporal durations. Matches spatial scales_lr."""

    velocity_lr_start: float = 1e-2
    """Starting learning rate for velocities (annealed during training).
    High initial LR to quickly fix noisy ROMA initialization."""

    velocity_lr_end: float = 1e-4
    """Ending learning rate for velocities. For fine-tuning convergence."""

    use_velocity: bool = True
    """Enable velocity-based motion. When False, Gaussians are static in position
    and only temporal opacity determines visibility. Use False for per-frame
    triangulation without velocity estimation."""

    no_sampling: bool = False
    """Disable all sampling - use ALL points from NPZ. For high-capacity training."""

    random_bkgd: bool = False
    """Use random background color during training. Helps with floaters and
    improves edge quality by preventing the model from baking in a fixed background."""

    # ==================== Periodic Relocation ====================
    # NOTE: Relocation starts at densification_start_step (controlled above)
    use_relocation: bool = True
    """Enable periodic relocation of low-opacity Gaussians to high-gradient regions."""

    relocation_every: int = 100
    """Relocate Gaussians every N iterations (after densification_start_step)."""

    relocation_stop_iter: int = 50_000
    """Stop relocation after this many iterations (allow fine-tuning)."""

    relocation_opacity_threshold: float = 0.005
    """Gaussians with opacity below this are considered 'dead' and may be relocated."""

    relocation_max_ratio: float = 0.015
    """Maximum fraction of Gaussians to relocate per step (1.5% default, prevents scene darkening)."""

    relocation_lambda_grad: float = 0.5
    """Weight for gradient magnitude in relocation sampling score."""

    relocation_lambda_opa: float = 0.5
    """Weight for opacity in relocation sampling score."""

    # ==================== Pruning (Disabled by Default) ====================
    # NOTE: Pruning starts at densification_start_step (controlled above)
    use_pruning: bool = False
    """Enable custom pruning. Disabled by default as MCMCStrategy handles pruning."""

    prune_every: int = 500
    """Prune Gaussians every N iterations (after densification_start_step)."""

    prune_stop_iter: int = 20_000
    """Stop pruning after this many iterations."""

    prune_opacity_threshold: float = 0.005
    """Prune Gaussians with opacity below this threshold."""

    # ==================== Rendering ====================
    near_plane: float = 0.01
    """Near clipping plane for rendering."""

    far_plane: float = 1e10
    """Far clipping plane for rendering."""

    packed: bool = False
    """Use packed mode for rasterization (more memory efficient for large scenes)."""

    antialiased: bool = False
    """Use antialiased rasterization mode."""

    # ==================== Trajectory Rendering ====================
    render_traj_path: str = "arc"
    """Trajectory type:
    - 'arc': Gentle arc movement (±15 degrees), good for 4D time-focused videos (default)
    - 'interp': Smooth spline interpolation through all camera poses
    - 'dolly': Smooth push-in/pull-out with subtle lateral movement
    - 'fixed': Static camera (first training camera), pure time progression
    - 'ellipse_z': Full ellipse in XY plane
    - 'ellipse_y': Full ellipse in XZ plane
    """

    render_traj_arc_degrees: float = 30.0
    """Arc angle in degrees for 'arc' trajectory (total sweep, e.g., 30 = ±15 from center)."""

    render_traj_dolly_amount: float = 0.2
    """Dolly amount for 'dolly' trajectory (fraction of scene scale, positive = push in)."""

    render_traj_n_frames: int = 120
    """Number of frames in the trajectory video."""

    render_traj_fps: int = 30
    """Frames per second for trajectory video."""

    render_traj_time_frames: Optional[int] = None
    """Number of time samples to cover. Options:
    - None: Auto-compute from (end_frame - start_frame + 1) to cover all frames
    - 0: Use same as n_frames (old behavior)
    - >0: Use this exact number of time samples"""

    render_traj_camera_loops: int = 1
    """Number of times camera loops through trajectory while time progresses.
    E.g., set to 2 to have camera go through trajectory twice while covering all times."""

    # ==================== PLY Export ====================
    export_ply: bool = False  # Disabled by default (slow and large files)
    """Export PLY sequence at end of training. One PLY per frame with
    positions and opacities computed for that specific time point."""

    export_ply_steps: Optional[List[int]] = None
    """Steps at which to export PLY sequences. If None, uses save_steps.
    Set to [30000] to only export PLY at the final step (saves disk space)."""

    export_ply_format: Literal["ply", "splat", "ply_compressed"] = "ply"
    """PLY export format:
    - 'ply': Standard PLY format (supported by most viewers)
    - 'splat': Custom format for antimatter15 viewer
    - 'ply_compressed': Compressed format for Supersplat viewer"""

    export_ply_opacity_threshold: float = 0.01
    """Only export Gaussians with combined opacity (base × temporal) above this threshold.
    Higher = smaller files but may miss faint Gaussians. 0.01 = 1% opacity cutoff."""

    export_ply_compact: bool = True
    """Use compact/factored export to save disk space (~15x smaller).
    Saves one canonical 4D PLY with all static parameters + motion params,
    plus compact per-frame binary files with only positions and opacities."""

    export_ply_frame_step: int = 1
    """Export every Nth frame. Set to 5 to export every 5th frame (5x smaller)."""

    # ==================== Strategy ====================
    strategy: Union[DefaultStrategy, MCMCStrategy] = field(
        default_factory=lambda: DefaultStrategy(verbose=True)
    )
    """Densification strategy: DefaultStrategy or MCMCStrategy.
    Controls how Gaussians are split, cloned, and pruned during training."""

    # ==================== Miscellaneous ====================
    global_scale: float = 1.0
    """Global scale factor for the scene."""

    tb_every: int = 50  # Log and flush to TensorBoard every N steps
    """Log scalar metrics to TensorBoard every N steps (loss, PSNR, etc.)."""

    tb_image_every: int = 200
    """Log images to TensorBoard every N steps (ground truth vs rendered)."""

    disable_viewer: bool = True
    """Disable the interactive Viser viewer. Set to False to enable 3D viewer at localhost:8080."""

    lpips_net: Literal["vgg", "alex"] = "alex"
    """Network architecture for LPIPS perceptual loss:
    - 'alex': AlexNet-based (faster, default)
    - 'vgg': VGG-based (slightly more accurate)"""

    # ==================== Checkpoint & Resume ====================
    ckpt_path: Optional[str] = None
    """Path to checkpoint file (.pt) to resume training from or export from.
    Checkpoint contains: splats state_dict, optimizer states, and step number."""

    export_only: bool = False
    """If True and ckpt_path is provided, load checkpoint and export PLY/videos
    without training. Useful for generating outputs from a trained model."""

    def adjust_steps(self, factor: float):
        """Scale training steps by factor."""
        self.eval_steps = [int(i * factor) for i in self.eval_steps]
        self.save_steps = [int(i * factor) for i in self.save_steps]
        if self.export_ply_steps is not None:
            self.export_ply_steps = [int(i * factor) for i in self.export_ply_steps]
        self.max_steps = int(self.max_steps * factor)
        self.sh_degree_interval = int(self.sh_degree_interval * factor)
        self.densification_start_step = int(self.densification_start_step * factor)
        self.reg_4d_start_step = int(self.reg_4d_start_step * factor)
        # Handle sentinel value: -1 means compute as 0.9 * max_steps
        if self.relocation_stop_iter < 0:
            self.relocation_stop_iter = int(0.9 * self.max_steps)
        else:
            self.relocation_stop_iter = int(self.relocation_stop_iter * factor)
        self.prune_stop_iter = int(self.prune_stop_iter * factor)

        strategy = self.strategy
        if isinstance(strategy, DefaultStrategy):
            strategy.refine_start_iter = int(strategy.refine_start_iter * factor)
            strategy.refine_stop_iter = int(strategy.refine_stop_iter * factor)
            strategy.reset_every = int(strategy.reset_every * factor)
            strategy.refine_every = int(strategy.refine_every * factor)
        elif isinstance(strategy, MCMCStrategy):
            strategy.refine_start_iter = int(strategy.refine_start_iter * factor)
            strategy.refine_stop_iter = int(strategy.refine_stop_iter * factor)
            strategy.refine_every = int(strategy.refine_every * factor)


def _finish_init(arrays, indices, transform):
    values = {key: arrays[key][indices] for key in
              ("positions", "velocities", "colors", "times", "durations")}
    values["positions"], values["velocities"] = apply_scene_transform(
        values["positions"], values["velocities"], transform,
    )
    # Retain the original velocity-outlier cap.
    speed = np.linalg.norm(values["velocities"], axis=1, keepdims=True)
    values["velocities"] *= np.minimum(10.0 / np.maximum(speed, 1e-8), 1.0)
    print(f"[InitNPZ] Loaded {len(indices):,} points")
    return {
        key: torch.from_numpy(np.ascontiguousarray(value)).reshape(
            -1, 1 if key in ("times", "durations") else 3
        )
        for key, value in values.items()
    }


def _time_groups(times, total_frames):
    # Group by frame, not exact float time: per-point jitter must not create
    # millions of separate groups and a quadratic scan of the point cloud.
    frames = np.rint(times * max(total_frames - 1, 1)).astype(np.int64)
    order = np.argsort(frames, kind="stable")
    _, starts = np.unique(frames[order], return_index=True)
    return np.split(order, starts[1:])


def _sample_groups(arrays, groups, budget, high_velocity_ratio=0.0, smart=None):
    if budget <= 0 or budget >= sum(len(group) for group in groups):
        return np.concatenate(groups)
    if budget < len(groups):
        groups = [groups[i] for i in np.linspace(0, len(groups) - 1, budget, dtype=int)]
    selected = []
    quota, remainder = divmod(budget, len(groups))
    for i, group in enumerate(groups):
        take = min(len(group), quota + (i < remainder))
        if take == len(group):
            chosen = group
        elif smart is not None:
            chosen = group[smart_sample_points(
                arrays["positions"][group], arrays["velocities"][group],
                arrays["colors"][group], take, seed=42 + i, **smart,
            )]
        else:
            n_fast = int(take * high_velocity_ratio)
            speed = np.linalg.norm(arrays["velocities"][group], axis=1)
            ordered = group[np.argsort(-speed)]
            chosen = np.concatenate([
                ordered[:n_fast],
                np.random.choice(ordered[n_fast:], take - n_fast, replace=False),
            ])
        selected.append(chosen)
    return np.concatenate(selected)


def load_init_npz(
    npz_path: str, max_samples: int = 2_000_000, n_times: int = 3,
    high_velocity_ratio: float = 0.0, frame_start: int = 0,
    frame_end: int = 300, transform: Optional[np.ndarray] = None,
) -> Dict[str, torch.Tensor]:
    """Load normalized-time initialization, sampling evenly spaced frame groups."""
    if n_times < 1 or not 0 <= high_velocity_ratio <= 1:
        raise ValueError("n_times must be positive and high_velocity_ratio in [0, 1]")
    arrays = read_init_arrays(npz_path, frame_start, frame_end)
    groups = _time_groups(arrays["times"], frame_end - frame_start)
    if 0 < max_samples < len(arrays["times"]) and n_times < len(groups):
        groups = [groups[i] for i in np.linspace(0, len(groups) - 1, n_times, dtype=int)]
    indices = _sample_groups(arrays, groups, max_samples, high_velocity_ratio)
    return _finish_init(arrays, indices, transform)


def load_init_npz_stratified(
    npz_path: str, max_samples: int = 4_000_000, frame_start: int = 0,
    frame_end: int = 300, transform: Optional[np.ndarray] = None,
) -> Dict[str, torch.Tensor]:
    """Keep the original per-frame, half-high-velocity sampling policy."""
    arrays = read_init_arrays(npz_path, frame_start, frame_end)
    groups = _time_groups(arrays["times"], frame_end - frame_start)
    indices = _sample_groups(arrays, groups, max_samples, high_velocity_ratio=0.5)
    return _finish_init(arrays, indices, transform)


def estimate_voxel_size(positions: np.ndarray, sample_size: int = 10000, k_neighbors: int = 5) -> float:
    """
    Estimate voxel size from point cloud statistics.
    Uses both local (NN distance) and global (bbox) metrics.
    """
    from scipy.spatial import cKDTree

    n_points = len(positions)
    if n_points == 0:
        return 0.1

    # Bounding box diagonal
    bbox_min = positions.min(axis=0)
    bbox_max = positions.max(axis=0)
    bbox_diagonal = np.linalg.norm(bbox_max - bbox_min)

    # Sample-based NN distance
    if n_points > sample_size:
        sample_idx = np.random.choice(n_points, sample_size, replace=False)
        sample_points = positions[sample_idx]
    else:
        sample_points = positions

    tree = cKDTree(sample_points)
    distances, _ = tree.query(sample_points, k=k_neighbors + 1)
    nn_distances = distances[:, 1:].mean(axis=1)
    median_nn_dist = np.median(nn_distances)

    # Voxel size: max of 15x NN distance and 1.5% bbox
    voxel_from_nn = median_nn_dist * 15
    voxel_from_bbox = bbox_diagonal * 0.015
    voxel_size = max(voxel_from_nn, voxel_from_bbox)
    voxel_size = np.clip(voxel_size, 0.01, 1.0)

    return float(voxel_size)


def smart_sample_points(
    positions: np.ndarray,
    velocities: np.ndarray,
    colors: np.ndarray,
    target_count: int,
    voxel_size: float = 0.05,
    velocity_weight: float = 5.0,
    center_weight: float = 2.0,
    seed: int = None,
) -> np.ndarray:
    """
    Smart Importance Sampling for point clouds.

    Combines:
    1. Density (voxel hashing): sparse areas get higher weight
    2. Velocity: moving points get boosted
    3. Center focus: points near scene center get boosted

    Returns:
        Selected indices array
    """
    n_points = len(positions)
    if n_points <= target_count:
        return np.arange(n_points)

    rng = np.random.RandomState(seed) if seed is not None else np.random

    # --- 1. Density Weights (Voxel Hashing) ---
    voxel_indices = np.floor(positions / voxel_size).astype(np.int64)
    voxel_keys = (voxel_indices[:, 0] * 73856093 ^
                  voxel_indices[:, 1] * 19349663 ^
                  voxel_indices[:, 2] * 83492791)

    unique_keys, inverse_indices, counts = np.unique(
        voxel_keys, return_inverse=True, return_counts=True
    )
    point_density_counts = counts[inverse_indices]
    w_density = 1.0 / np.sqrt(point_density_counts.astype(np.float32))

    # --- 2. Velocity Weights ---
    vel_mags = np.linalg.norm(velocities, axis=1)
    vel_max = vel_mags.max()
    if vel_max > 0:
        vel_norm = vel_mags / (vel_max + 1e-6)
    else:
        vel_norm = np.zeros_like(vel_mags)
    w_velocity = 1.0 + (vel_norm * velocity_weight)

    # --- 3. Center Focus Weights ---
    scene_center = np.median(positions, axis=0)
    dists = np.linalg.norm(positions - scene_center, axis=1)
    sigma = np.mean(dists) + 1e-6
    w_center = np.exp(-0.5 * (dists ** 2) / (sigma ** 2))
    w_center = 1.0 + (w_center * center_weight)

    # --- 4. Combine & Sample ---
    probs = w_density * w_velocity * w_center
    probs = probs / probs.sum()

    selected_indices = rng.choice(
        n_points, size=target_count, replace=False, p=probs
    )
    return selected_indices


def load_init_npz_keyframe(
    npz_path: str, max_samples: int = 8_000_000, keyframe_step: int = -1,
    frame_start: int = 0, frame_end: int = 300,
    transform: Optional[np.ndarray] = None, init_duration: float = -1.0,
    init_duration_multiplier: float = 2.0, use_smart_sampling: bool = True,
    smart_voxel_size: float = -1.0, smart_velocity_weight: float = 5.0,
    smart_center_weight: float = 2.0,
) -> Dict[str, torch.Tensor]:
    """Sample the available keyframes, preserving usable per-point durations."""
    arrays = read_init_arrays(npz_path, frame_start, frame_end)
    step = keyframe_step if keyframe_step > 0 else arrays["keyframe_step"]
    if step <= 0 or init_duration_multiplier <= 0:
        raise ValueError("keyframe_step and init_duration_multiplier must be positive")
    fallback = step / max(frame_end - frame_start - 1, 1) * init_duration_multiplier
    if init_duration > 0:
        arrays["durations"].fill(init_duration)
    elif not arrays["has_durations"]:
        arrays["durations"].fill(fallback)
    else:
        arrays["durations"] = np.where(arrays["durations"] > 0, arrays["durations"], fallback)
    groups = _time_groups(arrays["times"], frame_end - frame_start)
    smart = None
    if use_smart_sampling and 0 < max_samples < len(arrays["times"]):
        smart = dict(
            voxel_size=(smart_voxel_size if smart_voxel_size > 0
                        else estimate_voxel_size(arrays["positions"])),
            velocity_weight=smart_velocity_weight, center_weight=smart_center_weight,
        )
    indices = _sample_groups(arrays, groups, max_samples, smart=smart)
    return _finish_init(arrays, indices, transform)


def resolve_init_duration(cfg: Config, durations: Tensor) -> float:
    """Resolve the auto sentinel before it enters log-duration or loss math."""
    if cfg.init_duration > 0:
        return float(cfg.init_duration)
    usable = durations[torch.isfinite(durations) & (durations > 0)]
    if usable.numel():
        return max(float(usable.median()), MIN_DURATION)
    step = cfg.keyframe_step if cfg.keyframe_step > 0 else 1
    return max(step / max(cfg.end_frame - cfg.start_frame - 1, 1)
               * cfg.init_duration_multiplier, MIN_DURATION)


def create_optimizers(cfg: Config, splats, scene_scale: float):
    """Use the same optimizer type and batch scaling for initialization and resume."""
    lrs = dict(
        means=cfg.position_lr * scene_scale, scales=cfg.scales_lr,
        quats=cfg.quats_lr, opacities=cfg.opacities_lr, sh0=cfg.sh0_lr,
        shN=cfg.shN_lr, times=cfg.times_lr, durations=cfg.durations_lr,
        velocities=cfg.velocity_lr_start,
    )
    bs = cfg.batch_size
    return {
        name: torch.optim.Adam(
            [{"params": parameter, "lr": lrs[name] * math.sqrt(bs), "name": name}],
            eps=1e-15 / math.sqrt(bs),
            betas=(1 - bs * (1 - 0.9), 1 - bs * (1 - 0.999)),
        )
        for name, parameter in splats.items()
    }


def create_splats_with_optimizers_4d(
    cfg: Config,
    init_data: Dict[str, torch.Tensor],
    scene_scale: float = 1.0,
    device: str = "cuda",
) -> Tuple[torch.nn.ParameterDict, Dict[str, torch.optim.Optimizer]]:
    """
    Create 4D Gaussian splats with temporal parameters.

    Parameters per Gaussian (8 from paper + extras):
    - means: [N, 3] - canonical position µx
    - scales: [N, 3] - log scales
    - quats: [N, 4] - quaternion orientation
    - opacities: [N] - logit of base opacity σ
    - sh0: [N, 1, 3] - DC spherical harmonics
    - shN: [N, K, 3] - higher-order SH (K = (sh_degree+1)^2 - 1)
    - times: [N, 1] - canonical time µt
    - durations: [N, 1] - log of temporal duration s
    - velocities: [N, 3] - linear velocity v
    """
    points = init_data['positions']
    velocities = init_data['velocities']
    colors = init_data['colors']
    times = init_data['times']
    durations = init_data['durations']

    N = len(points)

    # Compute scales from KNN
    dist2_avg = (knn(points, 4)[:, 1:] ** 2).mean(dim=-1)
    dist_avg = torch.sqrt(dist2_avg).clamp(min=1e-6)
    scales = torch.log(dist_avg * cfg.init_scale).unsqueeze(-1).repeat(1, 3)

    # Initialize parameters
    quats = torch.zeros((N, 4))
    quats[:, 0] = 1.0
    opacities = torch.logit(torch.full((N,), cfg.init_opacity))

    fallback_duration = resolve_init_duration(cfg, durations)
    durations = torch.where(
        torch.isfinite(durations) & (durations > 0), durations,
        torch.full_like(durations, fallback_duration),
    )
    if cfg.init_duration > 0:
        durations = durations.clamp_min(cfg.init_duration)
    log_durations = durations.clamp_min(MIN_DURATION).log()

    # SH colors
    sh_colors = torch.zeros((N, (cfg.sh_degree + 1) ** 2, 3))
    sh_colors[:, 0, :] = rgb_to_sh(colors)

    # Create parameter dict
    splats = torch.nn.ParameterDict({
        "means": torch.nn.Parameter(points),
        "scales": torch.nn.Parameter(scales),
        "quats": torch.nn.Parameter(quats),
        "opacities": torch.nn.Parameter(opacities),
        "sh0": torch.nn.Parameter(sh_colors[:, :1, :]),
        "shN": torch.nn.Parameter(sh_colors[:, 1:, :]),
        "times": torch.nn.Parameter(times),
        "durations": torch.nn.Parameter(log_durations),
        "velocities": torch.nn.Parameter(velocities),
    }).to(device)

    optimizers = create_optimizers(cfg, splats, scene_scale)

    return splats, optimizers


class FreeTime4DRunner:
    """FreeTimeGS 4D Gaussian Splatting Trainer."""

    def __init__(self, local_rank: int, world_rank: int, world_size: int, cfg: Config):
        if cfg.batch_size != 1:
            raise ValueError("Vanilla training requires batch_size=1; frames have independent timestamps")
        if cfg.end_frame <= cfg.start_frame or cfg.test_every < 1:
            raise ValueError("A nonempty frame range and positive test_every are required")
        set_random_seed(42 + local_rank)

        self.cfg = cfg
        self.world_rank = world_rank
        self.local_rank = local_rank
        self.world_size = world_size
        self.device = f"cuda:{local_rank}"

        # Setup directories
        os.makedirs(cfg.result_dir, exist_ok=True)
        self.ckpt_dir = f"{cfg.result_dir}/ckpts"
        os.makedirs(self.ckpt_dir, exist_ok=True)
        self.stats_dir = f"{cfg.result_dir}/stats"
        os.makedirs(self.stats_dir, exist_ok=True)
        self.render_dir = f"{cfg.result_dir}/renders"
        os.makedirs(self.render_dir, exist_ok=True)

        # Tensorboard
        self.writer = SummaryWriter(log_dir=f"{cfg.result_dir}/tb")

        # Load dataset
        self.parser = FreeTimeParser(
            data_dir=cfg.data_dir,
            factor=cfg.data_factor,
            normalize=True,
            test_every=cfg.test_every,
            start_frame=cfg.start_frame,
            end_frame=cfg.end_frame,
        )
        # Create test_set: every N-th camera for validation
        num_cameras = len(self.parser.camera_names)
        test_set = list(range(0, num_cameras, cfg.test_every))
        print(f"[FreeTime4D] Using {len(test_set)} cameras for validation (every {cfg.test_every}-th of {num_cameras})")

        self.trainset = FreeTimeDataset(self.parser, split="train", test_set=test_set)
        self.valset = FreeTimeDataset(self.parser, split="val", test_set=test_set)
        self.scene_scale = self.parser.scene_scale * 1.1 * cfg.global_scale
        print(f"[FreeTime4D] Scene scale: {self.scene_scale}")
        print(f"[FreeTime4D] Train: {len(self.trainset)}, Val: {len(self.valset)}")

        # Load init NPZ and initialize Gaussians
        if cfg.init_npz_path is None or not os.path.exists(cfg.init_npz_path):
            raise ValueError(f"Init NPZ not found: {cfg.init_npz_path}")

        transform = self.parser.transform if hasattr(self.parser, 'transform') else None

        # Choose sampling strategy based on config
        # no_sampling=True means use ALL points (max_samples=0 disables sampling)
        effective_max_samples = 0 if cfg.no_sampling else cfg.max_samples

        if cfg.no_sampling:
            print("[FreeTime4D] NO SAMPLING - using ALL points from NPZ")

        if cfg.use_keyframe_sampling:
            # Use -1 for auto-detection from NPZ metadata
            effective_keyframe_step = -1 if cfg.auto_init_duration else cfg.keyframe_step
            effective_init_duration = -1.0 if cfg.auto_init_duration else cfg.init_duration
            print(f"[FreeTime4D] Using KEYFRAME sampling")
            if cfg.auto_init_duration:
                print(f"[FreeTime4D] AUTO mode: keyframe_step and init_duration will be read from NPZ")
            else:
                print(f"[FreeTime4D] EXPLICIT mode: keyframe_step={cfg.keyframe_step}, init_duration={cfg.init_duration}")
            init_data = load_init_npz_keyframe(
                cfg.init_npz_path,
                max_samples=effective_max_samples,
                keyframe_step=effective_keyframe_step,
                frame_start=cfg.start_frame,
                frame_end=cfg.end_frame,
                transform=transform,
                init_duration=effective_init_duration,
                init_duration_multiplier=cfg.init_duration_multiplier,
                use_smart_sampling=cfg.use_smart_sampling,
                smart_voxel_size=cfg.smart_sampling_voxel_size,
                smart_velocity_weight=cfg.smart_sampling_velocity_weight,
                smart_center_weight=cfg.smart_sampling_center_weight,
            )
        elif cfg.use_stratified_sampling:
            print("[FreeTime4D] Using STRATIFIED sampling (per-frame uniform distribution)")
            init_data = load_init_npz_stratified(
                cfg.init_npz_path,
                max_samples=effective_max_samples,
                frame_start=cfg.start_frame,
                frame_end=cfg.end_frame,
                transform=transform,
            )
        else:
            print("[FreeTime4D] Using STANDARD sampling")
            init_data = load_init_npz(
                cfg.init_npz_path,
                max_samples=effective_max_samples,
                n_times=cfg.sample_n_times,
                high_velocity_ratio=cfg.sample_high_velocity_ratio,
                frame_start=cfg.start_frame,
                frame_end=cfg.end_frame,
                transform=transform,
            )

        # Camera-rig extent does not bound the geometry visible to the cameras.
        # Keep distant initialization points rather than deleting valid content.
        self.effective_init_duration = resolve_init_duration(cfg, init_data["durations"])

        # Create splats and optimizers
        self.splats, self.optimizers = create_splats_with_optimizers_4d(
            cfg, init_data, self.scene_scale, self.device
        )
        print(f"[FreeTime4D] Initialized {len(self.splats['means']):,} Gaussians")

        # Strategy state (for DefaultStrategy or MCMCStrategy)
        self.cfg.strategy.check_sanity(self.splats, self.optimizers)
        if isinstance(self.cfg.strategy, DefaultStrategy):
            self.strategy_state = self.cfg.strategy.initialize_state(scene_scale=self.scene_scale)
        elif isinstance(self.cfg.strategy, MCMCStrategy):
            self.strategy_state = self.cfg.strategy.initialize_state()
        else:
            assert_never(self.cfg.strategy)

        # Gradient accumulator for relocation sampling score
        self.grad_accum = torch.zeros(len(self.splats["means"]), device=self.device)
        self.grad_count = 0

        # Losses
        self.psnr = PeakSignalNoiseRatio(data_range=1.0).to(self.device)
        self.ssim = StructuralSimilarityIndexMeasure(data_range=1.0).to(self.device)
        self.lpips = LearnedPerceptualImagePatchSimilarity(
            net_type=cfg.lpips_net, normalize=True,
        ).to(self.device)

        # Track starting step for resume
        self.start_step = 0

        # Load checkpoint if provided
        if cfg.ckpt_path is not None:
            self.load_checkpoint(cfg.ckpt_path)

    def load_checkpoint(self, ckpt_path: str):
        """Load model and optimizer states from checkpoint."""
        print(f"\n[Checkpoint] Loading from: {ckpt_path}")

        if not os.path.exists(ckpt_path):
            raise FileNotFoundError(f"Checkpoint not found: {ckpt_path}")

        ckpt = torch.load(ckpt_path, map_location=self.device, weights_only=False)
        validate_vanilla_checkpoint(ckpt)
        self.cfg.use_velocity = bool(ckpt.get("use_velocity", self.cfg.use_velocity))

        # Reinitialize splats from checkpoint (handles size mismatch from densification)
        ckpt_splats = ckpt["splats"]
        N = ckpt_splats["means"].shape[0]
        print(f"  Checkpoint has {N:,} Gaussians")

        # Replace splats with checkpoint data
        self.splats = torch.nn.ParameterDict({
            "means": torch.nn.Parameter(ckpt_splats["means"]),
            "scales": torch.nn.Parameter(ckpt_splats["scales"]),
            "quats": torch.nn.Parameter(ckpt_splats["quats"]),
            "opacities": torch.nn.Parameter(ckpt_splats["opacities"]),
            "sh0": torch.nn.Parameter(ckpt_splats["sh0"]),
            "shN": torch.nn.Parameter(ckpt_splats["shN"]),
            "times": torch.nn.Parameter(ckpt_splats["times"]),
            "durations": torch.nn.Parameter(ckpt_splats["durations"]),
            "velocities": torch.nn.Parameter(ckpt_splats["velocities"]),
        }).to(self.device)
        self._project_temporal_params()
        print(f"  Loaded {len(self.splats['means']):,} Gaussians")

        # Set start step for resume
        self.start_step = ckpt.get("step", 0) + 1
        print(f"  Will resume from step {self.start_step}")

        # Resize gradient accumulator to match loaded Gaussians
        self.grad_accum = torch.zeros(len(self.splats["means"]), device=self.device)
        self.grad_count = 0

        # Reinitialize optimizers for the new splats (needed for resume training)
        if not self.cfg.export_only:
            cfg = self.cfg
            self.optimizers = create_optimizers(cfg, self.splats, self.scene_scale)

            # Load optimizer states if available
            if "optimizers" in ckpt:
                for name, opt_state in ckpt["optimizers"].items():
                    if name in self.optimizers:
                        try:
                            self.optimizers[name].load_state_dict(opt_state)
                        except Exception as e:
                            print(f"  Warning: Could not load optimizer state for {name}: {e}")
                print("  Loaded optimizer states")

            # Re-initialize strategy state for the loaded Gaussians
            self.cfg.strategy.check_sanity(self.splats, self.optimizers)
            if isinstance(self.cfg.strategy, DefaultStrategy):
                self.strategy_state = self.cfg.strategy.initialize_state(scene_scale=self.scene_scale)
            elif isinstance(self.cfg.strategy, MCMCStrategy):
                self.strategy_state = self.cfg.strategy.initialize_state()

    def export_from_checkpoint(self):
        """Export PLY sequence and videos from loaded checkpoint (no training)."""
        if self.cfg.ckpt_path is None:
            raise ValueError("No checkpoint path provided for export_only mode")

        step = self.start_step - 1  # The step at which checkpoint was saved
        print(f"\n[Export] Exporting from checkpoint at step {step}")

        if self.world_rank == 0:
            # Render trajectory videos
            self.render_traj(step=step)

            # Export PLY sequence
            if self.cfg.export_ply:
                self.export_ply_sequence(step=step)

        print("[Export] Complete!")

    def _turbo_colormap(self, values: Tensor) -> Tensor:
        """
        Apply turbo colormap to normalized values [0, 1].

        Args:
            values: [N] tensor with values in [0, 1]
        Returns:
            colors: [N, 3] RGB colors
        """
        # Turbo colormap approximation (attempt to match matplotlib's turbo)
        # Based on: https://gist.github.com/mikhailov-work/0d177465a8151eb6edd1768d07d17c74
        x = values.unsqueeze(-1)  # [N, 1]

        # Red channel
        r = (0.13572138 + x * (4.61539260 + x * (-42.66032258 + x * (132.13108234 + x * (-152.94239396 + x * 59.28637943)))))
        # Green channel
        g = (0.09140261 + x * (2.19418839 + x * (4.84296658 + x * (-14.18503333 + x * (4.27729857 + x * 2.82956604)))))
        # Blue channel
        b = (0.10667330 + x * (12.64194608 + x * (-60.58204836 + x * (110.36276771 + x * (-89.90310912 + x * 27.34824973)))))

        colors = torch.cat([r, g, b], dim=-1).clamp(0, 1)  # [N, 3]
        return colors

    def _add_colorbar(self, img: Tensor, vmin: float, vmax: float, label: str = "", bar_width: int = 40) -> Tensor:
        """
        Add a vertical colorbar to the right side of an image.

        Args:
            img: [3, H, W] image tensor
            vmin: minimum value for colorbar
            vmax: maximum value for colorbar
            label: label text for the colorbar
            bar_width: width of the colorbar in pixels

        Returns:
            img_with_bar: [3, H, W + bar_width + margin] image with colorbar
        """
        C, H, W = img.shape
        device = img.device

        # Create colorbar gradient (bottom=0/blue to top=1/red)
        gradient = torch.linspace(0, 1, H, device=device)  # [H] from 0 to 1
        gradient = gradient.flip(0)  # Flip so top is high value (red)
        gradient = gradient.unsqueeze(0).unsqueeze(-1).expand(1, H, bar_width)  # [1, H, bar_width]

        # Apply turbo colormap to gradient
        gradient_flat = gradient.reshape(-1)  # [H * bar_width]
        colorbar_colors = self._turbo_colormap(gradient_flat)  # [H * bar_width, 3]
        colorbar = colorbar_colors.reshape(H, bar_width, 3).permute(2, 0, 1)  # [3, H, bar_width]

        # Add black border around colorbar
        colorbar[:, 0:2, :] = 0  # Top border
        colorbar[:, -2:, :] = 0  # Bottom border
        colorbar[:, :, 0:2] = 0  # Left border
        colorbar[:, :, -2:] = 0  # Right border

        # Create margin (white space between image and colorbar)
        margin_width = 10
        margin = torch.ones(3, H, margin_width, device=device)

        # Add tick marks on the margin (pointing to colorbar)
        tick_length = 6
        # Top tick (max value) - red
        margin[:, 8:12, -tick_length:] = 0
        # Bottom tick (min value) - blue
        margin[:, H-12:H-8, -tick_length:] = 0
        # Middle tick
        margin[:, H//2-2:H//2+2, -tick_length:] = 0
        # Quarter ticks
        margin[:, H//4-1:H//4+1, -tick_length+2:] = 0
        margin[:, 3*H//4-1:3*H//4+1, -tick_length+2:] = 0

        # Concatenate: [img | margin | colorbar]
        img_with_bar = torch.cat([img, margin, colorbar], dim=2)

        return img_with_bar, vmin, vmax

    def render_duration_velocity_images(
        self,
        camtoworlds: Tensor,
        Ks: Tensor,
        width: int,
        height: int,
        t: float,
        add_colorbar: bool = True,
    ) -> Tuple[Tensor, Tensor]:
        """
        Render duration and velocity visualization images using turbo heatmap.

        Args:
            add_colorbar: If True, add colorbar with min/max values to the right side

        Returns:
            duration_img: [3, H, W(+colorbar)] - duration heatmap (blue=low, red=high)
            velocity_img: [3, H, W(+colorbar)] - velocity magnitude heatmap (blue=low, red=high)
        """
        # Get positions at time t (always apply motion)
        means = self.compute_positions_at_time(t)

        # Get temporal opacity (always compute)
        temporal_opacity = self.compute_temporal_opacity(t)

        quats = self.splats["quats"]
        scales = torch.exp(self.splats["scales"])
        base_opacity = torch.sigmoid(self.splats["opacities"])
        opacities = torch.clamp(base_opacity * temporal_opacity, min=1e-4)

        # --- Duration visualization ---
        # Get durations and normalize to [0, 1]
        durations_exp = torch.exp(self.splats["durations"]).squeeze(-1)  # [N]
        dur_min, dur_max = durations_exp.min().item(), durations_exp.max().item()
        dur_normalized = (durations_exp - dur_min) / (dur_max - dur_min + 1e-8)  # [N]

        # Apply turbo heatmap colormap
        dur_colors = self._turbo_colormap(dur_normalized)  # [N, 3]

        # Render duration image (sh_degree=None means colors are direct RGB)
        dur_render, _, _ = rasterization(
            means=means,
            quats=quats,
            scales=scales,
            opacities=opacities,
            colors=dur_colors,  # [N, 3]
            viewmats=torch.linalg.inv(camtoworlds),
            Ks=Ks,
            width=width,
            height=height,
            sh_degree=None,  # Direct RGB colors
            packed=self.cfg.packed,
            near_plane=self.cfg.near_plane,
            far_plane=self.cfg.far_plane,
        )
        duration_img = dur_render[0, ..., :3].clamp(0, 1).permute(2, 0, 1)  # [3, H, W]

        # --- Velocity visualization ---
        # Get velocity magnitude and normalize to [0, 1]
        vel_mag = self.splats["velocities"].norm(dim=-1)  # [N]
        vel_min, vel_max = vel_mag.min().item(), vel_mag.max().item()
        vel_normalized = (vel_mag - vel_min) / (vel_max - vel_min + 1e-8)  # [N]

        # Apply turbo heatmap colormap
        vel_colors = self._turbo_colormap(vel_normalized)  # [N, 3]

        # Render velocity image
        vel_render, _, _ = rasterization(
            means=means,
            quats=quats,
            scales=scales,
            opacities=opacities,
            colors=vel_colors,  # [N, 3]
            viewmats=torch.linalg.inv(camtoworlds),
            Ks=Ks,
            width=width,
            height=height,
            sh_degree=None,  # Direct RGB colors
            packed=self.cfg.packed,
            near_plane=self.cfg.near_plane,
            far_plane=self.cfg.far_plane,
        )
        velocity_img = vel_render[0, ..., :3].clamp(0, 1).permute(2, 0, 1)  # [3, H, W]

        # Add colorbars if requested
        if add_colorbar:
            duration_img, _, _ = self._add_colorbar(duration_img, dur_min, dur_max, "Duration")
            velocity_img, _, _ = self._add_colorbar(velocity_img, vel_min, vel_max, "Velocity")

        return duration_img, velocity_img

    def compute_temporal_opacity(self, t: float) -> Tensor:
        return temporal_opacity(self.splats["times"], self.splats["durations"], t)

    @torch.no_grad()
    def _project_temporal_params(self):
        self.splats["durations"].clamp_(min=math.log(MIN_DURATION))

    def compute_positions_at_time(self, t: float) -> Tensor:
        return positions_at_time(
            self.splats["means"], self.splats["velocities"], self.splats["times"],
            t, self.cfg.use_velocity,
        )

    def compute_4d_regularization(self, temporal: Tensor) -> Tensor:
        return regularization_4d(torch.sigmoid(self.splats["opacities"]), temporal)

    def rasterize_splats(
        self,
        camtoworlds: Tensor,
        Ks: Tensor,
        width: int,
        height: int,
        t: float,
        sh_degree: int,
        **kwargs,
    ) -> Tuple[Tensor, Tensor, Dict]:
        """
        Rasterize 4D Gaussians at time t.

        Vanilla FreeTimeGS: Always apply motion and temporal opacity.
        No static/dynamic split - all Gaussians are 4D.
        """
        # Position: ALWAYS apply velocity (motion equation)
        # μx(t) = μx + v·(t - μt)
        means = self.compute_positions_at_time(t)

        # Temporal opacity: ALWAYS compute
        # σ(t) = exp(-0.5 * ((t - μt) / s)²)
        temporal_opacity = self.compute_temporal_opacity(t)

        quats = self.splats["quats"]
        scales = torch.exp(self.splats["scales"])
        base_opacity = torch.sigmoid(self.splats["opacities"])

        # Combined opacity: σ(x,t) = σ(t) × σ
        opacities = base_opacity * temporal_opacity
        opacities = torch.clamp(opacities, min=1e-4)  # Prevent black dots

        colors = torch.cat([self.splats["sh0"], self.splats["shN"]], dim=1)

        rasterize_mode = "antialiased" if self.cfg.antialiased else "classic"
        renders, alphas, info = rasterization(
            means=means,
            quats=quats,
            scales=scales,
            opacities=opacities,
            colors=colors,
            viewmats=torch.linalg.inv(camtoworlds),
            Ks=Ks,
            width=width,
            height=height,
            packed=self.cfg.packed,
            sh_degree=sh_degree,
            near_plane=self.cfg.near_plane,
            far_plane=self.cfg.far_plane,
            absgrad=(
                self.cfg.strategy.absgrad
                if isinstance(self.cfg.strategy, DefaultStrategy)
                else False
            ),
            rasterize_mode=rasterize_mode,
            **kwargs,
        )

        info["temporal_opacity"] = temporal_opacity
        info["positions_at_t"] = means
        return renders, alphas, info

    def relocate_gaussians(self, step: int) -> int:
        """
        Periodic relocation of low-opacity Gaussians (from paper).

        Sampling score: s = λg·∇g + λo·σ
        - Dead Gaussians (low opacity) are relocated to high-score regions.
        - This is independent of DefaultStrategy/MCMCStrategy.
        """
        cfg = self.cfg

        with torch.no_grad():
            base_opacity = torch.sigmoid(self.splats["opacities"])
            n_total = len(base_opacity)

            # Find dead Gaussians (low base opacity)
            dead_mask = base_opacity < cfg.relocation_opacity_threshold
            n_dead_total = dead_mask.sum().item()

            if n_dead_total == 0:
                return 0

            alive_mask = ~dead_mask
            if alive_mask.sum() == 0:
                return 0

            # Cap the number of relocations to prevent scene darkening
            max_relocate = int(n_total * cfg.relocation_max_ratio)
            n_to_relocate = min(n_dead_total, max_relocate)

            if n_to_relocate == 0:
                return 0

            dead_idx_all = dead_mask.nonzero(as_tuple=True)[0]
            alive_idx = alive_mask.nonzero(as_tuple=True)[0]

            # If we have more dead than we can relocate, pick the lowest opacity ones
            if n_dead_total > n_to_relocate:
                dead_opacities = base_opacity[dead_idx_all]
                _, sorted_indices = dead_opacities.sort()
                dead_idx = dead_idx_all[sorted_indices[:n_to_relocate]]
            else:
                dead_idx = dead_idx_all

            n_dead = len(dead_idx)

            # Compute sampling score: s = λg·∇g + λo·σ
            # Normalize gradient accumulator
            if self.grad_count > 0:
                grad_score = self.grad_accum / self.grad_count
                grad_score = grad_score / (grad_score.max() + 1e-8)  # Normalize to [0, 1]
            else:
                grad_score = torch.zeros_like(base_opacity)

            # Sampling score for alive Gaussians
            alive_grad = grad_score[alive_idx]
            alive_opa = base_opacity[alive_idx]
            alive_opa_norm = alive_opa / (alive_opa.max() + 1e-8)

            sampling_score = cfg.relocation_lambda_grad * alive_grad + cfg.relocation_lambda_opa * alive_opa_norm
            sampling_score = sampling_score.clamp(min=1e-8)

            # Sample sources weighted by sampling score
            probs = sampling_score / sampling_score.sum()

            # torch.multinomial has 2^24 category limit, use numpy for large counts
            if len(probs) > 2**24:
                probs_np = probs.cpu().numpy().astype(np.float64)
                probs_np = probs_np / probs_np.sum()  # Renormalize for float64
                sampled_np = np.random.choice(len(probs_np), size=n_dead, replace=True, p=probs_np)
                sampled = torch.from_numpy(sampled_np).to(self.device)
            else:
                sampled = torch.multinomial(probs, n_dead, replacement=True)

            source_idx = alive_idx[sampled]

            # Copy parameters from source to dead
            def param_fn(name: str, p: Tensor) -> Tensor:
                if name == "means":
                    # Add small noise to positions
                    noise = torch.randn(n_dead, 3, device=self.device) * 0.01 * self.scene_scale
                    p[dead_idx] = p[source_idx] + noise
                elif name == "opacities":
                    # Reset opacity to slightly below source to let it learn
                    source_opa = torch.sigmoid(p[source_idx])
                    # Start at 80% of source opacity instead of fixed init_opacity
                    new_opa = source_opa * 0.8
                    p[dead_idx] = torch.logit(new_opa.clamp(0.01, 0.99))
                else:
                    # Copy other parameters
                    p[dead_idx] = p[source_idx]
                return torch.nn.Parameter(p, requires_grad=p.requires_grad)

            def opt_fn(key: str, v: Tensor) -> Tensor:
                # Reset optimizer state for relocated Gaussians
                v[dead_idx] = 0
                return v

            _update_param_with_optimizer(param_fn, opt_fn, self.splats, self.optimizers)

            # A new population needs a fresh gradient-score window.
            self.grad_accum.zero_()
            self.grad_count = 0

        return n_dead

    def prune_gaussians(self, step: int) -> int:
        """Prune low-opacity Gaussians during canonical phase."""
        cfg = self.cfg

        with torch.no_grad():
            base_opacity = torch.sigmoid(self.splats["opacities"])

            # Find Gaussians to prune
            prune_mask = base_opacity < cfg.prune_opacity_threshold
            n_prune = prune_mask.sum().item()

            if n_prune == 0:
                return 0

            # Don't prune all Gaussians
            n_remaining = (~prune_mask).sum().item()
            if n_remaining < 1000:
                print(f"[Prune] Would leave only {n_remaining} Gaussians, skipping")
                return 0

            # Remove pruned Gaussians
            remove(
                params=self.splats,
                optimizers=self.optimizers,
                state=self.strategy_state,
                mask=prune_mask,
            )

            # Resize gradient accumulator
            self.grad_accum = torch.zeros(len(self.splats["means"]), device=self.device)

        return n_prune

    def budget_prune_gaussians(self, step: int) -> int:
        """
        Aggressive pruning to maintain fixed Gaussian budget.

        Used in "Pure Relocation" mode where we start with dense initialization
        and want to free up slots for relocation to fill with useful Gaussians.

        This is MORE aggressive than regular pruning:
        - Lower opacity threshold (0.001 vs 0.005)
        - Always active (no stop iteration)
        - Doesn't check minimum remaining count as strictly
        """
        cfg = self.cfg

        with torch.no_grad():
            base_opacity = torch.sigmoid(self.splats["opacities"])
            n_total = len(base_opacity)

            # Find very low opacity Gaussians
            prune_mask = base_opacity < cfg.budget_prune_threshold
            n_prune = prune_mask.sum().item()

            if n_prune == 0:
                return 0

            # Always keep at least 10% of initial count
            min_keep = int(cfg.max_samples * 0.1)
            n_remaining = (~prune_mask).sum().item()

            if n_remaining < min_keep:
                # Sort by opacity and keep the top min_keep
                _, sorted_idx = base_opacity.sort(descending=True)
                keep_mask = torch.zeros_like(prune_mask)
                keep_mask[sorted_idx[:min_keep]] = True
                prune_mask = ~keep_mask
                n_prune = prune_mask.sum().item()

            if n_prune == 0:
                return 0

            # Remove pruned Gaussians
            remove(
                params=self.splats,
                optimizers=self.optimizers,
                state={},
                mask=prune_mask,
            )

            # Resize gradient accumulator
            self.grad_accum = torch.zeros(len(self.splats["means"]), device=self.device)

            if n_prune > 0:
                print(f"[Budget Prune] Step {step}: Removed {n_prune:,} Gaussians "
                      f"(opacity < {cfg.budget_prune_threshold}), "
                      f"remaining: {len(self.splats['means']):,}")

        return n_prune

    def train(self):
        cfg = self.cfg
        device = self.device

        # Save config
        if self.world_rank == 0:
            with open(f"{cfg.result_dir}/cfg.yml", "w") as f:
                yaml.dump(vars(cfg), f, default_flow_style=False)

        max_steps = cfg.max_steps

        # Resume at the same position-LR schedule index even for legacy files.
        position_lr = cfg.position_lr * self.scene_scale * math.sqrt(cfg.batch_size)
        for group in self.optimizers["means"].param_groups:
            group["initial_lr"] = position_lr
            group["lr"] = position_lr * 0.01 ** (self.start_step / max_steps)
        schedulers = [torch.optim.lr_scheduler.ExponentialLR(
            self.optimizers["means"], gamma=0.01 ** (1.0 / max_steps),
        )]

        # Data loader (with skip_none_collate to handle missing frames)
        trainloader = torch.utils.data.DataLoader(
            self.trainset, batch_size=cfg.batch_size, shuffle=True,
            num_workers=4, persistent_workers=True, pin_memory=True,
            collate_fn=skip_none_collate,
        )
        trainloader_iter = iter(trainloader)

        print("\n" + "="*70)
        print("[Vanilla FreeTimeGS] No static/dynamic split - all Gaussians are 4D")
        print("="*70)
        if cfg.use_velocity:
            print(f"  Motion: µx(t) = µx + v·(t-µt)  [ENABLED]")
            print(f"  Velocity LR Annealing: {cfg.velocity_lr_start:.0e} → {cfg.velocity_lr_end:.0e}")
        else:
            print(f"  Motion: DISABLED (static Gaussians)")
        print(f"  Temporal opacity: σ(t) = exp(-0.5*((t-µt)/s)²)  [ALWAYS ON]")
        print(f"  4D Regularization: Lreg = (1/N)Σ(σ·sg[σ(t)]), λ={cfg.lambda_4d_reg}")
        print(f"  Duration Regularization: λ={cfg.lambda_duration_reg}")
        print(f"  Periodic Relocation: every {cfg.relocation_every} steps (after step {cfg.densification_start_step})")
        if cfg.no_sampling:
            print(f"  [HIGH CAPACITY] No sampling - using ALL points from NPZ")
        if cfg.use_keyframe_sampling:
            print("  [KEYFRAME MODE] Keyframes and durations resolved from initialization")
        if cfg.use_stratified_sampling:
            print(f"  [PURE RELOCATION MODE] Stratified sampling enabled")
        if cfg.use_budget_pruning:
            print(f"  [PURE RELOCATION MODE] Budget pruning every {cfg.budget_prune_every} steps (threshold={cfg.budget_prune_threshold})")
        if cfg.packed:
            print(f"  [MEMORY] Packed mode enabled for large Gaussian counts")
        print("="*70 + "\n")

        # Resume from checkpoint if applicable
        start_step = self.start_step
        if start_step > 0:
            print(f"[Resume] Resuming training from step {start_step}")

        global_tic = time.time()
        pbar = tqdm.tqdm(range(start_step, max_steps))

        for step in pbar:
            # Load batch
            try:
                data = next(trainloader_iter)
            except StopIteration:
                trainloader_iter = iter(trainloader)
                data = next(trainloader_iter)

            # Skip if entire batch had missing frames
            if data is None:
                continue

            camtoworlds = data["camtoworld"].to(device)
            Ks = data["K"].to(device)
            pixels = data["image"].to(device) / 255.0
            height, width = pixels.shape[1:3]
            t = data["time"].to(device).mean().item()

            # Phase determination (Vanilla FreeTimeGS - no static/dynamic split)
            # All Gaussians are 4D from step 0. Only difference is densification timing.
            in_settling = step < cfg.densification_start_step  # Steps 0-1000: settling
            in_refinement = step >= cfg.densification_start_step  # Steps 1000+: refinement

            # Velocity LR Annealing (from step 0, per paper's "Annealing Motion Scheduler")
            # High LR initially to capture fast motion, decay for fine-tuning
            progress = step / max(max_steps, 1)
            vel_lr = cfg.velocity_lr_start * (cfg.velocity_lr_end / cfg.velocity_lr_start) ** progress
            for pg in self.optimizers["velocities"].param_groups:
                pg["lr"] = vel_lr * math.sqrt(cfg.batch_size)

            # Times and durations: constant LR (no annealing needed)
            for pg in self.optimizers["times"].param_groups:
                pg["lr"] = cfg.times_lr * math.sqrt(cfg.batch_size)
            for pg in self.optimizers["durations"].param_groups:
                pg["lr"] = cfg.durations_lr * math.sqrt(cfg.batch_size)

            # SH degree schedule
            sh_degree = min(step // cfg.sh_degree_interval, cfg.sh_degree)

            # Forward pass (vanilla FreeTimeGS: always 4D, no static mode)
            renders, alphas, info = self.rasterize_splats(
                camtoworlds, Ks, width, height, t, sh_degree,
            )
            colors = renders[..., :3]

            # Random background color (helps with floaters and edge quality)
            # Applied after rasterization by compositing with alpha
            if cfg.random_bkgd:
                bkgd = torch.rand(1, 3, device=device)
                colors = colors + bkgd * (1.0 - alphas)

            # Strategy pre-backward (for gradient accumulation)
            self.cfg.strategy.step_pre_backward(
                params=self.splats,
                optimizers=self.optimizers,
                state=self.strategy_state,
                step=step,
                info=info,
            )

            # ============================================================
            # LOSS COMPUTATION (Paper: L = λimg*L1 + λssim*SSIM + λperc*LPIPS + λreg*Lreg)
            # ============================================================

            # Clamp colors to [0, 1] for loss computation (rendering can produce values slightly > 1)
            colors = torch.clamp(colors, 0.0, 1.0)

            colors_p = colors.permute(0, 3, 1, 2)  # [B, 3, H, W]
            pixels_p = pixels.permute(0, 3, 1, 2)  # [B, 3, H, W]

            # 1. L1 Loss (reconstruction)
            l1_loss = F.l1_loss(colors, pixels)

            # 2. SSIM Loss (structural similarity)
            ssim_val = fused_ssim(colors_p, pixels_p, padding="valid")
            ssim_loss = 1.0 - ssim_val

            # 3. LPIPS Loss (perceptual similarity) - paper: λperc=0.01
            lpips_loss = self.lpips(colors_p, pixels_p) if cfg.lambda_perc > 0 else torch.tensor(0.0, device=device)
            if cfg.lambda_perc > 0:
                self.lpips.reset()

            # 4. 4D Regularization (paper: λreg=1e-2) - after initial settling
            # Lreg(t) = (1/N) * Σ(σ * sg[σ(t)]) - stop-gradient on temporal opacity
            reg_4d_loss = torch.tensor(0.0, device=device)
            duration_reg_loss = torch.tensor(0.0, device=device)
            if step >= cfg.reg_4d_start_step and cfg.lambda_4d_reg > 0:
                temporal_opacity = info["temporal_opacity"]
                reg_4d_loss = self.compute_4d_regularization(temporal_opacity)

                # Duration regularization: penalize wide temporal windows (cause blur)
                # This helps dynamic regions have sharper temporal profiles
                if cfg.lambda_duration_reg > 0:
                    durations_exp = torch.exp(self.splats["durations"]).squeeze(-1)
                    # Penalize durations larger than target
                    target_duration = self.effective_init_duration
                    excess = torch.clamp(durations_exp - target_duration, min=0)
                    duration_reg_loss = (excess ** 2).mean()

            # Combine all losses with paper weights
            # Paper: λimg=0.8, λssim=0.2, λperc=0.01, λreg=1e-2
            loss_img = cfg.lambda_img * l1_loss
            loss_ssim = cfg.lambda_ssim * ssim_loss
            loss_lpips = cfg.lambda_perc * lpips_loss
            loss_4d_reg = cfg.lambda_4d_reg * reg_4d_loss
            loss_dur_reg = cfg.lambda_duration_reg * duration_reg_loss

            # Total loss
            loss = loss_img + loss_ssim + loss_lpips + loss_4d_reg + loss_dur_reg

            # Backward
            loss.backward()

            # Accumulate gradients for relocation sampling score (always, for when relocation starts)
            if "means" in self.splats and self.splats["means"].grad is not None:
                grad_mag = self.splats["means"].grad.norm(dim=-1)
                if len(self.grad_accum) == len(grad_mag):
                    self.grad_accum += grad_mag
                    self.grad_count += 1

            # Optimizer step
            for opt in self.optimizers.values():
                opt.step()
                opt.zero_grad(set_to_none=True)

            self._project_temporal_params()

            for sched in schedulers:
                sched.step()

            # =====================================================================
            # IMPORTANT: Densification disabled until ROMA init settles!
            # Steps 0-1000: Let initialized points find correct trajectories
            # Steps 1000+: Enable relocation/MCMC/pruning
            #
            # Order of operations (from paper):
            # 1. Relocation FIRST - "save" low-opacity Gaussians by moving them
            # 2. Strategy step SECOND - operates on already-relocated Gaussians
            # 3. Pruning LAST - remove any remaining truly dead Gaussians
            # =====================================================================

            # Only run densification after settling phase
            if in_refinement:  # step >= densification_start_step
                # Periodic relocation - BEFORE strategy step!
                # This prevents MCMC from pruning Gaussians we want to relocate
                if cfg.use_relocation:
                    if step < cfg.relocation_stop_iter:
                        if step % cfg.relocation_every == 0:
                            n_relocated = self.relocate_gaussians(step)
                            if n_relocated > 0:
                                print(f"[Relocation] Step {step}: relocated {n_relocated}")

                # Strategy post-backward (DefaultStrategy/MCMCStrategy operations)
                if isinstance(self.cfg.strategy, DefaultStrategy):
                    self.cfg.strategy.step_post_backward(
                        params=self.splats,
                        optimizers=self.optimizers,
                        state=self.strategy_state,
                        step=step,
                        info=info,
                        packed=cfg.packed,
                    )
                elif isinstance(self.cfg.strategy, MCMCStrategy):
                    self.cfg.strategy.step_post_backward(
                        params=self.splats,
                        optimizers=self.optimizers,
                        state=self.strategy_state,
                        step=step,
                        info=info,
                        lr=schedulers[0].get_last_lr()[0],
                    )

                # Budget pruning (Pure Relocation mode) - after strategy step
                if cfg.use_budget_pruning:
                    if step % cfg.budget_prune_every == 0:
                        self.budget_prune_gaussians(step)

            # Resize gradient accumulator if strategy changed Gaussian count
            if len(self.grad_accum) != len(self.splats["means"]):
                self.grad_accum = torch.zeros(len(self.splats["means"]), device=self.device)
                self.grad_count = 0

            # Pruning - only during refinement phase, AFTER strategy
            if in_refinement and cfg.use_pruning:
                if step < cfg.prune_stop_iter:
                    if step % cfg.prune_every == 0:
                        n_pruned = self.prune_gaussians(step)
                        if n_pruned > 0:
                            print(f"[Prune] Step {step}: removed {n_pruned}, remaining {len(self.splats['means'])}")

            # Progress bar (new phase names)
            phase = "SETTLE" if in_settling else "REFINE"
            pbar.set_description(
                f"[{phase}] loss={loss.item():.4f} l1={l1_loss.item():.4f} "
                f"t={t:.2f} N={len(self.splats['means'])} vel_lr={vel_lr:.1e}"
            )

            # ============================================================
            # TENSORBOARD LOGGING
            # ============================================================
            if self.world_rank == 0 and step % cfg.tb_every == 0:
                mem = torch.cuda.max_memory_allocated() / 1024**3

                # --- Total Loss ---
                self.writer.add_scalar("loss/total", loss.item(), step)

                # --- Individual Loss Components (raw values) ---
                self.writer.add_scalar("loss/l1_raw", l1_loss.item(), step)
                self.writer.add_scalar("loss/ssim_raw", ssim_loss.item(), step)
                self.writer.add_scalar("loss/lpips_raw", lpips_loss.item(), step)
                self.writer.add_scalar("loss/4d_reg_raw", reg_4d_loss.item(), step)

                # --- Weighted Loss Components (what goes into total) ---
                self.writer.add_scalar("loss_weighted/l1", loss_img.item(), step)
                self.writer.add_scalar("loss_weighted/ssim", loss_ssim.item(), step)
                self.writer.add_scalar("loss_weighted/lpips", loss_lpips.item(), step)
                self.writer.add_scalar("loss_weighted/4d_reg", loss_4d_reg.item(), step)

                # --- Quality Metrics ---
                self.writer.add_scalar("metrics/ssim", ssim_val.item(), step)  # Higher is better
                self.writer.add_scalar("metrics/psnr", -10 * torch.log10(F.mse_loss(colors, pixels)).item(), step)

                # --- Gaussian Statistics ---
                self.writer.add_scalar("gaussians/count", len(self.splats["means"]), step)
                self.writer.add_scalar("gaussians/mem_gb", mem, step)

                with torch.no_grad():
                    base_opacity = torch.sigmoid(self.splats["opacities"])
                    self.writer.add_scalar("gaussians/opacity_mean", base_opacity.mean().item(), step)
                    self.writer.add_scalar("gaussians/opacity_min", base_opacity.min().item(), step)
                    self.writer.add_scalar("gaussians/opacity_max", base_opacity.max().item(), step)

                    scales_exp = torch.exp(self.splats["scales"])
                    self.writer.add_scalar("gaussians/scale_mean", scales_exp.mean().item(), step)

                # --- Temporal Statistics ---
                with torch.no_grad():
                    temporal_opa = info["temporal_opacity"]
                    self.writer.add_scalar("temporal/opacity_mean", temporal_opa.mean().item(), step)
                    self.writer.add_scalar("temporal/opacity_min", temporal_opa.min().item(), step)
                    self.writer.add_scalar("temporal/opacity_max", temporal_opa.max().item(), step)

                    durations_exp = torch.exp(self.splats["durations"])
                    self.writer.add_scalar("temporal/duration_mean", durations_exp.mean().item(), step)
                    self.writer.add_scalar("temporal/duration_min", durations_exp.min().item(), step)
                    self.writer.add_scalar("temporal/duration_max", durations_exp.max().item(), step)

                    times = self.splats["times"]
                    self.writer.add_scalar("temporal/time_mean", times.mean().item(), step)
                    self.writer.add_scalar("temporal/time_std", times.std().item(), step)

                    vel_mag = self.splats["velocities"].norm(dim=-1)
                    self.writer.add_scalar("temporal/velocity_mean", vel_mag.mean().item(), step)
                    self.writer.add_scalar("temporal/velocity_max", vel_mag.max().item(), step)
                    self.writer.add_scalar("temporal/velocity_min", vel_mag.min().item(), step)

                # --- Training Info ---
                self.writer.add_scalar("train/time_t", t, step)
                self.writer.add_scalar("train/sh_degree", sh_degree, step)
                self.writer.add_scalar("train/lr_means", self.optimizers["means"].param_groups[0]["lr"], step)
                self.writer.add_scalar("train/lr_velocities", self.optimizers["velocities"].param_groups[0]["lr"], step)

                # --- Phase Indicator (for visualization) ---
                # 0 = Settling (ROMA init settling), 1 = Refinement (densification active)
                phase_num = 0 if in_settling else 1
                self.writer.add_scalar("train/phase", phase_num, step)

            # --- Image Logging (every tb_image_every steps) ---
            if self.world_rank == 0 and step % cfg.tb_image_every == 0:
                with torch.no_grad():
                    # Ground truth vs Rendered side by side
                    gt_img = pixels[0].clamp(0, 1)  # [H, W, 3]
                    render_img = colors[0].detach().clamp(0, 1)  # [H, W, 3]

                    # Create side-by-side comparison: [GT | Rendered]
                    comparison = torch.cat([gt_img, render_img], dim=1)  # [H, 2*W, 3]
                    comparison = comparison.permute(2, 0, 1)  # [3, H, 2*W] for tensorboard

                    self.writer.add_image("images/gt_vs_render", comparison, step)

                    # Also log difference image (error visualization)
                    diff = (gt_img - render_img).abs()
                    diff_normalized = diff / (diff.max() + 1e-8)  # Normalize for visibility
                    diff_img = diff_normalized.permute(2, 0, 1)
                    self.writer.add_image("images/error_map", diff_img, step)

                    # Log histograms
                    if "temporal_opacity" in info:
                        temp_opa = info["temporal_opacity"]
                        self.writer.add_histogram("histograms/temporal_opacity", temp_opa, step)
                    self.writer.add_histogram("histograms/base_opacity", torch.sigmoid(self.splats["opacities"]), step)
                    self.writer.add_histogram("histograms/velocities", self.splats["velocities"].norm(dim=-1), step)
                    self.writer.add_histogram("histograms/durations", torch.exp(self.splats["durations"]), step)
                    self.writer.add_histogram("histograms/times", self.splats["times"], step)

            # --- Duration & Velocity Visualization (every 500 steps) ---
            if self.world_rank == 0 and step % 500 == 0 and step > 0:
                with torch.no_grad():
                    duration_img, velocity_img = self.render_duration_velocity_images(
                        camtoworlds, Ks, width, height, t,
                    )
                    # duration_img: [3, H, W] - blue=short duration, red=long duration
                    # velocity_img: [3, H, W] - blue=slow, red=fast
                    self.writer.add_image("visualization/duration", duration_img, step)
                    self.writer.add_image("visualization/velocity", velocity_img, step)

                    # Log the min/max values to tensorboard for colorbar reference
                    durations_exp = torch.exp(self.splats["durations"]).squeeze(-1)
                    vel_mag = self.splats["velocities"].norm(dim=-1)
                    self.writer.add_scalar("colorbar/duration_min", durations_exp.min().item(), step)
                    self.writer.add_scalar("colorbar/duration_max", durations_exp.max().item(), step)
                    self.writer.add_scalar("colorbar/velocity_min", vel_mag.min().item(), step)
                    self.writer.add_scalar("colorbar/velocity_max", vel_mag.max().item(), step)
                    print(f"[Vis] Step {step}: duration=[{durations_exp.min():.4f}, {durations_exp.max():.4f}], "
                          f"velocity=[{vel_mag.min():.4f}, {vel_mag.max():.4f}]")

            if self.world_rank == 0 and step % cfg.tb_every == 0:
                self.writer.flush()

            # Save checkpoint, render trajectory, and export PLY at save_steps
            if step in [s - 1 for s in cfg.save_steps] or step == max_steps - 1:
                # Flush TensorBoard before long operations
                if self.world_rank == 0:
                    self.writer.flush()
                mem = torch.cuda.max_memory_allocated() / 1024**3
                stats = {
                    "mem": mem,
                    "ellipse_time": time.time() - global_tic,
                    "num_GS": len(self.splats["means"]),
                }
                print(f"[Save] Step {step}: {stats}")
                with open(f"{self.stats_dir}/train_step{step:04d}.json", "w") as f:
                    json.dump(stats, f)

                data = {
                    "step": step,
                    "use_velocity": cfg.use_velocity,
                    "n_frames": cfg.end_frame - cfg.start_frame,
                    "splats": self.splats.state_dict(),
                    "optimizers": {k: v.state_dict() for k, v in self.optimizers.items()},
                }
                torch.save(data, f"{self.ckpt_dir}/ckpt_{step}.pt")

                if self.world_rank == 0:
                    try:
                        self.render_traj(step=step)
                    except Exception as error:
                        print(f"[Export] Trajectory video failed: {error}")
                    ply_steps = cfg.export_ply_steps if cfg.export_ply_steps is not None else cfg.save_steps
                    if cfg.export_ply and (step + 1) in ply_steps:
                        try:
                            self.export_ply_sequence(step=step)
                        except Exception as error:
                            print(f"[Export] PLY export failed: {error}")

            # Evaluation
            if step in [s - 1 for s in cfg.eval_steps]:
                self.eval(step)

        print(f"\n[Training] Complete! Total time: {time.time() - global_tic:.1f}s")

    @torch.no_grad()
    def eval(self, step: int, stage: str = "val"):
        """Evaluate on validation set (sampled for speed)."""
        cfg = self.cfg
        device = self.device

        valloader = torch.utils.data.DataLoader(
            self.valset, batch_size=1, shuffle=False, collate_fn=skip_none_collate
        )
        metrics = defaultdict(list)
        ellipse_time = 0
        eval_count = 0

        # Sample every N-th frame for faster evaluation
        sample_every = cfg.eval_sample_every
        total_frames = len(valloader)
        eval_frames = total_frames // sample_every
        print(f"\n[Eval] Step {step}: evaluating {eval_frames} frames (every {sample_every}-th of {total_frames})")

        for i, data in enumerate(valloader):
            # Skip frames for faster evaluation
            if i % sample_every != 0:
                continue

            # Skip if frame is missing
            if data is None:
                continue

            camtoworlds = data["camtoworld"].to(device)
            Ks = data["K"].to(device)
            pixels = data["image"].to(device) / 255.0
            t = data["time"].to(device).mean().item()
            height, width = pixels.shape[1:3]

            torch.cuda.synchronize()
            tic = time.time()
            renders, _, _ = self.rasterize_splats(
                camtoworlds, Ks, width, height, t, cfg.sh_degree,
            )
            torch.cuda.synchronize()
            ellipse_time += time.time() - tic

            colors = torch.clamp(renders[..., :3], 0, 1)

            # Save image
            if self.world_rank == 0:
                canvas = torch.cat([pixels, colors], dim=2).squeeze(0).cpu().numpy()
                canvas = (canvas * 255).astype(np.uint8)
                imageio.imwrite(f"{self.render_dir}/{stage}_step{step}_{i:04d}.png", canvas)

                # Metrics
                pixels_p = pixels.permute(0, 3, 1, 2)
                colors_p = colors.permute(0, 3, 1, 2)
                metrics["psnr"].append(self.psnr(colors_p, pixels_p))
                metrics["ssim"].append(self.ssim(colors_p, pixels_p))
                metrics["lpips"].append(self.lpips(colors_p, pixels_p))
                eval_count += 1

        if self.world_rank == 0 and eval_count > 0:
            ellipse_time /= eval_count
            stats = {k: torch.stack(v).mean().item() for k, v in metrics.items()}
            stats.update({
                "ellipse_time": ellipse_time,
                "num_GS": len(self.splats["means"]),
            })
            print(
                f"PSNR: {stats['psnr']:.3f}, SSIM: {stats['ssim']:.4f}, LPIPS: {stats['lpips']:.3f} "
                f"Time: {ellipse_time:.3f}s/img, N: {stats['num_GS']}"
            )
            with open(f"{self.stats_dir}/{stage}_step{step:04d}.json", "w") as f:
                json.dump(stats, f)
            for k, v in stats.items():
                self.writer.add_scalar(f"{stage}/{k}", v, step)
            self.writer.flush()

    @torch.no_grad()
    def render_traj(self, step: int):
        """
        Render a 4D trajectory video with smooth camera motion and time progression.

        Creates a video that:
        1. Uses half the camera poses to generate a smooth trajectory
        2. Samples time smoothly from 0 to 1 over the video
        3. Exports as MP4 to result_dir/videos/

        New options:
        - render_traj_time_frames: Total time samples (video frames). Set higher to cover all times.
        - render_traj_camera_loops: How many times camera loops through trajectory.
        """
        print("\n[Render Trajectory] Starting 4D trajectory rendering...")
        cfg = self.cfg
        device = self.device

        # Get camera poses from parser
        camtoworlds_all = self.parser.camtoworlds  # [N, 4, 4] numpy array

        # Use half the cameras for trajectory generation
        num_cams = len(camtoworlds_all)
        camtoworlds_subset = camtoworlds_all[:num_cams // 2]  # [N/2, 4, 4]

        # Extract [N, 3, 4] for trajectory generation functions
        camtoworlds_34 = camtoworlds_subset[:, :3, :]  # [N/2, 3, 4]

        n_camera_frames = cfg.render_traj_n_frames
        camera_loops = cfg.render_traj_camera_loops

        # Auto-compute time frames from frame range if not specified
        if cfg.render_traj_time_frames is None:
            # Auto: use number of frames in the dataset
            n_time_frames = cfg.end_frame - cfg.start_frame + 1
            print(f"  Auto time frames: {n_time_frames} (from start_frame={cfg.start_frame} to end_frame={cfg.end_frame})")
        elif cfg.render_traj_time_frames == 0:
            # Old behavior: same as camera frames
            n_time_frames = n_camera_frames
        else:
            # Use specified value
            n_time_frames = cfg.render_traj_time_frames

        # Smart frame selection: always produce exactly 50 video frames
        # If >100 frames: take middle 100-frame window
        # Sample 50 evenly spaced time points from the window
        max_time_window = 100
        n_video_frames = 50  # Fixed output: 50 frames

        if n_time_frames <= max_time_window:
            # Use full time range [0, 1]
            time_start = 0.0
            time_end = 1.0
        else:
            # Take middle 100 frames worth of time
            center_ratio = 0.5
            half_window_ratio = (max_time_window / 2) / n_time_frames
            time_start = max(0.0, center_ratio - half_window_ratio)
            time_end = min(1.0, center_ratio + half_window_ratio)

        print(f"  Smart sampling: {n_video_frames} video frames (time range [{time_start:.3f}, {time_end:.3f}])")

        # Generate camera trajectory based on selected type
        from datasets.traj import (
            generate_smooth_arc_path,
            generate_dolly_zoom_path,
            generate_fixed_camera_path,
            generate_ellipse_path_z,
            generate_ellipse_path_y,
        )

        traj_type = cfg.render_traj_path.lower()
        if traj_type == "arc":
            traj_poses = generate_smooth_arc_path(
                camtoworlds_34,
                n_frames=n_camera_frames,
                arc_degrees=cfg.render_traj_arc_degrees,
            )
            print(f"  Trajectory: smooth arc (±{cfg.render_traj_arc_degrees/2:.0f}°) with {len(traj_poses)} poses")
        elif traj_type == "dolly":
            traj_poses = generate_dolly_zoom_path(
                camtoworlds_34,
                n_frames=n_camera_frames,
                dolly_amount=cfg.render_traj_dolly_amount,
            )
            print(f"  Trajectory: dolly zoom ({cfg.render_traj_dolly_amount:.0%} push) with {len(traj_poses)} poses")
        elif traj_type == "fixed":
            traj_poses = generate_fixed_camera_path(
                camtoworlds_34,
                n_frames=n_camera_frames,
                camera_index=0,
            )
            print(f"  Trajectory: fixed camera (static viewpoint) with {len(traj_poses)} poses")
        elif traj_type == "ellipse_z":
            traj_poses = generate_ellipse_path_z(camtoworlds_34, n_frames=n_camera_frames)
            print(f"  Trajectory: ellipse (XY plane) with {len(traj_poses)} poses")
        elif traj_type == "ellipse_y":
            traj_poses = generate_ellipse_path_y(camtoworlds_34, n_frames=n_camera_frames)
            print(f"  Trajectory: ellipse (XZ plane) with {len(traj_poses)} poses")
        else:  # "interp" or default
            n_interp = max(1, n_camera_frames // max(len(camtoworlds_34) - 1, 1))
            traj_poses = generate_interpolated_path(camtoworlds_34, n_interp)  # [M, 3, 4]
            # Subsample to exactly n_camera_frames
            if len(traj_poses) > n_camera_frames:
                indices = np.linspace(0, len(traj_poses) - 1, n_camera_frames, dtype=int)
                traj_poses = traj_poses[indices]
            print(f"  Trajectory: smooth interpolation through {len(traj_poses)} camera poses")

        # Convert to [N, 4, 4] by adding homogeneous row
        traj_poses_44 = np.concatenate([
            traj_poses,
            np.repeat(np.array([[[0.0, 0.0, 0.0, 1.0]]]), len(traj_poses), axis=0),
        ], axis=1)  # [N, 4, 4]

        # Expand camera trajectory to match video frames (loop if needed)
        # Camera loops through trajectory while time progresses linearly
        camera_indices = np.linspace(0, len(traj_poses_44) * camera_loops, n_video_frames, endpoint=False)
        camera_indices = (camera_indices % len(traj_poses_44)).astype(int)
        traj_poses_expanded = traj_poses_44[camera_indices]  # [n_video_frames, 4, 4]

        traj_poses_expanded = torch.from_numpy(traj_poses_expanded).float().to(device)

        # Get intrinsics from first camera
        K = torch.from_numpy(list(self.parser.Ks_dict.values())[0]).float().to(device)
        width, height_img = list(self.parser.imsize_dict.values())[0]

        # Create video directory
        video_dir = f"{cfg.result_dir}/videos"
        os.makedirs(video_dir, exist_ok=True)

        # Generate smooth time samples (using smart window from center)
        time_samples = np.linspace(time_start, time_end, n_video_frames)

        print(f"  Video frames: {n_video_frames}")
        print(f"  Camera poses: {len(traj_poses_44)}, loops: {camera_loops}")
        print(f"  Time range: [{time_samples[0]:.3f}, {time_samples[-1]:.3f}]")
        print(f"  Resolution: {width}x{height_img}")

        # Render frames
        video_path = f"{video_dir}/traj_4d_step{step}.mp4"
        writer = MP4Writer(video_path, cfg.render_traj_fps, width, height_img)

        try:
            for i in tqdm.trange(len(traj_poses_expanded), desc="Rendering trajectory"):
                camtoworlds = traj_poses_expanded[i:i+1]  # [1, 4, 4]
                Ks = K[None]  # [1, 3, 3]
                t = time_samples[i]

                # Render at this camera pose and time
                renders, alphas, info = self.rasterize_splats(
                    camtoworlds=camtoworlds,
                    Ks=Ks,
                    width=width,
                    height=height_img,
                    t=t,
                    sh_degree=cfg.sh_degree,
                )

                colors = torch.clamp(renders[..., :3], 0.0, 1.0)  # [1, H, W, 3]

                # Convert to uint8 and write
                frame = colors.squeeze(0).cpu().numpy()  # [H, W, 3]
                frame = (frame * 255).astype(np.uint8)
                writer.append_data(frame)

        finally:
            writer.close()
        print(f"  Video saved to: {video_path}")

    @torch.no_grad()
    def export_ply_sequence(self, step: int):
        """
        Export PLY files for each frame from start_frame to end_frame.

        Two modes:
        1. Compact mode (export_ply_compact=True): Saves ~15x less disk space
           - One canonical_4d.ply with all static params + motion parameters
           - Per-frame .npz files with only positions and opacities (16 bytes/Gaussian)

        2. Full mode (export_ply_compact=False): Original behavior
           - Full PLY per frame with all parameters (~236 bytes/Gaussian)
        """
        cfg = self.cfg

        if cfg.export_ply_compact:
            self._export_ply_compact(step)
        else:
            self._export_ply_full(step)

    @torch.no_grad()
    def _export_ply_compact(self, step: int):
        """
        Compact/factored PLY export - saves ~15x disk space.

        Exports:
        1. canonical_4d.npz - All Gaussian parameters in canonical space:
           - means, scales, quats, sh0, shN (static attributes)
           - velocities, times, durations (motion parameters)
           - base_opacities

        2. frames/frame_XXXXXX.npz - Per-frame compact data:
           - positions: [N_visible, 3] float16
           - opacities: [N_visible] float16
           - indices: [N_visible] int32 (maps to canonical Gaussians)

        This allows reconstruction of full PLY at any time, while saving
        ~15x disk space compared to full per-frame PLY export.
        """
        print("\n[PLY Export] Exporting compact 4D Gaussian sequence...")
        cfg = self.cfg
        device = self.device

        # Create export directory
        export_dir = f"{cfg.result_dir}/ply_4d_step{step}"
        frames_dir = os.path.join(export_dir, "frames")
        os.makedirs(frames_dir, exist_ok=True)

        # Get frame range
        start_frame = cfg.start_frame
        end_frame = cfg.end_frame
        n_frames = end_frame - start_frame

        # Smart frame selection: limit to 100 frames max, sample every 2nd frame
        max_frames_window = 100  # Maximum window size
        sample_step = 2  # Sample every 2nd frame within window

        if n_frames <= max_frames_window:
            # Use all frames but sample every 2nd
            frame_start_offset = 0
            frame_end_offset = n_frames
        else:
            # Take 100 frames from the middle
            middle = n_frames // 2
            frame_start_offset = middle - max_frames_window // 2
            frame_end_offset = frame_start_offset + max_frames_window

        # Generate frame indices: sample every 2nd frame within the window
        export_frames = list(range(frame_start_offset, frame_end_offset, sample_step))
        n_export_frames = len(export_frames)

        print(f"  Frame range: {n_frames} total, exporting {n_export_frames} frames")
        print(f"  Window: frames {frame_start_offset}-{frame_end_offset}, sampling every {sample_step}")
        print(f"  Output directory: {export_dir}")

        # ===== Step 1: Export canonical 4D parameters =====
        print("  Saving canonical 4D parameters...")
        canonical_path = os.path.join(export_dir, "canonical_4d.npz")

        # Get all parameters
        means = self.splats["means"].cpu().numpy()  # [N, 3]
        scales = self.splats["scales"].cpu().numpy()  # [N, 3] (log scale)
        quats = self.splats["quats"].cpu().numpy()  # [N, 4]
        opacities = self.splats["opacities"].cpu().numpy()  # [N] (logit)
        sh0 = self.splats["sh0"].cpu().numpy()  # [N, 1, 3]
        shN = self.splats["shN"].cpu().numpy()  # [N, K, 3]
        velocities = self.splats["velocities"].cpu().numpy()  # [N, 3]
        times = self.splats["times"].cpu().numpy()  # [N, 1]
        durations = self.splats["durations"].cpu().numpy()  # [N, 1]

        # Save canonical file (all static + motion params)
        np.savez_compressed(
            canonical_path,
            means=means.astype(np.float32),
            scales=scales.astype(np.float32),
            quats=quats.astype(np.float32),
            opacities=opacities.astype(np.float32),
            sh0=sh0.astype(np.float32),
            shN=shN.astype(np.float32),
            velocities=velocities.astype(np.float32),
            times=times.astype(np.float32),
            durations=durations.astype(np.float32),
            n_frames=n_frames,
            start_frame=start_frame,
            end_frame=end_frame,
            use_velocity=cfg.use_velocity,
            opacity_threshold=cfg.export_ply_opacity_threshold,
        )

        canonical_size = os.path.getsize(canonical_path) / (1024 * 1024)
        print(f"  Canonical file: {canonical_path} ({canonical_size:.1f} MB)")
        print(f"  Total Gaussians: {len(means):,}")

        # ===== Step 2: Export per-frame compact data =====
        print("  Saving per-frame positions and opacities...")

        base_opacity_gpu = torch.sigmoid(self.splats["opacities"])

        total_frame_size = 0
        frames_exported = 0

        for frame in tqdm.tqdm(export_frames, desc="Exporting frames"):
            # Compute normalized time t in [0, 1]
            t = frame / max(n_frames - 1, 1)

            means_t = self.compute_positions_at_time(t)
            temporal_opacity = self.compute_temporal_opacity(t)

            # Combined opacity
            opacities_t = base_opacity_gpu * temporal_opacity  # [N]

            # Filter by opacity threshold
            valid_mask = opacities_t > cfg.export_ply_opacity_threshold
            indices = torch.nonzero(valid_mask, as_tuple=True)[0]

            # Get visible data
            positions_visible = means_t[valid_mask].cpu().numpy().astype(np.float16)
            opacities_visible = opacities_t[valid_mask].cpu().numpy().astype(np.float16)
            indices_visible = indices.cpu().numpy().astype(np.int32)

            # Save compact frame file
            frame_path = os.path.join(frames_dir, f"frame_{frame:06d}.npz")
            np.savez_compressed(
                frame_path,
                positions=positions_visible,
                opacities=opacities_visible,
                indices=indices_visible,
                t=np.float32(t),
            )

            total_frame_size += os.path.getsize(frame_path)
            frames_exported += 1

            if frame == export_frames[0] or frame == export_frames[-1]:
                n_visible = len(indices_visible)
                print(f"  Frame {frame}: {n_visible:,} / {len(means):,} visible (t={t:.3f})")

        # ===== Step 3: Also export first and last frame as full PLY for compatibility =====
        print("  Saving reference PLY files (first and last frame)...")
        scales_log = self.splats["scales"]
        sh0_tensor = self.splats["sh0"]
        shN_tensor = self.splats["shN"]

        for frame in [0, n_frames - 1]:
            t = frame / max(n_frames - 1, 1)
            means_t = self.compute_positions_at_time(t)
            temporal_opacity = self.compute_temporal_opacity(t)
            opacities_t = base_opacity_gpu * temporal_opacity

            valid_mask = opacities_t > cfg.export_ply_opacity_threshold
            if not valid_mask.any():
                print(f"  Skipping reference PLY for empty frame {frame}")
                continue

            filepath = os.path.join(export_dir, f"reference_frame_{frame:06d}.ply")
            export_splats(
                means=means_t[valid_mask],
                scales=scales_log[valid_mask],
                quats=self.splats["quats"][valid_mask],
                opacities=torch.logit(opacities_t[valid_mask], eps=1e-6),
                sh0=sh0_tensor[valid_mask],
                shN=shN_tensor[valid_mask],
                format=cfg.export_ply_format,
                save_to=filepath,
            )

        # Summary
        total_frame_size_mb = total_frame_size / (1024 * 1024)
        total_size_mb = canonical_size + total_frame_size_mb

        print(f"\n  === Compact Export Summary ===")
        print(f"  Canonical file: {canonical_size:.1f} MB")
        print(f"  Frame files ({frames_exported}): {total_frame_size_mb:.1f} MB")
        print(f"  Total: {total_size_mb:.1f} MB")
        print(f"  (Full PLY export would be ~{total_size_mb * 15:.0f} MB)")
        print(f"  Output: {export_dir}")

    @torch.no_grad()
    def _export_ply_full(self, step: int):
        """
        Original full PLY export - one complete PLY per frame.
        Uses more disk space but compatible with all viewers.
        """
        print("\n[PLY Export] Exporting full Gaussian sequence...")
        cfg = self.cfg
        device = self.device

        # Create export directory
        ply_dir = f"{cfg.result_dir}/ply_sequence_step{step}"
        os.makedirs(ply_dir, exist_ok=True)

        # Get frame range
        start_frame = cfg.start_frame
        end_frame = cfg.end_frame
        n_frames = end_frame - start_frame
        frame_step = cfg.export_ply_frame_step

        n_export_frames = len(range(0, n_frames, frame_step))
        print(f"  Exporting {n_export_frames} frames (every {frame_step}) from {start_frame} to {end_frame}")
        print(f"  Output directory: {ply_dir}")

        # Get base Gaussian parameters (constant across time)
        # gsplat's exporter accepts raw log-scales and opacity logits.
        scales = self.splats["scales"]  # [N, 3]
        quats = self.splats["quats"]  # [N, 4]
        sh0 = self.splats["sh0"]  # [N, 1, 3]
        shN = self.splats["shN"]  # [N, K, 3]

        exported = 0
        for frame in tqdm.trange(0, n_frames, frame_step, desc="Exporting PLY"):
            # Compute normalized time t in [0, 1]
            t = frame / max(n_frames - 1, 1)

            # Compute positions at time t: µx(t) = µx + v·(t-µt)
            means_t = self.compute_positions_at_time(t)  # [N, 3]

            # Compute temporal opacity: σ(t) = exp(-0.5*((t-µt)/s)^2)
            temporal_opacity = self.compute_temporal_opacity(t)  # [N]

            # Combined opacity: base_opacity * temporal_opacity
            base_opacity = torch.sigmoid(self.splats["opacities"])  # [N]
            opacities_t = base_opacity * temporal_opacity  # [N]

            # Filter out low opacity Gaussians (makes files MUCH smaller)
            # Only keep Gaussians with combined opacity > threshold
            opacity_threshold = cfg.export_ply_opacity_threshold
            valid_mask = opacities_t > opacity_threshold
            n_visible = valid_mask.sum().item()
            if n_visible == 0:
                print(f"  Skipping PLY for empty frame {frame}")
                continue

            # Apply mask to all parameters
            means_visible = means_t[valid_mask]
            scales_visible = scales[valid_mask]
            quats_visible = quats[valid_mask]
            opacities_visible = torch.logit(opacities_t[valid_mask], eps=1e-6)
            sh0_visible = sh0[valid_mask]
            shN_visible = shN[valid_mask]

            if frame == 0 or frame >= n_frames - frame_step:
                print(f"  Frame {frame}: {n_visible:,} / {len(opacities_t):,} Gaussians visible (t={t:.3f})")

            # Export to PLY (only visible Gaussians)
            filepath = os.path.join(ply_dir, f"frame_{frame:06d}.ply")
            export_splats(
                means=means_visible,
                scales=scales_visible,
                quats=quats_visible,
                opacities=opacities_visible,
                sh0=sh0_visible,
                shN=shN_visible,
                format=cfg.export_ply_format,
                save_to=filepath,
            )
            exported += 1

        print(f"  Exported {exported} PLY files to {ply_dir}")


def main(local_rank: int, world_rank: int, world_size: int, cfg: Config):
    if world_size > 1 and not cfg.disable_viewer:
        cfg.disable_viewer = True
        if world_rank == 0:
            print("Viewer is disabled in distributed training.")

    runner = FreeTime4DRunner(local_rank, world_rank, world_size, cfg)

    if cfg.export_only:
        # Export mode: just generate PLY and videos from checkpoint
        runner.export_from_checkpoint()
    else:
        # Training mode (can resume from checkpoint)
        runner.train()


if __name__ == "__main__":
    """
    Usage:

    # Training with DefaultStrategy
    CUDA_VISIBLE_DEVICES=0 python simple_trainer_freetime_4d.py default \
        --data-dir /path/to/data \
        --init-npz-path /path/to/init_points.npz \
        --result-dir /path/to/results \
        --start-frame 0 --end-frame 300

    # Training with MCMCStrategy
    CUDA_VISIBLE_DEVICES=0 python simple_trainer_freetime_4d.py mcmc \
        --data-dir /path/to/data \
        --init-npz-path /path/to/init_points.npz \
        --result-dir /path/to/results

    # Resume training from checkpoint
    CUDA_VISIBLE_DEVICES=0 python simple_trainer_freetime_4d.py mcmc \
        --data-dir /path/to/data \
        --init-npz-path /path/to/init_points.npz \
        --result-dir /path/to/results \
        --ckpt-path /path/to/results/ckpts/ckpt_29999.pt

    # Export PLY sequence and videos from checkpoint (no training)
    CUDA_VISIBLE_DEVICES=0 python simple_trainer_freetime_4d.py mcmc \
        --data-dir /path/to/data \
        --init-npz-path /path/to/init_points.npz \
        --result-dir /path/to/results \
        --ckpt-path /path/to/results/ckpts/ckpt_59999.pt \
        --export-only
    """

    configs = {
        "default_keyframe": (
            "Dense Keyframes + Velocity Bridging. "
            "Samples densely from keyframes only and uses velocity to bridge gaps.",
            Config(
                # ============ Dense Keyframe Initialization ============
                no_sampling=True,             # Use ALL points from NPZ (no downsampling)
                max_samples=0,                # Ignored when no_sampling=True
                use_keyframe_sampling=True,   # KEY: Sample from keyframes only
                keyframe_step=-1,             # AUTO: Read from NPZ metadata
                init_opacity=0.5,
                init_scale=0.03,              # Small scales for memory efficiency

                # ============ Velocity Bridging ============
                use_velocity=True,            # Essential for moving points across gaps
                velocity_lr_start=5e-3,       # Allow velocity refinement
                velocity_lr_end=1e-4,

                # Duration: AUTO-computed from NPZ keyframe_step
                # Formula: init_duration = (keyframe_step / total_frames) * init_duration_multiplier
                # E.g., with keyframe_step=5, total_frames=60: gap=0.083, duration=0.167 (2x gap)
                auto_init_duration=True,      # Enable auto-computation from NPZ
                init_duration=-1.0,           # -1 triggers auto-computation
                init_duration_multiplier=2.0, # 2x keyframe gap for good overlap

                # ============ DISABLE Standard Densification ============
                strategy=DefaultStrategy(
                    verbose=True,
                    refine_start_iter=100_000,  # > max_steps = DISABLED
                    refine_stop_iter=100_000,
                    reset_every=100_000,        # Disable opacity reset
                ),

                # ============ ENABLE Pure Relocation ============
                use_relocation=True,
                relocation_every=100,
                densification_start_step=100,     # Start early (was 1000)
                relocation_stop_iter=-1,      # -1 = auto-compute as 0.9 * max_steps
                relocation_max_ratio=0.10,        # 10% - aggressive relocation

                # ============ Memory Safety ============
                packed=True,                  # CRITICAL for 8M+ Gaussians

                # ============ Regularization ============
                lambda_4d_reg=1e-4,           # Gentle 4D regularization
                lambda_duration_reg=1e-3,     # Light duration regularization

                # ============ Random Background ============
                random_bkgd=False,            # Disabled for 4D: interferes with temporal opacity
            ),
        ),
        "default_keyframe_small": (
            "Same as default_keyframe but with 4M budget for lower VRAM GPUs.",
            Config(
                # ============ Dense Keyframe Initialization ============
                max_samples=5_000_000,        # Lower budget for smaller GPUs
                use_keyframe_sampling=True,
                keyframe_step=-1,             # AUTO: Read from NPZ metadata
                init_opacity=0.5,
                init_scale=0.03,

                # ============ Velocity Bridging ============
                use_velocity=True,
                velocity_lr_start=5e-3,
                velocity_lr_end=1e-4,
                auto_init_duration=True,      # Enable auto-computation from NPZ
                init_duration=-1.0,           # -1 triggers auto-computation
                init_duration_multiplier=2.0, # 2x keyframe gap

                # ============ DISABLE Standard Densification ============
                strategy=DefaultStrategy(
                    verbose=True,
                    refine_start_iter=100_000,
                    refine_stop_iter=100_000,
                    reset_every=100_000,
                ),

                # ============ ENABLE Pure Relocation ============
                use_relocation=True,
                relocation_every=100,
                densification_start_step=100,     # Start early (was 1000)
                relocation_stop_iter=60_000,
                relocation_max_ratio=0.10,        # 10% - aggressive relocation

                # ============ Memory Safety ============
                packed=True,

                # ============ Regularization ============
                lambda_4d_reg=1e-4,
                lambda_duration_reg=1e-3,

                # ============ Random Background ============
                random_bkgd=False,            # Disabled for 4D
            ),
        ),
    }

    cfg = tyro.extras.overridable_config_cli(configs)
    cfg.adjust_steps(cfg.steps_scaler)

    cli(main, cfg, verbose=True)
