"""AV for pyremoteplay."""
import abc
import errno
import logging
import multiprocessing
import queue
import sys
import threading
import time
from collections import deque
from io import BytesIO
from struct import unpack_from

from .stream_packets import AVPacket, Packet
from .util import event_emitter, log_bytes, timeit

try:
    import av
    import ffmpeg
except ModuleNotFoundError as err:
    print(err)

try:
    from opuslib import Decoder
except Exception as err:
    print(err)

_LOGGER = logging.getLogger(__name__)


class AVHandler():
    """AV Handler."""

    def __init__(self, session):
        self._session = session
        self._receiver = None
        self._v_stream = None
        self._a_stream = None
        self._queue = deque(maxlen=5000)
        self._worker = None
        self._last_congestion = 0
        self._waiting = False

    def add_receiver(self, receiver):
        if self._receiver:
            raise RuntimeError("Cannot add Receiver more than once")
        if receiver is not None:
            self._receiver = receiver
            self._receiver.start()

    def set_cipher(self, cipher):
        self._cipher = cipher
        self._session.init_av_handler()

    def set_headers(self, v_header, a_header):
        """Set headers."""
        if self._receiver:
            self._receiver._v_header = v_header
            self._v_stream = AVStream("video", v_header, self._receiver.handle_video)
            self._a_stream = AVStream("audio", a_header, self._receiver.handle_audio)

    def add_packet(self, packet):
        """Add Packet."""
        packet = Packet.parse(packet)
        if len(self._queue) >= self._queue.maxlen:
            self._queue.clear()
            self._waiting = True
            _LOGGER.warning("AV Handler max queue size exceeded")
            self._session.error = "Decoder could not keep up. Try lowering framerate / resolution"
            self._session.stop()
            event_emitter.emit("stop")
            return
        if self._waiting and packet.unit_index == 0:
            self._waiting = False
        if not self._waiting:
            self._queue.append(packet)

    def process_packet(self):
        try:
            packet = self._queue.popleft()
        except IndexError:
            time.sleep(0.0001)
            return
        packet.decrypt(self._cipher)
        self._handle(packet)
        #self._send_congestion()

    def worker(self):
        while not self._session._stop_event.is_set():
            self.process_packet()
        _LOGGER.info("Closing AV Receiver")
        self._receiver.close()
        self._queue.clear()
        _LOGGER.info("AV Receiver Closed")

    def _send_congestion(self):
        now = time.time()
        if now - self._last_congestion > 0.2:
            self._session._stream.send_congestion(self.received, self.lost)
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
        self._frame_bad_order = False

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
        if sys.modules.get("opuslib") is None:
            _LOGGER.info("Opuslib not found. Ignoring audio.")
            return
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
            return

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
            self._frame_bad_order = False
            # First packet of Frame
            if packet.unit_index == 0:
                self._frame = packet.frame_index
                self._last_unit = -1
                self._buf.close()
                self._buf = BytesIO()
                if packet.index == 0:
                    self._buf.write(self._header)
                _LOGGER.debug("Started New Frame: %s", self.frame)

        # Current Frame not FEC
        if not packet.is_fec:
            # Packet is in order
            if packet.unit_index == self.last_unit + 1:
                self._last_unit += 1
                # Don't include first two decrypted bytes 
                self._buf.write(packet.data[2:])
            else:
                if not self._frame_bad_order:
                    _LOGGER.warning("Received unit out of order: %s, expected: %s", packet.unit_index, self.last_unit + 1)
                    self._frame_bad_order = True
                if self._lost > 65535:
                    self._lost = 0
                self._lost += (packet.index - self._last_index - 1)
                if self._lost > 65535:
                    self._lost = 1
                self._last_unit = packet.unit_index
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

    AV_CODEC_OPTIONS_H264 = {
        #"profile": "0",
        # "level": "3.2",
        "tune": "zerolatency",
        "preset": "ultrafast",
    }

    def video_frame(buf, codec, to_rgb=True):
        """Decode H264 Frame to raw image.
        Return AV Frame.

        Frame Format:
        AV_PIX_FMT_YUV420P (libavutil)
        YUV 4:2:0, 12bpp
        (1 Cr & Cb sample per 2x2 Y samples)
        """
        packet = av.packet.Packet(buf)
        frames = codec.decode(packet)
        if not frames:
            return None
        frame = frames[0]
        if frame.is_corrupt:
            _LOGGER.error("Corrupt Frame: %s", frame)
            return None
        #_LOGGER.debug(f"Frame: Key:{frame.key_frame}, Interlaced:{frame.interlaced_frame} Pict:{frame.pict_type}")
        if to_rgb:
            frame = frame.reformat(frame.width, frame.height, "rgb24")
        return frame

    def find_video_decoder(video_format="h264", use_hw=False):
        decoders = {
            "h264": [
                "h264_amf",
                "h264_nvenc",
                "h264_qsv",
                "h264_videotoolbox",
                "h264_omx",
                "h264_vaapi",
                "h264"
            ]
        }
        _LOGGER.debug("Using HW: %s", use_hw)
        if not use_hw:
            _LOGGER.debug("%s - %s - %s", video_format, use_hw, decoders)
            return decoders.get(video_format)[-1]
        for decoder in decoders.get(video_format):
            try:
                av.codec.Codec(decoder, "r")
            except (av.codec.codec.UnknownCodecError, av.error.PermissionError):
                _LOGGER.debug("Could not find Decoder: %s", decoder)
                continue
            break
        _LOGGER.info("Found Decoder: %s", decoder)
        return decoder

    def video_codec(video_format="h264", use_hw=False):
        decoder = AVReceiver.find_video_decoder(video_format=video_format, use_hw=use_hw)
        codec = av.codec.Codec(decoder, "r").create()
        codec.options = AVReceiver.AV_CODEC_OPTIONS_H264
        codec.pix_fmt = "yuv420p"
        #codec.flags = av.codec.context.Flags.LOW_DELAY
        codec.flags2 = av.codec.context.Flags2.FAST
        codec.thread_type = av.codec.context.ThreadType.AUTO
        return codec

    def __init__(self, session):
        self._session = session
        self.a_cb = None
        self.codec = None

    def get_video_codec(self):
        self.codec = AVReceiver.video_codec(video_format=self._session.video_format, use_hw=self._session.use_hw)

    def notify_started(self):
        self._session.receiver_started.set()

    def start(self):
        self.get_video_codec()
        self.notify_started()

    def decode_video_frame(self, buf: bytes) -> bytes:
        """Decode Video Frame."""
        frame = AVReceiver.video_frame(buf, self.codec)
        return frame

    def get_video_frame(self):
        """Return Video Frame."""
        raise NotImplementedError

    def handle_video(self, buf: bytes):
        """Handle video frame."""
        raise NotImplementedError

    def handle_audio(self, buf: bytes):
        """Handle audio frame."""
        raise NotImplementedError

    def close(self):
        """Close Receiver."""
        if self.codec is not None:
            self.codec.close()


