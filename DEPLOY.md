# PiDashCam 部署与真机验收

以当前 `deploy.sh` 为准，支持 Raspberry Pi OS Lite / Debian 12+ arm64。
本项目的服务以 root 运行，用于专用设备的挂载、网络、串口和 RTC 控制；这不是最小权限隔离。
不需要额外创建 `cam` 用户、设置固定密码或将 seatd socket 改成 666。

## 已安装 Raspberry Pi OS Lite

先用 Imager 设置自己的账号和 SSH 公钥，再连接设备。安装器保留已有 SSH 配置、主机密钥与网络连接，
不会设置 root 密码、自动开启热点、格式化磁盘或自动重启。镜像的首次用户设置只适用于预制镜像，见
[scripts/image/FLASHING.md](scripts/image/FLASHING.md)，不要在已配置好的系统上执行镜像 provision.sh。

```bash
bash deploy.sh --check
sudo bash deploy.sh
# 不立即接管 tty1（服务下次开机运行）
sudo bash deploy.sh --no-start
```

安装使用发行版 Qt/OpenCV/NumPy 和独立 venv，模型下载后验证固定 SHA-256，
在实际服务 HOME 中离线加载、推理成功后才切换版本。更新失败恢复上一版应用和 unit；
APT、模型缓存及 nginx 配置不在应用回滚范围内。安装前检查已有 nginx 站点，备份任何自定义配置。
默认发行版站点可自动替换，自定义 default 站点会让安装中止；其他同端口站点应先由管理员处理。

`--source DIR` 从干净且已经提交的本地 Git checkout 部署，不访问 GitHub；以该 checkout 的 HEAD 为准，
此时 `--ref` 不选择版本。`--model-dir DIR` 指定四个 ONNX 文件所在目录，仍强制校验。
`--wheelhouse DIR` 从本地 wheel 目录安装 Python 依赖，不访问 PyPI。

## 中国网络环境

64 位 Raspberry Pi OS 使用 Debian arm64 仓库，**不要换成 32 位 Raspbian 仓库**。
先备份 `/etc/apt/sources.list` 和 `/etc/apt/sources.list.d/`，保留 Suites、Components、Signed-By。
在 `.sources` / `.list` 中仅替换对应 URI：

| 原 URI | 国内镜像示例 |
| --- | --- |
| `http://deb.debian.org/debian/` | `https://mirrors.tuna.tsinghua.edu.cn/debian/` |
| `http://archive.raspberrypi.com/debian/` | `https://mirrors.tuna.tsinghua.edu.cn/raspberrypi/` |

