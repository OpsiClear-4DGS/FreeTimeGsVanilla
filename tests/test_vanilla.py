"""CPU regressions for the fixes retained on vanilla main.

Run with the project's installed runtime dependencies:
    python -m unittest discover -s tests -v
"""

import contextlib
import io
import math
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import cv2
import numpy as np
import torch
from plyfile import PlyData

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from datasets.FreeTime_dataset import _detect_image_format, find_available_colmap_frames
import combine_frames_fast_keyframes as combiner
from freetime_ops import MIN_DURATION, SPLAT_KEYS, duration_bounds, regularization_4d, temporal_opacity, validate_vanilla_checkpoint
from init_common import apply_scene_transform, read_init_arrays
import simple_trainer_freetime_4d_pure_relocation as trainer
from video_io import MP4Writer
from viewer_4d import Config4D, Splats4D


def write_npz(path, n=11, **overrides):
    values = dict(
        positions=np.arange(n * 3, dtype=np.float32).reshape(n, 3) * 0.01,
        velocities=np.full((n, 3), 0.01, dtype=np.float32),
        colors=np.full((n, 3), 0.5, dtype=np.float32),
        times=np.arange(n, dtype=np.float32) / max(n - 1, 1),
        durations=np.linspace(0.04, 0.14, n, dtype=np.float32),
        frame_start=0, frame_end=n, keyframe_step=2,
    )
    values.update(overrides)
    np.savez(path, **values)
    return values


def make_runner(cfg=None, n=8):
    cfg = cfg or trainer.Config(start_frame=0, end_frame=11)
    runner = object.__new__(trainer.FreeTime4DRunner)
    runner.cfg = cfg
    runner.device = "cpu"
    runner.scene_scale = 2.0
    runner.world_rank = 0
    runner.start_step = 0
    runner.duration_bounds = duration_bounds(cfg.end_frame - cfg.start_frame, cfg.min_duration_frames, cfg.max_duration_frames)
    runner.lr_schedule_steps = cfg.lr_schedule_steps or cfg.max_steps
    runner.sample_seed = 42
    runner.strategy_state = cfg.strategy.initialize_state(scene_scale=runner.scene_scale)
    initial = dict(
        positions=torch.arange(n * 3, dtype=torch.float32).reshape(n, 3) * 0.01,
        velocities=torch.full((n, 3), 0.02),
        colors=torch.full((n, 3), 0.5),
        times=torch.full((n, 1), 0.5),
        durations=torch.linspace(0.04, 0.14, n).reshape(n, 1),
    )
    runner.effective_init_duration = trainer.resolve_init_duration(cfg, initial["durations"])
    runner.splats, runner.optimizers = trainer.create_splats_with_optimizers_4d(
        cfg, initial, runner.scene_scale, "cpu",
    )
    runner.grad_accum = torch.ones(n)
    runner.grad_count = 2
    return runner


