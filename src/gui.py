"""
gui.py — iGAZE v3 Control Panel
══════════════════════════════════════════════════════════════════════════════

Tkinter dark-themed control panel that runs on the main thread.
Communicates with iGazeApp (main.py) through app.state (read) and
app.start_tracking() / stop_tracking() / apply_sensitivity() (write).

Layout
──────
  Section 1 — Control Panel  (start / stop / calibrate)
  Section 2 — Live Status     (auto-refreshed every 100 ms)
  Section 3 — Sensitivity     (sliders + Apply button)
"""

import threading
import tkinter as tk
from tkinter import ttk, messagebox
import os
from pathlib import Path

# ── Colour palette (Catppuccin Mocha) ─────────────────────────────────────
BG        = "#1e1e2e"
SURFACE   = "#313244"
OVERLAY   = "#45475a"
TEXT      = "#cdd6f4"
SUBTEXT   = "#a6adc8"
ACCENT    = "#89b4fa"   # blue
GREEN     = "#a6e3a1"
RED       = "#f38ba8"
YELLOW    = "#f9e2af"
ORANGE    = "#fab387"
MAUVE     = "#cba6f7"

FONT_MONO   = ("Consolas", 10)
FONT_MONO_S = ("Consolas", 9)
FONT_HEAD   = ("Consolas", 11, "bold")
FONT_BTN    = ("Consolas", 10, "bold")


