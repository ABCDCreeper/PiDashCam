# PiDashCam

树莓派触屏行车记录仪，支持 U 盘循环录像、回放、车牌识别、GPS 和局域网管理。

## 一键安装与部署

使用 **64 位 Raspberry Pi OS Lite（Bookworm / Trixie）**，连接网络、摄像头和触摸屏。
先使用 Imager 配置自己的登录账户、Wi-Fi 和 SSH；没有默认 SSH 密码。

在终端执行：

```bash
sudo apt-get update && sudo apt-get install -y curl ca-certificates
setup_file="$(mktemp)" && curl -fL https://raw.githubusercontent.com/ABCDCreeper/PiDashCam/main/deploy.sh -o "$setup_file" && sudo bash "$setup_file"
```

脚本安装系统依赖、独立 Python 环境、中文字体、屏幕服务和开机服务，部署完成后自动启动。
安装期间需要网络，首次加载识别模型可能较慢。屏幕由 cage 接管 tty1，因此默认要求 Lite 系统。
运行服务名为 `pidashcam`；程序需要管理挂载、网络和 RTC，作为专用设备的 root 服务运行。
不会创建默认登录密码、格式化 U 盘、删除录像、关闭现有网络连接或自动重启。

## 更新与选项

再次执行相同命令即可更新。已有版本在新代码完成依赖、导入、模型检查后才切换。
切换后连续检查新版本 HTTP 接口，失败自动恢复上一版代码和服务文件。
系统依赖安装及可选硬件配置不在代码回滚范围内。旧版本保留在 releases 中。

```bash
# 从现有安装运行更新
sudo bash /opt/pidashcam/current/app/deploy.sh
# 仅检查平台，不安装
bash deploy.sh --check
# 安装但不立即接管屏幕，下次启动服务或重启时生效
sudo bash deploy.sh --no-start
# 同时配置 GPS UART、I2C、DS3231 RTC；完成后自行重启
sudo bash deploy.sh --enable-hardware
# 指定分支或标签
sudo bash deploy.sh --ref main
```

`--enable-hardware` 仅适用于 Raspberry Pi OS，使用标准 `/dev/serial0` 和 DS3231。
不带此选项时不修改启动分区或串口控制台。32 位系统会在安装前退出。
桌面系统应先使用 `--no-start` 安装，再手动处理桌面与 tty1 的占用。

## 数据与日常操作

| 位置 / 命令 | 用途 |
| --- | --- |
| `/opt/pidashcam/current` | 当前代码及 Python 环境 |
| `/opt/pidashcam/releases` | 已部署版本 |
| `/var/lib/pidashcam/Videos` | 配置、已保存网络等运行数据 |
| U 盘上的 `REC_*.mkv` | 录像；只在确认挂载可写后录制 |
| `sudo systemctl status pidashcam` | 服务状态 |
| `sudo journalctl -u pidashcam -f` | 实时日志 |
| `sudo systemctl restart pidashcam` | 重启应用 |
| `sudo systemctl disable --now pidashcam` | 停止并取消开机启动 |

管理页面：`http://<设备IP>`。可在触屏设置中开启 `Pi-Dashcam` 热点，密码 `12345678`。
安装不会自动切断 Wi-Fi 来开启热点。请只在可信局域网使用管理页面。

更详细的手动安装及硬件说明见 [DEPLOY.md](DEPLOY.md)。脚本安装方式使用本页的服务名和路径，
与旧手动示例中的 `dashcam.service`、用户主目录路径不同，二者不要同时运行。

## 验证

```bash
bash -n deploy.sh
python3 scripts/check_deploy.py
python3 check_debug.py
python3 check_storage.py
```

部署脚本流程检查使用临时目录和模拟系统命令，不修改主机。实际 ARM 硬件、显示与首次 apt/pip 安装需要目标设备验证。

## 自动构建可烧录镜像（GitHub Actions）

目标固定为树莓派 4B / 8GB / HDMI 800×480。工作流使用原生 ARM64 runner，构建完整系统，
不是首次开机再下载依赖的占位镜像。基础系统和四个识别模型均做 SHA-256 检查；
镜像封装前验证模型推理、H.264 编码和文件系统。

1. 打开仓库 **Actions → Build Raspberry Pi image → Run workflow**。
2. 等待运行成功，在该次运行的 **Artifacts** 下载 `PiDashCam-RPi4B-arm64-800x480`。
3. 解压下载的 ZIP，校验 `SHA256SUMS`，使用 Raspberry Pi Imager 烧录其中的 `.img.xz`。
4. 详情见 [烧录与首次启动说明](scripts/image/FLASHING.md)。构建日志单独保存，产物保留 14 天。

首次添加、后续修改镜像脚本或工作流时，也会在 main 上自动触发一次构建；普通应用修改后可手动构建。
工作流仅需要仓库读取权限，不需要存放个人账号、Wi-Fi 或 SSH 密钥等 Secrets。
通用镜像不包含固定密码：后台密码与 SSH 主机密钥在设备上首次生成。
镜像的局域网管理入口有登录认证，应用 API 仅监听本机；普通脚本安装方式尚未同步此入口设计。
构建通过仍不等于完成真机启动、触摸、摄像头和 GPS 验收。

## 虚拟启动验证

Actions → **Verify image in QEMU** 下载指定构建任务已经生成的镜像，校验 SHA-256 后，
仅修改一次性测试副本。原镜像产物不会改动。
先尝试 QEMU 的树莓派 4B 板级模型；若模拟器无法完成启动，则使用通用 ARM 虚拟机内核
检查同一根文件系统，并在报告中注明，不能将这种回退视为 Pi 固件启动通过。

验证范围包括：系统启动、首次生成后台密码和 SSH 主机密钥、网页认证、跨站请求拒绝、
离线模型加载、合成摄像头预览、实际 H.264 录像、回放和暂停拖动。
交互式首次账号设置在测试副本中跳过；摄像头用合成输入，GPS 未测试，界面使用 Qt 离屏渲染。
HDMI、触屏、USB 硬件和真实 Kiosk 仍需实机验收。
在 `image-qemu-verification` 产物下载串口日志和 JSON 结果；失败也会上传日志。

最近一次实际虚拟验证 **未完全通过**：通用 ARM 系统与多数应用探测通过，录像时长检查失败，Pi 4B 板级模拟未完成启动。详见 [虚拟验证报告](docs/IMAGE-VALIDATION.md)。
