"""
gesture_mapper.py  — iGAZE v3
══════════════════════════════════════════════════════════════════════════════

GESTURE MAP  (4 gestures only)
────────────────────────────────────────────────────────────────────────────
  Gaze LEFT / RIGHT      →  Switch browser tab (Ctrl+Shift+Tab / Ctrl+Tab)

  Triple blink           →  Open App-Switcher (Win+Tab)
    then gaze LEFT/RIGHT →  Navigate apps
    then triple blink    →  Open / confirm selected app

  Head tilt UP / DOWN    →  Continuous scrolling (speed ∝ tilt angle)

  Eyes closed ≥ 10 s     →  Disable eye control (toggle OFF)

DESIGN DECISIONS
────────────────
1. Triple-blink window (0.9 s) — tightened from 1.2 s
   Three blinks must land within a tight rolling window so casual blinking
   never accumulates into a false triple. The window resets on any gap.

2. 10-second disable fires on EYE OPEN, not during hold
   The event is confirmed when the user intentionally opens their eyes,
   preventing re-fire if the eye flutters just before fully opening.
   A debounce of EYE_CLOSE_DEBOUNCE_FRAMES frames is required before the
   timer even starts, so brief EAR dips from head-tilt don't count.
   A fired-flag guards against double-firing.

3. Scrolling on head-pitch only — gaze-Y ignored
   Natural reading involves large vertical eye movements. By routing
   scroll through the head channel these never cause false scrolls
   (Midas Touch fix).

4. Dead-zone + proportional speed
   Pitch within ±dead_zone degrees is neutral (no scroll). Beyond that,
   speed scales linearly to max_scroll_steps_per_sec, giving natural control.
   Dead-zone raised to 10° to avoid accidental scroll from minor head nods.

5. Capped wheel cadence
   A deliberate tilt sends one notch, followed by a bounded repeat rate.

6. App-Switcher suspends scrolling
   Head pitch is ignored while Win+Tab is open so head movements while
   reading the app list don't accidentally trigger a scroll.
"""

import math
import sys
import threading
import time

try:
    import pyautogui
    _PYAUTOGUI = True
except ImportError:
    _PYAUTOGUI = False

from igaze.vision.gaze_detector import GAZE_LEFT, GAZE_RIGHT, GAZE_CENTER, GAZE_UP, GAZE_DOWN

# ── Modes ──────────────────────────────────────────────────────────────────
MODE_NORMAL = "normal"


class DesktopInputStopped(RuntimeError):
    """The user activated the mouse-corner emergency stop."""
MODE_APPSW  = "app_sw"   # App-Switcher open, gaze L/R navigates apps


