# pylint: disable=c-extension-no-member,invalid-name
"""Stream Window for GUI."""
from __future__ import annotations
import time
import logging
from typing import TYPE_CHECKING

import av
from PySide6 import QtCore, QtWidgets
from PySide6.QtCore import Qt  # pylint: disable=no-name-in-module
from PySide6.QtMultimedia import QAudioDevice
from pyremoteplay.receiver import AVReceiver
from pyremoteplay.device import RPDevice
from pyremoteplay.const import Resolution

from .joystick import JoystickWidget
from .controls import ControlsWidget
from .util import label, message
from .video import VideoWidget, YUVGLWidget
from .widgets import FadeOutLabel
from .audio import QtAudioWorker, SoundDeviceAudioWorker

if TYPE_CHECKING:
    from .main_window import MainWindow

_LOGGER = logging.getLogger(__name__)


class QtReceiver(AVReceiver):
    """AV Receiver for QT."""

    def __init__(self):
        super().__init__()
        self.video_signal = None
        self.audio_signal = None
        self.rgb = True

    def handle_video(self, frame: av.VideoFrame):
        """Handle video frame."""
        self.video_signal.emit(frame)

    def handle_audio(self, frame: av.AudioFrame):
        """Handle Audio Frame."""
        self.audio_signal.emit(frame)

    def set_signals(self, video_signal, audio_signal):
        """Set signals."""
        self.video_signal = video_signal
        self.audio_signal = audio_signal


class StreamWindow(QtWidgets.QWidget):
    """Window for stream."""

    started = QtCore.Signal()
    stopped = QtCore.Signal()
    video_frame = QtCore.Signal(object)
    audio_frame = QtCore.Signal(object)

    def __init__(self, main_window: MainWindow):
        self.mapping = None
        self.fps = None
        self.fullscreen = False
        self.use_opengl = False
        self.show_fps = False
        self.video_output = None
        self.audio_device = None
        self.audio_output = None
        self.fps_sample = 0
        self.last_time = time.time()
        super().__init__()
        self.main_window = main_window
        self.hide()

        self.setMaximumWidth(self.screen().virtualSize().width())
        self.setMaximumHeight(self.screen().virtualSize().height())
        self.setObjectName("stream-window")
        self.setStyleSheet("#stream-window{background-color: black}")
        self.center_text = FadeOutLabel(
            "Starting Stream...", self, alignment=Qt.AlignCenter
        )
        self.setLayout(QtWidgets.QVBoxLayout())
        self.layout().setContentsMargins(0, 0, 0, 0)
        self.joystick = JoystickWidget(self, left=True, right=True)
        self.joystick.hide()
        self.input_options = None
        self.fps_label = label(self, "FPS: ")

        self.rp_worker = self.main_window.rp_worker
        self.rp_worker.started.connect(self._show_video)
        self.rp_worker.finished.connect(self.close)

    def resizeEvent(self, event):
        """Resize Event."""
        super().resizeEvent(event)
        self.center_text.setFixedSize(self.size().width(), 100)

    def setup_receiver(self) -> QtReceiver:
        """Setup Receiver."""
        receiver = QtReceiver()
        receiver.rgb = not self.use_opengl
        receiver.set_signals(self.video_frame, self.audio_frame)
        self.audio_frame.connect(
            self.audio_output.next_audio_frame, Qt.QueuedConnection
        )
        self.video_frame.connect(
            self.video_output.next_video_frame, Qt.QueuedConnection
        )
        return receiver

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
        self.setWindowTitle(f"Session {user} @ {device.host}")
        _LOGGER.debug(audio_device)
        if options["use_qt_audio"]:
            self.audio_output = QtAudioWorker()
        else:
            self.audio_output = SoundDeviceAudioWorker()
        self.input_options = input_options
        self.mapping = (
            ControlsWidget.DEFAULT_MAPPING if input_map is None else input_map
        )
        self.fps = options.get("fps")
        self.fullscreen = options.get("fullscreen")
        self.use_opengl = options.get("use_opengl")
        self.show_fps = options.get("show_fps")
        self.audio_device = audio_device
        resolution = Resolution.preset(options["resolution"])
        output = YUVGLWidget if self.use_opengl else VideoWidget
        self.video_output = output(
            resolution["width"],
            resolution["height"],
        )

        receiver = self.setup_receiver()
        self.rp_worker.setup(device, user, options, receiver)
        device.session.events.on("audio_config", self._init_audio)
        self.resize(
            resolution["width"],
            resolution["height"],
        )
        self.video_output.hide()
        self.layout().addWidget(self.video_output)
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
        self.center_text.move(0, self.contentsRect().center().y())

    def _show_message(self):
        key_name = ""
        for key, button in self.mapping.items():
            if button == "QUIT":
                key_name = key.replace("Key_", "")
                break
        self.center_text.hide()
        self.center_text.move(0, 0)
        self.center_text.setText(f"Press {key_name} to quit.")
        self.center_text.show_and_hide()

    def _show_video(self):
        """Show Video Output."""
        self._show_message()
        self.video_output.show()
        joysticks = self.input_options.get("joysticks")
        if joysticks:
            self.joystick.hide_sticks()
            self.joystick.show_sticks(joysticks["left"], joysticks["right"])
            self.joystick.default_pos()
        self.setFixedSize(self.width(), self.height())

    def _init_audio(self):
        self.audio_output.start(
            self.audio_device, self.rp_worker.device.session.receiver.audio_config
        )

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
        if self.rp_worker.device:
            if self.rp_worker.device.session:
                self.rp_worker.device.session.standby()

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
            self.audio_output.quit()
        self.stopped.emit()
