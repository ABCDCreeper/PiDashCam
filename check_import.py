from PyQt5.QtWidgets import QComboBox
print("QComboBox import OK")
try:
    import ui_mainwindow
    print("ui_mainwindow import OK")
except Exception as e:
    print(f"Import error: {e}")
