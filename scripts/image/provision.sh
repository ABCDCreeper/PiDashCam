#!/usr/bin/env bash
# Executed inside the mounted ARM64 rootfs, with service startup disabled.
set -Eeuo pipefail
export DEBIAN_FRONTEND=noninteractive
APP=/opt/pidashcam/releases/image/app
VENV=/opt/pidashcam/releases/image/venv
DATA=/var/lib/pidashcam
apt-get update
apt-get install -y --no-install-recommends ca-certificates curl sudo python3-venv \
    python3-pip python3-pyqt5 python3-opencv python3-av python3-picamera2 python3-numpy python3-psutil python3-serial \
    network-manager ffmpeg v4l-utils i2c-tools cage seatd libpam-systemd fonts-noto-cjk \
    qtwayland5 exfatprogs util-linux util-linux-extra nginx-light apache2-utils openssl openssh-server
install -d -m 0700 "$DATA" "$DATA/Videos" "$DATA/.cache" "$DATA/.hyperlpr3"
python3 -m venv --system-site-packages "$VENV"
"$VENV/bin/python" -m pip install --only-binary=:all: 'onnxruntime==1.23.2' tqdm requests fastapi uvicorn python-multipart loguru
"$VENV/bin/python" -m pip install --no-deps 'hyperlpr3==0.1.3'
# Pin and verify the model files, so first boot does not require a download.
python3 - <<'PY'
import hashlib,subprocess
from pathlib import Path
folder=Path('/opt/pidashcam/models/20230229/onnx');folder.mkdir(parents=True,exist_ok=True)
commit='ea1d0b15cc17364e3afda2b13ec83fee18042549'
models={
 'litemodel_cls_96x_r1.onnx':'fe123688d3bf08b9ef029fcd57f3ac4644ac2e5ef8b9e7a676aacd5fad152142',
 'rpv3_mdict_160_r3.onnx':'35521a46fd8beaa6fd942f969bc666f4ef5c8433d2082eae002e6c94c5d307a2',
 'y5fu_320x_sim.onnx':'2a985dc63a5cc947ec36d18503d6fc0fd54525b9dba17f2fe29a71e86b44456d',
 'y5fu_640x_sim.onnx':'0306de937471b87f56eb3f5620815e7e7058f8ab7428e0734fc64627cc4d716c'}
for name,digest in models.items():
 path=folder/name
 subprocess.run(['curl','--fail','--location','--retry','3','--max-time','180',
  f'https://raw.githubusercontent.com/szad670401/HyperLPR/{commit}/resource/models/onnx/{name}',
  '-o',str(path)],check=True)
 assert hashlib.sha256(path.read_bytes()).hexdigest()==digest,name
Path('/var/lib/pidashcam/.hyperlpr3/20230229').symlink_to(folder.parent,target_is_directory=True)
PY
ln -sfn /opt/pidashcam/releases/image /opt/pidashcam/current
python3 - "$APP" "${SOURCE_REVISION:?}" <<'PY'
from pathlib import Path
import sys
app=Path(sys.argv[1])
source=(app/'deploy.sh').read_text()
unit=source.split("<<'UNIT'\n",1)[1].split('\nUNIT\n',1)[0]
unit=unit.replace('@REVISION@',sys.argv[2]).replace('Wants=seatd.service','Requires=seatd.service pidashcam-firstboot.service')
unit=unit.replace('Environment=PYTHONUNBUFFERED=1', 'Environment=PYTHONUNBUFFERED=1\nEnvironment=DASHCAM_HTTP_HOST=127.0.0.1\nEnvironment=DASHCAM_HTTP_PORT=8080\nEnvironment=DASHCAM_PUBLIC_PORT=80')
unit=unit.replace('After=network.target systemd-logind.service seatd.service','After=network.target systemd-logind.service seatd.service pidashcam-firstboot.service userconfig.service')
unit=unit.replace('RuntimeDirectoryMode=0700','RuntimeDirectoryMode=0700\nUMask=0077')
Path('/etc/systemd/system/pidashcam.service').write_text(unit+'\n')
PY
install -m 0700 "$APP/scripts/image/firstboot.sh" /usr/local/sbin/pidashcam-firstboot
cat > /etc/systemd/system/pidashcam-firstboot.service <<'UNIT'
[Unit]
Description=Generate unique PiDashCam credentials and SSH keys
After=local-fs.target
Before=nginx.service pidashcam.service ssh.service
ConditionPathExists=!/var/lib/pidashcam/.firstboot-complete
[Service]
Type=oneshot
ExecStart=/usr/local/sbin/pidashcam-firstboot
RemainAfterExit=yes
[Install]
WantedBy=multi-user.target
UNIT
install -d /etc/systemd/system/nginx.service.d
cat > /etc/systemd/system/nginx.service.d/pidashcam.conf <<'UNIT'
[Unit]
Requires=pidashcam-firstboot.service
After=pidashcam-firstboot.service
UNIT
rm -f /etc/nginx/sites-enabled/default
# Empty password database denies all access until firstboot creates credentials.
install -m 0640 -o root -g www-data /dev/null /etc/nginx/dashcam.htpasswd
cat > /etc/nginx/sites-available/pidashcam <<'NGINX'
server {
    listen 80 default_server;
    server_name _;
    auth_basic "PiDashCam";
    auth_basic_user_file /etc/nginx/dashcam.htpasswd;
    # Only same-origin browser access; reject cross-site state-changing requests.
    set $request_origin $http_origin;
    if ($http_origin = "") { set $request_origin "$scheme://$http_host"; }
    if ($request_origin != "$scheme://$http_host") { return 403; }
    if ($http_sec_fetch_site = cross-site) { return 403; }
    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $http_host;
        proxy_hide_header Access-Control-Allow-Origin;
        proxy_buffering off;
        proxy_read_timeout 300s;
    }
}
NGINX
ln -s /etc/nginx/sites-available/pidashcam /etc/nginx/sites-enabled/pidashcam
nginx -t
systemctl enable pidashcam-firstboot.service seatd.service NetworkManager.service pidashcam.service nginx.service
# Root SSH and passwordless root login are not enabled by this build.
passwd -l root
systemctl disable ssh.service ssh.socket
"$VENV/bin/python" -m compileall -q "$APP"
HOME="$DATA" QT_QPA_PLATFORM=offscreen "$VENV/bin/python" - <<'PY'
import cv2,psutil,serial,onnxruntime,hyperlpr3,numpy as np,json,subprocess,tempfile
from pathlib import Path
from PyQt5.QtWidgets import QApplication
cv2.setNumThreads(1)
app=QApplication([])
model=hyperlpr3.LicensePlateCatcher()
model(np.zeros((480,640,3),dtype=np.uint8))
with tempfile.TemporaryDirectory() as directory:
 p=Path(directory)/'smoke.mkv'
 subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-f','lavfi','-i','testsrc2=size=320x180:rate=10',
  '-t','1','-c:v','libx264','-pix_fmt','yuv420p','-y',str(p)],check=True)
 metadata=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_entries','stream=codec_name','-of','json',str(p)]))
 assert metadata['streams'][0]['codec_name']=='h264'
Path('/var/lib/pidashcam/build-smoke.json').write_text(json.dumps(dict(model_inference=True,h264_encoding=True,
    opencv=cv2.__version__,onnxruntime=onnxruntime.__version__,numpy=np.__version__),indent=2))
PY
apt-get clean
