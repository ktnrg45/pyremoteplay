# pylint: disable=c-extension-no-member,invalid-name
"""Stream Window for GUI."""
import time
from collections import deque
import logging

from PySide6 import QtCore, QtMultimedia, QtWidgets
from PySide6.QtCore import Qt  # pylint: disable=no-name-in-module
from PySide6.QtMultimedia import QAudioDevice
from pyremoteplay.av import AVReceiver
from pyremoteplay.device import RPDevice

from .joystick import JoystickWidget
from .controls import ControlsWidget
from .util import label, message
from .video import VideoWidget, YUVGLWidget

_LOGGER = logging.getLogger(__name__)


class QtReceiver(AVReceiver):
    """AV Receiver for QT."""

    def __init__(self, codec_name=""):
        super().__init__(codec_name)
        self.video_signal = None
        self.audio_signal = None
        self.rgb = True
        self.audio_queue = deque()

    def decode_video_frame(self, buf: bytes) -> bytes:
        """Decode Video Frame."""
        frame = AVReceiver.video_frame(buf, self.codec, to_rgb=self.rgb)
        return frame

    def handle_video(self, buf):
        """Handle video frame."""
        frame = self.decode_video_frame(buf)
        if frame is None:
            return
        self.video_signal.emit(frame)

    def handle_audio(self, buf):
        """Handle Audio Frame."""
        frame = self.decode_audio_frame(buf)
        if frame is None:
            return
        self.audio_queue.append(QtCore.QByteArray(frame))
        self.audio_signal.emit()

    def set_signals(self, video_signal, audio_signal):
        """Set signals."""
        self.video_signal = video_signal
        self.audio_signal = audio_signal


class RPWorker(QtCore.QObject):
    """Worker to interface with RP Session."""

    finished = QtCore.Signal()
    started = QtCore.Signal()
    standby_done = QtCore.Signal()

    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        self.window = None
        self.device = None
        self.session = None
        self.error = ""
        self.standby_done.connect(self.standby_finished)

    def run(self, standby=False):
        """Run Session."""
        if not self.session:
            _LOGGER.warning("No Session")
            self.stop()
            return
        if not self.window and not standby:
            _LOGGER.warning("No Stream Window")
            self.stop()
            return
        self.session.events.on("stop", self.stop)
        # pylint: disable=protected-access
        self.session.events.on("audio_config", self.window._init_audio)
        self.session.loop = self.main_window.async_handler.loop
        self.session.loop.create_task(self.start(standby))

    def stop(self, standby=False):
        """Stop session."""
        if self.session:
            self.error = self.session.error
            _LOGGER.info("Stopping Session @ %s", self.session.host)
            self.session.stop()
        if standby:
            self.standby_done.emit()
        self.session = None
        self.device = None
        self.window = None
        self.finished.emit()

    def setup(
        self,
        window,
        device: RPDevice,
        user: str,
        options: dict,
    ):
        """Setup session."""
        self.window = window
        self.device = device
        self.session = self.device.create_session(
            user,
            resolution=options.get("resolution"),
            fps=options.get("fps"),
            av_receiver=QtReceiver(options.get("decoder")),
            use_hw=options.get("use_hw"),
            quality=options.get("quality"),
        )

    async def start(self, standby=False):
        """Start Session."""
        _LOGGER.debug("Session Start")
        if self.window:
            self.session.av_receiver.rgb = False if self.window.use_opengl else True

        started = await self.device.connect()

        if not started:
            _LOGGER.warning("Session Failed to Start")
            self.stop()
            return

        if standby:
            result = await self.device.standby()
            _LOGGER.info("Standby Success: %s", result)
            self.stop(standby=True)
            return

        self.session.av_receiver.set_signals(
            self.window.video_frame, self.window.audio_frame
        )
        self.window.audio_frame.connect(
            self.window.next_audio_frame, Qt.QueuedConnection
        )
        self.window.video_frame.connect(
            self.window.video_output.next_video_frame, Qt.QueuedConnection
        )
        self.started.emit()

        if self.session.stop_event:
            await self.session.stop_event.wait()
            _LOGGER.info("Session Finished")

    def standby_finished(self):
        """Callback when standby command sent."""
        host = self.session.host if self.session else "Unknown"
        self.main_window.standby_callback(host)

    def stick_state(
        self, stick: str, direction: str = None, value: float = None, point=None
    ):
        """Send stick state"""
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
        """Send button."""
        self.session.controller.button(button, action)


