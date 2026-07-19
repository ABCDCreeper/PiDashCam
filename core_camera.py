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

class SystemUtils:
    @staticmethod
    def get_storage_devices():
        devices = []
        internal_path = os.path.expanduser("~/Videos")
        os.makedirs(internal_path, exist_ok=True)
        root_usage = psutil.disk_usage(internal_path)
        devices.append({
            "name": "💽 内录", 
            "path": internal_path, 
            "free_gb": round(root_usage.free / (1024**3), 1), 
            "percent": root_usage.percent, 
            "is_usb": False
        })
        
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
                        
                    # 🌟 核心修改：使用 sudo mkdir -p 创建目录，彻底解决 [Errno 13] 权限问题！
                    if not mountpoint:
                        target_mount = f"/mnt/usb_{dev_name}"
                        subprocess.run(["sudo", "mkdir", "-p", target_mount], capture_output=True)
                        m_res = subprocess.run(["sudo", "mount", f"/dev/{dev_name}", target_mount], capture_output=True, text=True)
                        if m_res.returncode == 0:
                            mountpoint = target_mount
                            # 🌟 核心修复 1：挂载成功后，立刻使用 chmod 777 开放读写权限！解决普通用户写不进去的问题！
                            subprocess.run(["sudo", "chmod", "-R", "777", target_mount], capture_output=True)
                            print(f"🔌 暴力挂载且授权成功: /dev/{dev_name} -> {mountpoint}")
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

class CameraWorker(QObject):
    frame_ready = pyqtSignal(QImage, str) 
    playback_progress = pyqtSignal(int, int, str)

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
        self.storage_path = os.path.expanduser("~/Videos")
        self.fps_target = 30
        self.width = 1280
        self.height = 720
        self.video_writer = None
        self.record_start_time = 0
        self.current_video_filename = ""
        
        self.lpr_catcher = None
        self.lpr_busy = False
        self.plate_text = ""
        self.plate_box = None
        self.plate_conf = 0.0
        self.last_detect_time = 0
        self.last_infer_time = 0
        self.last_log_time = 0
        
        threading.Thread(target=self._load_lpr_model, daemon=True).start()

    def switch_storage_path(self, new_path):
        if self.storage_path != new_path:
            self.storage_path = new_path
            if self.is_recording and self.video_writer:
                print(f"🔄 切换存储路径至: {new_path}，立刻触发录像分段...")
                self.stop_recording_file() # 关掉老路径的文件，下个循环会自动在 new_path 创建新文件

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
                    best_plate = results[0]
                    conf = float(best_plate[1])
                    if conf > 0.85:
                        self.plate_text = best_plate[0]
                        self.plate_conf = conf
                        self.plate_box = [int(v) for v in best_plate[3]]
                        self.last_detect_time = time.time()
                        self._log_plate_to_json(self.plate_text, conf)
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
            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        self.running = True
        threading.Thread(target=self._loop, daemon=True).start()

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
            
            # 🌟 核心突破：第一步画黑框使用 cv2.LINE_8！绝对不给它生成灰色过度像素的机会！
            cv2.putText(frame, now_str, (x_pos, y_pos), font, font_scale, (0, 0, 0), thickness_bg, cv2.LINE_8)
            # 第二步画白芯使用 cv2.LINE_AA！保留文字本身内部弧度的圆润感！
            cv2.putText(frame, now_str, (x_pos, y_pos), font, font_scale, (255, 255, 255), thickness_fg, cv2.LINE_AA)
            
            if self.is_recording:
                self._handle_recording(frame)
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
            if self.plate_box and (current_time - self.last_detect_time < 2.5):
                x1, y1, x2, y2 = [v * 2 for v in self.plate_box]
                cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 3)
                osd_text = f"📷 {self.plate_text} ({self.plate_conf:.2f})"
            
            rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            h, w, ch = rgb_frame.shape
            q_img = QImage(rgb_frame.data, w, h, ch * w, QImage.Format_RGB888).copy()
            
            self.frame_ready.emit(q_img, osd_text)
            time.sleep(1 / self.fps_target)
                
    def _handle_recording(self, frame):
        now = time.time()
        if self.video_writer is None or (now - self.record_start_time) > 180:
            self.stop_recording_file()
            self._check_and_clean_disk()
            
            # 🌟 再次确保目标文件夹有写入权限
            try:
                os.makedirs(self.storage_path, exist_ok=True)
            except Exception as e:
                print(f"❌ 无法创建目录 {self.storage_path}: {e}")
                return
            
            self.current_video_filename = datetime.now().strftime("REC_%Y%m%d_%H%M%S.mkv")
            filepath = os.path.join(self.storage_path, self.current_video_filename)
            fourcc = cv2.VideoWriter_fourcc(*'mp4v')
            self.video_writer = cv2.VideoWriter(filepath, fourcc, self.fps_target, (self.width, self.height))
            
            # 🌟 核心修复 3：检测视频文件是否真正创建成功！防止静默失败！
            if not self.video_writer.isOpened():
                print(f"❌ 致命错误：无法写入文件 {filepath}！请检查目录写入权限！")
                self.video_writer.release()
                self.video_writer = None
                return
                
            self.record_start_time = now
            print(f"🎬 开始录制新分段: {filepath}")

        if self.video_writer:
            self.video_writer.write(frame)

    def stop_recording_file(self):
        if self.video_writer:
            self.video_writer.release()
            self.video_writer = None
            self.current_video_filename = ""
            print("🛑 录制底层资源已安全释放。")
            
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
        if self.cap: self.cap.release()