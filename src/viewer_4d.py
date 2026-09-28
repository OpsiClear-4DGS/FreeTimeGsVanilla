"""
4D Gaussian Splatting Viewer with Temporal Visibility Masking

This viewer loads a trained 4D Gaussian checkpoint and renders it interactively
using viser and nerfview. Key features:

1. Temporal position: mu_x(t) = mu_x + v * (t - mu_t)
2. Temporal opacity: sigma(t) = exp(-0.5 * ((t - mu_t) / s)^2)
3. Combined opacity: sigma(x,t) = sigma(t) * sigma_base
4. Efficient visibility masking based on temporal opacity threshold
5. Optional spatial filtering (sphere covering N% of scene)

Usage:
    python viewer_4d.py --ckpt results/freetime_4d/ckpts/ckpt_29999.pt --port 8080
"""

import argparse
import math
import os
import time
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

import numpy as np
import torch
import torch.nn.functional as F
import viser
from gsplat.rendering import rasterization

import nerfview


from freetime_ops import MIN_DURATION, temporal_opacity, positions_at_time, validate_vanilla_checkpoint


@dataclass
class Config4D:
    """Configuration for 4D viewer."""
    ckpt: str = ""
    port: int = 8080
    device: str = "cuda"

    # Temporal settings
    total_frames: int = 300  # Total frames in the sequence

    # Visibility thresholds for efficiency
    temporal_opacity_threshold: float = 0.01  # Gaussians with temporal_opa < this are masked
    base_opacity_threshold: float = 0.005  # Gaussians with base_opa < this are masked

    # Spatial filtering (sphere covering N% of scene)
    use_spatial_filter: bool = True
    spatial_filter_percentile: float = 95  # Keep points within this percentile from center
    spatial_filter_padding: float = 1.1  # Multiply radius by this for padding

    # Rendering settings
    sh_degree: int = 3
    near_plane: float = 0.01
    far_plane: float = 1e10
    antialiased: bool = False
    packed: bool = True

    # Performance
    precompute_visibility: bool = True  # Precompute visibility masks for all frames
    visibility_cache_frames: int = 300  # Number of frames to cache


