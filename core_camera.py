# core_camera.py
import os
import time
import json
import psutil
import threading
import tempfile
import subprocess as sp
import cv2
import av
from fractions import Fraction
from camera_capture import open_camera, PiCameraCapture
from datetime import datetime
from PyQt5.QtCore import pyqtSignal, QObject
from PyQt5.QtGui import QImage

try:
    import serial
    SERIAL_AVAILABLE = True
except ImportError:
    SERIAL_AVAILABLE = False
    serial = None


class GPSWorker(QObject):
    """L76X GPS 模块后台读取线程 — 解析 NMEA 0183 语句，通过信号输出定位数据。
    
    微雪 L76X 默认波特率 9600，GPIO 串口 /dev/serial0。
    NMEA 语句: $GPRMC (经纬度+速度), $GPGGA (卫星数+定位质量)。
    """
    gps_data_updated = pyqtSignal(dict)

    def __init__(self, port="/dev/serial0", baudrate=9600):
        super().__init__()
        self.port = port
        self.baudrate = baudrate
        self.running = False
        self._serial = None

        # 最新解析数据
        self.latitude = 0.0
        self.longitude = 0.0
        self.speed_kmh = 0.0
        self.satellites = 0
        self.has_fix = False
        self.fix_quality = 0
        self.last_update = 0.0
        self.serial_error = None  # None=正常, str=错误信息

    def start(self):
        if not SERIAL_AVAILABLE:
            self.serial_error = "pyserial 未安装"
            self._emit_empty()
            return
        self.running = True
        threading.Thread(target=self._read_loop, daemon=True).start()

    def _read_loop(self):
        try:
            self._serial = serial.Serial(self.port, self.baudrate, timeout=0.5)
        except Exception as e:
            self.serial_error = f"串口打开失败: {e}"
            self._emit_empty()
            return

        buf = ""
        while self.running:
            try:
                waiting = self._serial.in_waiting or 1
                chunk = self._serial.read(waiting).decode("ascii", errors="ignore")
                buf += chunk
                while "\n" in buf:
                    line, buf = buf.split("\n", 1)
                    self._parse_nmea(line.strip())
            except Exception:
                time.sleep(0.5)
        if self._serial:
            self._serial.close()

    def _parse_nmea(self, sentence):
        if not sentence.startswith("$"):
            return
        if "*" in sentence:
            sentence, _ = sentence.split("*", 1)
        parts = sentence.split(",")
        talker = parts[0]
        try:
            if talker.endswith("RMC"):
                self._parse_rmc(parts)
            elif talker.endswith("GGA"):
                self._parse_gga(parts)
        except Exception:
            pass

    def _parse_rmc(self, parts):
        if len(parts) < 10:
            return
        if parts[2] != "A":
            self.has_fix = False
            self._emit_update()
            return

        lat_str, lat_dir = parts[3], parts[4]
        if lat_str and lat_dir and len(lat_str) >= 4:
            lat = float(lat_str[:2]) + float(lat_str[2:]) / 60.0
            if lat_dir == "S":
                lat = -lat
            self.latitude = lat

        lon_str, lon_dir = parts[5], parts[6]
        if lon_str and lon_dir and len(lon_str) >= 5:
            lon = float(lon_str[:3]) + float(lon_str[3:]) / 60.0
            if lon_dir == "W":
                lon = -lon
            self.longitude = lon

        speed_str = parts[7]
        if speed_str:
            self.speed_kmh = float(speed_str) * 1.852

        self.has_fix = True
        self.last_update = time.time()
        self._emit_update()

    def _parse_gga(self, parts):
        if len(parts) < 8:
            return
        quality = parts[6]
        if quality:
            self.fix_quality = int(quality)
        sat_str = parts[7]
        if sat_str:
            self.satellites = int(sat_str)
        if self.fix_quality == 0:
            self.has_fix = False
        self._emit_update()

    def _emit_update(self):
        self.gps_data_updated.emit({
            "latitude": self.latitude,
            "longitude": self.longitude,
            "speed_kmh": self.speed_kmh,
            "satellites": self.satellites,
            "has_fix": self.has_fix,
            "fix_quality": self.fix_quality,
            "serial_error": self.serial_error,
            "updated_monotonic": time.monotonic(),
        })

    def _emit_empty(self):
        self.gps_data_updated.emit({
            "latitude": 0.0, "longitude": 0.0, "speed_kmh": 0.0,
            "satellites": 0, "has_fix": False, "fix_quality": 0,
            "serial_error": self.serial_error,
        })

    def stop(self):
        self.running = False
        if self._serial:
            self._serial.close()


