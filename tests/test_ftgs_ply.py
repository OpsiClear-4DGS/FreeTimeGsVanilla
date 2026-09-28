"""Read exported files independently with plyfile and check model semantics."""

import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from plyfile import PlyData
import torch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from export_ftgs_ply import export_checkpoint, save_ftgs_ply
from freetime_ops import MIN_DURATION, positions_at_time, temporal_opacity


def make_splats(n=7, degree=3):
    k = (degree + 1) ** 2 - 1
    return {
        "means": torch.arange(n * 3, dtype=torch.float32).reshape(n, 3) / 10,
        "scales": torch.full((n, 3), -2.0),
        "quats": torch.tensor([[1., 0., 0., 0.]]).repeat(n, 1),
        "opacities": torch.linspace(-2, 2, n),
        "sh0": torch.arange(n * 3, dtype=torch.float32).reshape(n, 1, 3) / 20,
        "shN": torch.arange(n * k * 3, dtype=torch.float32).reshape(n, k, 3) / 100,
        "times": torch.linspace(-0.1, 1.1, n).reshape(n, 1),
        "durations": torch.full((n, 1), math.log(0.2)),
        "velocities": torch.arange(n * 3, dtype=torch.float32).reshape(n, 3) / 30,
    }


def columns(vertex, names):
    return np.column_stack([vertex[name] for name in names])


