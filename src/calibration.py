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
import os
import time
import numpy as np
import cv2


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

        t0 = time.time()
        print("[Phase 1/2] Keep eyes OPEN and look at the green dot…")

        while time.time() - t0 < half:
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
                             (time.time() - t0) / half, (0, 255, 0))
            cv2.imshow("iGAZE – Calibration", frame)
            cv2.waitKey(1)

        # ── Phase 2: natural blinks ────────────────────────────────────────
        blink_troughs: list[float] = []
        in_blink              = False
        blink_low             = 1.0
        prev_ear              = 0.3
        phase2_start          = time.time()

        print("[Phase 2/2] Blink naturally 2-3 times when ready…")
        soft_threshold        = np.percentile(samples_ear, 20) if samples_ear else 0.18

        while time.time() - phase2_start < half:
            frame, landmarks, _, _, left_ear, right_ear = tracker.get_frame_data()
            if frame is None:
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

            elapsed_p2 = time.time() - phase2_start
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

    def apply_to_detector(self, detector) -> None:
        """Push calibrated parameters into a GazeDetector instance."""
        detector.ear_baseline      = self.ear_baseline
        detector._ear_hard_thresh  = self.ear_baseline * self.ear_hard_fraction
        detector._ear_soft_thresh  = self.ear_baseline * self.ear_soft_fraction
        detector.EAR_THRESH        = detector._ear_hard_thresh
        detector.center_h          = self.center_h
        detector.center_v          = self.center_v
        detector.calibrated        = self.is_calibrated
        # Refresh debug snapshot
        detector._debug["ear_baseline"]    = detector.ear_baseline
        detector._debug["ear_hard_thresh"] = detector._ear_hard_thresh
        detector._debug["ear_soft_thresh"] = detector._ear_soft_thresh
        print("[Calibration] Parameters applied to GazeDetector.")

    # ══════════════════════════════════════════════════════════════════════
    # Persistence
    # ══════════════════════════════════════════════════════════════════════

    def save_calibration(self, filepath: str) -> bool:
        """
        Serialise calibration data to JSON.

        Returns True on success.
        """
        data = {
            "ear_baseline":       self.ear_baseline,
            "ear_open_p20":       self.ear_open_p20,
            "ear_hard_fraction":  self.ear_hard_fraction,
            "ear_soft_fraction":  self.ear_soft_fraction,
            "center_h":           self.center_h,
            "center_v":           self.center_v,
            "h_sensitivity":      self.h_sensitivity,
            "v_sensitivity":      self.v_sensitivity,
            "blink_min_ms":       self.blink_min_ms,
            "min_inter_blink_ms": self.min_inter_blink_ms,
            "is_calibrated":      self.is_calibrated,
        }
        try:
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            print(f"[Calibration] Saved to {filepath}")
            return True
        except OSError as exc:
            print(f"[Calibration] Save failed: {exc}")
            return False

    def load_calibration(self, filepath: str) -> bool:
        """
        Load previously saved calibration from JSON.

        Returns True if the file exists and is valid, False otherwise.
        """
        if not os.path.isfile(filepath):
            return False
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)

            self.ear_baseline       = float(data["ear_baseline"])
            self.ear_open_p20       = float(data.get("ear_open_p20", 0.24))
            self.ear_hard_fraction  = float(data["ear_hard_fraction"])
            self.ear_soft_fraction  = float(data["ear_soft_fraction"])
            self.center_h           = float(data["center_h"])
            self.center_v           = float(data["center_v"])
            self.h_sensitivity      = float(data.get("h_sensitivity", 0.05))
            self.v_sensitivity      = float(data.get("v_sensitivity", 0.05))
            self.blink_min_ms       = int(data.get("blink_min_ms", 40))
            self.min_inter_blink_ms = int(data.get("min_inter_blink_ms", 120))
            self.is_calibrated      = bool(data.get("is_calibrated", False))

            print(
                f"[Calibration] Loaded from {filepath}  "
                f"(EAR={self.ear_baseline:.3f}  calibrated={self.is_calibrated})"
            )
            return self.is_calibrated

        except (KeyError, ValueError, json.JSONDecodeError) as exc:
            print(f"[Calibration] Load failed ({exc}) — will recalibrate.")
            return False

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
