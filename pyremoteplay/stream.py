"""Stream for pyremoteplay."""

import base64
import logging
import queue
import socket
import threading

from construct import (Bytes, BytesInteger, Const, GreedyBytes, Int32ub,
                       Padding, Struct)
from Cryptodome.Random import get_random_bytes
from Cryptodome.Util.strxor import strxor

from .av import HEADER_TYPE_AUDIO, HEADER_TYPE_VIDEO, AVReceiver
from .const import FPS_PRESETS, RESOLUTION_PRESETS
from .crypt import StreamECDH
from .feedback import Controller
from .stream_packets import ProtoHandler, UnexpectedMessage, get_launch_spec
from .util import from_b, listener, log_bytes, to_b

_LOGGER = logging.getLogger(__name__)

STREAM_PORT = 9296
A_RWND = b"\x00\x01\x90\x00"
OUTBOUND_STREAMS = 0x64
INBOUND_STREAMS = 0x64

DEFAULT_RTT = 1
DEFAULT_MTU = 1454

DATA_LENGTH = 26
DATA_ACK_LENGTH = 29

HEADER_TYPE_CONTROL = 0x00
HEADER_TYPE_FEEDBACK_EVENT = 0x01
HEADER_TYPE_HANDSHAKE = 0x04
HEADER_TYPE_CONGESTION = 0x05
HEADER_TYPE_FEEDBACK_STATE = 0x06
HEADER_TYPE_RUMBLE_EVENT = 0x07
HEADER_TYPE_CLIENT_INFO = 0x08
HEADER_TYPE_PAD_INFO_EVENT = 0x09

CHUNK_TYPE_DATA = 0x00
CHUNK_TYPE_INIT = 0x01
CHUNK_TYPE_INIT_ACK = 0x02
CHUNK_TYPE_DATA_ACK = 0x03
CHUNK_TYPE_COOKIE = 0x0A
CHUNK_TYPE_COOKIE_ACK = 0x0B

HEADER_STRUCT = Struct(
    "type" / Bytes(1),
    "tag_remote" / Bytes(4),
    "gmac" / Bytes(4),
    "key_pos" / Bytes(4),
    "chunk_type" / Bytes(1),
    "chunk_flag" / Bytes(1),
    "payload_length" / Bytes(2),
)

DATA_STRUCT = Struct(
    "sequence_num" / Bytes(4),
    "channel" / Bytes(2),
    "padding" / Padding(3),
    "data" / GreedyBytes,
)

DATA_ACK_STRUCT = Struct(
    "sequence_num" / Bytes(4),
    "rwnd" / Bytes(4),
    "gap_ack_blocks_count" / Bytes(2),
    "dup_tsns_count" / Bytes(2),
)

FEEDBACK_STRUCT = Struct(
    "type" / Bytes(1),
    "sequence_num" / Bytes(2),
    "padding" / Padding(1),
    "key_pos" / Bytes(4),
    "gmac" / Bytes(4),
    "data" / GreedyBytes,
)


def get_header(
    header_type: int,
    tag_remote: bytes,
    gmac: bytes,
    key_pos: int,
    chunk_type: int,
    chunk_flag: int,
    payload_length: int,
) -> bytes:
    """Return header for packet."""
    # length of (Chunk Type + Chunk Flag + Payload Length + Payload)
    payload_length += 4

    header = HEADER_STRUCT.build(
        {
            "type": header_type,
            "tag_remote": tag_remote,
            "gmac": gmac,
            "key_pos": key_pos,
            "chunk_type": chunk_type,
            "chunk_flag": chunk_flag,
            "payload_length": payload_length,
        }
    )
    return header


def get_feedback(feedback_type: int, sequence_num: int, key_pos: int, gmac: bytes, data: bytes) -> bytes:
    """Return Feedback Packet."""
    feedback = FEEDBACK_STRUCT.build(
        {
            "type": feedback_type,
            "sequence_num": sequence_num,
            "key_pos": key_pos,
            "gmac": gmac,
            "data": data,
        }
    )
    return feedback


def parse_msg(msg: bytes):
    """Parse Message."""
    fmt = Struct(
        "header" / HEADER_STRUCT,
        "payload" / GreedyBytes,
    )
    data = fmt.parse(msg)
    return data


