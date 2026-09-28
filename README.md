
# FreeTimeGSVanilla

### Gsplat-based 4D Gaussian Splatting for Dynamic Scenes

<img src="assets/demo.gif" width="100%" alt="FreeTimeGS Demo">

[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)
[![Python 3.12+](https://img.shields.io/badge/Python-3.12+-blue.svg)](https://www.python.org/downloads/)

A vanilla minimal implementation of **FreeTimeGS** built on [gsplat](https://github.com/nerfstudio-project/gsplat) for reconstructing dynamic scenes from multi-view video.

## Model scope

This repository uses normalized time, exponential durations, the original motion
and opacity equations, and the two original training presets.

Correctness and compatibility fixes cover frame-gap velocity estimates,
consistent NPZ time and coordinate conversion, per-point durations, small sampling
budgets, Adam resume settings, duration gradients, LPIPS input normalization, relocation
statistics, and matching training/viewer/export math. MP4 output handles odd
image dimensions and export failures do not interrupt a saved training run.

## Installation

Use Python 3.12+, a CUDA-capable PyTorch environment, and a CUDA toolkit for the
gsplat, fused-ssim, and torch-scatter extensions. The lockfile pins dependencies:

```bash
uv sync --locked
source .venv/bin/activate
```

**Key Features**

- **4D Gaussian Primitives** - Each Gaussian has position, velocity, time, and duration
- **Temporal Motion Model** - `x(t) = x + v * (t - t_canonical)`
- **gsplat Backend** - Efficient CUDA kernels for fast rendering
- **Flexible Optimization** - MCMC and DefaultStrategy densification
- **Keyframe Processing** - Smart sampling for large video sequences


**Based on the paper:** 
*FreeTimeGS: Free Gaussian Primitives at Anytime Anywhere for Dynamic Scene Reconstruction* Yifan Wang, Peishan Yang, Zhen Xu, Jiaming Sun, Zhanhua Zhang, Yong Chen, Hujun Bao, Sida Peng, Xiaowei Zhou **CVPR 2025** [[Paper]](https://openaccess.thecvf.com/content/CVPR2025/papers/Wang_FreeTimeGS_Free_Gaussian_Primitives_at_Anytime_Anywhere_for_Dynamic_Scene_CVPR_2025_paper.pdf) [[Project Page]](https://zju3dv.github.io/freetimegs/)


---


## Repository Structure

```
FreeTimeGsVanilla/
│
├── src/                          # Core source code
│   ├── simple_trainer_freetime_4d_pure_relocation.py   # Main 4D GS trainer
│   ├── combine_frames_fast_keyframes.py                # Keyframe point cloud combiner
│   ├── viewer_4d.py                                    # Interactive 4D Gaussian viewer
│   └── utils.py                                        # Utility functions (KNN, colormap, etc.)
│
├── datasets/                     # Data loading & processing
│   ├── __init__.py               # Package exports
│   ├── FreeTime_dataset.py       # Dataset class (COLMAP poses, images)
│   ├── normalize.py              # Scene normalization utilities
│   ├── traj.py                   # Camera trajectory generation
│   └── read_write_model.py       # COLMAP binary/text I/O
│
├── run_pipeline.sh               # Full pipeline (combine + train)
├── run_small.sh                  # Example using the 5M-point budget
├── run_full.sh                   # Example using all input points
│
├── LICENSE                       # AGPL-3.0 license
└── README.md                     # This file
```

## Pipeline Overview

The training pipeline consists of two main steps:

1. **Point Cloud Preparation** (`src/combine_frames_fast_keyframes.py`):
   - Loads per-frame triangulated 3D points
   - Extracts keyframes at specified intervals
   - Estimates velocity using k-NN matching between consecutive keyframes
   - Outputs an NPZ file with positions, velocities, colors, and timestamps

2. **4D Gaussian Training** (`src/simple_trainer_freetime_4d_pure_relocation.py`):
   - Initializes 4D Gaussians from the NPZ file
   - Trains with temporal parameters (position, velocity, time, duration)
   - Outputs PLY sequences and trajectory videos

## Keyframes vs All Frames (Stride/Step)

### Why Keyframes?

Processing every single frame of a video is computationally expensive and often redundant. Adjacent frames are typically very similar. Instead, we use **keyframes** - frames sampled at regular intervals.

### Keyframe Step (Stride)

The `--keyframe-step` parameter controls how many frames to skip between keyframes:

- **Step = 1**: Use ALL frames (no skipping) - most accurate but slowest
- **Step = 5**: Use every 5th frame (0, 5, 10, 15, ...) - good balance
- **Step = 10**: Use every 10th frame - faster but less temporal detail

**Example**: For a 60-frame video with `--keyframe-step 5`:
```
Frames:    0  1  2  3  4  5  6  7  8  9  10 11 12 ... 55 56 57 58 59
Keyframes: *              *              *              *
           0              5              10             55
```

The final frame is also included when its point cloud exists, so the sequence
retains endpoint coverage even when the stride does not divide the frame range.

### Velocity Estimation

Velocity uses the next frame when available, otherwise the next keyframe or
final endpoint. Displacement is divided by the actual frame gap:
```
v = (matched_position[next_frame] - position[t]) / (next_frame - t)
```

This gives the average velocity over the keyframe interval.

## NPZ File Format

The NPZ file contains the initial 4D Gaussian data:

| Field | Shape | Description |
|-------|-------|-------------|
| `positions` | [N, 3] | 3D coordinates (x, y, z) |
| `velocities` | [N, 3] | Velocity vectors in world units per frame |
| `colors` | [N, 3] | RGB colors normalized to [0, 1] |
| `times` | [N, 1] | Normalized timestamps in [0, 1] |
| `durations` | [N, 1] | Temporal duration (visibility window) |
| `has_velocity` | [N] | Boolean mask for valid velocity estimates |

**Metadata fields:**
- `frame_start`, `frame_end`: Inclusive start and exclusive end in new NPZ files
- `time_denominator`: `frame_end - frame_start - 1` (at least 1); converts normalized time to frame offsets
- `n_keyframes`: Number of keyframes used
- `keyframe_step`: Step between keyframes
- `mode`: Processing mode identifier

Legacy files from this repo's combiner are still accepted with their original
inclusive end and frame-count denominator. When loading a subrange, timestamps
and durations are renormalized together and velocities retain their physical
displacement. Valid per-point durations are preserved in automatic mode.

### Example NPZ Creation

```python
import numpy as np

# Your triangulated point clouds (one per frame)
points_frame_0 = np.load("points3d_frame000000.npy")  # [M, 3]
colors_frame_0 = np.load("colors_frame000000.npy")    # [M, 3], values 0-255

# Combine and save
np.savez(
    "init_points.npz",
    positions=positions,      # [N, 3] float32
    velocities=velocities,    # [N, 3] float32
    colors=colors / 255.0,    # [N, 3] float32, normalized to [0, 1]
    times=times,              # [N, 1] float32, normalized to [0, 1]
    durations=durations,      # [N, 1] float32
    has_velocity=has_velocity, # [N] bool
    frame_start=0,
    frame_end=61,              # exclusive
    time_denominator=60,
)
```

## Input Requirements

### Per-Frame Point Cloud Files

The `src/combine_frames_fast_keyframes.py` script expects:

```
input_dir/
├── points3d_frame000000.npy   # [M, 3] float32 - 3D positions
├── colors_frame000000.npy     # [M, 3] float32 - RGB colors (0-255)
├── points3d_frame000001.npy
├── colors_frame000001.npy
├── ...
└── points3d_frameXXXXXX.npy
```

These are typically generated by triangulating matched features across camera views.

### COLMAP Data

The trainer expects a COLMAP sparse reconstruction:

```
data_dir/
├── images/                    # Or images_Nx/ for downsampled
│   ├── cam01_frame000000.jpg
│   └── ...
└── sparse/
    └── 0/
        ├── cameras.bin
        ├── images.bin
        └── points3D.bin
```

## Usage

### Full Pipeline

```bash
bash run_pipeline.sh \
    /path/to/triangulation/output \
    /path/to/colmap/data \
    /path/to/results \
    0 61 5 0 default_keyframe_small
```

The arguments after the paths are start frame, exclusive end frame, keyframe
step, GPU ID, and preset. Training uses one image and timestamp per step
(`--batch-size 1`).

### Step by Step

**Step 1: Combine keyframes**

The combiner's `--frame-end` is inclusive; the trainer's `--end-frame` is exclusive.

```bash
python src/combine_frames_fast_keyframes.py \
    --input-dir /path/to/triangulation/output \
    --output-path /path/to/keyframes.npz \
    --frame-start 0 \
    --frame-end 60 \
    --keyframe-step 5
```

**Step 2: Train 4D Gaussians**

```bash
CUDA_VISIBLE_DEVICES=0 python src/simple_trainer_freetime_4d_pure_relocation.py default_keyframe \
    --data-dir /path/to/colmap/data \
    --init-npz-path /path/to/keyframes.npz \
    --result-dir /path/to/results \
    --start-frame 0 \
    --end-frame 61 \
    --max-steps 30000
```

### Available Configs

| Config | Points | Description |
|--------|--------|-------------|
| `default_keyframe` | All input points | No initialization downsampling |
| `default_keyframe_small` | Up to 5M | Reduced initialization budget |

## Outputs

### Single-file FTGS export

Export a trained Vanilla checkpoint to one `.ftgs.ply` on CPU, without the
dataset or a GPU:

```bash
python src/export_ftgs_ply.py \
    --ckpt /path/to/results/ckpts/ckpt_29999.pt \
    --output /path/to/scene.ftgs.ply
```

Omitting `--output` saves next to the checkpoint with the `.ftgs.ply` suffix.
For older checkpoints, `--n-frames 61` supplies frame-count metadata and
`--no-velocity` records a model trained with motion disabled. Otherwise these
settings come from the checkpoint; legacy models default to motion enabled and
omit the frame count when it is unknown.

The trainer accepts `--export-ply --export-ply-format ftgs` to save
`result_dir/ckpt_<step>.ftgs.ply` at the existing PLY export steps. To export an
existing checkpoint through the trainer CLI:

```bash
python src/simple_trainer_freetime_4d_pure_relocation.py default_keyframe \
    --export-only --export-ply-format ftgs \
    --ckpt-path /path/to/results/ckpts/ckpt_29999.pt \
    --result-dir /path/to/export
```

This mode exports only the complete model and reads frame count and motion
settings from the checkpoint. It bypasses dataset loading, GPU initialization,
and video rendering. FTGS export keeps every Gaussian and SH coefficient;
the per-frame opacity threshold, frame stride, and compact-export setting do
not filter this file.

### FTGS PLY layout, version 1

This is this repository's documented format for Vanilla FreeTimeGS. It is a
binary little-endian PLY with one `vertex` per Gaussian; every property is a
32-bit float. Spatial coordinates remain in the trainer's model space.

| Properties | Stored values |
| --- | --- |
| `x`, `y`, `z` | Canonical position at that Gaussian's `time` |
| `nx`, `ny`, `nz` | Zeros, following the usual 3DGS PLY layout |
| `f_dc_0..2` | RGB DC spherical harmonic coefficients |
| `f_rest_0..` | Remaining SH coefficients, all red coefficients then green then blue |
| `opacity` | Base opacity logit; apply sigmoid |
| `scale_0..2` | Spatial log-scales; apply exp |
| `rot_0..3` | Quaternion in wxyz order; normalize when rendering |
| `time` | Canonical time on the normalized sequence timeline |
| `log_duration` | Log temporal standard deviation; apply exp |
| `velocity_0..2` | Displacement per unit of normalized time |

Header comments record `ftgs_version 1`, `time_units normalized`, `sh_degree`,
`use_velocity` (0 or 1), `min_duration 0.02`, `opacity_floor 0.0001`, and
`n_frames` when known. `log_duration` includes Vanilla's minimum-duration
projection. Raw velocity values are retained even when `use_velocity` is 0.

For a time `t`, an importer reconstructs the model using:

```text
dt = t - time
position = [x, y, z] + [velocity_0, velocity_1, velocity_2] * dt
           # use [x, y, z] directly when use_velocity is 0
alpha = max(sigmoid(opacity) * exp(-0.5 * (dt / exp(log_duration))**2), 0.0001)
```

The sequence spans `t=0..1`; Gaussian canonical times may lie outside that
interval. Relative frame `i` maps to `i / max(n_frames - 1, 1)`. This file stores
rendering parameters, not optimizer state. The browser player below reads this
layout directly; the Python/CUDA viewer loads `.pt` checkpoints.

### Existing training outputs

After training, you'll find:

```
results/
├── ckpts/
│   └── ckpt_30000.pt              # Model checkpoint
├── videos/
│   ├── traj_4d_step30000.mp4      # RGB trajectory video
│   ├── traj_duration_step30000.mp4    # Duration heatmap
│   └── traj_velocity_step30000.mp4    # Velocity heatmap
├── ply_sequence_step30000/
│   ├── frame_000000.ply           # Per-frame PLY exports
│   └── ...
└── tb/                            # TensorBoard logs
```

## Browser player for FTGS files

The standalone [FTGS Player](player/README.md) opens `.ftgs.ply` files locally or
from a URL and runs entirely in the browser with WebGL2. It includes playback,
seeking, speed and loop controls, orbit/pan/zoom, and a synthetic demo. It runs as a static site
without a build step or package installation.

```bash
python -m http.server 8765 --bind 127.0.0.1 --directory player
```

Open <http://localhost:8765> and choose **Open file** or drop in an exported
`.ftgs.ply`. Local files stay in the browser. The default preview loads up to
1 million Gaussians; choose **All points** for full detail when GPU memory allows.
See the [player documentation](player/README.md) for format support, controls,
rendering limits, and tests.

## Python/CUDA 4D Viewer

An interactive viewer for visualizing trained 4D Gaussian Splatting models with temporal animation.

### Installation

The viewer uses the same environment installed above.

**Verify installation:**
```bash
python -c "import viser; import nerfview; import gsplat; print('All dependencies installed!')"
```

### Quick Start

```bash
CUDA_VISIBLE_DEVICES=0 python src/viewer_4d.py \
    --ckpt /path/to/results/ckpts/ckpt_30000.pt \
    --port 8080 \
    --total-frames 60 \
    --temporal-threshold 0.05 \
    --spatial-percentile 95
```

Then open `http://localhost:8080` in your browser.

New checkpoints record the frame count and velocity switch, which the viewer
loads automatically. `--total-frames` supplies the count for legacy checkpoints.
Checkpoints must use the documented parameters, normalized time, and exponential
durations. Incompatible checkpoints are rejected.

### Checkpoint File Format (.pt)

The checkpoint file contains all trained 4D Gaussian parameters:

```python
checkpoint = {
    "splats": {
        "means": tensor[N, 3],       # Canonical 3D positions
        "scales": tensor[N, 3],      # Log-scale parameters
        "quats": tensor[N, 4],       # Rotation quaternions (wxyz)
        "opacities": tensor[N],      # Logit opacities
        "sh0": tensor[N, 1, 3],      # DC spherical harmonics
        "shN": tensor[N, K, 3],      # Higher-order SH coefficients
        # 4D temporal parameters:
        "times": tensor[N, 1],       # Canonical time (when Gaussian is most visible)
        "durations": tensor[N, 1],   # Log temporal duration (visibility window width)
        "velocities": tensor[N, 3],  # Linear velocity vectors
    },
    "step": int,                     # Training step
    ...
}
```

### Command Line Arguments

| Argument | Default | Description |
|----------|---------|-------------|
| `--ckpt` | **required** | Path to trained checkpoint `.pt` file |
| `--port` | 8080 | HTTP port for the viewer |
| `--device` | cuda | Device to use (cuda, cuda:0, cuda:1, etc.) |
| `--total-frames` | 300 | Total number of frames in the sequence |
| `--temporal-threshold` | 0.01 | Minimum temporal opacity to render a Gaussian |
| `--spatial-percentile` | 95 | Percentile of points to keep (removes outliers) |
| `--no-spatial-filter` | False | Disable spatial filtering |
| `--no-precompute` | False | Disable precomputing visibility masks |
| `--sh-degree` | 3 | Spherical harmonics degree |

### Understanding Key Parameters

#### `--temporal-threshold`

Controls which Gaussians are rendered at each frame based on their temporal opacity.

Each Gaussian has a temporal opacity computed as:
```
temporal_opacity(t) = exp(-0.5 * ((t - t_canonical) / duration)^2)
```

- **Lower threshold (0.01)**: More Gaussians visible, smoother but slower
- **Higher threshold (0.1)**: Fewer Gaussians, faster but may show gaps

```
Temporal opacity vs time for a Gaussian centered at t=0.5:

    1.0 |       ****
        |      *    *
    0.5 |     *      *
        |    *        *
  0.05 -|---*----------*--- threshold
        |  *            *
    0.0 +-------------------> time
        0.0    0.5    1.0
              ^
          Gaussian visible when opacity > threshold
```

#### `--spatial-percentile`

Removes outlier Gaussians that are far from the scene center.

- **95%**: Keep Gaussians within the 95th percentile distance from center (removes 5% outliers)
- **99%**: Keep more Gaussians (removes only 1% outliers)
- **100%**: Keep all Gaussians (no spatial filtering)

This is useful when training produces "floater" artifacts far from the main scene.

```
Example with 5M Gaussians:
┌─────────────────────────────────────┐
│  · ·                            · · │  <- outliers (removed)
│      ┌───────────────────────┐      │
│      │  * * * * * * * * * *  │      │  <- 95% kept
│      │  * * * SCENE * * * *  │      │
│      │  * * * * * * * * * *  │      │
│      └───────────────────────┘      │
│  ·                              ·   │  <- outliers (removed)
└─────────────────────────────────────┘
```

### Viewer UI Controls

Once the viewer is running, you can control it through the web interface:

**Animation Panel:**
- **Frame Slider**: Manually scrub through time
- **Auto Play**: Toggle automatic playback
- **Play Speed (FPS)**: Control playback speed (1-60 FPS)

**Visibility Filtering Panel:**
- **Temporal Opacity Threshold**: Adjust visibility threshold in real-time
- **Use Visibility Mask**: Toggle efficient rendering on/off

**Camera Controls (in browser):**
- Left-click + drag: Rotate camera
- Right-click + drag: Pan camera
- Scroll: Zoom in/out

### Efficiency: Visibility Masking

The viewer uses multi-level filtering for efficient rendering:

| Filter Stage | Purpose | Typical Reduction |
|--------------|---------|-------------------|
| Spatial filter | Remove outliers | 100% → 96% |
| Base opacity filter | Remove transparent Gaussians | 96% → 95% |
| Temporal filter | Only render temporally-visible | 95% → **8%** |

**Result**: Only ~8% of Gaussians are rendered per frame, enabling interactive framerates with millions of Gaussians.

### Example Usage

**Basic viewing:**
```bash
python src/viewer_4d.py --ckpt results/ckpts/ckpt_30000.pt --total-frames 60
```

**High-quality (show more Gaussians):**
```bash
python src/viewer_4d.py \
    --ckpt results/ckpts/ckpt_30000.pt \
    --total-frames 60 \
    --temporal-threshold 0.01 \
    --spatial-percentile 99
```

**Fast preview (fewer Gaussians):**
```bash
python src/viewer_4d.py \
    --ckpt results/ckpts/ckpt_30000.pt \
    --total-frames 60 \
    --temporal-threshold 0.1 \
    --spatial-percentile 90
```

**Debug mode (no filtering):**
```bash
python src/viewer_4d.py \
    --ckpt results/ckpts/ckpt_30000.pt \
    --total-frames 60 \
    --no-spatial-filter \
    --temporal-threshold 0.0
```

## Key Parameters

### Point Cloud Preparation

| Parameter | Default | Description |
|-----------|---------|-------------|
| `--keyframe-step` | 5 | Frames between keyframes |
| `--max-velocity-distance` | 0.5 | Max k-NN match distance |
| `--sample-ratio` | 1.0 | Point subsampling ratio |

### Training

| Parameter | Default | Description |
|-----------|---------|-------------|
| `--max-steps` | 60000 | Training iterations |
| `--init-duration` | 0.1 | Initial temporal duration |
| `--velocity-lr-start` | 5e-3 | Initial velocity learning rate |
| `--velocity-lr-end` | 1e-4 | Final velocity learning rate |
| `--lambda-4d-reg` | 1e-3 | 4D regularization weight |

## 4D Gaussian Parameters

Each Gaussian has 8 learnable parameter groups:

1. **Position (x)**: [N, 3] - Canonical 3D position
2. **Time (t)**: [N, 1] - When the Gaussian is most visible
3. **Duration (s)**: [N, 1] - Temporal width
4. **Velocity (v)**: [N, 3] - Linear velocity
5. **Scale**: [N, 3] - 3D scale
6. **Quaternion**: [N, 4] - Rotation
7. **Opacity**: [N] - Base opacity
8. **Spherical Harmonics**: [N, K, 3] - View-dependent color

### Motion Model

Position at time t:
```
x(t) = x + v * (t - t_canonical)
```

Temporal opacity (Gaussian falloff):
```
opacity(t) = exp(-0.5 * ((t - t_canonical) / duration)^2)
```
## Regression checks

With the runtime dependencies installed, run the CPU tests with:

```bash
python -m unittest discover -s tests -v
```

These exercise NPZ/combiner round trips, duration gradients, optimizer resume,
relocation, checkpoint compatibility, PLY/NPZ export, and MP4 output. They do not
replace a full scene training and quality comparison on a GPU.

## Citation

If you find this work useful, please cite the original paper:

```bibtex
@InProceedings{Wang_2025_CVPR,
    author    = {Wang, Yifan and Yang, Peishan and Xu, Zhen and Sun, Jiaming and Zhang, Zhanhua and Chen, Yong and Bao, Hujun and Peng, Sida and Zhou, Xiaowei},
    title     = {FreeTimeGS: Free Gaussian Primitives at Anytime Anywhere for Dynamic Scene Reconstruction},
    booktitle = {Proceedings of the IEEE/CVF Conference on Computer Vision and Pattern Recognition (CVPR)},
    month     = {June},
    year      = {2025},
    pages     = {21750-21760}
}
```

## License

This project is licensed under the GNU Affero General Public License v3.0 - see the [LICENSE](LICENSE) file for details.