class Splats4D:
    """4D Gaussian splats with temporal parameters."""

    def __init__(self, ckpt_path: str, cfg: Config4D):
        self.cfg = cfg
        self.device = cfg.device

        print(f"Loading checkpoint from {ckpt_path}...")
        # Load on CPU first to avoid CUDA init issues, then move to device
        ckpt = torch.load(ckpt_path, map_location="cpu", weights_only=False)
        validate_vanilla_checkpoint(ckpt)
        self.use_velocity = bool(ckpt.get("use_velocity", True))
        cfg.total_frames = int(ckpt.get("n_frames", cfg.total_frames))
        splats = ckpt["splats"]

        # Load all parameters
        self.means = splats["means"].to(cfg.device)  # [N, 3] - canonical positions
        self.scales = splats["scales"].to(cfg.device)  # [N, 3] - log scales
        self.quats = splats["quats"].to(cfg.device)  # [N, 4] - quaternions
        self.opacities = splats["opacities"].to(cfg.device)  # [N] - logit opacities
        self.sh0 = splats["sh0"].to(cfg.device)  # [N, 1, 3] - DC SH
        self.shN = splats["shN"].to(cfg.device)  # [N, K, 3] - higher order SH

        # 4D parameters
        self.times = splats["times"].to(cfg.device)  # [N, 1] - canonical times
        self.durations = splats["durations"].to(cfg.device).clamp_min(np.log(MIN_DURATION))  # [N, 1] - temporal durations (log)
        self.velocities = splats["velocities"].to(cfg.device)  # [N, 3] - velocities

        self.n_gaussians = self.means.shape[0]
        print(f"Loaded {self.n_gaussians:,} Gaussians")
        print(f"  Means shape: {self.means.shape}")
        print(f"  Times range: [{self.times.min().item():.3f}, {self.times.max().item():.3f}]")
        print(f"  Durations range (exp): [{torch.exp(self.durations).min().item():.3f}, {torch.exp(self.durations).max().item():.3f}]")
        print(f"  Velocity magnitude range: [{self.velocities.norm(dim=1).min().item():.3f}, {self.velocities.norm(dim=1).max().item():.3f}]")

        # Precompute base opacity
        self.base_opacity = torch.sigmoid(self.opacities)

        # Compute scene bounds and spatial filter
        self._compute_scene_bounds()

        # Precompute visibility masks if enabled
        self.visibility_masks: Optional[Dict[int, torch.Tensor]] = None
        if cfg.precompute_visibility:
            self._precompute_visibility_masks()

    def _compute_scene_bounds(self):
        """Compute scene bounds and spatial filter sphere."""
        points = self.means.detach().cpu().numpy()

        # Use percentile-based center to avoid outlier influence
        min_coords = np.percentile(points, 5, axis=0)
        max_coords = np.percentile(points, 95, axis=0)
        self.scene_center = torch.tensor(
            (min_coords + max_coords) / 2,
            device=self.device, dtype=torch.float32
        )

        # Compute distances from center
        distances = (self.means - self.scene_center).norm(dim=1)

        # Compute filter radius based on percentile
        radius_percentile = np.percentile(
            distances.cpu().numpy(),
            self.cfg.spatial_filter_percentile
        )
        self.filter_radius = radius_percentile * self.cfg.spatial_filter_padding

        # Create spatial mask
        if self.cfg.use_spatial_filter:
            self.spatial_mask = distances <= self.filter_radius
            n_filtered = self.spatial_mask.sum().item()
            print(f"Spatial filter: keeping {n_filtered:,} / {self.n_gaussians:,} Gaussians "
                  f"({100*n_filtered/self.n_gaussians:.1f}%)")
            print(f"  Filter radius: {self.filter_radius:.3f}")
        else:
            self.spatial_mask = torch.ones(self.n_gaussians, dtype=torch.bool, device=self.device)

        # Apply base opacity threshold
        opacity_mask = self.base_opacity >= self.cfg.base_opacity_threshold
        self.base_mask = self.spatial_mask & opacity_mask
        n_base = self.base_mask.sum().item()
        print(f"Base mask (spatial + opacity): {n_base:,} Gaussians ({100*n_base/self.n_gaussians:.1f}%)")

    def _precompute_visibility_masks(self):
        """Precompute visibility masks for all frames."""
        print("Precomputing visibility masks...")
        self.visibility_masks = {}

        n_frames = min(self.cfg.visibility_cache_frames, self.cfg.total_frames)

        for frame_idx in range(n_frames):
            t = frame_idx / max(1, self.cfg.total_frames - 1)
            temporal_opa = self.compute_temporal_opacity(t)

            # Combine with base mask
            visible = self.base_mask & (temporal_opa >= self.cfg.temporal_opacity_threshold)
            self.visibility_masks[frame_idx] = visible

            if frame_idx % 50 == 0:
                n_visible = visible.sum().item()
                print(f"  Frame {frame_idx}: {n_visible:,} visible ({100*n_visible/self.n_gaussians:.1f}%)")

        print(f"Cached {len(self.visibility_masks)} visibility masks")

    def compute_temporal_opacity(self, t: float) -> torch.Tensor:
        return temporal_opacity(self.times, self.durations, t)

    def compute_positions_at_time(self, t: float) -> torch.Tensor:
        return positions_at_time(self.means, self.velocities, self.times, t, self.use_velocity)

    def get_visibility_mask(self, frame_idx: int, t: float) -> torch.Tensor:
        """
        Get visibility mask for a frame.

        If precomputed, use cached mask. Otherwise compute on-the-fly.
        """
        if self.visibility_masks is not None and frame_idx in self.visibility_masks:
            return self.visibility_masks[frame_idx]

        # Compute on-the-fly
        temporal_opa = self.compute_temporal_opacity(t)
        return self.base_mask & (temporal_opa >= self.cfg.temporal_opacity_threshold)

    def get_gaussians_at_time(
        self,
        t: float,
        frame_idx: int,
        apply_visibility_mask: bool = True
    ) -> Tuple[torch.Tensor, torch.Tensor, torch.Tensor, torch.Tensor, torch.Tensor, torch.Tensor]:
        """
        Get Gaussian parameters at time t.

        Returns:
            means, quats, scales, opacities, colors, mask
        """
        # Compute positions at time t
        means = self.compute_positions_at_time(t)

        # Compute temporal opacity
        temporal_opa = self.compute_temporal_opacity(t)

        # Combined opacity
        opacities = self.base_opacity * temporal_opa
        opacities = torch.clamp(opacities, min=1e-4)

        # Static parameters
        quats = self.quats
        scales = torch.exp(self.scales)
        colors = torch.cat([self.sh0, self.shN], dim=1)

        # Get visibility mask
        if apply_visibility_mask:
            mask = self.get_visibility_mask(frame_idx, t)
        else:
            mask = torch.ones(self.n_gaussians, dtype=torch.bool, device=self.device)

        return means, quats, scales, opacities, colors, mask


