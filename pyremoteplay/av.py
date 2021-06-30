"""AV for pyremoteplay."""
import abc
import errno
import fractions
import logging
import multiprocessing
import queue
import threading
import time
from io import BytesIO

import av
import ffmpeg

from .stream_packets import AVPacket, Packet
from .util import from_b, log_bytes, to_b

_LOGGER = logging.getLogger(__name__)


class AVHandler():
    """AV Handler."""

    def __init__(self, ctrl):
        self._ctrl = ctrl
        self._receiver = None
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
        self._receiver.close()

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


class AVReceiver(abc.ABC):
    """Base Class for AV Receiver."""

    def __init__(self, ctrl):
        self._ctrl = ctrl
        self.encoder = None
        self.decoder = None

    def get_codecs(self):
        """Get Codec Contexts."""
        dec_codec = av.CodecContext.create("h264", "r")
        enc_codec = av.CodecContext.create("libx264", "w")
        dec_codec.width = enc_codec.width = self._ctrl.resolution["width"]
        enc_codec.height = enc_codec.height = self._ctrl.resolution["height"]
        dec_codec.bit_rate = enc_codec.bit_rate = self._ctrl.resolution["bitrate"]
        dec_codec.pix_fmt = enc_codec.pix_fmt = "yuv420p"
        dec_codec.framerate = enc_codec.framerate = fractions.Fraction(self._ctrl.fps, 1)
        dec_codec.time_base = enc_codec.time_base = fractions.Fraction(1, self._ctrl.fps)
        enc_codec.options = {
            "profile": "baseline",
            "level": "31",
            "tune": "zerolatency",
        }
        enc_codec.open()
        dec_codec.open()
        self.encoder = enc_codec
        self.decoder = dec_codec

    def handle_video(self, frame: bytes):
        """Handle video frame."""
        raise NotImplementedError

    def close(self):
        """Close Receiver."""
        raise NotImplementedError


class AVFileReceiver(AVReceiver):
    """Writes AV to file."""
    def process(pipe, av_file):
        log = open("avfr.log", "w")
        log.write("AV File Receiver started\n")
        output = open(av_file, "wb")
        frame = 0
        while True:
            try:
                data = pipe.recv_bytes()
                written = output.write(data)
                frame += 1
                log.write(f"File Receiver wrote: Frame {frame} {written} bytes\n")
            except KeyboardInterrupt:
                break
            except Exception as err:
                log.write(f"{err}\n")
                break
        pipe.close()
        log.write("AV File Receiver stopping\n")
        log.close()

    def __init__(self, ctrl, av_file="rp_output.h264"):
        super().__init__(ctrl)
        self._ctrl = ctrl
        self._worker = None
        self.file = av_file
        self.v_queue = queue.Queue()
        self._send_pipe = None

    def start(self):
        _LOGGER.debug("File Receiver Starting")
        if self.file is None:
            self.file = "rp_output.h264"
        recv_pipe, send_pipe = multiprocessing.Pipe(duplex=False)
        self._send_pipe = send_pipe
        self._worker = multiprocessing.Process(
            target=AVFileReceiver.process,
            args=(recv_pipe, self.file),
        )
        self._worker.start()

    def handle_video(self, buf):
        try:
            self._send_pipe.send_bytes(buf.getvalue())
        except Exception as error:
            if error.errno != errno.EPIPE and self._ctrl.state != self._ctrl.STATE_STOP:
                _LOGGER.error("File Receiver error: %s", error)
                _LOGGER.info("File Receiver closing pipe")
                self._send_pipe.close()
                self._ctrl.stop()

    def worker(self):
        _LOGGER.debug("File Receiver Started")
        output = open(self.file, 'wb')
        while not self._ctrl._stop_event.is_set():
            try:
                buf = self.v_queue.get(timeout=1)
            except queue.Empty:
                time.sleep(0.01)
                continue
            frame = buf.getvalue()
            output.write(frame)
            # packet = av.packet.Packet(len(frame))
            # packet.update(frame)
            # pts += 1
            # packet.pts = pts
            # packet.time_base = self.decoder.time_base
            # frames = self.decoder.decode(packet)
            # packets = []
            # for frame in frames:
            #     packets.extend(self.encoder.encode(frame))
            #     _LOGGER.debug(frame)
            # for packet in packets:
            #     packet.pts = pts
            #     packet.time_base = self.decoder.time_base
            #     output.write(packet.to_bytes())
            #     _LOGGER.debug(packet)

            #output.mux(packet)

        # flush
        # packet = stream.encode(None)
        # output.mux(packet)
        while True:
            try:
                buf = self.v_queue.get(timeout=1)
            except queue.Empty:
                break
            frame = buf.getvalue()
            output.write(frame)
        output.close()

    def close(self):
        pass
