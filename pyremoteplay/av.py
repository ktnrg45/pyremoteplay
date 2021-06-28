"""AV for pyremoteplay."""
import logging
import queue
import threading
import time
from io import BytesIO
from pathlib import Path

import av

from .stream_packets import AVPacket, Packet
from .util import from_b, log_bytes, to_b

_LOGGER = logging.getLogger(__name__)


class AVHandler():
    """AV Handler."""

    def __init__(self, ctrl):
        self._ctrl = ctrl
        self._receiver = ctrl.av_receiver
        self._v_header = b''
        self._a_header = b''
        self._v_buf = BytesIO()
        self._a_buf = BytesIO()
        self._v_frame = -1
        self._a_frame = -1
        self._last_v_unit = -1
        self._last_a_unit = -1
        self._v_lock = threading.Lock()
        self._queue = queue.Queue()
        self._worker = None

    def set_cipher(self, cipher):
        self._cipher = cipher
        self._worker = threading.Thread(
            target=self.worker,
        )
        self._worker.start()

    def set_headers(self, v_header, a_header):
        """Set headers."""
        self._v_header = v_header
        self._a_header = a_header

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
            _LOGGER.debug(packet)
            if packet.type == AVPacket.Type.VIDEO:
                self._handle_video(packet)
            else:
                self._handle_audio(packet)

    def _handle_video(self, packet: AVPacket):
        # New Frame
        if packet.frame_index != self.v_frame:
            # First packet of Frame
            if packet.unit_index == 0:
                self._v_frame = packet.frame_index
                self._last_v_unit = -1
                self._v_buf = BytesIO()
                self._v_buf.write(self._v_header)
                _LOGGER.debug("Started New Frame: %s", self._v_frame)
        # Current Frame not FEC
        if not packet.is_fec:
            # Packet is in order
            if packet.unit_index == self._last_v_unit + 1:
                self._last_v_unit += 1
                written = self._v_buf.write(packet.data)
                _LOGGER.debug("Wrote: %s. Packet Size: %s", written, len(packet.data))
            else:
                _LOGGER.info("Received unit out of order: %s, expected: %s", packet.unit_index, self._last_v_unit)
            if packet.is_last_src:
                _LOGGER.debug("Frame: %s finished with length: %s", self._v_frame, self._v_buf.tell())
                self._receiver.handle_video(self._v_buf)

    def _handle_audio(self, packet: AVPacket):
        pass

    @property
    def has_receiver(self) -> bool:
        """Return True if receiver is not None."""
        return self._receiver is not None

    @property
    def v_frame(self) -> int:
        """Return current video frame index."""
        return self._v_frame

    @property
    def a_frame(self) -> int:
        """Return current video frame index."""
        return self._a_frame


class AVReceiver():
    """Base Class for AV Receiver."""

    def handle_video(self, frame: bytes):
        """Handle video frame."""
        raise NotImplementedError

    def close(self):
        """Close Receiver."""
        raise NotImplementedError


class AVFileReceiver(AVReceiver):
    """Writes AV to file."""

    def __init__(self, ctrl, av_file=None):
        self._ctrl = ctrl
        self._worker = None
        self.file = av_file
        self.v_queue = queue.Queue()
        self.start()

    def start(self):
        if self.file is None:
            self.file = "rp_av.h264"
        self._worker = threading.Thread(
            target=self.worker,
        )
        self._worker.start()

    def handle_video(self, buf):
        self.v_queue.put(buf)

    def worker(self):
        _LOGGER.debug("File Receiver Started")
        output = av.open(self.file, 'w')
        stream = output.add_stream('h264')
        # codec = av.codec.Codec("h264")
        # ctx = codec.create()
        # ctx.width = 1920
        # ctx.height = 1080
        # ctx.bit_rate = 10000
        pts = -1
        while not self._ctrl._stop_event.is_set():
            try:
                buf = self.v_queue.get(timeout=1)
            except queue.Empty:
                time.sleep(0.01)
                continue
            frame = buf.getvalue()
            packet = av.packet.Packet(len(frame))
            packet.update(frame)
            packet.stream = stream
            pts += 1
            packet.pts = pts
            _LOGGER.debug(packet)
            output.mux(packet)

        # flush
        packet = stream.encode(None)
        output.mux(packet)

        output.close()

    def close(self):
        pass
