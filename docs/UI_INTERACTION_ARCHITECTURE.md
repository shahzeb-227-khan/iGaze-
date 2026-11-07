# iGAZE: UI and interaction architecture

Decision document • 7 November 2026 • Architecture only; no application changes in this review.

## Recommendation

Use PySide6 with Qt Quick Controls/QML for the redesigned Windows desktop UI. Keep Python for camera processing and the control engine. Run a short packaging and camera-preview spike before migration; if it fails within half a day, use PySide6 Widgets with layouts and QScrollArea. Avoid adding a web server/Electron bridge to the desktop pipeline under the exhibition deadline. Pin the tested dependencies after the spike rather than selecting versions by novelty.

Offer two independently selectable input modes: **Eyes only** and **Eyes + optional head assistance**. Reading/Slides are task profiles, separate from input capabilities. Do not make head movement mandatory for a user whose neck movement is limited. For the exhibition, demonstrate reliable discrete commands; arbitrary pixel-accurate gaze cursor control remains experimental. Eye-only scrolling should use a deliberately entered command panel with large Up/Down/Stop targets, not continuous scrolling whenever the eyes move downward while reading.

The research question should concern accidental activation versus task efficiency and effort. Hybrid input and attractive UI alone are not defensible novelty claims.

## What the current code and screenshot establish

| Finding | Evidence | Consequence |
|---|---|---|
| Input enabled without completed calibration | Screenshot says Session active and Calibration Needed; main.py calls mapper regardless of calibrated state | Wrong thresholds can issue real commands |
| Head scroll uses absolute pitch | head_pose.py returns smoothed raw Euler pitch; gesture_mapper.py directly compares it with dead zone | Camera angle and resting posture can cause scroll; screenshot shows +33.7 degrees |
| No head-scroll intention phase | update_head_pitch accumulates output as soon as threshold is exceeded | Brief head movement can trigger immediately |
| Camera and input share a blocking loop | main.py calls mapper methods before the next capture; mapper calls PyAutoGUI synchronously | Default 100 ms PyAutoGUI pause can substantially reduce processing rate during actions |
| Earlier safety patch restored PyAutoGUI defaults | Previous PAUSE=0 and FAILSAFE=False overrides were removed | Likely contributor to reported slowdown; keep fail-safe but isolate dispatch. This is a diagnosis to benchmark, not a measured attribution |
| Image-mode face inference | EyeTracker uses detect(), with no VIDEO running mode | Evaluate video tracking path rather than independently processing every image |
| Frame-based filtering | Five-sample gaze history, alpha=0.15 head EMA, frame-count blink debounce | Behaviour slows when FPS falls. At 11 FPS five samples span about 0.36 seconds; EMA low-frequency delay is approximately 0.52 seconds, not an end-to-end latency measurement |
| Iris features assume axis-aligned eye geometry | _get_iris_ratios uses x/y corner and lid ratios | Head roll, eyelid motion and perspective can perturb the signal. Need controlled directional validation, not an assumed sign fix |
| No usable compact layout | gui.py fixed minimum 960x720, packed rows, no scrollable page, no wrapping policy | Restored/smaller windows and DPI scaling can hide controls |
| Debug feed overlays the app | Separate OpenCV window and dense face mesh in screenshot | Competing focus and obscured UI; integrate preview |

The screenshot is not sufficient to prove the gaze direction is wrong: intended target and calibration are unknown. It does show low FPS and unsafe readiness feedback. A truly minimized window shows no content; the reported clipping likely concerns restored/smaller windows or display scaling, which must be reproduced.

## UI design specification

