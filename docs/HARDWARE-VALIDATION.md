# Raspberry Pi 4B 部署与硬件验证（2026-10-30）

本轮在现有 Raspberry Pi OS Lite arm64 Trixie 上实际部署；没有烧录、格式化或构建镜像。
基线为 main `b1b13632ebd6ba58d22a557cff46f473b7003865`。
部署代码为 `d19240929b1c8c0428ea0a27df4d9a60ad25bf05`，后续文档提交不改变该运行版本。
这些修复没有包含在之前 Actions 产出的旧镜像内，旧镜像的虚拟验证结论仍为部分通过。

## 实际环境与恢复

- 开发电脑是 x86_64 Zorin OS 18.1；系统安装和服务配置只在目标树莓派执行。
- 目标是 Pi 4 Model B Rev 1.5、8GB，Raspberry Pi 内核 6.18.50，Debian 13.7。
- 系统位于约 64GB SanDisk USB 盘，录像盘是约 32GB Silicon Motion USB Flash Drive（FAT32）。
  设备名随枚举变化，根盘检测按当前根分区父设备排除，不能固定将 sda 当作录像盘。
- 首轮 APT 解包期间出现 fsync No such device 和根分区只读，随后 SSH 失联。
  用户本地重启并启动 SSH 后恢复；根分区 fsync 检查、dpkg 配置修复和后续安装通过。
  本轮恢复后未再观察到相同 I/O 错误、欠压标志；此前故障根因没有证实。
- APT 源已备份；使用国内 HTTPS 镜像及签名验证。源码和已校验模型从本地上传，
  arm64 Python wheel 从国内镜像预下载后离线安装，避免系统 piwheels 额外源拖慢安装。

## 已验证的功能

| 项目 | 真实证据和边界 |
| --- | --- |
| 开机启动 | pidashcam、seatd、nginx、SSH 均 enabled；整机重启后恢复 active，模型、预览与自动录像恢复 |
| 退出重启 | 终止 Cage 后 MainPID 改变、NRestarts 从 0 到 1，API 和自动录像恢复；主动 stop 保持 inactive |
| 日志/进程 | 显式 journal 输出；去掉 root 服务不需要的 PAM 登录会话，Python/FFmpeg 留在应用 cgroup，journalctl -u 能看到模型及录像计时 |
| HDMI | JRP JRP4301，HDMI-A-2；Wayland 和 DRM 均确认 800×480、约 59.977Hz，获得真实合成器截图 |
| 触摸与布局 | USB2IIC_CTP_CONTROL；用户确认触摸准确。修复设置页 GPS 错误撑大窗口后，用户确认返回布局正常 |
| 摄像头 | OV5647 CSI，Picamera2/libcamera 输出 1920×1080 BGR；独立采集 30 帧约 1.367 秒，应用真实画面、JPEG 快照通过 |
| 编码 | 实际进程使用 h264_v4l2m2m，输出 H.264 / MKV / 30fps，非仅检测到设备节点 |
| Web | 未认证 401；认证页面/API 200；跨站 Origin 403；认证下载 Range 206，1024 字节符合请求 |
| 监听 | nginx 对 LAN 的 80；后端仅 127.0.0.1:8080，开发电脑访问目标 8080 连接失败 |
| 口令与 SSH | admin 口令随机生成且更新时保留；密码文件 0600，数据目录 0700，htpasswd root:www-data 0640。保留现有 SSH 公钥和主机密钥；root 密码状态 locked |
| 设备权限 | 应用是专用 root 服务；seatd socket root:video 0770，未 chmod 666。DRM/video/render/input 节点按原系统权限使用 |
| 存储 | 独立 FAT32 盘自动挂载、写入及关闭录像成功，约 30GiB 可用；系统 U 盘未误选为录像盘，没有清空或格式化 |
| 无线 | 设置 CN，解除软屏蔽，扫描到多个网络；在未连接状态关闭/开启无线后恢复扫描，有线 SSH 保持连接 |
| GPS 通信 | UART 开启且串口控制台已关闭；5 秒收到 50 条 NMEA、50 条校验有效，包含 GNRMC/GNGGA，实际尚无定位、卫星数 0 |
| RTC | DS3231 在 I2C 0x68 注册 rtc0；初始 SET TIME，通过已同步的网络时间初始化后读回成功；再次重启时内核成功从 RTC 设置系统时钟，Trixie 所需 util-linux-extra 已补装并纳入脚本 |
| 时间/地点来源 | 系统时钟、Asia/Shanghai、NTP；录像时间基准为 monotonic。车牌日志新增系统时间来源和 GPS 坐标，仅保存 5 秒内有效定位，否则为空 |

