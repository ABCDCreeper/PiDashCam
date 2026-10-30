#!/usr/bin/env bash
# Raspberry Pi OS Lite / Debian 12+ arm64 appliance installer.
set -Eeuo pipefail
export DEBIAN_FRONTEND=noninteractive
REPO=https://github.com/ABCDCreeper/PiDashCam.git
REF=main
START=1
HARDWARE=0
CHECK=0
SOURCE=
MODEL_DIR=
WHEELHOUSE=
BASE=/opt/pidashcam
DATA=/var/lib/pidashcam
STAGE=
PREVIOUS=
SWITCHED=0
UNIT_BACKUP=
usage() {
    cat <<'EOF'
Usage: sudo bash deploy.sh [--ref BRANCH_OR_TAG] [--no-start] [--enable-hardware] [--check] [--source DIR] [--model-dir DIR] [--wheelhouse DIR]
  --source            Deploy a clean local Git checkout, without contacting GitHub
  --model-dir         Directory containing the four verified ONNX model files
  --wheelhouse        Install Python dependencies offline from this directory
  --ref               Git branch/tag to deploy (default: main)
  --no-start          Install and enable service, start on next boot
  --enable-hardware   Enable Pi I2C, GPS UART, DS3231 RTC; reboot manually afterward
  --check             Read-only platform checks, no installation
Repeated runs update the application, preserving /var/lib/pidashcam and USB recordings.
EOF
}
while (($#)); do
    case "$1" in
        --ref) [[ $# -ge 2 && -n $2 && $2 != -* ]] || { usage; exit 2; }; REF=$2; shift 2 ;;
        --source|--model-dir|--wheelhouse)
            [[ $# -ge 2 && -d $2 ]] || { usage; exit 2; }
            case "$1" in
                --source) SOURCE=$(realpath "$2") ;;
                --model-dir) MODEL_DIR=$(realpath "$2") ;;
                --wheelhouse) WHEELHOUSE=$(realpath "$2") ;;
            esac
            shift 2 ;;
        --no-start) START=0; shift ;;
        --enable-hardware) HARDWARE=1; shift ;;
        --check) CHECK=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) printf 'Unknown option: %s\n' "$1" >&2; usage; exit 2 ;;
    esac
done
[[ -r /etc/os-release ]] || { echo 'Linux with /etc/os-release required.' >&2; exit 1; }
# shellcheck disable=SC1091
source /etc/os-release
[[ ${ID:-} == debian || ${ID:-} == raspbian ]] || { echo 'Use Raspberry Pi OS Lite / Debian 12 or newer.' >&2; exit 1; }
[[ ${VERSION_ID%%.*} =~ ^[0-9]+$ && ${VERSION_ID%%.*} -ge 12 ]] || { echo 'Debian 12+ required.' >&2; exit 1; }
[[ $(dpkg --print-architecture) == arm64 ]] || { echo '64-bit ARM (arm64) required; 32-bit onnxruntime is not supported.' >&2; exit 1; }
[[ -d /run/systemd/system ]] || { echo 'A running systemd system is required.' >&2; exit 1; }
if ((CHECK)); then
    echo "Platform OK: ${PRETTY_NAME}; arm64. No changes made."
    exit 0
fi
[[ $EUID -eq 0 ]] || { echo 'Run with sudo bash deploy.sh.' >&2; exit 1; }
if ((START)) && systemctl is-active --quiet display-manager; then
    echo 'An active desktop owns the screen. Use Raspberry Pi OS Lite, or install with --no-start.' >&2
    exit 1
fi
# Serialize installers without interrupting the current application.
exec 9>/run/pidashcam-deploy.lock
flock -n 9 || { echo 'Another deployment is running.' >&2; exit 1; }
cleanup() {
    [[ -z $STAGE || ! -d $STAGE ]] || rm -rf -- "$STAGE"
}
rollback() {
    local status=$?
    trap - ERR
    set +e
    echo "Deployment failed (exit $status)." >&2
    if ((SWITCHED)); then
        if [[ -n $UNIT_BACKUP ]]; then
            cp -- "$UNIT_BACKUP" /etc/systemd/system/pidashcam.service
        fi
        if [[ -n $PREVIOUS ]]; then
            ln -sfn -- "$PREVIOUS" "$BASE/current.rollback"
            mv -Tf -- "$BASE/current.rollback" "$BASE/current"
            systemctl daemon-reload
            # Legacy installers served port 80 themselves. Release that port
            # before restoring their unit if this update had started nginx.
            if ! grep -q 'DASHCAM_HTTP_PORT=8080' /etc/systemd/system/pidashcam.service; then
                systemctl disable --now nginx.service
            fi
            systemctl restart pidashcam.service
            echo "Restored previous application: $PREVIOUS" >&2
        else
            systemctl disable --now pidashcam.service
            [[ ! -L $BASE/current ]] || rm -f -- "$BASE/current"
        fi
    fi
    exit "$status"
}
trap cleanup EXIT
trap rollback ERR
if [[ -n $SOURCE ]]; then
    git -C "$SOURCE" rev-parse --verify HEAD >/dev/null
    [[ -z $(git -C "$SOURCE" status --porcelain) ]] || { echo 'Commit local source changes before deploying.' >&2; exit 1; }