class FtgsPlyTests(unittest.TestCase):
    def test_chunked_binary_file_preserves_all_parameters_and_sh_order(self):
        splats = {name: torch.nn.Parameter(value) for name, value in make_splats().items()}
        before = {name: value.detach().clone() for name, value in splats.items()}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "scene.ftgs.ply"
            save_ftgs_ply(path, splats, n_frames=61, chunk_size=2)
            data = PlyData.read(path)
            vertex = data["vertex"]
            self.assertFalse(data.text)
            self.assertEqual(data.byte_order, "<")
            self.assertEqual(vertex.count, 7)
            self.assertIn("ftgs_version 1", data.comments)
            self.assertIn("n_frames 61", data.comments)
            self.assertIn("sh_degree 3", data.comments)
            self.assertIn("time_units normalized", data.comments)
            for key, names in {
                "means": ["x", "y", "z"], "scales": [f"scale_{i}" for i in range(3)],
                "quats": [f"rot_{i}" for i in range(4)],
                "times": ["time"], "durations": ["log_duration"],
                "velocities": [f"velocity_{i}" for i in range(3)],
            }.items():
                np.testing.assert_array_equal(columns(vertex, names), before[key].numpy())
            np.testing.assert_array_equal(vertex["opacity"], before["opacities"].numpy())
            np.testing.assert_array_equal(columns(vertex, ["nx", "ny", "nz"]), np.zeros((7, 3)))
            for channel in range(3):
                np.testing.assert_array_equal(vertex[f"f_dc_{channel}"], before["sh0"][:, 0, channel])
                for band in range(15):
                    np.testing.assert_array_equal(vertex[f"f_rest_{channel * 15 + band}"],
                                                  before["shN"][:, band, channel])
        for name in splats:
            torch.testing.assert_close(splats[name], before[name])
            self.assertIsNone(splats[name].grad)

    def test_readback_reconstructs_motion_and_opacity_at_arbitrary_times(self):
        splats = make_splats()
        with tempfile.TemporaryDirectory() as directory:
            for use_velocity in (True, False):
                path = Path(directory) / "scene.ftgs.ply"
                save_ftgs_ply(path, splats, use_velocity=use_velocity)
                data = PlyData.read(path)
                vertex = data["vertex"]
                self.assertIn(f"use_velocity {int(use_velocity)}", data.comments)
                means = columns(vertex, ["x", "y", "z"])
                velocity = columns(vertex, ["velocity_0", "velocity_1", "velocity_2"])
                for t in (0., 0.37, 1.):
                    delta = t - vertex["time"]
                    positions = means + velocity * delta[:, None] if use_velocity else means
                    opacity = np.maximum(
                        1 / (1 + np.exp(-vertex["opacity"]))
                        * np.exp(-0.5 * (delta / np.exp(vertex["log_duration"])) ** 2), 1e-4,
                    )
                    expected_positions = positions_at_time(splats["means"], splats["velocities"],
                                                          splats["times"], t, use_velocity)
                    expected_opacity = (splats["opacities"].sigmoid()
                                        * temporal_opacity(splats["times"], splats["durations"], t)).clamp_min(1e-4)
                    np.testing.assert_allclose(positions, expected_positions.numpy(), atol=1e-6)
                    np.testing.assert_allclose(opacity, expected_opacity.numpy(), atol=1e-7)

    def test_legacy_duration_floor_and_disabled_velocity_metadata(self):
        splats = make_splats()
        splats["durations"][0] = -20
        with tempfile.TemporaryDirectory() as directory:
            checkpoint = Path(directory) / "ckpt_7.pt"
            torch.save({"splats": splats, "use_velocity": False, "n_frames": 11}, checkpoint)
            output = export_checkpoint(checkpoint)
            self.assertEqual(output, checkpoint.with_suffix(".ftgs.ply"))
            data = PlyData.read(output)
            self.assertIn("use_velocity 0", data.comments)
            self.assertIn("n_frames 11", data.comments)
            self.assertAlmostEqual(float(np.exp(data["vertex"]["log_duration"][0])), MIN_DURATION)
            self.assertEqual(splats["durations"][0].item(), -20)

    def test_empty_models_and_sh_zero_are_valid_ply_files(self):
        with tempfile.TemporaryDirectory() as directory:
            for n in (0, 3):
                path = Path(directory) / "scene.ftgs.ply"
                save_ftgs_ply(path, make_splats(n=n, degree=0))
                data = PlyData.read(path)
                self.assertEqual(data["vertex"].count, n)
                self.assertIn("sh_degree 0", data.comments)
                self.assertNotIn("f_rest_0", data["vertex"].data.dtype.names)

    def test_invalid_late_chunk_does_not_replace_existing_file(self):
        splats = make_splats()
        splats["velocities"][-1, 0] = float("nan")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "scene.ftgs.ply"
            path.write_bytes(b"existing model")
            with self.assertRaisesRegex(ValueError, "velocities contains non-finite"):
                save_ftgs_ply(path, splats, chunk_size=2)
            self.assertEqual(path.read_bytes(), b"existing model")
            self.assertEqual(list(Path(directory).iterdir()), [path])

    def test_invalid_shapes_and_unsupported_checkpoints_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "scene.ftgs.ply"
            splats = make_splats()
            splats["times"] = torch.zeros(6, 1)
            with self.assertRaisesRegex(ValueError, "times must be"):
                save_ftgs_ply(path, splats)
            self.assertFalse(path.exists())
            checkpoint = Path(directory) / "model.pt"
            torch.save({"splats": make_splats(), "color_correction": {"bias": torch.ones(3)}}, checkpoint)
            with self.assertRaisesRegex(ValueError, "incompatible with the Vanilla FreeTimeGS model"):
                export_checkpoint(checkpoint, path)
            self.assertFalse(path.exists())

    def test_standalone_cli_exports_on_cpu_with_legacy_metadata_overrides(self):
        with tempfile.TemporaryDirectory() as directory:
            checkpoint = Path(directory) / "model.pt"
            output = Path(directory) / "output/scene.ftgs.ply"
            torch.save({"splats": make_splats()}, checkpoint)
            result = subprocess.run([
                sys.executable, str(ROOT / "src/export_ftgs_ply.py"), "--ckpt", str(checkpoint),
                "--output", str(output), "--n-frames", "31", "--no-velocity",
            ], cwd=directory, env={**os.environ, "CUDA_VISIBLE_DEVICES": ""},
                capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stderr)
            data = PlyData.read(output)
            self.assertIn("n_frames 31", data.comments)
            self.assertIn("use_velocity 0", data.comments)

    def test_trainer_mode_exports_one_complete_model_even_with_compact_default(self):
        from test_vanilla import make_runner, trainer

        with tempfile.TemporaryDirectory() as directory:
            cfg = trainer.Config(result_dir=directory, start_frame=5, end_frame=16,
                                 use_velocity=False, export_ply_format="ftgs")
            runner = make_runner(cfg)
            runner.export_ply_sequence(7)
            path = Path(directory) / "ckpt_7.ftgs.ply"
            self.assertEqual(list(Path(directory).iterdir()), [path])
            data = PlyData.read(path)
            self.assertEqual(data["vertex"].count, 8)
            self.assertIn("n_frames 11", data.comments)
            self.assertIn("use_velocity 0", data.comments)

    def test_trainer_export_only_bypasses_dataset_initialization(self):
        from test_vanilla import trainer

        with tempfile.TemporaryDirectory() as directory:
            checkpoint = Path(directory) / "ckpt_9.pt"
            torch.save({"splats": make_splats(), "n_frames": 31, "use_velocity": False}, checkpoint)
            cfg = trainer.Config(result_dir=directory, ckpt_path=str(checkpoint),
                                 export_only=True, export_ply_format="ftgs")
            with patch.object(trainer, "FreeTime4DRunner", side_effect=AssertionError("Must not load dataset")):
                trainer.main(0, 0, 1, cfg)
            data = PlyData.read(Path(directory) / "ckpt_9.ftgs.ply")
            self.assertIn("n_frames 31", data.comments)
            self.assertIn("use_velocity 0", data.comments)


if __name__ == "__main__":
    unittest.main()
