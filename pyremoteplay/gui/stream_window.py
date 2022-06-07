# pylint: disable=c-extension-no-member,invalid-name
"""Stream Window for GUI."""
from __future__ import annotations
import time
import logging
from typing import TYPE_CHECKING, Any

import av
from PySide6 import QtCore, QtWidgets
from PySide6.QtCore import Qt  # pylint: disable=no-name-in-module
from pyremoteplay.receiver import AVReceiver
from pyremoteplay.device import RPDevice
from pyremoteplay.const import Resolution
from pyremoteplay.gamepad import Gamepad

from .joystick import JoystickWidget
from .util import message, format_qt_key
from .video import VideoWidget, YUVGLWidget
from .widgets import FadeOutLabel
from .audio import QtAudioWorker, SoundDeviceAudioWorker

if TYPE_CHECKING:
    from .workers import RPWorker
    from .options import Options

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


class FPSLabel(QtWidgets.QLabel):
    """Fps Label."""

    def __init__(self, fps: int, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setStyleSheet("background-color:#33333333;color:white;padding-left:5px;")
        self.__generator = self.__fps_generator(fps)

    def __fps_generator(self, fps: int):
        samples = 0
        last_time = time.time()
        while True:
            samples += 1
            if samples >= fps:
                now = time.time()
                delta = now - last_time
                last_time = now
                self.setText(f"FPS: {samples/delta:.2f}")
                samples = 0
            yield

    def frameUpdated(self):
        """Update FPS times."""
        next(self.__generator)


class StreamWindow(QtWidgets.QWidget):
    """Window for stream."""

    started = QtCore.Signal(RPDevice)
    stopped = QtCore.Signal(str)
    video_frame = QtCore.Signal(object)
    audio_frame = QtCore.Signal(object)

    def __init__(
        self,
        rp_worker: RPWorker,
        device: RPDevice,
        options: Options,
        audio_device: Any,
        input_map_kb: dict,
        input_options_kb: dict,
        gamepad: Gamepad = None,
    ):
        self._rp_worker = rp_worker
        self._device = device
        self._options = options
        self._audio_device = audio_device
        self._input_map_kb = input_map_kb
        self._input_options_kb = input_options_kb
        self._gamepad = gamepad

        super().__init__()
        self.hide()
        self.setWindowTitle(f"Session {options.profile} @ {device.host}")
        self.setMaximumWidth(self.screen().virtualSize().width())
        self.setMaximumHeight(self.screen().virtualSize().height())
        self.setObjectName("stream-window")
        self.setStyleSheet("#stream-window{background-color: black}")
        self.setLayout(QtWidgets.QVBoxLayout())
        self.layout().setContentsMargins(0, 0, 0, 0)

        self._center_text = FadeOutLabel(
            "Starting Stream...", self, alignment=Qt.AlignCenter
        )

        self._audio_output = (
            QtAudioWorker() if options.use_qt_audio else SoundDeviceAudioWorker()
        )

        kwargs = {}
        video_widget = VideoWidget
        if self.options().use_opengl:
            video_widget = YUVGLWidget
            kwargs["is_nv12"] = self.options().use_hw
        resolution = Resolution.preset(self.options().resolution)
        self._video_output = video_widget(
            resolution["width"],
            resolution["height"],
            **kwargs,
        )
        self.layout().addWidget(self._video_output)

        self._joystick = JoystickWidget(self)
        self._joystick.hide()
        self._joystick.setParent(self._video_output)

        self._fps_label = FPSLabel(self.options().fps, "FPS: ")
        self._fps_label.setParent(self._video_output)
        self._fps_label.hide()

        self._rp_worker.started.connect(self._show_video)
        self._rp_worker.finished.connect(self.close)

        self.resize(
            resolution["width"],
            resolution["height"],
        )

    def options(self) -> Options:
        """Return Options."""
        return self._options

    def start(self):
        """Start Session."""
        receiver = self._setup_receiver()
        codec = self.options().codec
        if not self.options().use_hw:
            codec = codec.split("_")[0]
        hdr = self.options().hdr
        user = self.options().profile
        self.device.create_session(
            user,
            receiver=receiver,
            loop=self._rp_worker.loop,
            resolution=self.options().resolution,
            fps=self.options().fps,
            quality=self.options().quality,
            codec=codec,
            hdr=hdr,
        )
        if self._gamepad:
            self._gamepad.controller = self.device.controller
        self.device.session.events.on("audio_config", self._init_audio)
        self._video_output.hide()

        if self.options().show_fps:
            self._video_output.frame_updated.connect(self._fps_label.frameUpdated)
            self._fps_label.move(20, 20)
            self._fps_label.resize(80, 20)
            self._fps_label.show()

        self.started.emit(self.device)
        if self.options().fullscreen:
            self.showFullScreen()
        else:
            self.show()
        self._center_text.move(0, self.contentsRect().center().y())

    def move_stick(self, stick: str, point: QtCore.QPointF):
        """Move Stick."""
        self._rp_worker.send_stick(self.device, stick, point)

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

    def resizeEvent(self, event):
        """Resize Event."""
        super().resizeEvent(event)
        self._center_text.setFixedSize(self.size().width(), 100)

    def closeEvent(self, event):
        """Close Event."""
        self.hide()
        self._cleanup()
        self.deleteLater()
        event.accept()

    def fullscreen(self) -> bool:
        """Return True if full screen."""
        return self.options().fullscreen

    def _setup_receiver(self) -> QtReceiver:
        """Setup Receiver."""
        receiver = QtReceiver()
        video_format = "rgb24"
        if self.options().use_opengl:
            video_format = "nv12" if self.options().use_hw else "yuv420p"
        receiver.video_format = video_format
        _LOGGER.info("Using video format: %s", video_format)
        receiver.set_signals(self.video_frame, self.audio_frame)
        self.audio_frame.connect(
            self._audio_output.next_audio_frame, Qt.QueuedConnection
        )
        self.video_frame.connect(
            self._video_output.next_video_frame, Qt.QueuedConnection
        )
        return receiver

    def _show_message(self):
        key_name = ""
        for key, button in self._input_map_kb.items():
            if button == "QUIT":
                key_name = format_qt_key(key)
                break
        self._center_text.hide()
        self._center_text.move(0, 0)
        self._center_text.setText(f"Press {key_name} to quit.")
        self._center_text.show_and_hide()

    def _show_video(self):
        """Show Video Output."""
        self._show_message()
        self._video_output.show()
        show_left = show_right = True
        try:
            joysticks = self._input_options_kb["joysticks"]
            show_left = joysticks["left"]
            show_right = joysticks["right"]
        except KeyError:
            show_left = show_right = True

        self._joystick.hide_sticks()
        self._joystick.show_sticks(show_left, show_right)
        self._joystick.default_pos()
        self.setFixedSize(self.width(), self.height())

    def _init_audio(self):
        self._audio_output.start(
            self._audio_device, self.device.session.receiver.audio_config
        )

    def _point_from_stick_button(
        self, stick_button: str, pressed: bool
    ) -> tuple[str, QtCore.QPointF]:
        stick_button = stick_button.split("_")
        stick = stick_button[1]
        direction = stick_button[2]
        value = 1.0 if pressed else 0.0
        point = QtCore.QPointF(0.0, 0.0)
        if direction in ("UP", "LEFT") and value != 0.0:
            value *= -1.0

        if direction in ("LEFT", "RIGHT"):
            point.setX(value)
        else:
            point.setY(value)
        return stick, point

    def _handle_press(self, key):
        button = self._input_map_kb.get(key)
        if button is None:
            return
        if button == "QUIT":
            self._rp_worker.stop()
            return
        if button == "STANDBY":
            message(
                self,
                "Standby",
                "Set host to standby?",
                level="info",
                callback=self._standby,
                escape=True,
            )
            return
        if "STICK" in button:
            stick, point = self._point_from_stick_button(button, True)
            self._rp_worker.send_stick(self.device, stick, point)
        else:
            self._rp_worker.send_button(self.device, button, "press")

    def _handle_release(self, key):
        button = self._input_map_kb.get(key)
        if button is None:
            _LOGGER.debug("Button Invalid: %s", key)
            return
        if button in ["QUIT", "STANDBY"]:
            return
        if "STICK" in button:
            stick, point = self._point_from_stick_button(button, False)
            self._rp_worker.send_stick(self.device, stick, point)
        else:
            self._rp_worker.send_button(self.device, button, "release")

    def _standby(self):
        """Place host in standby."""
        if self.device:
            if self.device.session:
                self.device.session.standby()

    def _cleanup(self):
        """Cleanup session."""
        _LOGGER.debug("Cleaning up window")
        if self._video_output:
            self._video_output.deleteLater()
            self._video_output = None
        if self._audio_output:
            self._audio_output.quit()
        if self._gamepad:
            self._gamepad.controller = None
            self._gamepad = None
        error = ""
        if self.device and self.device.session:
            error = self.device.session.error
            self.device.disconnect()
        self.stopped.emit(error)

    @property
    def device(self) -> RPDevice:
        """Return Device."""
        return self._device