class QueueReceiver(AVReceiver):
    def __init__(self, session):
        super().__init__(session)
        self.v_queue = deque(maxlen=10)
        self.lock = threading.Lock()

    def add_audio_cb(self, cb):
        self.a_cb = cb

    def start(self):
        self.get_video_codec()
        self.notify_started()

    def close(self):
        super().close()
        self.v_queue.clear()

    def get_video_frame(self):
        try:
            frame = self.v_queue.popleft()
            return frame
        except IndexError:
            return None

    def handle_video(self, buf):
        frame = self.decode_video_frame(buf)
        if frame is None:
            return
        if self.queue_size >= self.v_queue.maxlen:
            _LOGGER.warning("AV Receiver max queue size exceeded")
            self.v_queue.clear()
        self.v_queue.append(frame)
        event_emitter.emit('frame')

    def handle_audio(self, buf):
        if self.a_cb is not None:
            self.a_cb(buf)

    @property
    def queue_size(self):
        return len(self.v_queue)


# class ProcessReceiver(AVReceiver):
#     """Uses Multiprocessing. Seems to be slower than threaded."""
#     class FrameMinimal():
#         def __init__(self, plane, width, height):
#             self.planes = [plane]
#             self.width = width
#             self.height = height

#     def process(q_in, q_out, video_format="h264", use_hw=False):
#         codec = AVReceiver.video_codec(video_format=video_format, use_hw=use_hw)

#         _LOGGER.info("Process Started")
#         while True:
#             frame = None
#             try:
#                 buf = q_in.get_nowait()
#             except queue.Empty:
#                 continue
#             frame = AVReceiver.video_frame(buf, codec)
#             if frame is None:
#                 continue
#             try:
#                 q_out.put_nowait((bytearray(frame.planes[0]), frame.width, frame.height))
#                 frame = None
#                 event_emitter.emit("frame")
#             except BrokenPipeError:
#                 break

#     def listener(session, queue):
#         while not session.is_stopped:
#             if not queue.empty():
#                 event_emitter.emit("frame")
#                 break
#             else:
#                 time.sleep(0.001)

#     def __init__(self, session):
#         super().__init__(session)
#         self.pipe1, self.pipe2 = multiprocessing.Pipe()
#         self.manager = multiprocessing.Manager()
#         self.v_queue_in = self.manager.Queue(100)
#         self.v_queue = self.manager.Queue(10)
#         self.lock = self.manager.Lock()
#         self._worker = None

#     def start(self):
#         self._worker = multiprocessing.Process(
#             target=ProcessReceiver.process,
#             args=(self.v_queue_in, self.v_queue, self._session.video_format, self._session.use_hw),
#             daemon=True,
#         )
#         self._worker.start()
#         _LOGGER.info("Process Start")
#         self._listener = threading.Thread(target=ProcessReceiver.listener, args=(self._session, self.v_queue))
#         self._listener.start()
#         self.notify_started()

#     def get_video_frame(self):
#         frame = None
#         try:  
#             frame = self.v_queue.get_nowait()
#             frame = ProcessReceiver.FrameMinimal(frame[0], frame[1], frame[2])
#         except queue.Empty:
#             pass
#         return frame

#     def handle_video(self, buf):
#         self.v_queue_in.put_nowait(buf)

#     def handle_audio(self, buf):
#         if self.a_cb is not None:
#             self.a_cb(buf)

#     def close(self):
#         if self._worker:
#             self._worker.terminate()
#             self._worker.join()
#             self._worker.close()


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

    def __init__(self, session):
        super().__init__(session)
        self._session = session
        self._worker = None
        self._pipe = None
        self.file = None
        self.v_queue = deque()

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
            if error.errno != errno.EPIPE and not self._session.is_running:
                _LOGGER.error("File Receiver error: %s", error)
                _LOGGER.info("File Receiver closing pipe")
                self._pipe.close()
                self._session.stop()

    def close(self):
        if self._worker:
            self._worker.terminate()
            self._worker.join()
            self._worker.close()
