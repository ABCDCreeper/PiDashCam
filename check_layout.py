"""800x480 page-navigation regression; no camera or system mutation."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from types import SimpleNamespace
import unittest
from PyQt5.QtWidgets import QApplication, QMainWindow, QPushButton
from ui_mainwindow import DashcamMainWindow


class LayoutChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_return_from_settings_keeps_controls_inside_screen(self):
        window = DashcamMainWindow.__new__(DashcamMainWindow)
        QMainWindow.__init__(window)
        window.setStyleSheet('QPushButton {min-height:55px; font-size:18px; padding:5px;}')
        window.worker = SimpleNamespace(storage_devices=[], storage_path='', _pending_storage_path=None,
                                        width=1920, height=1080, fps_target=30, is_recording=False)
        window.init_ui()
        window.resize(800, 480)
        window.show()
        self.addCleanup(window.close)
        window.update_gps_status({'serial_error': 'Serial port unavailable: ' + '/dev/serial0 ' * 40})
        for index in (1, 0, 1, 0):
            window.stack.setCurrentIndex(index)
            self.app.processEvents()
        self.assertEqual((window.width(), window.height()), (800, 480))
        self.assertGreater(window.video_label.height(), 300)
        buttons=window.page_main.findChildren(QPushButton)
        self.assertEqual(len(buttons), 4)
        for button in buttons:
            self.assertTrue(window.page_main.rect().contains(button.geometry()), button.text())


if __name__ == '__main__':
    unittest.main(verbosity=2)
