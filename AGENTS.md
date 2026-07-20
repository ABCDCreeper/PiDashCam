# AGENTS.md — DashCam (车载行车记录仪)

A PyQt5 fullscreen touch-screen dashcam application for Linux ARM SBCs (Raspberry Pi / similar). Features live camera capture, loop recording to USB, video playback with AI license-plate recognition (HyperLPR3), L76X GPS overlay, USB auto-mount/cleanup, WiFi client/hotspot via nmcli, system time sync with DS3231 RTC, and on-screen QWERTY keyboard.

## Build / Test / Lint

```bash
# System dependencies (Debian / Raspberry Pi OS)
sudo apt install python3-pyqt5 python3-opencv python3-psutil network-manager

# Python dependencies
pip install hyperlpr3

# L76X GPS (optional — app works without pyserial)
pip install pyserial

# Run (must be on the target Linux device with camera + touch screen)
python main.py

# Syntax-check all modules
python -m py_compile main.py core_camera.py ui_mainwindow.py ui_wifi.py ui_keyboard.py touch_widgets.py
```

No unit tests, linter config, or CI. `py_compile` is the sole pre-deploy check. GPS serial requires `/dev/serial0` at 9600 baud (微雪 L76X default).

## Architecture

```
main.py                 Entry point: QApplication, BlankCursor, showFullScreen
  │                       + starts DashcamWebServer on port 8080
  ├─ ui_mainwindow.py    DashcamMainWindow — 3-page QStackedWidget shell
  │                       + TimeSettingDialog (fullscreen RTC time setter)
  ├─ core_camera.py       CameraWorker (cv2 capture → QImage signals)
  │                       SystemUtils (lsblk USB scanner + stale-mount cleanup)
  │                       GPSWorker (L76X NMEA 0183 parser → serial → GPS OSD)
  ├─ ui_wifi.py           WifiScannerDialog + WifiWorker (nmcli scan + connect)
  ├─ ui_keyboard.py       TouchKeyboard (fullscreen on-screen keyboard)
  ├─ touch_widgets.py     TapSlider, TouchScrollListWidget
  └─ web_server.py        DashcamWebServer — HTTP API + 管理网页后台
                           static/index.html — 单页管理仪表盘前端
```

### Component map

| File | Class(es) | Role |
|------|-----------|------|
| `main.py` | — | Hide cursor, launch fullscreen window |
| `core_camera.py` | `SystemUtils` | `lsblk`-based USB discovery; auto-mount with `sudo mkdir -p` + `chmod 777`; stale `/mnt/usb_*` cleanup |
| `core_camera.py` | `GPSWorker` | Daemon thread reads `/dev/serial0`, parses `$GPRMC` (lat/lon/speed) and `$GPGGA` (satellites/fix quality), emits dict via `gps_data_updated` signal |
| `core_camera.py` | `CameraWorker` | Background thread: cv2 capture → timestamp + GPS OSD overlay → LPR (HyperLPR3, ≥0.85 conf) → QImage signals. 3-min `.mkv` loop recording; video playback with seek; USB hot-plug with auto-switch and write-test fallback; disk cleanup at 88% full or <1.5GB free; plates_log.json |
| `ui_mainwindow.py` | `DashcamMainWindow` | 3-page stack: main (record), settings (storage bar + GPS status + WiFi/time), playback (TapSlider + list). OSD overlays: AI plate, network status, "no USB" fullscreen warning. Auto-starts recording 1s after boot if USB present |
| `ui_mainwindow.py` | `TimeSettingDialog` | Fullscreen date/time adjuster with `◀ ▶` buttons; sets system clock via `sudo date -s` then syncs DS3231 via `sudo hwclock --systohc` |
| `ui_wifi.py` | `WifiWorker` | Background nmcli scan (`--rescan yes`, 12s timeout, deduped SSIDs) |
| `ui_wifi.py` | `WifiScannerDialog` | Fullscreen nmcli WiFi scanner using TouchScrollListWidget; accepts selected SSID |
| `ui_keyboard.py` | `TouchKeyboard` | Fullscreen QWERTY + numeric keyboard with 确定/取消/退格/清空/空格 |
| `touch_widgets.py` | `TapSlider` | Tap groove to jump (not just drag handle); only fires when click is outside handle rect |
| `touch_widgets.py` | `TouchScrollListWidget` | 10px drag threshold to distinguish scroll from item click; proportional scroll sensitivity |
| `web_server.py` | `DashcamWebServer` | Daemon-thread HTTP server on port 8080 with REST API (status, videos, plates, snapshot, network, recording control) and static file serving |
| `static/index.html` | — | Single-page management dashboard: system status, video gallery + player, plate log browser, network panel |

### Data flow

