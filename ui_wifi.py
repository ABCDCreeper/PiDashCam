# ui_wifi.py
import time
import threading
import subprocess
from PyQt5.QtCore import Qt, QTimer, pyqtSignal, QObject
from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QLabel,
                             QHBoxLayout, QPushButton, QListWidgetItem)

from touch_widgets import TouchScrollListWidget

class WifiWorker(QObject):
    scan_finished = pyqtSignal(list)
    scan_error = pyqtSignal(str)
    
    def do_scan(self):
        try:
            subprocess.run(["sudo","nmcli","radio","wifi","on"])
            time.sleep(1)
            cmd = ["sudo", "nmcli", "-t", "-f", "SSID,SIGNAL,BSSID", "dev", "wifi", "list", "--rescan", "yes"]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=12)
            networks = []
            for line in res.stdout.split('\n'):
                parts = line.split(':')
                if len(parts) >= 3:
                    ssid, signal, bssid = parts[0], parts[1], parts[2]
                    if ssid.strip() and not ssid.startswith('--') and ssid not in [n['ssid'] for n in networks]:
                        networks.append({'ssid': ssid, 'signal': signal, 'bssid': bssid.strip()})
            self.scan_finished.emit(networks)
        except Exception as e:
            self.scan_error.emit(str(e))

class WifiScannerDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Window)
        
        # 🌟 优化问题 3：全面放大了 Wi-Fi 连接窗口！保证手指随手点哪都能选中！
        self.setStyleSheet("""
            QDialog { background-color: #111; color: white; }
            QLabel { font-size: 20px; color: #00aaff; font-weight: bold; padding: 5px; }
            QPushButton { 
                background-color: #2d2d2d; color: white; font-size: 18px; font-weight: bold; 
                border-radius: 6px; border: 2px solid #444; min-height: 55px;
            }
            QPushButton:pressed { background-color: #0073e6; }
            
            QListWidget { background-color: #0a0a0a; color: white; border: 1px solid #333; font-size: 18px; outline: none; }
            QListWidget::item { min-height: 65px; padding-left: 15px; border-bottom: 1px solid #222; }
            QListWidget::item:selected { background-color: #0073e6; font-weight: bold; }
        """)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)
        
        self.lbl_status = QLabel("正在深度扫描周边 Wi-Fi 信号...")
        layout.addWidget(self.lbl_status)
        
        self.list_widget = TouchScrollListWidget()
        layout.addWidget(self.list_widget)
        
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(15)
        
        self.btn_rescan = QPushButton("🔄 重新扫描")
        self.btn_rescan.clicked.connect(self.scan_networks)
        
        self.btn_cancel = QPushButton("❌ 取消返回")
        self.btn_cancel.setStyleSheet("background-color: #7b241c; border-color: #922b21;")
        self.btn_cancel.clicked.connect(self.reject)
        
        btn_layout.addWidget(self.btn_rescan)
        btn_layout.addWidget(self.btn_cancel)
        layout.addLayout(btn_layout)
        
        self.list_widget.itemClicked.connect(self.on_network_selected)
        
        self.worker = WifiWorker()
        self.worker.scan_finished.connect(self._update_list)
        self.worker.scan_error.connect(self._show_error)
        
        self.selected_ssid = None
        self.selected_bssid = None
        self.showFullScreen()
        QTimer.singleShot(100, self.scan_networks)

    def scan_networks(self):
        self.list_widget.clear()
        self.lbl_status.setText("正在扫描中，请稍候...")
        self.btn_rescan.setEnabled(False)
        threading.Thread(target=self.worker.do_scan, daemon=True).start()

    def _update_list(self, networks):
        self.list_widget.clear()
        if not networks:
            self.lbl_status.setText("⚠️ 未找到信号 (尝试点重新扫描)")
        else:
            self.lbl_status.setText("📶 请按压选择要连接的 Wi-Fi：")
            for net in networks:
                item = QListWidgetItem(f"   📶  {net['ssid']}   (信号强度: {net['signal']}%)")
                item.setData(Qt.UserRole, {'ssid': net['ssid'], 'bssid': net.get('bssid', '')})
                self.list_widget.addItem(item)
        self.btn_rescan.setEnabled(True)
        
    def _show_error(self, err_msg):
        self.lbl_status.setText(f"❌ 扫描遇到阻碍: {err_msg}")
        self.btn_rescan.setEnabled(True)

    def on_network_selected(self, item):
        data = item.data(Qt.UserRole)
        self.selected_ssid = data['ssid']
        self.selected_bssid = data.get('bssid', '')
        self.accept()