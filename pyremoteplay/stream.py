"""Stream for pyremoteplay."""
import abc
import base64
import logging
import queue
import socket
import threading
from enum import IntEnum
from struct import pack, pack_into, unpack_from

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
A_RWND = 0x019000
OUTBOUND_STREAMS = 0x64
INBOUND_STREAMS = 0x64

DEFAULT_RTT = 1
DEFAULT_MTU = 1454

DATA_LENGTH = 26
DATA_ACK_LENGTH = 29


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
    "tsn" / Bytes(4),
    "channel" / Bytes(2),
    "padding" / Padding(3),
    "data" / GreedyBytes,
)

DATA_ACK_STRUCT = Struct(
    "tsn" / Bytes(4),
    "rwnd" / Bytes(4),
    "gap_ack_blocks_count" / Bytes(2),
    "dup_tsns_count" / Bytes(2),
)

FEEDBACK_STRUCT = Struct(
    "type" / Bytes(1),
    "tsn" / Bytes(2),
    "padding" / Padding(1),
    "key_pos" / Bytes(4),
    "gmac" / Bytes(4),
    "data" / GreedyBytes,
)


class PacketSection(abc.ABC):
    """Abstract Packet Section."""

    LENGTH = 0

    class Type(abc.ABC):
        """Abstract Type Class."""
        pass

    def __init__(self, _type: int):
        self._length = self.LENGTH if self.LENGTH != 0 else 0
        if not self._type_valid(_type):
            raise ValueError(f"Invalid type: {_type} for {self.__name__}")
        self.__type__ = self.__class__.Type(_type)

    def __repr__(self) -> str:
        return f"<{self.__module__}.{self.__class__.__name__} type={self.type.name}>"

    def _type_valid(self, _type: int) -> bool:
        """Return True if type is valid."""
        valid = True
        if issubclass(self.__class__, PacketSection):
            valid = _type in list(self.__class__.Type)
        return valid

    @abc.abstractclassmethod
    def bytes(self):
        """Abstract method. Return compiled bytes."""
        raise NotImplemented

    @property
    def type(self) -> Type:
        """Return Section Type."""
        return self.__type__

    @property
    def length(self) -> int:
        """Return size in bytes."""
        return self._length


class Header(PacketSection):
    """RP Header section of packet."""
    LENGTH = 13

    class Type(IntEnum):
        """Enums for RP Headers."""
        CONTROL = 0x00
        FEEDBACK_EVENT = 0x01
        HANDSHAKE = 0x04
        CONGESTION = 0x05
        FEEDBACK_STATE = 0x06
        RUMBLE_EVENT = 0x07
        CLIENT_INFO = 0x08
        PAD_EVENT = 0x09

    def parse(buf: bytearray, params: dict) -> int:
        """Return type. Unpack and parse header."""
        params["tag_remote"] = unpack_from("!I", buf, 1)[0]
        params["gmac"] = unpack_from("!I", buf, 5)[0]
        params["key_pos"] = unpack_from("!I", buf, 9)[0]
        return unpack_from("!b", buf, 0)[0]

    def __init__(self, _type: int, **kwargs):
        super().__init__(_type)
        self.tag_remote = kwargs.get("tag_remote") or 0
        self.gmac = kwargs.get("gmac") or 0
        self.key_pos = kwargs.get("key_pos") or 0

    def bytes(self, buf: bytearray):
        """Pack buffer with compiled bytes."""
        pack_into(
            "!bIII",
            buf,
            0,
            self.type,
            self.tag_remote,
            self.gmac,
            self.key_pos,
        )


