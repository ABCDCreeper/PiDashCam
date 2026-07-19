# ui_keyboard.py
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QLabel, QLineEdit, 
                             QGridLayout, QPushButton, QSizePolicy)

class TouchKeyboard(QDialog):
    def __init__(self, parent=None, title="请输入 Wi-Fi 密码"):
        super().__init__(parent)
        self.setWindowTitle("车载触控键盘")
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Window)
        self.setStyleSheet("background-color: #050505; color: white; font-size: 16px;")
        
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(2, 2, 2, 2)
        self.layout.setSpacing(2)
        
        self.lbl_title = QLabel(title)
        self.lbl_title.setAlignment(Qt.AlignCenter)
        self.lbl_title.setStyleSheet("color: #00aaff; font-weight: bold;")
        self.layout.addWidget(self.lbl_title)
        
        self.input_box = QLineEdit()
        self.input_box.setStyleSheet("background-color: #222; border: 1px solid #00aaff; padding: 2px; font-size: 20px;")
        self.input_box.setReadOnly(True) 
        self.layout.addWidget(self.input_box)
        
        self.grid = QGridLayout()
        self.grid.setSpacing(1) # 极小按钮间距
        self.layout.addLayout(self.grid)
        
        keys = [
            ['1','2','3','4','5','6','7','8','9','0'],
            ['q','w','e','r','t','y','u','i','o','p'],
            ['a','s','d','f','g','h','j','k','l'],
            ['z','x','c','v','b','n','m','-','_'],
            ['清空', '@', '空格', '退格', '确定', '取消']
        ]
        
        for row, key_row in enumerate(keys):
            for col, key in enumerate(key_row):
                btn = QPushButton(key)
                btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
                btn.setMinimumHeight(40) # 为 480p 屏幕极限压扁
                if key == '确定':
                    btn.setStyleSheet("background-color: #0088cc; font-weight: bold; border-radius: 2px;")
                elif key == '取消':
                    btn.setStyleSheet("background-color: #a93226; border-radius: 2px;")
                else:
                    btn.setStyleSheet("background-color: #2c3e50; border: 1px solid #1c2833; border-radius: 2px;")
                
                btn.clicked.connect(lambda checked, k=key: self.key_pressed(k))
                self.grid.addWidget(btn, row, col)
                
        self.showFullScreen()
                
    def key_pressed(self, key):
        current_text = self.input_box.text()
        if key == '确定':
            self.accept()
        elif key == '取消':
            self.reject()
        elif key == '退格':
            self.input_box.setText(current_text[:-1])
        elif key == '清空':
            self.input_box.clear()
        elif key == '空格':
            self.input_box.setText(current_text + ' ')
        else:
            self.input_box.setText(current_text + key)
            
    def get_password(self):
        return self.input_box.text()