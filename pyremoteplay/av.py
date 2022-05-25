"""AV for pyremoteplay."""
from __future__ import annotations
import logging
import time
from collections import deque

from .stream_packets import AVPacket, Packet
from .const import FFMPEG_PADDING

_LOGGER = logging.getLogger(__name__)


class AVHandler:
    """AV Handler."""

    def __del__(self):
        self._queue.clear()

    def __init__(self, session):
        self._session = session
        self._receiver = None
        self._v_stream = None
        self._a_stream = None
        self._cipher = None
        self._queue = deque(maxlen=5000)
        self._worker = None
        self._last_congestion = 0
        self._waiting = False

    def add_receiver(self, receiver):
        """Add AV reciever and run."""
        if self._receiver:
            raise RuntimeError("Cannot add Receiver more than once")
        if receiver is not None:
            self._receiver = receiver
            self._receiver.set_session(self._session)
            self._receiver.get_video_codec()

    def set_cipher(self, cipher):
        """Set cipher. Schedules handler to run."""
        self._cipher = cipher
        self._session.events.emit("av_ready")

    def set_headers(self, v_header, a_header):
        """Set headers."""
        if self._receiver:
            self._v_stream = AVStream(
                "video", v_header, self._receiver.handle_video_data
            )
            self._a_stream = AVStream(
                "audio", a_header, self._receiver.handle_audio_data
            )
            self._receiver.get_audio_config(a_header)

    def add_packet(self, packet):
        """Add Packet."""
        packet = Packet.parse(packet)
        if len(self._queue) >= self._queue.maxlen:
            self._queue.clear()
            self._waiting = True
            _LOGGER.warning("AV Handler max queue size exceeded")
            self._session.error = (
                "Decoder could not keep up. Try lowering framerate / resolution"
            )
            self._session.stop()
            return
        if self._waiting and packet.unit_index == 0:
            self._waiting = False
        if not self._waiting:
            self._queue.append(packet)

    def process_packet(self):
        """Process AV Packet."""
        try:
            packet = self._queue.popleft()
        except IndexError:
            time.sleep(0.0001)
            return
        packet.decrypt(self._cipher)
        self._handle(packet)
        # self._send_congestion()

    def worker(self):
        """Worker for AV Handler. Run in thread."""
        while not self._session.is_stopped:
            self.process_packet()
        _LOGGER.debug("Closing AV Receiver")
        self._receiver.close()
        self._queue.clear()
        _LOGGER.debug("AV Receiver Closed")

    def _send_congestion(self):
        now = time.time()
        if now - self._last_congestion > 0.2:
            self._session.stream.send_congestion(self.received, self.lost)
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


class AVStream:
    """AV Stream."""

    TYPE_VIDEO = "video"
    TYPE_AUDIO = "audio"

    def __init__(self, av_type: str, header: bytes, callback: callable):
        self._type = av_type
        self._callback = callback
        self._header = header
        self._packets = []
        self._frame = -1
        self._last_unit = -1
        self._lost = 0
        self._received = 0
        self._last_index = -1
        self._frame_bad_order = False
        self._missing = []

        if self._type not in [AVStream.TYPE_VIDEO, AVStream.TYPE_AUDIO]:
            raise ValueError("Invalid Type")
        if av_type == AVStream.TYPE_VIDEO:
            self._header = b"".join([self._header, bytes(FFMPEG_PADDING)])

    def _set_new_frame(self, packet: AVPacket):
        self._frame_bad_order = False
        self._missing = []
        self._packets = []
        self._frame = packet.frame_index
        self._last_unit = -1
        _LOGGER.debug("Started New Frame: %s", self.frame)

    def _handle_missing_packet(self, index: int, unit_index: int):
        """Mark missing unit indexes as missing. Write null placeholder bytes for each."""
        if not self._frame_bad_order:
            _LOGGER.warning(
                "Received unit out of order: %s, expected: %s",
                unit_index,
                self.last_unit + 1,
            )
            self._frame_bad_order = True

        _range = range(self.last_unit + 1, unit_index)
        for _ in _range:
            self._packets.append(b"")
        self._missing.extend(range(self.last_unit + 1, unit_index))
        if self._lost > 65535:
            self._lost = 0
        self._lost += index - self._last_index - 1
        if self._lost > 65535:
            self._lost = 1
        self._last_unit = unit_index - 1

    def _handle_src_packet(self, packet: AVPacket):
        if packet.is_last_src and not self._frame_bad_order:
            if len(self._packets) < packet.frame_length_src:
                _LOGGER.error("Frame buffer missing packets")
                return
            self._callback(
                self._header
                + b"".join(
                    [packet[2:] for packet in self._packets[: packet.frame_length_src]]
                )
            )

    def _handle_fec_packet(self, packet: AVPacket):
        try:
            import pyjerasure  # pylint: disable=import-outside-toplevel
        except ModuleNotFoundError:
            pass
        if not self._frame_bad_order and not self._missing:
            # Ignore FEC packets if all src packets received.
            return
        if packet.is_last:
            if len(self._missing) <= packet.frame_length_fec:
                matrix = pyjerasure.Matrix(
                    "cauchy", packet.frame_length_src, packet.frame_length_fec, 8
                )
                restored = b""
                packets = self._packets
                max_size = max([len(packet) for packet in packets])
                size = pyjerasure.align_size(matrix, max_size)
                missing = tuple(self._missing)
                buf = b"".join([packet.ljust(size, b"\x00") for packet in packets])
                _LOGGER.debug("Attempting FEC Decode")
                try:
                    restored = pyjerasure.decode_from_bytes(
                        matrix,
                        buf,
                        missing,
                        size,
                    )
                except Exception as err:  # pylint: disable=broad-except
                    _LOGGER.error(err)
                    return
                if restored:
                    _LOGGER.debug("FEC Successful")
                    for index in missing:
                        packets[index] = restored[
                            size * index : size * (index + 1)
                        ].rstrip(b"\x00")
                    self._callback(
                        self._header
                        + b"".join(
                            [
                                packet[2:]
                                for packet in packets[: packet.frame_length_src]
                            ]
                        )
                    )
                else:
                    _LOGGER.warning("FEC Failed")

    def handle(self, packet: AVPacket):
        """Handle Packet."""
        if self._received > 65535:
            self._received = 0
        self._received += 1
        if self._received > 65535:
            self._received = 1

        # Audio frames are sent in one packet.
        if self._type == AVStream.TYPE_AUDIO:
            self._callback(packet.data[: packet.frame_size_audio])
            return

        # New Video Frame.
        if packet.frame_index != self.frame:
            self._set_new_frame(packet)

        # Check if packet is in order
        if packet.unit_index != self.last_unit + 1:
            self._handle_missing_packet(packet.index, packet.unit_index)

        self._last_unit += 1
        # First two decrypted bytes is the difference of the unit size and the data size.
        self._packets.append(packet.data)

        # Current Frame is src.
        if not packet.is_fec:
            self._handle_src_packet(packet)
        else:
            self._handle_fec_packet(packet)

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