class Chunk(PacketSection):
    """RP Chunk.Type. Very similar to SCTP."""

    class Type(IntEnum):
        """Enums for Chunks."""
        DATA = 0x00
        INIT = 0x01
        INIT_ACK = 0x02
        DATA_ACK = 0x03
        COOKIE = 0x0A
        COOKIE_ACK = 0x0B

    def data(**kwargs) -> bytes:
        """Return Data PL."""
        tsn = kwargs.get("tsn")
        channel = kwargs.get("channel")
        data = kwargs.get("data")
        return pack("!IHxxx", tsn, channel) + data

    def init(**kwargs) -> bytes:
        """Return Init PL."""
        init_tag = kwargs.get("tag")
        init_tsn = kwargs.get("tsn")
        return pack("!IIHHI", init_tag, A_RWND, OUTBOUND_STREAMS, INBOUND_STREAMS, init_tsn)

    def init_ack(**kwargs) -> bytes:
        """Return Init Ack PL."""
        return b''

    def data_ack(**kwargs) -> bytes:
        """Return Data Ack PL."""
        tsn = kwargs.get("tsn")
        gap_acks = kwargs.get("gap_ack_blocks_count") or 0
        dup_tsns = kwargs.get("dup_tsns_count") or 0
        return pack("!IIHH", tsn, A_RWND, gap_acks, dup_tsns)

    def cookie(**kwargs) -> bytes:
        """Return Cookie PL."""
        return kwargs.get("data")

    def cookie_ack(**kwargs) -> bytes:
        """Return Cookie Ack PL."""
        return b''

    PAYLOADS = {
        0x00: data,
        0x01: init,
        0x02: init_ack,
        0x03: data_ack,
        0x0A: cookie,
        0x0B: cookie_ack,
    }

    def __init__(self, _type: int, **kwargs):
        super().__init__(_type)
        self.flag = kwargs.get("flag") or 0
        self.payload = self.PAYLOADS[_type](**kwargs)

    def bytes(self, buf: bytearray):
        """Pack buffer with compiled bytes."""
        if self.flag < 0 or not isinstance(self.flag, int):
            raise ValueError(f"Chunk flag: {self.flag} is not valid")
        pack_into("!bb", buf, Header.LENGTH, self.type, self.flag)

    @property
    def length(self) -> int:
        """Return size in bytes."""
        return len(self.payload) + 2


class Packet(PacketSection):
    """Full RP Packet."""

    def from_bytes(msg: bytes):
        """Return new instance from bytes."""
        buf = bytearray(msg)
        params = {}
        h_type = Header.parse(buf, params)
        c_type = Chunk.parse(buf, params)

    def __repr__(self) -> str:
        return (
            f"<{self.__module__}.{self.__class__.__name__} "
            f"type={self.type.name} chunk={self._chunk.type.name} "
            f"flag={self._chunk.flag}>"
        )

    def __init__(self, header_type: int, chunk_type: int, **kwargs):
        self._header = Header(header_type, **kwargs)
        self._chunk = Chunk(chunk_type, **kwargs)

    def bytes(self) -> bytes:
        """Return compiled bytes."""
        chunk_len = self._chunk.length + 2
        buf = bytearray(self._header.length + chunk_len)
        self._header.bytes(buf)
        self._chunk.bytes(buf)
        pack_into("!H", buf, self._header.length + 2, chunk_len)
        buf[self._header.length + 4:] = self._chunk.payload
        return bytes(buf)

    @property
    def type(self):
        if self._header is None:
            return None
        return self._header.type


