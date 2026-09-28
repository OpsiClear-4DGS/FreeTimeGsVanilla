"""Transparency regressions: soft edges, black objects, and background gradients."""
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import cv2
import imageio.v2 as imageio
import numpy as np
import torch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT)]
from datasets.FreeTime_dataset import FreeTimeDataset
from foreground_loss import composite_foreground


class ForegroundLossTests(unittest.TestCase):
    def test_correct_soft_foreground_matches_on_any_background(self):
        rgb = torch.tensor([[[[0., 0., 0.], [.2, .6, .9], [.8, .1, .4]]]])
        alpha = torch.tensor([[[[1.], [.5], [0.]]]])
        for bg in (0., .5, 1.):
            pred, target, loss = composite_foreground(rgb * alpha, alpha, rgb, alpha, rgb.new_full((1,3), bg))
            torch.testing.assert_close(pred, target)
            self.assertEqual(loss.item(), 0.)
            torch.testing.assert_close(target[..., 0, :], torch.zeros(1,1,3))

    def test_black_floaters_get_a_removal_gradient_even_on_black(self):
        # RGB L1 cannot see this opaque black floater; alpha supervision can.
        alpha = torch.tensor([[[[.8], [.2]]]], requires_grad=True)
        desired = torch.tensor([[[[0.], [1.]]]])
        black = torch.zeros(1,1,2,3)
        pred, target, loss = composite_foreground(black, alpha, black, desired, torch.zeros(1,3))
        self.assertEqual((pred-target).abs().sum().item(), 0.)
        loss.backward()
        self.assertGreater(alpha.grad[0,0,0,0].item(), 0.)  # decrease outside opacity
        self.assertLess(alpha.grad[0,0,1,0].item(), 0.)  # retain black foreground

    def test_random_background_is_matched_and_target_is_premultiplied_once(self):
        alpha = torch.full((1,2,2,1), .5)
        rgb = torch.full((1,2,2,3), .8)
        pred, target, _ = composite_foreground(rgb*alpha, alpha, rgb, alpha, rgb.new_full((1,3), .2))
        torch.testing.assert_close(pred, torch.full_like(pred, .5))
        torch.testing.assert_close(target, pred)

    def test_misaligned_alpha_is_rejected_instead_of_broadcasting(self):
        rgb = torch.zeros(1,2,3,3)
        with self.assertRaisesRegex(ValueError, 'align'):
            composite_foreground(rgb, torch.zeros(1,2,3,1), rgb, torch.zeros(1,2,3), torch.zeros(1,3))


class ForegroundDatasetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.image = np.zeros((8,10,4), np.uint8)
        self.image[..., 0] = np.arange(10)[None, :] * 25
        self.image[..., 3] = np.arange(10)[None, :] * 25
        self.image[0,0,3] = 255  # truly black foreground
        imageio.imwrite(self.root/'000000.png', self.image)
        self.parser = SimpleNamespace(
            total_frames=1, start_frame=0, camera_names=['cam000'], campaths=[str(self.root)],
            frame_start_offset=0, frame_digits=6, image_format='.png', factor=1,
            camera_ids=[1], Ks_dict={1:np.eye(3)}, params_dict={1:[]},
            camtoworlds=np.eye(4)[None], mask_dict={1:None},
        )

    def dataset(self, **kw):
        return FreeTimeDataset(self.parser, test_set=[], **kw)

    def test_straight_rgb_soft_alpha_and_black_foreground_survive(self):
        data = self.dataset(alpha_mode='transparent')[0]
        np.testing.assert_array_equal(data['image'], self.image[..., :3])
        np.testing.assert_allclose(data['alpha'], self.image[..., 3:4]/255., atol=1e-7)
        self.assertEqual(data['alpha'][0,0].item(), 1.)
        self.assertEqual(data['image'][0,0].sum().item(), 0.)
        self.assertNotIn('alpha', self.dataset()[0])

    def test_resize_undistortion_and_crop_keep_alpha_aligned(self):
        self.parser.factor = 2
        self.parser.params_dict = {1:[.1]}
        yy, xx = np.indices((4,5), dtype=np.float32)
        self.parser.mapx_dict = {1:xx + .25}
        self.parser.mapy_dict = {1:yy}
        self.parser.roi_undist_dict = {1:(1,0,4,4)}
        expected = cv2.resize(self.image, (5,4), interpolation=cv2.INTER_LINEAR)
        expected = cv2.remap(expected, xx+.25, yy, cv2.INTER_LINEAR)[:,1:5]
        expected = expected[1:3,1:3]
        with patch('numpy.random.randint', side_effect=[1,1]):
            data = self.dataset(alpha_mode='transparent', patch_size=2)[0]
        np.testing.assert_array_equal(data['image'], expected[..., :3])
        np.testing.assert_allclose(data['alpha'], expected[..., 3:4]/255., atol=1e-7)

    def test_rgb_or_missing_frame_cannot_silently_disable_supervision(self):
        imageio.imwrite(self.root/'000000.png', self.image[..., :3])
        with self.assertRaisesRegex(ValueError, 'straight RGBA'):
            self.dataset(alpha_mode='transparent')[0]
        self.assertNotIn('alpha', self.dataset()[0])
        (self.root/'000000.png').unlink()
        with self.assertRaisesRegex(FileNotFoundError, 'RGBA'):
            self.dataset(alpha_mode='transparent')[0]
        self.assertIsNone(self.dataset()[0])


if __name__ == '__main__':
    unittest.main()
