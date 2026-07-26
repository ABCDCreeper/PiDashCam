# ui_mainwindow.py
import os
import json
import threading
import subprocess
from datetime import datetime
from PyQt5.QtCore import Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QPixmap
from PyQt5.QtWidgets import (QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, 
                             QPushButton, QLabel, QStackedWidget, QListWidget, 
                             QListWidgetItem, QMessageBox,
                             QSizePolicy, QDialog, QProgressBar, QFrame)

from ui_keyboard import TouchKeyboard
from ui_wifi import WifiScannerDialog
from touch_widgets import TapSlider, TouchScrollListWidget
from core_camera import SystemUtils, CameraWorker


class TimeSettingDialog(QDialog):
    """全屏触控系统时间设置对话框（DS3231 RTC）"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("修改系统时间")
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Window)
        self.setStyleSheet("background-color: #080808; color: white; font-size: 15px;")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setSpacing(4)

        title = QLabel("🕐 修改系统时间")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("color: #00aaff; font-size: 20px; font-weight: bold; padding: 2px;")
        layout.addWidget(title)

        # 实时系统时钟（每秒刷新）
        self.lbl_sys_time = QLabel()
        self.lbl_sys_time.setAlignment(Qt.AlignCenter)
        self.lbl_sys_time.setStyleSheet(
            "color: #aaa; font-size: 13px; padding: 1px;"
        )
        layout.addWidget(self.lbl_sys_time)

        now = datetime.now()
        self.year = now.year
        self.month = now.month
        self.day = now.day
        self.hour = now.hour
        self.minute = now.minute

        self.val_labels = {}

        # 第一行：年 月 日
        row_date = QHBoxLayout()
        row_date.setSpacing(3)
        self._add_field(row_date, "年", "year")
        self._add_field(row_date, "月", "month")
        self._add_field(row_date, "日", "day")
        layout.addLayout(row_date)

        # 第二行：时 分
        row_time = QHBoxLayout()
        row_time.setSpacing(3)
        self._add_field(row_time, "时", "hour")
        self._add_field(row_time, "分", "minute")
        layout.addLayout(row_time)

        layout.addStretch()

        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        btn_confirm = QPushButton("💾 保存")
        btn_confirm.setStyleSheet(
            "background-color: #0088cc; font-size: 17px; font-weight: bold; "
            "min-height: 45px; border-radius: 6px;"
        )
        btn_confirm.clicked.connect(self._confirm)
        btn_cancel = QPushButton("❌ 取消")
        btn_cancel.setStyleSheet(
            "background-color: #a93226; font-size: 17px; "
            "min-height: 45px; border-radius: 6px;"
        )
        btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(btn_confirm)
        btn_row.addWidget(btn_cancel)
        layout.addLayout(btn_row)

        # 启动实时时钟刷新
        self._sys_timer = QTimer(self)
        self._sys_timer.timeout.connect(self._update_system_time)
        self._sys_timer.start(1000)
        self._update_system_time()

        self.showFullScreen()

    def _update_system_time(self):
        """每秒刷新实时系统时钟"""
        now = datetime.now()
        self.lbl_sys_time.setText(
            f"⏱ 系统时间: {now.year}-{now.month:02d}-{now.day:02d} "
            f"{now.hour:02d}:{now.minute:02d}:{now.second:02d}"
        )

    def _add_field(self, parent_layout, label, field):
        """紧凑地将一个时间字段控件组添加到父布局中：[◀][值][▶][标签]"""
        btn_down = QPushButton("◀")
        btn_down.setFixedSize(44, 44)
        btn_down.setStyleSheet(
            "background-color: #2c3e50; font-size: 20px; "
            "border-radius: 22px; font-weight: bold;"
        )
        btn_down.clicked.connect(lambda checked, f=field: self._adjust(f, -1))

        val_lbl = QLabel()
        val_lbl.setAlignment(Qt.AlignCenter)
        val_lbl.setStyleSheet(
            "color: #fff; font-size: 20px; font-weight: bold; "
            "min-width: 72px; min-height: 42px; "
            "background-color: #1a1a1a; border: 2px solid #333; border-radius: 6px;"
        )
        self.val_labels[field] = val_lbl
        current = getattr(self, field)
        val_lbl.setText(str(current) if field == "year" else f"{current:02d}")

        btn_up = QPushButton("▶")
        btn_up.setFixedSize(44, 44)
        btn_up.setStyleSheet(
            "background-color: #2c3e50; font-size: 20px; "
            "border-radius: 22px; font-weight: bold;"
        )
        btn_up.clicked.connect(lambda checked, f=field: self._adjust(f, 1))

        lbl = QLabel(label)
        lbl.setStyleSheet("color: #aaa; font-size: 16px; font-weight: bold; min-width: 24px;")
        lbl.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)

        parent_layout.addWidget(btn_down)
        parent_layout.addWidget(val_lbl)
        parent_layout.addWidget(btn_up)
        parent_layout.addWidget(lbl)

    def _adjust(self, field, delta):
        limits = {
            "year": (2000, 2099),
            "month": (1, 12),
            "day": (1, 31),
            "hour": (0, 23),
            "minute": (0, 59),
        }
        current = getattr(self, field)
        lo, hi = limits[field]
        new_val = current + delta
        if new_val < lo or new_val > hi:
            return
        setattr(self, field, new_val)
        if field == "year":
            self.val_labels[field].setText(str(new_val))
        else:
            self.val_labels[field].setText(f"{new_val:02d}")

    def _confirm(self):
        try:
            datetime(self.year, self.month, self.day, self.hour, self.minute)
        except ValueError as e:
            QMessageBox.warning(self, "日期无效", f"请输入合法的日期时间：\n{e}")
            return

        time_str = (
            f"{self.year}-{self.month:02d}-{self.day:02d} "
            f"{self.hour:02d}:{self.minute:02d}:00"
        )

        # 收集所有错误信息
        errors = []

        try:
            # 方案 1：date -s
            res = subprocess.run(
                ["sudo", "-n", "date", "-s", time_str],
                capture_output=True, text=True, timeout=10
            )
            if res.returncode != 0:
                err = res.stderr.strip() or "(无错误输出)"
                # 如果是 sudo 密码问题，尝试 timedatectl
                if "password" in err.lower() or "no tty" in err.lower():
                    # 尝试 timedatectl 作为 fallback
                    res = subprocess.run(
                        ["sudo", "-n", "timedatectl", "set-time", time_str],
                        capture_output=True, text=True, timeout=10
                    )
                    if res.returncode != 0:
                        errors.append(f"date -s: {err}")
                        errors.append(f"timedatectl: {res.stderr.strip()}")
                else:
                    errors.append(f"date -s: {err}")
        except Exception as e:
            errors.append(f"date -s 异常: {e}")

        if not errors:
            # 时间设置成功，验证
            try:
                verify = subprocess.run(
                    ["date", "+%Y-%m-%d %H:%M:%S"],
                    capture_output=True, text=True, timeout=5
                )
                actual = verify.stdout.strip()
                expected = time_str
                if actual != expected:
                    errors.append(f"设置后验证失败：期望 {expected}，实际 {actual}")
            except Exception as e:
                pass  # 验证失败不影响，只记录

        if not errors:
            # 同步到 RTC
            try:
                res2 = subprocess.run(
                    ["sudo", "-n", "hwclock", "--systohc"],
                    capture_output=True, text=True, timeout=10
                )
                if res2.returncode != 0:
                    QMessageBox.warning(self, "部分成功",
                        f"系统时间已设置，但同步 DS3231 失败：\n{res2.stderr.strip()}")
                    self.accept()
                    return
            except Exception as e:
                QMessageBox.warning(self, "部分成功",
                    f"系统时间已设置，但同步 DS3231 时异常：\n{e}")
                self.accept()
                return

            parent = self.parent()
            if parent and hasattr(parent, "show_osd_message"):
                parent.show_osd_message(f"✅ 时间已更新: {time_str}")
            self.accept()
        else:
            QMessageBox.warning(self, "设置失败",
                "无法设置系统时间：\n" + "\n".join(errors))


class RecordingSettingsDialog(QDialog):
    """全屏触控视频录制参数设置对话框（按钮方式，无 Wayland 兼容问题）"""
    BTN_CHECKED = (
        "background-color: #0073e6; font-size: 17px; font-weight: bold; "
        "min-height: 44px; border-radius: 6px; border: 2px solid #00aaff;"
    )
    BTN_NORMAL = (
        "background-color: #1a1a1a; font-size: 17px; "
        "min-height: 44px; border-radius: 6px; border: 2px solid #333;"
    )

    def __init__(self, parent=None, current_w=1280, current_h=720, current_fps=30,
                 res_list=None, fps_list=None):
        super().__init__(parent)
        self.setWindowTitle("视频录制参数")
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Window)
        self.setStyleSheet("background-color: #080808; color: white; font-size: 15px;")

        if res_list is None:
            res_list = [(640, 480), (1280, 720)]
        if fps_list is None:
            fps_list = [15, 20, 25, 30]

        self.selected_w = current_w
        self.selected_h = current_h
        self.selected_fps = current_fps
        self._res_btns = {}
        self._fps_btns = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(6)

        title = QLabel("📹 视频录制参数")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("color: #00aaff; font-size: 20px; font-weight: bold; padding: 2px;")
        layout.addWidget(title)

        self.lbl_current = QLabel()
        self.lbl_current.setAlignment(Qt.AlignCenter)
        self.lbl_current.setStyleSheet("color: #39ff14; font-size: 16px; font-weight: bold; padding: 2px;")
        self._update_current_display()
        layout.addWidget(self.lbl_current)

        # 分辨率按钮（多行，每行最多 5 个）
        res_label = QLabel("分辨率")
        res_label.setStyleSheet("color: #aaa; font-size: 15px; font-weight: bold; padding: 2px;")
        layout.addWidget(res_label)
        res_opts = [(f"{w}×{h}", (w, h)) for w, h in res_list]
        layout.addLayout(self._make_button_grid(res_opts, "res", current_w, current_h))

        # 帧率按钮
        fps_label = QLabel("帧率 (fps)")
        fps_label.setStyleSheet("color: #aaa; font-size: 15px; font-weight: bold; padding: 2px;")
        layout.addWidget(fps_label)
        fps_opts = [(f, f) for f in fps_list]
        layout.addLayout(self._make_button_grid(fps_opts, "fps", current_fps))

        layout.addStretch()

        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        btn_save = QPushButton("💾 保存")
        btn_save.setStyleSheet(
            "background-color: #0088cc; font-size: 17px; font-weight: bold; "
            "min-height: 44px; border-radius: 6px;"
        )
        btn_save.clicked.connect(self.accept)
        btn_cancel = QPushButton("❌ 取消")
        btn_cancel.setStyleSheet(
            "background-color: #a93226; font-size: 17px; "
            "min-height: 44px; border-radius: 6px;"
        )
        btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(btn_save)
        btn_row.addWidget(btn_cancel)
        layout.addLayout(btn_row)

        self.showFullScreen()

    def _make_button_grid(self, options, kind, *current_vals):
        """创建按钮网格：options 是 [(label, value), ...] 列表，每行最多 5 个"""
        rows = []
        row = QHBoxLayout()
        row.setSpacing(4)
        count = 0
        for label, value in options:
            btn = QPushButton(str(label))
            is_current = False
            if kind == "res":
                is_current = (value[0] == current_vals[0] and value[1] == current_vals[1])
                key = (value[0], value[1])
            else:
                is_current = (value == current_vals[0])
                key = value

            btn.setStyleSheet(self.BTN_CHECKED if is_current else self.BTN_NORMAL)
            btn.clicked.connect(lambda checked, k=kind, v=value: self._on_select(k, v))
            row.addWidget(btn, stretch=1)
            count += 1
            if count >= 5:
                rows.append(row)
                row = QHBoxLayout()
                row.setSpacing(4)
                count = 0

            if kind == "res":
                self._res_btns[key] = btn
            else:
                self._fps_btns[key] = btn

        if count > 0:
            rows.append(row)

        container = QVBoxLayout()
        container.setSpacing(3)
        for r in rows:
            container.addLayout(r)
        return container

    def _on_select(self, kind, value):
        if kind == "res":
            self.selected_w, self.selected_h = value
            for key, btn in self._res_btns.items():
                if key == value:
                    btn.setStyleSheet(self.BTN_CHECKED)
                else:
                    btn.setStyleSheet(self.BTN_NORMAL)
        else:
            self.selected_fps = value
            for key, btn in self._fps_btns.items():
                if key == value:
                    btn.setStyleSheet(self.BTN_CHECKED)
                else:
                    btn.setStyleSheet(self.BTN_NORMAL)
        self._update_current_display()

    def _update_current_display(self):
        self.lbl_current.setText(
            f"当前选择: {self.selected_w}×{self.selected_h} · {self.selected_fps}fps"
        )


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
        self.worker.storage_notify.connect(self.on_storage_notify)
        self.worker.storage_path_changed.connect(self.on_storage_path_changed)
        self.worker.recording_toggled.connect(self.on_recording_toggled)
        self.worker.gps.gps_data_updated.connect(self.update_gps_status)
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
        # 没有 U 盘不自动录像
        if not self.worker.storage_path:
            return
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
        main_layout.addWidget(self.video_label, stretch=8)
        
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

        # 全屏“请插入U盘”提示
        self.no_usb_lbl = QLabel("⚠️ 未检测到U盘\n请插入U盘开始录制", self.video_label)
        self.no_usb_lbl.setAlignment(Qt.AlignCenter)
        self.no_usb_lbl.setStyleSheet("background-color: rgba(0, 0, 0, 220); color: #ff4444; font-size: 28px; font-weight: bold;")
        self.no_usb_lbl.hide()

        self.stack = QStackedWidget()
        main_layout.addWidget(self.stack, stretch=2)

        self.page_main = self.create_main_page()
        self.stack.addWidget(self.page_main)
        self.page_settings = self.create_settings_page()
        self.stack.addWidget(self.page_settings)
        self.page_playback = self.create_playback_page()
        self.stack.addWidget(self.page_playback)

        # 初始显示录制参数
        self._update_recording_params()

    def create_main_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(2)
        
        # 录制参数行
        self.recording_params_lbl = QLabel()
        self.recording_params_lbl.setAlignment(Qt.AlignCenter)
        self.recording_params_lbl.setStyleSheet(
            "color: #aaa; font-size: 14px; font-weight: bold; padding: 1px;"
        )
        layout.addWidget(self.recording_params_lbl)
        
        # 按钮行（4 个等大按钮）
        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)
        
        self.btn_record = QPushButton("● 开始录像")
        self.btn_record.setCheckable(True)
        self.btn_record.clicked.connect(self.toggle_record)
        self.btn_record.setStyleSheet("background-color: #1b4f72; font-size: 18px;")
        
        btn_settings = QPushButton("⚙️ 系统设置")
        btn_settings.clicked.connect(lambda: self.stack.setCurrentIndex(1))
        
        btn_playback = QPushButton("📂 视频回放")
        btn_playback.clicked.connect(self.open_playback_page)
        
        btn_video_settings = QPushButton("🎬 录制参数")
        btn_video_settings.clicked.connect(self.open_video_settings)
        
        btn_row.addWidget(self.btn_record, stretch=3)
        btn_row.addWidget(btn_settings, stretch=2)
        btn_row.addWidget(btn_playback, stretch=2)
        btn_row.addWidget(btn_video_settings, stretch=2)
        layout.addLayout(btn_row)
        return page

    def create_settings_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(8)
        
        # 统一状态面板（U盘 + GPS 集成在一个容器内）
        status_frame = QFrame()
        status_frame.setStyleSheet("QFrame { background-color: #111; border: 2px solid #333; border-radius: 8px; }")
        status_layout = QVBoxLayout(status_frame)
        status_layout.setContentsMargins(10, 8, 10, 8)
        status_layout.setSpacing(4)
        
        # 标题行：U盘标签（左）+ GPS 状态（右）
        header_row = QHBoxLayout()
        header_row.setSpacing(10)
        
        self.storage_label = QLabel("U盘状态: 检测中...")
        self.storage_label.setStyleSheet("color: #00aaff; font-size: 18px; font-weight: bold; padding: 5px; border: none;")
        header_row.addWidget(self.storage_label)
        
        header_row.addStretch()
        
        self.gps_status_lbl = QLabel("🛰 GPS: 检测中...")
        self.gps_status_lbl.setStyleSheet("color: #888; font-size: 16px; padding: 5px; border: none;")
        header_row.addWidget(self.gps_status_lbl)
        
        status_layout.addLayout(header_row)
        
        self.storage_bar = QProgressBar()
        self.storage_bar.setTextVisible(True)
        self.storage_bar.setMinimumHeight(40)
        self.storage_bar.setStyleSheet("""
            QProgressBar { background-color: #222; border: 2px solid #444; border-radius: 6px; text-align: center; color: white; font-size: 16px; font-weight: bold; }
            QProgressBar::chunk { background-color: #0073e6; border-radius: 4px; }
        """)
        status_layout.addWidget(self.storage_bar)
        
        self.storage_detail = QLabel("")
        self.storage_detail.setStyleSheet("color: #aaa; font-size: 14px; padding: 2px; border: none;")
        status_layout.addWidget(self.storage_detail)
        
        layout.addWidget(status_frame)

        # 控制栏：返回（左）+ 热点、WiFi、修改时间
        row1 = QHBoxLayout()
        row1.setSpacing(10)
        
        btn_back = QPushButton("⬅")
        btn_back.setFixedWidth(70)
        btn_back.setStyleSheet("font-size: 22px;")
        btn_back.clicked.connect(lambda: self.stack.setCurrentIndex(0))
        
        btn_ap = QPushButton("📡 开启车载热点")
        btn_ap.clicked.connect(self.start_ap_flow)
        
        btn_client = QPushButton("🌐 连接 Wi-Fi")
        btn_client.clicked.connect(self.start_wifi_connection_flow)
        
        btn_time = QPushButton("🕐 修改系统时间")
        btn_time.clicked.connect(self.open_time_setting)
        
        row1.addWidget(btn_back)
        row1.addWidget(btn_ap)
        row1.addWidget(btn_client)
        row1.addWidget(btn_time)
        layout.addLayout(row1)

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
        
        self.slider_progress = TapSlider(Qt.Horizontal)
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

        self.list_videos = TouchScrollListWidget()
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
        """更新 U 盘空间条形图"""
        devices = SystemUtils.get_storage_devices()
        if not devices:
            self.storage_label.setText("⚠️ 未检测到U盘")
            self.storage_bar.setValue(0)
            self.storage_bar.setFormat("无U盘")
            self.storage_bar.setStyleSheet("""
                QProgressBar { background-color: #222; border: 2px solid #922b21; border-radius: 6px; text-align: center; color: #ff4444; font-size: 16px; font-weight: bold; }
                QProgressBar::chunk { background-color: #922b21; border-radius: 4px; }
            """)
            self.storage_detail.setText("请插入U盘开始录制")
            return
        
        dev = devices[0]  # 只有一个 U 盘
        total_gb = round(dev["free_gb"] / (1 - dev["percent"] / 100), 1) if dev["percent"] > 0 else dev["free_gb"]
        used_gb = round(total_gb - dev["free_gb"], 1)
        pct = int(dev["percent"])
        
        self.storage_label.setText("U盘状态")
        self.storage_bar.setMinimum(0)
        self.storage_bar.setMaximum(100)
        self.storage_bar.setValue(pct)
        self.storage_bar.setFormat(f"已用 {pct}%")
        self.storage_bar.setStyleSheet("""
            QProgressBar { background-color: #222; border: 2px solid #444; border-radius: 6px; text-align: center; color: white; font-size: 16px; font-weight: bold; }
            QProgressBar::chunk { background-color: #0073e6; border-radius: 4px; }
        """)
        self.storage_detail.setText(f"🟦 已用 {used_gb}G  |  🟩 剩余 {dev['free_gb']}G  |  总计 {total_gb}G")

    def change_storage_path(self, btn):
        # 不再需要手动切换，U 盘自动检测
        pass

    def on_storage_notify(self, msg):
        """接收后台线程的存储切换结果通知（成功/失败）"""
        self.show_osd_message(msg)

    def on_storage_path_changed(self, new_path):
        """存储路径变更后刷新 UI radio 按钮"""
        self.refresh_storage_ui()

    def on_recording_toggled(self, recording):
        """后台录制状态变更 → 同步按钮"""
        self.btn_record.setChecked(recording)
        if recording:
            self.btn_record.setText("■ 录像中 (点此停止)")
            self.btn_record.setStyleSheet("background-color: #922b21; font-size: 20px;")
        else:
            self.btn_record.setText("● 开始录像")
            self.btn_record.setStyleSheet("background-color: #1b4f72; font-size: 20px;")
        self._update_recording_params()

    def _update_recording_params(self):
        """更新首页录制参数标签"""
        w, h, fps = self.worker.width, self.worker.height, self.worker.fps_target
        base = f"📹 {w}×{h} · {fps}fps"
        if self.worker.is_recording:
            base += "  ·  ● 录制中"
        self.recording_params_lbl.setText(base)

    def open_video_settings(self):
        """打开视频录制参数设置对话框"""
        res_list, fps_list = self.worker.get_camera_caps()
        dlg = RecordingSettingsDialog(
            self,
            current_w=self.worker.width,
            current_h=self.worker.height,
            current_fps=self.worker.fps_target,
            res_list=res_list,
            fps_list=fps_list,
        )
        if dlg.exec_() == QDialog.Accepted:
            self.worker.apply_video_params(dlg.selected_w, dlg.selected_h, dlg.selected_fps)
            self._update_recording_params()
            self.show_osd_message(
                f"✅ 视频参数已更新: {dlg.selected_w}×{dlg.selected_h} @ {dlg.selected_fps}fps"
            )

    def open_time_setting(self):
        dlg = TimeSettingDialog(self)
        dlg.exec_()

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
        
        # 全屏“请插入U盘”提示
        if not self.worker.storage_path:
            self.no_usb_lbl.resize(label_size)
            self.no_usb_lbl.show()
        else:
            self.no_usb_lbl.hide()
            
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

    def update_gps_status(self, data):
        if not hasattr(self, "gps_status_lbl"):
            return
        err = data.get("serial_error")
        if err:
            self.gps_status_lbl.setText(f"🛰 GPS: 未连接 ({err})")
            self.gps_status_lbl.setStyleSheet("color: #ff4444; font-size: 16px; padding: 5px;")
        elif data.get("has_fix"):
            self.gps_status_lbl.setText(
                f"🛰 GPS: {data['satellites']}颗卫星  |  {data['speed_kmh']:.0f} km/h  |  "
                f"{data['latitude']:.5f}°  {data['longitude']:.5f}°"
            )
            self.gps_status_lbl.setStyleSheet("color: #00ff66; font-size: 16px; padding: 5px;")
        else:
            self.gps_status_lbl.setText("🛰 GPS: 等待定位...")
            self.gps_status_lbl.setStyleSheet("color: #ffaa00; font-size: 16px; padding: 5px;")

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
            
            # 获取 wlan0 的 IPv4 地址
            if "🌐" in status_str or "📡" in status_str:
                try:
                    ip_cmd = ["sudo", "nmcli", "-t", "-f", "IP4.ADDRESS", "device", "show", "wlan0"]
                    ip_res = subprocess.run(ip_cmd, capture_output=True, text=True, timeout=5)
                    for ip_line in ip_res.stdout.strip().split('\n'):
                        if ':' in ip_line:
                            addr = ip_line.split(':', 1)[1].split('/')[0]
                            if addr:
                                status_str += f"  |  IP: {addr}"
                                break
                except Exception:
                    pass
            
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

    # 🌟 新增：读取和保存 Wi-Fi 密码的辅助方法（优先 U 盘）
    def load_saved_wifi(self):
        # 优先从 U 盘读取
        if self.worker.storage_path:
            path = os.path.join(self.worker.storage_path, "wifi_passwords.json")
            if os.path.exists(path):
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        return json.load(f)
                except Exception:
                    pass
        # 回退到系统盘
        path = os.path.expanduser("~/Videos/wifi_passwords.json")
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def save_wifi_password(self, ssid, password):
        # 保存到 U 盘
        if self.worker.storage_path:
            path = os.path.join(self.worker.storage_path, "wifi_passwords.json")
        else:
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
        if scanner.exec_() == QDialog.Accepted and scanner.selected_bssid:
            target_ssid = scanner.selected_ssid
            target_bssid = scanner.selected_bssid
            
            # 检查记忆库
            saved_wifis = self.load_saved_wifi()
            if target_ssid in saved_wifis:
                # 记住了！直接用保存的密码秒连，不用再弹键盘！
                password = saved_wifis[target_ssid]
                self.show_osd_message(f"⚡ 使用已记住密码连接:\n{target_ssid}...")
                threading.Thread(target=self._bg_connect_wifi, args=(target_ssid, password, False, target_bssid), daemon=True).start()
            else:
                # 没记住，弹触控键盘输密码
                kb = TouchKeyboard(self, title=f"请输入 Wi-Fi 密码: {target_ssid}")
                if kb.exec_() == QDialog.Accepted:
                    password = kb.get_password()
                    self.show_osd_message(f"正在尝试连接:\n{target_ssid}...")
                    # 传入 True，连接成功后自动记忆！
                    threading.Thread(target=self._bg_connect_wifi, args=(target_ssid, password, True, target_bssid), daemon=True).start()

    # 🌟 升级：解决从热点切换到 Wi-Fi 失败的硬件竞争问题
    def _bg_connect_wifi(self, ssid, password, need_save=False, bssid=""):
        try:
            # 1. 核心修复：单网卡从 AP 切 Client，必须彻底杀掉热点并断开设备！
            subprocess.run(["sudo", "nmcli", "connection", "down", "Pi-Dashcam"], capture_output=True)
            subprocess.run(["sudo", "nmcli", "device", "disconnect", "wlan0"], capture_output=True)
            # 2. 关键留白：强制休眠 2 秒！留给 Linux 内核把网卡从 AP 广播模式切换回监听模式
            time.sleep(2.0)
            
            # 3. 反复扫描直到目标 SSID 出现（最多 3 次，每次间隔 2 秒）
            ssid_visible = False
            for attempt in range(3):
                subprocess.run(
                    ["sudo", "nmcli", "device", "wifi", "list", "--rescan", "yes"],
                    capture_output=True, timeout=15
                )
                # 检查目标 SSID 是否在扫描结果中
                check_fields = "SSID,BSSID" if bssid else "SSID"
                check = subprocess.run(
                    ["sudo", "nmcli", "-t", "-f", check_fields, "device", "wifi", "list"],
                    capture_output=True, text=True, timeout=10
                )
                target_match = bssid if bssid else ssid
                if target_match in check.stdout:
                    ssid_visible = True
                    break
                time.sleep(2.0)
            if not ssid_visible:
                raise Exception(f"未扫描到 {bssid or ssid}")
            
            # 4. 发起连接
            target = bssid if bssid else ssid
            cmd = ["sudo", "nmcli", "device", "wifi", "connect", target, "password", password]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if res.returncode == 0:
                msg = f"✅ 成功连接至 Wi-Fi:\n{ssid}"
                if need_save:
                    self.save_wifi_password(ssid, password) # 成功才记住密码
            else:
                err = res.stderr.strip()[-80:] if res.stderr else "无详细信息"
                msg = f"❌ 连接失败:\n{err}"
        except Exception as e:
            msg = f"❌ Wi-Fi 连接超时: {e}"
            
        self.osd_signal.emit(msg)
        self.poll_network_status() # 成功后同步更新左下角网络状态