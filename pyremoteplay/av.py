"""AV for pyremoteplay."""
import abc
import errno
import logging
import multiprocessing
import queue
import threading
import time
from io import BytesIO
from struct import unpack_from
import socket

import ffmpeg
from opuslib import Decoder

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
        self._last_congestion = 0

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
            self._v_stream = AVStream("video", v_header, self._receiver.handle_video)
            self._a_stream = AVStream("audio", a_header, self._receiver.handle_audio)

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
            #self._send_congestion()
        self._receiver.close()

    def _send_congestion(self):
        now = time.time()
        if now - self._last_congestion > 0.2:
            self._ctrl._stream.send_congestion(self.received, self.lost)
            self._last_congestion = now

    def _handle(self, packet: AVPacket):
        _LOGGER.debug(packet)
        if not self._receiver:
            return
        if packet.type == AVPacket.Type.VIDEO:
            stream = self._v_stream
        else:
            stream = self._a_stream
        stream.handle(packet)

    @property
    def has_receiver(self) -> bool:
        """Return True if receiver is not None."""
        return self._receiver is not None

    @property
    def lost(self) -> int:
        """Return Total AV packets lost."""
        return self._v_stream.lost + self._a_stream.lost

    @property
    def received(self) -> int:
        """Return Total AV packets received."""
        return self._v_stream.received + self._a_stream.received


class AVStream():
    """AV Stream."""

    TYPE_VIDEO = "video"
    TYPE_AUDIO = "audio"

    def __init__(self, av_type: str, header: bytes, callback: callable):
        self._type = av_type
        self._callback = callback
        self._header = header
        self._buf = BytesIO()
        self._frame = -1
        self._last_unit = -1
        self._lost = 0
        self._received = 0
        self._last_index = -1

        if self._type not in [AVStream.TYPE_VIDEO, AVStream.TYPE_AUDIO]:
            raise ValueError("Invalid Type")
        if self._type == AVStream.TYPE_AUDIO:
            self._audio_config = {}
            self._audio_decoder = None
            self.get_audio_config()

    def get_audio_config(self):
        """Get Audio config from header."""
        if self._type != AVStream.TYPE_AUDIO:
            raise RuntimeError("Type is not Audio")
        self._audio_config = {
            "channels": self._header[0],
            "bits": self._header[1],
            "rate": unpack_from("!I", self._header, 2)[0],
            "frame_size": unpack_from("!I", self._header, 6)[0],
            "unknown": unpack_from("!I", self._header, 10)[0],
        }
        _LOGGER.info("Audio Config: %s", self._audio_config)
        self._audio_decoder = Decoder(self._audio_config['rate'], self._audio_config['channels'])

    def audio_decode(self, packet: AVPacket):
        if not self._audio_config:
            raise RuntimeError("Audio Config not set")

        assert len(packet.data) % packet.frame_size_audio == 0
        buf = bytearray()
        for count in range(0, packet.frame_length_src):
            start = count * packet.frame_size_audio
            end = (count + 1) * packet.frame_size_audio
            buf.extend(self._audio_decoder.decode(packet.data[start:end], self._audio_config['frame_size']))
        return buf

    def handle(self, packet: AVPacket):
        """Handle Packet."""
        if self._received > 65535:
            self._received = 0
        self._received += 1
        if self._received > 65535:
            self._received = 1

        if self._type == AVStream.TYPE_AUDIO:
            data = self.audio_decode(packet)
            self._callback(data)
            return
        # New Video Frame
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
                if self._lost > 65535:
                    self._lost = 0
                self._lost += (packet.index - self._last_index - 1)
                if self._lost > 65535:
                    self._lost = 1
            if packet.is_last_src:
                _LOGGER.debug("Frame: %s finished with length: %s", self.frame, self._buf.tell())
                self._callback(self._buf.getvalue())

    @property
    def frame(self) -> int:
        """Return Current Frame Index."""
        return self._frame

    @property
    def last_unit(self) -> int:
        """Return last unit."""
        return self._last_unit

    @property
    def lost(self) -> int:
        """Return Total AV packets lost."""
        return self._lost

    @property
    def received(self) -> int:
        """Return Total AV packets received."""
        return self._received


class AVReceiver(abc.ABC):
    """Base Class for AV Receiver."""

    def __init__(self, ctrl):
        self._ctrl = ctrl

    def notify_started(self):
        self._ctrl.receiver_started.set()

    def handle_video(self, data: bytes):
        """Handle video frame."""
        raise NotImplementedError

    def handle_audio(self, data: bytes):
        """Handle audio frame."""
        raise NotImplementedError

    def close(self):
        """Close Receiver."""
        raise NotImplementedError


class QueueReceiver(AVReceiver):
    def __init__(self, ctrl):
        super().__init__(ctrl)
        self.a_cb = None
        self.v_queue = queue.Queue()
        
    def add_audio_cb(self, cb):
    	self.a_cb = cb

    def start(self):
    	self.notify_started()
    	
    def close(self):
    	pass
    	
    def handle_video(self, buf):
        self.v_queue.put(buf)

    def handle_audio(self, buf):
        if self.a_cb is not None:
            self.a_cb(buf)
        


class AVFileReceiver(AVReceiver):
    """Writes AV to file."""
    def process(pipe, av_file):
        video = ffmpeg.input('pipe:', format='h264')
        audio = ffmpeg.input('pipe:', format='s16le', ac=2, ar=48000)
        #joined = ffmpeg.concat(video, audio, v=1, a=1).node
        #outputs = ffmpeg.merge_outputs(video, audio)
        process = (
            ffmpeg
            .output(video, audio, av_file, format='mp4', pix_fmt='yuv420p')
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
                now = time.time()
                _LOGGER.debug("Receiver FPS: %s", 1 / (now - last))
                last = now
            except KeyboardInterrupt:
                break
            except Exception as err:
                _LOGGER.error(err)
                break
        process.stdin.write(b'')
        process.stdin.close()
        pipe.close()
        process.wait()

    def __init__(self, ctrl):
        super().__init__(ctrl)
        self._ctrl = ctrl
        self._worker = None
        self._pipe = None
        self.file = None
        self.v_queue = queue.Queue()

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

    def handle_video(self, data: bytes):
        """Handle Video frame."""
        self.send_process(data)

    def handle_audio(self, data: bytes):
        self.send_process(data)

    def send_process(self, data):
        try:
            self._pipe.send_bytes(data)
        except Exception as error:
            if error.errno != errno.EPIPE and not self._ctrl.is_running:
                _LOGGER.error("File Receiver error: %s", error)
                _LOGGER.info("File Receiver closing pipe")
                self._pipe.close()
                self._ctrl.stop()

    def close(self):
        if self._worker:
            self._worker.terminate()
            self._worker.join()
            self._worker.close()
