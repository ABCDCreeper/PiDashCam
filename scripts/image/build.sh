#!/usr/bin/env bash
# Run on an ARM64 Linux build host, not on a live target device.
set -Eeuo pipefail
[[ $EUID -eq 0 ]] || { echo 'Run as root on the isolated ARM64 build runner.' >&2; exit 1; }
[[ $(uname -m) == aarch64 ]] || { echo 'Native ARM64 runner required.' >&2; exit 1; }
SOURCE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
OUTPUT=${IMAGE_OUTPUT:-"$SOURCE/image-output"}
mkdir -p "$OUTPUT"
WORK=$(mktemp -d)
ROOT="$WORK/root"
LOOP=
cleanup() {
    local status=$?
    trap - EXIT
    set +e
    if mountpoint -q "$ROOT"; then
        umount -R "$ROOT" || { echo "Unmount failed; preserving $WORK for recovery." >&2; exit 1; }
    fi
    if [[ -n $LOOP ]]; then losetup -d "$LOOP"; fi
    rm -rf -- "$WORK"
    exit "$status"
}
trap cleanup EXIT
BASE_NAME=2026-10-06-raspios-trixie-arm64-lite.img.xz
BASE_URL="https://downloads.raspberrypi.com/raspios_lite_arm64/images/raspios_lite_arm64-2026-10-06/$BASE_NAME"
BASE_SHA=483db18a48da399b5b7022ffae9a07bc6d99daab30e593aedac09b69ff422843
# Keep enough room for rootfs growth, package installation and compressed output.
FREE=$(df -PB1 "$OUTPUT" | awk 'NR==2 {print $4}')
(( FREE >= 20*1024*1024*1024 )) || { echo 'At least 20 GiB free build space required.' >&2; exit 1; }
curl --fail --location --retry 3 --connect-timeout 30 --max-time 1800 "$BASE_URL" -o "$WORK/base.img.xz"
printf '%s  %s\n' "$BASE_SHA" "$WORK/base.img.xz" | sha256sum --check --strict
xz --decompress "$WORK/base.img.xz"
IMAGE="$OUTPUT/PiDashCam-RPi4B-arm64-800x480.img"
mv "$WORK/base.img" "$IMAGE"
truncate -s 8G "$IMAGE"
printf ',+\n' | sfdisk --no-reread -N 2 "$IMAGE"
LOOP=$(losetup --find --show --partscan "$IMAGE")
udevadm settle
[[ -b ${LOOP}p1 && -b ${LOOP}p2 ]] || { echo 'Image partitions unavailable.' >&2; exit 1; }
set +e
e2fsck -pf "${LOOP}p2"
FS_STATUS=$?
set -e
(( FS_STATUS <= 1 )) || exit "$FS_STATUS"
resize2fs "${LOOP}p2"
mkdir -p "$ROOT"
mount "${LOOP}p2" "$ROOT"
mount "${LOOP}p1" "$ROOT/boot/firmware"
mount --rbind /dev "$ROOT/dev"
mount --make-rslave "$ROOT/dev"
mount -t proc proc "$ROOT/proc"
mount --bind /sys "$ROOT/sys"
mount -o remount,bind,ro "$ROOT/sys"
# Keep resolver changes temporary; no runner credentials enter the image.
cp -a "$ROOT/etc/resolv.conf" "$WORK/resolv.conf"
rm -f "$ROOT/etc/resolv.conf"
cp -L /etc/resolv.conf "$ROOT/etc/resolv.conf"
printf '#!/bin/sh\nexit 101\n' > "$ROOT/usr/sbin/policy-rc.d"
chmod 0755 "$ROOT/usr/sbin/policy-rc.d"
install -d "$ROOT/opt/pidashcam/releases/image/app"
git -C "$SOURCE" archive HEAD | tar -x -C "$ROOT/opt/pidashcam/releases/image/app"
install -m 0700 "$SOURCE/scripts/image/provision.sh" "$ROOT/root/image-provision.sh"
REVISION=$(git -C "$SOURCE" rev-parse HEAD)
chroot "$ROOT" /usr/bin/env -i HOME=/root PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    DEBIAN_FRONTEND=noninteractive SYSTEMD_OFFLINE=1 SOURCE_REVISION="$REVISION" \
    /bin/bash /root/image-provision.sh
rm -f "$ROOT/usr/sbin/policy-rc.d" "$ROOT/root/image-provision.sh" "$ROOT/etc/resolv.conf"
cp -a "$WORK/resolv.conf" "$ROOT/etc/resolv.conf"
# Image-wide settings: primary micro-HDMI connector next to the USB-C power socket.
cat >> "$ROOT/boot/firmware/config.txt" <<'BOOT'

[all]
disable_overscan=1
enable_uart=1
dtparam=i2c_arm=on
BOOT
python3 - "$ROOT/boot/firmware/cmdline.txt" <<'PY'
from pathlib import Path
import sys
p=Path(sys.argv[1]);tokens=p.read_text().strip().split()
tokens=[t for t in tokens if not t.startswith(('console=serial','console=ttyAMA','video=HDMI-A-1:'))]
tokens.append('video=HDMI-A-1:800x480@60D')
p.write_text(' '.join(tokens)+'\n')
PY
cp "$SOURCE/scripts/image/FLASHING.md" "$ROOT/boot/firmware/PIDASHCAM-README.txt"
cp "$SOURCE/scripts/image/FLASHING.md" "$OUTPUT/FLASHING.md"
# Per-device credentials and host identity are generated on the Pi, never in CI.
rm -f "$ROOT/etc/ssh/ssh_host_"* "$ROOT/var/lib/systemd/random-seed"
truncate -s 0 "$ROOT/etc/machine-id"
rm -f "$ROOT/var/lib/dbus/machine-id"
ln -s /etc/machine-id "$ROOT/var/lib/dbus/machine-id"
rm -rf "$ROOT/root/.cache" "$ROOT/var/cache/apt/archives/"*.deb
rm -f "$ROOT/var/lib/pidashcam/admin-password" "$ROOT/var/lib/pidashcam/.firstboot-complete"
python3 - "$ROOT" "$OUTPUT/build-manifest.json" "$BASE_SHA" "$REVISION" <<'PY'
import json,sys
from pathlib import Path
root=Path(sys.argv[1]);smoke=json.loads((root/'var/lib/pidashcam/build-smoke.json').read_text())
assert smoke['model_inference'] and smoke['h264_encoding']
data=dict(target='Raspberry Pi 4B 8GB',architecture='arm64',display='HDMI-A-1 800x480@60',
          base_sha256=sys.argv[3],source_revision=sys.argv[4],smoke=smoke,
          hardware_boot_tested=False,credentials='generated on first boot')
Path(sys.argv[2]).write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')
PY
sync
umount -R "$ROOT"
losetup -d "$LOOP"
LOOP=
# Filesystem check after all writes; never upload an image that failed verification.
LOOP=$(losetup --find --show --read-only --partscan "$IMAGE")
e2fsck -fn "${LOOP}p2"
fsck.vfat -n "${LOOP}p1"
losetup -d "$LOOP"
LOOP=
xz -T2 -3 "$IMAGE"
(cd "$OUTPUT" && sha256sum ./*.img.xz > SHA256SUMS)
chmod a+r "$OUTPUT/"*
echo "Image complete: $IMAGE.xz"
