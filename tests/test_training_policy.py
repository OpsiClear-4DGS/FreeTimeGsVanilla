"""Recipe invariants: physical time, phase transitions and interrupted updates."""

import math
from pathlib import Path
import random
import tempfile
import unittest

import numpy as np
from plyfile import PlyData
import torch

from test_vanilla import make_runner, trainer
from export_ftgs_ply import export_checkpoint
from foreground_loss import foreground_mse_loss
from freetime_ops import duration_bounds, positions_at_time, temporal_opacity
from training_policy import TrainingPhase, IterationSampler, apply_phase, phase_at_step, validate_phases
from viewer_4d import Config4D, Splats4D


class TemporalContractTests(unittest.TestCase):
    def test_frame_bounds_preserve_physical_motion_and_activation_for_different_clip_lengths(self):
        results = []
        for frames in (13, 121):
            span = frames - 1
            self.assertEqual(duration_bounds(frames, .5, 2), (.5 / span, 2 / span))
            centers = torch.tensor([[5 / span]])
            widths = torch.tensor([[math.log(.75 / span)]])
            means = torch.tensor([[1., 2., 3.]])
            velocities = torch.tensor([[.03, -.02, .01]]) * span
            t = 5.5 / span
            results.append((positions_at_time(means, velocities, centers, t), temporal_opacity(centers, widths, t)))
        for actual, expected in zip(results[0], results[1]):
            torch.testing.assert_close(actual, expected)

    def test_checkpoint_export_and_viewer_keep_subframe_sigma_on_full_clip(self):
        runner = make_runner(trainer.Config(start_frame=0, end_frame=121,
            min_duration_frames=.5, max_duration_frames=2))
        with torch.no_grad():
            runner.splats['durations'].fill_(math.log(.75 / 120))
            runner.splats['times'].fill_(60 / 120)
        with tempfile.TemporaryDirectory() as directory:
            checkpoint = Path(directory) / 'model.pt'
            torch.save(runner.checkpoint(3), checkpoint)
            ply = PlyData.read(export_checkpoint(checkpoint))
            widths = torch.from_numpy(ply['vertex']['log_duration'].copy())[:, None]
            torch.testing.assert_close(widths, runner.splats['durations'])
            self.assertIn(f'min_duration {.5 / 120}', ply.comments)
            viewer = Splats4D(str(checkpoint), Config4D(device='cpu', use_spatial_filter=False, precompute_visibility=False))
            for frame in (59., 60., 60.5, 61.):
                torch.testing.assert_close(viewer.compute_temporal_opacity(frame / 120), runner.compute_temporal_opacity(frame / 120))

    def test_projection_respects_both_bounds_and_invalid_bounds_fail(self):
        runner = make_runner(trainer.Config(start_frame=0, end_frame=121,
            min_duration_frames=.5, max_duration_frames=2))
        with torch.no_grad():
            runner.splats['durations'][0] = -20
            runner.splats['durations'][1] = 0
        runner._project_temporal_params()
        self.assertAlmostEqual(runner.splats['durations'][0].exp().item() * 120, .5, places=6)
        self.assertAlmostEqual(runner.splats['durations'][1].exp().item() * 120, 2, places=6)
        for minimum, maximum in ((0, None), (float('nan'), None), (2, 1), (.5, float('inf'))):
            with self.subTest(minimum=minimum), self.assertRaises(ValueError):
                duration_bounds(121, minimum, maximum)