def get_init_pl(
    tag_local: bytes, outbound: int, inbound: int, sequence_num: bytes
) -> bytes:
    """Return INIT Payload."""
    fmt = Struct(
        "tag_local" / Bytes(4),
        "rwnd" / Bytes(4),
        "outbound" / Bytes(2),
        "inbound" / Bytes(2),
        "sequence_num" / Bytes(4),
    )
    msg = fmt.build(
        {
            "tag_local": tag_local,
            "rwnd": A_RWND,
            "outbound": outbound,
            "inbound": inbound,
            "sequence_num": sequence_num,
        }
    )
    return msg


def get_data_pl(sequence_num: bytes, channel: int, data: bytes) -> bytes:
    """Return Data Payload."""
    msg = DATA_STRUCT.build({"sequence_num": sequence_num, "channel": channel, "data": data})
    return msg


def get_data_ack_pl(sequence_num: bytes) -> bytes:
    """Return Data Payload."""
    msg = DATA_ACK_STRUCT.build(
        {
            "sequence_num": sequence_num,
            "rwnd": A_RWND,
            "gap_ack_blocks_count": 0,
            "dup_tsns_count": 0,
        }
    )
    return msg


class RPStream():
    """RP Stream Class."""

    STATE_INIT = "init"
    STATE_READY = "ready"

    def __init__(self, host: str, stop_event, ctrl, resolution="1080p"):
        self._host = host
        self._ctrl = ctrl
        self._state = None
        self._sequence_num = self._tag_local = get_random_bytes(4)
        self._tag_remote = b"\x00\x00\x00\x00"
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
        self.controller = Controller(self)

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

    def _ready(self):
        _LOGGER.debug("Stream Ready")
        self._state = RPStream.STATE_READY

    def get_msg(self, header_type: int, chunk_type: int, chunk_flag: int, payload: bytes = b'', key_pos=0, gmac=bytes(4)):
        """Return Message."""
        header = get_header(
            header_type,
            self._tag_remote,
            gmac,
            key_pos,
            chunk_type,
            chunk_flag,
            len(payload),
        )
        msg = b"".join([header, payload])
        return msg

    def _advance_sequence(self):
        """Advance SCTP sequence number."""
        if self.state == RPStream.STATE_INIT:
            return
        self._sequence_num = to_b(from_b(self._sequence_num) + 1, 4)
        _LOGGER.debug("Sequence advanced to: %s", self._sequence_num.hex())

    def _send_init(self):
        """Send Init Packet."""
        chunk_flag = 0
        payload = get_init_pl(
            self._tag_local,
            OUTBOUND_STREAMS,
            INBOUND_STREAMS,
            self._sequence_num,
        )
        msg = self.get_msg(HEADER_TYPE_CONTROL, CHUNK_TYPE_INIT, chunk_flag, payload)
        log_bytes("Stream Init", msg)
        self.send(msg)

    def send_data(self, data: bytes, chunk_flag: int, channel: int, proto=False):
        """Send Data Packet."""
        key_pos = 0
        if self.cipher:
            self._advance_sequence()
        payload = get_data_pl(self._sequence_num, channel, data)
        msg = self.get_msg(HEADER_TYPE_CONTROL, CHUNK_TYPE_DATA, chunk_flag, payload, key_pos=key_pos)
        if self.cipher:
            key_pos = self.cipher.key_pos
            if proto:
                advance_by = len(data)
            else:
                advance_by = DATA_LENGTH + len(data)
            gmac = self.cipher.get_gmac(msg, advance_by)
            msg = self.get_msg(HEADER_TYPE_CONTROL, CHUNK_TYPE_DATA, chunk_flag, payload, key_pos=key_pos, gmac=gmac)
        self.send(msg)

    def send_data_ack(self, sequence_num: bytes):
        """Send Data Packet."""
        log_bytes("RECV Seq Num", sequence_num)
        key_pos = 0
        payload = get_data_ack_pl(sequence_num)
        msg = self.get_msg(HEADER_TYPE_CONTROL, CHUNK_TYPE_DATA_ACK, 0, payload, key_pos=key_pos)
        if self.cipher:
            gmac = self.cipher.get_gmac(msg, DATA_ACK_LENGTH)
            key_pos = self.cipher.key_pos
            msg = self.get_msg(HEADER_TYPE_CONTROL, CHUNK_TYPE_DATA_ACK, 0, payload, key_pos=key_pos, gmac=gmac)
        self.send(msg)

    def send_feedback(self, is_event: bool, data: bytes):
        """Send feedback packet."""
        feedback_type = HEADER_TYPE_FEEDBACK_EVENT if is_event else HEADER_TYPE_FEEDBACK_STATE
        key_pos = 0
        gmac = bytes(4)
        sequence_num = self.controller.sequence_num
        msg = get_feedback(feedback_type, sequence_num, key_pos, gmac, data)
        if self.cipher:
            gmac = self.cipher.get_gmac(msg, len(data))
            key_pos = self.cipher.key_pos
            msg = get_feedback(feedback_type, sequence_num, key_pos, gmac, data)
        self.controller.sequence_num += 1
        self.send(msg)

    def send(self, msg: bytes):
        """Send Message."""
        self._protocol.sendto(msg, (self._host, STREAM_PORT))
        log_bytes(f"Stream Send", msg)

    def _send(self):
        return
        if not self._send_buf.empty():
            data_send = self._send_buf.get_nowait()
            self._protocol.sendto(data_send, (self._host, STREAM_PORT))
            log_bytes(f"Stream Send", data_send)

    def _handle(self, msg):
        """Handle packets."""
        type_mask = from_b(msg[:1]) & 0x0F
        if type_mask == HEADER_TYPE_AUDIO or type_mask == HEADER_TYPE_VIDEO:
            if self.resolution is not None:
                self.av.handle_av(msg)
            return
        data = parse_msg(msg)
        if self.cipher:
            msg = bytearray(msg)
            gmac = bytes(msg[5:9])
            key_pos = from_b(bytes(msg[9:13]))
            msg[5:13] = bytes(8)
            msg = bytes(msg)
            self.cipher.verify_gmac(msg, key_pos, gmac)
        if data.header.type == to_b(HEADER_TYPE_CONTROL, 1):
            self._recv_control(data)
        else:
            _LOGGER.debug("Unknown MSG type: %s", data.header.type)
        if self._state == RPStream.STATE_READY:
            self.controller.send_state()

    def _recv_control(self, data: bytes):
        if data.header.chunk_type == to_b(CHUNK_TYPE_INIT_ACK, 1):
            self._recv_init(data)
        elif data.header.chunk_type == to_b(CHUNK_TYPE_COOKIE_ACK, 1):
            self._recv_cookie()
        elif data.header.chunk_type == to_b(CHUNK_TYPE_DATA_ACK, 1):
            self._recv_data_ack(data)
        elif data.header.chunk_type == to_b(CHUNK_TYPE_DATA, 1):
            self._recv_data(data)

    def _recv_init(self, data):
        """Handle Init."""
        fmt = Struct(
            "tag_remote" / Bytes(4),
            "rwnd" / Bytes(4),
            "outbound" / Bytes(2),
            "inbound" / Bytes(2),
            "sequence_num" / Bytes(4),
            "data" / GreedyBytes,
        )
        payload = fmt.parse(data.payload)
        self._tag_remote = payload.tag_remote
        log_bytes("INIT Tag Local", self._tag_local)
        log_bytes("INIT Tag Remote", self._tag_remote)

        chunk_flag = 0
        msg = self.get_msg(
            HEADER_TYPE_CONTROL,
            CHUNK_TYPE_COOKIE,
            chunk_flag,
            payload.data,
        )
        self.send(msg)

    def _recv_cookie(self):
        self._send_big()

    def _recv_data(self, data):
        payload = DATA_STRUCT.parse(data.payload)
        proto_data = payload.data
        sequence_num = payload.sequence_num
        self.proto.handle(proto_data)
        self.send_data_ack(sequence_num)

    def _recv_data_ack(self, data):
        payload = DATA_ACK_STRUCT.parse(data.payload)
        _LOGGER.debug(
            "RECV ACK, Sequence: %s Gap Blocks: %s, Dup: %s",
            payload.sequence_num.hex(),
            payload.gap_ack_blocks_count.hex(),
            payload.dup_tsns_count.hex(),
        )

    def _send_big(self):
        self._ecdh = StreamECDH()
        handshake_key = self._ecdh.handshake_key
        ecdh_pub_key = self._ecdh.public_key
        ecdh_sig = self._ecdh.public_sig

        chunk_flag = channel = 1

        enc_key = b"\x00\x00\x00\x00"

        launch_spec = self.format_launch_spec(handshake_key)

        data = ProtoHandler.big_payload(
            client_version=9,
            session_key=self._ctrl.session_id,
            launch_spec=launch_spec,
            encrypted_key=enc_key,
            ecdh_pub_key=ecdh_pub_key,
            ecdh_sig=ecdh_sig,
        )
        log_bytes("Big Payload", data)
        self.send_data(data, chunk_flag, channel)

    def format_launch_spec(self, handshake_key: bytes, format_type=None) -> bytes:
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

    @property
    def state(self) -> str:
        """Return State."""
        return self._state
