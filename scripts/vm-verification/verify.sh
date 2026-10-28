#!/usr/bin/env bash
set -Eeuo pipefail
[[ $EUID == 0 && $(uname -m) == aarch64 ]] || { echo 'Use the isolated ARM64 verification runner.' >&2; exit 1; }
SOURCE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
RESULTS="$SOURCE/vm-results"
mkdir -p "$RESULTS"
WORK=$(mktemp -d)
ROOT="$WORK/root"
LOOP=
cleanup() {
    local status=$?
    trap - EXIT
    set +e
    if mountpoint -q "$ROOT"; then umount -R "$ROOT" || { echo "Keeping $WORK: unmount failed" >&2; exit 1; }; fi
    if [[ -n $LOOP ]]; then losetup -d "$LOOP"; fi
    rm -rf -- "$WORK"
    exit "$status"
}
trap cleanup EXIT
(cd "$SOURCE/vm-artifact" && sha256sum -c SHA256SUMS)
xz -dc "$SOURCE/vm-artifact/PiDashCam-RPi4B-arm64-800x480.img.xz" > "$WORK/original.img"
# The downloaded .img.xz is immutable; all boot-time changes go to this test copy.
cp --reflink=auto --sparse=always "$WORK/original.img" "$WORK/test.img"
LOOP=$(losetup --find --show --partscan "$WORK/test.img")
udevadm settle
mkdir -p "$ROOT"
mount "${LOOP}p2" "$ROOT"
mount "${LOOP}p1" "$ROOT/boot/firmware"
cp "$ROOT/boot/firmware/kernel8.img" "$WORK/kernel8.img"
cp "$ROOT/boot/firmware/bcm2711-rpi-4-b.dtb" "$WORK/rpi4.dtb"
ROOT_PARTUUID=$(blkid -s PARTUUID -o value "${LOOP}p2")
# Skip interactive enrollment only on the disposable test copy, never on the artifact.
ln -sfn /dev/null "$ROOT/etc/systemd/system/userconfig.service"
install -m 0644 "$SOURCE/scripts/vm-verification/guest-probe.py" "$ROOT/root/vm-guest-probe.py"
cat > "$ROOT/etc/systemd/system/pidashcam-vm-probe.service" <<'UNIT'
[Unit]
Description=Disposable image verification probe
After=nginx.service pidashcam.service pidashcam-firstboot.service
Wants=nginx.service pidashcam-firstboot.service
[Service]
Type=oneshot
Environment=HOME=/var/lib/pidashcam
Environment=QT_QPA_PLATFORM=offscreen
ExecStart=/opt/pidashcam/current/venv/bin/python /root/vm-guest-probe.py
StandardOutput=journal+console
StandardError=journal+console
TimeoutStartSec=600
[Install]
WantedBy=multi-user.target
UNIT
ln -s /etc/systemd/system/pidashcam-vm-probe.service "$ROOT/etc/systemd/system/multi-user.target.wants/pidashcam-vm-probe.service"
sync
umount -R "$ROOT"
losetup -d "$LOOP"
LOOP=
python3 "$SOURCE/scripts/vm-verification/supervise.py" "$WORK" "$RESULTS" "$ROOT_PARTUUID" raspi4b
if [[ -f $RESULTS/raspi4b-result.json ]]; then
    python3 - "$RESULTS/raspi4b-result.json" <<'PY'
import json,sys
j=json.load(open(sys.argv[1]));assert j['passed'],j
PY
else
    echo 'Pi 4 model could not complete the probe. Trying the same rootfs with a generic ARM virtual board.'
    # Generic ARM kernel changes are restricted to the disposable test copy.
    # This fallback does not validate Pi firmware, Pi kernel, HDMI or touch devices.
    apt-get update
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends linux-image-generic
    KERNEL=$(find /boot -maxdepth 1 -name 'vmlinuz-*-generic' | sort -V | tail -1)
    [[ -n $KERNEL ]] || { echo 'Generic ARM kernel unavailable.' >&2; exit 1; }
    VERSION=${KERNEL##*/vmlinuz-}
    cp "$KERNEL" "$WORK/virt-kernel"
    cp "/boot/initrd.img-$VERSION" "$WORK/virt-initrd"
    LOOP=$(losetup --find --show --partscan "$WORK/test.img")
    udevadm settle
    mount "${LOOP}p2" "$ROOT"
    mkdir -p "$ROOT/usr/lib/modules"
    cp -a "/lib/modules/$VERSION" "$ROOT/usr/lib/modules/"
    sync
    umount "$ROOT"
    losetup -d "$LOOP"
    LOOP=
    python3 "$SOURCE/scripts/vm-verification/supervise.py" "$WORK" "$RESULTS" "$ROOT_PARTUUID" virt
    python3 - "$RESULTS/virt-result.json" <<'PY'
import json,sys
j=json.load(open(sys.argv[1]));assert j['passed'],j
PY
fi