Visual direction: quiet, polished assistive workspace. Warm off-white background (#F5F7FB), white cards, dark navy text (#132238), teal primary action (#006B62), restrained blue accents. Optional dark theme later. Validate actual text and state contrast; palette selection alone is not compliance. Use Segoe UI Variable with Segoe UI fallback, 16 logical-pixel body text, 24–28 headings, an 8-pixel spacing grid, 12–16 corner radii. Prefer clear labels and small consistent SVG icons over decorative emoji. Remove the large marketing slogan and developer counters from the primary workspace.

Desktop layout:

    iGAZE          Workspace  Setup  History         Camera ready
    -----------------------------------------------------------
    Reading                     Input: Eyes + head assistance
    [Change task]               [Change input mode]
    -----------------------------------------------------------
    Session card                              Camera preview
    Ready / Calibrate / Paused                 integrated, optional
    What to do next                           face-position guidance
    [Start control] [Practice]                 no mesh by default
    -----------------------------------------------------------
    Current intent: Scroll down
    Hold progress [---------]  Cancel: return to neutral
    -----------------------------------------------------------
    Persistent bottom bar:  [Pause control]     [Stop session]

States: disconnected, camera ready, calibration required, calibrating, validating, practice, ready, armed, candidate action, executing, paused, tracking lost, error. Each state needs a text explanation and recovery action. Never display Active while calibration is required. Preview/tracking may run without permission to send desktop input.

Setup wizard: camera and lighting check → comfortable neutral posture → gaze targets → blink baseline → independent validation → safe practice → explicit Enable control. Explain each step before timing it. Allow retry/cancel. Caregiver-assisted setup is acceptable; disclose that initial setup is not yet independently eye-operable.

Responsive behaviour in logical pixels:

- Above 1000: main task card and preview in two columns, compact top navigation.
- 720–999: stack preview under controls; collapse secondary statistics.
- Below 720: single column; all nonessential content scrolls vertically. Keep Pause/Stop outside the scrolling area. Minimum supported size should fit the exhibition laptop at its actual DPI.
- Use Qt layouts and implicit sizes, not fixed positions. Wrap descriptive text. Scroll history vertically with resize-aware columns. No horizontal scroll for ordinary controls.
- Test 800x600, 1024x768, 1366x768, 1920x1080; 100%, 125%, 150%, 200% scale; maximize/restore and monitor changes. Evaluate available logical area, not just physical resolution.

HCI acceptance checklist (not a claim to satisfy every HCI rule):

- Visibility: one authoritative control status; show intent and dwell progress before execution.
- User control: persistent Pause/Stop, explicit resume, cancel on leaving target, no automatic resume after tracking loss.
- Error prevention: calibration/quality gates, neutral rearm, one discrete action per deliberate gesture.
- Recognition: profile-specific illustrated guidance; explain why a command was withheld.
- Consistency: same labels and shortcuts across wizard, workspace and compact mode.
- Accessibility: keyboard navigation, visible focus, accessible names/roles, screen-reader checks; do not encode state only by colour. Body text contrast target 4.5:1; UI boundaries/state indicators 3:1 where applicable.
- Target sizes: use 48–56 logical-pixel button heights for manual controls; larger, widely separated gaze targets (initially 96–140 pixels, then tune to measured gaze error). WCAG's 24 CSS-pixel minimum is a web baseline, not an adequate gaze-target specification.
- Reduce cognitive load: FPS, EAR, raw pose and counters belong in Diagnostics. Disable animation during gaze selection; honour reduced-motion preferences.

## Processing architecture

    Camera owner → newest frame slot → inference worker
                                          ↓
                                timestamped signal snapshot
                                          ↓
                              quality + calibration gate
                                          ↓
                                 intent state machine
                                          ↓
                              bounded action dispatcher → OS
                                          ↓
                             outcomes / session summary

    UI reads snapshots at 10–15 Hz; preview capped at 10–15 Hz.
    Storage receives batched events and completed-session summaries.

One owner per resource. Qt main thread owns all UI. Capture thread owns VideoCapture. Inference worker owns FaceLandmarker, using VIDEO mode and strictly increasing timestamps; compare against current mode on the same hardware. Use a replaceable one-frame slot, not an accumulating video queue. UI sends commands to the controller; it never mutates mapper internals. SQLite connections belong to their calling worker; no writes in the frame-critical path.

Define immutable FramePacket(frame_id, capture_time, image), SignalSnapshot(frame_id, capture_time, computed_time, gaze_features, relative_pose, eye_state, validity_reasons), Intent(state, target, progress, generation), ActionRequest(kind, amount, created_time, expiry, generation, target_window), and ActionResult(status, reason, executed_time). An eye-opening ratio is not a calibrated tracking-confidence probability; label it accurately.

Dispatcher owns OS calls. Keep PyAutoGUI fail-safe enabled. Begin with its default pause off the perception thread, rate-limit/coalesce scroll, and measure throughput. Any later pause reduction requires stop/kill-switch tests. Never permit a backlog: replace old continuous commands, expire discrete requests, invalidate the generation on pause, loss, stop or profile change, and recheck eligibility immediately before sending. A system event already sent cannot be retracted. Bind actions to the selected target app and suspend on unexpected foreground changes. Disable Win+Tab during the exhibition default profile because it intentionally changes target focus.

Register an actual system-wide stop shortcut and provide a caregiver stop button. The present Tk Escape binding only works when the dashboard has focus. On minimize, default to paused; optional background control must be explicitly enabled with a visible tray status and functioning global stop. Hiding the preview must not stop inference when background mode is intentionally active.

Suggested folders: igaze/domain/{signals,intents,profiles}.py; igaze/services/{controller,calibration,quality}.py; igaze/infrastructure/{camera,mediapipe_adapter,input_dispatcher,storage}.py; igaze/ui/{bridge.py,qml/}; tests/{intent,dispatcher,calibration,storage}; tools/benchmark_pipeline.py. Migrate incrementally behind interfaces.

## Prevent accidental head triggers

Start paused. Measure neutral head pose as a robust median during a comfortable, stationary 1–2 second interval. Use relative pitch = current pitch minus neutral pitch; validate sign and angle discontinuities with known head movements. Check pose residuals, abrupt jumps, face size and visibility. Do not continuously recenter while the user is issuing a command.

Proposed state sequence:

    PAUSED → READY → NEUTRAL_STABLE → CANDIDATE → ACTIVE_SCROLL
                              ↑          ↓              ↓
                              └──── CANCEL / REARM ─────┘

Initial tunable values, not research-established constants: neutral stable 300 ms; enter relative pitch beyond 8 degrees; show progress for 450 ms; exit inside 4 degrees. Use hysteresis so the signal does not chatter around one threshold. Require a fresh deliberate dwell after direction reversal. Ramp scroll from a low speed over 300 ms and cap wheel steps per second. A brief glance down cancels before output. Stale samples over an initial 150 ms budget, poor eye/pose quality, camera loss or focus change cancel pending intent. Tune the age budget from measured latency; if the machine cannot meet it, visibly withhold control rather than relax it silently.

For discrete left/right commands, require a time-based stable neutral interval between actions. The current single CENTER sample can rearm, and closed eyes are passed as CENTER: these must not count as a valid neutral fixation. Eye closure cancels gaze candidates. Disable triple-blink switching by default until its false-trigger rate is measured. Avoid requiring forceful or prolonged eye closure as the only pause mechanism.

## Better gaze output

First validate left/right mapping and eye-local geometry with recorded known targets. Normalize iris location along the eye-corner axis and its perpendicular, retain separate eye features and reject occluded/unreliable eyes. Evaluate pose compensation rather than simply averaging everything. Inspect mirrored-camera conventions explicitly.

Use centre/left/right guided samples for the immediate discrete-command mode; add up/down only for a validated command panel. Fit user-specific class boundaries from sample distributions and include a reject/uncertain region. Use separate held-out target trials after calibration; show which direction failed and retry it. A centre-only calibration cannot establish directional accuracy. Five/nine-point regression can be a later cursor experiment; it must not be presented as pixel-accurate just because points were collected.

Replace frame-count thresholds with monotonic durations. Compare a time-based EMA and 1-Euro filter under the same task; avoid stacking multiple heavy smoothers. Do not continuously learn from assumed successful clicks: implicit calibration needs trustworthy labels and rollback.

## Performance and release gates

Measure capture wait, inference, feature extraction, intent processing, dispatcher delay and preview render separately. Compare no-output practice, scrolling output, preview off/on, minimized mode and resized UI. Log p50/p95 age and delay, dropped frames, CPU and executed/expired commands. Do not use FPS as the only responsiveness metric.

Provisional laptop targets: perception >=25 FPS if the camera permits, p95 capture-to-intent processing <=100 ms, UI pause response <=100 ms, and zero queued actions executed after pause/loss invalidation. Deliberate dwell is additional intentional latency and must be reported separately. If unmet, reduce preview/mesh work and evaluate lower inference resolution without destroying iris detail. UI replacement alone is not the performance fix.

Release scenarios: no input before valid calibration; brief head dip; natural blink; eye closure during candidate; lost face; stale frame; long held gaze; direction reversal; foreground change; pause with queued command; stop/start; calibration cancel; camera unplug; minimized restore; Windows DPI change. Check trigger counts during natural reading and conversation, not only scripted commands.

## Research positioning and evaluation

Relevant current prior art includes LookAHead (2026 hybrid gaze/head refinement), GazeFollower (2025 calibration/inference framework, actively maintained), and Accessible Eye-Tracking in the Global South (2026 webcam prototype with target users). Thus neither hybrid control, webcam affordability, profiles nor calibration alone establishes novelty. LookAHead publisher access was blocked during this review; do not repeat its detailed outcome numbers without checking the full primary paper.

Proposed contribution to investigate: a capability-selectable, uncertainty-aware intention gate that reduces unintended desktop commands while retaining acceptable completion time. Compare (A) calibrated fixed-threshold baseline, (B) baseline + dwell/neutral rearm, (C) full quality gate + relative head calibration + hysteresis. Separately compare Eyes only versus optional head assistance using equivalent tasks. This separates interaction-policy effects from a prettier UI.

Report unintended actions per minute, task success, task completion time, missed commands, calibration retry/failure rate, p95 processing latency, SUS and workload ratings. Counterbalance condition order, define tasks and exclusions before collecting results, retain failed trials, report participant-level variation and uncertainty. Determine sample size from study design/pilot variability; no arbitrary promise that a small exhibition demo proves superiority. Any target-user or injury-related claims require appropriate recruitment, consent and institutional ethics review. Avoid claims that head movement suits every injury.

## Four-day implementation handoff

Day 1: Sol implements instrumentation, preview-only default, calibration gate, neutral-relative pitch, intent/rearm states, independent dispatcher and emergency stop. Record before/after measurements. Do this before polishing the UI.

Day 2: Sol builds the PySide6/QML packaging spike, responsive shell, integrated preview and calibration wizard. Validate scaling on the actual exhibition laptop. Use the Widgets fallback if the spike fails.

Day 3: Sol finishes both input modes, practice panel, held-out calibration validation, persistence, diagnostics and the ablation logging switches. Luna can handle bounded icons, copy and documentation with explicit component specifications.

Day 4: Freeze features; test false activations, cold start, installer, camera recovery, compact layouts and the full demo script. Defer website expansion and experimental cursor work until the core demo is reliable. Astra reviews the intent/dispatch boundaries and final research claims, not all routine implementation.

Implementation prompt: Read this architecture, inspect existing behaviour, and implement Day 1 first. Keep application input disabled until calibration and explicit arming succeed. Preserve the existing code as a baseline. Produce measurements before claiming faster or more accurate performance. Then implement the responsive UI. Do not silently change experimental thresholds or fabricate evaluation data.

## Sources checked

- PyAutoGUI default delay and fail-safe: https://pyautogui.readthedocs.io/en/latest/index.html
- Qt responsive layouts: https://doc.qt.io/qt-6/qtquicklayouts-overview.html
- Qt high DPI: https://doc.qt.io/qtforpython-6.11/overviews/qtdoc-highdpi.html
- MediaPipe Face Landmarker modes: https://developers.google.com/edge/mediapipe/solutions/vision/face_landmarker?tab=Python
- Nielsen usability heuristics: https://www.nngroup.com/articles/ten-usability-heuristics/
- WCAG reflow (web guidance adapted here, not a desktop compliance certification): https://www.w3.org/WAI/WCAG21/Understanding/reflow
- WCAG target size: https://www.w3.org/WAI/WCAG22/Understanding/target-size-minimum.html
- GazeFollower maintained calibration configuration: https://github.com/GanchengZhu/GazeFollower/blob/main/gazefollower/misc/DefaultConfig.py
- Accessible Eye-Tracking in the Global South, institutional publication record: https://puceinvestiga.puce.edu.ec/es/publications/accessible-eye-tracking-in-the-global-south-design-and-explorator/
- LookAHead DOI (publisher full text inaccessible in this review): https://doi.org/10.1145/3806018
