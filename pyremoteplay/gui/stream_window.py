"""Stream Window for GUI."""
import time
from collections import deque
from enum import Enum

from pyremoteplay.av import AVReceiver
from pyremoteplay.session import SessionAsync
from pyremoteplay.util import event_emitter, timeit
from PySide6 import QtCore, QtGui, QtMultimedia, QtWidgets
from PySide6.QtCore import Qt

from .options import ControlsWidget
from .util import label, message


class QtReceiver(AVReceiver):

    def __init__(self, session):
        super().__init__(session)
        self.video_signal = None
        self.audio_signal = None

    def handle_video(self, buf):
        frame = self.decode_video_frame(buf)
        if frame is None:
            return
        self.video_signal.emit(frame)

    def handle_audio(self, packet):
        frame = self.decode_audio_frame(packet)
        if frame is None:
            return
        self.audio_signal.emit(frame)

    def set_signals(self, video_signal, audio_signal):
        self.video_signal = video_signal
        self.audio_signal = audio_signal


class RPWorker(QtCore.QObject):
    finished = QtCore.Signal()
    started = QtCore.Signal()
    standby_done = QtCore.Signal()

    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        self.window = None
        self.session = None
        self.thread = None
        self.error = ""
        self.standby_done.connect(self.standby_finished)
        event_emitter.on("stop", self.stop)

    def run(self, standby=False):
        if not self.session:
            print("No Session")
            self.stop()
            return
        if not self.window and not standby:
            print("No Stream Window")
            self.stop()
            return
        self.session.loop = self.main_window.async_handler.loop
        self.session.loop.create_task(self.start(standby))

    def stop(self, standby=False):
        if self.session:
            print(f"Stopping Session @ {self.session.host}")
            self.session.stop()
            self.error = self.session.error
        if standby:
            self.standby_done.emit()
        self.session = None
        self.window = None
        self.finished.emit()

    def setup(self, window, host, profile, resolution='720p', fps=60, use_hw=False, quality='default'):
        self.window = window
        self.session = SessionAsync(host, profile, resolution=resolution, fps=fps, av_receiver=QtReceiver, use_hw=use_hw, quality=quality)

    async def start(self, standby=False):
        print("Session Start")
        status = await self.session.start()
        if not status:
            print("Session Failed to Start")
            # message(None, "Error", self.session.error)
            self.stop()
            return
        else:
            self.session.av_receiver.set_signals(self.window.av_worker.video_frame, self.window.av_worker.audio_frame)
            self.window.av_worker.video_frame.connect(self.window.av_worker.next_video_frame)
            self.window.av_worker.audio_frame.connect(self.window.av_worker.next_audio_frame)
            self.started.emit()
        if standby:
            await self.session.stream_ready.wait()
            self.send_standby()
        elif self.session._stop_event:
            await self.session._stop_event.wait()
            print("Session Finished")

    def send_standby(self):
        self.session.standby()
        self.stop(standby=True)

    def standby_finished(self):
        host = self.session.host if self.session else "Unknown"
        self.main_window.standby_callback(host)

    def stick_state(self, stick: str, direction: str = None, value: float = None, point=None):
        if point is not None:
            self.session.controller.stick(stick, point=point)
            return

        if direction in ("LEFT", "RIGHT"):
            axis = "X"
        else:
            axis = "Y"
        if direction in ("UP", "LEFT") and value != 0.0:
            value *= -1.0
        self.session.controller.stick(stick, axis, value)

    def send_button(self, button, action):
        self.session.controller.button(button, action)


class AVProcessor(QtCore.QObject):
    started = QtCore.Signal()
    frame = QtCore.Signal()
    slow = QtCore.Signal()
    video_frame = QtCore.Signal(object)
    audio_frame = QtCore.Signal(object)

    def __init__(self, window):
        super().__init__()
        self.window = window
        self.pixmaps = deque()
        self._set_slow = False
        self.skip = False
        # event_emitter.on("video_frame", self.next_video_frame)
        # event_emitter.on("audio_frame", self.next_audio_frame)

    def next_video_frame(self, frame):
        #frame = self.window.rp_worker.session.av_receiver.get_video_frame()
        if len(self.pixmaps) > 5:
            if self.skip:
                self.skip = False
            else:
                self.skip = True
        else:
            self.skip = False
        if self.skip:
            return
        if frame is None:
            return
        image = QtGui.QImage(
            bytearray(frame.planes[0]),
            frame.width,
            frame.height,
            frame.width * 3,
            QtGui.QImage.Format_RGB888,
        )
        # if len(self.pixmaps) > 5:
        #     self.pixmaps.clear()
        self.pixmaps.append(QtGui.QPixmap.fromImage(image))

    def next_audio_frame(self, frame):
        #frame = self.window.rp_worker.session.av_receiver.get_audio_frame()
        self.window.new_audio_frame(frame)


