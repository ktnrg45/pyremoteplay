# pylint: disable=c-extension-no-member,invalid-name
"""Joystick Widget for stream window."""
from __future__ import annotations
from enum import Enum
from typing import TYPE_CHECKING
from PySide6 import QtWidgets, QtGui, QtCore
from PySide6.QtCore import Qt  # pylint: disable=no-name-in-module

if TYPE_CHECKING:
    from .stream_window import StreamWindow


class JoystickWidget(QtWidgets.QFrame):
    """Container Widget for joysticks."""

    def __init__(self, parent: StreamWindow):
        super().__init__(parent)
        self._last_pos = None
        self._grab_outside = False
        self._left = Joystick(self, "left")
        self._right = Joystick(self, "right")
        self.setLayout(QtWidgets.QHBoxLayout())
        self.layout().setAlignment(Qt.AlignCenter)
        self.layout().setContentsMargins(0, 0, 0, 0)
        self.setStyleSheet(
            "background-color: rgba(255, 255, 255, 0.4); border-radius:25%;"
        )
        cursor = QtGui.QCursor()
        cursor.setShape(Qt.SizeAllCursor)
        self.setCursor(cursor)
        self.layout().addWidget(self._left)
        self.layout().addWidget(self._right)

    # pylint: disable=useless-super-delegation
    def window(self) -> StreamWindow:
        """Return Window."""
        return super().window()

    def hide_sticks(self):
        """Hide Joysticks."""
        self._left.hide()
        self._right.hide()

    def show_sticks(self, left=False, right=False):
        """Show Joysticks."""
        width = 0
        if left:
            width += Joystick.SIZE
            self._left.show()
        if right:
            width += Joystick.SIZE
            self._right.show()
        self.resize(width, Joystick.SIZE)
        self.show()

    def default_pos(self):
        """Move widget to default position."""
        if self.window().options().fullscreen:
            width = self.screen().virtualSize().width()
            height = self.screen().virtualSize().height()
        else:
            width = self.window().size().width()
            height = self.window().size().height()
        x_pos = width / 2 - self.size().width() / 2
        y_pos = height - self.size().height()
        new_pos = QtCore.QPoint(x_pos, y_pos)
        self.move(new_pos)

    def mousePressEvent(self, event):
        """Mouse Press Event"""
        event.accept()
        self._grab_outside = True
        self._last_pos = event.globalPos()

    def mouseReleaseEvent(self, event):  # pylint: disable=unused-argument
        """Mouse Release Event."""
        event.accept()
        self._grab_outside = False

    def mouseMoveEvent(self, event):
        """Mouse Move Event."""
        event.accept()
        if event.buttons() == QtCore.Qt.NoButton:
            return
        if self._grab_outside:
            cur_pos = self.mapToGlobal(self.pos())
            global_pos = event.globalPos()
            diff = global_pos - self._last_pos
            new_pos = self.mapFromGlobal(cur_pos + diff)
            if self.window().options().fullscreen:
                max_x = self.screen().virtualSize().width()
                max_y = self.screen().virtualSize().height()
            else:
                max_x = self.window().size().width()
                max_y = self.window().size().height()
            x_pos = min(max(new_pos.x(), 0), max_x - self.size().width())
            y_pos = min(max(new_pos.y(), 0), max_y - self.size().height())
            new_pos = QtCore.QPoint(x_pos, y_pos)
            self.move(new_pos)
            self._last_pos = global_pos


class Joystick(QtWidgets.QLabel):
    """Draggable Joystick Widget."""

    SIZE = 180
    RADIUS = 50

    class Direction(Enum):
        """Enums for directions."""

        LEFT = 0
        RIGHT = 1
        UP = 2
        DOWN = 3

    def __init__(self, parent: JoystickWidget, stick: str):
        self._stick = stick
        self._grabbed = False
        super().__init__(parent)
        self.setMinimumSize(Joystick.SIZE, Joystick.SIZE)
        self._moving_offset = QtCore.QPointF(0, 0)

        self.setStyleSheet("background-color: rgba(0, 0, 0, 0.0)")
        self._set_cursor()

    # pylint: disable=useless-super-delegation
    def parent(self) -> JoystickWidget:
        """Return Parent."""
        return super().parent()

    # pylint: disable=useless-super-delegation
    def window(self) -> StreamWindow:
        """Return Window."""
        return super().window()

    def _set_cursor(self, shape=Qt.SizeAllCursor):
        cursor = QtGui.QCursor()
        cursor.setShape(shape)
        self.setCursor(cursor)

    def paintEvent(self, event):  # pylint: disable=unused-argument
        """Paint Event."""
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        bounds = QtCore.QRectF(
            -Joystick.RADIUS,
            -Joystick.RADIUS,
            Joystick.RADIUS * 2,
            Joystick.RADIUS * 2,
        ).translated(self.center)
        painter.setBrush(QtGui.QColor(75, 75, 75, 150))
        painter.drawEllipse(bounds)
        painter.setBrush(Qt.black)
        painter.drawEllipse(self._center_ellipse())

    def _center_ellipse(self):
        if self._grabbed:
            return QtCore.QRectF(-40, -40, 80, 80).translated(self._moving_offset)
        return QtCore.QRectF(-40, -40, 80, 80).translated(self.center)

    def _limit_bounds(self, point):
        limit_line = QtCore.QLineF(self.center, point)
        if limit_line.length() > Joystick.RADIUS:
            limit_line.setLength(Joystick.RADIUS)
        return limit_line.p2()

    def mousePressEvent(self, event):
        """Mouse Press Event."""
        if self._center_ellipse().contains(event.pos()):
            event.accept()
            self._grabbed = True
            self._set_cursor(Qt.ClosedHandCursor)
            self._moving_offset = self._limit_bounds(event.pos())
            self.window().move_stick(self._stick, self.joystick_position)
            self.update()
        else:
            event.ignore()
            self.parent().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        """Mouse Release Event."""
        if self._grabbed:
            event.accept()
            self._grabbed = False
            self._moving_offset = QtCore.QPointF(0, 0)
            self.window().move_stick(self._stick, self.joystick_position)
            self._set_cursor(Qt.OpenHandCursor)
            self.update()
        else:
            event.ignore()
            self.parent().mouseReleaseEvent(event)

    def mouseMoveEvent(self, event):
        """Mouse Move Event."""
        if self._grabbed:
            event.accept()
            self._set_cursor(Qt.ClosedHandCursor)
            self._moving_offset = self._limit_bounds(event.pos())
            self.window().move_stick(self._stick, self.joystick_position)
            self.update()
        else:
            event.ignore()

    @property
    def center(self) -> QtCore.QPointF:
        """Return Center."""
        return QtCore.QPointF(self.width() / 2, self.height() / 2)

    @property
    def joystick_position(self) -> QtCore.QPointF:
        """Return Joystick Position."""
        if not self._grabbed:
            return QtCore.QPointF(0.0, 0.0)
        vector = QtCore.QLineF(self.center, self._moving_offset)
        point = vector.p2()
        return (point - self.center) / Joystick.RADIUS