class InitializationTests(unittest.TestCase):
    def test_subrange_preserves_physical_time_duration_and_displacement(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "init.npz"
            original = write_npz(path, frame_start=100, frame_end=111)
            actual = read_init_arrays(str(path), 102, 108)
            np.testing.assert_allclose(actual["times"], np.arange(6) / 5, atol=1e-7)
            np.testing.assert_allclose(actual["durations"], original["durations"][2:8] * 2)
            np.testing.assert_allclose(actual["velocities"] / 5, original["velocities"][2:8])
            np.testing.assert_array_equal(actual["positions"], original["positions"][2:8])

    def test_legacy_combiner_end_and_denominator_are_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "legacy.npz"
            write_npz(path, n=10, times=np.arange(10, dtype=np.float32) / 10,
                      frame_end=9, mode="keyframes_with_velocity")
            actual = read_init_arrays(str(path), 0, 10)
            np.testing.assert_allclose(actual["times"], np.arange(10) / 9, atol=1e-7)
            np.testing.assert_allclose(actual["velocities"], 0.09)

    def test_all_loaders_share_time_and_float32_transform_fixes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "init.npz"
            original = write_npz(path)
            transform = np.eye(4, dtype=np.float64)
            transform[:3, :3] *= 2
            transform[:3, 3] = [10, 20, 30]
            for loader in (trainer.load_init_npz, trainer.load_init_npz_stratified,
                           trainer.load_init_npz_keyframe):
                with self.subTest(loader=loader.__name__):
                    values = loader(str(path), max_samples=0, frame_end=11, transform=transform)
                    np.testing.assert_allclose(values["positions"], original["positions"] * 2 + [10, 20, 30], atol=1e-6)
                    np.testing.assert_allclose(values["velocities"], 0.2)
                    np.testing.assert_allclose(values["durations"].flatten(), original["durations"])
                    self.assertTrue(all(value.dtype == torch.float32 for value in values.values()))

    def test_no_sampling_and_small_budgets_never_erase_the_point_set(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "init.npz"
            write_npz(path)
            for loader in (trainer.load_init_npz, trainer.load_init_npz_stratified,
                           trainer.load_init_npz_keyframe):
                with self.subTest(loader=loader.__name__):
                    self.assertEqual(len(loader(str(path), max_samples=0, frame_end=11)["times"]), 11)
                    self.assertEqual(len(loader(str(path), max_samples=2, frame_end=11)["times"]), 2)

    def test_missing_durations_use_keyframe_gap_without_nan(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "init.npz"
            values = write_npz(path)
            values.pop("durations")
            np.savez(path, **values)
            result = trainer.load_init_npz_keyframe(str(path), frame_end=11, max_samples=0)
            np.testing.assert_allclose(result["durations"], 0.4)

    def test_jittered_times_group_by_frame(self):
        times = np.array([0.1999, 0.2, 0.2001, 0.6, 0.6001], dtype=np.float32)
        groups = trainer._time_groups(times, 11)
        self.assertEqual([group.tolist() for group in groups], [[0, 1, 2], [3, 4]])

    def test_empty_range_and_malformed_arrays_fail_at_input(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "init.npz"
            write_npz(path)
            with self.assertRaisesRegex(ValueError, "No initialization points"):
                read_init_arrays(str(path), 40, 50)
            with self.assertRaisesRegex(ValueError, "greater"):
                read_init_arrays(str(path), 3, 3)
            write_npz(path, velocities=np.zeros((2, 3), dtype=np.float32))
            with self.assertRaisesRegex(ValueError, "velocities"):
                read_init_arrays(str(path), 0, 11)

    def test_scene_translation_does_not_change_velocity(self):
        transform = np.eye(4)
        transform[:3, 3] = 100
        points, velocities = apply_scene_transform(np.zeros((2, 3)), np.ones((2, 3)), transform)
        np.testing.assert_array_equal(points, 100)
        np.testing.assert_array_equal(velocities, 1)
        self.assertEqual(points.dtype, np.float32)


class CombinerAndDatasetTests(unittest.TestCase):
    def test_knn_averages_requested_neighbors_and_handles_small_clouds(self):
        source = np.zeros((1, 3), dtype=np.float32)
        target = np.array([[0.1, 0, 0], [0.3, 0, 0]], dtype=np.float32)
        velocity, valid = combiner.compute_velocity_knn(source, target, k=8, n_workers=1)
        np.testing.assert_allclose(velocity, [[0.2, 0, 0]])
        self.assertTrue(valid.all())

    def test_combiner_to_loader_keeps_endpoint_and_real_frame_gap(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = np.arange(12, dtype=np.float32).reshape(4, 3) * 10
            for frame in (10, 14, 17):
                np.save(root / f"points3d_frame{frame:06d}.npy", base + [0.1 * (frame - 10), 0, 0])
            output = root / "init.npz"
            argv = ["combiner", "--input-dir", str(root), "--output-path", str(output),
                    "--frame-start", "10", "--frame-end", "17", "--keyframe-step", "4"]
            with patch.object(sys, "argv", argv), contextlib.redirect_stdout(io.StringIO()):
                combiner.main()
            with np.load(output) as values:
                self.assertEqual(int(values["frame_end"]), 18)
                self.assertEqual(int(values["time_denominator"]), 7)
                np.testing.assert_allclose(np.unique(values["times"]), [0, 4 / 7, 1])
                np.testing.assert_allclose(values["velocities"][:8, 0], 0.1, atol=2e-6)
            actual = read_init_arrays(str(output), 10, 18)
            self.assertEqual(float(actual["times"].max()), 1.0)
            np.testing.assert_allclose(actual["velocities"][:8, 0] / 7, 0.1, atol=2e-6)

    def test_image_extension_detection_accepts_existing_webp_frames(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "000001.WEBP").touch()
            result = _detect_image_format(directory)
            self.assertEqual(result["extension"], ".WEBP")
            self.assertEqual(result["frame_start"], 1)

    def test_existing_colmap_initializers_find_sorted_frame_directories(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for frame in (1, 3, 9):
                path = root / f"frame_{frame:06d}"
                path.mkdir()
                (path / "points3D.bin").touch()
            self.assertEqual([frame for frame, _ in find_available_colmap_frames(directory, 1, 9)], [1, 3])


class ModelAndResumeTests(unittest.TestCase):
    def test_factory_keeps_only_vanilla_parameters_and_npz_durations(self):
        runner = make_runner()
        self.assertEqual(set(runner.splats), SPLAT_KEYS)
        torch.testing.assert_close(runner.splats["durations"].exp().flatten(), torch.linspace(0.04, 0.14, 8))
        torch.testing.assert_close(runner.splats["quats"], torch.tensor([1., 0., 0., 0.]).repeat(8, 1))

    def test_auto_duration_sentinel_cannot_enter_log_math(self):
        cfg = trainer.Config(init_duration=-1, keyframe_step=-1)
        value = trainer.resolve_init_duration(cfg, torch.zeros(4, 1))
        self.assertGreater(value, 0)
        self.assertTrue(math.isfinite(value))

    def test_duration_projection_preserves_gradients_at_the_floor(self):
        runner = make_runner()
        with torch.no_grad():
            runner.splats["durations"].fill_(math.log(1e-6))
        runner._project_temporal_params()
        runner.compute_temporal_opacity(0.51).sum().backward()
        self.assertTrue((runner.splats["durations"].grad.abs() > 0).all())
        torch.testing.assert_close(runner.splats["durations"].exp(), torch.full((8, 1), MIN_DURATION))

    def test_paper_regularizer_stops_only_temporal_gradients(self):
        opacity = torch.tensor([0.3, 0.7], requires_grad=True)
        temporal = torch.tensor([0.5, 1.0], requires_grad=True)
        loss = regularization_4d(opacity, temporal)
        loss.backward()
        self.assertAlmostEqual(float(loss.detach()), 0.425, places=6)
        self.assertIsNone(temporal.grad)
        torch.testing.assert_close(opacity.grad, torch.tensor([0.25, 0.5]))

    def test_resume_uses_identical_adam_settings_and_loads_state(self):
        original = make_runner()
        sum(value.square().sum() for value in original.splats.values()).backward()
        for optimizer in original.optimizers.values():
            optimizer.step()
        checkpoint = dict(step=14, splats=original.splats.state_dict(),
                          optimizers={name: opt.state_dict() for name, opt in original.optimizers.items()},
                          legacy_numpy_scalar=np.float32(1), use_velocity=False)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "checkpoint.pt"
            torch.save(checkpoint, path)
            restored = make_runner()
            restored.load_checkpoint(str(path))
        self.assertEqual(restored.start_step, 15)
        self.assertFalse(restored.cfg.use_velocity)
        for name, optimizer in restored.optimizers.items():
            self.assertIs(type(optimizer), torch.optim.Adam)
            old = original.optimizers[name]
            for setting in ("lr", "eps", "betas"):
                self.assertEqual(optimizer.param_groups[0][setting], old.param_groups[0][setting])
            torch.testing.assert_close(optimizer.state[restored.splats[name]]["exp_avg"],
                                       old.state[original.splats[name]]["exp_avg"])

    def test_resume_without_optimizer_state_keeps_batch_and_scene_scaling(self):
        cfg = trainer.Config(batch_size=4)
        runner = make_runner(cfg)
        expected = trainer.create_optimizers(cfg, runner.splats, runner.scene_scale)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "checkpoint.pt"
            torch.save(dict(step=0, splats=runner.splats.state_dict()), path)
            runner.load_checkpoint(str(path))
        for name, optimizer in runner.optimizers.items():
            self.assertEqual(optimizer.param_groups[0]["lr"], expected[name].param_groups[0]["lr"])

    def test_unsupported_checkpoints_are_rejected_instead_of_silently_reinterpreted(self):
        splats = make_runner().splats.state_dict()
        validate_vanilla_checkpoint({"splats": splats})
        variants = [
            {"splats": {**splats, "marginal_gates": torch.zeros(8, 1)}},
            {"splats": splats, "time_units": "seconds"},
            {"splats": splats, "model_spec": {"duration_activation": "sigmoid"}},
            {"splats": splats, "color_correction": {"bias": torch.ones(3)}},
        ]
        for checkpoint in variants:
            with self.subTest(keys=list(checkpoint)), self.assertRaisesRegex(ValueError, "incompatible with the Vanilla FreeTimeGS model"):
                validate_vanilla_checkpoint(checkpoint)

    def test_relocation_keeps_sources_and_resets_changed_optimizer_state(self):
        runner = make_runner(trainer.Config(relocation_max_ratio=1), n=20)
        sum(value.square().sum() for value in runner.splats.values()).backward()
        for optimizer in runner.optimizers.values():
            optimizer.step()
            optimizer.zero_grad(set_to_none=True)
        with torch.no_grad():
            runner.splats["opacities"][:4] = torch.logit(torch.tensor(1e-4))
        before = {name: value.detach().clone() for name, value in runner.splats.items()}
        self.assertEqual(runner.relocate_gaussians(0), 4)
        for name, value in runner.splats.items():
            torch.testing.assert_close(value[4:], before[name][4:])
            self.assertEqual(float(runner.optimizers[name].state[value]["exp_avg"][:4].abs().sum()), 0)
        self.assertEqual(runner.grad_count, 0)
        self.assertEqual(float(runner.grad_accum.sum()), 0)


class ExportTests(unittest.TestCase):
    def test_compact_export_uses_activated_duration_and_velocity_switch(self):
        with tempfile.TemporaryDirectory() as directory:
            cfg = trainer.Config(result_dir=directory, start_frame=0, end_frame=5, use_velocity=False)
            runner = make_runner(cfg)
            runner._export_ply_compact(7)
            for frame in (0, 2, 4):
                path = next(Path(directory).rglob(f"frame_{frame:06d}.npz"))
                with np.load(path) as actual:
                    t = frame / 4
                    opacity = runner.splats["opacities"].sigmoid() * runner.compute_temporal_opacity(t)
                    mask = opacity > cfg.export_ply_opacity_threshold
                    np.testing.assert_array_equal(actual["indices"], torch.where(mask)[0].numpy())
                    np.testing.assert_allclose(actual["opacities"], opacity[mask].detach().numpy(), atol=1e-3)
                    np.testing.assert_allclose(actual["positions"], runner.splats["means"][mask].detach().numpy(), atol=1e-3)

    def test_ply_export_preserves_raw_scale_and_combined_opacity(self):
        with tempfile.TemporaryDirectory() as directory:
            cfg = trainer.Config(result_dir=directory, start_frame=0, end_frame=3,
                                 export_ply_frame_step=1)
            runner = make_runner(cfg)
            runner._export_ply_full(7)
            vertex = PlyData.read(Path(directory) / "ply_sequence_step7/frame_000001.ply")["vertex"]
            np.testing.assert_allclose(vertex["scale_0"], runner.splats["scales"][:, 0].detach().numpy())
            np.testing.assert_allclose(vertex["opacity"], runner.splats["opacities"].detach().numpy(), atol=1e-6)
            self.assertFalse((Path(directory) / "ply_sequence_step7/frame_000000.ply").exists())

    def test_viewer_matches_training_temporal_and_motion_equations(self):
        runner = make_runner(trainer.Config(use_velocity=False))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "checkpoint.pt"
            torch.save(dict(splats=runner.splats.state_dict(), use_velocity=False, n_frames=11), path)
            viewer = Splats4D(str(path), Config4D(device="cpu", use_spatial_filter=False, precompute_visibility=False))
        torch.testing.assert_close(viewer.compute_temporal_opacity(0.51), runner.compute_temporal_opacity(0.51))
        torch.testing.assert_close(viewer.compute_positions_at_time(0.51), runner.compute_positions_at_time(0.51))
        self.assertEqual(viewer.cfg.total_frames, 11)

    def test_video_writer_handles_odd_dimensions_and_flushes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "video.mp4"
            with MP4Writer(str(path), 30, 9, 7) as writer:
                for _ in range(3):
                    writer.append_data(np.full((7, 9, 3), 128, dtype=np.uint8))
            reader = cv2.VideoCapture(str(path))
            try:
                success, frame = reader.read()
                self.assertTrue(success)
                self.assertEqual(frame.shape, (8, 10, 3))
            finally:
                reader.release()


if __name__ == "__main__":
    unittest.main()
