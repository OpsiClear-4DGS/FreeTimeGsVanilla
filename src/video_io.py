"""Explicit OpenCV/FFmpeg MP4 output for trajectory renders.

This avoids ImageIO backend auto-selection, which chose a broken PyAV path in
the training environment.
"""

import cv2
import numpy as np


class MP4Writer:
    """Write RGB uint8 frames to an MP4 file through OpenCV/FFmpeg.

    MPEG-4's usual YUV 4:2:0 path requires even dimensions. SelfCap frames are
    1919x1079, so the writer repeats the final row/column to 1920x1080 rather
    than resizing the image or asking each caller to know codec constraints.
    """

    def __init__(self, path: str, fps: float, width: int, height: int):
        if fps <= 0:
            raise ValueError(f"fps must be positive, got {fps}")
        if width <= 0 or height <= 0:
            raise ValueError(f"video dimensions must be positive, got {width}x{height}")

        self.path = str(path)
        self.input_width = int(width)
        self.input_height = int(height)
        self.width = self.input_width + self.input_width % 2
        self.height = self.input_height + self.input_height % 2
        self._writer = cv2.VideoWriter(
            self.path,
            cv2.VideoWriter_fourcc(*"mp4v"),
            float(fps),
            (self.width, self.height),
        )
        if not self._writer.isOpened():
            self._writer.release()
            self._writer = None
            raise RuntimeError(
                "OpenCV could not open an MP4/FFmpeg writer for "
                f"{self.path!r} at {self.width}x{self.height}"
            )

    def append_data(self, frame: np.ndarray) -> None:
        """Append one ``[H,W,3]`` RGB uint8 frame."""
        if self._writer is None:
            raise RuntimeError("cannot append to a closed MP4Writer")
        if frame.dtype != np.uint8:
            raise TypeError(f"video frame must be uint8, got {frame.dtype}")
        expected = (self.input_height, self.input_width, 3)
        if frame.shape != expected:
            raise ValueError(f"video frame shape must be {expected}, got {frame.shape}")

        if self.width != self.input_width or self.height != self.input_height:
            frame = cv2.copyMakeBorder(
                frame,
                0,
                self.height - self.input_height,
                0,
                self.width - self.input_width,
                cv2.BORDER_REPLICATE,
            )
        bgr = cv2.cvtColor(np.ascontiguousarray(frame), cv2.COLOR_RGB2BGR)
        self._writer.write(bgr)

    def close(self) -> None:
        """Flush the codec; safe to call more than once."""
        if self._writer is not None:
            self._writer.release()
            self._writer = None

    def __enter__(self) -> "MP4Writer":
        return self

    def __exit__(self, *_args) -> None:
        self.close()
