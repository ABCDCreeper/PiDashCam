# main.py
import sys
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import QApplication
from ui_mainwindow import DashcamMainWindow
from web_server import DashcamWebServer

if __name__ == "__main__":
    app = QApplication(sys.argv)
    # 隐藏鼠标光标，适配车载触控屏模式
    app.setOverrideCursor(Qt.BlankCursor)
    window = DashcamMainWindow()
    window.showFullScreen()

    # 启动管理网页服务器（端口 8080，零额外依赖）
    web_server = DashcamWebServer(camera_worker=window.worker, port=8080)
    web_url = web_server.start()
    # 延迟显示管理地址（等 OSD 系统就绪）
    QTimer.singleShot(2000, lambda: window.show_osd_message(
        f"🌐 管理网页已启动\n{web_url}"
    ))

    sys.exit(app.exec_())