class JoystickWidget(QtWidgets.QFrame):
    def __init__(self, window, left=False, right=False):
        super().__init__(window)
        self.window = window
        self.grab_outside = False
        self.left = Joystick(self, "left") if left else None
        self.right = Joystick(self, "right") if right else None
        self.layout = QtWidgets.QHBoxLayout(self)
        self.layout.setAlignment(Qt.AlignCenter)
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.setStyleSheet("background-color: rgba(255, 255, 255, 0.4); border-radius:25%;")
        self.set_cursor()
        for joystick in [self.left, self.right]:
            self.layout.addWidget(joystick)
            joystick.show()

    def set_cursor(self, shape=Qt.SizeAllCursor):
        cursor = QtGui.QCursor()
        cursor.setShape(shape)
        self.setCursor(cursor)

    def hide_sticks(self):
        self.left.hide()
        self.right.hide()

    def show_sticks(self, left=False, right=False):
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
        if self.window.fullscreen:
            width = self.window.main_window.screen.virtualSize().width()
            height = self.window.main_window.screen.virtualSize().height()
        else:
            width = self.window.size().width()
            height = self.window.size().height()
        x_pos = width / 2 - self.size().width() / 2
        y_pos = height - self.size().height()
        new_pos = QtCore.QPoint(x_pos, y_pos)
        self.move(new_pos)

    def mousePressEvent(self, event):
        self.grab_outside = True
        self._last_pos = event.globalPos()

    def mouseReleaseEvent(self, event):
        self.grab_outside = False

    def mouseMoveEvent(self, event):
        if event.buttons() == QtCore.Qt.NoButton:
            return
        if self.grab_outside:
            cur_pos = self.mapToGlobal(self.pos())
            global_pos = event.globalPos()
            diff = global_pos - self._last_pos
            new_pos = self.mapFromGlobal(cur_pos + diff)
            if self.window.fullscreen:
                max_x = self.window.main_window.screen.virtualSize().width()
                max_y = self.window.main_window.screen.virtualSize().height()
            else:
                max_x = self.window.size().width()
                max_y = self.window.size().height()
            x_pos = min(max(new_pos.x(), 0), max_x - self.size().width())
            y_pos = min(max(new_pos.y(), 0), max_y - self.size().height())
            new_pos = QtCore.QPoint(x_pos, y_pos)
            self.move(new_pos)
            self._last_pos = global_pos


