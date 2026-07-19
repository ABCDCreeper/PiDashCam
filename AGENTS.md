# AGENTS.md — DashCam (车载行车记录仪)

A PyQt5 fullscreen touch-screen dashcam application for Linux ARM SBCs (Raspberry Pi / similar). Features live camera capture, loop recording, video playback with AI license-plate recognition (HyperLPR3), USB storage auto-mount, WiFi client/hotspot management via nmcli.

## Build / Test / Lint

```bash
# Install system dependencies (Debian/Raspberry Pi OS)
sudo apt install python3-pyqt5 python3-opencv python3-psutil network-manager

# Install Python dependencies (prefer system packages; pip fallback)
pip install hyperlpr3

# Run the app (must run on the target Linux device with camera + touch screen)
python main.py

# Syntax-check all modules
python -m py_compile main.py core_camera.py ui_mainwindow.py ui_wifi.py ui_keyboard.py touch_widgets.py
```

There are no unit tests, no linter config, and no CI. Syntax-check with `py_compile` is the only validation before deploying to hardware.

## Architecture

```
main.py              Entry point — QApplication, BlankCursor, showFullScreen
  └─ ui_mainwindow.py    DashcamMainWindow — primary UI shell
       ├─ core_camera.py   CameraWorker (cv2 capture → QImage signals)
       │                   SystemUtils (lsblk USB storage scanner)
       ├─ ui_wifi.py       WifiScannerDialog (nmcli scan + connect)
       ├─ ui_keyboard.py   TouchKeyboard (fullscreen on-screen keyboard)
       └─ touch_widgets.py TapSlider, TouchScrollListWidget (touch-friendly widgets)
```

### Component map

| File | Class(es) | Role |
|------|-----------|------|
| `main.py` | — | Boot: hide cursor, launch fullscreen window |
| `core_camera.py` | `SystemUtils` | `lsblk`-based USB storage discovery; auto-mount with sudo |
| `core_camera.py` | `CameraWorker` | Background thread: cv2 capture → timestamp overlay → LPR detection → QImage signals. Also handles recording (3-min segment .mkv) and video file playback with seek support |
| `ui_mainwindow.py` | `DashcamMainWindow` | 3-page QStackedWidget: main (record toggle), settings (storage + WiFi), playback (slider + video list). OSD overlays for network status, AI plate results |
| `ui_wifi.py` | `WifiScannerDialog` | Fullscreen nmcli WiFi scanner; saves passwords to `~/Videos/wifi_passwords.json`; handles AP→Client mode switch with 2s sleep for radio reset |
| `ui_keyboard.py` | `TouchKeyboard` | Fullscreen on-screen keyboard with QWERTY layout + numeric row |
| `touch_widgets.py` | `TapSlider` | QSlider subclass: tap groove to jump (not just drag handle) |
| `touch_widgets.py` | `TouchScrollListWidget` | QListWidget subclass: 10px drag threshold to distinguish scroll from click; suppresses `itemClicked` during scroll |

### Data flow

1. **Live preview**: `CameraWorker._loop()` reads cv2 frame → overlays timestamp, REC indicator, plate bounding box → `frame_ready.emit(q_img, osd)` → `DashcamMainWindow.update_monitor()` renders scaled QPixmap into `video_label`
2. **Recording**: `_handle_recording()` writes frames via cv2.VideoWriter to 3-minute `.mkv` segments in `self.storage_path` (default `~/Videos`, switchable via USB radio buttons)
3. **Playback**: user taps video in list → `play_selected_video()` sets `worker.playback_file` → `_run_playback()` reads file, emits `playback_progress(current, total, osd)` → slider + time label updated
4. **Seek**: slider move → `on_slider_moved(val)` → `worker.seek_frame = val` → next playback iteration calls `cap.set(POS_FRAMES)`
5. **WiFi**: `nmcli` subprocess in daemon threads → cross-thread `osd_signal` / `net_status_signal` for safe UI updates

### Thread safety pattern

All background work (camera capture, LPR, nmcli commands, disk scan) runs in `threading.Thread(daemon=True)`. PyQt5 signals (`pyqtSignal`) carry results back to the UI thread — never touch widgets from background threads.

## Key Files & Directories

- **`main.py`** — Entry point, cursor hiding, fullscreen launch
- **`core_camera.py`** — Camera capture, recording, playback, storage scanning (~390 lines, heaviest file)
- **`ui_mainwindow.py`** — Main window layout, OSD, network polling, WiFi connection flow (~454 lines)
- **`ui_wifi.py`** — WiFi scan dialog, nmcli worker (~109 lines)
- **`ui_keyboard.py`** — On-screen touch keyboard (~73 lines)
- **`touch_widgets.py`** — Custom touch-friendly QSlider + QListWidget (~89 lines)
- **`~/.vscode/sftp.json`** — SFTP deploy config (syncs to target device)
- **`~/Videos/`** — Default recording storage; also holds `wifi_passwords.json` and `plates_log.json`

## Coding Conventions

- **Language**: Python 3, with Chinese UI labels and comments
- **Widgets**: PyQt5 only (no Qt Designer `.ui` files); all UI built programmatically
- **Stylesheets**: heavy inline QSS via `setStyleSheet()`; dark theme (#000 background, #0073e6 accent)
- **Naming**: `snake_case` for methods/variables, `CamelCase` for classes; `btn_`, `lbl_`, `list_` prefixes on widgets
- **Signals**: `pyqtSignal` for cross-thread communication; emoji in OSD messages (⚡ ✅ ❌ 📡)
- **System calls**: `subprocess.run(["sudo", ...])` for nmcli, mount, mkdir, chmod; Linux-only, no Windows compat
- **Storage**: recording path defaults to `os.path.expanduser("~/Videos")`; USB devices auto-mounted under `/mnt/usb_<dev>` with sudo
- **Error handling**: broad `try/except` with `print()` diagnostics; no structured logging

## Git Workflow

Single-branch (`main`) with Chinese commit messages. No PRs, no CI, no branching convention. Direct pushes to the target device via SFTP (`.vscode/sftp.json`).

## Tips for AI Agents

- **This is Linux-only code.** Subprocess calls (`lsblk`, `nmcli`, `sudo mount`) will fail on macOS/Windows. Don't try to run it locally — syntax-check only.
- **Don't touch the signal/slot wiring.** Cross-thread `pyqtSignal` → `connect()` is the backbone that keeps the UI responsive during capture/playback/WiFi ops. Adding a direct widget call from a background thread will crash.
- **The 2-second `time.sleep(2.0)` in `_bg_connect_wifi` is load-bearing.** It waits for the single wlan0 interface to transition from AP master mode to client managed mode. Removing it causes connection failures.
- **LPR model loads asynchronously** in a daemon thread (`_load_lpr_model`). `self.lpr_catcher` may be `None` for several seconds after startup.
- **`core_camera.py.bak`** is a backup of a prior version — ignore it.
- **When editing stylesheets**, note that `QSlider` and `QListWidget` CSS selectors still apply because `TapSlider` and `TouchScrollListWidget` inherit from them.
- **The `.codewhale/` and `.deepseek/` dirs** are gitignored tooling artifacts — don't modify or remove them.