class StreamWindow(QtWidgets.QWidget):
    """Window for stream."""

    started = QtCore.Signal()
    video_frame = QtCore.Signal(object)
    audio_frame = QtCore.Signal()

    def __init__(self, main_window):
        self.mapping = None
        self.fps = None
        self.fullscreen = False
        self.use_opengl = False
        self.show_fps = False
        self.audio_device = None
        self.fps_sample = 0
        self.last_time = time.time()
        super().__init__()
        self.main_window = main_window
        self.hide()
        _LOGGER.debug(
            "Screen Size: %s x %s",
            self.main_window.screen.virtualSize().width(),
            self.main_window.screen.virtualSize().height(),
        )
        self.setMaximumWidth(self.main_window.screen.virtualSize().width())
        self.setMaximumHeight(self.main_window.screen.virtualSize().height())
        self.setStyleSheet("background-color: black")
        self.video_output = None
        self.audio_output = None
        self.audio_device = None
        self.audio_buffer = None
        self.audio_queue = None
        self.audio_thread = QtCore.QThread()
        self.opengl = False
        self.center_text = QtWidgets.QLabel(
            "Starting Stream...", alignment=Qt.AlignCenter
        )
        self.center_text.setWordWrap(True)
        self.center_text.setStyleSheet("QLabel {color: white;font-size: 24px;}")
        self.layout = QtWidgets.QVBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.addWidget(self.center_text)
        self.joystick = JoystickWidget(self, left=True, right=True)
        self.joystick.hide()
        self.input_options = None
        self.fps_label = label(self, "FPS: ")

        self.rp_worker = self.main_window.rp_worker
        self.rp_worker.started.connect(self._show_video)
        self.rp_worker.finished.connect(self.close)

    def start(
        self,
        device: RPDevice,
        user: str,
        options: dict,
        audio_device: QAudioDevice = None,
        input_map: dict = None,
        input_options: dict = None,
    ):
        """Start Session."""
        _LOGGER.debug(audio_device)
        self.center_text.show()
        self.input_options = input_options
        self.mapping = (
            ControlsWidget.DEFAULT_MAPPING if input_map is None else input_map
        )
        self.fps = options.get("fps")
        self.fullscreen = options.get("fullscreen")
        self.use_opengl = options.get("use_opengl")
        self.show_fps = options.get("show_fps")
        self.audio_device = audio_device
        self.setWindowTitle(f"Session {user} @ {device.host}")
        self.rp_worker.setup(self, device, user, options)
        self.resize(
            self.rp_worker.session.resolution["width"],
            self.rp_worker.session.resolution["height"],
        )
        output = YUVGLWidget if self.use_opengl else VideoWidget
        self.video_output = output(
            self.rp_worker.session.resolution["width"],
            self.rp_worker.session.resolution["height"],
        )
        self.video_output.hide()
        self.layout.addWidget(self.video_output)
        self.joystick.setParent(self.video_output)
        self.fps_label.setParent(self.video_output)

        if self.show_fps:
            self.video_output.frame_updated.connect(self._set_fps)
            self._init_fps()
            self.fps_label.show()
        else:
            self.fps_label.hide()
        self.started.connect(self.main_window.session_start)
        self.started.emit()
        if self.fullscreen:
            self.showFullScreen()
        else:
            self.show()

    def _show_video(self):
        """Show Video Output."""
        self.center_text.hide()
        self.video_output.show()
        joysticks = self.input_options.get("joysticks")
        if joysticks:
            self.joystick.hide_sticks()
            self.joystick.show_sticks(joysticks["left"], joysticks["right"])
            self.joystick.default_pos()
        self.setFixedSize(self.width(), self.height())

    def _init_audio(self):
        if not self.rp_worker.session:
            return
        config = self.rp_worker.session.av_receiver.audio_config
        if not config:
            return
        if self.audio_buffer or self.audio_output:
            return
        self.audio_queue = self.rp_worker.session.av_receiver.audio_queue
        audio_format = QtMultimedia.QAudioFormat()
        audio_format.setChannelCount(config["channels"])
        audio_format.setSampleRate(config["rate"])
        audio_format.setSampleFormat(QtMultimedia.QAudioFormat.Int16)

        self.audio_output = QtMultimedia.QAudioSink(
            self.audio_device, format=audio_format
        )
        self.audio_output.setBufferSize(config["packet_size"] * 4)
        self.audio_buffer = self.audio_output.start()
        self.audio_buffer.moveToThread(self.audio_thread)

    def next_audio_frame(self):
        """Handle next audio frame."""
        if self.audio_output is None:
            self._init_audio()
        if len(self.audio_queue) < 4:
            return
        data = self.audio_queue.popleft()
        if self.audio_buffer:
            self.audio_buffer.write(data)

    def _init_fps(self):
        self.fps_label.move(20, 20)
        self.fps_label.resize(80, 20)
        self.fps_label.setStyleSheet(
            "background-color:#33333333;color:white;padding-left:5px;"
        )
        self.fps_sample = 0
        self.last_time = time.time()

    def _set_fps(self):
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
        """Mouse Press Event."""
        button = event.button().name.decode()
        self._handle_press(button)
        event.accept()

    def mouseReleaseEvent(self, event):
        """Mouse Release Event."""
        button = event.button().name.decode()
        self._handle_release(button)
        event.accept()

    def keyPressEvent(self, event):
        """Key Press Event."""
        key = Qt.Key(event.key()).name.decode()
        if not event.isAutoRepeat():
            self._handle_press(key)
        event.accept()

    def keyReleaseEvent(self, event):
        """Key Release Event."""
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
            message(
                self.main_window,
                "Standby",
                "Set host to standby?",
                level="info",
                callback=self.send_standby,
                escape=True,
            )
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
            _LOGGER.debug("Button Invalid: %s", key)
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
        """Place host in standby."""
        if self.rp_worker.session is not None:
            self.rp_worker.session.standby()

    def closeEvent(self, event):
        """Close Event."""
        self.hide()
        self.cleanup()
        self.deleteLater()
        event.accept()

    def cleanup(self):
        """Cleanup session."""
        _LOGGER.debug("Cleaning up window")
        if self.video_output:
            self.video_output.deleteLater()
            self.video_output = None
        if self.audio_output:
            self.audio_output.stop()
            self.audio_output = None
            self.audio_buffer = None
        self.main_window.session_stop()