class GestureMapper:
    """
    Hybrid eye-gaze + head-movement gesture mapper — v3.

    Parameters
    ----------
    gaze_hold_seconds : float
        Seconds gaze must stay LEFT/RIGHT to fire a tab or app action.
    action_cooldown : float
        Minimum seconds between successive tab-switch / app-navigate actions.
    head_dead_zone : float
        Pitch range (±degrees) treated as neutral — no scroll.
    head_max_pitch : float
        Pitch angle (degrees) that yields maximum scroll speed.
    max_scroll_steps_per_sec : float
        Peak scroll speed in wheel notches per second.
    triple_blink_window : float
        Rolling window (seconds) in which 3 blinks must occur for a triple.
    disable_hold_sec : float
        Eye-close duration (seconds) that disables eye control on re-open.
    """

    # Minimum consecutive frames the eye must be below EAR threshold before
    # we consider it truly closed and start the disable timer.  At ~30 fps
    # this is ~0.27 s — enough to swallow blink artifacts from head tilts.
    _EYE_CLOSE_DEBOUNCE_FRAMES: int = 8

    def __init__(
        self,
        gaze_hold_seconds:     float = 0.50,
        action_cooldown:       float = 0.55,
        head_dead_zone:        float = 10.0,   # raised from 5° — prevents micro-nod scroll
        head_max_pitch:        float = 20.0,
        max_scroll_steps_per_sec: float = 3.0,
        head_hold_seconds:     float = .22,
        triple_blink_window:   float = 1.25,
        disable_hold_sec:      float = 5.0,    # 5 s intentional eye-close to toggle control
    ):
        self.gaze_hold        = gaze_hold_seconds
        self.cooldown         = action_cooldown
        self.head_dead_zone   = head_dead_zone
        self.head_max_pitch   = head_max_pitch
        self.max_scroll       = max_scroll_steps_per_sec
        self.head_hold_seconds = head_hold_seconds
        self.triple_win       = triple_blink_window
        self.disable_hold_sec = disable_hold_sec

        self.enabled          = True
        self.mode             = MODE_NORMAL
        self.actions_sent = 0
        self.on_action = None
        self._input_lock = threading.Lock()
        self._input_paused = False
        self._pause_version = 0
        self._seen_pause_version = 0
        self._needs_neutral = False
        self._last_nav_direction = None
        self._neutral_since = None
        self._gaze_pause_since = None

        # ── Gaze tracking ─────────────────────────────────────────────────
        self._current_gaze    = GAZE_CENTER
        self._gaze_start: float | None = None
        self._last_action_t   = 0.0

        # ── Head / scroll ─────────────────────────────────────────────────
        self._tilt_direction = 0
        self._scroll_active = False
        self._next_scroll_at = 0.0

        # ── Triple-blink detector ─────────────────────────────────────────
        self._blink_times: list[float] = []   # timestamps of recent blinks
        self._head_tilted_at = None
        self._mode_change_t: float = 0.0      # time of last mode switch (suppresses ghost blinks)
        self._BLINK_SUPPRESS_AFTER_MODE: float = .45

        # ── 10-second eye-close ───────────────────────────────────────────
        self._eye_close_start:  float | None = None
        self._disable_fired:    bool = False
        self._eye_closed_frames: int  = 0   # debounce counter

    # ══════════════════════════════════════════════════════════════════════
    # Public API  —  call once per frame from your main loop
    # ══════════════════════════════════════════════════════════════════════

    def update_frame(self, gaze, pitch, *, eye_open, blink):
        """Choose one command channel before updating its dwell timer."""
        if self._pause_version != self._seen_pause_version:
            self._seen_pause_version = self._pause_version
            self.reset_tracking()
            self._needs_neutral = True
            self._last_nav_direction = None
            self.mode = MODE_NORMAL
        if self._input_paused:
            return "PAUSED", 0

        mode, enabled = self.mode, self.enabled
        self.update_eye_state(eye_open)
        if self.mode != mode or self.enabled != enabled:
            self.reset_tracking()
            return "idle", 0

        now = time.monotonic()
        tilted = (self.enabled and pitch is not None
                  and math.isfinite(pitch) and abs(pitch) > self.head_dead_zone)
        continuing = (self._scroll_active and pitch is not None and math.isfinite(pitch)
                      and self._tilt_direction * pitch > .70 * self.head_dead_zone)
        if tilted or continuing:
            self._head_tilted_at = now
            self._blink_times.clear()
            self._clear_gaze_hold()
            scroll = self.update_head_pitch(pitch if eye_open else None)
            if self._scroll_active:
                self._needs_neutral = True
            return scroll

        self._reset_scroll()
        # Suppress a blink completed during the return from a head tilt.
        settled = self._head_tilted_at is None or now - self._head_tilted_at >= .12
        self.update_blink(blink and settled)
        if self.mode != mode:
            self._clear_gaze_hold()
            self._needs_neutral = False
            self._last_nav_direction = None
            self._last_action_t = now
            return "idle", 0
        if not settled or len(self._blink_times) >= 2:
            self._clear_gaze_hold()
            return "idle", 0

        before = self.actions_sent
        self.update_gaze(gaze, eyes_closed=not eye_open)
        if self.actions_sent != before:
            self._blink_times.clear()
        return "idle", 0

    def _clear_gaze_hold(self):
        self._neutral_since = None
        self._gaze_pause_since = None
        self._current_gaze = GAZE_CENTER
        self._gaze_start = None

    def update_gaze(self, gaze_direction: str | None, *, eyes_closed=False):
        """Send one navigation command per hold. A blink does not rearm it."""
        now = time.monotonic()
        if self.enabled and (eyes_closed or gaze_direction is None):
            self._neutral_since = None
            if self._gaze_pause_since is None:
                self._gaze_pause_since = now
            elif now - self._gaze_pause_since > .25:
                self._gaze_start = None
            return
        if self._gaze_pause_since is not None:
            gap = now - self._gaze_pause_since
            if self._gaze_start is not None and gaze_direction == self._current_gaze and gap <= .25:
                self._gaze_start += gap
            else:
                self._gaze_start = None
            self._gaze_pause_since = None
        task_view = self.mode == MODE_APPSW
        if not self.enabled or gaze_direction not in (GAZE_LEFT, GAZE_RIGHT):
            self._current_gaze = GAZE_CENTER
            self._gaze_start = None
            if self.enabled and gaze_direction in (GAZE_CENTER, GAZE_UP, GAZE_DOWN):
                if self._neutral_since is None:
                    self._neutral_since = now
                if now - self._neutral_since >= .15:
                    self._needs_neutral = False
            else:
                self._neutral_since = None
            return
        self._neutral_since = None
        if self._needs_neutral and (not task_view or gaze_direction == self._last_nav_direction):
            return
        if gaze_direction != self._current_gaze or self._gaze_start is None:
            self._current_gaze = gaze_direction
            self._gaze_start = now
            return
        hold = min(self.gaze_hold, .32) if task_view else self.gaze_hold
        cooldown = min(self.cooldown, .34) if task_view else self.cooldown
        if now - self._gaze_start < hold or now - self._last_action_t < cooldown:
            return
        if self.mode == MODE_APPSW:
            self._navigate_app(gaze_direction)
        else:
            self._switch_tab(gaze_direction)
        self._last_action_t = now
        self._gaze_start = None
        self._needs_neutral = True
        self._last_nav_direction = gaze_direction

    def update_head_pitch(self, pitch: float | None) -> tuple[str, int]:
        """Send one wheel notch after a deliberate tilt, then repeat at a capped rate."""
        if not self.enabled or self.mode == MODE_APPSW or pitch is None or not math.isfinite(pitch):
            self._reset_scroll()
            return "idle", 0

        now = time.monotonic()
        direction = 1 if pitch > 0 else -1
        continuing = self._scroll_active and direction == self._tilt_direction
        # A smaller release zone avoids flickering around the entry threshold.
        dead_zone = self.head_dead_zone * .70 if continuing else self.head_dead_zone
        if abs(pitch) <= dead_zone:
            self._reset_scroll()
            return "idle", 0

        if direction != self._tilt_direction:
            self._tilt_direction = direction
            self._scroll_active = False
            self._next_scroll_at = now + self.head_hold_seconds

        pitch_range = max(1, self.head_max_pitch - self.head_dead_zone)
        strength = min(1, max(0, (abs(pitch) - self.head_dead_zone) / pitch_range))
        rate = self.max_scroll * (.60 + .40 * strength)
        if now >= self._next_scroll_at:
            # PyAutoGUI's Windows backend forwards raw wheel delta. One
            # physical wheel notch is 120 on Windows, and one click elsewhere.
            wheel_notch = 120 if sys.platform == "win32" else 1
            if _PYAUTOGUI:
                self._send("scroll_down" if direction > 0 else "scroll_up",
                           pyautogui.scroll, -direction * wheel_notch)
            self._scroll_active = True
            self._next_scroll_at = now + 1 / rate

        if not self._scroll_active:
            return "HOLD", 0
        return ("DOWN" if direction > 0 else "UP"), round(100 * rate / self.max_scroll)

    def _reset_scroll(self):
        self._tilt_direction = 0
        self._scroll_active = False
        self._next_scroll_at = 0.0

    def reset_tracking(self, *, keep_completed_blinks=False):
        self._clear_gaze_hold()
        self._reset_scroll()
        self._head_tilted_at = None
        if not keep_completed_blinks:
            self._blink_times.clear()
        self._eye_close_start = None
        self._eye_closed_frames = 0
        self._disable_fired = False

    def update_blink(self, blink_occurred: bool):
        """
        Call every frame; pass True on the frame a completed blink is detected.

        Internally accumulates timestamps. When 3 blinks fall within
        triple_blink_window seconds, the triple-blink action fires.
        """
        now = time.monotonic()
        # Drop blinks outside the rolling window
        self._blink_times = [t for t in self._blink_times
                             if now - t <= self.triple_win]
        if not self.enabled or not blink_occurred or now - self._mode_change_t < self._BLINK_SUPPRESS_AFTER_MODE:
            return
        self._blink_times.append(now)

        if len(self._blink_times) >= 3:
            self._blink_times.clear()
            self._handle_triple_blink()

    def update_eye_state(self, eye_open: bool):
        """
        Call every frame with True when the eye is open (avg EAR ≥ threshold).

        Fires WHEN THE EYE RE-OPENS after being closed for ≥ disable_hold_sec.

        A debounce of _EYE_CLOSE_DEBOUNCE_FRAMES consecutive below-threshold
        frames is required before the close timer starts.  This prevents a
        brief EAR dip caused by tilting the head upward from being treated as
        a real eye closure.
        """
        now = time.monotonic()

        if not eye_open:
            # Increment debounce counter; only start the timer once we've seen
            # enough consecutive closed frames to be sure it's a real closure.
            self._eye_closed_frames += 1
            if self._eye_closed_frames >= self._EYE_CLOSE_DEBOUNCE_FRAMES:
                if self._eye_close_start is None:
                    self._eye_close_start = now
                    self._disable_fired   = False
        else:
            # Eye just opened — evaluate the close duration
            if (self._eye_close_start is not None
                    and not self._disable_fired):
                elapsed = now - self._eye_close_start
                if elapsed >= self.disable_hold_sec:
                    self._disable_fired = True
                    self._disable_control()

            # Reset both debounce counter AND timer on any open frame
            self._eye_close_start  = None
            self._disable_fired    = False
            self._eye_closed_frames = 0

    # Re-enable from outside (e.g. a GUI button or keyboard shortcut)
    def enable(self):
        self.set_input_paused(False)
        self._seen_pause_version = self._pause_version
        self._needs_neutral = False
        self.reset_tracking()
        self.enabled      = True
        self._reset_scroll()

    # ══════════════════════════════════════════════════════════════════════
    # Internal helpers
    # ══════════════════════════════════════════════════════════════════════

    @property
    def input_paused(self):
        return self._input_paused

    @property
    def input_epoch(self):
        return self._pause_version

    def set_input_paused(self, paused):
        with self._input_lock:
            if self._input_paused != paused:
                self._input_paused = paused
                self._pause_version += 1

    def _send(self, kind, action, *args):
        with self._input_lock:
            if self._input_paused or self._pause_version != self._seen_pause_version:
                return False
            try:
                action(*args, _pause=False)
            except pyautogui.FailSafeException as exc:
                raise DesktopInputStopped(
                    "Mouse-corner emergency stop. Move the pointer away from the corner, "
                    "then click Start Tracking when ready."
                ) from exc
            self.actions_sent += 1
            if self.on_action is not None:
                self.on_action(kind, self.mode)
            return True

    def _handle_triple_blink(self):
        if not _PYAUTOGUI:
            return
        # Suppress triple-blink if we just changed mode (prevents head-scroll
        # movements from accumulating ghost blinks during the transition).
        if time.monotonic() - self._mode_change_t < self._BLINK_SUPPRESS_AFTER_MODE:
            return
        if self.mode == MODE_NORMAL:
            if not self._send("open_task_view", pyautogui.hotkey, 'win', 'tab'):
                return
            self.mode          = MODE_APPSW
            self._current_gaze = GAZE_CENTER
            self._gaze_start   = None
            self._reset_scroll()
            self._blink_times.clear()        # discard any blinks from before switch
            self._mode_change_t = time.monotonic()
        elif self.mode == MODE_APPSW:
            if not self._send("confirm_app", pyautogui.press, 'enter'):
                return
            self.mode          = MODE_NORMAL
            self._current_gaze = GAZE_CENTER
            self._gaze_start   = None
            self._blink_times.clear()        # discard blinks accumulated in switcher
            self._mode_change_t = time.monotonic()

    def _switch_tab(self, gaze: str):
        if not _PYAUTOGUI:
            return
        if gaze == GAZE_LEFT:
            self._send("previous_tab", pyautogui.hotkey, 'ctrl', 'shift', 'tab')
        elif gaze == GAZE_RIGHT:
            self._send("next_tab", pyautogui.hotkey, 'ctrl', 'tab')

    def _navigate_app(self, gaze: str):
        if _PYAUTOGUI and gaze in (GAZE_LEFT, GAZE_RIGHT):
            self._send("app_" + gaze.lower(), pyautogui.press, gaze.lower())

    def _disable_control(self):
        """Toggle eye control on/off."""
        if self.mode == MODE_APPSW and _PYAUTOGUI:
            self._send("cancel_task_view", pyautogui.press, 'escape')
            self.mode = MODE_NORMAL
        self.enabled      = not self.enabled  # Toggle instead of just disabling
        self._reset_scroll()