class Joystick(QtWidgets.QLabel):

    SIZE = 180

    class Direction(Enum):
        LEFT = 0
        RIGHT = 1
        UP = 2
        DOWN = 3

    def __init__(self, parent, stick):
        super().__init__(parent)
        self.parent = parent
        self.stick = stick
        self.setMinimumSize(Joystick.SIZE, Joystick.SIZE)
        self.movingOffset = QtCore.QPointF(0, 0)
        self.grabbed = False
        self.__maxDistance = 50
        self.setStyleSheet("background-color: rgba(0, 0, 0, 0.0)")
        self.set_cursor()
        self.setMouseTracking(True)

    def set_cursor(self, shape=Qt.SizeAllCursor):
        cursor = QtGui.QCursor()
        cursor.setShape(shape)
        self.setCursor(cursor)

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        bounds = QtCore.QRectF(-self.__maxDistance, -self.__maxDistance, self.__maxDistance * 2, self.__maxDistance * 2).translated(self._center())
        painter.setBrush(QtGui.QColor(75, 75, 75, 150))
        painter.drawEllipse(bounds)
        painter.setBrush(Qt.black)
        painter.drawEllipse(self._centerEllipse())

    def _centerEllipse(self):
        if self.grabbed:
            return QtCore.QRectF(-40, -40, 80, 80).translated(self.movingOffset)
        return QtCore.QRectF(-40, -40, 80, 80).translated(self._center())

    def _center(self):
        return QtCore.QPointF(self.width()/2, self.height()/2)

    def _boundJoystick(self, point):
        limitLine = QtCore.QLineF(self._center(), point)
        if (limitLine.length() > self.__maxDistance):
            limitLine.setLength(self.__maxDistance)
        return limitLine.p2()

    def joystickDirection(self):
        if not self.grabbed:
            return (0.0, 0.0)
        vector = QtCore.QLineF(self._center(), self.movingOffset)
        point = vector.p2()
        point_x = (point.x() - self._center().x()) / self.__maxDistance
        point_y = (point.y() - self._center().y()) / self.__maxDistance
        return (point_x, point_y)

    def mousePressEvent(self, event):
        is_center = self._centerEllipse().contains(event.pos())
        if is_center:
            self.grabbed = True
            self.movingOffset = self._boundJoystick(event.pos())
            self.update()
        if not self.grabbed:
            self.parent.mousePressEvent(event)
        else:
            self.set_cursor(Qt.ClosedHandCursor)
        return super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if not self.grabbed:
            self.parent.mouseMoveEvent(event)
        self.grabbed = False
        self.movingOffset = QtCore.QPointF(0, 0)
        self.update()
        point = self.joystickDirection()
        self.parent.window.rp_worker.stick_state(self.stick, point=point)
        self.set_cursor(Qt.OpenHandCursor)

    def mouseMoveEvent(self, event):
        if self.grabbed:
            self.set_cursor(Qt.ClosedHandCursor)
            self.movingOffset = self._boundJoystick(event.pos())
            self.update()
            point = self.joystickDirection()
            self.parent.window.rp_worker.stick_state(self.stick, point=point)
        else:
            is_center = self._centerEllipse().contains(event.pos())
            if is_center:
                self.set_cursor(Qt.OpenHandCursor)
            else:
                self.set_cursor()
            self.parent.mouseMoveEvent(event)


