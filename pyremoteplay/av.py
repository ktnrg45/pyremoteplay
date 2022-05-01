"""AV for pyremoteplay."""
from __future__ import annotations
import abc
import logging
import time
from collections import deque
from struct import unpack_from
import warnings

from .stream_packets import AVPacket, Packet

try:
    import av
except ModuleNotFoundError:
    warnings.warn("av not installed")

_LOGGER = logging.getLogger(__name__)

FFMPEG_PADDING = 64  # AV_INPUT_BUFFER_PADDING_SIZE


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
            self._receiver.start()

    def set_cipher(self, cipher):
        """Set cipher. Schedules handler to run."""
        self._cipher = cipher
        self._session.init_av_handler()

    def set_headers(self, v_header, a_header):
        """Set headers."""
        if self._receiver:
            self._v_stream = AVStream("video", v_header, self._receiver.handle_video)
            self._a_stream = AVStream("audio", a_header, self._receiver.handle_audio)
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


class AVReceiver(abc.ABC):
    """Base Class for AV Receiver."""

    AV_CODEC_OPTIONS_H264 = {
        # "profile": "0",
        # "level": "3.2",
        "tune": "zerolatency",
        "preset": "ultrafast",
    }

    AV_CODEC_OPTIONS_HEVC = {
        "tune": "zerolatency",
        "preset": "ultrafast",
    }

    @staticmethod
    def audio_frame(buf, codec_ctx):
        """Return decoded audio frame."""
        packet = av.packet.Packet(buf)
        frames = codec_ctx.decode(packet)
        if not frames:
            return None
        frame = frames[0]
        return frame

    @staticmethod
    def video_frame(buf, codec_ctx, to_rgb=True):
        """Decode H264 Frame to raw image.
        Return AV Frame.

        Frame Format:
        AV_PIX_FMT_YUV420P (libavutil)
        YUV 4:2:0, 12bpp
        (1 Cr & Cb sample per 2x2 Y samples)
        """
        packet = av.packet.Packet(b"".join([buf, bytes(FFMPEG_PADDING)]))
        frames = codec_ctx.decode(packet)
        if not frames:
            return None
        frame = frames[0]
        if frame.is_corrupt:
            _LOGGER.error("Corrupt Frame: %s", frame)
            return None
        # _LOGGER.debug(
        #     "Frame: Key:%s, Interlaced:%s Pict:%s",
        #     frame.key_frame,
        #     frame.interlaced_frame,
        #     frame.pict_type,
        # )
        if to_rgb:
            frame = frame.reformat(frame.width, frame.height, "rgb24")
        elif frame.format.name == "nv12":  # HW Decode will output NV12 frames
            frame = frame.reformat(format="yuv420p")
        return frame

    @staticmethod
    def find_video_decoder(video_format="h264", use_hw=False):
        """Return all decoders found."""
        found = []
        decoders = (
            ("amf", "AMD"),
            ("cuvid", "Nvidia"),
            ("qsv", "Intel"),
            ("videotoolbox", "Apple"),
            (video_format, "CPU"),
        )

        decoder = None
        _LOGGER.debug("Using HW: %s", use_hw)
        if not use_hw:
            _LOGGER.debug("%s - %s - %s", video_format, use_hw, decoders)
            return [(video_format, "CPU")]
        for decoder in decoders:
            if decoder[0] == video_format:
                name = video_format
            else:
                name = f"{video_format}_{decoder[0]}"
            try:
                av.codec.Codec(name, "r")
            except (av.codec.codec.UnknownCodecError, av.error.PermissionError):
                _LOGGER.debug("Could not find Decoder: %s", name)
                continue
            found.append((name, decoder[1]))
            _LOGGER.debug("Found Decoder: %s", name)
        return found

    @staticmethod
    def video_codec(codec_name: str):
        """Return Video Codec Context."""
        try:
            codec_ctx = av.codec.Codec(codec_name, "r").create()
        except av.codec.codec.UnknownCodecError:
            _LOGGER.error("Invalid codec: %s", codec_name)
        _LOGGER.info("Using Decoder: %s", codec_name)
        if codec_name.startswith("h264"):
            codec_ctx.options = AVReceiver.AV_CODEC_OPTIONS_H264
        elif codec_name.startswith("hevc"):
            codec_ctx.options = AVReceiver.AV_CODEC_OPTIONS_HEVC
        codec_ctx.pix_fmt = "yuv420p"
        codec_ctx.flags = av.codec.context.Flags.LOW_DELAY
        codec_ctx.flags2 = av.codec.context.Flags2.FAST
        codec_ctx.thread_type = av.codec.context.ThreadType.AUTO
        return codec_ctx

    @staticmethod
    def audio_codec(codec_name: str = "opus"):
        """Return Audio Codec Context."""
        codec_ctx = av.codec.Codec(codec_name, "r").create()
        codec_ctx.format = "s16"
        return codec_ctx

    def __init__(self):
        self._session = None
        self.rgb = False
        self.video_decoder = None
        self.audio_decoder = None
        self.audio_resampler = None
        self.audio_config = {}

    def get_audio_config(self, header: bytes):
        """Get Audio config from header."""
        self.audio_config = {
            "channels": header[0],
            "bits": header[1],
            "rate": unpack_from("!I", header, 2)[0],
            "frame_size": unpack_from("!I", header, 6)[0],
            "unknown": unpack_from("!I", header, 10)[0],
        }
        self.audio_config["packet_size"] = (
            self.audio_config["channels"]
            * (self.audio_config["bits"] // 8)
            * self.audio_config["frame_size"]
        )
        _LOGGER.info("Audio Config: %s", self.audio_config)

        if not self.audio_decoder:
            self.audio_decoder = AVReceiver.audio_codec()
            self.audio_resampler = av.audio.resampler.AudioResampler(
                "s16",
                self.audio_config["channels"],
                self.audio_config["rate"],
            )
            self._session.events.emit("audio_config")

    def get_video_codec(self):
        """Get Codec Context."""
        codec_name = self._session.video_format
        self.video_decoder = AVReceiver.video_codec(codec_name)
        try:
            self.video_decoder.open()
        except av.error.ValueError as error:
            if self._session:
                try:
                    msg = error.log[2]
                except Exception:  # pylint: disable=broad-except
                    msg = str(error)
                self._session.error = msg
                self._session.stop()

    def set_session(self, session):
        """Set Session."""
        self._session = session

    def notify_started(self):
        """Notify session that receiver has started."""
        self._session.receiver_started.set()

    def start(self):
        """Start receiver."""
        self.notify_started()

    def decode_video_frame(self, buf: bytes) -> av.VideoFrame:
        """Return decoded Video Frame."""
        if not self.video_decoder:
            return None
        frame = AVReceiver.video_frame(buf, self.video_decoder, self.rgb)
        return frame

    def decode_audio_frame(self, buf: bytes) -> av.AudioFrame:
        """Return decoded Audio Frame."""
        if not self.audio_config or not self.audio_decoder:
            return None

        frame = AVReceiver.audio_frame(buf, self.audio_decoder)
        if frame:
            # Need format to be s16. Format is float.
            frame = self.audio_resampler.resample(frame)
        return frame

    def get_video_frame(self):
        """Return Video Frame."""
        raise NotImplementedError

    def get_audio_frame(self):
        """Return Audio Frame."""
        raise NotImplementedError

    def handle_video(self, buf: bytes):
        """Handle video frame."""
        raise NotImplementedError

    def handle_audio(self, buf: bytes):
        """Handle audio frame."""
        raise NotImplementedError

    def close(self):
        """Close Receiver."""
        if self.video_decoder is not None:
            self.video_decoder.close()


class QueueReceiver(AVReceiver):
    """Receiver which stores decoded frames in queues."""

    def __init__(self):
        super().__init__()
        self.v_queue = deque(maxlen=10)
        self.a_queue = deque(maxlen=10)

    def close(self):
        """Close Receiver."""
        super().close()
        self.v_queue.clear()

    def get_video_frame(self):
        """Return oldest Video Frame from queue."""
        try:
            frame = self.v_queue.popleft()
            return frame
        except IndexError:
            return None

    def get_audio_frame(self):
        """Return oldest Audio Frame from queue."""
        try:
            frame = self.a_queue.popleft()
            return frame
        except IndexError:
            return None

    def handle_video(self, buf):
        """Handle video frame. Add to queue."""
        frame = self.decode_video_frame(buf)
        if frame is None:
            return
        if len(self.v_queue) >= self.v_queue.maxlen:
            _LOGGER.warning("AV Receiver max video queue size exceeded")
            self.v_queue.clear()
        self.v_queue.append(frame)
        self._session.events.emit("video_frame")

    def handle_audio(self, buf):
        """Handle Audio Frame. Add to queue."""
        frame = self.decode_audio_frame(buf)
        if frame is None:
            return
        if len(self.a_queue) >= self.a_queue.maxlen:
            _LOGGER.warning("AV Receiver max audio queue size exceeded")
            self.a_queue.clear()
        self.a_queue.append(frame)
        self._session.events.emit("audio_frame")
