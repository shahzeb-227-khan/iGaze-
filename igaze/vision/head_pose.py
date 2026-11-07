"""
head_pose.py
------------
Estimates head pitch (up/down tilt) from MediaPipe Face Mesh landmarks.

Design decision: We use a 3-D PnP solve with a small subset of stable
landmarks rather than the full mesh. This gives a robust, low-jitter
pitch angle that is unaffected by eyebrow raises or eye movements —
keeping the gaze and head channels completely independent.
"""

import math
import numpy as np
import cv2


# ── Canonical 3-D face model (metric, origin at nose tip) ─────────────────
# Indices match MediaPipe Face Mesh 468-point topology
_MODEL_POINTS = np.array([
    [  0.0,    0.0,    0.0   ],  # 1  – nose tip
    [  0.0,  -63.6,  -12.5  ],  # 152 – chin
    [-43.3,   32.7,  -26.0  ],  # 263 – left eye outer corner
    [ 43.3,   32.7,  -26.0  ],  # 33  – right eye outer corner
    [-28.9,  -28.9,  -24.1  ],  # 287 – left mouth corner
    [ 28.9,  -28.9,  -24.1  ],  # 57  – right mouth corner
], dtype=np.float64)

_LANDMARK_IDS = [1, 152, 263, 33, 287, 57]


def _build_camera_matrix(w: int, h: int) -> tuple[np.ndarray, np.ndarray]:
    focal = w
    cx, cy = w / 2.0, h / 2.0
    K = np.array([[focal, 0, cx],
                  [0, focal, cy],
                  [0,     0,  1]], dtype=np.float64)
    dist = np.zeros((4, 1), dtype=np.float64)
    return K, dist


def _rotation_matrix_to_euler(R: np.ndarray):
    """Rodrigues rotation matrix → (pitch, yaw, roll) in degrees."""
    sy = math.sqrt(R[0, 0]**2 + R[1, 0]**2)
    singular = sy < 1e-6
    if not singular:
        pitch = math.atan2( R[2, 1],  R[2, 2])
        yaw   = math.atan2(-R[2, 0],  sy)
        roll  = math.atan2( R[1, 0],  R[0, 0])
    else:
        pitch = math.atan2(-R[1, 2],  R[1, 1])
        yaw   = math.atan2(-R[2, 0],  sy)
        roll  = 0.0
    return math.degrees(pitch), math.degrees(yaw), math.degrees(roll)


class HeadPoseEstimator:
    """
    Estimates head pitch from a MediaPipe Face Mesh result.

    Parameters
    ----------
    smooth_alpha : float
        EMA smoothing factor (0 = frozen, 1 = no smoothing).
        0.25 gives a ~4-frame lag at 30 fps — enough to kill sensor jitter
        without making the response feel sluggish.
    """

    def __init__(self, smooth_alpha: float = 0.15):  # lower α = more smoothing, less jitter
        self._alpha  = smooth_alpha
        self._smooth_pitch: float | None = None
        self._K: np.ndarray | None = None
        self._dist: np.ndarray | None = None

    def _ensure_camera(self, w: int, h: int):
        if self._K is None:
            self._K, self._dist = _build_camera_matrix(w, h)

    def reset(self):
        self._smooth_pitch = None

    def update(self, frame: np.ndarray, face_landmarks) -> float | None:
        """
        Compute and return the smoothed pitch angle (degrees).

        Positive pitch → head tilted DOWN (nose moves toward screen).
        Negative pitch → head tilted UP.

        Returns None if landmarks are absent or solvePnP fails.
        """
        if face_landmarks is None:
            return None

        h, w = frame.shape[:2]
        self._ensure_camera(w, h)

        # Collect the 6 image points
        image_points = []
        # Handle both old API (with .landmark attribute) and new Tasks API (direct list)
        landmarks_list = (face_landmarks.landmark if hasattr(face_landmarks, 'landmark') 
                         else face_landmarks)
        for idx in _LANDMARK_IDS:
            lm = landmarks_list[idx]
            image_points.append([lm.x * w, lm.y * h])
        image_points = np.array(image_points, dtype=np.float64)

        success, rvec, _ = cv2.solvePnP(
            _MODEL_POINTS, image_points,
            self._K, self._dist,
            flags=cv2.SOLVEPNP_ITERATIVE
        )
        if not success:
            return None

        R, _ = cv2.Rodrigues(rvec)
        pitch, _, _ = _rotation_matrix_to_euler(R)
        if not math.isfinite(pitch):
            return None

        # Exponential moving average
        if self._smooth_pitch is None:
            self._smooth_pitch = pitch
        else:
            # Follow the shortest rotation across the ±180° boundary.
            delta = (pitch - self._smooth_pitch + 180) % 360 - 180
            self._smooth_pitch += self._alpha * delta

        return self._smooth_pitch

