import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from igaze.controller import iGazeApp
from igaze.controls import GestureMapper, MODE_APPSW, pyautogui
from igaze.storage import Store
from igaze.vision.calibration import Calibration
from igaze.vision.gaze_model import GazeModel, eye_features
from igaze.vision.head_scroll import HeadCalibrationProgress, HeadScrollCalibration, HeadScrollGate


class CoreTests(unittest.TestCase):
    def test_task_view_ignores_vertical_gaze_but_keeps_horizontal_navigation(self):
        mapper = GestureMapper()
        mapper.mode = MODE_APPSW
        now = [100.0]
        with (patch("igaze.controls.time.monotonic", side_effect=lambda: now[0]),
              patch("igaze.controls.pyautogui") as desktop):
            for t, direction in ((100, "DOWN"), (100.6, "DOWN"), (101, "UP"), (101.6, "UP")):
                now[0] = t
                mapper.update_frame(direction, 0, eye_open=True, blink=False)
            desktop.press.assert_not_called()
            for t in (102, 102.35):
                now[0] = t
                mapper.update_frame("LEFT", 0, eye_open=True, blink=False)
            desktop.press.assert_called_once_with("left", _pause=False)

    def test_mouse_corner_stops_tracking_and_explains_restart(self):
        with tempfile.TemporaryDirectory() as folder, patch("igaze.controller.EyeTracker"):
            app = iGazeApp(store=Store(Path(folder) / "igaze.sqlite3"))
            app.mapper.mode = MODE_APPSW
            app.state["running"] = True
            with (patch.object(app, "_run_tracking", side_effect=lambda: app.mapper._navigate_app("LEFT")),
                  patch.object(pyautogui, "press", side_effect=pyautogui.FailSafeException("corner")),
                  patch("igaze.controller.cv2.destroyAllWindows")):
                app._tracking_loop()
            self.assertFalse(app.state["running"])
            self.assertFalse(app.state["enabled"])
            self.assertTrue(app.state["emergency_stop"])
            self.assertIn("click Start Tracking", app.state["error"])
            self.assertTrue(pyautogui.FAILSAFE)

    def test_failed_head_capture_retries_current_step_without_discarding_points(self):
        wizard = HeadCalibrationProgress()
        wizard.accept([32] * 8)
        with self.assertRaisesRegex(ValueError, "still during capture"):
            wizard.accept([34, 48] * 4)
        self.assertEqual(wizard.points, [32])
        wizard.accept([44] * 8)
        with self.assertRaisesRegex(ValueError, "opposite"):
            wizard.accept([43] * 8)
        self.assertEqual(wizard.points, [32, 44])
        wizard.accept([20] * 8)
        with self.assertRaisesRegex(ValueError, "starting posture"):
            wizard.accept([42] * 8)
        calibration = wizard.accept([33] * 8)
        self.assertEqual(calibration, HeadScrollCalibration(32, 44, 20))

    def test_cancelled_head_capture_keeps_saved_calibration(self):
        with tempfile.TemporaryDirectory() as folder, patch("igaze.controller.EyeTracker"):
            store = Store(Path(folder) / "igaze.sqlite3")
            previous = HeadScrollCalibration(32, 44, 20)
            store.set("head_scroll", previous.to_dict())
            app = iGazeApp(store=store)
            with patch("igaze.controller.capture_head_calibration", return_value=None):
                self.assertFalse(app.run_head_calibration())
            self.assertEqual(app.head_calibration, previous)
            self.assertEqual(store.get("head_scroll"), previous.to_dict())
            self.assertFalse(app._camera_lock.locked())

    def test_saved_head_points_require_neutral_and_learn_inverted_pitch_sign(self):
        points = HeadScrollCalibration.fit({
            "neutral": [34] * 8, "up": [47] * 8, "down": [22] * 8,
            "centre check": [34.5] * 8,
        })
        with tempfile.TemporaryDirectory() as folder, patch("igaze.controller.EyeTracker"):
            path = Path(folder) / "igaze.sqlite3"
            Store(path).set("head_scroll", points.to_dict())
            app = iGazeApp(store=Store(path))
            gate = HeadScrollGate(app.head_calibration)
            self.assertIsNone(gate.update(47, 100))
            self.assertIsNone(gate.update(47, 101))
            self.assertIsNone(gate.update(34, 101.1))
            self.assertEqual(gate.update(34, 101.42), 0)
            self.assertEqual(gate.update(38, 101.46), 0)
            self.assertEqual(gate.update(22, 101.50), 18)
            self.assertEqual(gate.update(47, 101.55), -18)
            self.assertIsNone(gate.update(None, 101.60))
            self.assertIsNone(gate.update(47, 102))

    def test_scroll_and_triple_blink_do_not_also_emit_a_tab_command(self):
        mapper = GestureMapper()
        now = [100.0]
        with (patch("igaze.controls.time.monotonic", side_effect=lambda: now[0]),
              patch("igaze.controls.pyautogui") as desktop):
            for t, pitch, blink in ((100, 0, False), (100.3, 14, False),
                                    (100.55, 14, True), (100.75, 14, True),
                                    (100.80, 0, True), (101, 0, False), (101.6, 0, False),
                                    (102, 0, True), (102.3, 0, True)):
                now[0] = t
                mapper.update_frame("RIGHT", pitch, eye_open=True, blink=blink)
            desktop.scroll.assert_called_once()
            desktop.hotkey.assert_not_called()
            self.assertEqual(len(mapper._blink_times), 2)
            now[0] = 102.62
            mapper.update_frame("RIGHT", 0, eye_open=True, blink=True)
            desktop.hotkey.assert_called_once_with("win", "tab", _pause=False)
            desktop.press.assert_not_called()
            for t in (103.2, 103.75):
                now[0] = t
                mapper.update_frame("LEFT", 0, eye_open=True, blink=False)
            desktop.press.assert_called_once_with("left", _pause=False)

    def test_windows_scroll_sends_one_notch_after_short_hold_without_bursts(self):
        mapper = GestureMapper()
        now = [100.0]
        with (patch("igaze.controls.time.monotonic", side_effect=lambda: now[0]),
              patch("igaze.controls.sys.platform", "win32"),
              patch("igaze.controls.pyautogui") as desktop):
            self.assertEqual(mapper.update_head_pitch(11), ("HOLD", 0))
            now[0] = 100.20
            mapper.update_head_pitch(11)
            desktop.scroll.assert_not_called()
            now[0] = 100.23
            self.assertEqual(mapper.update_head_pitch(11)[0], "DOWN")
            desktop.scroll.assert_called_once_with(-120, _pause=False)
            now[0] = 100.26
            mapper.update_head_pitch(9)
            desktop.scroll.assert_called_once()
            now[0] = 101.50
            mapper.update_head_pitch(11)
            self.assertEqual(desktop.scroll.call_count, 2)
            self.assertEqual(mapper.update_head_pitch(0), ("idle", 0))
            now[0] = 101.51
            mapper.update_head_pitch(-11)
            now[0] = 101.74
            mapper.update_head_pitch(-11)
            desktop.scroll.assert_called_with(120, _pause=False)

    def test_head_scroll_works_without_a_gaze_prediction_and_stops_at_neutral(self):
        with tempfile.TemporaryDirectory() as folder, patch("igaze.controller.EyeTracker"):
            app = iGazeApp(store=Store(Path(folder) / "igaze.sqlite3"))
            app.head_calibration = HeadScrollCalibration(neutral=33, up=10, down=56)
            frame = np.zeros((24, 32, 3), dtype=np.uint8)
            app.tracker.draw_debug.return_value = frame
            now = [100.0]
            poses = iter([33.0] * 12 + [56.0] * 5 + [33.0] * 3
                         + [56.0] * 17 + [33.0] * 5 + [10.0] * 17 + [33.0] * 5)
            history = []

            def capture():
                history.append(app.state["scroll_state"])
                now[0] += .05
                app.tracker.capture_time = now[0]
                return frame, [object()], None, None, .31, .31

            def pitch(*_):
                value = next(poses, None)
                if value is None:
                    app.stop_event.set()
                return value

            app.tracker.get_frame_data.side_effect = capture
            with (patch("igaze.controller.time.monotonic", side_effect=lambda: now[0]),
                  patch.object(app.head_pose, "update", side_effect=pitch),
                  patch.object(app.detector, "get_gaze_direction", return_value=None),
                  patch("igaze.controller.cv2.imshow"),
                  patch("igaze.controller.cv2.waitKey", return_value=-1),
                  patch("igaze.controls.pyautogui") as desktop):
                app._run_tracking()
                wheel_steps = [call.args[0] for call in desktop.scroll.call_args_list]
                desktop.hotkey.assert_not_called()
            self.assertIn("HOLD", history[12:21])
            self.assertNotIn("DOWN", history[:21])
            self.assertEqual(history[38:43], ["idle"] * 5)
            self.assertTrue(any(step < 0 for step in wheel_steps))
            self.assertTrue(any(step > 0 for step in wheel_steps))
            self.assertEqual(history[-1], "idle")
            self.assertEqual(app.state["scroll_state"], "CENTER")  # End-of-stream pose loss.

    def test_learned_screen_labels_survive_database_reload_and_send_correct_tab(self):
        # Increasing image-space ratio means screen-left in this fixture.
        samples = {"LEFT": (.64, .60), "CENTER": (.51, .49), "RIGHT": (.39, .41)}
        training = {key: [value] * 8 for key, value in samples.items()}
        validation = {key: [(x + .008, y - .005)] * 8 for key, (x, y) in samples.items()}
        with tempfile.TemporaryDirectory() as folder, patch("igaze.controller.EyeTracker"):
            path = Path(folder) / "igaze.sqlite3"
            candidate = Calibration()
            candidate.is_calibrated = True
            candidate.gaze_model = GazeModel.fit(training, validation)
            Store(path).set("calibration", candidate.to_dict())
            app = iGazeApp(store=Store(path))
            self.assertIsNotNone(app.detector.gaze_model)
            frame = np.zeros((200, 200, 3), dtype=np.uint8)
            landmarks = [None] * 478
            for first, second, iris, x, ratio in ((33, 133, 468, .1, .64), (362, 263, 473, .6, .60)):
                landmarks[first] = SimpleNamespace(x=x, y=.3)
                landmarks[second] = SimpleNamespace(x=x + .2, y=.4)
                landmarks[iris] = SimpleNamespace(x=x + .2 * ratio, y=.3 + .1 * ratio)
            np.testing.assert_allclose(eye_features(landmarks, 200, 200), samples["LEFT"])
            gaze = app.detector.get_gaze_direction(frame, landmarks)
            self.assertEqual(gaze, "LEFT")
            with patch("igaze.controls.time.monotonic", side_effect=[100.0, 100.55]), patch("igaze.controls.pyautogui") as desktop:
                app.mapper.update_gaze(gaze)
                app.mapper.update_gaze(gaze)
                desktop.hotkey.assert_called_once_with("ctrl", "shift", "tab", _pause=False)
            self.assertIsNone(app.detector.gaze_model.predict((.96, .12)))

    def test_training_rejects_wrong_directions_on_separate_validation_pass(self):
        training = {"LEFT": [(.64, .60)] * 8, "CENTER": [(.51, .49)] * 8,
                    "RIGHT": [(.39, .41)] * 8}
        validation = dict(training, RIGHT=training["LEFT"])
        with self.assertRaisesRegex(ValueError, "Right validation failed"):
            GazeModel.fit(training, validation)

    def test_three_single_frame_blinks_open_switcher_despite_missing_iris(self):
        with tempfile.TemporaryDirectory() as folder, patch("igaze.controller.EyeTracker"):
            app = iGazeApp(store=Store(Path(folder) / "igaze.sqlite3"))
            frame = np.zeros((24, 32, 3), dtype=np.uint8)
            now = [100.0]
            app.tracker.draw_debug.return_value = frame

            def capture():
                now[0] += .05
                step = round((now[0] - 100) / .05)
                ear = .15 if step in (2, 12, 23) else .31
                if step == 25:
                    app.stop_event.set()
                app.tracker.capture_time = now[0]
                landmarks = None if step == 17 else [object()]
                return frame, landmarks, None, None, ear, ear

            app.tracker.get_frame_data.side_effect = capture
            with (patch("igaze.controller.time.monotonic", side_effect=lambda: now[0]),
                  patch.object(app.head_pose, "update", return_value=None),
                  patch.object(app.detector, "get_gaze_direction", return_value=None),
                  patch("igaze.controller.cv2.imshow"),
                  patch("igaze.controller.cv2.waitKey", return_value=-1),
                  patch("igaze.controls.pyautogui") as desktop):
                app._run_tracking()
            desktop.hotkey.assert_called_once_with("win", "tab", _pause=False)
            self.assertEqual(app.detector.total_blinks, 3)

    def test_short_blink_pauses_gaze_hold_without_restarting_it(self):
        mapper = GestureMapper(gaze_hold_seconds=.65, action_cooldown=0)
        now = [100.0]
        with patch("igaze.controls.time.monotonic", side_effect=lambda: now[0]), patch("igaze.controls.pyautogui") as desktop:
            mapper.update_gaze("RIGHT")
            now[0] = 100.4
            mapper.update_gaze(None, eyes_closed=True)
            now[0] = 100.5
            mapper.update_gaze("RIGHT")
            desktop.hotkey.assert_not_called()
            now[0] = 100.76
            mapper.update_gaze("RIGHT")
            desktop.hotkey.assert_called_once_with("ctrl", "tab", _pause=False)

    def test_import_and_reopen_keeps_calibration_and_settings(self):
        with tempfile.TemporaryDirectory() as folder, patch("igaze.controller.EyeTracker"):
            path = Path(folder) / "igaze.sqlite3"
            app = iGazeApp(store=Store(path))
            self.assertTrue(app.load_calibration())  # Existing JSON fixture at the project root.
            app.apply_sensitivity({"gaze_hold_seconds": 1.2})
            reopened = iGazeApp(store=Store(path))
            self.assertTrue(reopened.state["calibrated"])
            self.assertEqual(reopened.detector.center_h, app.detector.center_h)
            self.assertEqual(reopened.mapper.gaze_hold, 1.2)

    def test_one_hold_sends_once_and_missing_signal_cannot_rearm(self):
        mapper = GestureMapper(gaze_hold_seconds=.5, action_cooldown=0)
        now = [100.0]
        with patch("igaze.controls.time.monotonic", side_effect=lambda: now[0]), patch("igaze.controls.pyautogui") as desktop:
            for t, gaze in ((100, "RIGHT"), (100.6, "RIGHT"), (101.5, "RIGHT"),
                            (102, None), (102.3, "RIGHT"), (103, "RIGHT")):
                now[0] = t
                mapper.update_gaze(gaze)
            desktop.hotkey.assert_called_once_with("ctrl", "tab", _pause=False)
            for t, gaze in ((103.1, "CENTER"), (103.3, "CENTER"),
                            (103.4, "LEFT"), (104, "LEFT")):
                now[0] = t
                mapper.update_gaze(gaze)
            desktop.hotkey.assert_called_with("ctrl", "shift", "tab", _pause=False)
            self.assertEqual(mapper.actions_sent, 2)

    def test_tracking_summary_records_successful_commands(self):
        with tempfile.TemporaryDirectory() as folder, patch("igaze.controller.EyeTracker"):
            app = iGazeApp(store=Store(Path(folder) / "igaze.sqlite3"))
            with patch.object(app, "_run_tracking", side_effect=lambda: setattr(app.mapper, "actions_sent", 2)):
                with patch("igaze.controller.cv2.destroyAllWindows"):
                    app._tracking_loop()
            started, profile, duration, actions, error = app.store.history()[0]
            self.assertEqual((profile, actions, error), ("Desktop", 2, ""))
            self.assertGreaterEqual(duration, 0)
            self.assertFalse(app.state["running"])


if __name__ == "__main__":
    unittest.main()