class SystemUtils:
    @staticmethod
    def get_storage_devices():
        devices = []

        try:
            cmd = ["lsblk", "-o", "NAME,LABEL,MOUNTPOINT,TRAN,RM,PKNAME,TYPE", "-J"]
            res = sp.run(cmd, capture_output=True, text=True, timeout=5)
            block_devs = json.loads(res.stdout).get("blockdevices", [])
            
            # 1. 扫描根目录 / 对应的硬件父级盘名称
            root_disk_name = ""
            def find_root_disk(devs):
                nonlocal root_disk_name
                for d in devs:
                    if d.get("mountpoint") == "/":
                        root_disk_name = d.get("pkname") or d.get("name")
                        return
                    if "children" in d:
                        find_root_disk(d["children"])
            find_root_disk(block_devs)

            # 2. 深度遍历设备树
            def parse_devices(dev_list, parent_usb=False):
                for dev in dev_list:
                    dev_name = dev.get("name", "")
                    pk_name = dev.get("pkname", "")
                    dev_type = dev.get("type", "")
                    
                    if root_disk_name and (dev_name == root_disk_name or pk_name == root_disk_name):
                        continue
                    if dev_name.startswith("mmcblk") or dev_name.startswith("nvme") or dev_name.startswith("loop"):
                        continue

                    is_usb = parent_usb or (dev.get("tran") == "usb") or dev.get("rm") in [True, 1, "1", "true"]
                    
                    if dev_type == "disk" and "children" in dev and len(dev["children"]) > 0:
                        parse_devices(dev["children"], is_usb)
                        continue
                        
                    if not is_usb:
                        continue

                    mountpoint = dev.get("mountpoint")
                    label = dev.get("label") or f"U盘({dev_name})"
                    
                    if mountpoint in ["/", "/boot", "/boot/firmware"]:
                        continue
                        
                    # 挂载 USB 设备（需 root 运行 DashCam 才能写入 exFAT）
                    target_mount = f"/mnt/usb_{dev_name}"
                    if not mountpoint:
                        sp.run(["sudo", "-n", "mkdir", "-p", target_mount], capture_output=True, timeout=5)
                        m_res = sp.run(
                            ["sudo", "-n", "mount", f"/dev/{dev_name}", target_mount],
                            capture_output=True, text=True, timeout=10
                        )
                        if m_res.returncode == 0:
                            mountpoint = target_mount
                            print(f"🔌 挂载成功: /dev/{dev_name} -> {mountpoint}")
                        else:
                            print(f"⚠️ 无法挂载 /dev/{dev_name}: {m_res.stderr.strip()}")
                            continue
                            
                    # 🌟 额外保险：如果是以 root 挂载的目录，读取 psutil 前先确保当前账户有读权限
                    if mountpoint and os.path.exists(mountpoint):
                        try:
                            usage = psutil.disk_usage(mountpoint)
                            devices.append({
                                "name": label,
                                "path": mountpoint,
                                "free_gb": round(usage.free / (1024**3), 1),
                                "total_gb": round(usage.total / (1024**3), 1),
                                "percent": usage.percent,
                                "is_usb": True
                            })
                        except Exception as e:
                            print(f"⚠️ 读取容量失败 ({mountpoint}): {e}")
                            
            parse_devices(block_devs)
        except Exception as e:
            print(f"⚠️ 存储扫描异常: {e}")
            
        return devices

    @staticmethod
    def _cleanup_stale_mounts():
        """启动时清理已拔出 USB 遗留的挂载点（只卸载，不删除目录以免误删数据）"""
        import glob
        try:
            live = set()
            for dev in glob.glob("/dev/sd[a-z][0-9]*"):
                live.add(dev.split("/")[-1])
            for d in glob.glob("/mnt/usb_sd*"):
                devname = d.split("_", 1)[-1]
                if devname not in live:
                    sp.run(["sudo", "umount", "-l", d], capture_output=True)
                    # 只清理空目录，有内容的绝不动
                    try:
                        if not os.listdir(d):
                            sp.run(["sudo", "rmdir", d], capture_output=True)
                    except Exception:
                        pass
                    print(f"🧹 已清理废弃挂载: {d}")
        except Exception:
            pass

