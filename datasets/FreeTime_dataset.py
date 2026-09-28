"""
FreeTime Dataset - Full Sequence Multi-Frame COLMAP Dataset

This dataset loads:
1. Camera poses from a reference COLMAP reconstruction
2. Images from ALL frames (full sequence, no GOP chunking)
3. Normalized time [0, 1] over the full sequence

Key differences from GIFStream dataset:
- No GOP_size chunking - uses total_frames for full sequence
- Time normalized over entire sequence
- Supports multi-frame COLMAP for 4D initialization (sparse frames)

Supports two formats (auto-detected):
1. "flat" format: cam00/00001.png
2. "nested" format: 002-002/000000.jpg (like your elly dataset)

Usage:
    parser = FreeTimeParser(data_dir, start_frame=0, end_frame=300)
    dataset = FreeTimeDataset(parser, split="train")
"""

import os
import re
import json
from typing import Any, Dict, List, Optional, Literal, Tuple
from pathlib import Path

import cv2
import imageio.v2 as imageio
import numpy as np
import torch
from tqdm import tqdm

try:
    # Try new API first (pycolmap >= 3.10)
    from pycolmap import Reconstruction as PyColmapReconstruction
    PYCOLMAP_API = "new"
except ImportError:
    try:
        # Fall back to old API (pycolmap < 3.10)
        from pycolmap import SceneManager
        PyColmapReconstruction = None
        PYCOLMAP_API = "old"
    except ImportError:
        SceneManager = None
        PyColmapReconstruction = None
        PYCOLMAP_API = None
        print("pycolmap not found. Install with: pip install pycolmap")

from .normalize import (
    align_principle_axes,
    similarity_from_cameras,
    transform_cameras,
    transform_points,
)


def find_available_colmap_frames(sparse_dir: str, start_frame: int, end_frame: int):
    """Find per-frame reconstructions used by the existing COLMAP init loaders."""
    frames = []
    for path in Path(sparse_dir).glob("frame_*"):
        suffix = path.name.removeprefix("frame_")
        if path.is_dir() and suffix.isdigit() and start_frame <= int(suffix) < end_frame:
            model = path / "0" if (path / "0").is_dir() else path
            if (model / "points3D.bin").exists() or (model / "points3D.txt").exists():
                frames.append((int(suffix), str(model)))
    return sorted(frames)