fi
apt-get -o Acquire::Retries=3 update
apt-get install -y --no-install-recommends \
    ca-certificates curl git sudo python3-venv python3-pip python3-pyqt5 \
    python3-opencv python3-av python3-numpy python3-psutil python3-serial \
    network-manager ffmpeg v4l-utils i2c-tools cage seatd libpam-systemd \
    fonts-noto-cjk qtwayland5 exfatprogs util-linux util-linux-extra nginx-light apache2-utils openssl
# Raspberry Pi CSI sensors expose raw Unicam nodes; use libcamera processing.
if apt-cache show python3-picamera2 >/dev/null 2>&1; then
    apt-get install -y --no-install-recommends python3-picamera2
fi
install -d -m 0755 "$BASE/releases"
install -d -m 0700 "$DATA" "$DATA/Videos" "$DATA/.cache"
STAGE=$(mktemp -d "$BASE/releases/.staging.XXXXXX")
if [[ -n $SOURCE ]]; then
    git clone --no-hardlinks "$SOURCE" "$STAGE/app"
else
    git clone --depth 1 --branch "$REF" --single-branch "$REPO" "$STAGE/app"
fi
REV=$(git -C "$STAGE/app" rev-parse HEAD)
RELEASE="$BASE/releases/$(date -u +%Y%m%dT%H%M%S)-${REV:0:12}"
[[ ! -e $RELEASE ]] || RELEASE="$RELEASE-$$"
mv -- "$STAGE" "$RELEASE"
STAGE=$RELEASE
python3 -m venv --system-site-packages "$STAGE/venv"
PIP_ARGS=()
if [[ -n $WHEELHOUSE ]]; then PIP_ARGS=(--no-index --find-links "$WHEELHOUSE"); fi
# Use distribution Qt/OpenCV/NumPy; avoid a pip OpenCV wheel replacing Qt plugins.
"$STAGE/venv/bin/python" -m pip install --disable-pip-version-check \
    "${PIP_ARGS[@]}" --only-binary=:all: 'onnxruntime==1.23.2' tqdm requests fastapi uvicorn python-multipart loguru
"$STAGE/venv/bin/python" -m pip install --disable-pip-version-check "${PIP_ARGS[@]}" --no-deps 'hyperlpr3==0.1.3'
(
    cd "$STAGE/app"
    "$STAGE/venv/bin/python" -m py_compile main.py core_camera.py camera_capture.py ui_mainwindow.py \
        ui_wifi.py ui_keyboard.py touch_widgets.py web_server.py network_manager.py
)
# Prepare verified models under the runtime HOME, not the installing user's HOME.
MODEL_ARGS=()
if [[ -n $MODEL_DIR ]]; then MODEL_ARGS=(--source "$MODEL_DIR"); fi
python3 "$STAGE/app/scripts/prepare_models.py" --data "$DATA" "${MODEL_ARGS[@]}"
# Load bundled models before touching the live service. No camera/USB operations.
HOME="$DATA" QT_QPA_PLATFORM=offscreen "$STAGE/venv/bin/python" - <<'PY'
import cv2, psutil, serial, onnxruntime
from PyQt5.QtWidgets import QApplication
import hyperlpr3
app = QApplication([])
model = hyperlpr3.LicensePlateCatcher()
import numpy as np
model(np.zeros((480, 640, 3), dtype=np.uint8))
print('Qt, OpenCV, serial, ONNX and plate models: OK')
PY
if ((HARDWARE)); then
    command -v raspi-config >/dev/null || { echo '--enable-hardware requires Raspberry Pi OS raspi-config.' >&2; exit 1; }
    raspi-config nonint do_i2c 0
    raspi-config nonint do_serial_hw 0
    raspi-config nonint do_serial_cons 1
    BOOT=/boot/firmware/config.txt
    [[ -f $BOOT ]] || BOOT=/boot/config.txt
    [[ -f $BOOT ]] || { echo 'Boot config not found.' >&2; exit 1; }
    if ! grep -qxF 'dtoverlay=i2c-rtc,ds3231' "$BOOT"; then
        printf '\n[all]\ndtoverlay=i2c-rtc,ds3231\n' >> "$BOOT"
    fi
