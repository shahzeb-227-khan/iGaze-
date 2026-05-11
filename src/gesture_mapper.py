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
   speed scales linearly to max_scroll_px_per_sec, giving natural control.
   Dead-zone raised to 10° to avoid accidental scroll from minor head nods.

5. Fractional-pixel accumulator
   Avoids rounding loss at slow speeds; whole-pixel chunks are flushed
   each frame, giving smooth scroll at any speed.

6. App-Switcher suspends scrolling
   Head pitch is ignored while Win+Tab is open so head movements while
   reading the app list don't accidentally trigger a scroll.
"""

import time

try:
    import pyautogui
    pyautogui.PAUSE    = 0.0
    pyautogui.FAILSAFE = False
    _PYAUTOGUI = True
except ImportError:
    _PYAUTOGUI = False

from gaze_detector import GAZE_LEFT, GAZE_RIGHT, GAZE_CENTER

# ── Modes ──────────────────────────────────────────────────────────────────
MODE_NORMAL = "normal"
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
    max_scroll_px_per_sec : float
        Peak scroll speed in pixels per second.
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
        gaze_hold_seconds:     float = 0.8,    # reduced from 1.5 s — snappier tab switch
        action_cooldown:       float = 0.9,    # reduced from 1.5 s — faster repeat actions
        head_dead_zone:        float = 10.0,   # raised from 5° — prevents micro-nod scroll
        head_max_pitch:        float = 20.0,
        max_scroll_px_per_sec: float = 300.0,  # halved from 600 — less sensitive
        triple_blink_window:   float = 0.9,    # tightened from 1.2 s — fewer false triples
        disable_hold_sec:      float = 5.0,    # 5 s intentional eye-close to toggle control
    ):
        self.gaze_hold        = gaze_hold_seconds
        self.cooldown         = action_cooldown
        self.head_dead_zone   = head_dead_zone
        self.head_max_pitch   = head_max_pitch
        self.max_scroll       = max_scroll_px_per_sec
        self.triple_win       = triple_blink_window
        self.disable_hold_sec = disable_hold_sec

        self.enabled          = True
        self.mode             = MODE_NORMAL

        # ── Gaze tracking ─────────────────────────────────────────────────
        self._current_gaze    = GAZE_CENTER
        self._gaze_start: float | None = None
        self._last_action_t   = 0.0

        # ── Head / scroll ─────────────────────────────────────────────────
        self._last_frame_t    = time.time()
        self._scroll_debt: float = 0.0

        # ── Triple-blink detector ─────────────────────────────────────────
        self._blink_times: list[float] = []   # timestamps of recent blinks
        self._mode_change_t: float = 0.0      # time of last mode switch (suppresses ghost blinks)
        self._BLINK_SUPPRESS_AFTER_MODE: float = 1.5  # seconds to ignore blinks after mode change

        # ── 10-second eye-close ───────────────────────────────────────────
        self._eye_close_start:  float | None = None
        self._disable_fired:    bool = False
        self._eye_closed_frames: int  = 0   # debounce counter

    # ══════════════════════════════════════════════════════════════════════
    # Public API  —  call once per frame from your main loop
    # ══════════════════════════════════════════════════════════════════════

    def update_gaze(self, gaze_direction: str):
        """
        Feed current gaze direction every frame.

        Normal mode  : LEFT/RIGHT → switch browser tab.
        App-sw mode  : LEFT/RIGHT → navigate app list via arrow keys.
        UP/DOWN      : always silently ignored.
        """
        if not self.enabled:
            return

        if gaze_direction not in (GAZE_LEFT, GAZE_RIGHT):
            self._current_gaze = GAZE_CENTER
            self._gaze_start   = None
            return

        now = time.time()
        if gaze_direction == self._current_gaze:
            if self._gaze_start and (now - self._gaze_start) >= self.gaze_hold:
                if (now - self._last_action_t) >= self.cooldown:
                    if self.mode == MODE_APPSW:
                        self._navigate_app(gaze_direction)
                    else:
                        self._switch_tab(gaze_direction)
                    self._last_action_t = now
                    self._gaze_start    = now   # restart for repeat actions
        else:
            self._current_gaze = gaze_direction
            self._gaze_start   = now

    def update_head_pitch(self, pitch: float | None):
        """
        Feed the smoothed head pitch (degrees) every frame.
        Positive = head tilted DOWN; negative = head tilted UP.
        No-op while App-Switcher is open.
        """
        now = time.time()
        dt  = now - self._last_frame_t
        self._last_frame_t = now

        if not self.enabled or self.mode == MODE_APPSW or pitch is None:
            self._scroll_debt = 0.0
            return

        abs_p = abs(pitch)
        if abs_p <= self.head_dead_zone:
            self._scroll_debt = 0.0
            return

        effective = abs_p - self.head_dead_zone
        max_eff   = max(self.head_max_pitch - self.head_dead_zone, 1e-6)
        speed_f   = min(effective / max_eff, 1.0)
        px        = speed_f * self.max_scroll * dt

        # head UP (pitch < 0) → positive scroll (up); head DOWN → negative
        self._scroll_debt += px if pitch < 0 else -px

        if abs(self._scroll_debt) >= 1.0:
            chunk = int(self._scroll_debt)
            self._scroll_debt -= chunk
            if _PYAUTOGUI:
                pyautogui.scroll(chunk)

    def update_blink(self, blink_occurred: bool):
        """
        Call every frame; pass True on the frame a completed blink is detected.

        Internally accumulates timestamps. When 3 blinks fall within
        triple_blink_window seconds, the triple-blink action fires.
        """
        if not self.enabled or not blink_occurred:
            return

        now = time.time()
        # Drop blinks outside the rolling window
        self._blink_times = [t for t in self._blink_times
                             if now - t <= self.triple_win]
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
        now = time.time()

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
        self.enabled      = True
        self._scroll_debt = 0.0

    # ══════════════════════════════════════════════════════════════════════
    # Internal helpers
    # ══════════════════════════════════════════════════════════════════════

    def _handle_triple_blink(self):
        if not _PYAUTOGUI:
            return
        # Suppress triple-blink if we just changed mode (prevents head-scroll
        # movements from accumulating ghost blinks during the transition).
        if time.time() - self._mode_change_t < self._BLINK_SUPPRESS_AFTER_MODE:
            return
        if self.mode == MODE_NORMAL:
            pyautogui.hotkey('win', 'tab')   # open Task View / App Switcher
            self.mode          = MODE_APPSW
            self._current_gaze = GAZE_CENTER
            self._gaze_start   = None
            self._scroll_debt  = 0.0
            self._blink_times.clear()        # discard any blinks from before switch
            self._mode_change_t = time.time()
        elif self.mode == MODE_APPSW:
            pyautogui.press('enter')         # confirm selected app
            self.mode          = MODE_NORMAL
            self._current_gaze = GAZE_CENTER
            self._gaze_start   = None
            self._blink_times.clear()        # discard blinks accumulated in switcher
            self._mode_change_t = time.time()

    def _switch_tab(self, gaze: str):
        if not _PYAUTOGUI:
            return
        if gaze == GAZE_LEFT:
            pyautogui.hotkey('ctrl', 'shift', 'tab')
        elif gaze == GAZE_RIGHT:
            pyautogui.hotkey('ctrl', 'tab')

    def _navigate_app(self, gaze: str):
        if not _PYAUTOGUI:
            return
        pyautogui.press('left' if gaze == GAZE_LEFT else 'right')

    def _disable_control(self):
        """Toggle eye control on/off."""
        if self.mode == MODE_APPSW and _PYAUTOGUI:
            pyautogui.press('escape')
            self.mode = MODE_NORMAL
        self.enabled      = not self.enabled  # Toggle instead of just disabling
        self._scroll_debt = 0.0


# ══════════════════════════════════════════════════════════════════════════
# Integration demo  (python gesture_mapper.py)
# ══════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    import cv2
    from eye_tracker   import EyeTracker
    from gaze_detector import GazeDetector
    from head_pose     import HeadPoseEstimator

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
        max_scroll_px_per_sec = 300.0,  # halved: less accidental scroll
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
                    (time.time() - mapper._eye_close_start) / mapper.disable_hold_sec,
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