# iGAZE v3 — Eye-Assisted Desktop Interaction System

> A computer-vision system that translates eye movements, blink patterns, and head gestures into real desktop actions — enabling hands-free tab switching, scrolling, and application navigation.

---

## Table of Contents

1. [Description](#description)
2. [Features](#features)
3. [Demo / Output](#demo--output)
4. [Tech Stack](#tech-stack)
5. [System Architecture](#system-architecture)
6. [Installation Guide](#installation-guide)
7. [Usage Instructions](#usage-instructions)
8. [Project Structure](#project-structure)
9. [Algorithm Overview](#algorithm-overview)
10. [Configuration Options](#configuration-options)
11. [Results / Performance](#results--performance)
12. [Limitations](#limitations)
13. [Future Improvements](#future-improvements)
14. [Contributing](#contributing)
15. [License](#license)

---

## Description

iGAZE v3 is a hands-free desktop interaction system powered by real-time computer vision. Using a standard webcam, it continuously tracks the user's iris position, blink patterns, and head pitch to map natural eye and head gestures to desktop control actions such as browser tab switching, proportional scrolling, and Windows App Switcher navigation.

**Problem it solves:**  
Users who experience repetitive strain injury (RSI), manual input fatigue, or limited hand mobility frequently need to scroll, switch tabs, or navigate applications throughout the day. iGAZE eliminates the need for keyboard or mouse input for these common operations by interpreting natural physiological signals already present in normal computer use.

**Why it is useful:**  
- Reduces physical load on hands and wrists during long computing sessions.  
- Provides an accessible alternative input channel for users with motor impairments.  
- Operates on commodity hardware — no special eye-tracking device is required.

---

## Features

### Core Features

| Gesture | Action |
|---|---|
| Gaze LEFT (held for configurable duration) | Switch browser tab backward (`Ctrl+Shift+Tab`) |
| Gaze RIGHT (held for configurable duration) | Switch browser tab forward (`Ctrl+Tab`) |
| Head tilt UP | Scroll page upward (speed proportional to tilt angle) |
| Head tilt DOWN | Scroll page downward (speed proportional to tilt angle) |
| Triple blink within rolling window | Open Windows Task View / App Switcher (`Win+Tab`) |
| Gaze LEFT / RIGHT in App Switcher mode | Navigate between open applications |
| Triple blink in App Switcher mode | Confirm / launch selected application (`Enter`) |
| Eyes closed for configurable hold duration | Toggle eye control ON / OFF |

### Advanced Features

- **Two-phase interactive calibration** — Phase 1 collects open-eye EAR baseline and iris centre; Phase 2 measures natural blink troughs to derive user-specific blink thresholds.
- **Persistent calibration** — Parameters are saved to `igaze_cal.json` and loaded automatically on subsequent launches; recalibration is skipped when a valid file exists.
- **Calibration validation** — Post-calibration method reports false-positive rate and mean EAR clearance above the blink threshold.
- **Gaze smoothing** — A majority-vote history buffer (5-frame window) suppresses transient iris jitter before a gaze direction is reported.
- **Head pitch EMA smoothing** — Exponential moving average (α = 0.15) on the raw PnP pitch output removes high-frequency jitter without introducing perceptible lag.
- **Proportional scroll speed** — Scroll velocity scales linearly between the dead-zone boundary and a configurable maximum pitch angle.
- **Fractional-pixel scroll accumulator** — Prevents rounding loss at low speeds, giving smooth scroll at any tilt magnitude.
- **Eye-close debounce** — Requires 8 consecutive below-threshold frames before the disable timer starts, preventing brief EAR dips caused by head tilts from triggering a false disable.
- **Triple-blink false-positive suppression** — A 1.5-second blink-ignore window after any mode change prevents ghost blinks accumulated during head movement from firing `Win+Tab`.
- **App Switcher scroll suspension** — Head-pitch scrolling is suppressed while the App Switcher is open so head movements while reading the app list do not accidentally scroll.
- **Dark-themed Tkinter GUI** — Control panel (Catppuccin Mocha palette) provides start/stop controls, calibration buttons, a live status panel refreshed every 100 ms, and four live sensitivity sliders.
- **Thread-safe architecture** — OpenCV capture loop runs on a daemon thread; Tkinter GUI runs on the main thread; communication is through a plain shared `state` dict (effectively atomic for CPython scalar writes).
- **Live sensitivity adjustment** — All major thresholds can be changed at runtime through the GUI without restarting the application.
- **Right-wink detection** — The blink detector identifies right-eye-only winks (right EAR below threshold while left EAR remains clearly open), though this signal is not currently mapped to a desktop action.
- **On-screen HUD** — The camera feed window overlays gaze direction, pitch, scroll state/speed, blink counter, operating mode, control status, FPS, and a disable-progress bar.

---

## Demo / Output

> Place screenshots or a demo GIF here.

```
docs/
  screenshots/
    gui_control_panel.png     ← Tkinter control panel
    camera_hud_overlay.png    ← Camera feed with debug overlay
    calibration_phase1.png    ← Phase 1 calibration screen
    calibration_phase2.png    ← Phase 2 blink collection screen
  demo.gif                    ← Animated walkthrough of all 4 gestures
```

*Suggested recording: a short screen-capture showing tab switching, scrolling, and App Switcher navigation side-by-side with the camera feed.*

---

## Tech Stack

| Component | Library / Tool |
|---|---|
| Language | Python 3.12+ |
| Face & iris landmark detection | MediaPipe Tasks (`mediapipe`) — FaceLandmarker (478-point model) |
| Camera capture & image processing | OpenCV (`opencv-python`) |
| Numerical computation | NumPy |
| Head pose estimation | OpenCV `solvePnP` (Rodrigues + Euler decomposition) |
| Desktop automation | PyAutoGUI |
| GUI | Tkinter (stdlib) + `ttk` |
| Calibration persistence | JSON (stdlib) |
| Threading | Python `threading` (stdlib) |

No external web frameworks, databases, or cloud services are used.

---

## System Architecture

```
┌──────────────────────────────────────────────────────────────────────────┐
│                          iGazeApp (main.py)                              │
│                                                                          │
│  Main thread                        Daemon thread                        │
│  ┌─────────────┐                   ┌──────────────────────────────────┐ │
│  │  iGazeGUI   │  reads app.state  │         _tracking_loop           │ │
│  │  (gui.py)   │ ◄──────────────── │                                  │ │
│  │             │                   │  1. EyeTracker.get_frame_data()  │ │
│  │  Controls   │  write calls      │     → frame, landmarks, EARs     │ │
│  │  Sliders    │ ────────────────► │  2. HeadPoseEstimator.update()   │ │
│  │  Status     │                   │     → smoothed pitch (degrees)   │ │
│  └─────────────┘                   │  3. GazeDetector.update_blink()  │ │
│                                    │     → single blink flag          │ │
│  ┌─────────────┐                   │  4. GazeDetector.get_gaze_dir()  │ │
│  │ Calibration │                   │     → CENTER/LEFT/RIGHT/UP/DOWN  │ │
│  │  (cal.py)   │                   │  5. Compute eye_open + conf      │ │
│  │             │                   │  6. GestureMapper.update_*()     │ │
│  │ Phase 1: EAR│                   │     → PyAutoGUI desktop actions  │ │
│  │ Phase 2:    │                   │  7. Draw HUD, cv2.imshow()       │ │
│  │  Blink EAR  │                   └──────────────────────────────────┘ │
│  │ Save/Load   │                                                         │
│  │  JSON       │                                                         │
│  └─────────────┘                                                         │
└──────────────────────────────────────────────────────────────────────────┘
```

> [Insert Architecture Diagram Here]  
> Recommended: a block diagram showing the data flow from webcam → MediaPipe → EyeTracker → GazeDetector / HeadPoseEstimator → GestureMapper → OS.

### Module Responsibilities

| Module | Responsibility |
|---|---|
| `eye_tracker.py` | Webcam capture, MediaPipe FaceLandmarker inference, EAR computation, debug visualisation |
| `gaze_detector.py` | Iris-ratio gaze direction (5-class), blink detection, right-wink detection, gaze smoothing |
| `head_pose.py` | 6-point PnP head pose estimation, pitch extraction, EMA smoothing |
| `gesture_mapper.py` | Combines all signals into desktop actions via PyAutoGUI; manages modes, cooldowns, debounce |
| `calibration.py` | Two-phase interactive calibration, JSON persistence, post-calibration validation |
| `gui.py` | Tkinter control panel, live status polling, sensitivity slider interface |
| `main.py` | Application controller, thread management, shared state dict, HUD rendering |

---

## Installation Guide

### Prerequisites

- Python 3.12 or later
- A functioning webcam (built-in or USB)
- Windows OS (PyAutoGUI hotkeys are Windows-specific as used: `win+tab`, `ctrl+tab`, `ctrl+shift+tab`)

### 1. Clone the Repository

```bash
git clone https://github.com/<your-username>/igaze.git
cd igaze
```

### 2. Create and Activate a Virtual Environment

```bash
python -m venv venv

# Windows (PowerShell)
.\venv\Scripts\Activate.ps1

# Windows (Command Prompt)
venv\Scripts\activate.bat
```

### 3. Install Dependencies

No `requirements.txt` is present in the repository at this time. Install the required packages manually:

```bash
pip install mediapipe opencv-python numpy pyautogui
```

> **Note:** The MediaPipe model file `face_landmarker.task` (~3.6 MB) is included in the repository root and is loaded at runtime. Do not move or rename it.

### 4. Verify Installation

```bash
python eye_tracker.py
```

A camera window should open showing the live feed with eye landmark overlays. Press `q` to close.

---

## Usage Instructions

### Starting the Full Application

```bash
python main.py
```

This launches the Tkinter control panel. From there:

1. **(Optional) Run or Load Calibration** — Click **◎ RUN CALIBRATION** and follow the on-screen prompts:
   - **Phase 1:** Keep eyes open and look at the green dot for ~2 seconds.
   - **Phase 2:** Blink naturally 2–3 times when prompted.
   - Click **↓ SAVE CAL** to persist the result. On subsequent launches, click **↑ LOAD CAL** to skip recalibration.

2. **Start Tracking** — Click **▶ START TRACKING**. The camera feed window opens alongside the control panel.

3. **Interact with the system using the gesture map below:**

| Gesture | Desktop Action |
|---|---|
| Look LEFT and hold | Switch to previous browser tab |
| Look RIGHT and hold | Switch to next browser tab |
| Tilt head UP | Scroll up (continuously, speed varies with tilt) |
| Tilt head DOWN | Scroll down (continuously, speed varies with tilt) |
| Blink 3 times quickly | Open Windows Task View (App Switcher) |
| Look LEFT / RIGHT in App Switcher | Navigate between open apps |
| Blink 3 times quickly again | Launch the selected app |
| Close eyes and hold | Toggle eye control ON / OFF |

4. **Stop Tracking** — Click **■ STOP TRACKING** or press `q` in the camera window.

### Running Individual Modules (Standalone Tests)

```bash
# Test webcam capture and EAR visualisation only
python eye_tracker.py

# Test gaze detection with live overlay
python gaze_detector.py

# Test all 4 gestures end-to-end (no GUI)
python gesture_mapper.py
```

---

## Project Structure

```
igaze/
├── main.py               # Application entry point and controller
├── eye_tracker.py        # Webcam capture, MediaPipe inference, EAR computation
├── gaze_detector.py      # Iris-ratio gaze classification, blink detection
├── head_pose.py          # PnP-based head pitch estimation with EMA smoothing
├── gesture_mapper.py     # Gesture-to-action mapping, mode management
├── calibration.py        # Two-phase calibration, JSON persistence, validation
├── gui.py                # Tkinter dark-themed control panel
├── face_landmarker.task  # MediaPipe FaceLandmarker model (binary, ~3.6 MB)
├── igaze_cal.json        # Saved calibration parameters (auto-generated)
├── .gitignore
└── README.md
```

---

## Algorithm Overview

### Eye Aspect Ratio (EAR)

The Eye Aspect Ratio is a scalar that drops sharply when an eye closes. It is computed from six eye landmark points:

```
EAR = (||p2 - p6|| + ||p3 - p5||) / (2 * ||p1 - p4||)

Where p1..p6 are the six landmark pixels around the eye contour.
```

A blink is detected when the average EAR of both eyes falls below a threshold for at least 2 consecutive frames and then recovers.

### Iris-Based Gaze Direction (Pseudocode)

```
FOR each frame:
    Detect 478 face landmarks with MediaPipe FaceLandmarker
    
    Extract iris centre landmarks (index 468 left, 473 right)
    Extract inner/outer/top/bottom eye corner landmarks
    
    Compute horizontal ratio:
        lh = (iris_x - inner_corner_x) / (outer_corner_x - inner_corner_x)
        rh = (iris_x - inner_corner_x) / (outer_corner_x - inner_corner_x)
        avg_h = (lh + rh) / 2
    
    Compute vertical ratio similarly → avg_v
    
    diff_h = avg_h - calibrated_center_h
    diff_v = avg_v - calibrated_center_v
    
    IF diff_h < -h_sensitivity  → GAZE_LEFT
    IF diff_h >  h_sensitivity  → GAZE_RIGHT
    IF diff_v < -v_sensitivity  → GAZE_UP
    IF diff_v >  v_sensitivity  → GAZE_DOWN
    ELSE                        → GAZE_CENTER
    
    Apply majority-vote smoothing over last 5 frames
```

### Head Pose Estimation (Pseudocode)

```
Define 6 canonical 3-D face points (nose tip, chin, eye corners, mouth corners)
Map to 6 corresponding 2-D image points from landmarks
Build camera intrinsic matrix from frame width/height (focal = width)

Solve PnP (iterative) → rotation vector rvec
Convert rvec to rotation matrix R (Rodrigues)
Extract Euler pitch from R

Apply exponential moving average:
    smooth_pitch = α * raw_pitch + (1 - α) * smooth_pitch_prev
    (α = 0.15 by default)

Return smooth_pitch
```

### Triple-Blink Gesture (Pseudocode)

```
MAINTAIN a rolling list of blink timestamps

ON each blink event:
    Purge timestamps older than triple_blink_window seconds from list
    Append current timestamp
    
    IF len(list) >= 3:
        Clear list
        IF in Normal mode  → send Win+Tab, enter App Switcher mode
        IF in App Switcher → send Enter, return to Normal mode
```

---

## Configuration Options

All parameters below can be adjusted at runtime through the GUI sliders or by passing a `settings` dict to `app.apply_sensitivity()`. Default values are shown.

| Parameter | Default | Range | Description |
|---|---|---|---|
| `scroll_dead_zone` | `10.0°` | 3°–15° | Head pitch range treated as neutral; no scroll is triggered within this band |
| `gaze_hold_seconds` | `0.8 s` | 0.5–3.0 s | Duration gaze must remain LEFT or RIGHT before a tab-switch or app-navigate fires |
| `triple_max_window_ms` | `900 ms` | 400–2000 ms | Rolling window within which 3 blinks must occur to register a triple-blink |
| `disable_hold_sec` | `5.0 s` | 2.0–15.0 s | Eye-close duration that toggles eye control ON/OFF on re-open |

Additional parameters set during calibration (stored in `igaze_cal.json`):

| Parameter | Description |
|---|---|
| `ear_baseline` | 80th-percentile open-eye EAR for the current user |
| `ear_hard_fraction` | Fraction of baseline used as the blink EAR threshold |
| `center_h` / `center_v` | Calibrated iris-position centre (horizontal / vertical) |
| `h_sensitivity` / `v_sensitivity` | Dead-band around gaze centre before a direction is reported |

Fixed internal parameters (require code change to modify):

| Parameter | Value | Location | Description |
|---|---|---|---|
| `head_max_pitch` | `20.0°` | `GestureMapper.__init__` | Pitch angle yielding maximum scroll speed |
| `max_scroll_px_per_sec` | `300.0` | `GestureMapper.__init__` | Peak scroll velocity in pixels/second |
| `action_cooldown` | `0.9 s` | `GestureMapper.__init__` | Minimum time between successive tab-switch / app-navigate actions |
| `smooth_alpha` | `0.15` | `HeadPoseEstimator.__init__` | EMA factor for head pitch smoothing |
| `_EYE_CLOSE_DEBOUNCE_FRAMES` | `8` | `GestureMapper` class | Consecutive below-EAR frames required before the disable timer starts |
| `_BLINK_SUPPRESS_AFTER_MODE` | `1.5 s` | `GestureMapper` class | Blink ignore window after a mode change |

---

## Results / Performance

> This section is a placeholder. Populate with measured data after evaluation.

**Suggested evaluation metrics:**

| Metric | Placeholder |
|---|---|
| Gaze classification accuracy (5 classes) | — |
| False-positive blink rate (blinks/min at rest) | — |
| Triple-blink precision / recall | — |
| Scroll dead-zone compliance (% of nod events suppressed) | — |
| Disable toggle reliability | — |
| Median tracking loop FPS | — |

```
docs/
  results/
    gaze_confusion_matrix.png
    ear_distribution_pre_post_calibration.png
    fps_over_time.png
```

---

## Limitations

- **Single face only** — The `FaceLandmarkerOptions` is set to `num_faces=1`. The system will not function with multiple people in frame simultaneously.
- **Lighting sensitivity** — Iris landmark quality degrades in low light or with strong backlighting. Calibration in the same lighting conditions as intended use is recommended.
- **Windows-specific hotkeys** — The gesture-to-action mappings in `gesture_mapper.py` use `Win+Tab`, `Ctrl+Tab`, and `Ctrl+Shift+Tab`, which are Windows-specific shortcuts. Porting to macOS or Linux requires remapping these.
- **No vertical gaze actions** — Gaze UP and DOWN are detected but explicitly silenced in `update_gaze()` (by design, to prevent the Midas Touch problem during reading). They cannot currently be bound to actions.
- **Right-wink detected but not mapped** — The wink detection signal returned by `gaze_detector.update_blink()` is computed but not consumed by `GestureMapper` or any other component.
- **Fixed camera index** — The webcam is opened at index `0`. Systems with multiple cameras will require a code change.
- **Calibration requires face visibility** — If fewer than 10 EAR samples are collected during Phase 1 (e.g. face not in frame), the calibration falls back to default parameters silently.
- **`apply_to_detector` attribute mismatch** — `Calibration.apply_to_detector()` references `detector._debug` which does not exist on the current `GazeDetector` class; `iGazeApp.run_calibration()` catches the resulting `AttributeError` and applies parameters via a fallback path.

---

## Future Improvements

- **Map right-wink** to a configurable desktop action (e.g. middle-click or screenshot).
- **Vertical gaze actions** — Introduce an optional mode that binds gaze UP/DOWN to page navigation or volume control for users who prefer it.
- **Multi-camera selection** — Add a camera index picker to the GUI.
- **Cross-platform hotkey layer** — Abstract PyAutoGUI calls behind a platform-detection layer so the same gesture map works on macOS and Linux.
- **`requirements.txt` / `pyproject.toml`** — Add a dependency file to simplify installation.
- **Automated test suite** — Unit tests for EAR calculation, iris-ratio logic, PnP solver, and gesture state machine using pre-recorded landmark sequences.
- **Dwell-click** — Allow a prolonged CENTER gaze to simulate a left mouse click, enabling pointer control without a mouse.
- **Performance profiling** — Identify and reduce per-frame latency; the MediaPipe inference step is the dominant cost.
- **Calibration quality feedback** — Surface the `validate_calibration()` metrics in the GUI so users know whether to re-calibrate.

---

## Contributing

1. Fork the repository and create a feature branch:
   ```bash
   git checkout -b feature/your-feature-name
   ```
2. Make your changes and ensure existing behaviour is not broken (run `python gesture_mapper.py` and `python gaze_detector.py` as a quick smoke test).
3. Keep changes focused; one feature or fix per pull request.
4. Describe the motivation and approach clearly in the pull request description.
5. Submit the pull request against the `main` branch.

---

## License

No license file is present in the repository.

**Recommendation:** Add an [MIT License](https://choosealicense.com/licenses/mit/) for open-source sharing, or a [GPL-3.0 License](https://choosealicense.com/licenses/gpl-3.0/) if you require derivative works to remain open source.

To add the MIT license, create a `LICENSE` file with the following header (fill in year and name):

```
MIT License

Copyright (c) 2025 <Your Name>

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software...
```
