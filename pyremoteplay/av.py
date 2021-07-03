"""AV for pyremoteplay."""
import abc
import errno
import logging
import multiprocessing
import queue
import threading
import time
from io import BytesIO

import ffmpeg

from .stream_packets import AVPacket, Packet
from .util import log_bytes

_LOGGER = logging.getLogger(__name__)


class AVHandler():
    """AV Handler."""

    def __init__(self, ctrl):
        self._ctrl = ctrl
        self._receiver = None
        self._v_stream = None
        self._a_stream = None
        self._queue = queue.Queue()
        self._worker = None

    def add_receiver(self, receiver):
        if self._receiver:
            raise RuntimeError("Cannot add Receiver more than once")
        if receiver is not None:
            self._receiver = receiver
            self._receiver.start()

    def set_cipher(self, cipher):
        self._cipher = cipher
        self._worker = threading.Thread(
            target=self.worker,
        )
        self._worker.start()

    def set_headers(self, v_header, a_header):
        """Set headers."""
        if self._receiver:
            self._v_stream = AVStream(v_header, self._receiver.handle)
            self._a_stream = AVStream(a_header, self._receiver.handle)

    def add_packet(self, packet):
        """Add Packet."""
        self._queue.put(packet)

    def worker(self):
        while not self._ctrl._stop_event.is_set():
            try:
                msg = self._queue.get(timeout=1)
            except queue.Empty:
                time.sleep(0.01)
                continue
            packet = Packet.parse(msg)
            packet.decrypt(self._cipher)
            self._handle(packet)
        self._receiver.close()

    def _handle(self, packet: AVPacket):
        _LOGGER.debug(packet)
        if not self._receiver:
            return
        if packet.type == AVPacket.Type.VIDEO:
            stream = self._v_stream
        else:
            return
            #stream = self._a_stream
        stream.handle(packet)

    @property
    def has_receiver(self) -> bool:
        """Return True if receiver is not None."""
        return self._receiver is not None


class AVStream():
    """AV Stream."""

    def __init__(self, header: bytes, callback: callable):
        self._callback = callback
        self._header = header
        self._buf = BytesIO()
        self._frame = -1
        self._last_unit = -1

    def handle(self, packet: AVPacket):
        """Handle Packet."""
        # New Frame
        if packet.frame_index != self.frame:
            # First packet of Frame
            if packet.unit_index == 0:
                self._frame = packet.frame_index
                self._last_unit = -1
                self._buf = BytesIO()
                self._buf.write(self._header)
                _LOGGER.debug("Started New Frame: %s", self.frame)
        # Current Frame not FEC
        if not packet.is_fec:
            # Packet is in order
            if packet.unit_index == self.last_unit + 1:
                self._last_unit += 1
                self._buf.write(packet.data)
            else:
                _LOGGER.debug("Received unit out of order: %s, expected: %s", packet.unit_index, self.last_unit + 1)
            if packet.is_last_src:
                _LOGGER.debug("Frame: %s finished with length: %s", self.frame, self._buf.tell())
                self._callback(self._buf)

    @property
    def frame(self) -> int:
        """Return Current Frame Index."""
        return self._frame

    @property
    def last_unit(self) -> int:
        """Return last unit."""
        return self._last_unit


class AVReceiver(abc.ABC):
    """Base Class for AV Receiver."""

    def __init__(self, ctrl):
        self._ctrl = ctrl

    def notify_started(self):
        self._ctrl.receiver_started.set()

    def handle_video(self, buf: BytesIO):
        """Handle video frame."""
        raise NotImplementedError

    def handle_audio(self, buf: BytesIO):
        """Handle audio frame."""
        raise NotImplementedError

    def close(self):
        """Close Receiver."""
        raise NotImplementedError


class AVFileReceiver(AVReceiver):
    """Writes AV to file."""
    def process(pipe, av_file):
        log = open("avfr.log", "w")
        log.write("AV File Receiver started\n")

        video = ffmpeg.input('pipe:', format='h264')
        # audio = ffmpeg.input('pipe:', format='ogg')
        process = (
            ffmpeg
            .concat(video)
            .output(av_file, format='mp4', pix_fmt='yuv420p')
            .overwrite_output()
            .run_async(pipe_stdin=True)
        )

        frame = 0

        if process.poll() is None:
            _LOGGER.info("FFMPEG Started")
            pipe.send(1)
        last = time.time()
        while True:
            try:
                data = pipe.recv_bytes()
                written = process.stdin.write(data)
                frame += 1
                _LOGGER.debug(f"File Receiver wrote: Frame {frame} {written} bytes\n")
                log.write(f"File Receiver wrote: Frame {frame} {written} bytes\n")
                now = time.time()
                _LOGGER.debug("Receiver FPS: %s", 1 / (now - last))
                last = now
            except KeyboardInterrupt:
                break
            except Exception as err:
                log.write(f"{err}\n")
                break
        process.stdin.write(b'')
        process.stdin.close()
        pipe.close()
        process.wait()
        log.write("AV File Receiver stopping\n")
        log.close()

    def __init__(self, ctrl):
        super().__init__(ctrl)
        self._ctrl = ctrl
        self._worker = None
        self.file = None
        self.v_queue = queue.Queue()
        self._pipe = None

    def start(self):
        _LOGGER.debug("File Receiver Starting")
        if self.file is None:
            self.file = "rp_output.mp4"
        recv_pipe, self._pipe = multiprocessing.Pipe()
        self._worker = multiprocessing.Process(
            target=AVFileReceiver.process,
            args=(recv_pipe, self.file),
            daemon=True,
        )
        self._worker.start()
        status = self._pipe.recv()
        if status == 1:
            _LOGGER.info("File Receiver started")
            self.notify_started()

    def handle(self, buf: BytesIO):
        """Handle frame."""
        try:
            self._pipe.send_bytes(buf.getvalue())
        except Exception as error:
            if error.errno != errno.EPIPE and self._ctrl.state != self._ctrl.STATE_STOP:
                _LOGGER.error("File Receiver error: %s", error)
                _LOGGER.info("File Receiver closing pipe")
                self._pipe.close()
                self._ctrl.stop()

    def close(self):
        if self._worker:
            self._worker.terminate()
            self._worker.close()