class PhaseTests(unittest.TestCase):
    def test_phase_boundary_freezes_temporal_parameters_without_erasing_adam_momentum(self):
        cfg = trainer.Config(phases=[TrainingPhase(start_step=1, freeze_times=True,
            freeze_durations=True, allow_relocation=False, position_lr_factor=.25,
            foreground_mse_weight=2, lpips_interval=4)])
        runner = make_runner(cfg)
        apply_phase(runner, 0)
        sum(p.square().sum() for p in runner.splats.values()).backward()
        for opt in runner.optimizers.values():
            opt.step(); opt.zero_grad(set_to_none=True)
        times = runner.splats['times'].detach().clone()
        widths = runner.splats['durations'].detach().clone()
        state = runner.optimizers['means'].state[runner.splats['means']]['exp_avg'].clone()
        policy = apply_phase(runner, 1)
        torch.testing.assert_close(runner.optimizers['means'].state[runner.splats['means']]['exp_avg'], state)
        self.assertFalse(policy.allow_relocation)
        self.assertEqual(policy.foreground_mse_weight, 2)
        self.assertEqual(policy.lpips_interval, 4)
        sum(p.square().sum() for p in runner.splats.values() if p.requires_grad).backward()
        for opt in runner.optimizers.values():
            opt.step(); opt.zero_grad(set_to_none=True)
        torch.testing.assert_close(runner.splats['times'], times)
        torch.testing.assert_close(runner.splats['durations'], widths)

    def test_phase_changes_inherit_and_invalid_order_is_rejected(self):
        cfg = trainer.Config(freeze_times=True, phases=[TrainingPhase(10, foreground_mse_weight=1),
            TrainingPhase(20, freeze_durations=True, lpips_interval=4)])
        validate_phases(cfg)
        self.assertEqual(phase_at_step(cfg, 9).foreground_mse_weight, 0)
        phase = phase_at_step(cfg, 20)
        self.assertTrue(phase.freeze_times and phase.freeze_durations)
        self.assertEqual(phase.foreground_mse_weight, 1)
        for phases in ([TrainingPhase(10), TrainingPhase(9)], [TrainingPhase(0, lpips_interval=0)]):
            with self.assertRaises(ValueError):
                validate_phases(trainer.Config(phases=phases))

    def test_foreground_loss_is_area_normalized_and_empty_masks_have_finite_zero_gradients(self):
        pred = torch.ones(1, 2, 4, 3, requires_grad=True)
        target = torch.zeros_like(pred)
        alpha = torch.zeros(1, 2, 4, 1)
        alpha[0, 0, 0] = 1
        self.assertEqual(foreground_mse_loss(pred, target, alpha).item(), 1)
        alpha.zero_()
        loss = foreground_mse_loss(pred, target, alpha)
        loss.backward()
        self.assertEqual(loss.item(), 0)
        self.assertEqual(pred.grad.abs().sum().item(), 0)


class ResumeTests(unittest.TestCase):
    def test_incomplete_resume_state_and_changed_frame_clock_are_rejected(self):
        original = make_runner()
        with tempfile.TemporaryDirectory() as directory:
            checkpoint = Path(directory) / 'model.pt'
            state = original.checkpoint(0)
            state['optimizers'].pop('means')
            torch.save(state, checkpoint)
            with self.assertRaisesRegex(ValueError, 'missing Adam'):
                make_runner().load_checkpoint(str(checkpoint))
            torch.save(original.checkpoint(0), checkpoint)
            with self.assertRaisesRegex(ValueError, 'frame range'):
                make_runner(trainer.Config(end_frame=12)).load_checkpoint(str(checkpoint))

    def test_sampling_continues_at_consumed_iteration_despite_prefetch(self):
        rng = torch.get_rng_state().clone()
        whole = list(IterationSampler(7, 0, 30, 42))
        self.assertEqual(whole[9:], list(IterationSampler(7, 9, 30, 42)))
        self.assertEqual(len(set(whole[:7])), 7)
        torch.testing.assert_close(torch.get_rng_state(), rng)

    def test_saved_resume_matches_next_updates_and_keeps_original_schedule_when_extended(self):
        cfg = trainer.Config(max_steps=10, phases=[TrainingPhase(3, freeze_times=True, freeze_durations=True)])
        original = make_runner(cfg)

        def update(runner, step):
            apply_phase(runner, step)
            factor = torch.rand(()) + random.random() + np.random.random()
            loss = sum(p.square().sum() for p in runner.splats.values() if p.requires_grad) * factor
            loss.backward()
            runner.grad_accum += runner.splats['means'].grad.norm(dim=-1)
            runner.grad_count += 1
            for opt in runner.optimizers.values():
                opt.step(); opt.zero_grad(set_to_none=True)
            runner._project_temporal_params()

        torch.manual_seed(12); np.random.seed(12); random.seed(12)
        for step in range(2):
            update(original, step)
        with tempfile.TemporaryDirectory() as directory:
            checkpoint = Path(directory) / 'model.pt'
            torch.save(original.checkpoint(1), checkpoint)
            for step in range(2, 6):
                update(original, step)
            restored = make_runner(trainer.Config(max_steps=100))
            restored.load_checkpoint(str(checkpoint))
            self.assertEqual(restored.lr_schedule_steps, 10)
            for step in range(2, 6):
                update(restored, step)
            for name in original.splats:
                torch.testing.assert_close(restored.splats[name], original.splats[name], rtol=0, atol=0)
            torch.testing.assert_close(restored.grad_accum, original.grad_accum, rtol=0, atol=0)
            self.assertEqual(restored.grad_count, original.grad_count)
            fine = make_runner(trainer.Config(resume_mode='finetune'))
            fine.load_checkpoint(str(checkpoint))
            self.assertEqual(fine.start_step, 0)
            self.assertTrue(all(not opt.state for opt in fine.optimizers.values()))


if __name__ == '__main__':
    unittest.main()