class CameraWorker(QObject):
    frame_ready = pyqtSignal(QImage, str) 
    playback_progress = pyqtSignal(int, int, str)
    storage_notify = pyqtSignal(str)  # 存储切换结果通知 UI（成功/失败）
    storage_path_changed = pyqtSignal(str)  # 存储路径变更（通知 UI 刷新 radio）
    recording_toggled = pyqtSignal(bool)  # 录制状态变更（同步 UI 按钮）
    storage_devices_updated = pyqtSignal(list)

    def __init__(self):
        super().__init__()
        self.cap = None
        self._snapshot_lock = threading.Lock()
        self._snapshot_frame = None
        self.running = False
        self.is_recording = False
        self.is_playback = False
        self.is_paused = False
        self.seek_frame = None
        self.playback_file = None
        self.new_playback_file = None  
        self.playback_fps = 30.0
        self.storage_path = ""  # 初始为空，等 U 盘检测
        self.storage_devices = []
        self._last_storage_check = 0.0
        self.fps_target = 30
        self.width = 1920
        self.height = 1080
        self._encoder_proc = None  # ffmpeg Popen（硬件/软件编码）或 None
        self._hw_encoder = None    # 检测到的 H.264 硬件编码器名，或 None
        self.record_start_time = 0
        self._record_start_monotonic = 0.0
        self.current_video_filename = ""
        
        self.lpr_catcher = None
        self.lpr_busy = False
        self.plates = []  # [(text, conf, box, detect_time), ...] — 支持多车牌
        self.last_detect_time = 0
        self.last_infer_time = 0
        self.last_log_time = 0
        self.known_plates = {}  # 已知车牌映射 {plate: nickname}，web_server 会同步更新
        
        self._restart_camera = False  # 切换参数时通知 _loop 重启摄像头
        self._writer_lock = threading.RLock()  # 保护 _encoder_proc 跨线程访问（可重入）
        self._plates_lock = threading.Lock()   # 保护 plates_log.json 并发写入
        self._segment_frame_count = 0  # 当前录制段帧数计数
        self._total_recorded_frames = 0  # 累计录制帧数
        self._pending_storage_path = None  # 待切换的目标路径
        self._frame_count = 0  # 用于周期性 USB 检测
        self._no_usb_shown = False  # 全屏 OSD 防重复
        
        # 检测硬件编码器（h264_v4l2m2m），不可用则回退到 ffmpeg libx264
        self._hw_encoder = self._detect_hw_encoder()
        
        # 启动时清理一次废弃挂载（仅一次，避免运行时误删）
        SystemUtils._cleanup_stale_mounts()
        
        # 启动时恢复上一次的 USB 存储位置
        saved = self._load_storage_config()
        if saved and os.path.ismount(saved) and os.path.isdir(saved):
            self.storage_path = saved
            print(f"📂 已恢复存储位置: {saved}")
        
        # 恢复用户手动设置的视频参数
        vp = self._load_video_params()
        if vp:
            self.width = vp.get("width", self.width)
            self.height = vp.get("height", self.height)
            self.fps_target = vp.get("fps", self.fps_target)
        # 启动时检测 U 盘：有则自动切换
        self._auto_select_usb()
        self._reload_known_plates()
        
        # GPS 模块（微雪 L76X，GPIO 串口）
        self.gps = GPSWorker()
        self.gps_data = {"has_fix": False, "serial_error": None}
        self.gps.gps_data_updated.connect(self._on_gps_data)

        threading.Thread(target=self._load_lpr_model, daemon=True).start()

    def _detect_hw_encoder(self):
        """检测可用的 H.264 硬件编码器，返回 'h264_v4l2m2m' 或 None"""
        try:
            # 先检查 ffmpeg 编码器列表
            res = sp.run(["ffmpeg", "-encoders"], capture_output=True, text=True, timeout=5)
            if "h264_v4l2m2m" not in res.stdout:
                return None
            # 验证编码器设备可用（尝试编码一帧）
            test = sp.run(
                ["ffmpeg", "-hide_banner", "-f", "lavfi", "-i",
                 "testsrc2=size=32x32:rate=1", "-c:v", "h264_v4l2m2m",
                 "-frames:v", "1", "-f", "null", "-"],
                capture_output=True, text=True, timeout=5
            )
            if test.returncode == 0:
                print("✅ 硬件编码器 h264_v4l2m2m 可用")
                return "h264_v4l2m2m"
        except Exception:
            pass
        print("⚠️ 硬件编码器不可用，将使用 ffmpeg libx264 软件编码")
        return None

    def switch_storage_path(self, new_path):
        """安全切换存储路径：先保存当前片段到旧盘，再在新盘开始新片段。
        
        如果新盘创建失败，自动回退到旧路径继续录像，不会卡死。
        """
        if self.storage_path == new_path:
            return
        self._pending_storage_path = new_path
        if self.is_recording and self._encoder_proc:
            print(f"🔄 正在保存当前片段到原盘，即将切换至: {new_path}")
            self.stop_recording_file()  # 保存旧盘文件，下个循环在新盘创建

    def _auto_select_usb(self):
        """检测 U 盘，有则自动切换，无则清空路径并通知 UI"""
        try:
            devices = SystemUtils.get_storage_devices()
            self.storage_devices = devices
            self.storage_devices_updated.emit(devices)
            for dev in devices:
                if dev.get("is_usb"):
                    usb_path = dev["path"]
                    # 先测试能否写入
                    test_file = os.path.join(usb_path, ".dashcam_write_test")
                    try:
                        with open(test_file, "w") as f:
                            f.write("ok")
                        os.remove(test_file)
                    except Exception:
                        continue
                    # 已经在正确的 U 盘上？
                    if self.storage_path == usb_path:
                        return
                    print(f"🔍 检测到 U 盘: {usb_path}，自动切换并开始录制...")
                    self.switch_storage_path(usb_path)
                    # 确保录制线程知道要开始录
                    if not self.is_recording:
                        self.is_recording = True
                        self.recording_toggled.emit(True)
                    return
            # 没找到可用的 USB
            if self.storage_path or self._pending_storage_path:
                # 之前有盘，现在没了
                self.storage_path = ""
                self._pending_storage_path = None
                self.stop_recording_file()
                self._save_storage_config("")
                self.is_recording = False
                self.recording_toggled.emit(False)
                self.storage_notify.emit("⚠️ U盘已拔出，请插入U盘")
                self.storage_path_changed.emit("")
                self._no_usb_shown = False
        except Exception:
            pass

    def _load_lpr_model(self):
        try:
            import hyperlpr3
            self.lpr_catcher = hyperlpr3.LicensePlateCatcher()
            print("✅ HyperLPR3 车牌识别模型加载成功！")
        except Exception as e:
            print(f"❌ AI 模型加载失败: {e}")

    def _log_plate_to_json(self, plate, conf):
        now = time.time()
        if now - self.last_log_time >= 10.0:
            self.last_log_time = now
            if not self.storage_path:
                return
            log_file = os.path.join(self.storage_path, "plates_log.json")
            offset_sec = round(self.recording_elapsed(), 1) if self.is_recording else 0
            gps = getattr(self, "gps_data", {})
            fresh_fix = bool(gps.get("has_fix") and gps.get("serial_error") is None and
                             time.monotonic() - gps.get("updated_monotonic", 0) < 5.0)
            entry = {
                "time_source": "system_clock",
                "location_source": "gps_nmea" if fresh_fix else None,
                "gps_has_fix": fresh_fix,
                "latitude": gps.get("latitude") if fresh_fix else None,
                "longitude": gps.get("longitude") if fresh_fix else None,
                "time_str": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "timestamp": now,
                "plate": plate,
                "confidence": round(float(conf), 2),
                "video_file": self.current_video_filename,
                "video_offset_sec": offset_sec
            }
            try:
                with self._plates_lock:
                    data = []
                    if os.path.exists(log_file):
                        with open(log_file, "r", encoding="utf-8") as f:
                            data = json.load(f)
                    data.append(entry)
                    if len(data) > 3000: data = data[-3000:]
                    # 原子写入：先写临时文件再 rename，防止断电/并发导致文件损坏
                    tmp = log_file + ".tmp"
                    with open(tmp, "w", encoding="utf-8") as f:
                        json.dump(data, f, ensure_ascii=False, indent=2)
                    os.replace(tmp, log_file)
            except Exception:
                pass

    def _lpr_task(self, frame_copy):
        self.lpr_busy = True
        self.last_infer_time = time.time()
        try:
            if self.lpr_catcher:
                results = self.lpr_catcher(frame_copy)
                if results:
                    now = time.time()
                    new_plates = []
                    for plate in results:
                        conf = float(plate[1])
                        if conf > 0.95:
                            text = plate[0]
                            box = [int(v) for v in plate[3]]
                            new_plates.append((text, conf, box, now))
                    # 多车牌时只保留框面积最大的 3 个（节省树莓派 CPU）
                    new_plates = sorted(new_plates, key=lambda p: (p[2][2]-p[2][0])*(p[2][3]-p[2][1]), reverse=True)[:3]
                    for text, conf, *_ in new_plates:
                        self._log_plate_to_json(text, conf)
                    if new_plates:
                        self.plates = new_plates
                        self.last_detect_time = now
        except Exception:
            pass
        finally:
            self.lpr_busy = False

    def _storage_ready(self, path):
        return bool(path and os.path.ismount(path) and os.access(path, os.W_OK))

    def _check_and_clean_disk(self, path=None):
        """只回收本应用的旧录像，持续释放到安全空间。"""
        path = path or self.storage_path
        try:
            files = sorted(
                (os.path.join(path, name) for name in os.listdir(path)
                 if name.startswith("REC_") and name.endswith((".mkv", ".mp4"))
                 and not os.path.islink(os.path.join(path, name))
                 and name != self.current_video_filename), key=os.path.getmtime)
            while True:
                usage = psutil.disk_usage(path)
                reserve = min(1.5 * 1024**3, usage.total * .1)
                if usage.percent <= 88 and usage.free >= reserve:
                    return True
                if not files:
                    return False
                os.remove(files.pop(0))
        except OSError as exc:
            print(f"存储检查失败: {exc}")
            return False

    def start_camera(self):
        if self.cap is None or not self.cap.isOpened():
            self.cap = open_camera(self.width, self.height, self.fps_target)
        self.running = True
        self.gps.start()
        self._camera_thread = threading.Thread(target=self._loop, daemon=True)
        self._camera_thread.start()

    def get_camera_caps(self):
        """查询摄像头支持的分辨率上限和帧率，返回 (res_list, fps_list)
        优先使用 v4l2-ctl 精确查询，失败时回退到尝试法"""
        import re
        res_set = set()
        fps_set = set()

        if isinstance(self.cap, PiCameraCapture):
            # Processed output sizes, rather than raw Unicam Bayer formats.
            modes = self.cap.sensor_modes
            resolutions = [(w, h) for w, h in ((640, 480), (1280, 720), (1920, 1080))
                           if any("size" in m and m["size"][0] >= w and m["size"][1] >= h for m in modes)]
            return resolutions, [15, 25, 30]

        # 方案 1：v4l2-ctl（精确，不受 OpenCV 占用影响）
        try:
            result = sp.run(
                ["v4l2-ctl", "--list-formats-ext"],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                text = result.stdout
                current_w, current_h, current_max_fps = 0, 0, 0

                def _flush_res():
                    nonlocal current_w, current_h, current_max_fps
                    if current_w > 0 and current_h > 480 and current_max_fps > 15:
                        res_set.add((current_w, current_h))
                    current_w, current_h, current_max_fps = 0, 0, 0

                for line in text.split('\n'):
                    m_size = re.match(r"\s*Size:\s*Discrete\s+(\d+)x(\d+)", line)
                    if m_size:
                        _flush_res()  # 保存上一个分辨率（如果 fps 够高）
                        w, h = int(m_size.group(1)), int(m_size.group(2))
                        if 80 <= w <= 2592 and 60 <= h <= 1944:
                            current_w, current_h = w, h
                        continue
                    m_int = re.match(r"\s*Interval:\s*Discrete\s+\S+\s+\((\d+\.?\d*)\s*fps\)", line)
                    if m_int and current_w > 0:
                        fps = round(float(m_int.group(1)))
                        if fps > current_max_fps:
                            current_max_fps = fps
                        if fps > 15 and fps <= 120:
                            fps_set.add(fps)
                _flush_res()  # 最后一个分辨率
        except Exception:
            pass

        # 方案 2：回退 — 用 OpenCV 尝试常见分辨率（需释放摄像头）
        if not res_set:
            candidates = [
                (320, 240), (640, 480), (800, 600),
                (1024, 768), (1280, 720), (1280, 960),
                (1920, 1080),
            ]
            # 临时暂停主循环，用工作摄像头测试
            was_running = self.running
            self.running = False
            time.sleep(0.2)
            try:
                if self.cap and self.cap.isOpened():
                    for w, h in candidates:
                        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, w)
                        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)
                        aw = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                        ah = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                        if aw > 0 and ah > 0:
                            res_set.add((aw, ah))
                    # 恢复原参数
                    self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
                    self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
            except Exception:
                pass
            self.running = was_running
            if not self.running and self.cap:
                threading.Thread(target=self._loop, daemon=True).start()

        if not res_set:
            res_set = {(1280, 720), (1920, 1080)}

        if not fps_set:
            fps_set = {20, 25, 30, 60}

        res_list = sorted(res_set, key=lambda x: x[0] * x[1])
        fps_list = sorted(fps_set)
        return res_list, fps_list

    def apply_video_params(self, width, height, fps):
        """设置新参数，由 _loop 线程安全完成全部切换"""
        self.width = width
        self.height = height
        self.fps_target = fps
        self._save_video_params()
        self._restart_camera = True  # _loop 线程处理停止录制+重启+恢复录制

    def _on_gps_data(self, data):
        self.gps_data = data

    def _loop(self):
        while self.running:
            try:
                self._loop_tick()
            except Exception:
                import traceback
                print("❌ _loop 线程异常，1 秒后恢复:")
                traceback.print_exc()
                time.sleep(1.0)

    def _loop_tick(self):
        tick_start = time.monotonic()
        # 线程安全重启摄像头（由 apply_video_params 触发）
        if self._restart_camera:
            self._restart_camera = False
            was_recording = self.is_recording
            # 1. 安全停止录制
            self.is_recording = False
            with self._writer_lock:
                if self._encoder_proc:
                    self._close_encoder()
                    self.current_video_filename = ""
            if was_recording:
                self.recording_toggled.emit(False)
            # 2. 重启摄像头
            if self.cap and self.cap.isOpened():
                self.cap.release()
            self.cap = open_camera(self.width, self.height, self.fps_target)
            # 3. 恢复录制
            if was_recording:
                self.is_recording = True
                self.recording_toggled.emit(True)
            return

        if self.is_playback and self.playback_file:
            if self.is_recording:
                self.is_recording = False
                self.recording_toggled.emit(False)
            self.stop_recording_file()
            self._run_playback()
            return

        if self.cap is None or not self.cap.isOpened():
            time.sleep(0.2)
            return
        ret, frame = self.cap.read()
        self._last_capture_monotonic = time.monotonic()
        if not ret:
            time.sleep(0.01)
            return

        self._frame_count += 1
        current_time = time.time()

        # —— OSD 每帧都绘制（避免闪烁） ——
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        font = cv2.FONT_HERSHEY_SIMPLEX
        font_scale = 0.85
        thickness_bg = 5
        thickness_fg = 2

        (text_w, text_h), _ = cv2.getTextSize(now_str, font, font_scale, thickness_bg)
        x_pos = self.width - text_w - 20
        y_pos = self.height - 20

        gps = self.gps_data
        if gps.get("serial_error") is None:
            if gps.get("has_fix"):
                lat = gps["latitude"]
                lon = gps["longitude"]
                lat_hemi = "N" if lat >= 0 else "S"
                lon_hemi = "E" if lon >= 0 else "W"
                gps_str = f"{abs(lat):.5f}°{lat_hemi}  {abs(lon):.5f}°{lon_hemi}"
                gps_scale = 0.55
                (gw, _), _ = cv2.getTextSize(gps_str, font, gps_scale, 4)
                cv2.putText(frame, gps_str, (self.width - gw - 20, y_pos - 28),
                            font, gps_scale, (0, 0, 0), 4, cv2.LINE_8)
                cv2.putText(frame, gps_str, (self.width - gw - 20, y_pos - 28),
                            font, gps_scale, (255, 255, 255), 1, cv2.LINE_AA)
                info_str = f"{gps['satellites']} sat  {gps['speed_kmh']:.0f} km/h"
                cv2.putText(frame, info_str, (20, 108), font, 0.5, (0, 0, 0), 4, cv2.LINE_8)
                cv2.putText(frame, info_str, (20, 108), font, 0.5, (0, 255, 255), 1, cv2.LINE_AA)
            else:
                cv2.putText(frame, "GPS: No Fix", (x_pos, y_pos - 28),
                            font, 0.5, (0, 0, 0), 4, cv2.LINE_8)
                cv2.putText(frame, "GPS: No Fix", (x_pos, y_pos - 28),
                            font, 0.5, (128, 128, 128), 1, cv2.LINE_AA)

        cv2.putText(frame, now_str, (x_pos, y_pos), font, font_scale, (0, 0, 0), thickness_bg, cv2.LINE_8)
        cv2.putText(frame, now_str, (x_pos, y_pos), font, font_scale, (255, 255, 255), thickness_fg, cv2.LINE_AA)

        if self.is_recording:
            elapsed = int(self.recording_elapsed())
            cv2.circle(frame, (40, 40), 12, (0, 0, 255), -1)
            cv2.putText(frame, f"REC {elapsed}s", (60, 48), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 0, 255), 2)
            try:
                self._handle_recording(frame)
            except Exception:
                pass
        else:
            if self._encoder_proc is not None:
                self.stop_recording_file()


        # LPR 识别跳帧执行（节省 CPU 用于预览）
        if self._frame_count % 3 == 0:
            if self.lpr_catcher and not self.lpr_busy and (current_time - self.last_infer_time > 0.8):
                small_frame = cv2.resize(frame, (self.width // 2, self.height // 2))
                threading.Thread(target=self._lpr_task, args=(small_frame,), daemon=True).start()

        osd_text = ""
        if self.plates and (current_time - self.last_detect_time < 2.5):
            parts = []
            for plate_text, plate_conf, plate_box, _ in self.plates:
                x1, y1, x2, y2 = [v * 2 for v in plate_box]
                cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 3)
                nickname = self.known_plates.get(plate_text, "")
                if nickname:
                    parts.append(f"📷 {nickname}({plate_text})")
                else:
                    parts.append(f"📷 {plate_text} ({plate_conf:.2f})")
            osd_text = "\n".join(parts)

        # 预览帧缩小至 1/4 像素（640×360 @ 1080p），大幅减少 QImage 开销
        preview_frame = cv2.resize(frame, (0, 0), fx=0.5, fy=0.5, interpolation=cv2.INTER_LINEAR)
        with self._snapshot_lock:
            self._snapshot_frame = preview_frame
        rgb_frame = cv2.cvtColor(preview_frame, cv2.COLOR_BGR2RGB)
        h, w, ch = rgb_frame.shape
        q_img = QImage(rgb_frame.data, w, h, ch * w, QImage.Format_RGB888).copy()

        self.frame_ready.emit(q_img, osd_text)

        # 每 3 秒检测一次 U 盘热插拔
        if time.monotonic() - self._last_storage_check >= 3:
            self._last_storage_check = time.monotonic()
            self._auto_select_usb()

        # cap.read/OSD/编码本身已耗时，只休眠本帧剩余预算。
        time.sleep(max(0.0, 1.0 / self.fps_target - (time.monotonic() - tick_start)))
                
    def _handle_recording(self, frame):
        with self._writer_lock:
            if self.is_recording:
                self._handle_recording_locked(frame)

    def get_snapshot_jpeg(self):
        """只读取主循环发布的画面，不跨线程访问 VideoCapture。"""
        with self._snapshot_lock:
            frame = self._snapshot_frame
        if frame is None or not self.running:
            return None
        ok, jpeg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
        return jpeg.tobytes() if ok else None

    def _handle_recording_locked(self, frame):
        # 未检测到 U 盘（且无待切换路径）：停止录制
        if not self.storage_path and not self._pending_storage_path:
            self.is_recording = False
            self.stop_recording_file()
            return
        
        now = time.time()
        # 更改系统时间时也换段，避免 wallclock 输入时间戳向前/向后跳变。
        clock_changed = (self._encoder_proc is not None and
                         abs((now - self.record_start_time) - self.recording_elapsed()) > 2.0)
        if self._encoder_proc is None or self.recording_elapsed() >= 180 or clock_changed:
            self.stop_recording_file()
            
            # 处理待切换的存储路径：先尝试在新盘创建，失败则回退到旧盘
            target_path = self.storage_path
            if self._pending_storage_path is not None:
                target_path = self._pending_storage_path
            
            if not self._storage_ready(target_path):
                self._pending_storage_path = None
                self.storage_path = ""
                self.is_recording = False
                self.recording_toggled.emit(False)
                self.storage_notify.emit("存储设备不可用，请检查 U 盘连接")
                return
            if self._check_and_clean_disk(target_path) is False:
                self.is_recording = False
                self.recording_toggled.emit(False)
                self.storage_notify.emit("存储空间不足，请清理 U 盘后重新录像")
                return

            try:
                os.makedirs(target_path, exist_ok=True)
            except Exception as e:
                print(f"❌ 无法创建目录 {target_path}: {e}")
                if self._pending_storage_path is not None:
                    print(f"⚠️ 新盘不可用，回退到旧路径: {self.storage_path}")
                    self.storage_notify.emit(f"⚠️ 无法创建目录 {target_path}，已回退旧盘")
                    self._pending_storage_path = None
                return
            
            self.current_video_filename = datetime.now().strftime("REC_%Y%m%d_%H%M%S.mkv")
            filepath = os.path.join(target_path, self.current_video_filename)
            if os.path.exists(filepath):
                self.current_video_filename = datetime.now().strftime("REC_%Y%m%d_%H%M%S_%f.mkv")
                filepath = os.path.join(target_path, self.current_video_filename)
            
            # 启动 ffmpeg 编码器（硬件优先，不可用时用 libx264）
            self._encoder_proc = self._start_ffmpeg_encoder(filepath, self.fps_target)
            if self._encoder_proc is None:
                print(f"❌ 致命错误：无法启动编码器写入 {filepath}！")
                if self._pending_storage_path is not None:
                    print(f"⚠️ 新盘创建失败，回退到旧路径: {self.storage_path}")
                    self.storage_notify.emit(f"⚠️ 无法写入 {target_path}，已回退旧盘")
                    self._pending_storage_path = None
                return
            
            # 新盘切换成功，正式更新路径
            if self._pending_storage_path is not None:
                self.storage_path = self._pending_storage_path
                self._pending_storage_path = None
                self._save_storage_config(self.storage_path)
                self.storage_notify.emit(f"✅ 已切换至新盘: {self.storage_path}")
                self.storage_path_changed.emit(self.storage_path)
                print(f"✅ 存储路径已切换至: {self.storage_path}")
                self._init_storage_configs(self.storage_path)
                
            self.record_start_time = time.time()
            self._record_start_monotonic = time.monotonic()
            self._segment_frame_count = 0
            print(f"🎬 开始录制新分段: {filepath} [{self._hw_encoder or 'libx264'}]")

        if self._encoder_proc:
            try:
                with self._writer_lock:
                    if self._encoder_proc:
                        self._write_frame(frame)
            except Exception:
                # U盘拔出等写入失败：停止录制
                print(f"⚠️ 写入失败（U盘已拔出），停止录制")
                self.stop_recording_file()
                self.storage_path = ""
                self._save_storage_config("")
                self._pending_storage_path = None
                self.is_recording = False
                self._no_usb_shown = False
                self.storage_notify.emit("⚠️ U盘写入失败，已停止录制")

    def recording_elapsed(self):
        """当前片段经过时间，不受 GPS/RTC/手动校时影响。"""
        if not self._record_start_monotonic:
            return 0.0
        return max(0.0, time.monotonic() - self._record_start_monotonic)

    def stop_recording_file(self):
        """安全关闭当前录制分段"""
        with self._writer_lock:
            if self._encoder_proc:
                self._close_encoder()
                self.current_video_filename = ""
                print("🛑 录制底层资源已安全释放。")
            self._record_start_monotonic = 0.0
            self.record_start_time = 0

    def _start_ffmpeg_encoder(self, filepath, fps):
        """启动 ffmpeg 子进程进行 H.264 编码，返回 Popen 或 None。
        
        优先使用硬件编码器（h264_v4l2m2m），不可用时回退到 libx264 软件编码。
        """
        encoder = self._hw_encoder or "libx264"
        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostats",
            "-progress", "pipe:1", "-stats_period", "0.1",
            # NUT carries acquisition PTS across pipe/startup delays. FFmpeg
            # wallclock timestamps would discard time blocked on its first read.
            "-f", "nut", "-probesize", "32", "-analyzeduration", "0",
            "-fpsprobesize", "0", "-i", "-",
            "-c:v", encoder,
            "-b:v", "4M",
            "-g", "30",
            "-r", str(fps), "-vsync", "cfr",
        ]
        if encoder == "h264_v4l2m2m":
            cmd += ["-num_capture_buffers", "16"]
        if encoder == "libx264":
            cmd += ["-preset", "ultrafast", "-tune", "zerolatency"]
        cmd += ["-pix_fmt", "yuv420p", "-f", "matroska", "-y", filepath]
        
        # 不使用无人读取的 stderr PIPE，避免长时间录制被日志堵住。
        error_log = tempfile.TemporaryFile()
        try:
            spawn_started = time.monotonic()
            proc = sp.Popen(cmd, stdin=sp.PIPE, stdout=sp.PIPE, stderr=error_log)
            proc._dashcam_error_log = error_log
            proc._dashcam_first_input = False
            proc._dashcam_last_pts = -1
            proc._dashcam_mux = av.open(proc.stdin, mode="w", format="nut")
            stream = proc._dashcam_mux.add_stream("rawvideo", rate=int(fps))
            stream.width, stream.height, stream.pix_fmt = self.width, self.height, "bgr24"
            stream.time_base = stream.codec_context.time_base = Fraction(1, 1000000)
            proc._dashcam_stream = stream
            print(f"RECORD_TIMING event=encoder_spawn mono={time.monotonic():.6f} "
                  f"started={spawn_started:.6f} pid={proc.pid} codec={encoder}")
            # Drain progress continuously; readiness means the first encoded output,
            # not merely Popen success. Keep this independent of Qt and camera reads.
            def read_progress():
                first_output = False
                try:
                    for line in proc.stdout:
                        if line.startswith(b"frame=") and int(line.split(b"=", 1)[1]) > 0 and not first_output:
                            first_output = True
                            print(f"RECORD_TIMING event=encoder_first_output mono={time.monotonic():.6f} pid={proc.pid}")
                finally:
                    proc.stdout.close()
            threading.Thread(target=read_progress, daemon=True).start()
            return proc
        except Exception as e:
            error_log.close()
            print(f"❌ 启动 ffmpeg 编码器失败: {e}")
            return None

    def _write_frame(self, frame):
        """向 ffmpeg 编码器写入一帧"""
        try:
            proc = self._encoder_proc
            first_input = not getattr(proc, "_dashcam_first_input", False)
            if first_input:
                print(f"RECORD_TIMING event=first_input_begin mono={time.monotonic():.6f} pid={proc.pid}")
            captured = getattr(self, "_last_capture_monotonic", time.monotonic())
            elapsed = max(0.0, captured - self._record_start_monotonic)
            pts = 0 if first_input else max(proc._dashcam_last_pts + 1, round(elapsed * 1000000))
            video_frame = av.VideoFrame.from_ndarray(frame, format="bgr24")
            video_frame.pts, video_frame.time_base = pts, Fraction(1, 1000000)
            for packet in proc._dashcam_stream.encode(video_frame):
                proc._dashcam_mux.mux(packet)
            proc._dashcam_last_pts = pts
            if first_input:
                proc.stdin.flush()
                proc._dashcam_first_input = True
                print(f"RECORD_TIMING event=first_input_written mono={time.monotonic():.6f} pid={proc.pid}")
            self._segment_frame_count += 1
            self._total_recorded_frames += 1
        except BrokenPipeError:
            raise Exception("ffmpeg pipe 断开")

    def _close_encoder(self):
        """安全关闭 ffmpeg 编码器进程（关闭 stdin → 等待退出 → 必要时 kill）"""
        if self._encoder_proc is None:
            return
        proc = self._encoder_proc
        print(f"RECORD_TIMING event=encoder_close_begin mono={time.monotonic():.6f} pid={proc.pid} inputs={self._segment_frame_count}")
        self._encoder_proc = None
        try:
            mux = getattr(proc, "_dashcam_mux", None)
            if mux:
                try:
                    for packet in proc._dashcam_stream.encode(None):
                        mux.mux(packet)
                finally:
                    mux.close()
        except Exception as exc:
            print(f"编码流收尾失败: {exc}")
        try:
            if proc.stdin:
                proc.stdin.close()
        except Exception:
            pass
        try:
            proc.wait(timeout=5)
        except sp.TimeoutExpired:
            proc.kill()
            proc.wait()
        print(f"RECORD_TIMING event=encoder_exit mono={time.monotonic():.6f} pid={proc.pid} status={proc.returncode}")
        error_log = getattr(proc, "_dashcam_error_log", None)
        if error_log:
            if proc.returncode:
                error_log.seek(max(0, error_log.seek(0, os.SEEK_END) - 500))
                error = error_log.read().decode("utf-8", errors="replace").strip()
                if error:
                    print(f"❌ 编码器退出: {error}")
            error_log.close()

    def _load_storage_config(self):
        """读取上次保存的存储路径"""
        cfg = os.path.join(os.path.expanduser("~/Videos"), "storage_config.json")
        try:
            if os.path.exists(cfg):
                with open(cfg, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    return data.get("storage_path", "")
        except Exception:
            pass
        return ""

    def _save_storage_config(self, path):
        """持久化存储路径，下次启动自动恢复"""
        cfg = os.path.join(os.path.expanduser("~/Videos"), "storage_config.json")
        try:
            os.makedirs(os.path.dirname(cfg), exist_ok=True)
            with open(cfg, "w", encoding="utf-8") as f:
                json.dump({"storage_path": path}, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _init_storage_configs(self, path):
        """U 盘切换后自动初始化缺失的配置文件，确保存储就绪"""
        configs = {
            "plates_log.json": [],
            "video_params.json": {
                "width": self.width,
                "height": self.height,
                "fps": self.fps_target
            },
            "wifi_passwords.json": {},
            "known_plates.json": {}
        }
        for filename, default in configs.items():
            filepath = os.path.join(path, filename)
            if not os.path.exists(filepath):
                try:
                    with open(filepath, "w", encoding="utf-8") as f:
                        json.dump(default, f, ensure_ascii=False, indent=2)
                    print(f"📄 已创建配置文件: {filename}")
                except Exception as e:
                    print(f"⚠️ 无法创建 {filename}: {e}")

    def _reload_known_plates(self):
        """从文件加载已知车牌映射（优先 U 盘，回退系统盘）"""
        if self.storage_path:
            cfg = os.path.join(self.storage_path, "known_plates.json")
        else:
            cfg = os.path.expanduser("~/Videos/known_plates.json")
        try:
            if os.path.exists(cfg):
                with open(cfg, "r", encoding="utf-8") as f:
                    self.known_plates = json.load(f)
                    print(f"📋 已加载 {len(self.known_plates)} 个已知车牌")
        except Exception:
            pass

    def _load_video_params(self):
        """读取用户手动设置并保存的视频参数（优先 U 盘）"""
        # 优先从 U 盘读取
        if self.storage_path:
            cfg = os.path.join(self.storage_path, "video_params.json")
            try:
                if os.path.exists(cfg):
                    with open(cfg, "r", encoding="utf-8") as f:
                        return json.load(f)
            except Exception:
                pass
        # 回退到系统盘
        cfg = os.path.join(os.path.expanduser("~/Videos"), "video_params.json")
        try:
            if os.path.exists(cfg):
                with open(cfg, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception:
            pass
        return {}

    def _save_video_params(self):
        """持久化用户手动设置的视频参数到 U 盘"""
        if not self.storage_path:
            return
        cfg = os.path.join(self.storage_path, "video_params.json")
        try:
            with open(cfg, "w", encoding="utf-8") as f:
                json.dump({
                    "width": self.width,
                    "height": self.height,
                    "fps": self.fps_target
                }, f, ensure_ascii=False, indent=2)
        except Exception:
            pass
            
    def _run_playback(self):
        p_cap = cv2.VideoCapture(self.playback_file)
        total_frames = int(p_cap.get(cv2.CAP_PROP_FRAME_COUNT))
        fps = p_cap.get(cv2.CAP_PROP_FPS) or 30.0
        self.playback_fps = fps
        
        def load_logs(filepath):
            video_name = os.path.basename(filepath)
            log_file = os.path.join(self.storage_path, "plates_log.json")
            if os.path.exists(log_file):
                try:
                    with open(log_file, "r", encoding="utf-8") as f:
                        return [item for item in json.load(f) if item.get("video_file") == video_name]
                except Exception:
                    pass
            return []

        matched_logs = load_logs(self.playback_file)

        while self.running and self.is_playback:
            frame_start = time.monotonic()
            if self.new_playback_file:
                p_cap.release()
                self.playback_file = self.new_playback_file
                self.new_playback_file = None
                p_cap = cv2.VideoCapture(self.playback_file)
                total_frames = int(p_cap.get(cv2.CAP_PROP_FRAME_COUNT))
                fps = p_cap.get(cv2.CAP_PROP_FPS) or 30.0
                self.playback_fps = fps
                matched_logs = load_logs(self.playback_file)
                self.seek_frame = 0 
                continue

            if not p_cap.isOpened():
                break

            seeking = self.seek_frame is not None
            if seeking:
                p_cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, min(self.seek_frame, total_frames - 1)))
                self.seek_frame = None
                
            if self.is_paused and not seeking:
                time.sleep(0.05)
                continue
                
            ret, frame = p_cap.read()
            if not ret:
                self.is_paused = True
                continue
                
            current_frame = int(p_cap.get(cv2.CAP_PROP_POS_FRAMES))
            current_sec = current_frame / fps
            
            current_plate_osd = ""
            for log in matched_logs:
                if abs(log.get("video_offset_sec", -999) - current_sec) <= 3.0:
                    current_plate_osd = f"📂 历史车牌: {log['plate']} ({log['confidence']})"
                    break
                    
            cv2.putText(frame, "[Playback Mode]", (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 200, 255), 2)
            rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            h, w, ch = rgb_frame.shape
            q_img = QImage(rgb_frame.data, w, h, ch * w, QImage.Format_RGB888).copy()
            
            self.frame_ready.emit(q_img, current_plate_osd)
            self.playback_progress.emit(current_frame, total_frames, current_plate_osd)
            time.sleep(max(0.0, 1 / fps - (time.monotonic() - frame_start)))
            
        p_cap.release()
        self.is_playback = False
        self.is_paused = False
        self.new_playback_file = None

    def stop(self):
        self.running = False
        self.is_recording = False
        self.stop_recording_file()
        self.gps.stop()
        camera_thread = getattr(self, "_camera_thread", None)
        if camera_thread and camera_thread is not threading.current_thread():
            camera_thread.join(timeout=3)
        # VideoCapture.read 尚未返回时不能从 UI 线程 release，否则可能原生崩溃。
        if self.cap and (camera_thread is None or not camera_thread.is_alive()):
            self.cap.release()