# ══════════════════════════════════════════════════════════════════════════
# Integration demo  (python gesture_mapper.py)
# ══════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    import cv2
    from igaze.vision.eye_tracker   import EyeTracker
    from igaze.vision.gaze_detector import GazeDetector
    from igaze.vision.head_pose     import HeadPoseEstimator

    print("\n" + "=" * 60)
    print("         iGAZE v3  —  4-Gesture Control")
    print("=" * 60)
    print()
    print("  ◀▶  Gaze LEFT / RIGHT      →  Switch browser tab")
    print("  ◉◉◉ Triple blink           →  Open App Switcher")
    print("        then gaze L / R      →  Navigate apps")
    print("        then triple blink    →  Open selected app")
    print("  ↕   Head tilt UP / DOWN    →  Scroll (continuous)")
    print("  ●   Eyes closed 10 s       →  Disable eye control")
    print()
    print("  Press 'q' to quit.\n")

    tracker   = EyeTracker()
    detector  = GazeDetector()
    head_pose = HeadPoseEstimator(smooth_alpha=0.15)  # stronger smoothing reduces jitter
    mapper    = GestureMapper(
        gaze_hold_seconds     = 0.8,    # snappier tab switching
        action_cooldown       = 0.9,    # faster repeat
        head_dead_zone        = 10.0,   # raised: ignore small head nods
        head_max_pitch        = 20.0,
        max_scroll_steps_per_sec = 3.0,
        triple_blink_window   = 0.9,    # tightened: fewer false Win+Tab
        disable_hold_sec      = 5.0,    # 5 s intentional hold to disable
    )

    print("[1/2] Calibrating gaze — look straight ahead ...")
    time.sleep(2)
    detector.calibrate(tracker, duration=3)
    print("[2/2] Running.\n")

    try:
        while True:
            frame, landmarks, left_eye, right_eye, left_ear, right_ear = \
                tracker.get_frame_data()
            if frame is None:
                break

            # Head pose (use landmarks from EyeTracker)
            pitch    = head_pose.update(frame, landmarks)

            # Blink + gaze
            single, _, _ = detector.update_blink(left_ear, right_ear)
            gaze         = detector.get_gaze_direction(
                               frame, landmarks, left_eye, right_eye)

            avg_ear  = (left_ear + right_ear) / 2.0
            eye_open = avg_ear >= detector.EAR_THRESH

            # Feed mapper (order matters: eye state before blink)
            mapper.update_eye_state(eye_open)
            mapper.update_blink(single)
            mapper.update_head_pitch(pitch)
            mapper.update_gaze(gaze)

            # ── Overlay ────────────────────────────────────────────────────
            frame = tracker.draw_debug(
                frame, landmarks, left_eye, right_eye, left_ear, right_ear)

            ctrl_col  = (0, 255, 0)   if mapper.enabled           else (0, 0, 255)
            mode_col  = (255, 150, 0) if mapper.mode == MODE_APPSW else (150, 150, 150)
            pitch_str = f"{pitch:+.1f}°" if pitch is not None else "n/a"

            stats = [
                ("Gaze",    gaze,                                (0, 200, 255)),
                ("Pitch",   pitch_str,                           (0, 200, 200)),
                ("Blinks",  f"{len(mapper._blink_times)}/3",    (200, 255, 0)),
                ("Mode",    mapper.mode.upper(),                 mode_col),
                ("Control", "ON" if mapper.enabled else "OFF",   ctrl_col),
            ]
            for i, (lbl, val, col) in enumerate(stats):
                cv2.putText(frame, f"{lbl}: {val}",
                            (10, 90 + i * 25),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, col, 2)

            # 10-second progress bar
            if mapper._eye_close_start is not None:
                prog = min(
                    (time.monotonic() - mapper._eye_close_start) / mapper.disable_hold_sec,
                    1.0)
                bar_w = int(300 * prog)
                cv2.rectangle(frame, (10, 232), (310, 250), (40, 40, 40), -1)
                cv2.rectangle(frame, (10, 232), (10 + bar_w, 250), (0, 80, 255), -1)
                cv2.putText(frame,
                            f"Disable: {prog * mapper.disable_hold_sec:.1f}s / {mapper.disable_hold_sec:.0f}s",
                            (10, 228),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 80, 255), 1)

            cv2.imshow("iGAZE v3  (press 'q' to quit)", frame)
            if cv2.waitKey(1) & 0xFF == ord('q'):
                break

    except KeyboardInterrupt:
        print("\n[Info] Interrupted.")
    finally:
        tracker.release()
        cv2.destroyAllWindows()
        print("[Info] Closed.")