class Viewer4D:
    """Interactive 4D Gaussian viewer using viser and nerfview."""

    def __init__(self, cfg: Config4D):
        self.cfg = cfg
        self.device = cfg.device

        # Load splats
        self.splats = Splats4D(cfg.ckpt, cfg)

        # Viewer state
        self.current_frame = 0
        self.auto_play = False
        self.play_speed = 30.0  # FPS

        # Set up viser server
        self.server = viser.ViserServer(port=cfg.port, verbose=False)

        # Set up UI
        self._setup_ui()

        # Set up nerfview
        self.viewer = nerfview.Viewer(
            server=self.server,
            render_fn=self._render_fn,
            mode="rendering",
        )

    def _setup_ui(self):
        """Set up UI controls."""
        with self.server.gui.add_folder("Animation"):
            self.time_slider = self.server.gui.add_slider(
                "Frame",
                min=0,
                max=self.cfg.total_frames - 1,
                step=1,
                initial_value=0,
            )
            self.auto_play_checkbox = self.server.gui.add_checkbox(
                "Auto Play", initial_value=False
            )
            self.play_speed_slider = self.server.gui.add_slider(
                "Play Speed (FPS)",
                min=1.0,
                max=60.0,
                step=1.0,
                initial_value=30.0,
            )

        with self.server.gui.add_folder("Visibility Filtering"):
            self.temporal_threshold_slider = self.server.gui.add_slider(
                "Temporal Opacity Threshold",
                min=0.0,
                max=0.5,
                step=0.01,
                initial_value=self.cfg.temporal_opacity_threshold,
            )
            self.use_visibility_mask = self.server.gui.add_checkbox(
                "Use Visibility Mask", initial_value=True
            )

        with self.server.gui.add_folder("Stats"):
            self.stats_text = self.server.gui.add_markdown("Initializing...")

        # Callbacks
        @self.time_slider.on_update
        def _(_):
            self.current_frame = int(self.time_slider.value)
            self._update_stats()
            # Trigger re-render when frame changes
            if hasattr(self, 'viewer'):
                self.viewer.rerender(None)

        @self.auto_play_checkbox.on_update
        def _(_):
            self.auto_play = self.auto_play_checkbox.value

        @self.play_speed_slider.on_update
        def _(_):
            self.play_speed = self.play_speed_slider.value

        @self.temporal_threshold_slider.on_update
        def _(_):
            self.cfg.temporal_opacity_threshold = self.temporal_threshold_slider.value
            # Invalidate visibility cache
            if self.splats.visibility_masks is not None:
                self.splats._precompute_visibility_masks()

    def _update_stats(self):
        """Update stats display."""
        t = self.current_frame / max(1, self.cfg.total_frames - 1)
        mask = self.splats.get_visibility_mask(self.current_frame, t)
        n_visible = mask.sum().item()

        self.stats_text.content = f"""
**Frame:** {self.current_frame} / {self.cfg.total_frames - 1}

**Time:** {t:.3f}

**Visible Gaussians:** {n_visible:,} / {self.splats.n_gaussians:,} ({100*n_visible/self.splats.n_gaussians:.1f}%)
"""

    @torch.no_grad()
    def _render_fn(
        self,
        camera_state: nerfview.CameraState,
        img_wh: Tuple[int, int],
    ):
        """Render function for nerfview."""
        width, height = img_wh

        # Get camera matrices
        c2w = torch.from_numpy(camera_state.c2w).float().to(self.device)
        K = torch.from_numpy(camera_state.get_K(img_wh)).float().to(self.device)
        viewmat = c2w.inverse()

        # Get normalized time
        t = self.current_frame / max(1, self.cfg.total_frames - 1)

        # Get Gaussian parameters at time t
        means, quats, scales, opacities, colors, mask = self.splats.get_gaussians_at_time(
            t=t,
            frame_idx=self.current_frame,
            apply_visibility_mask=self.use_visibility_mask.value,
        )

        # Apply visibility mask
        if self.use_visibility_mask.value and mask.sum() > 0:
            means = means[mask]
            quats = quats[mask]
            scales = scales[mask]
            opacities = opacities[mask]
            colors = colors[mask]

        if means.shape[0] == 0:
            return np.zeros((height, width, 3), dtype=np.float32)

        # Render
        rasterize_mode = "antialiased" if self.cfg.antialiased else "classic"

        render_colors, render_alphas, _ = rasterization(
            means=means,
            quats=quats,
            scales=scales,
            opacities=opacities,
            colors=colors,
            viewmats=viewmat[None],
            Ks=K[None],
            width=width,
            height=height,
            sh_degree=self.cfg.sh_degree,
            near_plane=self.cfg.near_plane,
            far_plane=self.cfg.far_plane,
            packed=self.cfg.packed,
            rasterize_mode=rasterize_mode,
            backgrounds=torch.ones(3, device=self.device),  # [C=1, D=3] -> [D] for packed
        )

        render_rgbs = render_colors[0, ..., :3].clamp(0, 1).cpu().numpy()
        return render_rgbs

    def run(self):
        """Run the viewer."""
        print(f"Viewer running at http://localhost:{self.cfg.port}")
        self._update_stats()

        last_frame_time = time.time()

        try:
            while True:
                # Handle auto-play
                if self.auto_play:
                    current_time = time.time()
                    elapsed = current_time - last_frame_time

                    if elapsed >= 1.0 / self.play_speed:
                        self.current_frame = (self.current_frame + 1) % self.cfg.total_frames
                        self.time_slider.value = self.current_frame
                        self._update_stats()
                        last_frame_time = current_time
                        # Trigger re-render for all clients when frame changes
                        self.viewer.rerender(None)

                time.sleep(0.01)

        except KeyboardInterrupt:
            print("Viewer stopped.")


