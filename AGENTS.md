# AGENTS.md — DashCam (车载行车记录仪)

A PyQt5 fullscreen touch-screen dashcam application for Linux ARM SBCs (Raspberry Pi / similar). Features live camera capture, loop recording to USB, video playback with AI license-plate recognition (HyperLPR3), USB auto-mount, WiFi client/hotspot via nmcli, and system time sync with DS3231 RTC.

## Build / Test / Lint

```bash
# System dependencies (Debian / Raspberry Pi OS)
sudo apt install python3-pyqt5 python3-opencv python3-psutil network-manager

# Python dependencies
pip install hyperlpr3

# Run (must be on the target Linux device with camera + touch screen)
python main.py

# Syntax-check all modules
python -m py_compile main.py core_camera.py ui_mainwindow.py ui_wifi.py ui_keyboard.py touch_widgets.py
```

No unit tests, linter config, or CI. `py_compile` is the sole pre-deploy check.

## Architecture

```
main.py                 Entry point: QApplication, BlankCursor, showFullScreen
  └─ ui_mainwindow.py    DashcamMainWindow — 3-page QStackedWidget shell
       │                   + TimeSettingDialog (fullscreen RTC time setter)
       ├─ core_camera.py   CameraWorker (cv2 capture → QImage signals)
       │                   SystemUtils (lsblk USB storage scanner)
       ├─ ui_wifi.py       WifiScannerDialog (nmcli scan + connect)
       ├─ ui_keyboard.py   TouchKeyboard (fullscreen on-screen keyboard)
       └─ touch_widgets.py TapSlider, TouchScrollListWidget
```

### Component map

| File | Class(es) | Role |
|------|-----------|------|
| `main.py` | — | Hide cursor, launch fullscreen window |
| `core_camera.py` | `SystemUtils` | `lsblk`-based USB discovery; auto-mount with `sudo` |
| `core_camera.py` | `CameraWorker` | Background thread: cv2 capture → timestamp overlay → LPR → QImage signals. 3-min `.mkv` loop recording; video playback with seek |
| `ui_mainwindow.py` | `DashcamMainWindow` | 3-page stack: main (record), settings (storage/WiFi/time), playback (slider + list). OSD overlays |
| `ui_mainwindow.py` | `TimeSettingDialog` | Fullscreen date/time adjuster using `◀ ▶` buttons; sets system clock via `sudo date -s` then syncs DS3231 via `sudo hwclock --systohc` |
| `ui_wifi.py` | `WifiScannerDialog` | Fullscreen nmcli WiFi scanner; password memory in `~/Videos/wifi_passwords.json`; 2s sleep for AP→Client radio switch |
| `ui_keyboard.py` | `TouchKeyboard` | Fullscreen on-screen QWERTY + numeric keyboard |
| `touch_widgets.py` | `TapSlider` | Tap groove to jump (not just drag handle) |
| `touch_widgets.py` | `TouchScrollListWidget` | 10px drag threshold to distinguish scroll from click |

### Data flow

1. **Live preview**: `CameraWorker._loop()` reads cv2 frame → overlays timestamp, REC dot, plate bbox → `frame_ready.emit(q_img, osd)` → `update_monitor()` renders scaled QPixmap
2. **Recording**: `_handle_recording()` writes frames via `cv2.VideoWriter` to 3-min `.mkv` segments on USB storage; auto-deletes oldest file when disk >88% full
3. **Playback**: user taps video → `play_selected_video()` → `_run_playback()` streams file, emits `playback_progress` → slider + time label
4. **Seek**: slider move → `on_slider_moved(val)` → `worker.seek_frame = val` → next loop calls `cap.set(POS_FRAMES)`
5. **WiFi / time**: `nmcli`, `date`, `hwclock` subprocess calls in daemon threads → cross-thread `pyqtSignal` back to UI

### Thread safety

All background work (camera, LPR, nmcli, disk) runs in `threading.Thread(daemon=True)`. PyQt5 signals carry results to the UI thread. Never touch widgets from background threads.

## Key Files

- `main.py` — entry point
- `core_camera.py` — camera, recording, playback, USB scan (~534 lines, heaviest)
- `ui_mainwindow.py` — main window, OSD, network, WiFi flow, time dialog (~691 lines)
- `ui_wifi.py` — WiFi scan dialog (~109 lines)
- `ui_keyboard.py` — on-screen keyboard (~73 lines)
- `touch_widgets.py` — custom touch widgets (~89 lines)
- `~/.vscode/sftp.json` — SFTP deploy config to target device

Runtime data stored under `~/Videos/`: recordings, `wifi_passwords.json`, `storage_config.json`, `plates_log.json`.

## Coding Conventions

- Python 3 with Chinese UI labels / comments
- PyQt5, programmatic UI only (no Qt Designer `.ui` files)
- Inline QSS stylesheets; dark theme (#000 background, #0073e6 accent)
- `snake_case` methods/variables, `CamelCase` classes; `btn_`, `lbl_` widget prefixes
- `pyqtSignal` for cross-thread; emoji in OSD messages
- `subprocess.run(["sudo", ...])` for system ops; Linux-only, no Windows compat
- Broad `try/except` with `print()` diagnostics; no structured logging

## Git Workflow

- Branch: `main` only, no PRs, no CI
- **Commit messages in English** (short imperative summary)
- Deploy: direct push to target device via SFTP

## Tips for AI Agents

- **Linux-only code.** `lsblk`, `nmcli`, `sudo mount/date/hwclock` will fail elsewhere. Syntax-check only on non-Linux.
- **Don't break signal/slot wiring.** Cross-thread signals are the backbone; adding a direct widget call from a background thread will crash.
- **The 2-second `time.sleep(2.0)` in `_bg_connect_wifi` is load-bearing.** It waits for wlan0 to switch from AP master → client managed mode.
- **LPR model loads async** in a daemon thread; `self.lpr_catcher` may be `None` for several seconds after startup.
- **`core_camera.py.bak`** is a prior-version backup — ignore it.
- **`TapSlider` / `TouchScrollListWidget`** inherit from `QSlider` / `QListWidget`; CSS selectors for the base classes still apply.
- **After any code change, commit with an English message** and push via SFTP for hardware testing.
- **DS3231 RTC** is the hardware clock; time changes must run `sudo hwclock --systohc` to persist across reboots.
