# PiDashCam 树莓派 4B 镜像

目标：树莓派 4B，8GB 内存，64 位系统，HDMI 800×480。建议使用 16GB 或更大存储卡。
镜像包含依赖、车牌模型、应用、触屏 Kiosk 和开机服务；首次启动不需要再下载这些内容。
构建通过不等于已经完成树莓派真机启动和触摸验证，build-manifest.json 会明确标注。

## 下载和烧录

在 Actions → Build Raspberry Pi image → 成功的运行记录 → Artifacts 中下载
PiDashCam-RPi4B-arm64-800x480，解压外层 ZIP，得到 .img.xz 和 SHA256SUMS。
核对 SHA-256 后，在 Raspberry Pi Imager 中选择“自定义镜像”，选择 .img.xz 烧录。
烧录会清空所选存储卡，请正确选择设备。

HDMI 使用靠近 USB-C 电源的 micro-HDMI 口（HDMI0）；它被固定为 800×480。
USB 摄像头、触屏 USB 线和录像 U 盘接好后上电。没有 U 盘时不录像。
GPS UART 和 I2C 已启用，串口控制台已让出；DS3231 overlay 需按实际硬件另行配置。

## 首次启动账号和网络

通用镜像没有预设登录密码、Wi-Fi 密码或 SSH 私钥。
如 Imager 提供系统自定义选项，请配置自己的账号、Wi-Fi、时区及 SSH 公钥。
若自定义镜像没有提供这些选项，首次启动可通过键盘/控制台完成 Raspberry Pi OS 的用户设置；
也可按官方方式在 boot 分区添加 userconf.txt（用户名:密码的加密哈希），需要 SSH 时添加 ssh 空文件。
不要向公开仓库上传这些个人配置。Wi-Fi 也可通过设备触屏连接。
如首次用户设置占用 tty1，请完成账号设置后重启，再运行 Kiosk。

## 管理网页

应用接口只监听 127.0.0.1:8080；局域网网页由 nginx 提供登录认证。
网页用户是 admin，密码在每台设备首次启动时独立随机生成，不是固定默认密码。
使用自己配置的系统账号登录后读取：

    sudo cat /var/lib/pidashcam/admin-password

在可信局域网访问 http://<设备IP>，输入上述独立密码。没有配置公网 TLS，请勿映射到公网。
运行目录受限于 root；专用设备的 Qt/硬件控制仍以 root 运行，认证代理不等于最小权限隔离。

## 排障和更新

    sudo systemctl status pidashcam seatd nginx
    sudo journalctl -u pidashcam -b --no-pager
    sudo journalctl -u pidashcam-firstboot -b --no-pager

Kiosk 无法显示时需检查 HDMI 接口、触屏输入、seatd、TTY 和 DRM 状态。
HTTP 启动成功不等于摄像头、GPS 或屏幕已工作。
本镜像使用受认证网络入口；旧版 deploy.sh 会丢失认证入口设计。当前 main 的安装脚本已使用
本机后端和 nginx 认证；更新前先备份配置，保留镜像的首次启动 unit，核实认证与 SSH 均正常。
录像在 U 盘，配置在 /var/lib/pidashcam/Videos。重新烧录会清空系统卡上的配置，先备份。
