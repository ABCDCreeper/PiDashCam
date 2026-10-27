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

    # 启动管理网页服务器（端口 80，需 root 或 setcap 授权）
    web_server = DashcamWebServer(camera_worker=window.worker, port=80)
    web_url = web_server.start()
    # 延迟显示管理地址（等 OSD 系统就绪）
    QTimer.singleShot(2000, lambda: window.show_osd_message(
        f"🌐 管理网页已启动\n{web_url}" if web_url else "❌ 管理网页启动失败，请检查端口权限或占用"
    ))
    app.aboutToQuit.connect(web_server.stop)
    app.aboutToQuit.connect(window.worker.stop)

    sys.exit(app.exec_())