class StreamWindow(QtWidgets.QWidget):
    started = QtCore.Signal()

    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        self.hide()
        print(self.main_window.screen.virtualSize().width(), self.main_window.screen.virtualSize().height())
        self.setMaximumWidth(self.main_window.screen.virtualSize().width())
        self.setMaximumHeight(self.main_window.screen.virtualSize().height())
        self.setStyleSheet("background-color: black")
        self.video_output = QtWidgets.QLabel(self, alignment=Qt.AlignCenter)
        self.audio_output = None
        self.center_text = QtWidgets.QLabel("Starting Stream...", alignment=Qt.AlignCenter)
        self.center_text.setWordWrap(True)
        self.center_text.setStyleSheet("QLabel {color: white;font-size: 24px;}")
        self.layout = QtWidgets.QVBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.addWidget(self.video_output)
        self.layout.addWidget(self.center_text)
        self.joystick = JoystickWidget(self, left=True, right=True)
        self.joystick.hide()
        self.input_options = None
        self.fps_label = label(self, "FPS: ")
        self.av_worker = AVProcessor(self)
        self.timer = QtCore.QTimer()
        self.timer.setTimerType(Qt.PreciseTimer)
        self.timer.timeout.connect(self.new_video_frame)
        self.ms_refresh = 0
        self._video_transform_mode = Qt.SmoothTransformation

        self.rp_worker = self.main_window.rp_worker
        self.av_thread = QtCore.QThread()
        self.rp_worker.started.connect(self.show_video)
        self.rp_worker.finished.connect(self.close)
        self.av_worker.slow.connect(self.av_slow)
        self.av_worker.moveToThread(self.av_thread)
        self.av_thread.started.connect(self.start_timer)
        event_emitter.on("audio_config", self.init_audio)

    def start(self, host, name, profile, resolution='720p', fps=60, show_fps=False, fullscreen=False, input_map=None, input_options=None, use_hw=False, quality="default"):
        self.center_text.show()
        self.input_options = input_options
        self.frame_mutex = QtCore.QMutex()
        self.video_output.hide()
        self.mapping = ControlsWidget.DEFAULT_MAPPING if input_map is None else input_map
        self.fps = fps
        self.fullscreen = fullscreen
        self.ms_refresh = 1000.0/self.fps * 0.4
        self.setWindowTitle(f"Session {name} @ {host}")
        self.rp_worker.setup(self, host, profile, resolution, fps, use_hw, quality)
        self.show_fps = show_fps

        if self.show_fps:
            self.init_fps()
            self.fps_label.show()
        else:
            self.fps_label.hide()
        self.av_thread.start()
        self.started.connect(self.main_window.session_start)
        self.started.emit()
        self.resize(self.rp_worker.session.resolution['width'], self.rp_worker.session.resolution['height'])
        if self.fullscreen:
            self.showFullScreen()
        else:
            self.show()

    def show_video(self):
        self.center_text.hide()
        self.video_output.show()
        joysticks = self.input_options.get("joysticks")
        if joysticks:
            self.joystick.hide_sticks()
            self.joystick.show_sticks(joysticks['left'], joysticks['right'])
            self.joystick.default_pos()

    def init_audio(self):
        config = self.rp_worker.session.av_receiver.audio_config
        audio_format = QtMultimedia.QAudioFormat()
        audio_format.setChannelCount(config['channels'])
        audio_format.setSampleRate(config['rate'])
        audio_format.setSampleFormat(QtMultimedia.QAudioFormat.Int16)

        self.audio_output = QtMultimedia.QAudioSink(format=audio_format)
        self.audio_output.setBufferSize(3840)
        self.audio_buffer = self.audio_output.start()

    def new_video_frame(self):
        try:
            pixmap = self.av_worker.pixmaps.popleft()
        except IndexError:
            return
        if self.fullscreen:
            pixmap = pixmap.scaled(self.video_output.size(), aspectMode=Qt.KeepAspectRatio, mode=self._video_transform_mode)
        self.video_output.setPixmap(pixmap)
        if self.show_fps:
            self.set_fps()

    def new_audio_frame(self, data):
        if self.audio_output is None:
            self.init_audio()
        self.audio_buffer.write(data)

    def av_slow(self):
        self._video_transform_mode = Qt.FastTransformation

    def init_fps(self):
        self.fps_label.move(20, 20)
        self.fps_label.setStyleSheet("background-color:#33333333;color:white;padding-left:5px;")
        self.fps_sample = 0
        self.last_time = time.time()

    def set_fps(self):
        if self.fps_label is not None:
            self.fps_sample += 1
            if self.fps_sample < self.fps:
                return
            now = time.time()
            delta = now - self.last_time
            self.last_time = now
            self.fps_label.setText(f"FPS: {int(self.fps/delta)}")
            self.fps_sample = 0

    def mousePressEvent(self, event):
        button = event.button().name.decode()
        self._handle_press(button)
        event.accept()

    def mouseReleaseEvent(self, event):
        button = event.button().name.decode()
        self._handle_release(button)
        event.accept()

    def keyPressEvent(self, event):
        key = Qt.Key(event.key()).name.decode()
        if not event.isAutoRepeat():
            self._handle_press(key)
        event.accept()

    def keyReleaseEvent(self, event):
        if event.isAutoRepeat():
            return
        key = Qt.Key(event.key()).name.decode()
        self._handle_release(key)
        event.accept()

    def _handle_press(self, key):
        button = self.mapping.get(key)
        if button is None:
            return
        if button == "QUIT":
            self.rp_worker.stop()
            return
        if button == "STANDBY":
            message(self.main_window, "Standby", "Set host to standby?", level="info", cb=self.send_standby, escape=True)
            return
        if "STICK" in button:
            button = button.split("_")
            stick = button[1]
            direction = button[2]
            self.rp_worker.stick_state(stick, direction, 1.0)
        else:
            self.rp_worker.send_button(button, "press")

    def _handle_release(self, key):
        button = self.mapping.get(key)
        if button is None:
            print(f"Button Invalid: {key}")
            return
        if button in ["QUIT", "STANDBY"]:
            return
        if "STICK" in button:
            button = button.split("_")
            stick = button[1]
            direction = button[2]
            self.rp_worker.stick_state(stick, direction, 0.0)
        else:
            self.rp_worker.send_button(button, "release")

    def send_standby(self):
        if self.rp_worker.session is not None:
            self.rp_worker.session.standby()

    def closeEvent(self, event):
        self.hide()
        self.cleanup()
        self.deleteLater()
        event.accept()

    def cleanup(self):
        print("Cleaning up window")
        self.timer.stop()
        self.av_thread.quit()
        event_emitter.remove_all_listeners("video_frame")
        event_emitter.remove_all_listeners("audio_frame")
        if self.audio_output:
            self.audio_output.stop()
            self.audio_output = None
            self.audio_buffer = None
        self.main_window.session_stop()

    def start_timer(self):
        print("AV Processor Started")
        self.timer.start(self.ms_refresh)
