# iGAZE desktop workspace

The default launcher uses the original Tkinter control panel and gaze engine.

```powershell
.\.venv\Scripts\python.exe main.py
```

Run from the project directory. Choose **Run Calibration** to train the personalized gaze model. Follow the initial eye and blink prompts, then follow all six left, centre and right dots with your eyes. Keep your head still. The first pass trains the model and the second checks it. A failed check keeps the previous calibration. **Load Cal** can still restore an older calibration.

After calibration, click **Start Tracking** and focus the app you want to control. For a quick check, open two browser tabs and hold your gaze left or right to switch. Use **Reset Defaults** if an older saved hold setting feels slow.

For head scrolling, stop tracking and click **Calibrate Head Scroll**. Read each prompt, get into position, then click **Capture**. If a capture needs a retry, stay on that step. **Back** lets you recapture the previous position. iGAZE saves the neutral, up and down references separately from the eye model after the final neutral check. Start tracking and briefly match your saved neutral posture before tilting. Head scrolling rechecks neutral after tracking loss.

The Qt prototype and its test files are archived under the local ignored `tmp` directory.

**Local database.** SQLite is created on launch at `%LOCALAPPDATA%\iGAZE\igaze.sqlite3`. It holds calibration parameters, the fitted gaze model, sensitivity settings and completed-session summaries with duration, input event counts and errors. Camera images and training samples stay in memory. **Load Cal** can import the earlier JSON file. Saved calibration and settings reload automatically on subsequent launches.

**Tab gestures.** One hold sends one shortcut. Return your gaze briefly to centre before another tab switch. Frames that are missing or stale cancel the current gesture.

**App switching.** Blink three times to open Task View. Use left and right gaze to move the highlight, then blink three times again to open the highlighted app. Move the pointer away from the corner before restarting after an emergency stop.
