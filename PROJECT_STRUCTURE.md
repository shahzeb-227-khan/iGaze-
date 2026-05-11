# iGAZE v3 - Project Structure

```
igaze2/
│
├── 📁 src/                          ← Source code
│   ├── main.py                      ← Application controller
│   ├── gui.py                       ← Tkinter GUI interface
│   ├── calibration.py               ← Calibration system
│   ├── eye_tracker.py               ← Eye tracking module
│   ├── gaze_detector.py             ← Gaze detection
│   ├── gesture_mapper.py            ← Gesture mapping
│   └── head_pose.py                 ← Head pose estimation
│
├── 📁 config/                       ← Configuration files
│   ├── igaze_cal.json               ← Calibration cache (auto-generated)
│   └── face_landmarker.task         ← ML model (478-point face landmarks)
│
├── 📁 scripts/                      ← Launch scripts
│   ├── create_shortcut.vbs          ← Create desktop shortcut
│   └── launch_igaze.bat             ← Alternative launcher
│
├── 📁 assets/                       ← Application assets
│   └── iGaze.ico                    ← Application icon
│
├── 📁 build/                        ← Build configuration
│   └── igaze_desktop.spec           ← PyInstaller config (for .exe builds)
│
├── 📁 docs/                         ← Documentation
│   ├── README.md                    ← Project overview
│   └── FAST_STARTUP.md              ← Startup optimization guide
│
├── 📁 venv/                         ← Python virtual environment
│   ├── Scripts/
│   └── [packages...]
│
├── 📁 __pycache__/                  ← Python cache (auto-generated)
│
├── run.bat                          ← Quick launcher from root
├── .gitignore                       ← Git ignore rules
└── main.py                          ← [DEPRECATED - Use src/main.py]
```

---

## 📋 File Organization

| Folder | Purpose |
|---|---|
| `src/` | All Python source code modules |
| `config/` | Configuration, calibration, and model files |
| `scripts/` | Launcher and setup scripts |
| `assets/` | Icons and visual resources |
| `build/` | Build configs for packaging (PyInstaller) |
| `docs/` | Documentation and guides |
| `venv/` | Python virtual environment |

---

## 🚀 How to Launch

### Option 1: Double-click `run.bat` (Fastest from root)
```
run.bat
```

### Option 2: Use desktop shortcut (One-time setup)
```
scripts/create_shortcut.vbs  (right-click → Open with Windows Script Host)
Then double-click desktop icon
```

### Option 3: Console from root
```
python src/main.py
```

---

## ✅ File Paths Updated

All paths have been automatically updated:
- ✅ `src/main.py` → Points to `../config/` for calibration and model
- ✅ `src/gui.py` → Points to `../assets/iGaze.ico`
- ✅ `scripts/launch_igaze.bat` → Launches `src/main.py`
- ✅ `scripts/create_shortcut.vbs` → Creates shortcut to `src/main.py`

---

## 🔨 Building Standalone .exe (Optional)

```bash
pip install pyinstaller
pyinstaller build/igaze_desktop.spec
```

Output: `dist/iGAZE v3/iGAZE v3.exe`

---

**Project is now organized and ready to use!** 🎉
