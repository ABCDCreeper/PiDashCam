# 镜像虚拟启动验证结果（2026-10-28）

结论：**未完全通过；仍应作为测试镜像。** 原始可下载产物未被修改。

## 验证对象与方法

- 镜像构建任务：40703731345，产物 PiDashCam-RPi4B-arm64-800x480。
- 实际验证任务：https://github.com/ABCDCreeper/PiDashCam/actions/runs/40710514212
- 工作流下载原产物并核对 SHA256SUMS，只在一次性副本中添加探测服务。
- QEMU 树莓派 4B 模型使用镜像内核、initramfs 和测试用设备树适配；未执行真实树莓派固件引导。
- 板级模型未完成启动后，使用通用 ARM 虚拟机内核及相应模块检查同一镜像的根文件系统。
- 交互式首次用户设置在测试副本中屏蔽；摄像头为 320×180 合成输入，存储为来宾内 tmpfs 挂载。
- Qt 使用 offscreen 渲染，不能据此认定 HDMI、触屏和 Kiosk 显示已验证。

## 实际结果

通用 ARM 虚拟机进入 Debian GNU/Linux 13（trixie）。15 项应用探测中，14 项通过、1 项失败。

通过：后台首次生成密码、SSH 主机密钥生成、nginx 运行、未认证请求返回 401、跨站请求返回 403、
模型加载、预览帧、可写挂载检查、认证 API、JPEG 快照、真实 H.264 文件编码、回放、暂停拖动、Qt 离屏界面渲染。

失败：录像时长。操作窗口为 7.189 秒，生成文件为 4.000 秒，差值 3.189 秒，超过检查容差。
这可能包含虚拟环境下 FFmpeg 冷启动或处理延迟；尚未定因，不能直接推断真机必然有相同偏差，
也不能把这项失败忽略后宣称镜像全部通过。

## 树莓派板级启动

QEMU 4B 模型报告 GPIO、SD 供电和 UART 错误，随后出现：

    Warning: unable to open an initial console.
    Kernel panic - not syncing: Attempted to kill init! exitcode=0x00000100

使用的树莓派内核为 6.18.50+rpt-rpi-v8。板级模拟失败与模拟器未实现的外设存在关联，
但本轮没有证明真机一定成功或失败。通用 ARM 根文件系统启动成功，不能替代 Pi 4B 启动链验收。

## Kiosk 与硬件边界

探测时 pidashcam.service 显示 active/running，并记录了 root PAM 会话打开。
这只是 systemd 的进程状态；没有获得 HDMI 输出或触摸操作的证据，因此 Kiosk 不判为显示验收通过。
物理摄像头、USB 插拔、GPS、RTC、Wi-Fi 切换和硬件编码器仍未验证。

## 后续应做

1. 分别测量编码器首帧延迟与连续录制时长，定位本轮时长偏差。
2. 在备用系统卡上实机启动，确认首次账号设置、HDMI 800×480、触摸与 seatd/cage。
3. 使用真实摄像头和 U 盘，完成录制、停止、回放、断开存储等验收。

完整串口日志和原始 JSON 保存在验证任务的 image-qemu-verification 产物中（保留 14 天）。