def get_header(
    header_type: int,
    tag_remote: bytes,
    gmac: bytes,
    key_pos: int,
    chunk_type: Chunk,
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


def get_feedback(feedback_type: int, tsn: int, key_pos: int, gmac: bytes, data: bytes) -> bytes:
    """Return Feedback Packet."""
    feedback = FEEDBACK_STRUCT.build(
        {
            "type": feedback_type,
            "tsn": tsn,
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
    tag_local: bytes, outbound: int, inbound: int, tsn: bytes
) -> bytes:
    """Return INIT Payload."""
    fmt = Struct(
        "tag_local" / Bytes(4),
        "rwnd" / Bytes(4),
        "outbound" / Bytes(2),
        "inbound" / Bytes(2),
        "tsn" / Bytes(4),
    )
    msg = fmt.build(
        {
            "tag_local": tag_local,
            "rwnd": A_RWND,
            "outbound": outbound,
            "inbound": inbound,
            "tsn": tsn,
        }
    )
    return msg


def get_data_pl(tsn: bytes, channel: int, data: bytes) -> bytes:
    """Return Data Payload."""
    msg = DATA_STRUCT.build({"tsn": tsn, "channel": channel, "data": data})
    return msg


def get_data_ack_pl(tsn: bytes) -> bytes:
    """Return Data Payload."""
    msg = DATA_ACK_STRUCT.build(
        {
            "tsn": tsn,
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
        self._tsn = self._tag_local = 0
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
        self._tsn = to_b(from_b(self._tsn) + 1, 4)
        _LOGGER.debug("Sequence advanced to: %s", self._tsn.hex())

    def _send_init(self):
        """Send Init Packet."""
        msg = Packet(Header.Type.CONTROL, Chunk.Type.INIT, tag=self._tag_local, tsn=self._tsn)
        _LOGGER.debug(msg)
        self.send(msg.bytes())

    def send_data(self, data: bytes, chunk_flag: int, channel: int, proto=False):
        """Send Data Packet."""
        key_pos = 0
        if self.cipher:
            self._advance_sequence()
        payload = get_data_pl(self._tsn, channel, data)
        msg = self.get_msg(Header.Type.CONTROL, Chunk.Type.DATA, chunk_flag, payload, key_pos=key_pos)
        if self.cipher:
            key_pos = self.cipher.key_pos
            if proto:
                advance_by = len(data)
            else:
                advance_by = DATA_LENGTH + len(data)
            gmac = self.cipher.get_gmac(msg, advance_by)
            msg = self.get_msg(Header.Type.CONTROL, Chunk.Type.DATA, chunk_flag, payload, key_pos=key_pos, gmac=gmac)
        self.send(msg)

    def send_data_ack(self, tsn: bytes):
        """Send Data Packet."""
        log_bytes("RECV Seq Num", tsn)
        key_pos = 0
        payload = get_data_ack_pl(tsn)
        msg = self.get_msg(Header.Type.CONTROL, Chunk.Type.DATA_ACK, 0, payload, key_pos=key_pos)
        if self.cipher:
            gmac = self.cipher.get_gmac(msg, DATA_ACK_LENGTH)
            key_pos = self.cipher.key_pos
            msg = self.get_msg(Header.Type.CONTROL, Chunk.Type.DATA_ACK, 0, payload, key_pos=key_pos, gmac=gmac)
        self.send(msg)

    def send_feedback(self, is_event: bool, data: bytes):
        """Send feedback packet."""
        feedback_type = Header.Type.FEEDBACK_EVENT if is_event else Header.Type.FEEDBACK_STATE
        key_pos = 0
        gmac = bytes(4)
        tsn = self.controller.tsn
        msg = get_feedback(feedback_type, tsn, key_pos, gmac, data)
        if self.cipher:
            gmac = self.cipher.get_gmac(msg, len(data))
            key_pos = self.cipher.key_pos
            msg = get_feedback(feedback_type, tsn, key_pos, gmac, data)
        self.controller.tsn += 1
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
        if type_mask == Header.Type.AUDIO or type_mask == Header.Type.VIDEO:
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
        if data.header.type == to_b(Header.Type.CONTROL, 1):
            self._recv_control(data)
        else:
            _LOGGER.debug("Unknown MSG type: %s", data.header.type)
        if self._state == RPStream.STATE_READY:
            pass
            # self.controller.send_state()

    def _recv_control(self, data: bytes):
        if data.header.chunk_type == to_b(Chunk.Type.INIT_ACK, 1):
            self._recv_init(data)
        elif data.header.chunk_type == to_b(Chunk.Type.COOKIE_ACK, 1):
            self._recv_cookie()
        elif data.header.chunk_type == to_b(Chunk.Type.DATA_ACK, 1):
            self._recv_data_ack(data)
        elif data.header.chunk_type == to_b(Chunk.Type.DATA, 1):
            self._recv_data(data)

    def _recv_init(self, data):
        """Handle Init."""
        fmt = Struct(
            "tag_remote" / Bytes(4),
            "rwnd" / Bytes(4),
            "outbound" / Bytes(2),
            "inbound" / Bytes(2),
            "tsn" / Bytes(4),
            "data" / GreedyBytes,
        )
        payload = fmt.parse(data.payload)
        self._tag_remote = payload.tag_remote
        log_bytes("INIT Tag Local", self._tag_local)
        log_bytes("INIT Tag Remote", self._tag_remote)

        chunk_flag = 0
        msg = self.get_msg(
            Header.Type.CONTROL,
            Chunk.Type.COOKIE,
            chunk_flag,
            payload.data,
        )
        self.send(msg)

    def _recv_cookie(self):
        self._send_big()

    def _recv_data(self, data):
        payload = DATA_STRUCT.parse(data.payload)
        proto_data = payload.data
        tsn = payload.tsn
        self.proto.handle(proto_data)
        self.send_data_ack(tsn)

    def _recv_data_ack(self, data):
        payload = DATA_ACK_STRUCT.parse(data.payload)
        _LOGGER.debug(
            "RECV ACK, Sequence: %s Gap Blocks: %s, Dup: %s",
            payload.tsn.hex(),
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
