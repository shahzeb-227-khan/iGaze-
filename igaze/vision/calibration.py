"""
calibration.py  —  iGAZE v3
══════════════════════════════════════════════════════════════════════════════

Adaptive calibration for:
  1. EAR baseline  (open-eye EAR per user)
  2. Gaze centre   (iris-ratio centre H/V)
  3. Blink sensitivity  (hard-close & soft-close fractions)

Data is persisted to a small JSON file so the user only calibrates once.

Usage
─────
    cal = Calibration()
    if not cal.load_calibration("igaze_cal.json"):
        cal.start_calibration(tracker, detector)
        cal.save_calibration("igaze_cal.json")
    cal.apply_to_detector(detector)
"""

import json
import math
import time
import numpy as np
import cv2
from igaze.vision.gaze_model import GazeModel, LABELS, eye_features


class Calibration:
    """
    Handles adaptive calibration of the EAR baseline, gaze centre,
    and blink sensitivity for a specific user.

    After calibrate() completes, call apply_to_detector(detector) to
    push all parameters into the GazeDetector instance.
    """

    def __init__(self):
        # Core EAR measurements
        self.ear_baseline:       float = 0.28   # 80th-pct open-eye EAR
        self.ear_open_p20:       float = 0.24   # 20th-pct  (natural low)
        self.ear_hard_fraction:  float = 0.45   # blink threshold fraction
        self.ear_soft_fraction:  float = 0.75   # soft-close threshold fraction

        # Gaze centre
        self.center_h:           float = 0.5
        self.center_v:           float = 0.5
        self.h_sensitivity:      float = 0.05
        self.v_sensitivity:      float = 0.05

        # Blink timing (kept here for persistence; applied to GestureMapper)
        self.blink_min_ms:       int   = 40
        self.min_inter_blink_ms: int   = 120

        self.is_calibrated:      bool  = False
        self.gaze_model = None

    # ══════════════════════════════════════════════════════════════════════
    # Calibration procedure
    # ══════════════════════════════════════════════════════════════════════

    def start_calibration(self, tracker, detector, duration: float = 4.0):
        """
        Full interactive calibration.

        Phase 1 (duration/2 s): eyes open, look straight ahead
            → collects EAR baseline + gaze centre
        Phase 2 (duration/2 s): blink naturally 2-3 times on cue
            → collects typical blink EAR trough for hard-fraction check

        Parameters
        ----------
        tracker  : EyeTracker instance
        detector : GazeDetector instance (used for iris ratio helper)
        duration : total seconds for the calibration window
        """
        half = duration / 2.0

        # ── Phase 1: open eyes ─────────────────────────────────────────────
        samples_ear: list[float] = []
        samples_h:   list[float] = []
        samples_v:   list[float] = []

        t0 = time.monotonic()
        print("[Phase 1/2] Keep eyes OPEN and look at the green dot…")

        while time.monotonic() - t0 < half:
            frame, landmarks, _, _, left_ear, right_ear = tracker.get_frame_data()
            if frame is None or landmarks is None:
                continue

            h_px, w_px   = frame.shape[:2]
            avg_h, avg_v = detector._get_iris_ratios(landmarks, w_px, h_px)
            avg_ear      = (left_ear + right_ear) / 2.0

            if avg_ear > 0.10 and avg_h is not None:
                samples_ear.append(avg_ear)
                samples_h.append(avg_h)
                samples_v.append(avg_v)

            self._draw_phase(frame, "LOOK AT THE DOT  (eyes OPEN)",
                             (time.monotonic() - t0) / half, (0, 255, 0))
            cv2.imshow("iGAZE – Calibration", frame)
            cv2.waitKey(1)

        # ── Phase 2: natural blinks ────────────────────────────────────────
        blink_troughs: list[float] = []
        in_blink              = False
        blink_low             = 1.0
        prev_ear              = 0.3
        phase2_start          = time.monotonic()

        print("[Phase 2/2] Blink naturally 2-3 times when ready…")
        soft_threshold        = np.percentile(samples_ear, 20) if samples_ear else 0.18

        while time.monotonic() - phase2_start < half:
            frame, landmarks, _, _, left_ear, right_ear = tracker.get_frame_data()
            if frame is None or landmarks is None:
                continue

            avg_ear = (left_ear + right_ear) / 2.0

            if avg_ear < soft_threshold and not in_blink:
                in_blink  = True
                blink_low = avg_ear
            elif avg_ear < blink_low and in_blink:
                blink_low = avg_ear
            elif avg_ear > soft_threshold and in_blink:
                blink_troughs.append(blink_low)
                in_blink = False

            elapsed_p2 = time.monotonic() - phase2_start
            self._draw_phase(frame, "BLINK naturally 2-3 times",
                             elapsed_p2 / half, (255, 255, 0))
            blink_label = f"Blinks detected: {len(blink_troughs)}"
            cv2.putText(frame, blink_label, (10, 80),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 200, 255), 2)
            cv2.imshow("iGAZE – Calibration", frame)
            cv2.waitKey(1)

        try:
            cv2.destroyWindow("iGAZE – Calibration")
        except Exception:
            pass

        # ── Compute parameters ─────────────────────────────────────────────
        if len(samples_ear) >= 10:
            self.ear_baseline   = float(np.percentile(samples_ear, 80))
            self.ear_open_p20   = float(np.percentile(samples_ear, 20))
            self.center_h       = float(np.mean(samples_h))
            self.center_v       = float(np.mean(samples_v))

            if len(blink_troughs) >= 2:
                avg_trough           = float(np.mean(blink_troughs))
                self.ear_hard_fraction = round(
                    (avg_trough / self.ear_baseline) * 1.15, 3)   # 15% margin
                self.ear_hard_fraction = max(0.30, min(self.ear_hard_fraction, 0.60))
                self.ear_soft_fraction = round(
                    (self.ear_open_p20 / self.ear_baseline) * 1.05, 3)
                self.ear_soft_fraction = max(0.60, min(self.ear_soft_fraction, 0.90))
            else:
                print("[Calibration] Too few blinks detected — using default fractions.")

            self.is_calibrated = True
            print(
                f"[Calibration] Done.\n"
                f"  EAR baseline={self.ear_baseline:.3f}  "
                f"hard_frac={self.ear_hard_fraction:.3f}  "
                f"soft_frac={self.ear_soft_fraction:.3f}\n"
                f"  Gaze centre  H={self.center_h:.3f}  V={self.center_v:.3f}"
            )
        else:
            print("[Calibration] Not enough samples — defaults kept.")

    # ══════════════════════════════════════════════════════════════════════
    # Apply to running detector
    # ══════════════════════════════════════════════════════════════════════

    def train_gaze_model(self, tracker):
        """Collect two separate passes with screen-coordinate direction labels."""
        training = {label: [] for label in LABELS}
        validation = {label: [] for label in LABELS}
        window = "iGAZE - Learn left and right"
        order = (*LABELS, "CENTER", "RIGHT", "LEFT")
        try:
            for step, label in enumerate(order):
                group = training if step < len(LABELS) else validation
                canvas = np.full((320, 640, 3), 27, dtype=np.uint8)
                x = {"LEFT": 100, "CENTER": 320, "RIGHT": 540}[label]
                phase = "Learn" if group is training else "Check"
                cv2.putText(canvas, f"{phase} {label.lower()} ({step + 1}/6)", (24, 38),
                            cv2.FONT_HERSHEY_SIMPLEX, .8, (255, 255, 255), 2)
                cv2.putText(canvas, "Follow the dot with your eyes. Keep your head still.",
                            (24, 76), cv2.FONT_HERSHEY_SIMPLEX, .53, (220, 220, 220), 1)
                cv2.circle(canvas, (x, 160), 14, (100, 245, 125), -1)
                cv2.imshow(window, canvas)
                start = time.monotonic()
                while True:
                    if cv2.waitKey(1) & 0xFF in (27, ord("q")):
                        raise ValueError("Calibration cancelled.")
                    frame, landmarks, _, _, left_ear, right_ear = tracker.get_frame_data()
                    elapsed = time.monotonic() - start
                    if elapsed >= 1.8:
                        break
                    if (elapsed >= .45 and frame is not None
                            and (left_ear + right_ear) / 2 >= .75 * self.ear_open_p20):
                        height, width = frame.shape[:2]
                        features = eye_features(landmarks, width, height)
                        if features is not None:
                            group[label].append(features)
                    preview = canvas.copy()
                    cv2.rectangle(preview, (24, 266), (616, 278), (75, 75, 75), -1)
                    cv2.rectangle(preview, (24, 266), (24 + int(592 * elapsed / 1.8), 278),
                                  (100, 245, 125), -1)
                    cv2.imshow(window, preview)
            self.gaze_model = GazeModel.fit(training, validation)
        finally:
            try:
                cv2.destroyWindow(window)
            except cv2.error:
                pass

    def apply_to_detector(self, detector) -> None:
        """Push calibrated parameters into a GazeDetector instance."""
        detector.ear_baseline      = self.ear_baseline
        detector.ear_open_p20      = self.ear_open_p20
        detector._ear_hard_thresh  = self.ear_baseline * self.ear_hard_fraction
        detector._ear_soft_thresh  = self.ear_baseline * self.ear_soft_fraction
        detector.EAR_THRESH        = detector._ear_hard_thresh
        detector.center_h          = self.center_h
        detector.center_v          = self.center_v
        detector.h_sensitivity     = self.h_sensitivity
        detector.v_sensitivity     = self.v_sensitivity
        detector.calibrated        = self.is_calibrated
        detector.gaze_model        = self.gaze_model
        detector.reset_tracking()
        # Refresh debug snapshot
        if hasattr(detector, "_debug"):
            detector._debug["ear_baseline"]    = detector.ear_baseline
            detector._debug["ear_hard_thresh"] = detector._ear_hard_thresh
            detector._debug["ear_soft_thresh"] = detector._ear_soft_thresh
        print("[Calibration] Parameters applied to GazeDetector.")

    # ══════════════════════════════════════════════════════════════════════
    # Persistence
    # ══════════════════════════════════════════════════════════════════════

    def to_dict(self):
        fields = ("ear_baseline", "ear_open_p20", "ear_hard_fraction", "ear_soft_fraction",
                  "center_h", "center_v", "h_sensitivity", "v_sensitivity", "blink_min_ms",
                  "min_inter_blink_ms", "is_calibrated")
        data = {field: getattr(self, field) for field in fields}
        data["gaze_model"] = self.gaze_model.to_dict() if self.gaze_model else None
        return data

    @classmethod
    def from_dict(cls, data):
        if not isinstance(data, dict) or data.get("is_calibrated") is not True:
            raise ValueError("No completed calibration.")
        required = ("ear_baseline", "ear_hard_fraction", "center_h", "center_v")
        if any(field not in data for field in required):
            raise ValueError("Saved calibration is incomplete.")
        result = cls()
        for field, default in result.to_dict().items():
            if field in ("is_calibrated", "gaze_model"):
                continue
            value = float(data.get(field, default))
            if not math.isfinite(value):
                raise ValueError(f"Invalid calibration value for {field}.")
            setattr(result, field, int(value) if isinstance(default, int) else value)
        if not (0 < result.ear_baseline <= 1 and 0 < result.ear_hard_fraction < 1
                and 0 < result.ear_soft_fraction <= 1 and 0 <= result.center_h <= 1
                and 0 <= result.center_v <= 1 and 0 < result.h_sensitivity <= 1
                and 0 < result.v_sensitivity <= 1):
            raise ValueError("Saved calibration is out of range.")
        result.is_calibrated = True
        if data.get("gaze_model") is not None:
            result.gaze_model = GazeModel.from_dict(data["gaze_model"])
        return result

    def save_calibration(self, filepath: str) -> bool:
        try:
            with open(filepath, "w", encoding="utf-8") as file:
                json.dump(self.to_dict(), file, indent=2)
            return True
        except OSError:
            return False

    def load_calibration(self, filepath: str) -> bool:
        """Import a calibration written by the earlier JSON-based app."""
        try:
            with open(filepath, encoding="utf-8-sig") as file:
                result = self.from_dict(json.load(file))
        except (OSError, ValueError, TypeError, OverflowError):
            return False
        self.__dict__.update(result.__dict__)
        return True

    def validate_calibration(self, test_ears: list[float]) -> dict:
        """
        Quick validation: pass a list of open-eye EAR samples collected
        after calibration.  Returns accuracy metrics.
        """
        if not test_ears:
            return {"error": "no samples"}

        arr            = np.array(test_ears, dtype=float)
        hard_thresh    = self.ear_baseline * self.ear_hard_fraction
        false_positive = float(np.mean(arr < hard_thresh))   # open frames below thresh
        mean_gap       = float(np.mean(arr) - hard_thresh)   # clearance above thresh

        return {
            "n_samples":           len(test_ears),
            "mean_open_ear":       round(float(np.mean(arr)), 4),
            "ear_baseline":        round(self.ear_baseline, 4),
            "hard_threshold":      round(hard_thresh, 4),
            "false_positive_rate": round(false_positive, 4),
            "mean_clearance":      round(mean_gap, 4),
        }

    # ══════════════════════════════════════════════════════════════════════
    # Internal UI helper
    # ══════════════════════════════════════════════════════════════════════

    @staticmethod
    def _draw_phase(frame, label: str, progress: float, dot_color):
        h, w = frame.shape[:2]
        overlay = frame.copy()
        cv2.rectangle(overlay, (0, 0), (w, h), (0, 0, 0), -1)
        cv2.addWeighted(overlay, 0.35, frame, 0.65, 0, frame)

        cv2.putText(frame, label,
                    (w // 2 - 220, h // 2 - 60),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.75, (0, 255, 255), 2)

        # Progress bar
        bar_x, bar_y, bar_w, bar_h2 = w // 2 - 150, h // 2 - 25, 300, 14
        cv2.rectangle(frame, (bar_x, bar_y),
                      (bar_x + bar_w, bar_y + bar_h2), (60, 60, 60), -1)
        fill = int(bar_w * min(progress, 1.0))
        cv2.rectangle(frame, (bar_x, bar_y),
                      (bar_x + fill, bar_y + bar_h2), (0, 200, 80), -1)

        # Target dot
        cv2.circle(frame, (w // 2, h // 2 + 30), 15, dot_color, -1)
        cv2.line(frame, (w // 2 - 40, h // 2 + 30),
                 (w // 2 + 40, h // 2 + 30), dot_color, 1)
        cv2.line(frame, (w // 2, h // 2 - 10),
                 (w // 2, h // 2 + 70), dot_color, 1)