fi
cat > "$RELEASE/pidashcam.service" <<'UNIT'
[Unit]
Description=PiDashCam touchscreen recorder
After=network.target systemd-logind.service seatd.service
Wants=seatd.service
Conflicts=getty@tty1.service
StartLimitIntervalSec=60
StartLimitBurst=5

[Service]
Type=simple
User=root
WorkingDirectory=/opt/pidashcam/current/app
Environment=HOME=/var/lib/pidashcam
Environment=XDG_CACHE_HOME=/var/lib/pidashcam/.cache
Environment=XDG_RUNTIME_DIR=/run/pidashcam
Environment=QT_QPA_PLATFORM=wayland
Environment=LIBSEAT_BACKEND=seatd
Environment=WLR_BACKEND=drm
Environment=PYTHONUNBUFFERED=1
Environment=DASHCAM_HTTP_HOST=127.0.0.1
Environment=DASHCAM_HTTP_PORT=8080
Environment=DASHCAM_PUBLIC_PORT=80
Environment=DASHCAM_REVISION=@REVISION@
RuntimeDirectory=pidashcam
RuntimeDirectoryMode=0700
UMask=0077
TTYPath=/dev/tty1
TTYReset=yes
TTYVHangup=yes
TTYVTDisallocate=yes
StandardInput=tty-force
StandardOutput=journal
StandardError=journal
ExecStart=/usr/bin/cage -s -m last -- /opt/pidashcam/current/venv/bin/python /opt/pidashcam/current/app/main.py
Restart=always
RestartSec=5
TimeoutStopSec=20
KillMode=mixed

[Install]
WantedBy=multi-user.target
UNIT
sed -i "s/@REVISION@/$REV/" "$RELEASE/pidashcam.service"
systemd-analyze verify "$RELEASE/pidashcam.service"
STAGE=
if [[ -L $BASE/current && -d $BASE/current ]]; then
    PREVIOUS=$(readlink -f "$BASE/current")
fi
if [[ -f /etc/systemd/system/pidashcam.service ]]; then
    UNIT_BACKUP="$RELEASE/previous.service"
    cp /etc/systemd/system/pidashcam.service "$UNIT_BACKUP"
fi
# Existing recordings and Wi-Fi connections are left in place.
systemctl enable --now seatd.service
systemctl enable NetworkManager.service
# Configure authenticated LAN entry before starting the loopback-only application.
# No SSH account, host-key or login policy changes on an existing installation.
bash "$RELEASE/app/scripts/configure_web.sh"
SWITCHED=1
install -m 0644 "$RELEASE/pidashcam.service" /etc/systemd/system/pidashcam.service
ln -sfn -- "$RELEASE" "$BASE/current.next"
mv -Tf -- "$BASE/current.next" "$BASE/current"
systemctl daemon-reload
systemctl enable pidashcam.service
if ((START)); then
    systemctl restart pidashcam.service
    systemctl restart nginx.service
    HEALTHY=0
    for ((attempt=0; attempt<30; attempt++)); do
        if curl --fail --silent --max-time 2 http://127.0.0.1:8080/api/status > "$RELEASE/health.json" &&
            python3 -c 'import json,sys; assert json.load(open(sys.argv[1]))["deployment_revision"] == sys.argv[2]' "$RELEASE/health.json" "$REV" &&
            systemctl is-active --quiet pidashcam.service; then
            HEALTHY=$((HEALTHY + 1))
            if ((HEALTHY >= 3)); then break; fi
        else
            HEALTHY=0
        fi
        sleep 1
    done
    if ((HEALTHY < 3)); then
        journalctl -u pidashcam.service -n 40 --no-pager >&2
        false # ERR trap restores the previous release.
    fi
fi
printf '\nDeployed %s\nData: %s\nLogs: sudo journalctl -u pidashcam -f\n' "$REV" "$DATA"
echo 'Web: http://<device-IP> (admin; read password with sudo cat /var/lib/pidashcam/admin-password)'
if ((HARDWARE)); then echo 'Hardware settings take effect after a manual reboot.'; fi
if ((!START)); then
    # Keep any currently running legacy port-80 service until the next boot.
    if ! systemctl is-active --quiet pidashcam.service; then systemctl restart nginx.service; fi
fi
if ((!START)); then echo 'Installed; start with sudo systemctl start pidashcam or reboot.'; fi
