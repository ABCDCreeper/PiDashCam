# ui_mainwindow.py
import os
import threading
import subprocess
from PyQt5.QtCore import Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QPixmap
from PyQt5.QtWidgets import (QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, 
                             QPushButton, QLabel, QStackedWidget, QListWidget, 
                             QListWidgetItem, QRadioButton, QButtonGroup, 
                             QSizePolicy, QDialog, QSlider, QScroller)

from ui_keyboard import TouchKeyboard
from ui_wifi import WifiScannerDialog
from core_camera import SystemUtils, CameraWorker

class DashcamMainWindow(QMainWindow):
    # 标准跨线程信号，确保后台线程分发的 UI 通知 100% 到达且不崩溃
    osd_signal = pyqtSignal(str)
    net_status_signal = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("行车记录仪 - 车载大触屏增强版")
        # 全面放大车载触摸屏 UI 样式：大按键、高列表、大拖拽球
        self.setStyleSheet("""
            QMainWindow { background-color: #000; }
            QPushButton { 
                background-color: #1a1a1a; color: #fff; border: 2px solid #333; 
                border-radius: 6px; font-size: 18px; font-weight: bold; min-height: 55px; padding: 5px;
            }
            QPushButton:pressed { background-color: #0073e6; }
            QPushButton:checked { background-color: #b03a2e; border-color: #922b21; }
            QLabel { color: #ccc; font-size: 16px; }
            QRadioButton { color: #fff; font-size: 18px; font-weight: bold; padding: 10px; }
            QRadioButton::indicator { width: 25px; height: 25px; }
            
            QListWidget { background-color: #111; color: white; border: 1px solid #333; font-size: 18px; outline: none; }
            QListWidget::item { min-height: 65px; padding-left: 15px; border-bottom: 1px solid #222; }
            QListWidget::item:selected { background-color: #0073e6; color: white; font-weight: bold; }
        """)

        # 绑定跨线程通信槽函数
        self.osd_signal.connect(self.show_osd_message)
        self.net_status_signal.connect(self.update_network_lbl)

        self.worker = CameraWorker()
        self.worker.frame_ready.connect(self.update_monitor)
        self.worker.playback_progress.connect(self.update_playback_ui)
        self.worker.start_camera()

        self.init_ui()
        
        # 延迟1秒自动开启录像 (满足从开机到断电持续录制需求)
        QTimer.singleShot(1000, self.auto_start_recording)
        
        # 启动定时轮询网络状态（每 5 秒查一次 Wi-Fi/热点状态）
        self.net_timer = QTimer(self)
        self.net_timer.timeout.connect(self.poll_network_status)
        self.net_timer.start(5000)
        QTimer.singleShot(500, self.poll_network_status) # 启动时先查一次

    def auto_start_recording(self):
        if not self.btn_record.isChecked():
            self.btn_record.setChecked(True)
            self.toggle_record()
            self.show_osd_message("⚡ 开机自动录像已开启")

    def init_ui(self):
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        main_layout = QVBoxLayout(main_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        self.video_label = QLabel("正在启动高清摄像头与 AI 系统...")
        self.video_label.setAlignment(Qt.AlignCenter)
        self.video_label.setStyleSheet("background-color: #000; font-size: 20px;")
        self.video_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        main_layout.addWidget(self.video_label, stretch=7)
        
        # 画面左侧“网络连接状态栏” (修改定位到左下角)
        self.net_status_lbl = QLabel("🌐 网络: 查询中...", self.video_label)
        self.net_status_lbl.setStyleSheet("color: #00ff66; background-color: rgba(0, 0, 0, 160); font-size: 16px; font-weight: bold; padding: 6px 10px; border-radius: 4px;")
        self.net_status_lbl.adjustSize()
        # 🌟 修改这里：定位到左下角 (距离左边15px，距离底部15px)
        self.net_status_lbl.move(15, self.video_label.height() - self.net_status_lbl.height() - 15)
        self.net_status_lbl.show()

        # 中央系统提示 OSD
        self.osd_msg = QLabel(self.video_label)
        self.osd_msg.setAlignment(Qt.AlignCenter)
        self.osd_msg.setStyleSheet("background-color: rgba(0, 0, 0, 200); color: #39ff14; font-size: 22px; font-weight: bold;")
        self.osd_msg.hide()
        
        # 右上角 AI 车牌识别 OSD
        self.ai_status_lbl = QLabel(self.video_label)
        self.ai_status_lbl.setStyleSheet("color: #00ffff; background-color: rgba(0, 0, 0, 180); font-size: 22px; font-weight: bold; padding: 8px 14px; border: 2px solid #00ffff; border-radius: 6px;")
        self.ai_status_lbl.hide()

        self.stack = QStackedWidget()
        main_layout.addWidget(self.stack, stretch=3)

        self.page_main = self.create_main_page()
        self.stack.addWidget(self.page_main)
        self.page_settings = self.create_settings_page()
        self.stack.addWidget(self.page_settings)
        self.page_playback = self.create_playback_page()
        self.stack.addWidget(self.page_playback)

    def create_main_page(self):
        page = QWidget()
        layout = QHBoxLayout(page)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(10)
        
        self.btn_record = QPushButton("● 开始录像")
        self.btn_record.setCheckable(True)
        self.btn_record.clicked.connect(self.toggle_record)
        self.btn_record.setStyleSheet("background-color: #1b4f72; font-size: 20px;")
        
        btn_settings = QPushButton("⚙️ 系统设置")
        btn_settings.clicked.connect(lambda: self.stack.setCurrentIndex(1))
        
        btn_playback = QPushButton("📂 视频回放")
        btn_playback.clicked.connect(self.open_playback_page)
        
        layout.addWidget(self.btn_record, stretch=4)
        layout.addWidget(btn_settings, stretch=3)
        layout.addWidget(btn_playback, stretch=3)
        return page

    def create_settings_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)
        
        row1 = QHBoxLayout()
        self.storage_layout = QHBoxLayout()
        row1.addLayout(self.storage_layout, stretch=4)
        btn_rescan_storage = QPushButton("🔄 刷新")
        btn_rescan_storage.setFixedWidth(100)
        btn_rescan_storage.clicked.connect(self.refresh_storage_ui)
        row1.addWidget(btn_rescan_storage, stretch=1)
        layout.addLayout(row1)
        
        row2 = QHBoxLayout()
        row2.setSpacing(10)
        btn_ap = QPushButton("📡 开启车载热点")
        btn_ap.clicked.connect(self.start_ap_flow)
        
        btn_client = QPushButton("🌐 连接 Wi-Fi")
        btn_client.clicked.connect(self.start_wifi_connection_flow)
        
        btn_back = QPushButton("⬅ 返回主界面")
        btn_back.clicked.connect(lambda: self.stack.setCurrentIndex(0))
        
        row2.addWidget(btn_ap)
        row2.addWidget(btn_client)
        row2.addWidget(btn_back)
        layout.addLayout(row2)
        
        self.refresh_storage_ui()
        return page

    def create_playback_page(self):
        page = QWidget()
        layout = QHBoxLayout(page)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(10)
        
        ctrl_layout = QVBoxLayout()
        ctrl_layout.setSpacing(6)
        
        slider_box = QHBoxLayout()
        self.lbl_play_time = QLabel("00:00 / 00:00")
        self.lbl_play_time.setStyleSheet("color: #00ffff; font-size: 18px; font-weight: bold; min-width: 120px;")
        
        self.slider_progress = QSlider(Qt.Horizontal)
        # 放大滑块区域，防止误触或拖滑出界
        self.slider_progress.setStyleSheet("""
            QSlider::groove:horizontal { height: 16px; background: #222; border-radius: 8px; }
            QSlider::sub-page:horizontal { background: #0073e6; border-radius: 8px; }
            QSlider::handle:horizontal { width: 34px; height: 34px; margin: -9px 0; background: #fff; border-radius: 17px; border: 2px solid #0073e6; }
        """)
        self.slider_progress.sliderMoved.connect(self.on_slider_moved)
        
        slider_box.addWidget(self.lbl_play_time)
        slider_box.addWidget(self.slider_progress)
        ctrl_layout.addLayout(slider_box)
        
        btn_box = QHBoxLayout()
        btn_box.setSpacing(10)
        self.btn_play_pause = QPushButton("⏸ 暂停")
        self.btn_play_pause.setStyleSheet("background-color: #2c3e50;")
        self.btn_play_pause.clicked.connect(self.toggle_play_pause)
        
        btn_exit = QPushButton("⏹ 退出回放")
        btn_exit.setStyleSheet("background-color: #922b21;")
        btn_exit.clicked.connect(self.stop_playback_and_back)
        
        btn_box.addWidget(self.btn_play_pause)
        btn_box.addWidget(btn_exit)
        ctrl_layout.addLayout(btn_box)
        
        layout.addLayout(ctrl_layout, stretch=6)

        self.list_videos = QListWidget()
        
        self.list_videos.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        QScroller.grabGesture(self.list_videos.viewport(), QScroller.LeftMouseButtonGesture)
        self.list_videos.itemClicked.connect(self.play_selected_video)
        layout.addWidget(self.list_videos, stretch=4)
        return page

    def toggle_record(self):
        # 绝不在 UI 线程里直接调 stop_recording_file，交给后台底层逻辑无冲突完成
        self.worker.is_recording = self.btn_record.isChecked()
        if self.worker.is_recording:
            self.btn_record.setText("■ 录像中 (点此停止)")
            self.btn_record.setStyleSheet("background-color: #922b21; font-size: 20px;")
        else:
            self.btn_record.setText("● 开始录像")
            self.btn_record.setStyleSheet("background-color: #1b4f72; font-size: 20px;")

    def refresh_storage_ui(self):
        while self.storage_layout.count():
            item = self.storage_layout.takeAt(0)
            if item.widget(): item.widget().deleteLater()
            
        self.storage_group = QButtonGroup(self)
        devices = SystemUtils.get_storage_devices()
        
        for i, dev in enumerate(devices):
            btn = QRadioButton(f"{dev['name']} ({dev['free_gb']}G)")
            self.storage_group.addButton(btn, i)
            self.storage_layout.addWidget(btn)
            if dev["path"] == self.worker.storage_path:
                btn.setChecked(True)
                
        if not self.storage_group.checkedButton() and self.storage_layout.count() > 0:
            self.storage_layout.itemAt(0).widget().setChecked(True)
        self.storage_group.buttonClicked.connect(self.change_storage_path)

    def change_storage_path(self, btn):
        idx = self.storage_group.id(btn)
        devices = SystemUtils.get_storage_devices()
        if idx < len(devices):
            new_path = devices[idx]["path"]
            # 🌟 核心修复：不直接给变量赋值，而是调用专职切盘方法！
            self.worker.switch_storage_path(new_path)
            self.show_osd_message(f"✅ 存储路径已实时切换至:\n{new_path}\n当前录像已无缝迁移！")

    def open_playback_page(self):
        self.list_videos.clear()
        path = self.worker.storage_path
        if os.path.exists(path):
            files = sorted([f for f in os.listdir(path) if f.endswith((".mkv", ".mp4"))], reverse=True)
            for f in files:
                self.list_videos.addItem(QListWidgetItem(f"{f}"))
        self.stack.setCurrentIndex(2)

    def play_selected_video(self, item):
        filename = item.text()
        filepath = os.path.join(self.worker.storage_path, filename)
        if os.path.exists(filepath):
            # 无论此前是否在播放中，安全传递新路径！支持中途无缝切换视频
            if self.worker.is_playback:
                self.worker.new_playback_file = filepath
            else:
                self.worker.playback_file = filepath
                self.worker.is_playback = True
                
            self.worker.is_paused = False
            self.btn_play_pause.setText("⏸ 暂停")
            self.btn_play_pause.setStyleSheet("background-color: #2c3e50;")

    def toggle_play_pause(self):
        if not self.worker.is_playback: return
        self.worker.is_paused = not self.worker.is_paused
        if self.worker.is_paused:
            self.btn_play_pause.setText("▶ 继续播放")
            self.btn_play_pause.setStyleSheet("background-color: #27ae60;")
        else:
            self.btn_play_pause.setText("⏸ 暂停")
            self.btn_play_pause.setStyleSheet("background-color: #2c3e50;")

    def on_slider_moved(self, val):
        if self.worker.is_playback:
            self.worker.seek_frame = val

    def update_playback_ui(self, current_frame, total_frames, osd_text):
        if total_frames > 0 and not self.slider_progress.isSliderDown():
            self.slider_progress.setMaximum(total_frames)
            self.slider_progress.setValue(current_frame)
            
            fps = 30
            cur_sec = int(current_frame / fps)
            tot_sec = int(total_frames / fps)
            self.lbl_play_time.setText(f"{cur_sec//60:02d}:{cur_sec%60:02d} / {tot_sec//60:02d}:{tot_sec%60:02d}")

    def stop_playback_and_back(self):
        self.worker.is_playback = False
        self.worker.is_paused = False
        self.worker.new_playback_file = None
        self.stack.setCurrentIndex(0)

    def update_monitor(self, q_img, status_text):
        label_size = self.video_label.size()
        scaled_pixmap = QPixmap.fromImage(q_img).scaled(label_size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.video_label.setPixmap(scaled_pixmap)
        
        # 实时定位车牌 OSD 到底部/右上角
        if status_text:
            self.ai_status_lbl.setText(status_text)
            self.ai_status_lbl.adjustSize()
            x = self.video_label.width() - self.ai_status_lbl.width() - 15
            self.ai_status_lbl.move(max(0, x), 15)
            self.ai_status_lbl.show()
        else:
            self.ai_status_lbl.hide()
            
        # 确保网络状态栏永远浮动在左上角
        y = self.video_label.height() - self.net_status_lbl.height() - 15
        self.net_status_lbl.move(15, max(0, y))
        
    def show_osd_message(self, msg):
        self.osd_msg.setText(msg)
        self.osd_msg.resize(self.video_label.width(), self.video_label.height())
        self.osd_msg.show()
        QTimer.singleShot(3000, self.osd_msg.hide)
        
    def update_network_lbl(self, text):
        self.net_status_lbl.setText(text)
        self.net_status_lbl.adjustSize()

    def poll_network_status(self):
        """后台轮询 NetworkManager 当前活动连接"""
        threading.Thread(target=self._bg_poll_network, daemon=True).start()

    def _bg_poll_network(self):
        try:
            cmd = ["sudo", "nmcli", "-t", "-f", "TYPE,NAME", "connection", "show", "--active"]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
            lines = res.stdout.strip().split('\n')
            
            status_str = "⚠️ 网络未连 (离线)"
            for line in lines:
                if "802-11-wireless" in line or "wifi" in line:
                    parts = line.split(':')
                    if len(parts) >= 2:
                        name = parts[1]
                        if name == "Pi-Dashcam" or "Hotspot" in name:
                            status_str = f"📡 热点开启中: {name}"
                        else:
                            status_str = f"🌐 Wi-Fi 已连: {name}"
                        break
            self.net_status_signal.emit(status_str)
        except Exception:
            pass

    def start_ap_flow(self):
        self.show_osd_message("正在开启车载热点\nPi-Dashcam...")
        threading.Thread(target=self._bg_start_ap, daemon=True).start()

    def _bg_start_ap(self):
        try:
            # 1. 尝试直接拉起已有的 Pi-Dashcam 热点配置
            up_cmd = ["sudo", "nmcli", "connection", "up", "Pi-Dashcam"]
            res = subprocess.run(up_cmd, capture_output=True, text=True, timeout=10)
            
            if res.returncode != 0:
                # 2. 如果没有建立过，则从头创建并启动热点
                cmd = ["sudo", "nmcli", "device", "wifi", "hotspot", "ifname", "wlan0", "ssid", "Pi-Dashcam", "password", "12345678"]
                res = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
                
            if res.returncode == 0:
                msg = "✅ 热点开启成功!\nWi-Fi: Pi-Dashcam\n密码: 12345678"
            else:
                msg = f"❌ 开启热点失败:\n{res.stderr.strip()[:40]}"
        except Exception:
            msg = f"❌ 热点执行超时或异常"
            
        # 标准跨线程信号发送，保证 100% 触发 UI 弹窗
        self.osd_signal.emit(msg)
        self.poll_network_status()

    # 🌟 新增：读取和保存 Wi-Fi 密码的辅助方法
    def load_saved_wifi(self):
        path = os.path.expanduser("~/Videos/wifi_passwords.json")
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def save_wifi_password(self, ssid, password):
        path = os.path.expanduser("~/Videos/wifi_passwords.json")
        saved = self.load_saved_wifi()
        saved[ssid] = password
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(saved, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    # 🌟 升级：支持检查是否已记住了密码
    def start_wifi_connection_flow(self):
        scanner = WifiScannerDialog(self)
        if scanner.exec_() == QDialog.Accepted and scanner.selected_ssid:
            target_ssid = scanner.selected_ssid
            
            # 检查记忆库
            saved_wifis = self.load_saved_wifi()
            if target_ssid in saved_wifis:
                # 记住了！直接用保存的密码秒连，不用再弹键盘！
                password = saved_wifis[target_ssid]
                self.show_osd_message(f"⚡ 使用已记住密码连接:\n{target_ssid}...")
                threading.Thread(target=self._bg_connect_wifi, args=(target_ssid, password, False), daemon=True).start()
            else:
                # 没记住，弹触控键盘输密码
                kb = TouchKeyboard(self, title=f"请输入 Wi-Fi 密码: {target_ssid}")
                if kb.exec_() == QDialog.Accepted:
                    password = kb.get_password()
                    self.show_osd_message(f"正在尝试连接:\n{target_ssid}...")
                    # 传入 True，连接成功后自动记忆！
                    threading.Thread(target=self._bg_connect_wifi, args=(target_ssid, password, True), daemon=True).start()

    # 🌟 升级：解决从热点切换到 Wi-Fi 失败的硬件竞争问题
    def _bg_connect_wifi(self, ssid, password, need_save=False):
        try:
            # 1. 核心修复：单网卡从 AP 切 Client，必须彻底杀掉热点并断开设备！
            subprocess.run(["sudo", "nmcli", "connection", "down", "Pi-Dashcam"], capture_output=True)
            subprocess.run(["sudo", "nmcli", "device", "disconnect", "wlan0"], capture_output=True)
            # 2. 关键留白：强制休眠 2 秒！留给 Linux 内核把网卡从 AP 广播模式切换回监听模式
            time.sleep(2.0)
            
            # 3. 重新扫描唤醒网卡
            subprocess.run(["sudo", "nmcli", "device", "wifi", "rescan"], capture_output=True)
            time.sleep(1.0)
            
            # 4. 发起连接
            cmd = ["sudo", "nmcli", "device", "wifi", "connect", ssid, "password", password]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
            if res.returncode == 0:
                msg = f"✅ 成功连接至 Wi-Fi:\n{ssid}"
                if need_save:
                    self.save_wifi_password(ssid, password) # 成功才记住密码
            else:
                msg = f"❌ 连接失败:\n请检查密码或信号强度"
        except Exception:
            msg = f"❌ Wi-Fi 连接超时"
            
        self.osd_signal.emit(msg)
        self.poll_network_status() # 成功后同步更新左下角网络状态