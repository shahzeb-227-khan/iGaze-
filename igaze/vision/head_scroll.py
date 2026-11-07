"""User-labelled head posture references for scroll intent."""
import math
from collections import deque
from dataclasses import asdict, dataclass
from statistics import median
import time
from textwrap import wrap

import cv2
import numpy as np

from igaze.vision.head_pose import HeadPoseEstimator


def angle_delta(pitch, neutral):
    return (pitch - neutral + 180) % 360 - 180


@dataclass(frozen=True)
class HeadScrollCalibration:
    neutral: float
    up: float
    down: float

    def __post_init__(self):
        if not all(math.isfinite(value) for value in (self.neutral, self.up, self.down)):
            raise ValueError("Invalid head calibration.")
        up = angle_delta(self.up, self.neutral)
        down = angle_delta(self.down, self.neutral)
        if up * down >= 0 or not all(7 <= abs(value) <= 45 for value in (up, down)):
            raise ValueError("Tilt up and down a little farther than neutral, then retry.")

    @classmethod
    def fit(cls, samples):
        model = cls(*(stable_pitch(samples[label]) for label in ("neutral", "up", "down")))
        if abs(angle_delta(stable_pitch(samples["centre check"]), model.neutral)) > model.neutral_radius:
            raise ValueError("The final centre posture differed from your starting posture. Retry head calibration.")
        return model

    @property
    def neutral_radius(self):
        nearest = min(abs(angle_delta(value, self.neutral)) for value in (self.up, self.down))
        return min(4.0, .35 * nearest)

    def signed_pitch(self, pitch):
        """Positive always means the user's labelled down tilt."""
        sign = 1 if angle_delta(self.down, self.neutral) > 0 else -1
        return sign * angle_delta(pitch, self.neutral)

    def to_dict(self):
        return {"version": 1, **asdict(self)}

    @classmethod
    def from_dict(cls, data):
        if not isinstance(data, dict) or data.get("version") != 1:
            raise ValueError("No compatible head calibration.")
        return cls(*(float(data[key]) for key in ("neutral", "up", "down")))


class HeadScrollGate:
    def __init__(self, calibration):
        self.calibration = calibration
        self.reset()

    def reset(self):
        self.armed = False
        self.neutral_since = None

    def update(self, pitch, now):
        if self.calibration is None or pitch is None or not math.isfinite(pitch):
            self.reset()
            return None
        delta = self.calibration.signed_pitch(pitch)
        target = self.calibration.down if delta >= 0 else self.calibration.up
        travel = abs(angle_delta(target, self.calibration.neutral))
        if abs(delta) > min(50, max(25, 1.9 * travel)):
            self.reset()
            return None
        if not self.armed:
            if abs(delta) > self.calibration.neutral_radius:
                self.neutral_since = None
                return None
            if self.neutral_since is None:
                self.neutral_since = now
            self.armed = now - self.neutral_since >= .30
            if not self.armed:
                return None
        if abs(delta) <= 5:
            return 0.0
        # Map each comfortable, labelled tilt to the same control strength.
        return 18 * delta / travel


HEAD_STAGES = (
    ("Neutral", "Sit naturally and face the screen."),
    ("Look up", "Tilt your chin up slightly and hold."),
    ("Look down", "Tilt your chin down slightly and hold."),
    ("Check neutral", "Return to your normal resting posture."),
)


class UnstableHeadPose(ValueError):
    pass


def stable_pitch(values):
    if len(values) < 6:
        raise ValueError("Keep your face visible so the camera can collect enough samples.")
    origin = values[0]
    offsets = [angle_delta(value, origin) for value in values]
    middle = median(offsets)
    if median(abs(value - middle) for value in offsets) > 2.5:
        raise UnstableHeadPose("Keep your head still during capture.")
    return origin + middle


class HeadCalibrationProgress:
    """Keep accepted steps while the current step is retried."""

    def __init__(self):
        self.points = []

    def accept(self, values):
        pitch = stable_pitch(values)
        step = len(self.points)
        if step in (1, 2):
            offset = angle_delta(pitch, self.points[0])
            if abs(offset) < 7:
                direction = "up" if step == 1 else "down"
                raise ValueError(f"Tilt your chin a little farther {direction}, then capture again.")
            if abs(offset) > 45:
                raise ValueError("Use a smaller, comfortable tilt and capture again.")
            if step == 2 and offset * angle_delta(self.points[1], self.points[0]) >= 0:
                raise ValueError("Tilt down in the opposite direction to your up pose.")
        if step == 3:
            calibration = HeadScrollCalibration(*self.points)
            if abs(angle_delta(pitch, calibration.neutral)) > calibration.neutral_radius:
                raise ValueError("Return to your starting posture, then capture again.")
            self.points.append(pitch)
            return calibration
        self.points.append(pitch)
        return None

    def back(self):
        if self.points:
            self.points.pop()