安全更新优先官方源；无法连接时可使用 `/debian-security/` 镜像，但需考虑镜像同步延迟。
参考 [Debian 镜像说明](https://mirrors.tuna.tsinghua.edu.cn/help/debian/) 和
[Raspberry Pi 镜像说明](https://mirrors.tuna.tsinghua.edu.cn/help/raspberrypi/)。不要关闭签名或 TLS 校验。

Python 安装可使用临时环境变量（不改系统 pip 配置）：

```bash
sudo env PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple bash deploy.sh
```

GitHub 不可达时，在能访问仓库的电脑克隆最新 main、完成修改和提交后，通过 SFTP 上传整个 checkout。
模型也可先在电脑下载再上传：

```bash
python3 scripts/prepare_models.py --data /path/to/model-cache
# 上传 model-cache/.hyperlpr3/20230229/onnx/ 中的四个文件
sudo env PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple \
  bash /path/to/PiDashCam/deploy.sh --source /path/to/PiDashCam --model-dir /path/to/onnx
```

## 硬件与权限

HDMI 推荐 800×480；先查看 `/sys/class/drm/card*-HDMI-A-*/status`、`modes` 和实际 DRM 状态，
不要仅凭 service active 判定屏幕显示成功。Cage 通过 seatd 直接管理 DRM 和输入设备，
这不同于 logind/udev 的会话 ACL 机制；不要使用 `chmod 666 /run/seatd.sock`。
当前应用 root 服务直接使用 seatd，不创建 PAM 登录会话，避免子进程移出服务 cgroup、日志归入会话而非应用服务。
不需要额外用户组；其他用户部署需要单独核实 seatd socket、video、render、input 权限。

USB 摄像头使用 OpenCV 的索引 0，优先检查 `v4l2-ctl --list-devices` 和 `/dev/video0` 的实际采集能力。
编码器 `/dev/video11` 等节点存在不等于有摄像头。CSI Unicam 节点自动使用发行版 Picamera2/libcamera 获取处理后的 BGR 帧，需安装 python3-picamera2。
可用 DASHCAM_CAMERA_BACKEND=opencv 或 picamera2 显式选择后端；CSI 不直接读取原始 Bayer 节点。

GPS 可选，读取 `/dev/serial0` 9600 baud 的 NMEA。车牌日志时间来自系统时钟；
仅在有定位且 NMEA 更新未超过 5 秒时保存 GPS 坐标与 gps_nmea 来源，否则坐标与来源为空，
不将 0,0 或过期定位当作真实地点。实际定位仍需在可接收卫星的环境验收。
DS3231 仅在实际接入时配置。`--enable-hardware` 会启用 UART/I2C、关闭串口控制台并添加 DS3231 overlay，
需要手动重启，不应在未确认硬件时使用。
时间标注来自系统时钟；网络时间同步不代表断电后的 RTC 保时已验证。
Trixie 的 hwclock 由 util-linux-extra 提供；确认 NTP 已同步后，使用 hwclock --systohc --utc 初始化 DS3231，再读回校验。设备时区需按使用地点核实。

录像仅保存到可写的独立 USB 挂载，不回退到系统盘。系统启动 U 盘应从录像设备扫描中排除。
清理仅涉及 `REC_*.mkv/mp4`，在占用达到 88% 或剩余不足 1.5 GiB 时删除旧分段；无法满足空间预留时停止录像。
不自动格式化未知盘；任何清空、格式化或烧录均须确认路径、型号、容量和挂载点。

## 认证和访问

后端仅监听 `127.0.0.1:8080`，nginx 在 LAN 的 80 端口提供 Basic 认证和同源检查。
首次安装在设备生成随机 admin 密码，重复安装保留它。使用自己的 SSH 登录后读取：

```bash
sudo cat /var/lib/pidashcam/admin-password
```

浏览器访问 `http://<设备IP>`。仅用于可信局域网，公网访问需要另行配置 TLS 与访问控制。
不要把密码、私钥、网络配置或运行数据提交到 Git 或排障报告。

## 日常操作

```bash
sudo systemctl status pidashcam seatd nginx
sudo systemctl start pidashcam
sudo systemctl stop pidashcam
sudo systemctl restart pidashcam
sudo systemctl enable pidashcam seatd nginx
sudo systemctl disable --now pidashcam
sudo journalctl -u pidashcam -b --no-pager
sudo journalctl -u pidashcam -f
```

应用和 venv：`/opt/pidashcam/current`；旧版本：`/opt/pidashcam/releases`；
运行数据：`/var/lib/pidashcam/Videos`。应用退出由 systemd 自动重启，主动 systemctl stop 不会触发重启。

## 部署检查与验收边界

修改代码后先执行语法检查、`python3 scripts/check_deploy.py`、`python3 scripts/image/check_config.py`，
并在具备 Qt/OpenCV/FFmpeg 的环境执行 `python3 check_debug.py` 和 `python3 check_storage.py`。
模拟测试不会代替真实硬件验收。已有镜像部分通过的虚拟结论见 [docs/IMAGE-VALIDATION.md](docs/IMAGE-VALIDATION.md)。

真机需要记录以下证据：

- 服务 enabled、启动日志、退出后的重启、重启整机后的状态；seatd、DRM、会话和输入设备实际权限。
- HDMI 实际输出 800×480，人工确认画面；触摸若存在，检查坐标与边缘点击。
- 真实摄像头预览、JPEG 截图、模型推理、真实车牌及时间/地点来源。
- 未认证访问 401、认证访问成功、跨站请求 403、后端 LAN 端口不可达。
- 存储挂载、目录权限、可用容量与保留策略；真实插拔需要防止损坏仍在写入的数据。
- WiFi 扫描、连接及断网恢复；远程操作须保留有线链路，并使用可回滚的网络配置，避免切断唯一 SSH 路径。

## 录像时长定位

`journalctl -u pidashcam` 中 `RECORD_TIMING` 记录 monotonic 时间：
启动/停止请求、子进程创建、首帧写入前后、首次编码输出、编码器关闭和退出。
首次编码输出来自 FFmpeg progress，代表已有编码帧，并非采集设备已就绪的精确时刻。
首帧 pipe 写入成功只证明交给编码器，不证明已经落盘。对比这些阶段定位启动延迟和处理吞吐。
应用将到帧时刻的 monotonic 时间戳通过 PyAV/NUT 送入 FFmpeg，再由 CFR 补帧，
避免 FFmpeg 冷启动阻塞首帧管道时丢掉时间。系统校时仍会触发换段；时间戳不按采集帧数固定递增。

使用真实摄像头，分别进行首次和后续多次录像（建议短段及 30/60 秒），同时记录实际操作窗口，
用画面中的时钟/运动核实播放速度，检查停止到收尾的延迟。对每个文件执行：

```bash
ffprobe -v error -count_frames -show_entries \
  stream=codec_name,r_frame_rate,avg_frame_rate,time_base,start_time,duration,nb_read_frames:format=start_time,duration \
  -of json /path/to/REC_file.mkv
ffprobe -v error -select_streams v:0 -show_entries frame=best_effort_timestamp_time,pkt_duration_time \
  -of csv /path/to/REC_file.mkv
```

不能通过放宽容差掩盖时长差异；没有真实摄像头时必须保留此项未验收状态。

## 系统盘 I/O 故障和中断安装

若 APT/dpkg 报 `No such device`、`Input/output error` 或根分区变成只读，先停止安装。
不要强行 remount 为读写，也不要对仍挂载的根分区运行 fsck。
通过本地控制台查看内核日志，检查电源、欠压、USB 连接及系统盘；必要时在维护环境离线检查文件系统。
SSH 无法连接时需要本地恢复或重新上电。恢复后先确认系统盘稳定可写且没有新的 I/O 错误，
再执行 `sudo dpkg --audit`、`sudo dpkg --configure -a` 和 `sudo apt-get -f install` 修复中断的安装，
之后重复部署。恢复期间不要误拔系统启动 U 盘，也不要把它用作录像盘。
