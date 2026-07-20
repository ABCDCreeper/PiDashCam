# DEPLOY.md — 车缘 DashCam 部署手册

从空白 SD 卡到完整可运行系统的分步指南。

---

## 1. 硬件清单

| 组件 | 型号 / 规格 | 备注 |
|---|---|---|
| 主板 | Raspberry Pi 4B / 5 (2GB+) | 或兼容 ARM SBC |
| 摄像头 | USB 摄像头 (支持 MJPG) | 测试通过：罗技 C270 / C920 |
| GPS | 微雪 L76X | 可选，GPIO 串口 `/dev/serial0` |
| RTC | DS3231 | 必须，I2C 接口，断电保存时间 |
| 屏幕 | HDMI 触控屏 (≥800×480) | 5-7 寸车载屏 |
| 存储 | USB 闪存盘 (≥16GB) | FAT32 / exFAT |
| WiFi | 板载无线网卡 | 支持 AP 模式 |

---

## 2. 系统安装

### 2.1 烧录 Raspberry Pi OS

用 Raspberry Pi Imager 烧录 **Raspberry Pi OS Lite (64-bit, Bookworm)**：

- **OS**: Raspberry Pi OS Lite (64-bit)
- **Storage**: ≥16GB microSD
- **Imager 预设**（可选）：
  - Hostname: `dashcam`
  - Username: `cam` 密码: `dashcam2024`
  - WiFi: 先不配（后面用热点模式）
  - SSH: 开启
  - Locale: Asia/Shanghai

烧录后插入树莓派，上电启动。

### 2.2 首次 SSH 连接

```bash
# 网线直连或先临时配 WiFi 后：
ssh cam@dashcam.local
# 或直接 IP：
ssh cam@192.168.x.x
```

---

## 3. 系统配置

### 3.1 基础更新 + 必要工具

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y git python3-pip python3-pyqt5 python3-opencv \
                    python3-psutil network-manager v4l-utils i2c-tools \
                    cage seatd
```

| 包 | 作用 |
|---|---|
| `cage` | Wayland kiosk compositor，让 PyQt5 直接在 Wayland 上全屏，无需桌面环境 |
| `seatd` | 最小化 seat 管理守护进程，为 cage 提供 DRM/输入设备访问 |

### 3.2 启用 I2C / 串口 / 摄像头

```bash
sudo raspi-config nonint do_i2c 0
sudo raspi-config nonint do_serial_hw 0
sudo raspi-config nonint do_serial_console 1   # 禁用串口控制台
sudo raspi-config nonint do_camera 0
sudo raspi-config nonint do_legacy_camera 0
```

重启使生效：

```bash
sudo reboot
```

### 3.3 配置 DS3231 RTC

```bash
# 加载 RTC 模块
echo "dtoverlay=i2c-rtc,ds3231" | sudo tee -a /boot/firmware/config.txt

# 同步网络时间到 RTC（首次需要联网）
sudo systemctl stop fake-hwclock
sudo fake-hwclock save
sudo hwclock -w
```

### 3.4 卸载自动挂载服务（避免冲突）

DashCam 通过 `SystemUtils` 自己管理 U 盘挂载，需要禁用系统自动挂载：

```bash
sudo apt purge -y usbmount 2>/dev/null
# 禁用 udisks2 自动挂载
sudo systemctl mask udisks2
```

### 3.5 配置 WiFi 热点

DashCam 启动时如果没连 WiFi，会处于离线状态。可以先配热点：

```bash
sudo nmcli connection add type wifi ifname wlan0 con-name Pi-Dashcam \
    autoconnect yes ssid Pi-Dashcam
sudo nmcli connection modify Pi-Dashcam 802-11-wireless.mode ap \
    802-11-wireless.band bg ipv4.method shared
sudo nmcli connection modify Pi-Dashcam wifi-sec.key-mgmt wpa-psk \
    wifi-sec.psk "dashcam123"