1. **Live preview**: `CameraWorker._loop()` reads cv2 frame → overlays timestamp (LINE_8 black outline + LINE_AA white core), GPS coords/speed/satellites, REC dot, plate bbox → `frame_ready.emit(q_img, osd)` → `update_monitor()` renders scaled QPixmap
2. **GPS**: `GPSWorker._read_loop()` reads serial chunks → `_parse_nmea()` dispatches to `_parse_rmc()` / `_parse_gga()` → `gps_data_updated.emit(dict)` → `CameraWorker._on_gps_data()` stores in `self.gps_data` → `_loop()` overlays on frame; also forwarded to `DashcamMainWindow.update_gps_status()` for settings page
3. **Recording**: `_handle_recording()` writes frames via `cv2.VideoWriter` (mp4v fourcc) to 3-min `.mkv` segments on USB storage; auto-deletes oldest file when disk >88% full or <1.5 GB free; `_check_and_clean_disk()` runs on segment boundaries
4. **Storage switching**: `switch_storage_path()` saves to old location, sets `_pending_storage_path`; next segment tries new path, falls back to old on failure; `_auto_select_usb()` scans every 90 frames (~3s) for hot-plug; write-tests with `.dashcam_write_test` before committing
5. **Playback**: user taps video → `play_selected_video()` → `_run_playback()` streams file, emits `playback_progress` → slider + time label; overlays historical plate detections from matched `plates_log.json` entries (within ±3s window)
6. **Seek**: slider move → `on_slider_moved(val)` → `worker.seek_frame = val` → next loop `cap.set(POS_FRAMES)`
7. **WiFi / time**: `nmcli`, `date`, `hwclock` subprocess calls in daemon threads → cross-thread `osd_signal` / `net_status_signal` back to UI; password memory in `~/Videos/wifi_passwords.json`

### Thread safety

All background work (camera, LPR, GPS serial, nmcli, disk) runs in `threading.Thread(daemon=True)`. PyQt5 signals carry results to the UI thread. Never touch widgets from background threads.

## Key Files

- `main.py` — entry point (~24 lines)
- `core_camera.py` — camera, recording, playback, USB scan + cleanup, GPS parsing, LPR, plate logging (~899 lines, heaviest)
- `ui_mainwindow.py` — main window, OSD overlays, storage UI, GPS status, WiFi flow, time dialog, network polling (~999 lines)
- `ui_wifi.py` — WiFi scan dialog + nmcli worker (~109 lines)
- `ui_keyboard.py` — on-screen keyboard (~73 lines)
- `touch_widgets.py` — TapSlider + TouchScrollListWidget (~89 lines)
- `web_server.py` — HTTP REST API server + management dashboard backend (~410 lines)
- `static/index.html` — Web management dashboard frontend (~625 lines)

Runtime data stored under `~/Videos/`: recordings, `wifi_passwords.json`, `storage_config.json`, `plates_log.json`.

## Coding Conventions

- Python 3 with Chinese UI labels / comments
- PyQt5, programmatic UI only (no Qt Designer `.ui` files)
- Inline QSS stylesheets; dark theme (#000 background, #0073e6 accent)
- `snake_case` methods/variables, `CamelCase` classes; `btn_`, `lbl_` widget prefixes
- `pyqtSignal` for cross-thread; emoji in OSD messages and UI labels
- `subprocess.run(["sudo", ...])` for system ops; Linux-only, no Windows compat
- Broad `try/except` with `print()` diagnostics; no structured logging
- OpenCV `cv2.LINE_8` for timestamp outlines (no grey anti-aliasing), `cv2.LINE_AA` for core text
- LPR detection threshold: confidence > 0.85; inference throttled to once per 400ms; plates logged every 10s max to `plates_log.json`

## Git Workflow

- Branch: `main` only, no PRs, no CI
- **Commit messages in English** (short imperative summary)
- Deploy: direct push to target device via SFTP

## Tips for AI Agents

- **Linux-only code.** `lsblk`, `nmcli`, `sudo mount/date/hwclock`, `/dev/serial0` will fail elsewhere. Syntax-check only on non-Linux.
- **Don't break signal/slot wiring.** Cross-thread signals are the backbone; adding a direct widget call from a background thread will crash. The signal list: `frame_ready`, `playback_progress`, `storage_notify`, `storage_path_changed`, `recording_toggled`, `gps_data_updated`, `osd_signal`, `net_status_signal`.
- **The 2-second `time.sleep(2.0)` in `_bg_connect_wifi` is load-bearing.** It waits for wlan0 to switch from AP master → client managed mode.
- **LPR model loads async** in a daemon thread; `self.lpr_catcher` may be `None` for several seconds after startup. LPR inference runs on a separate thread per frame; `self.lpr_busy` guards against concurrent inference.
- **GPS serial is optional.** If `pyserial` is not installed or `/dev/serial0` fails, `GPSWorker` emits an empty dict with `serial_error` set. Code should gracefully degrade — GPS OSD overlay is skipped entirely when `serial_error` is not None.
- **USB auto-mount uses `sudo chmod -R 777`** on `/mnt/usb_*` directories after mount. This is intentional for the root-running Pi setup — do not replace with finer-grained permissions without confirming the deployment user context.
- **`core_camera.py.bak`** is a prior-version backup — ignore it.
- **`TapSlider` / `TouchScrollListWidget`** inherit from `QSlider` / `QListWidget`; CSS selectors for the base classes still apply.
- **After any code change, commit with an English message** and push via SFTP for hardware testing.
- **DS3231 RTC** is the hardware clock; time changes must run `sudo hwclock --systohc` to persist across reboots.
- **Plates log** caps at 3000 entries; `plates_log.json` lives on the USB storage path, not `~/Videos/`.
- **Network polling** runs every 5 seconds via QTimer; uses `nmcli -t connection show --active` to detect 802-11-wireless or wifi connections.
