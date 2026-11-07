# iGAZE implementation progress

## Foundation milestone

Implemented missing-face temporal reset, suppression of gaze/scroll while eyes
are closed, bounded scroll integration after stalls, tracked worker lifecycle,
camera ownership between tracking and calibration, fresh calibration with sample
failure handling, and resource/user-data path helpers. Removed the import-time
override that disabled PyAutoGUI's failsafe. Calibration now applies gaze
sensitivity and supports detectors without a debug dictionary.

Regression command: `python -m unittest discover -s tests -v`.
Tests mock desktop actions and camera construction. Live webcam validation remains
required before claiming reliable recognition or exhibition readiness.

## Next milestones

1. Eliminate legacy calibration/demo duplication, validate persisted calibration,
   add explicit pause/re-arm and monotonic gesture timing.
2. Extract typed vision signals and actions; prototype packaged Qt interface.
3. Reading/Slides profiles, intent confirmation, SQLite and polished UI.
4. Product website, installer and fresh-machine validation.

The complete 16-page release plan is in `output/pdf/`.
