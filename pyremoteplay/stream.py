"""Stream for pyremoteplay."""
import base64
import logging
import queue
import socket
import threading

from Cryptodome.Random import get_random_bytes
from Cryptodome.Util.strxor import strxor

from .av import AVReceiver
from .const import FPS_PRESETS, RESOLUTION_PRESETS
from .crypt import StreamECDH
from .stream_packets import (AVPacket, Chunk, FeedbackPacket, Header, Packet,
                             ProtoHandler, UnexpectedMessage, get_launch_spec)
from .util import from_b, listener, log_bytes, to_b

_LOGGER = logging.getLogger(__name__)

STREAM_PORT = 9296
A_RWND = 0x019000
OUTBOUND_STREAMS = 0x64
INBOUND_STREAMS = 0x64

DEFAULT_RTT = 1
DEFAULT_MTU = 1454

DATA_LENGTH = 26
DATA_ACK_LENGTH = 29


class RPStream():
    """RP Stream Class."""

    STATE_INIT = "init"
    STATE_READY = "ready"

    def __init__(self, host: str, stop_event, ctrl, resolution="1080p", av_receiver=None):
        self._host = host
        self._ctrl = ctrl
        self._state = None
        self._tsn = self._tag_local = 1  #int.from_bytes(get_random_bytes(4), "big")
        self._tag_remote = 0
        self._key_pos = 0
        self._protocol = None
        self._stop_event = stop_event
        self._worker = None
        self._send_buf = queue.Queue()
        self._ecdh = None
        self.cipher = None
        self.proto = ProtoHandler(self)
        self.av = AVReceiver(self)
        self.resolution = RESOLUTION_PRESETS.get(resolution)
        self.max_fps = 60
        self.rtt = DEFAULT_RTT
        self.mtu_in = DEFAULT_MTU
        self.controller = None

    def connect(self):
        """Connect socket to Host."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(0)
        # a_rwnd = format_bytes(A_RWND)
        # sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, a_rwnd)
        self._protocol = sock
        self._state = RPStream.STATE_INIT
        self._worker = threading.Thread(
            target=listener,
            args=("Stream", self._protocol, self._handle, self._send, self._stop_event),
        )
        self._worker.start()
        self._send_init()

    def ready(self):
        _LOGGER.info("Stream Ready")
        self._state = RPStream.STATE_READY

    def _advance_sequence(self):
        """Advance SCTP sequence number."""
        if self.state == RPStream.STATE_INIT:
            return
        self._tsn += 1

    def _send_init(self):
        """Send Init Packet."""
        msg = Packet(Header.Type.CONTROL, Chunk.Type.INIT, tag=self._tag_local, tsn=self._tsn)
        self.send(msg.bytes())

    def _send_cookie(self, data: bytes):
        """Send Cookie Packet."""
        msg = Packet(Header.Type.CONTROL, Chunk.Type.COOKIE, tag=self._tag_local, tag_remote=self._tag_remote, data=data)
        self.send(msg.bytes())

    def send_data(self, data: bytes, flag: int, channel: int, proto=False):
        """Send Data Packet."""
        advance_by = 0
        if self.cipher:
            self._advance_sequence()
            if proto:
                advance_by = len(data)

        msg = Packet(Header.Type.CONTROL, Chunk.Type.DATA, tag_remote=self._tag_remote, tsn=self._tsn, flag=flag, channel=channel, data=data)
        self.send(msg.bytes(self.cipher, False, advance_by))

    def _send_data_ack(self, ack_tsn: int):
        """Send Data Packet."""
        msg = Packet(Header.Type.CONTROL, Chunk.Type.DATA_ACK, tag_remote=self._tag_remote, tag=self._tag_local, tsn=ack_tsn)
        self.send(msg.bytes(self.cipher, False, DATA_ACK_LENGTH))

    def send_feedback(self, feedback_type: int, sequence: int, data=b'', state=None):
        """Send feedback packet."""
        msg = FeedbackPacket(feedback_type, sequence=sequence, data=data, state=state)
        self.send(msg.bytes(self.cipher, True))

    def send(self, msg: bytes):
        """Send Message."""
        log_bytes(f"Stream Send", msg)
        self._protocol.sendto(msg, (self._host, STREAM_PORT))

    def _send(self):
        pass

    def _handle(self, msg):
        """Handle packets."""
        if Packet.is_av(msg[:1]):
            if self.av:
                packet = Packet.parse(msg)
        else:
            packet = Packet.parse(msg)
            _LOGGER.debug(packet)
            log_bytes(f"Stream RECV", msg)
            if self.cipher:
                gmac = packet.header.gmac
                _gmac = int.to_bytes(gmac, 4, "big")
                key_pos = packet.header.key_pos
                packet.header.gmac = packet.header.key_pos = 0
                gmac = self.cipher.verify_gmac(packet.bytes(), key_pos, _gmac)

            if packet.chunk.type == Chunk.Type.INIT_ACK:
                self._recv_init(packet)
            elif packet.chunk.type == Chunk.Type.COOKIE_ACK:
                self._recv_cookie_ack()
            elif packet.chunk.type == Chunk.Type.DATA_ACK:
                self._recv_data_ack(packet)
            elif packet.chunk.type == Chunk.Type.DATA:
                self._recv_data(packet)

    def _recv_init(self, packet):
        """Handle Init."""
        params = packet.params
        self._tag_remote = params["tag"]
        self._send_cookie(params["data"])

    def _recv_cookie_ack(self):
        """Handle Cookie Ack"""
        self._send_big()

    def _recv_data(self, packet):
        """Handle Data."""
        params = packet.params
        self._send_data_ack(params["tsn"])
        self.proto.handle(params["data"])

    def _recv_data_ack(self, packet):
        """Handle data ack."""
        params = packet.params
        _LOGGER.debug(f"TSN={params['tsn']} GAP_ACKs={params['gap_ack_blocks_count']} DUP_TSNs={params['dup_tsns_count']}")

    def _send_big(self):
        self._ecdh = StreamECDH()
        chunk_flag = channel = 1
        launch_spec = self._format_launch_spec(self._ecdh.handshake_key)
        data = ProtoHandler.big_payload(
            client_version=9,
            session_key=self._ctrl.session_id,
            launch_spec=launch_spec,
            encrypted_key=bytes(4),
            ecdh_pub_key=self._ecdh.public_key,
            ecdh_sig=self._ecdh.public_sig,
        )
        log_bytes("Big Payload", data)
        self.send_data(data, chunk_flag, channel)

    def _format_launch_spec(self, handshake_key: bytes, format_type=None) -> bytes:
        launch_spec = get_launch_spec(
            handshake_key=handshake_key,
            resolution=self.resolution,
            max_fps=self.max_fps,
            rtt=self.rtt,
            mtu_in=self.mtu_in,
        )
        if format_type == "raw":
            return launch_spec
        launch_size = len(launch_spec)
        launch_spec_enc = to_b(0x00, launch_size)
        launch_spec_enc = self._ctrl._cipher.encrypt(launch_spec_enc, counter=0)

        if format_type == "encrypted":
            return launch_spec_enc
        launch_spec_xor = strxor(launch_spec_enc, launch_spec)
        if format_type == "xor":
            return launch_spec_xor

        launch_spec_b64 = base64.b64encode(launch_spec_xor)
        return launch_spec_b64

    def set_ciphers(self, ecdh_pub_key: bytes, ecdh_sig: bytes):
        """Set Ciphers."""
        if not self._ecdh.set_secret(ecdh_pub_key, ecdh_sig):
            self._stop_event.set()
        self.cipher = self._ecdh.init_ciphers()

    def started(self):
        self._ctrl.init_controller()
        self.controller = self._ctrl.controller

    def recv_stream_info(self, info: dict):
        self.stream_info = info
        if self.av:
            self.av.set_headers(info["video_header"], info["audio_header"])

    @property
    def state(self) -> str:
        """Return State."""
        return self._state
