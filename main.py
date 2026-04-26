"""
main.py — iGAZE v3
══════════════════════════════════════════════════════════════════════════════

Application controller.  Owns all CV/tracking objects.

Thread model
────────────
  Main thread   : Tkinter GUI (gui.py calls root.mainloop())
  Daemon thread : OpenCV capture + gesture loop (_tracking_loop)

All communication between threads goes through self.state (plain dict).
Python dict reads/writes of simple values are effectively atomic on CPython
so no explicit lock is needed for this read-heavy / write-rare pattern.
"""

import collections
import threading
import time

import cv2

from eye_tracker    import EyeTracker
from gaze_detector  import GazeDetector
from head_pose      import HeadPoseEstimator
from gesture_mapper import GestureMapper, MODE_APPSW, MODE_NORMAL
from calibration    import Calibration

CAL_FILE = "igaze_cal.json"


class iGazeApp:
    """Application controller — create once, then call start_tracking()."""

    def __init__(self, model_path: str = "face_landmarker.task"):
        # ── Core CV objects ────────────────────────────────────────────────
        self.tracker   = EyeTracker(model_path=model_path)
        self.detector  = GazeDetector()
        self.head_pose = HeadPoseEstimator(smooth_alpha=0.15)
        self.mapper    = GestureMapper()
        self.cal       = Calibration()

        # ── Thread coordination ────────────────────────────────────────────
        self.stop_event       = threading.Event()
        self.calibration_done = threading.Event()

        # ── Shared state dict (GUI reads this every 100 ms) ───────────────
        self.state: dict = {
            "running":        False,
            "mode":           "normal",
            "enabled":        True,
            "gaze":           "CENTER",
            "pitch":          0.0,
            "scroll_state":   "idle",
            "scroll_speed":   0,
            "triple_count":   0,
            "triple_state":   "IDLE",
            "eye_open_conf":  1.0,
            "close_progress": 0.0,
            "ear":            0.0,
            "fps":            0.0,
            "calibrated":     False,
        }

    # ══════════════════════════════════════════════════════════════════════
    # Calibration
    # ══════════════════════════════════════════════════════════════════════

    def run_calibration(self):
        """
        Load saved calibration if available; otherwise run the interactive
        Calibration procedure (which blocks for ~4 s with an OpenCV window),
        then save and apply to the detector.

        Must be called from a NON-main thread so the GUI stays responsive.
        """
        loaded = self.cal.load_calibration(CAL_FILE)

        if not loaded:
            # Runs the 2-phase interactive calibration (opens a cv2 window)
            self.cal.start_calibration(self.tracker, self.detector, duration=4.0)
            self.cal.save_calibration(CAL_FILE)

        # apply_to_detector references detector._debug which does not exist
        # on the actual GazeDetector — apply the important fields manually.
        try:
            self.cal.apply_to_detector(self.detector)
        except AttributeError:
            # Fallback: apply only the fields that actually exist
            thresh = self.cal.ear_baseline * self.cal.ear_hard_fraction
            self.detector.EAR_THRESH   = thresh
            self.detector.center_h     = self.cal.center_h
            self.detector.center_v     = self.cal.center_v
            self.detector.calibrated   = self.cal.is_calibrated
            print(
                f"[Calibration] Applied (fallback).  "
                f"EAR_THRESH={thresh:.3f}  "
                f"H={self.cal.center_h:.3f}  V={self.cal.center_v:.3f}"
            )

        self.state["calibrated"] = True
        self.calibration_done.set()

    # ══════════════════════════════════════════════════════════════════════
    # Tracking loop  (runs on background daemon thread)
    # ══════════════════════════════════════════════════════════════════════

    def _tracking_loop(self):
        """
        Per-frame loop.  Runs until stop_event is set or 'q' is pressed.

        Call order inside the loop follows the gesture_mapper.py demo exactly:
          1. get_frame_data
          2. head_pose.update
          3. detector.update_blink  → single flag
          4. detector.get_gaze_direction
          5. compute eye_open from avg EAR
          6. mapper.update_eye_state  (MUST be before update_blink)
          7. mapper.update_blink
          8. mapper.update_head_pitch
          9. mapper.update_gaze
        """
        ts_buf = collections.deque(maxlen=30)   # rolling timestamps for FPS

        while not self.stop_event.is_set():
            t_frame = time.perf_counter()

            # ── 1. Capture ────────────────────────────────────────────────
            frame, landmarks, left_eye, right_eye, left_ear, right_ear = \
                self.tracker.get_frame_data()

            if frame is None:
                time.sleep(0.01)
                continue

            # ── 2. Head pose ──────────────────────────────────────────────
            pitch = self.head_pose.update(frame, landmarks)

            # ── 3. Blink detection ────────────────────────────────────────
            single, _, _ = self.detector.update_blink(left_ear, right_ear)

            # ── 4. Gaze direction ─────────────────────────────────────────
            gaze = self.detector.get_gaze_direction(
                frame, landmarks, left_eye, right_eye)

            # ── 5. Eye open state ─────────────────────────────────────────
            avg_ear  = (left_ear + right_ear) / 2.0
            eye_open = avg_ear >= self.detector.EAR_THRESH
            # Normalised confidence: 0 = right at threshold, 1 = wide-open
            eye_conf = min(
                max((avg_ear - self.detector.EAR_THRESH) /
                    max(self.detector.EAR_THRESH, 0.01), 0.0),
                1.0
            )

            # ── 6-9. Feed mapper (ORDER IS MANDATORY) ─────────────────────
            self.mapper.update_eye_state(eye_open)
            self.mapper.update_blink(single)
            self.mapper.update_head_pitch(pitch)
            self.mapper.update_gaze(gaze)

            # ── FPS ───────────────────────────────────────────────────────
            ts_buf.append(t_frame)
            fps = ((len(ts_buf) - 1) / (ts_buf[-1] - ts_buf[0] + 1e-9)
                   if len(ts_buf) > 1 else 0.0)

            # ── Scroll HUD values (derived from mapper attrs) ──────────────
            if pitch is not None and abs(pitch) > self.mapper.head_dead_zone:
                scroll_state = "UP" if pitch < 0 else "DOWN"
                pitch_eff    = abs(pitch) - self.mapper.head_dead_zone
                max_eff      = max(
                    self.mapper.head_max_pitch - self.mapper.head_dead_zone, 1)
                scroll_spd   = int(min(pitch_eff / max_eff, 1.0) * 100)
            else:
                scroll_state = "idle"
                scroll_spd   = 0

            # ── Disable-progress ──────────────────────────────────────────
            if self.mapper._eye_close_start is not None:
                elapsed  = time.time() - self.mapper._eye_close_start
                close_pr = min(elapsed / self.mapper.disable_hold_sec, 1.0)
            else:
                close_pr = 0.0

            # ── Triple-blink state ────────────────────────────────────────
            n_blinks = len(self.mapper._blink_times)
            triple_st = "COUNTING" if n_blinks > 0 else "IDLE"

            # ── Update shared state ───────────────────────────────────────
            self.state.update({
                "mode":           self.mapper.mode,
                "enabled":        self.mapper.enabled,
                "gaze":           gaze,
                "pitch":          pitch if pitch is not None else 0.0,
                "scroll_state":   scroll_state,
                "scroll_speed":   scroll_spd,
                "triple_count":   n_blinks,
                "triple_state":   triple_st,
                "eye_open_conf":  round(eye_conf, 2),
                "close_progress": close_pr,
                "ear":            round(avg_ear, 3),
                "fps":            round(fps, 1),
            })

            # ── Debug overlay ─────────────────────────────────────────────
            frame = self.tracker.draw_debug(
                frame, landmarks, left_eye, right_eye, left_ear, right_ear)
            self._draw_hud(frame, gaze, pitch, scroll_state,
                           scroll_spd, close_pr, fps)

            # ── Display (imshow + waitKey MUST be on same thread) ─────────
            cv2.imshow("iGAZE v3 — Camera Feed", frame)
            if cv2.waitKey(1) & 0xFF == ord('q'):
                break

        # ── Cleanup on loop exit ──────────────────────────────────────────
        cv2.destroyAllWindows()
        self.state["running"] = False
        print("[iGazeApp] Tracking loop stopped.")

    # ──────────────────────────────────────────────────────────────────────
    # HUD renderer
    # ──────────────────────────────────────────────────────────────────────

    def _draw_hud(self, frame, gaze, pitch, scroll_state,
                  scroll_spd, close_pr, fps):
        """Render status overlay onto the camera feed frame."""
        mapper    = self.mapper
        h_px, w_px = frame.shape[:2]

        ctrl_col   = (0, 220, 80)   if mapper.enabled             else (30, 30, 220)
        mode_col   = (0, 160, 255)  if mapper.mode == MODE_APPSW  else (160, 160, 160)
        pitch_str  = f"{pitch:+.1f}" if pitch is not None else "n/a"
        n_blinks   = len(mapper._blink_times)

        lines = [
            (f"Gaze:    {gaze}",                           (0, 200, 255)),
            (f"Pitch:   {pitch_str} deg",                  (0, 200, 200)),
            (f"Scroll:  {scroll_state}  {scroll_spd}%",   (200, 200,   0)),
            (f"Blinks:  {n_blinks}/3",                     (200, 255,   0)),
            (f"Mode:    {mapper.mode.upper()}",             mode_col),
            (f"Control: {'ON' if mapper.enabled else 'OFF'}", ctrl_col),
            (f"FPS:     {fps:.1f}",                         (140, 140, 140)),
        ]
        for i, (text, col) in enumerate(lines):
            cv2.putText(frame, text, (10, 90 + i * 24),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, col, 1, cv2.LINE_AA)

        # Disable progress bar at bottom of frame
        if mapper._eye_close_start is not None:
            bar_x, bar_y = 10, h_px - 40
            bar_w, bar_h = 300, 14
            cv2.rectangle(frame,
                          (bar_x, bar_y), (bar_x + bar_w, bar_y + bar_h),
                          (40, 40, 40), -1)
            fill = int(bar_w * close_pr)
            cv2.rectangle(frame,
                          (bar_x, bar_y), (bar_x + fill, bar_y + bar_h),
                          (30, 90, 220), -1)
            cv2.putText(
                frame,
                f"DISABLE: {close_pr * mapper.disable_hold_sec:.1f}s "
                f"/ {mapper.disable_hold_sec:.0f}s",
                (bar_x, bar_y - 6),
                cv2.FONT_HERSHEY_SIMPLEX, 0.44, (30, 90, 220), 1, cv2.LINE_AA)

    # ══════════════════════════════════════════════════════════════════════
    # Thread control
    # ══════════════════════════════════════════════════════════════════════

    def start_tracking(self):
        """Start the tracking loop on a daemon background thread."""
        if self.state["running"]:
            return
        self.stop_event.clear()
        self.state["running"] = True
        t = threading.Thread(target=self._tracking_loop, daemon=True,
                             name="iGAZE-tracking")
        t.start()

    def stop_tracking(self):
        """Signal the tracking loop to stop."""
        self.stop_event.set()
        self.state["running"] = False

    def cleanup(self):
        """Release camera resources — call once on application exit."""
        self.stop_tracking()
        time.sleep(0.15)        # let the loop exit its current iteration
        self.tracker.release()

    # ══════════════════════════════════════════════════════════════════════
    # Live sensitivity adjustment (called from GUI)
    # ══════════════════════════════════════════════════════════════════════

    def apply_sensitivity(self, settings: dict):
        """
        Apply sensitivity settings live to the running GestureMapper.

        Parameters (all optional — only keys present are applied)
        ──────────────────────────────────────────────────────────
        scroll_dead_zone     : float  degrees   (mapper.head_dead_zone)
        gaze_hold_seconds    : float  seconds   (mapper.gaze_hold)
        triple_max_window_ms : float  ms        (mapper.triple_win in seconds)
        disable_hold_sec     : float  seconds   (mapper.disable_hold_sec)
        """
        m = self.mapper
        if "scroll_dead_zone" in settings:
            m.head_dead_zone  = float(settings["scroll_dead_zone"])
        if "gaze_hold_seconds" in settings:
            m.gaze_hold       = float(settings["gaze_hold_seconds"])
        if "triple_max_window_ms" in settings:
            m.triple_win      = float(settings["triple_max_window_ms"]) / 1000.0
        if "disable_hold_sec" in settings:
            m.disable_hold_sec = float(settings["disable_hold_sec"])
        print(f"[iGazeApp] Sensitivity updated: {settings}")


# ══════════════════════════════════════════════════════════════════════════
# Entry point
# ══════════════════════════════════════════════════════════════════════════

def main():
    app = iGazeApp()
    from gui import iGazeGUI            # imported here to keep gui optional
    gui = iGazeGUI(app)
    gui.run()                           # blocks on tk.mainloop()


if __name__ == "__main__":
    main()
