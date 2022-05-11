# pylint: disable=c-extension-no-member,invalid-name
"""Joystick Widget for stream window."""
from enum import Enum

from PySide6 import QtWidgets, QtGui, QtCore
from PySide6.QtCore import Qt  # pylint: disable=no-name-in-module


class JoystickWidget(QtWidgets.QFrame):
    """Container Widget for joysticks."""

    def __init__(self, window, left=False, right=False):
        super().__init__(window)
        self.window = window
        self._last_pos = None
        self.grab_outside = False
        self.left = Joystick(self, "left") if left else None
        self.right = Joystick(self, "right") if right else None
        self.layout = QtWidgets.QHBoxLayout(self)
        self.layout.setAlignment(Qt.AlignCenter)
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.setStyleSheet(
            "background-color: rgba(255, 255, 255, 0.4); border-radius:25%;"
        )
        self._set_cursor()
        for joystick in [self.left, self.right]:
            self.layout.addWidget(joystick)
            joystick.show()

    def _set_cursor(self, shape=Qt.SizeAllCursor):
        cursor = QtGui.QCursor()
        cursor.setShape(shape)
        self.setCursor(cursor)

    def hide_sticks(self):
        """Hide Joysticks."""
        self.left.hide()
        self.right.hide()

    def show_sticks(self, left=False, right=False):
        """Show Joysticks."""
        width = 0
        if left:
            width += Joystick.SIZE
            self.left.show()
        if right:
            width += Joystick.SIZE
            self.right.show()
        self.resize(width, Joystick.SIZE)
        self.show()

    def default_pos(self):
        """Move widget to default position."""
        if self.window.fullscreen:
            width = self.screen().virtualSize().width()
            height = self.screen().virtualSize().height()
        else:
            width = self.window.size().width()
            height = self.window.size().height()
        x_pos = width / 2 - self.size().width() / 2
        y_pos = height - self.size().height()
        new_pos = QtCore.QPoint(x_pos, y_pos)
        self.move(new_pos)

    def mousePressEvent(self, event):
        """Mouse Press Event"""
        self.grab_outside = True
        self._last_pos = event.globalPos()

    def mouseReleaseEvent(self, event):  # pylint: disable=unused-argument
        """Mouse Release Event."""
        self.grab_outside = False

    def mouseMoveEvent(self, event):
        """Mouse Move Event."""
        if event.buttons() == QtCore.Qt.NoButton:
            return
        if self.grab_outside:
            cur_pos = self.mapToGlobal(self.pos())
            global_pos = event.globalPos()
            diff = global_pos - self._last_pos
            new_pos = self.mapFromGlobal(cur_pos + diff)
            if self.window.fullscreen:
                max_x = self.screen().virtualSize().width()
                max_y = self.screen().virtualSize().height()
            else:
                max_x = self.window.size().width()
                max_y = self.window.size().height()
            x_pos = min(max(new_pos.x(), 0), max_x - self.size().width())
            y_pos = min(max(new_pos.y(), 0), max_y - self.size().height())
            new_pos = QtCore.QPoint(x_pos, y_pos)
            self.move(new_pos)
            self._last_pos = global_pos


class Joystick(QtWidgets.QLabel):
    """Draggable Joystick Widget."""

    SIZE = 180
    MAX_DISTANCE = 50

    class Direction(Enum):
        """Enums for directions."""

        LEFT = 0
        RIGHT = 1
        UP = 2
        DOWN = 3

    def __init__(self, parent, stick):
        self._stick = stick
        self._grabbed = False
        super().__init__(parent)
        self.parent = parent
        self.setMinimumSize(Joystick.SIZE, Joystick.SIZE)
        self._moving_offset = QtCore.QPointF(0, 0)

        self.setStyleSheet("background-color: rgba(0, 0, 0, 0.0)")
        self._set_cursor()

    def _set_cursor(self, shape=Qt.SizeAllCursor):
        cursor = QtGui.QCursor()
        cursor.setShape(shape)
        self.setCursor(cursor)

    def paintEvent(self, event):  # pylint: disable=unused-argument
        """Paint Event."""
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        bounds = QtCore.QRectF(
            -Joystick.MAX_DISTANCE,
            -Joystick.MAX_DISTANCE,
            Joystick.MAX_DISTANCE * 2,
            Joystick.MAX_DISTANCE * 2,
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
        if limit_line.length() > Joystick.MAX_DISTANCE:
            limit_line.setLength(Joystick.MAX_DISTANCE)
        return limit_line.p2()

    def mousePressEvent(self, event):
        """Mouse Press Event."""
        is_center = self._center_ellipse().contains(event.pos())
        if is_center:
            self._grabbed = True
            self._moving_offset = self._limit_bounds(event.pos())
            point = self.joystick_position
            self.parent.window.rp_worker.stick_state(self._stick, point=point)
            self.update()
        if not self._grabbed:
            self.parent.mousePressEvent(event)
        else:
            self._set_cursor(Qt.ClosedHandCursor)

    def mouseReleaseEvent(self, event):
        """Mouse Release Event."""
        if self._grabbed:
            self._grabbed = False
            self._moving_offset = QtCore.QPointF(0, 0)
            point = self.joystick_position
            self.parent.window.rp_worker.stick_state(self._stick, point=point)
            self._set_cursor(Qt.OpenHandCursor)
            self.update()
        else:
            self.parent.mouseReleaseEvent(event)

    def mouseMoveEvent(self, event):
        """Mouse Move Event."""
        if self._grabbed:
            self._set_cursor(Qt.ClosedHandCursor)
            self._moving_offset = self._limit_bounds(event.pos())
            point = self.joystick_position
            self.parent.window.rp_worker.stick_state(self._stick, point=point)
            self.update()
        else:
            is_center = self._center_ellipse().contains(event.pos())
            if is_center:
                self._set_cursor(Qt.OpenHandCursor)
            else:
                self._set_cursor()
            self.parent.mouseMoveEvent(event)

    @property
    def center(self) -> QtCore.QPointF:
        """Return Center."""
        return QtCore.QPointF(self.width() / 2, self.height() / 2)

    @property
    def joystick_position(self) -> tuple:
        """Return Joystick Position."""
        if not self._grabbed:
            return (0.0, 0.0)
        vector = QtCore.QLineF(self.center, self._moving_offset)
        point = vector.p2()
        point_x = (point.x() - self.center.x()) / Joystick.MAX_DISTANCE
        point_y = (point.y() - self.center.y()) / Joystick.MAX_DISTANCE
        return (point_x, point_y)