def main():
    parser = argparse.ArgumentParser(description="4D Gaussian Splatting Viewer")
    parser.add_argument("--ckpt", type=str, required=True, help="Path to checkpoint .pt file")
    parser.add_argument("--port", type=int, default=8080, help="Viewer port")
    parser.add_argument("--device", type=str, default="cuda", help="Device (cuda or cuda:N)")
    parser.add_argument("--total-frames", type=int, default=300, help="Total frames in sequence")
    parser.add_argument("--temporal-threshold", type=float, default=0.01,
                        help="Temporal opacity threshold for visibility filtering")
    parser.add_argument("--spatial-percentile", type=float, default=95,
                        help="Percentile of points to keep in spatial filter")
    parser.add_argument("--no-spatial-filter", action="store_true",
                        help="Disable spatial filtering")
    parser.add_argument("--no-precompute", action="store_true",
                        help="Disable precomputing visibility masks")
    parser.add_argument("--sh-degree", type=int, default=3, help="SH degree")

    args = parser.parse_args()

    cfg = Config4D(
        ckpt=args.ckpt,
        port=args.port,
        device=args.device,
        total_frames=args.total_frames,
        temporal_opacity_threshold=args.temporal_threshold,
        spatial_filter_percentile=args.spatial_percentile,
        use_spatial_filter=not args.no_spatial_filter,
        precompute_visibility=not args.no_precompute,
        sh_degree=args.sh_degree,
    )

    viewer = Viewer4D(cfg)
    viewer.run()


if __name__ == "__main__":
    main()
