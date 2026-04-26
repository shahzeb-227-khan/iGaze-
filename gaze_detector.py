"""
gaze_detector.py
----------------
Iris-based gaze detection with live calibration.
Look CENTER first, then it learns your personal eye ranges.
"""

import time
import numpy as np
import cv2

GAZE_CENTER = "CENTER"
GAZE_LEFT   = "LEFT"
GAZE_RIGHT  = "RIGHT"
GAZE_UP     = "UP"
GAZE_DOWN   = "DOWN"

# Iris indices (MediaPipe 478-point model)
LEFT_IRIS_CENTER  = 468
RIGHT_IRIS_CENTER = 473

# Eye corner indices
LEFT_EYE_INNER  = 33;  LEFT_EYE_OUTER  = 133
LEFT_EYE_TOP    = 160; LEFT_EYE_BOTTOM = 144
RIGHT_EYE_INNER = 362; RIGHT_EYE_OUTER = 263
RIGHT_EYE_TOP   = 385; RIGHT_EYE_BOTTOM= 380


class GazeDetector:
    def __init__(self,
                 blink_ear_threshold=0.21,
                 blink_consec_frames=2,
                 double_blink_interval=0.6):

        self.EAR_THRESH          = blink_ear_threshold
        self.BLINK_FRAMES        = blink_consec_frames
        self.DOUBLE_BLINK_WINDOW = double_blink_interval

        self._frame_counter = 0
        self._blink_times   = []
        self.total_blinks   = 0
        self.last_gaze      = GAZE_CENTER

        # Smoothing
        self._gaze_history = []
        self._history_size = 5

        # Calibration
        self.calibrated    = False
        self.center_h      = 0.5
        self.center_v      = 0.5
        self.h_sensitivity = 0.05
        self.v_sensitivity = 0.05

    # --------------------------------------------------
    # Calibration
    # --------------------------------------------------

    def calibrate(self, tracker, duration=3):
        """Look straight at screen center during calibration."""
        print("[Calibration] Look straight at the screen center...")
        samples_h = []
        samples_v = []
        start     = time.time()

        while time.time() - start < duration:
            frame, landmarks, left_eye, right_eye, left_ear, right_ear = tracker.get_frame_data()
            if frame is None or landmarks is None:
                continue

            h, w         = frame.shape[:2]
            avg_h, avg_v = self._get_iris_ratios(landmarks, w, h)

            if avg_h is not None:
                samples_h.append(avg_h)
                samples_v.append(avg_v)

            remaining = duration - (time.time() - start)
            overlay   = frame.copy()
            cv2.rectangle(overlay, (0, 0), (w, h), (0, 0, 0), -1)
            cv2.addWeighted(overlay, 0.4, frame, 0.6, 0, frame)

            cv2.putText(frame, "LOOK AT THE DOT BELOW", (w//2 - 180, h//2 - 60),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2)
            cv2.putText(frame, f"Hold still... {remaining:.1f}s", (w//2 - 130, h//2 - 20),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)

            # Big green dot at center
            cv2.circle(frame, (w//2, h//2 + 30), 15, (0, 255, 0), -1)
            cv2.line(frame, (w//2 - 40, h//2 + 30), (w//2 + 40, h//2 + 30), (0,255,0), 1)
            cv2.line(frame, (w//2, h//2 - 10),      (w//2, h//2 + 70),       (0,255,0), 1)

            cv2.imshow("iGAZE - Calibration", frame)
            cv2.waitKey(1)

        try:
            cv2.destroyWindow("iGAZE - Calibration")
        except:
            pass

        if samples_h:
            self.center_h   = float(np.mean(samples_h))
            self.center_v   = float(np.mean(samples_v))
            self.calibrated = True
            print(f"[Calibration] Done. Center H={self.center_h:.3f}  V={self.center_v:.3f}")
        else:
            print("[Calibration] No face detected — using defaults.")

    # --------------------------------------------------
    # Iris ratio helper
    # --------------------------------------------------

    def _get_iris_ratios(self, landmarks_raw, w, h):
        """Get normalized iris position within the eye region."""
        try:
            if landmarks_raw is None or len(landmarks_raw) < 478:
                return None, None

            lx = landmarks_raw[LEFT_IRIS_CENTER].x  * w
            ly = landmarks_raw[LEFT_IRIS_CENTER].y  * h
            rx = landmarks_raw[RIGHT_IRIS_CENTER].x * w
            ry = landmarks_raw[RIGHT_IRIS_CENTER].y * h

            l_inner = landmarks_raw[LEFT_EYE_INNER].x  * w
            l_outer = landmarks_raw[LEFT_EYE_OUTER].x  * w
            l_top   = landmarks_raw[LEFT_EYE_TOP].y    * h
            l_bot   = landmarks_raw[LEFT_EYE_BOTTOM].y * h

            r_inner = landmarks_raw[RIGHT_EYE_INNER].x  * w
            r_outer = landmarks_raw[RIGHT_EYE_OUTER].x  * w
            r_top   = landmarks_raw[RIGHT_EYE_TOP].y    * h
            r_bot   = landmarks_raw[RIGHT_EYE_BOTTOM].y * h

            lh = (lx - l_inner) / (l_outer - l_inner + 1e-6)
            lv = (ly - l_top)   / (l_bot   - l_top   + 1e-6)
            rh = (rx - r_inner) / (r_outer - r_inner + 1e-6)
            rv = (ry - r_top)   / (r_bot   - r_top   + 1e-6)

            return (lh + rh) / 2.0, (lv + rv) / 2.0

        except Exception:
            return None, None

    # --------------------------------------------------
    # Blink Detection
    # --------------------------------------------------

    def update_blink(self, left_ear, right_ear):
        avg_ear      = (left_ear + right_ear) / 2.0
        single_blink = False
        double_blink = False
        right_wink   = False

        # --- Wink detection ---
        # Right wink = right eye closed, left eye open
        right_closed = right_ear < self.EAR_THRESH
        left_open    = left_ear  > self.EAR_THRESH + 0.05

        if right_closed and left_open:
            self._wink_frames = getattr(self, '_wink_frames', 0) + 1
        else:
            if getattr(self, '_wink_frames', 0) >= self.BLINK_FRAMES:
                right_wink = True
            self._wink_frames = 0

        # --- Normal blink detection (both eyes) ---
        if avg_ear < self.EAR_THRESH:
            self._frame_counter += 1
        else:
            if self._frame_counter >= self.BLINK_FRAMES:
                single_blink = True
                self.total_blinks += 1
                now = time.time()
                self._blink_times.append(now)
                self._blink_times = [
                    t for t in self._blink_times
                    if now - t <= self.DOUBLE_BLINK_WINDOW
                ]
                if len(self._blink_times) >= 2:
                    double_blink = True
                    self._blink_times.clear()
            self._frame_counter = 0

        return single_blink, double_blink, right_wink
    # --------------------------------------------------
    # Gaze Direction
    # --------------------------------------------------

    def _smooth_gaze(self, gaze):
        self._gaze_history.append(gaze)
        if len(self._gaze_history) > self._history_size:
            self._gaze_history.pop(0)
        return max(set(self._gaze_history), key=self._gaze_history.count)

    def get_gaze_direction(self, frame, landmarks_raw, left_eye=None, right_eye=None):
        if landmarks_raw is None:
            return self.last_gaze

        h, w         = frame.shape[:2]
        avg_h, avg_v = self._get_iris_ratios(landmarks_raw, w, h)

        if avg_h is None:
            return self.last_gaze

        # How far iris moved from YOUR calibrated center
        diff_h = avg_h - self.center_h
        diff_v = avg_v - self.center_v

        if diff_h < -self.h_sensitivity:
            gaze = GAZE_LEFT
        elif diff_h > self.h_sensitivity:
            gaze = GAZE_RIGHT
        elif diff_v < -self.v_sensitivity:
            gaze = GAZE_UP
        elif diff_v > self.v_sensitivity:
            gaze = GAZE_DOWN
        else:
            gaze = GAZE_CENTER

        gaze           = self._smooth_gaze(gaze)
        self.last_gaze = gaze
        return gaze


# --- Quick Test ---
if __name__ == "__main__":
    from eye_tracker import EyeTracker

    tracker  = EyeTracker()
    detector = GazeDetector()

    # Calibrate first — look at the green dot
    detector.calibrate(tracker, duration=3)

    print("Press 'q' to quit.")

    try:
        while True:
            frame, landmarks, left_eye, right_eye, left_ear, right_ear = tracker.get_frame_data()
            if frame is None:
                break

            single, double, right_wink = detector.update_blink(left_ear, right_ear)
            gaze           = detector.get_gaze_direction(frame, landmarks, left_eye, right_eye)
            frame          = tracker.draw_debug(frame, landmarks, left_eye, right_eye, left_ear, right_ear)

            # Gaze label with background box
            label      = f"  Gaze: {gaze}  "
            text_size  = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.8, 2)[0]
            cv2.rectangle(frame, (8, 78), (8 + text_size[0], 78 + text_size[1] + 10), (0,0,0), -1)
            cv2.putText(frame, label, (10, 100),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2)

            cv2.putText(frame, f"Blinks: {detector.total_blinks}",
                        (10, 130), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (200, 255, 0), 2)

            cv2.imshow("iGAZE - Gaze Detector", frame)
            if cv2.waitKey(1) & 0xFF == ord('q'):
                break

    finally:
        tracker.release()
        cv2.destroyAllWindows()