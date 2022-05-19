# pylint: disable=c-extension-no-member,invalid-name
"""Audio Workers."""
from collections import deque
import logging
import sounddevice
from PySide6 import QtCore, QtMultimedia
import av

_LOGGER = logging.getLogger(__name__)


class AbstractAudioWorker(QtCore.QObject):
    """Abstract Worker for Audio."""

    def __init__(self):
        super().__init__()
        self._output = None
        self._buffer = None
        self._device = None
        self._config = {}

        self._thread = QtCore.QThread()
        self.moveToThread(self._thread)
        self._thread.started.connect(self._init_audio)

    def setConfig(self, config: dict):
        """Set Config."""
        self._config = config

    def setDevice(self, device):
        """Set Device."""
        self._device = device

    def start(self, device, config: dict):
        """Start worker."""
        self.setConfig(config)
        self.setDevice(device)
        self._thread.start(QtCore.QThread.TimeCriticalPriority)

    def _init_audio(self):
        raise NotImplementedError

    @QtCore.Slot(av.AudioFrame)
    def next_audio_frame(self, frame: av.AudioFrame):
        """Handle next audio frame."""
        buf = bytes(frame.planes[0])[: self._config["packet_size"]]
        self._send_audio(buf)

    def _send_audio(self, buf: bytes):
        raise NotImplementedError

    def quit(self):
        """Quit Worker."""
        if self._output:
            self._output.stop()
        self._buffer = None
        self._thread.quit()


class QtAudioWorker(AbstractAudioWorker):
    """Worker for audio using QT."""

    def _init_audio(self):
        config = self._config
        if not config:
            return
        if self._buffer or self._output:
            return
        audio_format = QtMultimedia.QAudioFormat()
        audio_format.setChannelCount(config["channels"])
        audio_format.setSampleRate(config["rate"])
        audio_format.setSampleFormat(QtMultimedia.QAudioFormat.Int16)

        # bytes_per_second = audio_format.bytesForDuration(1000 * 1000)
        # interval = int(1 / (bytes_per_second / config["packet_size"]) * 1000)
        # _LOGGER.debug(interval)

        self._output = QtMultimedia.QAudioSink(self._device, format=audio_format)
        self._output.setBufferSize(config["packet_size"] * 4)
        self._buffer = self._output.start()
        _LOGGER.debug("Audio Worker init")

    def _send_audio(self, buf: bytes):
        if self._buffer is not None:
            self._buffer.write(buf)


class SoundDeviceAudioWorker(AbstractAudioWorker):
    """Worker for audio using sounddevice."""

    def setDevice(self, device):
        """Set Device."""
        self._device = device.get("index")

    def _init_audio(self):
        config = self._config
        if not config:
            return
        self._output = sounddevice.RawOutputStream(
            samplerate=config["rate"],
            blocksize=config["frame_size"],
            channels=config["channels"],
            dtype=f"int{config['bits']}",
            latency="low",
            dither_off=True,
            callback=self._callback,
            device=self._device,
        )
        max_len = 5
        buf = [self.__blank_frame()] * max_len
        self._buffer = deque(buf, maxlen=max_len)

        self._output.start()
        _LOGGER.debug("Audio Worker init")

    def _send_audio(self, buf: bytes):
        if self._buffer is not None:
            self._buffer.append(buf)

    # pylint: disable=unused-argument
    def _callback(self, buf, frames, _time, status):
        """Callback to write new frames."""
        try:
            data = self._buffer.popleft()
        except IndexError:
            data = self.__blank_frame()
        buf[:] = data

    def __blank_frame(self) -> bytes:
        if self._config:
            return bytes(self._config["packet_size"])
        return bytes()
