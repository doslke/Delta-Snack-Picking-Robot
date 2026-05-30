import threading

import cv2
import numpy as np


class CameraStream:
    """Background thread that continuously reads from the camera, always keeping the latest frame."""
    def __init__(self, cap: cv2.VideoCapture):
        self._cap   = cap
        self._frame = None
        self._ok    = False
        self._lock  = threading.Lock()
        self._stop  = False
        t = threading.Thread(target=self._worker, daemon=True)
        t.start()

    def _worker(self):
        while not self._stop:
            ok, frame = self._cap.read()
            with self._lock:
                self._ok    = ok
                self._frame = frame

    def read(self):
        with self._lock:
            if self._frame is None:
                return False, None
            return self._ok, self._frame.copy()

    def release(self):
        self._stop = True
        self._cap.release()


def load_camera_params(npz_path: str):
    """
    Load camera intrinsics from a .npz file; returns (K, dist, None, None).
    map1/map2 are built by build_undistort_maps once the resolution is known.
    """
    try:
        data = np.load(npz_path)
        K    = data["mtx"].astype(np.float64)
        dist = data["dist"].astype(np.float64)
        print(f"[INFO] Intrinsics loaded: {npz_path}")
        print(f"       fx={K[0,0]:.1f}  fy={K[1,1]:.1f}  "
              f"cx={K[0,2]:.1f}  cy={K[1,2]:.1f}")
        return K, dist, None, None
    except Exception as e:
        print(f"[WARN] Failed to load intrinsics file ({e}), skipping distortion correction")
        return None, None, None, None


def build_undistort_maps(K, dist, img_size):
    """Pre-compute remap tables for the given image size (w, h); call once on the first frame."""
    w, h = img_size
    new_K, _ = cv2.getOptimalNewCameraMatrix(K, dist, (w, h), alpha=0)
    map1, map2 = cv2.initUndistortRectifyMap(
        K, dist, None, new_K, (w, h), cv2.CV_16SC2)
    print(f"[INFO] Undistortion maps built  resolution=({w}×{h})")
    return map1, map2


def undistort(frame, map1, map2):
    """Apply pre-computed remap maps for distortion correction; returns frame unchanged if maps are None."""
    if map1 is None:
        return frame
    return cv2.remap(frame, map1, map2, cv2.INTER_LINEAR)