## 录像时长根因与修复

原实现以 FFmpeg 从 rawvideo 管道读到数据的时刻打时间戳。
树莓派上实际复现：操作窗口约 3.002 秒，文件仅 2.333 秒；首帧管道写入阻塞约 0.625 秒。
启动等待没有进入视频时间线，这与此前虚拟验证的时长偏短方向一致；并不等于已证明所有虚拟差值都来自同一原因。

修复以应用到帧的 monotonic 时间生成 PTS，通过 PyAV/NUT 将时间戳传给 FFmpeg，
再由 CFR 补帧。没有恢复每个采集帧固定加 1/fps 的错误做法，也没有放宽检查容差。

真实 CSI + 硬件 H.264 的连续启动/停止测试：

| 操作窗口 | 文件时长 | 差值 | 帧数 |
| --- | --- | --- | --- |
| 7.000 秒 | 7.033 秒 | +0.033 秒 | 211 |
| 15.000 秒 | 15.000 秒 | -0.0005 秒 | 450 |
| 30.002 秒 | 30.000 秒 | -0.0016 秒 | 900 |

三段均为 30/1 fps，首帧 PTS 0，后续严格递增，相邻 PTS 约 33/34ms。
最终 d192409 版本再次整机重启后复测：7.000 秒窗口得到 7.033 秒、211 包的 H.264 视频，随后恢复持续录像。
单次收尾约 0.14～0.16 秒。另一次重启应用后的首次自动录像：首帧写入至关闭约 10.527 秒，
文件 10.533 秒，首帧管道阻塞约 0.319 秒仍被时间线保留。

**输出 30fps 不代表每秒 30 个独立采集画面。** 上述首次段输入约 117 帧/10.527 秒，
当前 1080p + AI 处理存在吞吐限制，输出包含补帧；补帧维持速度，不增加真实细节。
当前没有证明在所有光照、分辨率、长期负载和存储设备下均达到目标独立帧率。

实际 1080p 录像文件回放：约 1.991 秒墙钟推进 2.000 秒媒体，比例约 1.005；
暂停状态拖动能输出新帧。该回放探测使用真实文件和同一回放实现，不把离屏回放探测等同于完整 HDMI 手动回放验收。

## 自动检查

共 51 项通过，另有 Python/Bash 语法和 git diff --check：

- check_debug.py：32；check_storage.py：4。
- check_camera.py：3；check_layout.py：1；check_plate_location.py：3。
- scripts/check_deploy.py：3；scripts/image/check_config.py：5。

包含模拟网络、临时文件、真实 FFmpeg 和离屏 UI 等不同层级；不能将全部数量解释成真实硬件功能全部通过。

## 尚未完成的验收

- GPS 室外卫星定位、位置精度和真实坐标进入车牌日志；当前不得声称已取得地点。
- 真实车牌识别准确率、车牌随录像的长期关联；当前仅验证模型加载与推理路径，未在镜头中验收真实车牌。
- 已保存 WiFi 的实际连接及掉线自动重连；本轮未索取无线密码，也未切断有线链路。
- RTC 断电电池保时、长期运行、突然断电恢复、满盘实际压力和物理存储拔插。
- 1080p 每秒 30 个独立采集画面的性能目标；输出 CFR 不替代该指标。
- 首轮系统盘失联的物理根因。

## 访问和日常操作

局域网访问 `http://192.168.1.10`（DHCP 地址可能变化），账号 admin。
密码只在设备上读取：`sudo cat /var/lib/pidashcam/admin-password`，不要上传到 Git 或报告。

```bash
sudo systemctl start pidashcam
sudo systemctl stop pidashcam
sudo systemctl restart pidashcam
sudo systemctl status pidashcam seatd nginx
sudo journalctl -u pidashcam -b --no-pager
sudo journalctl -u pidashcam -f
sudo systemctl disable --now pidashcam
```

代码、虚拟环境：/opt/pidashcam/current；运行数据：/var/lib/pidashcam/Videos。
本轮修复为本地 main 提交并已上传设备，尚未推送 GitHub，不应从旧远程 main 覆盖此已验收版本。
需要重复安装时，以 /opt/pidashcam-source/PiDashCam 的干净 checkout 为来源，并使用已有模型和 wheel 缓存。
