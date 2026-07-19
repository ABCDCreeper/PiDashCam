# touch_widgets.py
"""车载触屏专用控件：支持点击跳转的滑块 + 区分滑动/点击的列表"""
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QSlider, QStyle, QStyleOptionSlider, QListWidget,
    QAbstractItemView
)


class TapSlider(QSlider):
    """支持触屏点击跳转到任意位置的进度条。
    
    点击滑槽（groove）任意位置 → 立刻跳转到对应帧；
    点击/拖拽手柄（handle）→ 正常拖拽行为。
    """

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            opt = QStyleOptionSlider()
            self.initStyleOption(opt)
            style = self.style()

            groove_rect = style.subControlRect(
                QStyle.CC_Slider, opt, QStyle.SC_SliderGroove, self
            )
            handle_rect = style.subControlRect(
                QStyle.CC_Slider, opt, QStyle.SC_SliderHandle, self
            )

            # 只有点击滑槽（非手柄）时才跳转
            if groove_rect.contains(event.pos()) and not handle_rect.contains(event.pos()):
                val = QStyle.sliderValueFromPosition(
                    self.minimum(), self.maximum(),
                    event.x() - groove_rect.x(),
                    groove_rect.width(),
                    opt.upsideDown,
                )
                self.setValue(val)
                self.sliderMoved.emit(val)
                return  # 不调 super，由我们接管

        super().mousePressEvent(event)


class TouchScrollListWidget(QListWidget):
    """触屏友好列表：自动区分「滑动」和「点击」。

    - 手指按住后移动超过 10px → 视为滑动，滚动列表，不触发 itemClicked。
    - 手指按住后未明显移动 → 视为点击，正常触发 itemClicked。
    """

    def __init__(self, parent=None, scroll_sensitivity=0.4):
        super().__init__(parent)
        self._press_y = 0
        self._press_value = 0
        self._is_scrolling = False
        self._scroll_sensitivity = scroll_sensitivity  # <1.0 = 手指滑更远才滚动同样距离

        # 关闭原生滚动条（触屏不需要）
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)

    def mousePressEvent(self, event):
        self._press_y = event.y()
        self._press_value = self.verticalScrollBar().value()
        self._is_scrolling = False
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if not self._is_scrolling:
            dy = abs(event.y() - self._press_y)
            if dy > 10:  # 移动超过 10px 才判定为滑动
                self._is_scrolling = True

        if self._is_scrolling:
            delta = (self._press_y - event.y()) * self._scroll_sensitivity
            self.verticalScrollBar().setValue(self._press_value + int(delta))
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._is_scrolling:
            self._is_scrolling = False
            # 滑动结束：吃掉事件，不触发 itemClicked
            return
        super().mouseReleaseEvent(event)