def _load_colmap_points(colmap_path: str) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    Load 3D points from COLMAP using either new or old pycolmap API.

    Returns:
        positions: Nx3 float32 array of 3D positions
        colors: Nx3 uint8 array of RGB colors
        errors: N float32 array of reprojection errors
    """
    if PYCOLMAP_API == "new":
        rec = PyColmapReconstruction()
        rec.read(colmap_path)
        points3D_dict = rec.points3D
        if len(points3D_dict) == 0:
            return np.zeros((0, 3), dtype=np.float32), np.zeros((0, 3), dtype=np.uint8), np.zeros(0, dtype=np.float32)

        positions_list = []
        colors_list = []
        errors_list = []
        for pt_id, pt in points3D_dict.items():
            positions_list.append(pt.xyz)
            colors_list.append(pt.color)
            errors_list.append(pt.error)

        return (np.array(positions_list, dtype=np.float32),
                np.array(colors_list, dtype=np.uint8),
                np.array(errors_list, dtype=np.float32))

    elif PYCOLMAP_API == "old":
        manager = SceneManager(colmap_path)
        manager.load_points3D()
        return (manager.points3D.astype(np.float32),
                manager.point3D_colors.astype(np.uint8),
                manager.point3D_errors.astype(np.float32))

    else:
        raise RuntimeError("pycolmap not available")


def _get_rel_paths(path_dir: str) -> List[str]:
    """Recursively get relative paths of files in a directory."""
    paths = []
    for dp, dn, fn in os.walk(path_dir):
        for f in fn:
            paths.append(os.path.relpath(os.path.join(dp, f), path_dir))
    return paths


def _detect_image_format(camera_path: str) -> Dict[str, Any]:
    """Auto-detect image format from the first camera folder."""
    if not os.path.exists(camera_path):
        raise ValueError(f"Camera path does not exist: {camera_path}")

    files = sorted([f for f in os.listdir(camera_path)
                   if f.lower().endswith(('.png', '.jpg', '.jpeg', '.webp', '.bmp', '.tif', '.tiff'))])
    if not files:
        raise ValueError(f"No image files found in {camera_path}")

    first_file = files[0]
    extension = Path(first_file).suffix
    stem = Path(first_file).stem

    frame_digits = len(stem)
    try:
        first_frame_num = int(stem)
        frame_start = first_frame_num
    except ValueError:
        frame_start = 0

    return {
        'extension': extension,
        'frame_digits': frame_digits,
        'frame_start': frame_start,
    }


def _detect_colmap_format(imdata) -> Literal["flat", "nested"]:
    """Detect if COLMAP uses flat names (cam00.png) or nested (002-002/000000.jpg)."""
    for k in imdata:
        name = imdata[k].name
        if '/' in name or '\\' in name:
            return "nested"
        else:
            return "flat"
    return "flat"


class FreeTimeParser:
    """
    COLMAP parser for FreeTime - supports full sequence without GOP chunking.

    This parser loads camera poses from a reference COLMAP and sets up
    image paths for the full video sequence.

    Handles both "flat" (cam00.png) and "nested" (002-002/000000.jpg) formats.
    """

    def __init__(
        self,
        data_dir: str,
        factor: int = 1,
        normalize: bool = False,
        test_every: int = 8,
        start_frame: int = 0,
        end_frame: int = 300,
        reference_colmap: Optional[str] = None,  # Auto-detect if None
        image_format: Optional[str] = None,
        frame_digits: Optional[int] = None,
        frame_start: Optional[int] = None,
    ):
        """
        Args:
            data_dir: Path to data directory
            factor: Image downsampling factor
            normalize: Whether to normalize world coordinates
            test_every: Use every N-th camera for testing
            start_frame: First frame index (inclusive)
            end_frame: Last frame index (exclusive)
            reference_colmap: Which COLMAP to use for camera poses (auto-detect if None)
            image_format: Image extension (auto-detected if None)
            frame_digits: Number of digits in frame names
            frame_start: Starting frame index (0 or 1)
        """
        self.data_dir = data_dir
        self.factor = factor
        self.normalize = normalize
        self.test_every = test_every
        self.start_frame = start_frame
        self.end_frame = end_frame
        self.total_frames = end_frame - start_frame

        # Find COLMAP directory for camera poses
        # Priority: reference_colmap > sparse/frame_{start_frame} > sparse/0
        colmap_dir = None
        sparse_dir = os.path.join(data_dir, "sparse")

        if reference_colmap and os.path.exists(reference_colmap):
            colmap_dir = reference_colmap
        else:
            # Priority: sparse/0 (reference COLMAP) > per-frame COLMAP
            # IMPORTANT: sparse/0 should be preferred because it has cleaner points
            # from traditional SfM. Per-frame COLMAP (from RoMa) has more outliers.
            candidates = [
                os.path.join(sparse_dir, "0"),  # Reference COLMAP (clean SfM)
                os.path.join(sparse_dir, f"frame_{start_frame:06d}"),  # Per-frame
                os.path.join(sparse_dir, "frame_000000"),
                os.path.join(data_dir, f"colmap_{start_frame}", "sparse/0"),
            ]
            for candidate in candidates:
                if os.path.exists(candidate):
                    # Check if it has COLMAP files
                    if (os.path.exists(os.path.join(candidate, "cameras.bin")) or
                        os.path.exists(os.path.join(candidate, "cameras.txt"))):
                        colmap_dir = candidate
                        break

        if colmap_dir is None:
            raise ValueError(f"No COLMAP directory found. Tried: {candidates}")

        print(f"[FreeTimeParser] Using COLMAP: {colmap_dir}")
        print(f"[FreeTimeParser] Frame range: {start_frame} to {end_frame} ({self.total_frames} frames)")
        print(f"[FreeTimeParser] Using pycolmap API: {PYCOLMAP_API}")

        # Load COLMAP data using appropriate pycolmap API
        w2c_mats = []
        camera_ids = []
        Ks_dict = dict()
        params_dict = dict()
        imsize_dict = dict()
        mask_dict = dict()
        bottom = np.array([0, 0, 0, 1]).reshape(1, 4)

        if PYCOLMAP_API == "new":
            rec = PyColmapReconstruction()
            rec.read(colmap_dir)
            imdata = rec.images
            cameras_dict = rec.cameras
        elif PYCOLMAP_API == "old":
            manager = SceneManager(colmap_dir)
            manager.load_cameras()
            manager.load_images()
            manager.load_points3D()
            imdata = manager.images
            cameras_dict = manager.cameras
        else:
            raise RuntimeError("pycolmap not available")

        for k in imdata:
            im = imdata[k]

            # Extract rotation and translation based on API
            if PYCOLMAP_API == "new":
                # New API: cam_from_world() is a method returning Rigid3d
                pose = im.cam_from_world()  # Call as method
                quat = pose.rotation.quat  # [x, y, z, w]
                x, y, z, w = quat
                rot = np.array([
                    [1 - 2*y*y - 2*z*z, 2*x*y - 2*z*w, 2*x*z + 2*y*w],
                    [2*x*y + 2*z*w, 1 - 2*x*x - 2*z*z, 2*y*z - 2*x*w],
                    [2*x*z - 2*y*w, 2*y*z + 2*x*w, 1 - 2*x*x - 2*y*y]
                ])
                trans = np.array(pose.translation).reshape(3, 1)
            else:
                # Old API: im.R() gives rotation matrix, im.tvec gives translation
                rot = im.R()
                trans = im.tvec.reshape(3, 1)

            w2c = np.concatenate([np.concatenate([rot, trans], 1), bottom], axis=0)
            w2c_mats.append(w2c)

            camera_id = im.camera_id
            camera_ids.append(camera_id)

            cam = cameras_dict[camera_id]

            # Extract intrinsics based on API
            if PYCOLMAP_API == "new":
                fx = cam.focal_length_x
                fy = cam.focal_length_y
                cx = cam.principal_point_x
                cy = cam.principal_point_y
                cam_width = cam.width
                cam_height = cam.height
                model_name = str(cam.model)
                cam_params = cam.params
            else:
                fx, fy, cx, cy = cam.fx, cam.fy, cam.cx, cam.cy
                cam_width = cam.width
                cam_height = cam.height
                model_name = str(cam.camera_type)
                cam_params = []  # Old API handles distortion separately

            K = np.array([[fx, 0, cx], [0, fy, cy], [0, 0, 1]])
            K[:2, :] /= factor
            Ks_dict[camera_id] = K

            # Distortion parameters
            if "PINHOLE" in model_name or model_name in ["0", "1"]:
                params = np.empty(0, dtype=np.float32)
                camtype = "perspective"
            elif "SIMPLE_RADIAL" in model_name or model_name == "2":
                if PYCOLMAP_API == "old":
                    params = np.array([cam.k1 if hasattr(cam, 'k1') else 0.0, 0.0, 0.0, 0.0], dtype=np.float32)
                else:
                    params = np.array([cam_params[3] if len(cam_params) > 3 else 0.0, 0.0, 0.0, 0.0], dtype=np.float32)
                camtype = "perspective"
            elif "RADIAL" in model_name or model_name == "3":
                if PYCOLMAP_API == "old":
                    k1 = cam.k1 if hasattr(cam, 'k1') else 0.0
                    k2 = cam.k2 if hasattr(cam, 'k2') else 0.0
                else:
                    k1 = cam_params[3] if len(cam_params) > 3 else 0.0
                    k2 = cam_params[4] if len(cam_params) > 4 else 0.0
                params = np.array([k1, k2, 0.0, 0.0], dtype=np.float32)
                camtype = "perspective"
            elif "OPENCV" in model_name and "FISHEYE" not in model_name or model_name == "4":
                if PYCOLMAP_API == "old":
                    k1 = cam.k1 if hasattr(cam, 'k1') else 0.0
                    k2 = cam.k2 if hasattr(cam, 'k2') else 0.0
                    p1 = cam.p1 if hasattr(cam, 'p1') else 0.0
                    p2 = cam.p2 if hasattr(cam, 'p2') else 0.0
                else:
                    k1 = cam_params[4] if len(cam_params) > 4 else 0.0
                    k2 = cam_params[5] if len(cam_params) > 5 else 0.0
                    p1 = cam_params[6] if len(cam_params) > 6 else 0.0
                    p2 = cam_params[7] if len(cam_params) > 7 else 0.0
                params = np.array([k1, k2, p1, p2], dtype=np.float32)
                camtype = "perspective"
            elif "FISHEYE" in model_name or model_name == "5":
                if PYCOLMAP_API == "old":
                    k1 = cam.k1 if hasattr(cam, 'k1') else 0.0
                    k2 = cam.k2 if hasattr(cam, 'k2') else 0.0
                    k3 = cam.k3 if hasattr(cam, 'k3') else 0.0
                    k4 = cam.k4 if hasattr(cam, 'k4') else 0.0
                else:
                    k1 = cam_params[4] if len(cam_params) > 4 else 0.0
                    k2 = cam_params[5] if len(cam_params) > 5 else 0.0
                    k3 = cam_params[6] if len(cam_params) > 6 else 0.0
                    k4 = cam_params[7] if len(cam_params) > 7 else 0.0
                params = np.array([k1, k2, k3, k4], dtype=np.float32)
                camtype = "fisheye"
            else:
                params = np.empty(0, dtype=np.float32)
                camtype = "perspective"

            params_dict[camera_id] = params
            imsize_dict[camera_id] = (cam_width // factor, cam_height // factor)
            mask_dict[camera_id] = None

        print(f"[FreeTimeParser] {len(imdata)} cameras loaded")

        w2c_mats = np.stack(w2c_mats, axis=0)
        camtoworlds = np.linalg.inv(w2c_mats)

        # Detect COLMAP format and extract camera names
        colmap_format = _detect_colmap_format(imdata)
        print(f"[FreeTimeParser] Detected COLMAP format: {colmap_format}")

        # Debug: show first few COLMAP image names
        colmap_image_names = [imdata[k].name for k in imdata]
        print(f"[FreeTimeParser] First 3 COLMAP image names: {colmap_image_names[:3]}")

        if colmap_format == "flat":
            camera_names = [Path(imdata[k].name).stem for k in imdata]
        else:
            # Nested format: 002-002/000000.jpg -> use parent folder name
            camera_names = [Path(imdata[k].name).parent.name for k in imdata]

        print(f"[FreeTimeParser] First 3 extracted camera names: {camera_names[:3]}")

        # Sort by camera name
        inds = np.argsort(camera_names)
        camera_names = [camera_names[i] for i in inds]
        camtoworlds = camtoworlds[inds]
        camera_ids = [camera_ids[i] for i in inds]

        print(f"[FreeTimeParser] First 3 sorted camera names: {camera_names[:3]}")

        # 3D points - use helper function that handles both APIs
        points, points_rgb, points_err = _load_colmap_points(colmap_dir)

        # Normalize world space
        if normalize:
            T1 = similarity_from_cameras(camtoworlds)
            camtoworlds = transform_cameras(T1, camtoworlds)
            points = transform_points(T1, points)

            T2 = align_principle_axes(points)
            camtoworlds = transform_cameras(T2, camtoworlds)
            points = transform_points(T2, points)

            transform = T2 @ T1
        else:
            transform = np.eye(4, dtype=np.float32)

        # Camera paths (folders containing images)
        images_dir = os.path.join(data_dir, "images")
        folder_names = sorted(os.listdir(images_dir))
        self.campaths = [os.path.join(images_dir, x) for x in folder_names]

        print(f"[FreeTimeParser] Found {len(self.campaths)} camera folders")

        # CRITICAL: Verify camera_names from COLMAP match folder names
        # This ensures camtoworlds[i] corresponds to campaths[i]
        if len(camera_names) != len(folder_names):
            print(f"[WARNING] Camera count mismatch! COLMAP: {len(camera_names)}, Folders: {len(folder_names)}")

        mismatches = []
        for i, (colmap_name, folder_name) in enumerate(zip(camera_names, folder_names)):
            if colmap_name != folder_name:
                mismatches.append((i, colmap_name, folder_name))

        if mismatches:
            print(f"[WARNING] Camera name mismatches detected ({len(mismatches)}):")
            for i, colmap_name, folder_name in mismatches[:5]:  # Show first 5
                print(f"  Index {i}: COLMAP='{colmap_name}' vs Folder='{folder_name}'")
            if len(mismatches) > 5:
                print(f"  ... and {len(mismatches) - 5} more")
            print("[WARNING] This may cause GT/render mismatch! Check your COLMAP reconstruction.")
        else:
            print(f"[FreeTimeParser] ✓ All {len(camera_names)} camera names match folder names")

        # Auto-detect image format from first camera folder
        if image_format is None or frame_digits is None or frame_start is None:
            detected = _detect_image_format(self.campaths[0])
            print(f"[FreeTimeParser] Auto-detected: {detected}")
            self.image_format = image_format or detected['extension']
            self.frame_digits = frame_digits if frame_digits is not None else detected['frame_digits']
            self.frame_start_offset = frame_start if frame_start is not None else detected['frame_start']
        else:
            self.image_format = f".{image_format}" if not image_format.startswith('.') else image_format
            self.frame_digits = frame_digits
            self.frame_start_offset = frame_start

        print(f"[FreeTimeParser] Image format: {self.image_format}, "
              f"{self.frame_digits} digits, start_offset={self.frame_start_offset}")

        # Store all attributes
        self.camera_names = camera_names
        self.camtoworlds = camtoworlds
        self.camera_ids = camera_ids
        self.Ks_dict = Ks_dict
        self.params_dict = params_dict
        self.imsize_dict = imsize_dict
        self.mask_dict = mask_dict
        self.points = points
        self.points_err = points_err
        self.points_rgb = points_rgb
        self.transform = transform
        self.max_camera_id = max(camera_ids) if camera_ids else 0
        self.colmap_format = colmap_format
        self.camtype = camtype

        # Undistortion maps
        self.mapx_dict = dict()
        self.mapy_dict = dict()
        self.roi_undist_dict = dict()
        self._setup_undistortion()

        # Scene scale
        camera_locations = camtoworlds[:, :3, 3]
        scene_center = np.mean(camera_locations, axis=0)
        dists = np.linalg.norm(camera_locations - scene_center, axis=1)
        self.scene_scale = np.max(dists)
        self.extent = self.scene_scale  # Alias for compatibility

        print(f"[FreeTimeParser] Scene scale: {self.scene_scale:.3f}")

    def _setup_undistortion(self):
        """Setup undistortion maps for distorted cameras."""
        for camera_id in self.params_dict.keys():
            params = self.params_dict[camera_id]
            if len(params) == 0:
                continue

            K = self.Ks_dict[camera_id]
            width, height = self.imsize_dict[camera_id]

            if self.camtype == "perspective":
                K_undist, roi_undist = cv2.getOptimalNewCameraMatrix(
                    K, params, (width, height), 0
                )
                mapx, mapy = cv2.initUndistortRectifyMap(
                    K, params, None, K_undist, (width, height), cv2.CV_32FC1
                )
            elif self.camtype == "fisheye":
                fx, fy = K[0, 0], K[1, 1]
                cx, cy = K[0, 2], K[1, 2]
                grid_x, grid_y = np.meshgrid(
                    np.arange(width, dtype=np.float32),
                    np.arange(height, dtype=np.float32),
                    indexing="xy",
                )
                x1 = (grid_x - cx) / fx
                y1 = (grid_y - cy) / fy
                theta = np.sqrt(x1**2 + y1**2)
                r = 1.0 + params[0] * theta**2 + params[1] * theta**4 + \
                    params[2] * theta**6 + params[3] * theta**8
                mapx = (fx * x1 * r + width // 2).astype(np.float32)
                mapy = (fy * y1 * r + height // 2).astype(np.float32)

                mask = np.logical_and(
                    np.logical_and(mapx > 0, mapy > 0),
                    np.logical_and(mapx < width - 1, mapy < height - 1),
                )
                y_indices, x_indices = np.nonzero(mask)
                y_min, y_max = y_indices.min(), y_indices.max() + 1
                x_min, x_max = x_indices.min(), x_indices.max() + 1
                K_undist = K.copy()
                K_undist[0, 2] -= x_min
                K_undist[1, 2] -= y_min
                roi_undist = [x_min, y_min, x_max - x_min, y_max - y_min]
            else:
                continue

            self.mapx_dict[camera_id] = mapx
            self.mapy_dict[camera_id] = mapy
            self.Ks_dict[camera_id] = K_undist
            self.roi_undist_dict[camera_id] = roi_undist
            self.imsize_dict[camera_id] = (roi_undist[2], roi_undist[3])


class FreeTimeDataset:
    """
    Dataset for FreeTime training - full sequence without GOP chunking.

    Each item is a (camera, frame) pair from the full sequence.
    Time is normalized to [0, 1] over the entire sequence.
    """

    def __init__(
        self,
        parser: FreeTimeParser,
        split: str = "train",
        patch_size: Optional[int] = None,
        load_depths: bool = False,
        test_set: List[int] = None,
        remove_set: List[int] = None,
        alpha_mode: Literal["ignore", "transparent"] = "ignore",
    ):
        """
        Args:
            parser: FreeTimeParser instance
            split: "train" or "test"
            patch_size: If set, random crop to this size
            load_depths: Whether to load depth data
            test_set: List of camera indices for testing
            remove_set: List of camera indices to exclude
            alpha_mode: "transparent" requires straight RGBA and supervises opacity.
        """
        self.parser = parser
        self.split = split
        self.patch_size = patch_size
        self.load_depths = load_depths
        if alpha_mode not in ("ignore", "transparent"):
            raise ValueError(f"Unsupported alpha mode: {alpha_mode}")
        self.alpha_mode = alpha_mode
        self.total_frames = parser.total_frames
        self.start_frame = parser.start_frame

        if test_set is None:
            test_set = [0]  # Default: first camera for testing

        num_cameras = len(parser.camera_names)

        # Build camera indices for this split
        if split == "train":
            self.camera_indices = [i for i in range(num_cameras)
                                   if i not in test_set and
                                   (remove_set is None or i not in remove_set)]
        else:
            self.camera_indices = [i for i in test_set if i < num_cameras]

        # Build all (camera, frame) pairs
        # Frame indices are relative to start_frame
        self.pairs = []
        for cam_idx in self.camera_indices:
            for frame_offset in range(self.total_frames):
                self.pairs.append((cam_idx, frame_offset))

        print(f"[FreeTimeDataset] {split}: {len(self.camera_indices)} cameras × "
              f"{self.total_frames} frames = {len(self.pairs)} samples")

    def __len__(self):
        return len(self.pairs)

    def _get_image_path(self, camera_path: str, frame_idx: int) -> str:
        """Generate image path for a given frame."""
        # frame_idx is the actual frame number (not offset)
        frame_num = frame_idx + self.parser.frame_start_offset
        frame_name = f"{frame_num:0{self.parser.frame_digits}d}{self.parser.image_format}"
        return os.path.join(camera_path, frame_name)

    def __getitem__(self, item: int) -> Dict[str, Any]:
        cam_idx, frame_offset = self.pairs[item]
        camera_path = self.parser.campaths[cam_idx]

        # Actual frame index = start_frame + offset
        frame_idx = self.start_frame + frame_offset

        # Load image
        image_path = self._get_image_path(camera_path, frame_idx)

        # Skip missing frames gracefully
        if not os.path.exists(image_path):
            if self.alpha_mode == "transparent":
                raise FileNotFoundError(f"Missing RGBA training frame: {image_path}")
            return None

        image = imageio.imread(image_path)
        if self.alpha_mode == "transparent":
            if image.ndim != 3 or image.shape[-1] != 4 or image.dtype != np.uint8:
                raise ValueError(f"Transparent training requires 8-bit straight RGBA: {image_path}")
        else:
            image = image[..., :3]
        # Transform RGB and soft alpha together to keep their pixel coordinates
        # identical through resizing, camera undistortion and random cropping.
        image = cv2.resize(
            image,
            dsize=(image.shape[1] // self.parser.factor,
                   image.shape[0] // self.parser.factor),
            interpolation=cv2.INTER_LINEAR
        )

        camera_id = self.parser.camera_ids[cam_idx]
        K = self.parser.Ks_dict[camera_id].copy()
        params = self.parser.params_dict[camera_id]
        camtoworlds = self.parser.camtoworlds[cam_idx]
        mask = self.parser.mask_dict[camera_id]

        # Apply undistortion if needed
        if len(params) > 0:
            mapx = self.parser.mapx_dict[camera_id]
            mapy = self.parser.mapy_dict[camera_id]
            image = cv2.remap(image, mapx, mapy, cv2.INTER_LINEAR)
            x, y, w, h = self.parser.roi_undist_dict[camera_id]
            image = image[y:y+h, x:x+w]

        # Random crop if specified
        if self.patch_size is not None:
            h, w = image.shape[:2]
            x = np.random.randint(0, max(w - self.patch_size, 1))
            y = np.random.randint(0, max(h - self.patch_size, 1))
            image = image[y:y+self.patch_size, x:x+self.patch_size]
            K[0, 2] -= x
            K[1, 2] -= y

        # CRITICAL: Normalize time over FULL sequence (not GOP)
        # time = frame_offset / (total_frames - 1) -> [0, 1]
        time = frame_offset / max(self.total_frames - 1, 1)

        data = {
            "K": torch.from_numpy(K).float(),
            "camtoworld": torch.from_numpy(camtoworlds).float(),
            "image": torch.from_numpy(np.ascontiguousarray(image[..., :3])).float(),
            "image_id": item,
            "time": time,  # Normalized [0, 1] over full sequence
            "frame_idx": frame_idx,  # Actual frame number
            "frame_offset": frame_offset,  # Offset from start_frame
            "camera_id": camera_id - 1,
            "camera_idx": cam_idx,
        }

        if mask is not None:
            data["mask"] = torch.from_numpy(mask).bool()

        if self.alpha_mode == "transparent":
            data["alpha"] = torch.from_numpy(np.ascontiguousarray(image[..., 3:4])).float() / 255.0

        return data


def skip_none_collate(batch):
    """Collate function that filters out None samples (missing frames)."""
    batch = [x for x in batch if x is not None]
    if len(batch) == 0:
        return None
    return torch.utils.data.dataloader.default_collate(batch)


def load_multiframe_colmap_points(data_dir: str,
                                   start_frame: int = 0,
                                   end_frame: int = 300,
                                   frame_step: int = 1,
                                   max_error: float = 2.0,
                                   match_threshold: float = 0.1,
                                   transform: Optional[np.ndarray] = None) -> Dict[str, torch.Tensor]:
    """
    Load 3D points from multiple frame-specific COLMAP reconstructions.
    Matches points across frames and computes velocities.

    Handles SPARSE COLMAP frames (not every frame needs to have SfM).

    Args:
        data_dir: Path to data directory
        start_frame: First frame index
        end_frame: Last frame index
        frame_step: Load every N-th frame (e.g., 10 means frames 0, 10, 20, ...)
        max_error: Maximum reprojection error filter
        match_threshold: Maximum distance for KNN matching
        transform: Optional [4, 4] transformation matrix to apply to points
                  (e.g., from FreeTimeParser.transform for coordinate alignment)

    Returns:
        Dictionary with positions, times, velocities, colors, has_velocity
    """
    from scipy.spatial import cKDTree

    sparse_dir = os.path.join(data_dir, "sparse")

    # Find available COLMAP frames within range
    frame_dirs = find_available_colmap_frames(sparse_dir, start_frame, end_frame)

    # Filter by frame_step (only keep frames matching start_frame + k*frame_step)
    if frame_step > 1:
        frame_dirs = [(idx, path) for idx, path in frame_dirs
                      if (idx - start_frame) % frame_step == 0]

    print(f"[MultiFrameCOLMAP] Found {len(frame_dirs)} COLMAP reconstructions "
          f"in range [{start_frame}, {end_frame}) with step={frame_step}")

    if len(frame_dirs) == 0:
        raise ValueError(f"No frame_XXXXXX directories found in {sparse_dir} "
                        f"within range [{start_frame}, {end_frame})")

    for frame_idx, path in frame_dirs[:5]:
        print(f"  Frame {frame_idx}: {path}")
    if len(frame_dirs) > 5:
        print(f"  ... and {len(frame_dirs) - 5} more")

    # Total frames for time normalization
    total_frames = end_frame - start_frame

    # Load points from each available frame
    all_positions = []
    all_times = []
    all_velocities = []
    all_colors = []
    all_has_velocity = []

    prev_points = None
    prev_colors = None
    prev_time = None
    prev_frame_idx = None

    for frame_idx, colmap_path in frame_dirs:
        try:
            # Load points using API-agnostic helper
            positions, colors, errors = _load_colmap_points(colmap_path)

            if len(positions) == 0:
                print(f"  Frame {frame_idx}: No points, skipping")
                continue

            # Filter by error
            valid = errors < max_error
            positions = positions[valid]
            colors = colors[valid]

            # Additional spatial filtering: remove extreme outliers
            # Points too far from the centroid are likely triangulation errors
            if len(positions) > 100:
                centroid = np.median(positions, axis=0)
                dists_from_centroid = np.linalg.norm(positions - centroid, axis=1)
                dist_threshold = np.percentile(dists_from_centroid, 99)  # Remove top 1%
                spatial_valid = dists_from_centroid < dist_threshold
                n_before = len(positions)
                positions = positions[spatial_valid]
                colors = colors[spatial_valid]
                if n_before - len(positions) > 0:
                    print(f"    Spatial filter: {n_before} -> {len(positions)} points")

            # Normalize time: (frame_idx - start_frame) / (end_frame - start_frame - 1)
            time = (frame_idx - start_frame) / max(total_frames - 1, 1)

            print(f"  Frame {frame_idx} (t={time:.3f}): {len(positions)} points")

            if prev_points is not None and len(positions) > 0 and len(prev_points) > 0:
                # Match with previous frame using KNN
                tree = cKDTree(positions)
                distances, indices = tree.query(prev_points, k=1)

                valid_matches = distances < match_threshold
                n_matched = valid_matches.sum()

                if n_matched > 0:
                    # Compute velocity for matched points
                    matched_prev = prev_points[valid_matches]
                    matched_curr = positions[indices[valid_matches]]

                    # FIX: Use normalized time dt but cap to reasonable range
                    # dt is already in normalized time [0,1], velocity should be in world_units/normalized_time
                    # This is correct for the motion model: µx(t) = µx + v * (t - µt)
                    dt = time - prev_time
                    dt = max(dt, 1e-6)  # Prevent division by zero

                    # Velocity in normalized time units (world_units per normalized_time_unit)
                    velocities = (matched_curr - matched_prev) / dt

                    # Cap large velocities (likely from noisy COLMAP matches)
                    # A velocity of 1.0 means moving 1 world unit over the full time range [0,1]
                    # For most scenes, velocities > 0.5 are likely noise
                    max_velocity = 0.5  # world units per normalized time
                    vel_mag = np.linalg.norm(velocities, axis=1, keepdims=True)
                    vel_scale = np.clip(max_velocity / (vel_mag + 1e-8), a_min=None, a_max=1.0)
                    n_capped = (vel_mag.squeeze() > max_velocity).sum()
                    if n_capped > 0:
                        print(f"      Capped {n_capped}/{n_matched} large velocities (>{max_velocity:.2f})")
                    velocities = velocities * vel_scale

                    all_positions.append(matched_prev)
                    all_times.append(np.full((n_matched, 1), prev_time, dtype=np.float32))
                    all_velocities.append(velocities.astype(np.float32))
                    all_colors.append(prev_colors[valid_matches])
                    all_has_velocity.append(np.ones(n_matched, dtype=bool))

                    print(f"    Matched {n_matched} points with frame {prev_frame_idx}")

                # Add unmatched points from previous frame (no velocity)
                unmatched = ~valid_matches
                n_unmatched = unmatched.sum()
                if n_unmatched > 0:
                    all_positions.append(prev_points[unmatched])
                    all_times.append(np.full((n_unmatched, 1), prev_time, dtype=np.float32))
                    all_velocities.append(np.zeros((n_unmatched, 3), dtype=np.float32))
                    all_colors.append(prev_colors[unmatched])
                    all_has_velocity.append(np.zeros(n_unmatched, dtype=bool))

            elif prev_points is not None:
                # No matching possible, add prev points without velocity
                n_prev = len(prev_points)
                all_positions.append(prev_points)
                all_times.append(np.full((n_prev, 1), prev_time, dtype=np.float32))
                all_velocities.append(np.zeros((n_prev, 3), dtype=np.float32))
                all_colors.append(prev_colors)
                all_has_velocity.append(np.zeros(n_prev, dtype=bool))

            # Update previous
            prev_points = positions
            prev_colors = colors
            prev_time = time
            prev_frame_idx = frame_idx

        except Exception as e:
            print(f"  Warning: Failed to load frame {frame_idx}: {e}")
            continue

    # Add last frame's points (no velocity)
    if prev_points is not None:
        n_last = len(prev_points)
        all_positions.append(prev_points)
        all_times.append(np.full((n_last, 1), prev_time, dtype=np.float32))
        all_velocities.append(np.zeros((n_last, 3), dtype=np.float32))
        all_colors.append(prev_colors)
        all_has_velocity.append(np.zeros(n_last, dtype=bool))

    if len(all_positions) == 0:
        raise ValueError("No points loaded from any COLMAP frame")

    # Concatenate initial data
    positions = np.concatenate(all_positions, axis=0)
    times = np.concatenate(all_times, axis=0)
    velocities = np.concatenate(all_velocities, axis=0)
    colors = np.concatenate(all_colors, axis=0)
    has_velocity = np.concatenate(all_has_velocity, axis=0)

    print(f"\n[MultiFrameCOLMAP] Initial: {len(positions)} points")
    print(f"  Time range: [{times.min():.3f}, {times.max():.3f}]")

    # =================================================================
    # TEMPORAL EXTRAPOLATION: Cover the full [0, 1] time range
    # =================================================================
    # If COLMAP only covers part of the sequence, extrapolate points
    # with velocities to create anchors at later times.
    # FreeTimeGS paper: Each anchor has position, time, velocity.
    # Position at time t: p(t) = p(t0) + v * (t - t0)
    # =================================================================
    time_max_initial = times.max()
    time_coverage_threshold = 0.9  # If we cover less than 90%, extrapolate

    if time_max_initial < time_coverage_threshold:
        print(f"\n[MultiFrameCOLMAP] Extrapolating to cover full time range...")
        print(f"  Current coverage: {time_max_initial:.3f}")

        # Strategy 1: Extrapolate points WITH velocity to later times
        vel_mask = has_velocity
        n_with_vel = vel_mask.sum()

        if n_with_vel > 0:
            vel_positions = positions[vel_mask]
            vel_times = times[vel_mask]
            vel_velocities = velocities[vel_mask]
            vel_colors = colors[vel_mask]

            # Limit extrapolation to avoid memory explosion
            # Sample a subset if too many points with velocity
            max_vel_points = 200000  # Keep extrapolation manageable
            if n_with_vel > max_vel_points:
                sample_idx = np.random.choice(n_with_vel, max_vel_points, replace=False)
                vel_positions = vel_positions[sample_idx]
                vel_times = vel_times[sample_idx]
                vel_velocities = vel_velocities[sample_idx]
                vel_colors = vel_colors[sample_idx]
                print(f"  Sampled {max_vel_points} of {n_with_vel} velocity points for extrapolation")
                n_with_vel = max_vel_points

            # Create extrapolated copies at target times
            # Target times: spread between current max and 1.0
            extrapolate_times = np.array([0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0])
            extrapolate_times = extrapolate_times[extrapolate_times > time_max_initial]

            extra_positions = []
            extra_times = []
            extra_velocities = []
            extra_colors = []
            extra_has_velocity = []

            for target_time in extrapolate_times:
                # Extrapolate: p(target) = p(t0) + v * (target - t0)
                dt = target_time - vel_times  # [N, 1]
                extrapolated = vel_positions + vel_velocities * dt

                extra_positions.append(extrapolated)
                extra_times.append(np.full_like(vel_times, target_time))
                extra_velocities.append(vel_velocities.copy())  # Keep same velocity
                extra_colors.append(vel_colors.copy())
                extra_has_velocity.append(np.ones(len(vel_positions), dtype=bool))

            if extra_positions:
                positions = np.concatenate([positions] + extra_positions, axis=0)
                times = np.concatenate([times] + extra_times, axis=0)
                velocities = np.concatenate([velocities] + extra_velocities, axis=0)
                colors = np.concatenate([colors] + extra_colors, axis=0)
                has_velocity = np.concatenate([has_velocity] + extra_has_velocity, axis=0)

                print(f"  Extrapolated {n_with_vel} points to {len(extrapolate_times)} time steps")
                print(f"  Added {len(extra_positions) * n_with_vel} extrapolated anchors")

        # Strategy 2: Also add STATIC anchors at later times
        # For points WITHOUT velocity (likely static background), duplicate at various times
        static_mask = ~has_velocity[:len(all_positions[0])] if len(all_positions) > 0 else np.zeros(0, dtype=bool)
        n_static = static_mask.sum()

        if n_static > 0:
            # Sample static points to spread across time
            static_positions = positions[:len(all_positions[0])][static_mask]
            static_colors = colors[:len(all_positions[0])][static_mask]

            # Only add a subset at later times (avoid explosion)
            n_sample = min(n_static, 5000)
            sample_idx = np.random.choice(n_static, n_sample, replace=False)

            static_sample_pos = static_positions[sample_idx]
            static_sample_colors = static_colors[sample_idx]

            # Add at mid and late times
            for target_time in [0.5, 0.75, 1.0]:
                if target_time > time_max_initial:
                    positions = np.concatenate([positions, static_sample_pos], axis=0)
                    times = np.concatenate([times, np.full((n_sample, 1), target_time, dtype=np.float32)], axis=0)
                    velocities = np.concatenate([velocities, np.zeros((n_sample, 3), dtype=np.float32)], axis=0)
                    colors = np.concatenate([colors, static_sample_colors], axis=0)
                    has_velocity = np.concatenate([has_velocity, np.zeros(n_sample, dtype=bool)], axis=0)

            print(f"  Added {n_sample} static anchor samples at later times")

        print(f"  Final time range: [{times.min():.3f}, {times.max():.3f}]")
        print(f"  Total points after extrapolation: {len(positions)}")

    # Apply optional coordinate transform (e.g., from FreeTimeParser for normalization)
    if transform is not None:
        print(f"\n[MultiFrameCOLMAP] Applying coordinate transform...")
        transform_t = torch.from_numpy(transform).float()

        # Transform positions: p' = T @ [p; 1]
        positions_t = torch.from_numpy(positions).float()
        ones = torch.ones(len(positions_t), 1)
        positions_h = torch.cat([positions_t, ones], dim=1)  # [N, 4]
        positions_transformed = (transform_t @ positions_h.T).T[:, :3]  # [N, 3]

        # Transform velocities: v' = R @ v (rotation only, no translation)
        R = transform_t[:3, :3]  # [3, 3]
        velocities_t = torch.from_numpy(velocities).float()
        velocities_transformed = velocities_t @ R.T  # [N, 3]

        result = {
            'positions': positions_transformed,
            'times': torch.from_numpy(times).float(),
            'velocities': velocities_transformed,
            'colors': torch.from_numpy(colors).float() / 255.0,
            'has_velocity': torch.from_numpy(has_velocity).bool(),
        }
    else:
        result = {
            'positions': torch.from_numpy(positions).float(),
            'times': torch.from_numpy(times).float(),
            'velocities': torch.from_numpy(velocities).float(),
            'colors': torch.from_numpy(colors).float() / 255.0,
            'has_velocity': torch.from_numpy(has_velocity).bool(),
        }

    print(f"\n[MultiFrameCOLMAP] Final: {len(result['positions'])} points")
    print(f"  With velocity: {result['has_velocity'].sum().item()}")
    print(f"  Time range: [{result['times'].min():.3f}, {result['times'].max():.3f}]")

    vel_mags = result['velocities'].norm(dim=1)
    has_vel = result['has_velocity']
    if has_vel.any():
        print(f"  Velocity magnitude (matched): "
              f"min={vel_mags[has_vel].min():.4f}, "
              f"max={vel_mags[has_vel].max():.4f}, "
              f"mean={vel_mags[has_vel].mean():.4f}")

    return result


def load_multiframe_colmap_grid_tracked(
    data_dir: str,
    start_frame: int = 0,
    end_frame: int = 50,
    frame_step: int = 5,
    grid_divisions: Tuple[int, int, int] = (10, 10, 4),  # Finer grid for better locality
    max_points_per_cell: int = 2000,
    match_threshold: float = 0.1,
    max_error: float = 2.0,
    transform: Optional[np.ndarray] = None
) -> Dict[str, torch.Tensor]:
    """
    Load 3D points with grid-based stratified sampling and temporal tracking.

    This approach ensures:
    1. Full scene coverage - every grid cell is covered by some time step
    2. Correspondences preserved - we track points, not randomly match
    3. Better velocity - central difference from window of 3 frames

    Algorithm:
    1. Load all frames to get scene bounds and all points
    2. Create 3D grid over scene bounds
    3. Assign grid cells to time steps (round-robin)
    4. For each time step t_i with assigned cells:
       - Select points in those cells
       - Track to t_{i-1} and t_{i+1} using KNN
       - Compute velocity: v = (pos_{t+1} - pos_{t-1}) / (2*dt)
    5. Combine all tracked points

    Args:
        data_dir: Path to data directory
        start_frame: First frame index
        end_frame: Last frame index (exclusive)
        frame_step: Step between frames
        grid_divisions: (nx, ny, nz) number of grid cells in each dimension
        max_points_per_cell: Max points to keep per grid cell
        match_threshold: KNN matching threshold
        max_error: Max reprojection error filter
        transform: Optional coordinate transform

    Returns:
        Dictionary with positions, times, velocities, colors, has_velocity
    """
    from scipy.spatial import cKDTree

    sparse_dir = os.path.join(data_dir, "sparse")

    # Find available COLMAP frames
    frame_dirs = find_available_colmap_frames(sparse_dir, start_frame, end_frame)

    # Filter by frame_step
    if frame_step > 1:
        frame_dirs = [(idx, path) for idx, path in frame_dirs
                      if (idx - start_frame) % frame_step == 0]

    n_frames = len(frame_dirs)
    print(f"[GridTracked] Found {n_frames} frames in range [{start_frame}, {end_frame}) step={frame_step}")

    if n_frames < 2:
        raise ValueError(f"Need at least 2 frames for tracking, found {n_frames}")

    total_frames = end_frame - start_frame

    # Step 1: Load all frames to get scene bounds and point clouds
    print("[GridTracked] Loading all frames...")
    frame_data = {}  # frame_idx -> {positions, colors, time}
    all_points_for_bounds = []

    for frame_idx, colmap_path in frame_dirs:
        try:
            positions, colors, errors = _load_colmap_points(colmap_path)
            if len(positions) == 0:
                continue

            # Filter by error
            valid = errors < max_error
            positions = positions[valid]
            colors = colors[valid]

            # Spatial filter: remove extreme outliers
            if len(positions) > 100:
                centroid = np.median(positions, axis=0)
                dists = np.linalg.norm(positions - centroid, axis=1)
                thresh = np.percentile(dists, 99)
                spatial_valid = dists < thresh
                positions = positions[spatial_valid]
                colors = colors[spatial_valid]

            time = (frame_idx - start_frame) / max(total_frames - 1, 1)

            frame_data[frame_idx] = {
                'positions': positions,
                'colors': colors,
                'time': time
            }
            all_points_for_bounds.append(positions)

            print(f"  Frame {frame_idx} (t={time:.3f}): {len(positions)} points")

        except Exception as e:
            print(f"  Warning: Failed to load frame {frame_idx}: {e}")
            continue

    if len(frame_data) < 2:
        raise ValueError("Need at least 2 valid frames for tracking")

    # Step 2: Compute scene bounds from all points
    all_points = np.concatenate(all_points_for_bounds, axis=0)
    scene_min = np.percentile(all_points, 1, axis=0)  # Robust min
    scene_max = np.percentile(all_points, 99, axis=0)  # Robust max
    scene_extent = scene_max - scene_min

    print(f"\n[GridTracked] Scene bounds:")
    print(f"  Min: {scene_min}")
    print(f"  Max: {scene_max}")
    print(f"  Extent: {scene_extent}")

    # Step 3: Create grid with helper functions
    nx, ny, nz = grid_divisions
    n_cells = nx * ny * nz

    def get_cell_coords(pos):
        """Get (ix, iy, iz) grid coordinates for positions.

        NOTE: This normalizes positions ONLY for cell index assignment.
        The actual positions used for velocity calculation remain in world coordinates.
        """
        norm_pos = (pos - scene_min) / (scene_extent + 1e-8)
        norm_pos = np.clip(norm_pos, 0, 0.999)
        ix = (norm_pos[:, 0] * nx).astype(int)
        iy = (norm_pos[:, 1] * ny).astype(int)
        iz = (norm_pos[:, 2] * nz).astype(int)
        return ix, iy, iz

    def coords_to_idx(ix, iy, iz):
        """Convert grid coordinates to flat cell index."""
        return ix + iy * nx + iz * nx * ny

    def get_cell_idx(pos):
        """Get flat grid cell index for positions."""
        ix, iy, iz = get_cell_coords(pos)
        return coords_to_idx(ix, iy, iz)

    def get_neighbor_cells(ix, iy, iz, neighbor_radius=1):
        """Get all neighboring cell indices including self."""
        neighbors = []
        for dix in range(-neighbor_radius, neighbor_radius + 1):
            for diy in range(-neighbor_radius, neighbor_radius + 1):
                for diz in range(-neighbor_radius, neighbor_radius + 1):
                    nix = ix + dix
                    niy = iy + diy
                    niz = iz + diz
                    # Check bounds
                    if 0 <= nix < nx and 0 <= niy < ny and 0 <= niz < nz:
                        neighbors.append(coords_to_idx(nix, niy, niz))
        return neighbors

    # Step 4: Pre-compute cell indices and group points by cell for each frame
    print("\n[GridTracked] Building spatial index for each frame...")
    frame_cell_data = {}  # frame_idx -> {cell_idx -> {'positions': [...], 'indices': [...]}}

    for frame_idx, data in frame_data.items():
        positions = data['positions']
        cell_indices = get_cell_idx(positions)

        # Group by cell
        cell_groups = {}
        for i, cell_idx in enumerate(cell_indices):
            if cell_idx not in cell_groups:
                cell_groups[cell_idx] = {'positions': [], 'point_indices': []}
            cell_groups[cell_idx]['positions'].append(positions[i])
            cell_groups[cell_idx]['point_indices'].append(i)

        # Convert to numpy arrays for efficiency
        for cell_idx in cell_groups:
            cell_groups[cell_idx]['positions'] = np.array(cell_groups[cell_idx]['positions'])
            cell_groups[cell_idx]['point_indices'] = np.array(cell_groups[cell_idx]['point_indices'])

        frame_cell_data[frame_idx] = cell_groups

    # Step 5: Assign grid cells to time steps (round-robin)
    sorted_frame_indices = sorted(frame_data.keys())
    n_time_steps = len(sorted_frame_indices)

    cell_to_time = {}
    for cell_idx in range(n_cells):
        time_idx = cell_idx % n_time_steps
        cell_to_time[cell_idx] = sorted_frame_indices[time_idx]

    print(f"[GridTracked] Assigned {n_cells} cells to {n_time_steps} time steps")

    # Step 6: Process each time step with neighbor-based tracking
    all_positions = []
    all_times = []
    all_velocities = []
    all_colors = []
    all_has_velocity = []

    for t_idx, frame_idx in enumerate(sorted_frame_indices):
        data = frame_data[frame_idx]
        positions = data['positions']
        colors = data['colors']
        time = data['time']

        # Get cells assigned to this time step
        assigned_cells = [c for c, f in cell_to_time.items() if f == frame_idx]
        if len(assigned_cells) == 0:
            continue

        # Get points in assigned cells
        cell_indices = get_cell_idx(positions)
        in_assigned = np.isin(cell_indices, assigned_cells)

        selected_positions = positions[in_assigned]
        selected_colors = colors[in_assigned]
        selected_cell_indices = cell_indices[in_assigned]

        if len(selected_positions) == 0:
            continue

        # Subsample if too many points
        max_pts = max_points_per_cell * len(assigned_cells)
        if len(selected_positions) > max_pts:
            idx = np.random.choice(len(selected_positions), max_pts, replace=False)
            selected_positions = selected_positions[idx]
            selected_colors = selected_colors[idx]
            selected_cell_indices = selected_cell_indices[idx]

        n_selected = len(selected_positions)

        # Get previous and next frames
        prev_frame_idx = sorted_frame_indices[t_idx - 1] if t_idx > 0 else None
        next_frame_idx = sorted_frame_indices[t_idx + 1] if t_idx < n_time_steps - 1 else None

        # Initialize velocity array
        velocities = np.zeros((n_selected, 3), dtype=np.float32)
        has_velocity = np.zeros(n_selected, dtype=bool)

        # FAST TRACKING using KDTree per cell's neighbor region
        valid_prev = np.zeros(n_selected, dtype=bool)
        vel_backward = np.zeros((n_selected, 3), dtype=np.float32)
        valid_next = np.zeros(n_selected, dtype=bool)
        vel_forward = np.zeros((n_selected, 3), dtype=np.float32)

        # Get cell indices for selected points
        sel_cell_idx = get_cell_idx(selected_positions)
        sel_ix, sel_iy, sel_iz = get_cell_coords(selected_positions)

        # Group selected points by their cell for batch processing
        unique_cells = np.unique(sel_cell_idx)

        for cell_idx in unique_cells:
            cell_mask = sel_cell_idx == cell_idx
            cell_points = selected_positions[cell_mask]
            cell_indices = np.where(cell_mask)[0]

            if len(cell_points) == 0:
                continue

            # Get neighbor cells (use first point's coords)
            first_pt_idx = cell_indices[0]
            neighbors = get_neighbor_cells(sel_ix[first_pt_idx], sel_iy[first_pt_idx], sel_iz[first_pt_idx])

            # Track to PREVIOUS frame using KDTree (FAST)
            if prev_frame_idx is not None and prev_frame_idx in frame_cell_data:
                prev_cells = frame_cell_data[prev_frame_idx]
                prev_time = frame_data[prev_frame_idx]['time']
                dt_prev = time - prev_time

                candidate_pos_list = [prev_cells[nc]['positions'] for nc in neighbors if nc in prev_cells]
                if candidate_pos_list:
                    candidate_pos = np.concatenate(candidate_pos_list, axis=0)
                    tree = cKDTree(candidate_pos)
                    dists, indices = tree.query(cell_points, k=1)

                    valid_mask = dists < match_threshold
                    matched_prev_pos = candidate_pos[indices]

                    valid_indices = cell_indices[valid_mask]
                    valid_prev[valid_indices] = True
                    vel_backward[valid_indices] = (cell_points[valid_mask] - matched_prev_pos[valid_mask]) / max(dt_prev, 1e-6)

            # Track to NEXT frame using KDTree (FAST)
            if next_frame_idx is not None and next_frame_idx in frame_cell_data:
                next_cells = frame_cell_data[next_frame_idx]
                next_time = frame_data[next_frame_idx]['time']
                dt_next = next_time - time

                candidate_pos_list = [next_cells[nc]['positions'] for nc in neighbors if nc in next_cells]
                if candidate_pos_list:
                    candidate_pos = np.concatenate(candidate_pos_list, axis=0)
                    tree = cKDTree(candidate_pos)
                    dists, indices = tree.query(cell_points, k=1)

                    valid_mask = dists < match_threshold
                    matched_next_pos = candidate_pos[indices]

                    valid_indices = cell_indices[valid_mask]
                    valid_next[valid_indices] = True
                    vel_forward[valid_indices] = (matched_next_pos[valid_mask] - cell_points[valid_mask]) / max(dt_next, 1e-6)

        # Compute final velocity using central difference where possible
        # v = weighted_avg(vel_backward, vel_forward) if both available
        both_valid = valid_prev & valid_next
        only_prev = valid_prev & ~valid_next
        only_next = valid_next & ~valid_prev

        if both_valid.any():
            # Central difference: weighted average of forward and backward
            prev_dt = time - frame_data[prev_frame_idx]['time'] if prev_frame_idx else 0
            next_dt = frame_data[next_frame_idx]['time'] - time if next_frame_idx else 0
            total_dt = prev_dt + next_dt + 1e-8

            w_fwd = prev_dt / total_dt
            w_bwd = next_dt / total_dt

            velocities[both_valid] = w_bwd * vel_backward[both_valid] + w_fwd * vel_forward[both_valid]
            has_velocity[both_valid] = True

        if only_prev.any():
            velocities[only_prev] = vel_backward[only_prev]
            has_velocity[only_prev] = True

        if only_next.any():
            velocities[only_next] = vel_forward[only_next]
            has_velocity[only_next] = True

        # Cap large velocities (likely from noisy matches)
        max_velocity = 0.5  # world units per normalized time
        vel_mag = np.linalg.norm(velocities, axis=1, keepdims=True)
        large_vel_mask = (vel_mag.squeeze() > max_velocity) & has_velocity
        n_capped = large_vel_mask.sum()
        if n_capped > 0:
            vel_scale = np.clip(max_velocity / (vel_mag + 1e-8), a_min=None, a_max=1.0)
            velocities = velocities * vel_scale
            print(f"    Capped {n_capped} large velocities (>{max_velocity:.2f})")

        # Add to results
        all_positions.append(selected_positions)
        all_times.append(np.full((n_selected, 1), time, dtype=np.float32))
        all_velocities.append(velocities)
        all_colors.append(selected_colors)
        all_has_velocity.append(has_velocity)

        n_with_vel = has_velocity.sum()
        print(f"  Time {time:.3f} (frame {frame_idx}): {n_selected} points, "
              f"{n_with_vel} with velocity ({100*n_with_vel/n_selected:.1f}%)")

    if len(all_positions) == 0:
        raise ValueError("No points collected from grid-based tracking")

    # Concatenate results
    positions = np.concatenate(all_positions, axis=0)
    times = np.concatenate(all_times, axis=0)
    velocities = np.concatenate(all_velocities, axis=0)
    colors = np.concatenate(all_colors, axis=0)
    has_velocity = np.concatenate(all_has_velocity, axis=0)

    print(f"\n[GridTracked] Total: {len(positions)} points")
    print(f"  With velocity: {has_velocity.sum()} ({100*has_velocity.mean():.1f}%)")

    # Apply coordinate transform
    if transform is not None:
        print(f"[GridTracked] Applying coordinate transform...")
        transform_t = torch.from_numpy(transform).float()

        positions_t = torch.from_numpy(positions).float()
        ones = torch.ones(len(positions_t), 1)
        positions_h = torch.cat([positions_t, ones], dim=1)
        positions_transformed = (transform_t @ positions_h.T).T[:, :3]

        R = transform_t[:3, :3]
        velocities_t = torch.from_numpy(velocities).float()
        velocities_transformed = velocities_t @ R.T

        result = {
            'positions': positions_transformed,
            'times': torch.from_numpy(times).float(),
            'velocities': velocities_transformed,
            'colors': torch.from_numpy(colors).float() / 255.0,
            'has_velocity': torch.from_numpy(has_velocity).bool(),
        }
    else:
        result = {
            'positions': torch.from_numpy(positions).float(),
            'times': torch.from_numpy(times).float(),
            'velocities': torch.from_numpy(velocities).float(),
            'colors': torch.from_numpy(colors).float() / 255.0,
            'has_velocity': torch.from_numpy(has_velocity).bool(),
        }

    print(f"\n[GridTracked] Final: {len(result['positions'])} points")
    print(f"  Time range: [{result['times'].min():.3f}, {result['times'].max():.3f}]")

    vel_mags = result['velocities'].norm(dim=1)
    has_vel = result['has_velocity']
    if has_vel.any():
        print(f"  Velocity magnitude: min={vel_mags[has_vel].min():.4f}, "
              f"max={vel_mags[has_vel].max():.4f}, mean={vel_mags[has_vel].mean():.4f}")

    return result


def load_single_frame_with_velocity(data_dir: str,
                                     start_frame: int = 0,
                                     end_frame: int = 300,
                                     reference_time: float = 0.5,
                                     max_error: float = 2.0,
                                     match_threshold: float = 0.1,
                                     transform: Optional[np.ndarray] = None) -> Dict[str, torch.Tensor]:
    """
    Load 3D points from a SINGLE reference frame and compute velocity by matching
    with the next frame. This follows the paper's approach more closely:

    "For each video frame, we first use ROMA to obtain 2D matches...
     These 3D points and the corresponding time step are used to initialize
     the position and time of Gaussian primitives. Subsequently, 3D points
     of two video frames are matched by k-nearest neighbor algorithm, and
     the translation between the point pairs are taken as the velocity."

    Key insight: ONE set of Gaussians moves through time via µx(t) = µx + v*(t-µt)

    IMPORTANT: reference_time should be ~0.5 (middle of sequence) so Gaussians
    can move both forward and backward in time using velocity.
    With µt=0.5 and velocity v:
      - At t=0: position = µx + v*(0 - 0.5) = µx - 0.5v
      - At t=1: position = µx + v*(1 - 0.5) = µx + 0.5v

    Args:
        data_dir: Path to data directory
        start_frame: First frame index (used as reference)
        end_frame: Last frame index
        reference_time: Time value for the reference frame (default 0.5 = middle)
        max_error: Maximum reprojection error filter
        match_threshold: Maximum distance for KNN matching
        transform: Optional [4, 4] transformation matrix to apply to points
                  (e.g., from FreeTimeParser.transform for coordinate alignment)

    Returns:
        Dictionary with positions, times, velocities, colors, has_velocity
    """
    from scipy.spatial import cKDTree

    sparse_dir = os.path.join(data_dir, "sparse")

    # Find first two available COLMAP frames
    frame_dirs = find_available_colmap_frames(sparse_dir, start_frame, end_frame)

    print(f"[SingleFrameInit] Found {len(frame_dirs)} COLMAP reconstructions")

    if len(frame_dirs) < 2:
        raise ValueError(f"Need at least 2 COLMAP frames for velocity estimation, "
                        f"found {len(frame_dirs)}")

    # Use first frame as reference, second for velocity computation
    ref_frame_idx, ref_colmap_path = frame_dirs[0]
    next_frame_idx, next_colmap_path = frame_dirs[1]

    print(f"  Reference frame: {ref_frame_idx}")
    print(f"  Next frame for velocity: {next_frame_idx}")

    # Load reference frame points using API-agnostic helper
    ref_positions, ref_colors, ref_errors = _load_colmap_points(ref_colmap_path)

    # Filter by error
    valid = ref_errors < max_error
    ref_positions = ref_positions[valid]
    ref_colors = ref_colors[valid]

    print(f"  Reference frame points (after error filter): {len(ref_positions)}")

    # Load next frame points for velocity matching
    next_positions, _, next_errors = _load_colmap_points(next_colmap_path)

    valid_next = next_errors < max_error
    next_positions = next_positions[valid_next]

    print(f"  Next frame points (after error filter): {len(next_positions)}")

    # Compute time difference in normalized time units
    total_frames = end_frame - start_frame
    dt = (next_frame_idx - ref_frame_idx) / max(total_frames - 1, 1)
    dt = max(dt, 1e-6)  # Prevent division by zero

    # Match reference points to next frame using KNN
    N = len(ref_positions)
    velocities = np.zeros((N, 3), dtype=np.float32)
    has_velocity = np.zeros(N, dtype=bool)

    if len(next_positions) > 0:
        tree = cKDTree(next_positions)
        distances, indices = tree.query(ref_positions, k=1)

        valid_matches = distances < match_threshold
        n_matched = valid_matches.sum()

        if n_matched > 0:
            matched_ref = ref_positions[valid_matches]
            matched_next = next_positions[indices[valid_matches]]

            # Velocity = (position_next - position_ref) / dt
            matched_velocities = (matched_next - matched_ref) / dt
            velocities[valid_matches] = matched_velocities
            has_velocity[valid_matches] = True

            print(f"  Matched {n_matched}/{N} points ({100*n_matched/N:.1f}%)")

    # All points have the same reference time
    times = np.full((N, 1), reference_time, dtype=np.float32)

    # Apply optional coordinate transform
    if transform is not None:
        print(f"\n[SingleFrameInit] Applying coordinate transform...")
        transform_t = torch.from_numpy(transform).float()

        # Transform positions: p' = T @ [p; 1]
        positions_t = torch.from_numpy(ref_positions).float()
        ones = torch.ones(len(positions_t), 1)
        positions_h = torch.cat([positions_t, ones], dim=1)  # [N, 4]
        positions_transformed = (transform_t @ positions_h.T).T[:, :3]  # [N, 3]

        # Transform velocities: v' = R @ v (rotation only)
        R = transform_t[:3, :3]  # [3, 3]
        velocities_t = torch.from_numpy(velocities).float()
        velocities_transformed = velocities_t @ R.T  # [N, 3]

        result = {
            'positions': positions_transformed,
            'times': torch.from_numpy(times).float(),
            'velocities': velocities_transformed,
            'colors': torch.from_numpy(ref_colors).float() / 255.0,
            'has_velocity': torch.from_numpy(has_velocity).bool(),
        }
    else:
        result = {
            'positions': torch.from_numpy(ref_positions).float(),
            'times': torch.from_numpy(times).float(),
            'velocities': torch.from_numpy(velocities).float(),
            'colors': torch.from_numpy(ref_colors).float() / 255.0,
            'has_velocity': torch.from_numpy(has_velocity).bool(),
        }

    print(f"\n[SingleFrameInit] Final: {N} points")
    print(f"  With velocity: {result['has_velocity'].sum().item()} ({100*result['has_velocity'].sum().item()/N:.1f}%)")
    print(f"  Reference time: {reference_time}")

    vel_mags = result['velocities'].norm(dim=1)
    has_vel = result['has_velocity']
    if has_vel.any():
        print(f"  Velocity magnitude: "
              f"min={vel_mags[has_vel].min():.4f}, "
              f"max={vel_mags[has_vel].max():.4f}, "
              f"mean={vel_mags[has_vel].mean():.4f}")

    return result


def load_startframe_tracked_velocity(data_dir: str,
                                      start_frame: int = 0,
                                      end_frame: int = 300,
                                      frame_step: int = 10,
                                      max_error: float = 2.0,
                                      match_threshold: Optional[float] = None,
                                      transform: Optional[np.ndarray] = None) -> Dict[str, torch.Tensor]:
    """
    Load 3D points from START FRAME only and track them through subsequent frames
    to compute velocity. This follows a pure tracking approach:

    1. Load RoMa triangulated points from frame 0 (start frame)
    2. Track those SAME points through frames 10, 20, 30, 40, 50 using KNN
    3. Compute velocity as linear fit (displacement / time) across all observations
    4. All points initialized at t=0 with their computed velocity

    Key difference from multiframe approach:
    - multiframe: Each frame contributes its own points at different times
    - This approach: Only start frame points, tracked to compute velocity, all at t=0

    Motion model: position(t) = position_0 + velocity * t

    Args:
        data_dir: Path to data directory
        start_frame: First frame index (source of all points)
        end_frame: Last frame index
        frame_step: Frame step for tracking (e.g., 10 means track through 0, 10, 20, ...)
        max_error: Maximum reprojection error filter
        match_threshold: Maximum distance for KNN matching. If None, computed adaptively
                        based on scene scale and frame_step.
        transform: Optional [4, 4] transformation matrix

    Returns:
        Dictionary with positions (at t=0), times (all 0), velocities, colors, has_velocity
    """
    from scipy.spatial import cKDTree

    sparse_dir = os.path.join(data_dir, "sparse")

    # Find available COLMAP frames within range
    frame_dirs = find_available_colmap_frames(sparse_dir, start_frame, end_frame)

    # Filter by frame_step
    if frame_step > 1:
        frame_dirs = [(idx, path) for idx, path in frame_dirs
                      if (idx - start_frame) % frame_step == 0]

    print(f"[StartFrameTracked] Found {len(frame_dirs)} COLMAP frames with step={frame_step}")

    if len(frame_dirs) < 2:
        raise ValueError(f"Need at least 2 COLMAP frames, found {len(frame_dirs)}")

    for frame_idx, path in frame_dirs:
        print(f"  Frame {frame_idx}: {path}")

    # Load START frame points (this is the source of all our Gaussians)
    start_frame_idx, start_colmap_path = frame_dirs[0]
    start_positions, start_colors, start_errors = _load_colmap_points(start_colmap_path)

    # Filter by error
    valid = start_errors < max_error
    start_positions = start_positions[valid]
    start_colors = start_colors[valid]

    # Additional spatial filtering: remove extreme outliers
    if len(start_positions) > 100:
        centroid = np.median(start_positions, axis=0)
        dists_from_centroid = np.linalg.norm(start_positions - centroid, axis=1)
        dist_threshold = np.percentile(dists_from_centroid, 99)  # Remove top 1%
        spatial_valid = dists_from_centroid < dist_threshold
        n_before = len(start_positions)
        start_positions = start_positions[spatial_valid]
        start_colors = start_colors[spatial_valid]
        print(f"[StartFrameTracked] Spatial filter: {n_before} -> {len(start_positions)} points")

    N = len(start_positions)
    print(f"\n[StartFrameTracked] Start frame {start_frame_idx}: {N} points")

    # Compute adaptive match_threshold if not provided
    if match_threshold is None:
        # Estimate scene scale from point cloud using k-nearest neighbor distances
        sample_size = min(1000, N)
        sample_idx = np.random.choice(N, sample_size, replace=False)
        sample_pts = start_positions[sample_idx]

        tree_sample = cKDTree(sample_pts)
        # Get 5th nearest neighbor distance as robust scale estimate
        dists, _ = tree_sample.query(sample_pts, k=6)  # k=6 because first is self
        median_nn_dist = np.median(dists[:, 5])  # 5th NN distance

        # Scale threshold by frame_step: larger steps mean more potential movement
        # Base threshold: 10x median NN distance, scaled by sqrt(frame_step)
        match_threshold = median_nn_dist * 10.0 * np.sqrt(frame_step)
        print(f"[StartFrameTracked] Adaptive match_threshold: {match_threshold:.4f} "
              f"(median_nn_dist={median_nn_dist:.4f}, frame_step={frame_step})")
    else:
        print(f"[StartFrameTracked] Using provided match_threshold: {match_threshold:.4f}")

    # Total frames for time normalization
    total_frames = end_frame - start_frame

    # Initialize tracking arrays
    # For each point, store positions at each tracked time
    # positions_over_time[i] = list of (time, position) tuples for point i
    positions_over_time = [[(0.0, start_positions[i].copy())] for i in range(N)]

    # Track points through subsequent frames
    current_positions = start_positions.copy()
    current_valid = np.ones(N, dtype=bool)  # Which points are still being tracked

    for frame_idx, colmap_path in frame_dirs[1:]:
        # Load this frame's points
        frame_positions, _, frame_errors = _load_colmap_points(colmap_path)

        valid_frame = frame_errors < max_error
        frame_positions = frame_positions[valid_frame]

        if len(frame_positions) == 0:
            print(f"  Frame {frame_idx}: No points, stopping tracking")
            break

        # Normalized time for this frame
        time = (frame_idx - start_frame) / max(total_frames - 1, 1)

        # Build KD-tree for this frame's points
        tree = cKDTree(frame_positions)

        # Find matches for currently tracked points
        valid_indices = np.where(current_valid)[0]
        if len(valid_indices) == 0:
            print(f"  Frame {frame_idx}: No points left to track")
            break

        distances, indices = tree.query(current_positions[valid_indices], k=1)

        n_matched = 0
        for local_idx, global_idx in enumerate(valid_indices):
            if distances[local_idx] < match_threshold:
                # Successfully tracked this point
                matched_pos = frame_positions[indices[local_idx]]
                positions_over_time[global_idx].append((time, matched_pos.copy()))
                current_positions[global_idx] = matched_pos
                n_matched += 1
            else:
                # Lost track of this point
                current_valid[global_idx] = False

        print(f"  Frame {frame_idx} (t={time:.3f}): Tracked {n_matched}/{len(valid_indices)} points")

    # Compute velocity for each point using linear regression over tracked positions
    velocities = np.zeros((N, 3), dtype=np.float32)
    has_velocity = np.zeros(N, dtype=bool)
    n_observations = np.zeros(N, dtype=int)

    for i in range(N):
        observations = positions_over_time[i]
        n_obs = len(observations)
        n_observations[i] = n_obs

        if n_obs < 2:
            # Not enough observations for velocity
            continue

        # Extract times and positions
        times_obs = np.array([obs[0] for obs in observations])
        positions_obs = np.array([obs[1] for obs in observations])

        # Linear regression: position = position_0 + velocity * time
        # Using least squares: velocity = sum((t - t_mean)(p - p_mean)) / sum((t - t_mean)^2)
        t_mean = times_obs.mean()
        p_mean = positions_obs.mean(axis=0)

        t_diff = times_obs - t_mean
        p_diff = positions_obs - p_mean

        denom = (t_diff ** 2).sum()
        if denom > 1e-10:
            # velocity = sum(t_diff * p_diff) / sum(t_diff^2)
            velocity = (t_diff[:, np.newaxis] * p_diff).sum(axis=0) / denom
            velocities[i] = velocity
            has_velocity[i] = True

    # All points at t=0
    times = np.zeros((N, 1), dtype=np.float32)

    print(f"\n[StartFrameTracked] Velocity computation:")
    print(f"  Total points: {N}")
    print(f"  Points with velocity (>=2 observations): {has_velocity.sum()}")
    print(f"  Observation counts: min={n_observations.min()}, max={n_observations.max()}, mean={n_observations.mean():.1f}")

    # Apply optional coordinate transform
    if transform is not None:
        print(f"\n[StartFrameTracked] Applying coordinate transform...")
        transform_t = torch.from_numpy(transform).float()

        # Transform positions: p' = T @ [p; 1]
        positions_t = torch.from_numpy(start_positions).float()
        ones = torch.ones(len(positions_t), 1)
        positions_h = torch.cat([positions_t, ones], dim=1)
        positions_transformed = (transform_t @ positions_h.T).T[:, :3]

        # Transform velocities: v' = R @ v (rotation only)
        R = transform_t[:3, :3]
        velocities_t = torch.from_numpy(velocities).float()
        velocities_transformed = velocities_t @ R.T

        result = {
            'positions': positions_transformed,
            'times': torch.from_numpy(times).float(),
            'velocities': velocities_transformed,
            'colors': torch.from_numpy(start_colors).float() / 255.0,
            'has_velocity': torch.from_numpy(has_velocity).bool(),
        }
    else:
        result = {
            'positions': torch.from_numpy(start_positions).float(),
            'times': torch.from_numpy(times).float(),
            'velocities': torch.from_numpy(velocities).float(),
            'colors': torch.from_numpy(start_colors).float() / 255.0,
            'has_velocity': torch.from_numpy(has_velocity).bool(),
        }

    print(f"\n[StartFrameTracked] Final: {N} points at t=0")
    print(f"  With velocity: {result['has_velocity'].sum().item()} ({100*result['has_velocity'].sum().item()/N:.1f}%)")

    vel_mags = result['velocities'].norm(dim=1)
    has_vel = result['has_velocity']
    if has_vel.any():
        print(f"  Velocity magnitude: "
              f"min={vel_mags[has_vel].min():.4f}, "
              f"max={vel_mags[has_vel].max():.4f}, "
              f"mean={vel_mags[has_vel].mean():.4f}")

    return result


def load_roma_points(
    data_dir: str,
    start_frame: int = 0,
    end_frame: int = 300,
    frame_step: int = 1,
    max_velocity: float = 0.5,
    transform: Optional[np.ndarray] = None,
) -> Dict[str, torch.Tensor]:
    """
    Load Roma triangulated points from per-frame npy files.

    Files expected:
    - points3d_frame{frame:06d}.npy - [N, 3] 3D points
    - colors_frame{frame:06d}.npy - [N, 3] RGB colors (0-255)

    Args:
        data_dir: Path to data directory containing npy files
        start_frame: First frame index
        end_frame: Last frame index
        frame_step: Step between frames
        max_velocity: Cap velocities at this value
        transform: Optional 4x4 transform matrix

    Returns:
        Dictionary with positions, times, velocities, colors, has_velocity
    """
    from scipy.spatial import cKDTree

    print(f"\n[RomaPoints] Loading Roma triangulated points...")
    print(f"  Data dir: {data_dir}")
    print(f"  Frame range: [{start_frame}, {end_frame}) step={frame_step}")

    # Find available Roma point files
    roma_frames = []
    for frame_idx in range(start_frame, end_frame, frame_step):
        points_file = os.path.join(data_dir, f"points3d_frame{frame_idx:06d}.npy")
        colors_file = os.path.join(data_dir, f"colors_frame{frame_idx:06d}.npy")
        if os.path.exists(points_file) and os.path.exists(colors_file):
            roma_frames.append(frame_idx)

    if len(roma_frames) == 0:
        raise ValueError(f"No Roma point files found in {data_dir}")

    print(f"  Found {len(roma_frames)} Roma frames")

    total_frames = end_frame - start_frame

    all_positions = []
    all_times = []
    all_velocities = []
    all_colors = []
    all_has_velocity = []

    prev_points = None
    prev_time = None
    prev_frame_idx = None

    for i, frame_idx in enumerate(tqdm(roma_frames, desc="Loading Roma frames")):
        # Normalized time
        time = (frame_idx - start_frame) / max(total_frames - 1, 1)

        # Load points and colors
        points_file = os.path.join(data_dir, f"points3d_frame{frame_idx:06d}.npy")
        colors_file = os.path.join(data_dir, f"colors_frame{frame_idx:06d}.npy")

        positions = np.load(points_file).astype(np.float32)
        colors = np.load(colors_file).astype(np.float32)

        # Normalize colors to [0, 1]
        if colors.max() > 1.0:
            colors = colors / 255.0

        N = len(positions)

        # Initialize velocity as zero
        velocities = np.zeros((N, 3), dtype=np.float32)
        has_velocity = np.zeros(N, dtype=bool)

        # Match with previous frame to compute velocity
        if prev_points is not None and len(prev_points) > 0:
            dt = time - prev_time
            if dt > 1e-6:
                # Build KD-tree for current points
                tree = cKDTree(positions)

                # Find nearest neighbors for previous points
                dists, indices = tree.query(prev_points, k=1)

                # Match threshold (in world units)
                match_threshold = 0.1
                valid_matches = dists < match_threshold

                if valid_matches.sum() > 0:
                    # For matched points, compute velocity
                    matched_prev = prev_points[valid_matches]
                    matched_curr = positions[indices[valid_matches]]

                    # Velocity = (curr - prev) / dt
                    vel = (matched_curr - matched_prev) / dt

                    # Cap large velocities
                    vel_mag = np.linalg.norm(vel, axis=1, keepdims=True)
                    vel_scale = np.clip(max_velocity / (vel_mag + 1e-8), a_min=None, a_max=1.0)
                    vel = vel * vel_scale

                    # Assign velocity to matched current points
                    matched_indices = indices[valid_matches]
                    velocities[matched_indices] = vel
                    has_velocity[matched_indices] = True

                    n_matched = valid_matches.sum()
                    n_capped = (vel_mag.squeeze() > max_velocity).sum()
                    print(f"  Frame {frame_idx}: {N} pts, {n_matched} matched, {n_capped} vel capped")

        all_positions.append(positions)
        all_times.append(np.full((N, 1), time, dtype=np.float32))
        all_velocities.append(velocities)
        all_colors.append(colors)
        all_has_velocity.append(has_velocity)

        prev_points = positions
        prev_time = time
        prev_frame_idx = frame_idx

    # Concatenate all
    positions = np.concatenate(all_positions, axis=0)
    times = np.concatenate(all_times, axis=0)
    velocities = np.concatenate(all_velocities, axis=0)
    colors = np.concatenate(all_colors, axis=0)
    has_velocity = np.concatenate(all_has_velocity, axis=0)

    # Apply transform if provided
    if transform is not None:
        R = transform[:3, :3]
        t = transform[:3, 3]
        positions = (positions @ R.T) + t
        velocities = velocities @ R.T

    result = {
        'positions': torch.from_numpy(positions).float(),
        'times': torch.from_numpy(times).float(),
        'velocities': torch.from_numpy(velocities).float(),
        'colors': torch.from_numpy(colors).float(),
        'has_velocity': torch.from_numpy(has_velocity).bool(),
    }

    N = len(result['positions'])
    n_with_vel = result['has_velocity'].sum().item()

    print(f"\n[RomaPoints] Final: {N} points")
    print(f"  Time range: [{result['times'].min():.3f}, {result['times'].max():.3f}]")
    print(f"  With velocity: {n_with_vel} ({100*n_with_vel/N:.1f}%)")

    vel_mags = result['velocities'].norm(dim=1)
    has_vel = result['has_velocity']
    if has_vel.any():
        print(f"  Velocity magnitude: "
              f"min={vel_mags[has_vel].min():.4f}, "
              f"max={vel_mags[has_vel].max():.4f}, "
              f"mean={vel_mags[has_vel].mean():.4f}")

    return result


def load_windowed_points(
    npz_path: str,
    max_velocity: float = 0.5,
    max_angular_velocity: float = 0.5,
    transform: Optional[np.ndarray] = None,
    # Sampling parameters
    max_samples: int = 0,  # 0 = no sampling, use all points
    n_times: int = 3,  # Number of time windows to sample from
    high_velocity_ratio: float = 0.8,  # Ratio of high-velocity samples
    grid_resolution: int = 50,  # Spatial grid for coverage sampling
    sample_frame_start: int = 0,  # Frame range for sampling
    sample_frame_end: int = 300,
) -> Dict[str, torch.Tensor]:
    """
    Load windowed triangulated points with velocity from triangulate_windowed.py output.

    The windowed approach:
    - Tracks points within short temporal windows (e.g., 3 frames)
    - More points get velocity estimates (they only need to survive the window)
    - Each point has a time (middle of window) and duration (window span)
    - Each point has angular velocity estimated from local neighborhood rotation

    Sampling (when max_samples > 0):
    - Selects n_times time windows evenly across the sequence
    - For each time: 80% from high velocity regions, 20% random spatial coverage
    - Ensures both motion-rich regions and spatial coverage are represented

    Args:
        npz_path: Path to .npz file from triangulate_windowed.py
        max_velocity: Cap linear velocities at this value
        max_angular_velocity: Cap angular velocities at this value (radians per frame)
        transform: Optional 4x4 transform matrix for coordinate alignment
        max_samples: Maximum samples (0 = no sampling)
        n_times: Number of time windows to sample from
        high_velocity_ratio: Ratio of high-velocity samples (0.8 = 80%)
        grid_resolution: Spatial grid resolution for coverage sampling
        sample_frame_start: Start frame for sampling filter
        sample_frame_end: End frame for sampling filter

    Returns:
        Dictionary with positions, times, velocities, angular_velocities, colors, has_velocity, durations
    """
    print(f"\n[WindowedPoints] Loading windowed triangulated points...")
    print(f"  NPZ path: {npz_path}")

    if not os.path.exists(npz_path):
        raise ValueError(f"Windowed points file not found: {npz_path}")

    # Load npz file
    data = np.load(npz_path)

    positions = data['positions'].astype(np.float32)
    velocities = data['velocities'].astype(np.float32)
    colors = data['colors'].astype(np.float32)
    times = data['times'].astype(np.float32)
    durations = data['durations'].astype(np.float32)

    # Angular velocities (axis-angle format)
    if 'angular_velocities' in data:
        angular_velocities = data['angular_velocities'].astype(np.float32)
    else:
        # Backward compatibility: if not present, initialize to zeros
        angular_velocities = np.zeros_like(velocities)
        print("  [Warning] angular_velocities not found in npz, using zeros")

    # Metadata
    frame_start = int(data.get('frame_start', 0))
    frame_end = int(data.get('frame_end', 100))
    frame_step = int(data.get('frame_step', 10))
    window_size = int(data.get('window_size', 3))
    window_stride = int(data.get('window_stride', 2))
    k_neighbors = int(data.get('k_neighbors', 8))

    print(f"  Frame range: [{frame_start}, {frame_end}) step={frame_step}")
    print(f"  Window size: {window_size}, stride: {window_stride}")

    N = len(positions)
    print(f"  Loaded {N} points")

    # ==========================================================================
    # SAMPLING (if max_samples > 0)
    # ==========================================================================
    if max_samples > 0 and N > max_samples:
        print(f"\n  [Sampling] Reducing {N:,} points to {max_samples:,}")

        # Filter by frame range
        time_min = (sample_frame_start - frame_start) / max(frame_end - frame_start - 1, 1)
        time_max = (sample_frame_end - frame_start) / max(frame_end - frame_start - 1, 1)
        time_min, time_max = max(0, time_min), min(1, time_max)

        times_flat = times.flatten()
        time_mask = (times_flat >= time_min - 0.01) & (times_flat <= time_max + 0.01)

        if time_mask.sum() == 0:
            print(f"    Warning: No points in frame range [{sample_frame_start}, {sample_frame_end}), using all")
            time_mask = np.ones(N, dtype=bool)

        # Get unique times in range
        unique_times = np.unique(times_flat[time_mask])
        actual_n_times = min(n_times, len(unique_times))

        # Select which times to sample from
        if actual_n_times == len(unique_times):
            selected_times = unique_times
        else:
            time_indices = np.linspace(0, len(unique_times)-1, actual_n_times, dtype=int)
            selected_times = unique_times[time_indices]

        print(f"    Sampling from {actual_n_times} times: {selected_times.round(3)}")

        # Budget per time
        samples_per_time = max_samples // actual_n_times
        high_vel_per_time = int(samples_per_time * high_velocity_ratio)
        spatial_per_time = samples_per_time - high_vel_per_time

        # Compute velocity magnitudes
        vel_mag = np.linalg.norm(velocities, axis=1)

        all_indices = []
        for t in selected_times:
            t_mask = np.abs(times_flat - t) < 0.005
            t_indices = np.where(t_mask)[0]
            n_at_time = len(t_indices)

            if n_at_time == 0:
                continue

            t_vel = vel_mag[t_indices]
            t_pos = positions[t_indices]

            # High velocity sampling (80%)
            n_high = min(high_vel_per_time, n_at_time)
            if n_high > 0:
                sorted_idx = np.argsort(t_vel)[::-1]
                n_top = n_high // 2
                n_weighted = n_high - n_top

                top_indices = t_indices[sorted_idx[:n_top]]

                if n_weighted > 0 and len(sorted_idx) > n_top:
                    remaining_idx = sorted_idx[n_top:]
                    remaining_vel = t_vel[remaining_idx]
                    weights = remaining_vel / (remaining_vel.sum() + 1e-8)
                    n_weighted = min(n_weighted, len(remaining_idx))
                    weighted_sample = np.random.choice(remaining_idx, n_weighted, replace=False, p=weights)
                    weighted_indices = t_indices[weighted_sample]
                else:
                    weighted_indices = np.array([], dtype=np.int64)

                all_indices.extend(np.concatenate([top_indices, weighted_indices]).tolist())

            # Spatial coverage sampling (20%)
            n_spatial = min(spatial_per_time, n_at_time)
            if n_spatial > 0:
                pos_min, pos_max = t_pos.min(axis=0), t_pos.max(axis=0)
                pos_range = pos_max - pos_min + 1e-6
                grid_coords = ((t_pos - pos_min) / pos_range * grid_resolution).astype(int)
                grid_coords = np.clip(grid_coords, 0, grid_resolution - 1)
                cell_ids = (grid_coords[:, 0] * grid_resolution * grid_resolution +
                           grid_coords[:, 1] * grid_resolution + grid_coords[:, 2])

                unique_cells = np.unique(cell_ids)
                samples_per_cell = max(1, n_spatial // len(unique_cells))
                spatial_indices = []

                for cell in unique_cells:
                    cell_point_indices = np.where(cell_ids == cell)[0]
                    n_from_cell = min(samples_per_cell, len(cell_point_indices))
                    if n_from_cell > 0:
                        sampled = np.random.choice(cell_point_indices, n_from_cell, replace=False)
                        spatial_indices.extend(t_indices[sampled].tolist())
                    if len(spatial_indices) >= n_spatial:
                        break

                all_indices.extend(spatial_indices[:n_spatial])

        # Remove duplicates and apply
        all_indices = np.array(list(set(all_indices)), dtype=np.int64)
        np.random.shuffle(all_indices)

        positions = positions[all_indices]
        velocities = velocities[all_indices]
        angular_velocities = angular_velocities[all_indices]
        colors = colors[all_indices]
        times = times[all_indices]
        durations = durations[all_indices]

        N = len(positions)
        print(f"    Sampled to {N:,} points")

    # Normalize colors to [0, 1] if needed
    if colors.max() > 1.0:
        colors = colors / 255.0

    # Cap large linear velocities
    vel_mag = np.linalg.norm(velocities, axis=1, keepdims=True)
    large_vel_mask = vel_mag.squeeze() > max_velocity
    if large_vel_mask.any():
        vel_scale = np.clip(max_velocity / (vel_mag + 1e-8), a_min=None, a_max=1.0)
        velocities = velocities * vel_scale
        n_capped = large_vel_mask.sum()
        print(f"  Capped {n_capped} linear velocities exceeding {max_velocity}")

    # Cap large angular velocities
    ang_vel_mag = np.linalg.norm(angular_velocities, axis=1, keepdims=True)
    large_ang_vel_mask = ang_vel_mag.squeeze() > max_angular_velocity
    if large_ang_vel_mask.any():
        ang_vel_scale = np.clip(max_angular_velocity / (ang_vel_mag + 1e-8), a_min=None, a_max=1.0)
        angular_velocities = angular_velocities * ang_vel_scale
        n_ang_capped = large_ang_vel_mask.sum()
        print(f"  Capped {n_ang_capped} angular velocities exceeding {max_angular_velocity}")

    # All points from windowed tracking have velocity
    has_velocity = np.ones(N, dtype=bool)

    # Apply transform if provided
    if transform is not None:
        R = transform[:3, :3]
        t = transform[:3, 3]
        positions = (positions @ R.T) + t
        velocities = velocities @ R.T
        # Angular velocities also need rotation (but not translation)
        angular_velocities = angular_velocities @ R.T

    # Ensure times shape is [N, 1]
    if times.ndim == 1:
        times = times.reshape(-1, 1)

    result = {
        'positions': torch.from_numpy(positions).float(),
        'times': torch.from_numpy(times).float(),
        'velocities': torch.from_numpy(velocities).float(),
        'angular_velocities': torch.from_numpy(angular_velocities).float(),
        'colors': torch.from_numpy(colors).float(),
        'has_velocity': torch.from_numpy(has_velocity).bool(),
        'durations': torch.from_numpy(durations).float(),  # Extra: window duration per point
    }

    print(f"\n[WindowedPoints] Final: {N} points")
    print(f"  Time range: [{result['times'].min():.3f}, {result['times'].max():.3f}]")
    print(f"  Duration range: [{result['durations'].min():.3f}, {result['durations'].max():.3f}]")
    print(f"  With velocity: {N} (100.0%)")

    vel_mags = result['velocities'].norm(dim=1)
    print(f"  Linear velocity magnitude: "
          f"min={vel_mags.min():.4f}, "
          f"max={vel_mags.max():.4f}, "
          f"mean={vel_mags.mean():.4f}")

    ang_vel_mags = result['angular_velocities'].norm(dim=1)
    non_zero_ang = (ang_vel_mags > 1e-8).sum()
    print(f"  Angular velocity magnitude: "
          f"min={ang_vel_mags.min():.4f}, "
          f"max={ang_vel_mags.max():.4f}, "
          f"mean={ang_vel_mags.mean():.4f}, "
          f"non-zero={non_zero_ang}/{N} ({100*non_zero_ang/N:.1f}%)")

    return result


if __name__ == "__main__":
    import argparse

    arg_parser = argparse.ArgumentParser()
    arg_parser.add_argument("--data_dir", type=str, required=True)
    arg_parser.add_argument("--start_frame", type=int, default=0)
    arg_parser.add_argument("--end_frame", type=int, default=300)
    arg_parser.add_argument("--factor", type=int, default=1)
    arg_parser.add_argument("--test_4d_init", action="store_true",
                           help="Test 4D initialization from multi-frame COLMAP")
    args = arg_parser.parse_args()

    print(f"\n{'='*60}")
    print("FreeTime Dataset Test")
    print(f"{'='*60}")

    # Test parser
    parser = FreeTimeParser(
        data_dir=args.data_dir,
        factor=args.factor,
        start_frame=args.start_frame,
        end_frame=args.end_frame,
    )

    # Test dataset
    dataset = FreeTimeDataset(parser, split="train")
    print(f"\nDataset size: {len(dataset)}")

    # Test loading a few samples
    print("\nTesting image loading...")
    for i in [0, len(dataset) // 2, len(dataset) - 1]:
        data = dataset[i]
        print(f"  Sample {i}: image={tuple(data['image'].shape)}, "
              f"time={data['time']:.3f}, frame={data['frame_idx']}")

    # Test 4D initialization
    if args.test_4d_init:
        print(f"\n{'='*60}")
        print("Testing 4D Initialization from Multi-Frame COLMAP")
        print(f"{'='*60}")

        init_data = load_multiframe_colmap_points(
            args.data_dir,
            start_frame=args.start_frame,
            end_frame=args.end_frame,
        )

        print(f"\n4D Init Summary:")
        print(f"  Positions: {init_data['positions'].shape}")
        print(f"  Times: {init_data['times'].shape}")
        print(f"  Velocities: {init_data['velocities'].shape}")
        print(f"  Has velocity: {init_data['has_velocity'].sum().item()}/{len(init_data['has_velocity'])}")