```

### 3.6 启用 seatd 并配置权限（Wayland 必需）

seatd 是一个最小化 seat 管理守护进程，负责在无桌面环境下管理 DRM 设备和输入设备的访问权。

```bash
sudo systemctl enable seatd
sudo systemctl start seatd
# cam 用户加入必要组：video（GPU）、render（硬件加速）、input（触摸屏）、plugdev（USB）
sudo usermod -aG video,render,input,plugdev cam
```

**权限原理**（重要）：

seatd 通过 **logind session** 动态授权：
1. systemd unit 中 `PAMName=login` 为 `cam` 创建 login session（`loginctl` 可见）
2. seatd 检测到 `cam` 是 seat0 的活跃用户，自动添加 ACL 到 `/dev/dri/card0`、`/dev/input/*`
3. `Environment="LIBSEAT_BACKEND=seatd"` 通知 cage/wlroots 用 seatd 而非 elogind

因此 `cam` **不需要**在 `video` 组——seatd 绕过了 Unix 组权限。但加入 `video` 组提供手动测试时的回退方案。

> 加入组后需要**重新登录**或重启才能生效。

### 3.7 为什么用 cage 而不是 X11

传统做法是在 X11 桌面（或 `startx`）上运行 PyQt5。但车载场景下：

- ❌ X11 拖着一个完整的桌面会话，浪费 CPU/内存
- ❌ 需要配 `~/.xinitrc`、`DISPLAY`、`XAUTHORITY`

cage + seatd 方案：

- ✅ 零桌面环境，系统仅跑一个 compositor + 一个应用
- ✅ PyQt5 直接通过 `QT_QPA_PLATFORM=wayland` 渲染
- ✅ DRM 直接写屏，无 X11 中间层，更低延迟
- ✅ cage 自动处理 VT 切换、全屏、屏幕旋转

---

## 4. 应用部署

### 4.1 克隆代码

```bash
cd ~
git clone https://github.com/your-org/DashCam.git
# 或手动拷贝：
# scp -r . cam@192.168.x.x:~/DashCam/
cd ~/DashCam
```

### 4.2 安装 Python 依赖

```bash
# HyperLPR3 车牌识别
pip3 install hyperlpr3 --break-system-packages

# GPS 串口（可选）
pip3 install pyserial --break-system-packages
```

> `--break-system-packages` 在 Raspberry Pi OS Bookworm 下必须，因为系统强制 PEP 668。

### 4.3 端口 80 权限（关键）

Web 管理页面默认监听 **端口 80**（标准 HTTP，无需输端口号）。1024 以下端口需要 `CAP_NET_BIND_SERVICE` 能力。

由于应用通过 systemd 以 **非 root 用户** (`cam`) 运行，权限通过 `AmbientCapabilities` 注入——**无需 sudo，无需 setcap**。

在下面的 systemd unit 中已包含：

```
AmbientCapabilities=CAP_DAC_OVERRIDE CAP_NET_BIND_SERVICE
```

| 能力 | 用途 |
|---|---|
| `CAP_DAC_OVERRIDE` | 允许 `sudo mount` / `chmod` 操作 U 盘 |
| `CAP_NET_BIND_SERVICE` | 允许监听 80 端口 |

> 如果手动测试（不通过 systemd），需要临时 sudo 运行或改回 `port=8080`。

### 4.4 语法检查

```bash
cd ~/DashCam
python3 -m py_compile main.py web_server.py core_camera.py \
    ui_mainwindow.py ui_wifi.py ui_keyboard.py touch_widgets.py
echo "语法检查通过"
```

### 4.5 手动启动测试

**通过 cage 启动**（需要已登录的 TTY）：

```bash
cd ~/DashCam
# 在 tty1 上直接跑（需在 seat0 上已登录）
sudo chmod 666 /run/seatd.sock
cage -s -m last -- python3 main.py
```

**通过 SSH 测试（仅验证导入 + API，无 GUI）**：

```bash
cd ~/DashCam
# 端口 80 需要 root 或 CAP_NET_BIND_SERVICE
sudo python3 -c "
from web_server import DashcamWebServer
s = DashcamWebServer(port=80)
s.start()
import time; time.sleep(60)  # 运行 60 秒供测试
"
# 另一个终端：curl http://127.0.0.1/api/status
```

启动后，手机连接 `Pi-Dashcam` 热点（密码 `dashcam123`），浏览器访问 `http://192.168.x.x` 即可看到管理仪表盘。

---

## 5. 开机自启

### 5.1 systemd 服务（cage + Wayland）

创建 `/etc/systemd/system/dashcam.service`：

```ini
[Unit]
Description=车缘 DashCam 行车记录仪
After=network.target systemd-logind.service seatd.service
Wants=seatd.service
Conflicts=getty@tty1.service

[Service]
User=cam
Group=cam

# 非 root 用户获得 USB 挂载 + 80 端口权限
AmbientCapabilities=CAP_DAC_OVERRIDE CAP_NET_BIND_SERVICE

# 通过 PAM 创建 login session（seatd 依赖此 session 授权 DRM/输入设备）
PAMName=login

# Wayland + seatd 环境变量
Environment="XDG_RUNTIME_DIR=/run/user/1002"
Environment="QT_QPA_PLATFORM=wayland"
Environment="WLR_BACKEND=drm"
Environment="LIBSEAT_BACKEND=seatd"

# TTY 设置（接管 tty1）
TTYPath=/dev/tty1
TTYReset=yes
TTYVHangup=yes
TTYVTDisallocate=yes
StandardInput=tty-force
StandardOutput=journal
StandardError=journal

WorkingDirectory=/home/cam/DashCam

# 启动前修复 seatd socket 权限（root 执行，+ 前缀表示不作为服务的一部分校验）
ExecStartPre=+/bin/chmod 666 /run/seatd.sock
# cage 启动 PyQt5 应用：-s 允许 VT 切换，-m last 使用最后一个显示器
ExecStart=/usr/bin/cage -s -m last -- /usr/bin/python3 /home/cam/DashCam/main.py

Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

> **注意**：
> - `XDG_RUNTIME_DIR` 中的 `1002` 是 `cam` 用户的 UID。用 `id -u cam` 查看实际值，按需修改。
> - `PAMName=login` 是 seatd 授权的核心——它让 systemd 为 `cam` 创建一个完整的 login session（`loginctl` 可见），seatd 据此开放 DRM/输入设备。

启用：

```bash
sudo systemctl daemon-reload
sudo systemctl enable dashcam
sudo systemctl start dashcam
```

### 5.2 检查服务状态

```bash
sudo systemctl status dashcam
sudo journalctl -u dashcam -f  # 实时日志
```

---

## 6. 验证清单

部署完成后逐项验证：

- [ ] 摄像头画面正常显示（无花屏/黑屏）
- [ ] U 盘插入后自动挂载，屏幕显示"已用/x%"
- [ ] 按"开始录像"后 MKV 文件生成在 U 盘
- [ ] 视频回放可播放，进度条可拖动
- [ ] GPS 定位数据正常显示（需要空旷地带）
- [ ] 车牌识别可在画面中标注车牌
- [ ] 手机连 `Pi-Dashcam` 热点后浏览器访问 `http://<IP>` 可打开管理页面
- [ ] 管理页面可查看系统状态、视频列表、车牌日志
- [ ] 断电重启后时间不丢失（DS3231 正常）
- [ ] WiFi 连接功能正常（扫描 → 输入密码 → 连接成功）

---

## 7. 故障排查

| 问题 | 检查点 |
|---|---|
| cage 黑屏/无法启动 | `journalctl -u dashcam -n 50`；确认 `/run/seatd.sock` 权限；核对 `id -u cam` 与 `XDG_RUNTIME_DIR` |
| seatd socket 权限错误 | `sudo chmod 666 /run/seatd.sock`；确认 systemd unit 有 `ExecStartPre` |
| DRM 权限拒绝 | `getfacl /dev/dri/card0` 确认 seatd 已加 ACL；`loginctl` 确认 `cam` 在 seat0 上有活跃 session |
| 摄像头无画面 | `v4l2-ctl --list-devices`；确认 `/dev/video0` 存在 |
| U 盘不挂载 | `sudo lsblk -f`；确认文件系统为 FAT32/exFAT；`cam` 在 `plugdev` 组 |
| GPS 无数据 | `sudo cat /dev/serial0` 看是否有 NMEA 输出；确认串口配置 |
| 端口 80 启动失败 | 确认 systemd unit 有 `AmbientCapabilities=... CAP_NET_BIND_SERVICE`；或临时 `sudo` 运行 |
| 车牌识别不工作 | `python3 -c "import hyperlpr3; print(hyperlpr3.LicensePlateCatcher())"` |
| 车机屏幕不亮 | `/boot/firmware/config.txt` 加 `hdmi_force_hotplug=1`；或 `sudo raspi-config` 设分辨率 |
| 管理页打不开 | `curl http://127.0.0.1/api/status` 看是否有 JSON 返回；检查防火墙 `sudo iptables -L` |

---

## 8. 目录结构

```
~/DashCam/
├── main.py              # 入口
├── core_camera.py        # 核心：摄像头 + 录像 + GPS + LPR + 磁盘管理
├── ui_mainwindow.py      # PyQt5 UI（3 页：主界面 / 设置 / 回放）
├── ui_wifi.py            # WiFi 扫描 + nmcli 连接
├── ui_keyboard.py        # 全屏触控键盘
├── touch_widgets.py      # TapSlider + TouchScrollList
├── web_server.py         # 管理网页 HTTP 服务器 + REST API
├── static/
│   └── index.html        # 管理仪表盘前端
├── AGENTS.md             # 项目 AI 开发手册
├── DEPLOY.md             # 本文件
└── .gitignore

~/Videos/                 # 运行时数据
├── storage_config.json   # 上次使用的 U 盘路径
├── video_params.json     # 用户设置的录制参数
├── wifi_passwords.json   # 记忆的 WiFi 密码
└── plates_log.json       # 车牌识别日志（在 U 盘上）
```
