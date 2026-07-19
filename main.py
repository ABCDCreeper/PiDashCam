# main.py
import sys
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QApplication
from ui_mainwindow import DashcamMainWindow

if __name__ == "__main__":
    app = QApplication(sys.argv)
    # 隐藏鼠标光标，适配车载触控屏模式
    app.setOverrideCursor(Qt.BlankCursor)
    window = DashcamMainWindow()
    window.showFullScreen()
    sys.exit(app.exec_())