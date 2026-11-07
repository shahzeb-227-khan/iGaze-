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
import math
import sqlite3
import threading
import time

import cv2

from igaze.vision.eye_tracker    import EyeTracker
from igaze.vision.gaze_detector  import GazeDetector
from igaze.vision.head_pose      import HeadPoseEstimator
from igaze.vision.head_scroll import HeadScrollCalibration, HeadScrollGate, capture_head_calibration
from igaze.controls import DesktopInputStopped, GestureMapper, MODE_APPSW, MODE_NORMAL
from igaze.vision.calibration import Calibration
from igaze.paths import data_path, resource_path
from igaze.storage import Store, utc_now

SENSITIVITY_RANGES = {
    "scroll_dead_zone": (3, 15),
    "gaze_hold_seconds": (.5, 3),
    "triple_max_window_ms": (400, 2000),
    "disable_hold_sec": (2, 15),
}


class iGazeApp:
    """Application controller — create once, then call start_tracking()."""

    def __init__(self, model_path: str = "face_landmarker.task", store=None):
        self.store = store if store is not None else Store()
        # ── Core CV objects ────────────────────────────────────────────────
        self.tracker   = EyeTracker(model_path=str(resource_path(model_path)))
        self.detector  = GazeDetector()
        self.head_pose = HeadPoseEstimator(smooth_alpha=0.30)
        self.mapper    = GestureMapper(head_hold_seconds=.30)
        self.cal       = Calibration()
        try:
            self.head_calibration = HeadScrollCalibration.from_dict(self.store.get("head_scroll"))
        except (KeyError, TypeError, ValueError, OverflowError):
            self.head_calibration = None

        # ── Thread coordination ────────────────────────────────────────────
        self.stop_event       = threading.Event()
        self.calibration_done = threading.Event()
        self._camera_lock = threading.Lock()
        self._worker = None
        self._action_capture_time = None

        # ── Shared state dict (GUI reads this every 100 ms) ───────────────
        self.state: dict = {
            "running":        False,
            "error":          "",
            "emergency_stop": False,
            "paused":         False,
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
        self.apply_sensitivity(self.store.get("sensitivity", {}), persist=False)
        self.load_calibration(import_legacy=False)

    # ══════════════════════════════════════════════════════════════════════
    # Calibration
    # ══════════════════════════════════════════════════════════════════════

    def run_calibration(self):
        """Capture fresh calibration. Load Cal remains a separate button."""
        if not self._camera_lock.acquire(blocking=False):
            raise RuntimeError("Stop tracking before calibrating.")
        try:
            candidate = Calibration()
            candidate.start_calibration(self.tracker, self.detector, duration=4.0)
            if not candidate.is_calibrated:
                raise ValueError("No usable calibration samples. Face the camera and retry.")
            candidate.train_gaze_model(self.tracker)
            self.store.set("calibration", candidate.to_dict())
            candidate.apply_to_detector(self.detector)
            self.cal = candidate
            self.state["calibrated"] = True
            self.calibration_done.set()
        finally:
            self._camera_lock.release()

    def save_calibration(self):
        if not self.cal.is_calibrated:
            raise ValueError("Run calibration before saving.")
        self.store.set("calibration", self.cal.to_dict())

    def run_head_calibration(self):
        if not self._camera_lock.acquire(blocking=False):
            raise RuntimeError("Stop tracking before calibrating head scroll.")
        try:
            calibration = capture_head_calibration(self.tracker)
            if calibration is None:
                return False
            self.store.set("head_scroll", calibration.to_dict())
            self.head_calibration = calibration
            return True
        finally:
            self._camera_lock.release()

    def load_calibration(self, *, import_legacy=True):
        try:
            candidate = Calibration.from_dict(self.store.get("calibration"))
        except (ValueError, TypeError, OverflowError):
            if not import_legacy:
                return False
            candidate = Calibration()
            paths = (data_path("igaze_cal.json"), resource_path("igaze_cal.json"))
            if not any(candidate.load_calibration(path) for path in paths):
                return False
            self.store.set("calibration", candidate.to_dict())
        candidate.apply_to_detector(self.detector)
        self.cal = candidate
        self.state["calibrated"] = True
        return True

    def _tracking_loop(self):
        started, clock = utc_now(), time.monotonic()
        initial_actions = self.mapper.actions_sent
        events = []
        self._action_capture_time = None

        def record_action(kind, mode):
            now = time.monotonic()
            captured = self._action_capture_time
            age_ms = None if captured is None else round(max(0, now - captured) * 1000, 1)
            events.append((round(now - clock, 3), kind, mode, age_ms))

        self.mapper.on_action = record_action
        try:
            with self._camera_lock:
                self._run_tracking()
        except DesktopInputStopped as exc:
            self.stop_event.set()
            self.mapper.enabled = False
            self.mapper.mode = MODE_NORMAL
            self.state.update(error=str(exc), emergency_stop=True, enabled=False,
                              mode=MODE_NORMAL, scroll_state="idle", scroll_speed=0)
        except Exception as exc:
            self.state["error"] = str(exc)
        finally:
            self.mapper.on_action = None
            self._reset_gesture_state()
            try:
                cv2.destroyAllWindows()
                self.store.record(started, "Desktop", time.monotonic() - clock,
                                  self.mapper.actions_sent - initial_actions, self.state["error"],
                                  events=events)
            except (sqlite3.Error, OSError) as exc:
                self.state["error"] = f"Session history could not be saved: {exc}"
            finally:
                self.state.update(running=False, paused=False)

    def _reset_gesture_state(self, *, keep_completed_blinks=False):
        self.mapper.reset_tracking(keep_completed_blinks=keep_completed_blinks)
        self.detector.reset_tracking()
        self.head_pose.reset()

    def _run_tracking(self):
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
        head_scroll = HeadScrollGate(self.head_calibration)
        input_epoch = self.mapper.input_epoch
        missing_since = None
        last_capture = None

        while not self.stop_event.is_set():
            t_frame = time.perf_counter()

            # ── 1. Capture ────────────────────────────────────────────────
            frame, landmarks, left_eye, right_eye, left_ear, right_ear = \
                self.tracker.get_frame_data()

            if self.stop_event.is_set():
                break
            if input_epoch != self.mapper.input_epoch:
                input_epoch = self.mapper.input_epoch
                head_scroll.reset()
                self.detector.reset_tracking()
            if frame is None:
                if missing_since is None:
                    missing_since = t_frame
                elif t_frame - missing_since >= 2:
                    raise RuntimeError("Camera stopped delivering frames. Reconnect it and restart iGAZE.")
            else:
                missing_since = None
            if (frame is None or landmarks is None
                    or time.monotonic() - self.tracker.capture_time > .25):
                recent = last_capture is not None and time.monotonic() - last_capture <= .20
                self._reset_gesture_state(keep_completed_blinks=recent)
                head_scroll.reset()
                self.state.update(gaze="CENTER", pitch=0.0, scroll_state="idle", scroll_speed=0,
                                  triple_count=len(self.mapper._blink_times),
                                  triple_state="COUNTING" if self.mapper._blink_times else "IDLE",
                                  close_progress=0.0)
                if frame is not None:
                    cv2.imshow("iGAZE v3 - Camera Feed", frame)
                    if cv2.waitKey(1) & 0xFF == ord("q"):
                        break
                else:
                    self.stop_event.wait(.02)
                continue
            if last_capture is not None and self.tracker.capture_time - last_capture > .25:
                self._reset_gesture_state()
                head_scroll.reset()
            last_capture = self.tracker.capture_time

            # ── 2. Head pose ──────────────────────────────────────────────
            pitch = self.head_pose.update(frame, landmarks)

            # ── 3. Blink detection ────────────────────────────────────────
            if not all(math.isfinite(value) and value >= 0 for value in (left_ear, right_ear)):
                self._reset_gesture_state()
                head_scroll.reset()
                continue
            single, _, _ = self.detector.update_blink(left_ear, right_ear, last_capture)

            avg_ear = (left_ear + right_ear) / 2.0
            eye_open = avg_ear >= self.detector.EAR_THRESH and not self.detector.eyes_closed
            # Closed lids make iris landmarks unreliable. EAR still tracks the blink.
            task_view = self.mapper.mode == MODE_APPSW
            gaze = (self.detector.get_gaze_direction(frame, landmarks, left_eye, right_eye)
                    if eye_open else None)
            # Normalised confidence: 0 = right at threshold, 1 = wide-open
            eye_conf = min(
                max((avg_ear - self.detector.EAR_THRESH) /
                    max(self.detector.EAR_THRESH, 0.01), 0.0),
                1.0
            )

            # ── 6-9. Feed mapper (ORDER IS MANDATORY) ─────────────────────
            if self.mapper.input_paused:
                head_scroll.reset()
            control_pitch = head_scroll.update(pitch, last_capture)
            pitch = (self.head_calibration.signed_pitch(pitch)
                     if control_pitch is not None else None)
            # The iris model only classifies horizontal gaze. It can abstain
            # during a valid head tilt without blocking scrolling.
            self._action_capture_time = last_capture
            scroll_state, scroll_spd = self.mapper.update_frame(gaze, control_pitch, eye_open=eye_open, blink=single)
            if task_view != (self.mapper.mode == MODE_APPSW):
                self.detector.reset_gaze()
            if self.mapper.input_paused:
                scroll_state = "PAUSED"
            elif self.head_calibration is None:
                scroll_state = "CALIBRATE"
            elif not head_scroll.armed:
                scroll_state = "CENTER"

            # ── FPS ───────────────────────────────────────────────────────
            ts_buf.append(t_frame)
            fps = ((len(ts_buf) - 1) / (ts_buf[-1] - ts_buf[0] + 1e-9)
                   if len(ts_buf) > 1 else 0.0)

            # ── Disable-progress ──────────────────────────────────────────
            if self.mapper._eye_close_start is not None:
                elapsed  = time.monotonic() - self.mapper._eye_close_start
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
                "gaze":           gaze or "—",
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
            self._draw_hud(frame, gaze or "—", pitch, scroll_state,
                           scroll_spd, close_pr, fps)

            # ── Display (imshow + waitKey MUST be on same thread) ─────────
            cv2.imshow("iGAZE v3 - Camera Feed", frame)
            if cv2.waitKey(1) & 0xFF == ord('q'):
                break

    # ──────────────────────────────────────────────────────────────────────
    # HUD renderer
    # ──────────────────────────────────────────────────────────────────────

    def _draw_hud(self, frame, gaze, pitch, scroll_state,
                  scroll_spd, close_pr, fps):
        """Render status overlay onto the camera feed frame."""
        mapper    = self.mapper
        h_px, w_px = frame.shape[:2]

        control = "PAUSED" if mapper.input_paused else "ON" if mapper.enabled else "OFF"
        ctrl_col   = (0, 220, 80) if control == "ON" else (30, 30, 220)
        mode_col   = (0, 160, 255)  if mapper.mode == MODE_APPSW  else (160, 160, 160)
        pitch_str  = f"{pitch:+.1f}" if pitch is not None else "n/a"
        n_blinks   = len(mapper._blink_times)

        lines = [
            (f"Gaze:    {gaze}",                           (0, 200, 255)),
            (f"Pitch:   {pitch_str} deg",                  (0, 200, 200)),
            (f"Scroll:  {scroll_state}  {scroll_spd}%",   (200, 200,   0)),
            (f"Blinks:  {n_blinks}/3",                     (200, 255,   0)),
            (f"Mode:    {mapper.mode.upper()}",             mode_col),
            (f"Control: {control}", ctrl_col),
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
        if (self._worker is not None and self._worker.is_alive()) or self._camera_lock.locked():
            return
        self.stop_event.clear()
        self.mapper.mode = MODE_NORMAL
        self.mapper.enable()
        self.state.update(running=True, error="", emergency_stop=False, paused=False,
                          mode=MODE_NORMAL, enabled=True)
        self._worker = threading.Thread(target=self._tracking_loop, daemon=True,
                                        name="iGAZE-tracking")
        self._worker.start()

    def stop_tracking(self):
        self.stop_event.set()
        self.mapper.set_input_paused(True)

    def set_input_paused(self, paused):
        if self.state["running"] and not self.stop_event.is_set():
            self.mapper.set_input_paused(paused)
            self.state["paused"] = paused

    def cleanup(self):
        self.stop_tracking()
        if self._worker is not None:
            self._worker.join(timeout=2.0)
        if self._camera_lock.acquire(blocking=False):
            try:
                self.tracker.release()
            finally:
                self._camera_lock.release()

    # ══════════════════════════════════════════════════════════════════════
    # Live sensitivity adjustment (called from GUI)
    # ══════════════════════════════════════════════════════════════════════

    def sensitivity(self):
        return {
            "scroll_dead_zone": self.mapper.head_dead_zone,
            "gaze_hold_seconds": self.mapper.gaze_hold,
            "triple_max_window_ms": self.mapper.triple_win * 1000,
            "disable_hold_sec": self.mapper.disable_hold_sec,
        }

    def apply_sensitivity(self, settings: dict, *, persist=True):
        """
        Apply sensitivity settings live to the running GestureMapper.

        Parameters (all optional — only keys present are applied)
        ──────────────────────────────────────────────────────────
        scroll_dead_zone     : float  degrees   (mapper.head_dead_zone)
        gaze_hold_seconds    : float  seconds   (mapper.gaze_hold)
        triple_max_window_ms : float  ms        (mapper.triple_win in seconds)
        disable_hold_sec     : float  seconds   (mapper.disable_hold_sec)
        """
        if not isinstance(settings, dict):
            return
        valid = {}
        for key, (low, high) in SENSITIVITY_RANGES.items():
            if key not in settings:
                continue
            try:
                value = float(settings[key])
            except (TypeError, ValueError, OverflowError):
                continue
            if math.isfinite(value) and low <= value <= high:
                valid[key] = value
        settings = valid
        if persist:
            self.store.set("sensitivity", self.sensitivity() | settings)
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
    from igaze.ui import iGazeGUI            # imported here to keep gui optional
    gui = iGazeGUI(app)
    gui.run()                           # blocks on tk.mainloop()


if __name__ == "__main__":
    main()