def capture_head_calibration(tracker):
    window = "iGAZE - Calibrate Head Scroll"
    progress = HeadCalibrationProgress()
    pose = HeadPoseEstimator(smooth_alpha=.30)
    actions = deque()
    samples = deque()
    started = None
    notice = ""
    buttons = {"capture": (30, 344, 290, 402), "back": (306, 344, 446, 402),
               "cancel": (462, 344, 612, 402)}

    def on_mouse(event, x, y, _flags, _userdata):
        if event == cv2.EVENT_LBUTTONUP:
            for action, (left, top, right, bottom) in buttons.items():
                if left <= x <= right and top <= y <= bottom:
                    actions.append(action)
                    break

    def render(frame, visible, collecting, fraction):
        step = len(progress.points)
        title, prompt = HEAD_STAGES[step]
        canvas = np.full((432, 820, 3), (26, 23, 22), dtype=np.uint8)
        white = (247, 244, 238)
        green = (155, 233, 130)
        cv2.putText(canvas, f"HEAD SCROLL  /  STEP {step + 1} OF 4", (30, 47),
                    cv2.FONT_HERSHEY_SIMPLEX, .69, green, 2, cv2.LINE_AA)
        cv2.putText(canvas, title, (30, 103), cv2.FONT_HERSHEY_SIMPLEX, 1.19, white, 2, cv2.LINE_AA)
        cv2.putText(canvas, prompt, (30, 150), cv2.FONT_HERSHEY_SIMPLEX, .62, white, 1, cv2.LINE_AA)
        hint = ("Hold still while iGAZE checks your position." if collecting else
                "Get comfortable, then click Capture or press Space.")
        cv2.putText(canvas, hint, (30, 189), cv2.FONT_HERSHEY_SIMPLEX, .57, white, 1, cv2.LINE_AA)
        if frame is not None:
            canvas[61:211, 586:786] = cv2.resize(frame, (200, 150))
        cv2.putText(canvas, "Face visible" if visible else "Face not detected", (586, 235),
                    cv2.FONT_HERSHEY_SIMPLEX, .51, green if visible else (105, 175, 255), 1, cv2.LINE_AA)
        for index, line in enumerate(wrap(notice, 52)):
            cv2.putText(canvas, line, (30, 234 + index * 24),
                        cv2.FONT_HERSHEY_SIMPLEX, .59, (105, 193, 255), 1, cv2.LINE_AA)
        cv2.rectangle(canvas, (30, 302), (790, 315), (65, 61, 58), -1)
        if collecting:
            cv2.rectangle(canvas, (30, 302), (30 + round(760 * fraction), 315), green, -1)
        retry = notice and not notice.startswith("Position saved")
        labels = {"capture": "Capturing..." if collecting else "Retry capture" if retry else "Capture",
                  "back": "Back", "cancel": "Cancel"}
        for action, (left, top, right, bottom) in buttons.items():
            active = action == "cancel" or (not collecting and (action != "back" or step))
            colour = (98, 153, 71) if action == "capture" and active else (67, 60, 56)
            cv2.rectangle(canvas, (left, top), (right, bottom), colour, -1)
            cv2.putText(canvas, labels[action], (left + 17, top + 37),
                        cv2.FONT_HERSHEY_SIMPLEX, .69, white if active else (152, 145, 142), 1, cv2.LINE_AA)
        cv2.imshow(window, canvas)

    try:
        cv2.namedWindow(window, cv2.WINDOW_AUTOSIZE)
        render(None, False, False, 0)
        cv2.setMouseCallback(window, on_mouse)
        while True:
            key = cv2.waitKey(15) & 0xFF
            if key in (27, ord("q")):
                return None
            try:
                if cv2.getWindowProperty(window, cv2.WND_PROP_VISIBLE) < 1:
                    return None
            except cv2.error:
                return None
            if key in (32, 13):
                actions.append("capture")
            now = time.monotonic()
            while actions:
                action = actions.popleft()
                if action == "cancel":
                    return None
                if started is None and action == "back":
                    progress.back()
                    notice = ""
                elif started is None and action == "capture":
                    started = now
                    samples.clear()
                    notice = ""
                    pose.reset()

            frame, landmarks, *_ = tracker.get_frame_data()
            now = time.monotonic()
            visible = frame is not None and landmarks is not None
            elapsed = now - started if started is not None else 0.0
            if started is not None:
                if visible:
                    pitch = pose.update(frame, landmarks)
                    if elapsed >= .55 and pitch is not None and math.isfinite(pitch):
                        samples.append((now, pitch))
                else:
                    pose.reset()
                    samples.clear()
                while samples and now - samples[0][0] > 1.30:
                    samples.popleft()
                stable_window = len(samples) >= 6 and samples[-1][0] - samples[0][0] >= .90
                if stable_window or elapsed >= 4.0:
                    try:
                        if not stable_window:
                            raise ValueError("Keep your face visible and still, then retry this step.")
                        calibration = progress.accept([value for _, value in samples])
                    except UnstableHeadPose as exc:
                        notice = str(exc)
                        if elapsed >= 4.0:
                            started = None
                    except ValueError as exc:
                        notice = str(exc)
                        started = None
                    else:
                        if calibration is not None:
                            return calibration
                        started = None
                        notice = "Position saved. Capture the next pose when ready."
            render(frame, visible, started is not None, min(1, elapsed / 1.55))
    finally:
        try:
            cv2.destroyWindow(window)
        except cv2.error:
            pass
