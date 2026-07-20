# core_camera.py
import os
import time
import json
import psutil
import threading
import subprocess
import cv2
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
        
        # 清理已拔出 USB 留下的废弃挂载目录
        SystemUtils._cleanup_stale_mounts()
        
        try:
            cmd = ["lsblk", "-o", "NAME,LABEL,MOUNTPOINT,TRAN,RM,PKNAME,TYPE", "-J"]
            res = subprocess.run(cmd, capture_output=True, text=True)
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
            def parse_devices(dev_list):
                for dev in dev_list:
                    dev_name = dev.get("name", "")
                    pk_name = dev.get("pkname", "")
                    dev_type = dev.get("type", "")
                    
                    if root_disk_name and (dev_name == root_disk_name or pk_name == root_disk_name):
                        continue
                    if dev_name.startswith("mmcblk") or dev_name.startswith("nvme") or dev_name.startswith("loop"):
                        continue

                    is_usb = (dev.get("tran") == "usb") or dev.get("rm") in [True, 1, "1", "true"]
                    
                    if dev_type == "disk" and "children" in dev and len(dev["children"]) > 0:
                        parse_devices(dev["children"])
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
                        subprocess.run(["sudo", "mkdir", "-p", target_mount], capture_output=True)
                        m_res = subprocess.run(
                            ["sudo", "mount", f"/dev/{dev_name}", target_mount],
                            capture_output=True, text=True
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
                                "name": f"🔌 {label}",
                                "path": mountpoint,
                                "free_gb": round(usage.free / (1024**3), 1),
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
        """卸载并删除已不存在的 USB 设备对应的 /mnt/usb_* 目录"""
        import glob
        try:
            # 获取当前存在的 sd 设备列表
            live = set()
            for dev in glob.glob("/dev/sd[a-z]"):
                live.add(dev.split("/")[-1])
            for d in glob.glob("/mnt/usb_sd*"):
                devname = d.split("_", 1)[-1]
                if devname not in live:
                    # 设备已不存在 → 强制卸载并删除目录
                    subprocess.run(["sudo", "umount", "-l", d], capture_output=True)
                    subprocess.run(["sudo", "rm", "-rf", d], capture_output=True)
                    print(f"🧹 已清理废弃挂载: {d}")
        except Exception:
            pass

class CameraWorker(QObject):
    frame_ready = pyqtSignal(QImage, str) 
    playback_progress = pyqtSignal(int, int, str)
    storage_notify = pyqtSignal(str)  # 存储切换结果通知 UI（成功/失败）
    storage_path_changed = pyqtSignal(str)  # 存储路径变更（通知 UI 刷新 radio）
    recording_toggled = pyqtSignal(bool)  # 录制状态变更（同步 UI 按钮）

    def __init__(self):
        super().__init__()
        self.cap = None
        self.running = False
        self.is_recording = False
        self.is_playback = False
        self.is_paused = False
        self.seek_frame = None
        self.playback_file = None
        self.new_playback_file = None  
        self.storage_path = ""  # 初始为空，等 U 盘检测
        self.fps_target = 30
        self.width = 1920
        self.height = 1080
        self.video_writer = None
        self.record_start_time = 0
        self.current_video_filename = ""
        
        self.lpr_catcher = None
        self.lpr_busy = False
        self.plates = []  # [(text, conf, box, detect_time), ...] — 支持多车牌
        self.last_detect_time = 0
        self.last_infer_time = 0
        self.last_log_time = 0
        
        self._pending_storage_path = None  # 待切换的目标路径
        self._frame_count = 0  # 用于周期性 USB 检测
        self._no_usb_shown = False  # 全屏 OSD 防重复
        
        # 启动时恢复上一次的 USB 存储位置
        saved = self._load_storage_config()
        if saved and os.path.ismount(saved) and os.path.isdir(saved):
            self.storage_path = saved
            print(f"📂 已恢复存储位置: {saved}")
        
        # 启动时检测 U 盘：有则自动切换
        self._auto_select_usb()
        
        # GPS 模块（微雪 L76X，GPIO 串口）
        self.gps = GPSWorker()
        self.gps_data = {"has_fix": False, "serial_error": None}
        self.gps.gps_data_updated.connect(self._on_gps_data)

        threading.Thread(target=self._load_lpr_model, daemon=True).start()

    def switch_storage_path(self, new_path):
        """安全切换存储路径：先保存当前片段到旧盘，再在新盘开始新片段。
        
        如果新盘创建失败，自动回退到旧路径继续录像，不会卡死。
        """
        if self.storage_path == new_path:
            return
        self._pending_storage_path = new_path
        if self.is_recording and self.video_writer:
            print(f"🔄 正在保存当前片段到原盘，即将切换至: {new_path}")
            self.stop_recording_file()  # 保存旧盘文件，下个循环在新盘创建

    def _auto_select_usb(self):
        """检测 U 盘，有则自动切换，无则清空路径并通知 UI"""
        try:
            devices = SystemUtils.get_storage_devices()
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
            if self.storage_path:
                # 之前有盘，现在没了
                self.storage_path = ""
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
            log_file = os.path.join(self.storage_path, "plates_log.json")
            offset_sec = round(now - self.record_start_time, 1) if (self.is_recording and self.record_start_time > 0) else 0
            entry = {
                "time_str": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "timestamp": now,
                "plate": plate,
                "confidence": round(float(conf), 2),
                "video_file": self.current_video_filename,
                "video_offset_sec": offset_sec
            }
            try:
                data = []
                if os.path.exists(log_file):
                    with open(log_file, "r", encoding="utf-8") as f:
                        data = json.load(f)
                data.append(entry)
                if len(data) > 3000: data = data[-3000:]
                with open(log_file, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
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
                        if conf > 0.85:
                            text = plate[0]
                            box = [int(v) for v in plate[3]]
                            new_plates.append((text, conf, box, now))
                            self._log_plate_to_json(text, conf)
                    if new_plates:
                        self.plates = new_plates
                        self.last_detect_time = now
        except Exception:
            pass
        finally:
            self.lpr_busy = False

    def _check_and_clean_disk(self):
        try:
            usage = psutil.disk_usage(self.storage_path)
            free_gb = usage.free / (1024**3)
            if usage.percent > 88.0 or free_gb < 1.5:
                files = [os.path.join(self.storage_path, f) for f in os.listdir(self.storage_path) if f.endswith((".mkv", ".mp4"))]
                if files:
                    files.sort(key=lambda x: os.path.getmtime(x))
                    os.remove(files[0])
        except Exception:
            pass

    def start_camera(self):
        if self.cap is None or not self.cap.isOpened():
            self.cap = cv2.VideoCapture(0)
            # 设置 MJPG 格式以支持 1080p（Pi Camera YUYV 最高仅 800×448）
            self.cap.set(cv2.CAP_PROP_FOURCC, 1196444237)  # MJPG
            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        self.running = True
        self.gps.start()
        threading.Thread(target=self._loop, daemon=True).start()

    def get_camera_caps(self):
        """查询摄像头支持的分辨率上限和帧率，返回 (res_list, fps_list)
        优先使用 v4l2-ctl 精确查询，失败时回退到尝试法"""
        import re
        res_set = set()
        fps_set = set()

        # 方案 1：v4l2-ctl（精确，不受 OpenCV 占用影响）
        try:
            result = subprocess.run(
                ["v4l2-ctl", "--list-formats-ext"],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                text = result.stdout
                current_w, current_h = 0, 0
                # 逐行解析：Size: Discrete 后紧跟 Interval，关联处理
                for line in text.split('\n'):
                    m_size = re.match(r"\s*Size:\s*Discrete\s+(\d+)x(\d+)", line)
                    if m_size:
                        w, h = int(m_size.group(1)), int(m_size.group(2))
                        # 只接受合理视频分辨率 (80~2592)，过滤 Sensor 拍照分辨率
                        if 80 <= w <= 2592 and 60 <= h <= 1944:
                            current_w, current_h = w, h
                            res_set.add((w, h))
                        else:
                            current_w, current_h = 0, 0
                        continue
                    # Interval 必须紧跟有效的 Size 行
                    m_int = re.match(r"\s*Interval:\s*Discrete\s+\S+\s+\((\d+\.?\d*)\s*fps\)", line)
                    if m_int and current_w > 0:
                        fps = round(float(m_int.group(1)))
                        if 1 <= fps <= 120:
                            fps_set.add(fps)
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
            res_set = {(640, 480), (1280, 720)}

        if not fps_set:
            fps_set = {10, 15, 20, 25, 30, 60}

        res_list = sorted(res_set, key=lambda x: x[0] * x[1])
        fps_list = sorted(fps_set)
        return res_list, fps_list

    def apply_video_params(self, width, height, fps):
        """应用新的视频录制参数并重启摄像头"""
        self.width = width
        self.height = height
        self.fps_target = fps
        # 重启摄像头以应用新参数
        self.running = False
        time.sleep(0.3)
        if self.cap and self.cap.isOpened():
            self.cap.release()
        self.cap = cv2.VideoCapture(0)
        self.cap.set(cv2.CAP_PROP_FOURCC, 1196444237)  # MJPG
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        self.running = True
        threading.Thread(target=self._loop, daemon=True).start()

    def _on_gps_data(self, data):
        self.gps_data = data

    def _loop(self):
        while self.running:
            if self.is_playback and self.playback_file:
                self._run_playback()
                continue
                
            if self.cap is None or not self.cap.isOpened():
                time.sleep(0.2)
                continue
            ret, frame = self.cap.read()
            if not ret:
                time.sleep(0.01)
                continue
            
            # 🌟 终极物理排版：去除黑框的 LINE_AA 抗锯齿！用粗实线条彻底干掉灰雾感！
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            font = cv2.FONT_HERSHEY_SIMPLEX
            font_scale = 0.85 # 稍微加大字体一点点，行车记录更威武
            
            thickness_bg = 5 # 极粗纯黑实体外框
            thickness_fg = 2 # 锐利高亮纯白内嵌
            
            (text_w, text_h), _ = cv2.getTextSize(now_str, font, font_scale, thickness_bg)
            x_pos = self.width - text_w - 20
            y_pos = self.height - 20

            # GPS 叠加：仅在串口正常时显示（无硬件则完全不显示）
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
                    # 左上角：卫星 + 速度
                    info_str = f"{gps['satellites']} sat  {gps['speed_kmh']:.0f} km/h"
                    cv2.putText(frame, info_str, (20, 108), font, 0.5, (0, 0, 0), 4, cv2.LINE_8)
                    cv2.putText(frame, info_str, (20, 108), font, 0.5, (0, 255, 255), 1, cv2.LINE_AA)
                else:
                    cv2.putText(frame, "GPS: No Fix", (x_pos, y_pos - 28),
                                font, 0.5, (0, 0, 0), 4, cv2.LINE_8)
                    cv2.putText(frame, "GPS: No Fix", (x_pos, y_pos - 28),
                                font, 0.5, (128, 128, 128), 1, cv2.LINE_AA)

            # 🌟 核心突破：第一步画黑框使用 cv2.LINE_8！绝对不给它生成灰色过度像素的机会！
            cv2.putText(frame, now_str, (x_pos, y_pos), font, font_scale, (0, 0, 0), thickness_bg, cv2.LINE_8)
            # 第二步画白芯使用 cv2.LINE_AA！保留文字本身内部弧度的圆润感！
            cv2.putText(frame, now_str, (x_pos, y_pos), font, font_scale, (255, 255, 255), thickness_fg, cv2.LINE_AA)
            
            if self.is_recording:
                try:
                    self._handle_recording(frame)
                except Exception:
                    # U盘突然拔出等异常，保活录制线程
                    pass
                elapsed = int(time.time() - self.record_start_time)
                cv2.circle(frame, (40, 40), 12, (0, 0, 255), -1)
                cv2.putText(frame, f"REC {elapsed}s", (60, 48), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 0, 255), 2)
            else:
                if self.video_writer is not None:
                    self.stop_recording_file()
                
            cv2.putText(frame, "AI Status: ON (>0.85)", (20, 80), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
            
            current_time = time.time()
            if self.lpr_catcher and not self.lpr_busy and (current_time - self.last_infer_time > 0.4):
                small_frame = cv2.resize(frame, (self.width // 2, self.height // 2))
                threading.Thread(target=self._lpr_task, args=(small_frame,), daemon=True).start()

            osd_text = ""
            if self.plates and (current_time - self.last_detect_time < 2.5):
                parts = []
                for plate_text, plate_conf, plate_box, _ in self.plates:
                    x1, y1, x2, y2 = [v * 2 for v in plate_box]
                    cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 3)
                    parts.append(f"📷 {plate_text} ({plate_conf:.2f})")
                osd_text = "  ".join(parts)
            
            rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            h, w, ch = rgb_frame.shape
            q_img = QImage(rgb_frame.data, w, h, ch * w, QImage.Format_RGB888).copy()
            
            self.frame_ready.emit(q_img, osd_text)
            
            # 每 3 秒检测一次 U 盘热插拔
            self._frame_count += 1
            if self._frame_count % 90 == 0:
                self._auto_select_usb()
            
            time.sleep(1 / self.fps_target)
                
    def _handle_recording(self, frame):
        # 未检测到 U 盘（且无待切换路径）：停止录制
        if not self.storage_path and not self._pending_storage_path:
            self.is_recording = False
            self.stop_recording_file()
            return
        
        now = time.time()
        if self.video_writer is None or (now - self.record_start_time) > 180:
            self.stop_recording_file()
            self._check_and_clean_disk()
            
            # 处理待切换的存储路径：先尝试在新盘创建，失败则回退到旧盘
            target_path = self.storage_path
            if self._pending_storage_path is not None:
                target_path = self._pending_storage_path
            
            # 🌟 再次确保目标文件夹有写入权限
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
            fourcc = cv2.VideoWriter_fourcc(*'mp4v')
            self.video_writer = cv2.VideoWriter(filepath, fourcc, self.fps_target, (self.width, self.height))
            
            # 🌟 核心修复 3：检测视频文件是否真正创建成功！防止静默失败！
            if not self.video_writer.isOpened():
                print(f"❌ 致命错误：无法写入文件 {filepath}！请检查目录写入权限！")
                self.video_writer.release()
                self.video_writer = None
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
                
            self.record_start_time = now
            print(f"🎬 开始录制新分段: {filepath}")

        if self.video_writer:
            try:
                self.video_writer.write(frame)
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

    def stop_recording_file(self):
        if self.video_writer:
            self.video_writer.release()
            self.video_writer = None
            self.current_video_filename = ""
            print("🛑 录制底层资源已安全释放。")

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
            
    def _run_playback(self):
        p_cap = cv2.VideoCapture(self.playback_file)
        total_frames = int(p_cap.get(cv2.CAP_PROP_FRAME_COUNT))
        fps = p_cap.get(cv2.CAP_PROP_FPS) or 30.0
        
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
            if self.new_playback_file and self.new_playback_file != self.playback_file:
                p_cap.release()
                self.playback_file = self.new_playback_file
                self.new_playback_file = None
                p_cap = cv2.VideoCapture(self.playback_file)
                total_frames = int(p_cap.get(cv2.CAP_PROP_FRAME_COUNT))
                fps = p_cap.get(cv2.CAP_PROP_FPS) or 30.0
                matched_logs = load_logs(self.playback_file)
                self.seek_frame = 0 
                continue

            if not p_cap.isOpened():
                break

            if self.seek_frame is not None:
                p_cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, min(self.seek_frame, total_frames - 1)))
                self.seek_frame = None
                
            if self.is_paused:
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
            time.sleep(1 / fps)
            
        p_cap.release()
        self.is_playback = False
        self.is_paused = False
        self.new_playback_file = None

    def stop(self):
        self.running = False
        self.is_recording = False
        self.stop_recording_file()
        self.gps.stop()
        if self.cap: self.cap.release()