class iGazeGUI:
    """Tkinter control panel for iGAZE v3."""

    def __init__(self, app):
        self.app = app

        self.root = tk.Tk()
        self.root.title("iGAZE v3 — Eye Interaction System")
        self.root.geometry("520x720")
        self.root.resizable(False, False)
        self.root.configure(bg=BG)

        # Load and set the application icon
        self._set_icon()

        self._apply_theme()
        self._build_ui()

        # Start polling loop
        self.root.after(100, self._poll_state)

        # Graceful close
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ══════════════════════════════════════════════════════════════════════
    # Icon Setup
    # ══════════════════════════════════════════════════════════════════════

    def _set_icon(self):
        """Load and set the application icon from iGaze.ico"""
        try:
            # Try to find icon in multiple locations
            icon_paths = [
                Path(__file__).parent.parent / "assets" / "iGaze.ico",
                Path("../assets/iGaze.ico"),
                Path("iGaze.ico"),
                Path(__file__).parent / "iGaze.ico",
            ]
            
            for icon_path in icon_paths:
                if icon_path.exists():
                    self.root.iconbitmap(str(icon_path))
                    print(f"[GUI] Icon loaded: {icon_path}")
                    return
            
            print("[GUI] Warning: iGaze.ico not found, using default window icon")
        except Exception as e:
            print(f"[GUI] Error setting icon: {e}")

    # ══════════════════════════════════════════════════════════════════════
    # Theme
    # ══════════════════════════════════════════════════════════════════════

    def _apply_theme(self):
        style = ttk.Style(self.root)
        style.theme_use("clam")

        style.configure("TFrame",
                        background=BG)
        style.configure("TLabelframe",
                        background=BG, foreground=TEXT,
                        bordercolor=OVERLAY, lightcolor=OVERLAY,
                        darkcolor=OVERLAY)
        style.configure("TLabelframe.Label",
                        background=BG, foreground=ACCENT,
                        font=FONT_HEAD)
        style.configure("TLabel",
                        background=BG, foreground=TEXT,
                        font=FONT_MONO)
        style.configure("TButton",
                        background=SURFACE, foreground=TEXT,
                        font=FONT_BTN, relief="flat",
                        borderwidth=0, focusthickness=0)
        style.map("TButton",
                  background=[("active", OVERLAY), ("disabled", SURFACE)],
                  foreground=[("disabled", SUBTEXT)])

        # Named button variants
        style.configure("Start.TButton",
                        background=GREEN, foreground=BG)
        style.map("Start.TButton",
                  background=[("active", "#b9f0b5"), ("disabled", SURFACE)],
                  foreground=[("disabled", SUBTEXT)])

        style.configure("Stop.TButton",
                        background=RED, foreground=BG)
        style.map("Stop.TButton",
                  background=[("active", "#f7a8bc"), ("disabled", SURFACE)],
                  foreground=[("disabled", SUBTEXT)])

        style.configure("TScale",
                        background=BG, troughcolor=SURFACE,
                        sliderlength=16, sliderrelief="flat")
        style.configure("Horizontal.TProgressbar",
                        background=RED, troughcolor=SURFACE,
                        bordercolor=SURFACE, lightcolor=RED,
                        darkcolor=RED)

    # ══════════════════════════════════════════════════════════════════════
    # Build UI
    # ══════════════════════════════════════════════════════════════════════

    def _build_ui(self):
        pad = {"padx": 10, "pady": 6}

        # ── Header ────────────────────────────────────────────────────────
        hdr = tk.Label(self.root,
                       text="◉  iGAZE v3 — Eye Interaction System",
                       bg=BG, fg=ACCENT,
                       font=("Consolas", 13, "bold"))
        hdr.pack(pady=(12, 4))

        tk.Label(self.root,
                 text="Eye-controlled scroll · tab-switch · app-switcher",
                 bg=BG, fg=SUBTEXT,
                 font=("Consolas", 9)).pack(pady=(0, 8))

        ttk.Separator(self.root, orient="horizontal").pack(
            fill="x", padx=10, pady=2)

        # ── Section 1: Controls ───────────────────────────────────────────
        ctrl_frame = ttk.LabelFrame(self.root, text=" ■  CONTROL PANEL ",
                                    padding=10)
        ctrl_frame.pack(fill="x", padx=12, pady=6)

        btn_row1 = ttk.Frame(ctrl_frame)
        btn_row1.pack(fill="x", pady=(0, 6))

        self.btn_start = ttk.Button(btn_row1, text="▶  START TRACKING",
                                    style="Start.TButton",
                                    command=self._start_clicked)
        self.btn_start.pack(side="left", expand=True, fill="x", padx=(0, 4))

        self.btn_stop = ttk.Button(btn_row1, text="■  STOP TRACKING",
                                   style="Stop.TButton",
                                   command=self._stop_clicked,
                                   state="disabled")
        self.btn_stop.pack(side="left", expand=True, fill="x", padx=(4, 0))

        btn_row2 = ttk.Frame(ctrl_frame)
        btn_row2.pack(fill="x")

        self.btn_calibrate = ttk.Button(btn_row2, text="◎ RUN CALIBRATION",
                                        command=self._calibrate_clicked)
        self.btn_calibrate.pack(side="left", expand=True, fill="x", padx=(0, 3))

        self.btn_load = ttk.Button(btn_row2, text="↑ LOAD CAL",
                                   command=self._load_cal_clicked)
        self.btn_load.pack(side="left", expand=True, fill="x", padx=3)

        self.btn_save = ttk.Button(btn_row2, text="↓ SAVE CAL",
                                   command=self._save_cal_clicked)
        self.btn_save.pack(side="left", expand=True, fill="x", padx=(3, 0))

        # ── Section 2: Live Status ────────────────────────────────────────
        stat_frame = ttk.LabelFrame(self.root, text=" ● LIVE STATUS ",
                                    padding=10)
        stat_frame.pack(fill="x", padx=12, pady=6)

        def _row(parent, label, init_val, init_col=TEXT):
            row = ttk.Frame(parent)
            row.pack(fill="x", pady=1)
            ttk.Label(row, text=f"{label:<16}",
                      font=FONT_MONO_S, foreground=SUBTEXT).pack(side="left")
            lbl = tk.Label(row, text=init_val,
                           bg=BG, fg=init_col,
                           font=FONT_MONO, anchor="w")
            lbl.pack(side="left", fill="x", expand=True)
            return lbl

        self.lbl_status    = _row(stat_frame, "Status:",   "STOPPED",  RED)
        self.lbl_control   = _row(stat_frame, "Control:",  "ON",       GREEN)
        self.lbl_mode      = _row(stat_frame, "Mode:",     "NORMAL",   TEXT)
        self.lbl_gaze      = _row(stat_frame, "Gaze:",     "CENTER",   TEXT)
        self.lbl_pitch     = _row(stat_frame, "Pitch:",    "n/a",      TEXT)
        self.lbl_scroll    = _row(stat_frame, "Scroll:",   "idle  0%", TEXT)
        self.lbl_blinks    = _row(stat_frame, "Blinks:",   "0/3  [IDLE]", YELLOW)
        self.lbl_eye_conf  = _row(stat_frame, "Eye conf:", "1.00",     TEXT)
        self.lbl_ear       = _row(stat_frame, "EAR:",      "0.000",    TEXT)
        self.lbl_calibrated = _row(stat_frame, "Calibrated:", "NO",    ORANGE)
        self.lbl_fps       = _row(stat_frame, "FPS:",      "0.0",      SUBTEXT)

        # Disable progress bar
        pb_row = ttk.Frame(stat_frame)
        pb_row.pack(fill="x", pady=(6, 0))
        ttk.Label(pb_row, text=f"{'Disable timer:':<16}",
                  font=FONT_MONO_S, foreground=SUBTEXT).pack(side="left")
        self.pb_disable = ttk.Progressbar(
            pb_row, orient="horizontal", length=260,
            mode="determinate", style="Horizontal.TProgressbar")
        self.pb_disable.pack(side="left", fill="x", expand=True)
        self.lbl_dis_pct = tk.Label(pb_row, text="0%",
                                    bg=BG, fg=RED, font=FONT_MONO_S, width=5)
        self.lbl_dis_pct.pack(side="left")

        # ── Section 3: Sensitivity Settings ──────────────────────────────
        sens_frame = ttk.LabelFrame(self.root, text=" ⚙  SENSITIVITY SETTINGS ",
                                    padding=10)
        sens_frame.pack(fill="x", padx=12, pady=6)

        # (label_text, attr_name, from_, to_, resolution, default, fmt_fn)
        self._slider_defs = [
            ("Scroll Dead Zone",    "scroll_dead_zone",    3.0,  15.0, 0.5,
             10.0,  lambda v: f"{v:.1f}°"),
            ("Gaze Hold Time",      "gaze_hold_seconds",   0.5,   3.0, 0.1,
              1.5,  lambda v: f"{v:.1f}s"),
            ("Triple Blink Window", "triple_max_window_ms", 400, 2000,  50,
             900.0, lambda v: f"{int(v)}ms"),
            ("Disable Hold",        "disable_hold_sec",    2.0,  15.0, 0.5,
              5.0,  lambda v: f"{v:.1f}s"),
        ]
        self._sliders     = {}
        self._slider_lbls = {}

        for (name, key, lo, hi, res, default, fmt) in self._slider_defs:
            row = ttk.Frame(sens_frame)
            row.pack(fill="x", pady=3)

            ttk.Label(row, text=f"{name:<22}",
                      font=FONT_MONO_S, foreground=SUBTEXT,
                      width=22).pack(side="left")

            val_var = tk.DoubleVar(value=default)

            val_lbl = tk.Label(row, text=fmt(default),
                               bg=BG, fg=ACCENT,
                               font=FONT_MONO_S, width=8, anchor="e")
            val_lbl.pack(side="right")

            def _on_slide(v, _key=key, _fmt=fmt, _lbl=val_lbl):
                _lbl.config(text=_fmt(float(v)))

            slider = ttk.Scale(row, from_=lo, to=hi,
                               orient="horizontal",
                               variable=val_var,
                               command=_on_slide)
            slider.pack(side="left", fill="x", expand=True, padx=(4, 6))

            self._sliders[key]     = val_var
            self._slider_lbls[key] = val_lbl

        apply_btn = ttk.Button(sens_frame, text="⟳  APPLY SETTINGS",
                               command=self._apply_settings)
        apply_btn.pack(pady=(8, 0), fill="x")

        # Reset to defaults
        ttk.Button(sens_frame, text="↺  RESET DEFAULTS",
                   command=self._reset_defaults).pack(pady=(4, 0), fill="x")

    # ══════════════════════════════════════════════════════════════════════
    # Button callbacks
    # ══════════════════════════════════════════════════════════════════════

    def _start_clicked(self):
        self.app.start_tracking()
        self.btn_start.config(state="disabled")
        self.btn_stop.config(state="normal")
        self.btn_calibrate.config(state="disabled")

    def _stop_clicked(self):
        self.app.stop_tracking()
        self.btn_start.config(state="normal")
        self.btn_stop.config(state="disabled")
        self.btn_calibrate.config(state="normal")

    def _calibrate_clicked(self):
        """Run calibration in a background thread so GUI stays responsive."""
        self.btn_calibrate.config(state="disabled", text="◎ Calibrating…")
        self.btn_start.config(state="disabled")

        def _run():
            self.app.run_calibration()
            # GUI updates must happen on the main thread
            self.root.after(0, self._calibration_finished)

        threading.Thread(target=_run, daemon=True,
                         name="iGAZE-calibration").start()

    def _calibration_finished(self):
        self.btn_calibrate.config(state="normal", text="◎ RUN CALIBRATION")
        self.btn_start.config(state="normal")
        messagebox.showinfo(
            "iGAZE — Calibration",
            "Calibration complete!\n\n"
            f"EAR threshold : {self.app.detector.EAR_THRESH:.3f}\n"
            f"Gaze centre H : {self.app.detector.center_h:.3f}\n"
            f"Gaze centre V : {self.app.detector.center_v:.3f}"
        )

    def _save_cal_clicked(self):
        ok = self.app.cal.save_calibration("igaze_cal.json")
        if ok:
            messagebox.showinfo("iGAZE", "Calibration saved to igaze_cal.json")
        else:
            messagebox.showerror("iGAZE", "Failed to save calibration.")

    def _load_cal_clicked(self):
        ok = self.app.cal.load_calibration("igaze_cal.json")
        if ok:
            # Apply to live detector
            try:
                self.app.cal.apply_to_detector(self.app.detector)
            except AttributeError:
                thresh = (self.app.cal.ear_baseline *
                          self.app.cal.ear_hard_fraction)
                self.app.detector.EAR_THRESH = thresh
                self.app.detector.center_h   = self.app.cal.center_h
                self.app.detector.center_v   = self.app.cal.center_v
                self.app.detector.calibrated = self.app.cal.is_calibrated
            self.app.state["calibrated"] = True
            messagebox.showinfo("iGAZE", "Calibration loaded and applied.")
        else:
            messagebox.showwarning(
                "iGAZE",
                "No valid calibration file found (igaze_cal.json).\n"
                "Run calibration first."
            )

    def _apply_settings(self):
        settings = {key: var.get() for key, var in self._sliders.items()}
        self.app.apply_sensitivity(settings)
        messagebox.showinfo("iGAZE", "Settings applied.")

    def _reset_defaults(self):
        defaults = {
            "scroll_dead_zone":    10.0,
            "gaze_hold_seconds":    1.5,
            "triple_max_window_ms": 900.0,
            "disable_hold_sec":     5.0,
        }
        for key, val in defaults.items():
            self._sliders[key].set(val)
        # Trigger the formatter for each slider label
        for (name, key, lo, hi, res, default, fmt) in self._slider_defs:
            self._slider_lbls[key].config(text=fmt(self._sliders[key].get()))
        self.app.apply_sensitivity(defaults)

    # ══════════════════════════════════════════════════════════════════════
    # Polling loop  (runs on main thread via root.after)
    # ══════════════════════════════════════════════════════════════════════

    def _poll_state(self):
        s = self.app.state

        # Status / enabled
        running = s["running"]
        self.lbl_status.config(
            text="● RUNNING" if running else "○ STOPPED",
            fg=GREEN if running else RED)

        enabled = s["enabled"]
        self.lbl_control.config(
            text="ON" if enabled else "OFF",
            fg=GREEN if enabled else RED)

        # Mode
        mode = s["mode"]
        self.lbl_mode.config(
            text=mode.upper(),
            fg=ORANGE if mode == "app_sw" else TEXT)

        # Gaze
        gaze = s["gaze"]
        gaze_col = {
            "LEFT": ACCENT, "RIGHT": ACCENT,
            "UP": MAUVE,    "DOWN": MAUVE,
        }.get(gaze, TEXT)
        self.lbl_gaze.config(text=gaze, fg=gaze_col)

        # Pitch
        pitch = s["pitch"]
        self.lbl_pitch.config(
            text=f"{pitch:+.1f}°" if pitch != 0.0 else "n/a")

        # Scroll
        sc_st  = s["scroll_state"]
        sc_spd = s["scroll_speed"]
        sc_col = TEXT
        if sc_st == "UP":
            sc_col = GREEN
        elif sc_st == "DOWN":
            sc_col = ORANGE
        self.lbl_scroll.config(
            text=f"{sc_st}  {sc_spd}%", fg=sc_col)

        # Triple blink
        n  = s["triple_count"]
        ts = s["triple_state"]
        self.lbl_blinks.config(
            text=f"{n}/3  [{ts}]",
            fg=YELLOW if n > 0 else SUBTEXT)

        # Eye conf
        conf = s["eye_open_conf"]
        self.lbl_eye_conf.config(
            text=f"{conf:.2f}",
            fg=GREEN if conf > 0.5 else RED)

        # EAR
        self.lbl_ear.config(text=f"{s['ear']:.3f}")

        # Calibrated
        cal = s["calibrated"]
        self.lbl_calibrated.config(
            text="YES" if cal else "NO",
            fg=GREEN if cal else ORANGE)

        # FPS
        self.lbl_fps.config(text=f"{s['fps']:.1f}")

        # Disable progress bar
        prog = s["close_progress"]
        self.pb_disable["value"] = prog * 100
        pct_str = f"{int(prog * 100)}%"
        self.lbl_dis_pct.config(
            text=pct_str,
            fg=RED if prog > 0.6 else (YELLOW if prog > 0 else SUBTEXT))

        # Re-schedule
        self.root.after(100, self._poll_state)

    # ══════════════════════════════════════════════════════════════════════
    # Lifecycle
    # ══════════════════════════════════════════════════════════════════════

    def _on_close(self):
        """Gracefully stop tracking and release camera before destroying."""
        if self.app.state["running"]:
            self.app.stop_tracking()
        self.app.cleanup()
        self.root.destroy()

    def run(self):
        """Block on Tkinter main loop — must be called from the main thread."""
        self.root.mainloop()
