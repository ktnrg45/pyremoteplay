# pylint: disable=c-extension-no-member,invalid-name
"""Stream Window for GUI."""
import time
import logging
from collections import deque
import asyncio

import av
import sounddevice
from PySide6 import QtCore, QtMultimedia, QtWidgets
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


class QtAudioThread(QtCore.QThread):
    """Thread for audio using QT."""

    def __init__(self):
        super().__init__()
        self.audio_output = None
        self.audio_buffer = None
        self.device = None
        self.config = {}
        self.started.connect(self._init_audio)

    def start(self, device, config):
        """Start thread."""
        self.device = device
        self.config = config
        super().start()

    def _init_audio(self):
        config = self.config
        if not config:
            return
        if self.audio_buffer or self.audio_output:
            return
        audio_format = QtMultimedia.QAudioFormat()
        audio_format.setChannelCount(config["channels"])
        audio_format.setSampleRate(config["rate"])
        audio_format.setSampleFormat(QtMultimedia.QAudioFormat.Int16)

        bytes_per_second = audio_format.bytesForDuration(1000 * 1000)
        interval = int(1 / (bytes_per_second / config["packet_size"]) * 1000)
        _LOGGER.debug(interval)

        self.audio_output = QtMultimedia.QAudioSink(self.device, format=audio_format)
        self.audio_output.setBufferSize(config["packet_size"] * 4)
        self.audio_buffer = self.audio_output.start()
        _LOGGER.debug("Audio Thread init")

    def next_audio_frame(self, frame):
        """Handle next audio frame."""
        if self.audio_buffer:
            buf = bytes(frame.planes[0])[: self.config["packet_size"]]
            self.audio_buffer.write(buf)

    def quit(self):
        """Quit Thread."""
        self.audio_buffer = None
        if self.audio_output:
            self.audio_output.stop()
        super().quit()


class AudioThread(QtCore.QThread):
    """Thread for audio using sounddevice."""

    def __init__(self):
        super().__init__()
        self.audio_output = None
        self.queue = deque(maxlen=5)
        self.device = None
        self.config = {}
        self.__blank_frame = b""
        self.started.connect(self._init_audio)

    def start(self, device, config):
        """Start thread."""
        self.device = device.get("index")
        self.config = config
        super().start()

    def _init_audio(self):
        config = self.config
        if not config:
            return
        self.audio_output = sounddevice.RawOutputStream(
            samplerate=config["rate"],
            blocksize=config["frame_size"],
            channels=config["channels"],
            dtype=f"int{config['bits']}",
            latency="low",
            dither_off=True,
            callback=self.callback,
            device=self.device,
        )
        self.__blank_frame = bytes(config["packet_size"])
        self.audio_output.start()
        _LOGGER.debug("Audio Thread init")

    def next_audio_frame(self, frame):
        """Handle next audio frame."""
        self.queue.append(bytes(frame.planes[0])[: self.config["packet_size"]])

    # pylint: disable=unused-argument
    def callback(self, buf, frames, _time, status):
        """Callback to write new frames."""
        try:
            data = self.queue.popleft()
        except IndexError:
            data = self.__blank_frame
        buf[:] = data

    def quit(self):
        """Quit Thread."""
        if self.audio_output:
            self.audio_output.stop()
        super().quit()


class RPWorker(QtCore.QObject):
    """Worker to interface with RP Session."""

    finished = QtCore.Signal()
    started = QtCore.Signal()
    standby_done = QtCore.Signal(str)

    def __init__(self):
        super().__init__()
        self._loop = None
        self.device = None
        self.error = ""

    def setLoop(self, loop: asyncio.AbstractEventLoop):
        """Set Loop."""
        self._loop = loop

    def run(self, standby=False):
        """Run Session."""
        if not self.device:
            _LOGGER.warning("No Device")
            self.stop()
            return
        if not self.device.session:
            _LOGGER.warning("No Session")
            self.stop()
            return

        self.device.session.events.on("stop", self.stop)
        self._loop.create_task(self.start(standby))

    def stop(self, standby=False):
        """Stop session."""
        if self.device and self.device.session:
            self.error = self.device.session.error
            _LOGGER.info("Stopping Session @ %s", self.device.host)
            self.device.disconnect()
            if standby:
                self.standby_done.emit(self.error)
        self.device = None
        self.finished.emit()

    def setup(
        self,
        device: RPDevice,
        user: str,
        options: dict,
        receiver: QtReceiver,
    ):
        """Setup session."""
        self.device = device
        codec = options.get("codec")
        if not options.get("use_hw"):
            codec = codec.split("_")[0]
        hdr = options.get("hdr")
        if hdr and codec == "hevc":
            codec = "hevc_hdr"

        self.device.create_session(
            user,
            resolution=options.get("resolution"),
            fps=options.get("fps"),
            receiver=receiver,
            codec=codec,
            quality=options.get("quality"),
            loop=self._loop,
        )

    async def start(self, standby=False):
        """Start Session."""
        _LOGGER.debug("Session Start")
        if standby:
            self.device.session.receiver = None
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

        self.device.controller.start()
        self.started.emit()

        if self.device.session.stop_event:
            await self.device.session.stop_event.wait()
            _LOGGER.info("Session Finished")

    def stick_state(
        self, stick: str, direction: str = None, value: float = None, point=None
    ):
        """Send stick state"""
        if point is not None:
            self.device.controller.stick(stick, point=point)
            return

        if direction in ("LEFT", "RIGHT"):
            axis = "X"
        else:
            axis = "Y"
        if direction in ("UP", "LEFT") and value != 0.0:
            value *= -1.0
        self.device.controller.stick(stick, axis, value)

    def send_button(self, button, action):
        """Send button."""
        self.device.controller.button(button, action)


class StreamWindow(QtWidgets.QWidget):
    """Window for stream."""

    started = QtCore.Signal()
    video_frame = QtCore.Signal(object)
    audio_frame = QtCore.Signal(object)

    def __init__(self, main_window):
        self.mapping = None
        self.fps = None
        self.fullscreen = False
        self.use_opengl = False
        self.show_fps = False
        self.video_output = None
        self.audio_device = None
        self.audio_thread = None
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
            self.audio_thread.next_audio_frame, Qt.QueuedConnection
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
            self.audio_thread = QtAudioThread()
        else:
            self.audio_thread = AudioThread()
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
        self.audio_thread.start(
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
        if self.audio_thread:
            self.audio_thread.quit()
        self.main_window.session_stop()
