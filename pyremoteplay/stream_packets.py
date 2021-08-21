"""Stream Packets for pyremoteplay."""
import abc
import json
import logging
from base64 import b64encode
from enum import IntEnum
from struct import pack, pack_into, unpack_from

from google.protobuf.message import DecodeError

from .const import Quality
from .takion_pb2 import *
from .util import log_bytes, timeit

_LOGGER = logging.getLogger(__name__)

STREAM_START = b'\x00\x00\x00\x40\x01\x00\x00'
A_RWND = 0x019000
OUTBOUND_STREAMS = 0x64
INBOUND_STREAMS = 0x64


class UnexpectedMessage(Exception):
    """Message not Expected."""
    pass


class UnexpectedData(Exception):
    """Data incorrect or not expected."""
    pass


LAUNCH_SPEC = {
    'sessionId': 'sessionId4321',
    'streamResolutions': [
        {
            'resolution': {
                'width': None,
                'height': None
            },
            'maxFps': None,
            'score': 10
        }
    ],
    'network': {
        'bwKbpsSent': None,
        'bwLoss': 0.001000,
        'mtu': None,
        'rtt': None,
        'ports': [53, 2053]
    },
    'slotId': 1,
    'appSpecification': {
        'minFps': 30,
        'minBandwidth': 0,
        'extTitleId': 'ps3',
        'version': 1,
        'timeLimit': 1,
        'startTimeout': 100,
        'afkTimeout': 100,
        'afkTimeoutDisconnect': 100},
    'konan': {
        'ps3AccessToken': 'accessToken',
        'ps3RefreshToken': 'refreshToken'},
    'requestGameSpecification': {
        'model': 'bravia_tv',
        'platform': 'android',
        'audioChannels': '5.1',
        'language': 'sp',
        'acceptButton': 'X',
        'connectedControllers': ['xinput', 'ds3', 'ds4'],
        'yuvCoefficient': 'bt709',  # Changed from bt601
        'videoEncoderProfile': 'hw4.1',
        'audioEncoderProfile': 'audio1'
    },
    'userProfile': {
        'onlineId': 'psnId',
        'npId': 'npId',
        'region': 'US',
        'languagesUsed': ['en', 'jp']
    },
    "adaptiveStreamMode": "resize",
    "videoCodec": "avc",
    "dynamicRange": "HDR",
    'handshakeKey': None,
}


def get_launch_spec(
        handshake_key: bytes, resolution: dict, max_fps: int, rtt: int,
        mtu_in: int, quality: str) -> bytes:
    r = resolution
    quality = quality.upper()
    if quality == "DEFAULT":
        bitrate = r['bitrate']
    else:
        bitrate = int(Quality[quality])
    _LOGGER.info("Using bitrate: %s kbps", bitrate)
    launch_spec = LAUNCH_SPEC
    launch_spec['streamResolutions'][0]['resolution']['width'] = r['width']
    launch_spec['streamResolutions'][0]['resolution']['height'] = r['height']
    launch_spec['streamResolutions'][0]['maxFps'] = max_fps
    launch_spec['network']['bwKbpsSent'] = bitrate
    launch_spec['network']['mtu'] = mtu_in
    launch_spec['network']['rtt'] = rtt
    launch_spec['handshakeKey'] = b64encode(handshake_key).decode()
    launch_spec = json.dumps(launch_spec)
    launch_spec = launch_spec.replace(' ', '')  # minify
    launch_spec = launch_spec.replace(':0.001,', ':0.001000,' )  # Add three zeros
    _LOGGER.debug(
        "Length: %s, Launch Spec JSON: %s", len(launch_spec), launch_spec)
    launch_spec = launch_spec.encode()
    launch_spec = b''.join([launch_spec, b'\x00'])
    return launch_spec


class PacketSection(abc.ABC):
    """Abstract Packet Section."""

    LENGTH = 0

    class Type(abc.ABC):
        """Abstract Type Class."""
        pass

    def parse(msg: bytes):
        """Return new instance from bytes."""
        raise NotImplementedError

    def __init__(self, _type: int):
        self._length = self.LENGTH if self.LENGTH != 0 else 0
        if not self._type_valid(_type):
            raise ValueError(f"Invalid type: {_type} for {self.__name__}")
        self.__type = self.__class__.Type(_type)

    def __repr__(self) -> str:
        return (
            f"<{self.__module__}.{self.__class__.__name__} "
            f"type={self.type.name}>"
        )

    def _type_valid(self, _type: int) -> bool:
        """Return True if type is valid."""
        valid = True
        if issubclass(self.__class__, PacketSection):
            valid = _type in list(self.__class__.Type)
        return valid

    def bytes(self) -> bytes:
        """Abstract method. Return compiled bytes."""
        raise NotImplementedError

    def pack(self):
        """Abstract method. Pack compiled bytes."""
        raise NotImplementedError

    @property
    def type(self) -> Type:
        """Return Section Type."""
        return self.__type

    @property
    def length(self) -> int:
        """Return size in bytes."""
        return self._length


class Header(PacketSection):
    """RP Header section of packet."""
    LENGTH = 13

    SLOTS = [
        "tag_remote",
        "gmac",
        "key_pos",
    ]

    class Type(IntEnum):
        """Enums for RP Headers."""
        CONTROL = 0x00
        FEEDBACK_EVENT = 0x01
        VIDEO = 0x02
        AUDIO = 0x03
        HANDSHAKE = 0x04
        CONGESTION = 0x05
        FEEDBACK_STATE = 0x06
        RUMBLE_EVENT = 0x07
        CLIENT_INFO = 0x08
        PAD_EVENT = 0x09

    def parse(buf: bytearray, params: dict) -> int:
        """Return type. Unpack and parse header."""
        _type = unpack_from("!b", buf, 0)[0]
        params["tag_remote"] = unpack_from("!I", buf, 1)[0]
        params["gmac"] = unpack_from("!I", buf, 5)[0]
        params["key_pos"] = unpack_from("!I", buf, 9)[0]
        return _type

    def __init__(self, header_type: int, **kwargs):
        """Initialize Header.

        params:
            tag_remote: The tag of the receiver
            gmac: The 4 byte gmac tag of the packet
                    Calculated with gmac and key_pos equal to 0
            key_pos: The key position required to decrypt and verify gmac
        """

        super().__init__(header_type)
        self.tag_remote = kwargs.get("tag_remote") or 0
        self.gmac = kwargs.get("gmac") or 0
        self.key_pos = kwargs.get("key_pos") or 0

    def pack(self, buf: bytearray):
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

    def data(parse=False, **kwargs) -> bytes:
        """Return Data PL."""
        if parse:
            params = kwargs.get("params")
            payload = params.get("payload")
            if payload:
                params.pop("payload")
                params["tsn"] = unpack_from("!I", payload, 0)[0]
                params["channel"] = unpack_from("!H", payload, 4)[0]
                # Padding 3 Bytes?
                params["data"] = bytes(payload[9:])
                return None
        tsn = kwargs.get("tsn")
        channel = kwargs.get("channel")
        data = kwargs.get("data")
        return pack("!IHxxx", tsn, channel) + data

    def init(parse=False, **kwargs) -> bytes:
        """Return Init PL."""
        if parse:
            params = kwargs.get("params")
            payload = params.get("payload")
            if payload:
                params.pop("payload")
                params["tag"] = unpack_from("!I", payload, 0)[0]
                params["tsn"] = unpack_from("!I", payload, 4)[0]
                return None
        tag_local = kwargs.get("tag")
        init_tsn = kwargs.get("tsn")
        return pack("!IIHHI", tag_local, A_RWND, OUTBOUND_STREAMS, INBOUND_STREAMS, init_tsn)

    def init_ack(parse=False, **kwargs) -> None:
        """Return Init Ack PL."""
        if parse:
            params = kwargs.get("params")
            payload = params.get("payload")
            if payload:
                params.pop("payload")
                params["tag"] = unpack_from("!I", payload, 0)[0]
                params["a_rwnd"] = unpack_from("!I", payload, 4)[0]
                params["outbound_streams"] = unpack_from("!H", payload, 8)[0]
                params["inbound_streams"] = unpack_from("!H", payload, 10)[0]
                params["tsn"] = unpack_from("!I", payload, 12)[0]
                params["data"] = payload[16:]
        return None

    def data_ack(parse=False, **kwargs) -> bytes:
        """Return Data Ack PL."""
        if parse:
            params = kwargs.get("params")
            payload = params.get("payload")
            if payload:
                params.pop("payload")
                params["tsn"] = unpack_from("!I", payload, 0)[0]
                params["a_rwnd"] = unpack_from("!I", payload, 4)[0]
                params["gap_ack_blocks_count"] = unpack_from("!H", payload, 8)[0]
                params["dup_tsns_count"] = unpack_from("!H", payload, 10)[0]
                return None
        tsn = kwargs.get("tsn")
        gap_acks = kwargs.get("gap_ack_blocks_count") or 0
        dup_tsns = kwargs.get("dup_tsns_count") or 0
        return pack("!IIHH", tsn, A_RWND, gap_acks, dup_tsns)

    def cookie(parse=False, **kwargs) -> bytes:
        """Return Cookie PL."""
        return kwargs.get("data") or b''

    def cookie_ack(parse=False, **kwargs) -> bytes:
        """Return Cookie Ack PL."""
        if parse:
            params = kwargs.get("params")
            payload = params.get("payload")
            if payload:
                params.pop("payload")
                params["data"] = payload
                return None

    PAYLOADS = {
        0x00: data,
        0x01: init,
        0x02: init_ack,
        0x03: data_ack,
        0x0A: cookie,
        0x0B: cookie_ack,
    }

    def parse(buf: bytearray, params: dict) -> int:
        """Return type. Unpack and parse header."""
        _type = unpack_from("!b", buf, 13)[0]
        params["flag"] = unpack_from("!b", buf, 14)[0]
        params["payload"] = buf[17:]
        if _type in Chunk.PAYLOADS:
            Chunk.PAYLOADS[_type](parse=True, params=params)
            return _type
        raise ValueError(f"Unknown Chunk Type: {_type}")

    def __init__(self, chunk_type: int, **kwargs):
        super().__init__(chunk_type)
        self.flag = kwargs.get("flag") or 0
        self.payload = self.PAYLOADS[chunk_type](**kwargs)

    def pack(self, buf: bytearray):
        """Pack buffer with compiled bytes."""
        if self.flag < 0 or not isinstance(self.flag, int):
            raise ValueError(f"Chunk flag: {self.flag} is not valid")
        pack_into("!BB", buf, Header.LENGTH, self.type, self.flag)

    @property
    def length(self) -> int:
        """Return size in bytes."""
        return len(self.payload) + 4


class Packet(PacketSection):
    """Full RP Packet."""

    def is_av(header_type: bytes) -> int:
        """Return AV type if packet is AV else return 0."""
        av_mask = int.from_bytes(header_type, "big") & 0x0F
        if av_mask in [Header.Type.VIDEO, Header.Type.AUDIO]:
            return av_mask
        return 0

    def parse(msg: bytes):
        """Return new instance from bytes.

        Params is updated when parsing each section.
        """
        buf = bytearray(msg)
        params = {}
        av_mask = Packet.is_av(msg[:1])
        if av_mask:
            return AVPacket(av_mask, buf, **params)
        h_type = Header.parse(buf, params)
        c_type = Chunk.parse(buf, params)
        return Packet(h_type, c_type, **params)

    def __repr__(self) -> str:
        return (
            f"<RP Packet "
            f"type={self.type.name} chunk={self.chunk.type.name} "
            f"flag={self.chunk.flag}>"
        )

    def __init__(self, header_type: int, chunk_type: int, **kwargs):
        self.header = Header(header_type, **kwargs)
        self.chunk = Chunk(chunk_type, **kwargs)
        self.params = kwargs

    def bytes(self, cipher=None, encrypt=False, advance_by=0) -> bytes:
        """Pack compiled bytes."""
        buf = bytearray(self.header.length + self.chunk.length)
        self.header.pack(buf)
        self.chunk.pack(buf)
        pack_into("!H", buf, self.header.length + 2, self.chunk.length)  # Pack Chunk length
        payload = self.chunk.payload
        if cipher is not None:
            key_pos = cipher.key_pos
            if encrypt:
                payload = cipher.encrypt(payload)
        buf[self.header.length + 4:] = payload
        if cipher is not None:
            # If cipher is available need to send the gmac and key_pos
            if not advance_by:
                advance_by = len(payload)
            # gmac and key_pos need to be 0 when calculating gmac
            gmac = cipher.get_gmac(bytes(buf))
            gmac = int.from_bytes(gmac, "big")
            self.header.key_pos = key_pos
            self.header.gmac = gmac
            self.header.pack(buf)
            cipher.advance_key_pos(advance_by)
        return bytes(buf)

    @property
    def type(self) -> int:
        """Return Packet Type."""
        if self.header is None:
            return None
        return self.header.type


class AVPacket(PacketSection):
    """AV Packet. Parsing capability only."""

    class Type(IntEnum):
        """Enums for AV Packet."""
        VIDEO = Header.Type.VIDEO
        AUDIO = Header.Type.AUDIO

    def __repr__(self) -> str:
        nalu = self.nalu.hex() if self.has_nalu else None
        return (
            f"<RP AVPacket "
            f"type={self.type.name} "
            f"NALU={nalu} "
            f"codec={self.codec} "
            f"data size={len(self._data)} "
            f"key_pos={self.key_pos} "
            f"is_fec={self.is_fec} "
            f"index={self.index} "
            f"frame={self.frame_index} "
            f"unit={self.unit_index}/"
            f"{self.frame_meta['units']['src']}/"
            f"{self.frame_meta['units']['total']}>"
        )

    def __init__(self, _type: int, buf: bytearray, **kwargs):
        self.__type = self.Type(_type)
        self._has_nalu = (unpack_from("!B", buf, 0)[0] >> 4) & 1 != 0
        self._index = unpack_from("!H", buf, 1)[0]
        self._frame_index = unpack_from("!H", buf, 3)[0]
        self._dword2 = unpack_from("!I", buf, 5)[0]
        self._codec = unpack_from("!B", buf, 9)[0]
        # Unknown uint at offset 10
        self._key_pos = unpack_from("!I", buf, 14)[0]
        self._adaptive_stream_index = None
        self._data = None
        self._encrypted = True
        self._frame_meta = {}
        self._nalu = None

        offset = 1  # TODO: Offset 1 verify?
        if self.type == self.Type.VIDEO:
            offset = 3
            self._unit_index = (self._dword2 >> 0x15) & 0x7ff
            self._adaptive_stream_index = unpack_from("!b", buf, 20)[0] >> 5
        else:
            self._unit_index = (self._dword2 >> 0x18) & 0xff
        if self.has_nalu:
            # Unknown ushort at 18
            self._nalu = buf[18 + offset + 1: 18 + offset + 3]
            offset += 3
        self._data = buf[18 + offset:]

        self._get_frame_meta()

    def _get_frame_meta(self):
        self._frame_meta = {
            'frame': self.frame_index,
            'index': self.unit_index,
        }
        if self.type == self.Type.VIDEO:
            total = ((self._dword2 >> 0xa) & 0x7ff) + 1
            fec = self._dword2 & 0x3ff
            src = total - fec
            units = {
                'total': total,
                'fec': fec,
                'src': src,
            }
        else:
            _dword2 = self._dword2 & 0xffff
            total = ((self._dword2 >> 0x10) & 0xff) + 1
            fec = (_dword2 >> 4) & 0xf
            src = _dword2 & 0xf  # TODO: Verify?
            size = _dword2 >> 8  # TODO: Verify?
            units = {
                'total': total,
                'fec': fec,
                'src': src,
                'size': size,
            }
        self._frame_meta['units'] = units

    def decrypt(self, cipher):
        """Decrypt AV Data."""
        if self.encrypted:
            self._data = cipher.decrypt(self.data, self.key_pos)
            self._encrypted = False

    @property
    def type(self) -> int:
        """Return Packet Type."""
        return self.__type

    @property
    def has_nalu(self) -> bool:
        """Return True if packet has NALU unit."""
        return self._has_nalu

    @property
    def index(self) -> int:
        """Return index of packet in stream."""
        return self._index

    @property
    def frame_meta(self) -> dict:
        """Return frame meta data."""
        return self._frame_meta

    @property
    def frame_index(self) -> int:
        """Return frame index in stream."""
        return self._frame_index

    @property
    def unit_index(self) -> int:
        """Return the index within frame."""
        return self._unit_index

    @property
    def frame_length(self) -> int:
        """Return the length of units."""
        return self._frame_meta['units']['total']

    @property
    def frame_length_src(self) -> int:
        """Return the length of src units."""
        return self._frame_meta['units']['src']

    @property
    def frame_length_fec(self) -> int:
        """Return the length of fec units."""
        return self._frame_meta['units']['fec']

    @property
    def frame_size_audio(self) -> int:
        """Return frame size for audio packet."""
        return self._frame_meta['units']['size']

    @property
    def codec(self) -> int:
        """Return the codec value."""
        return self._codec

    @property
    def key_pos(self) -> int:
        """Return the key position for decryption."""
        return self._key_pos

    @property
    def adapative_stream_index(self) -> int:
        """Return the Adaptive Stream Index."""
        return self._adapative_stream_index

    @property
    def is_last(self) -> bool:
        """Return True if packet is the last packet."""
        return self.unit_index == self.frame_length - 1

    @property
    def is_last_src(self) -> bool:
        """Return True if packet is the last src packet."""
        return self.unit_index == self.frame_length_src - 1

    @property
    def is_fec(self) -> bool:
        """Return True if packet is fec."""
        return self.unit_index >= self.frame_length_src

    @property
    def nalu(self) -> bytes:
        """Return NALU info."""
        return self._nalu

    @property
    def encrypted(self) -> bool:
        """Return True if data is currently encrypted."""
        return self._encrypted

    @property
    def data(self) -> bytes:
        """Return AV Data."""
        return self._data


class FeedbackHeader(PacketSection):
    """Feedback Header."""

    LENGTH = 12

    class Type(IntEnum):
        """Enums for Feedback Header."""
        EVENT = Header.Type.FEEDBACK_EVENT
        STATE = Header.Type.FEEDBACK_STATE

    def __repr__(self) -> str:
        return (
            f"<RP Feedback Header "
            f"type={self.type}>"
        )

    def __init__(self, feedback_type: int, **kwargs):
        super().__init__(feedback_type)
        self.sequence = kwargs.get("sequence") or 0
        self.gmac = kwargs.get("gmac") or 0
        self.key_pos = kwargs.get("key_pos") or 0

    def pack(self, buf: bytearray):
        """Pack buffer with compiled bytes."""
        pack_into(
            "!BHxII",
            buf,
            0,
            self.type,
            self.sequence,
            self.key_pos,
            self.gmac,
        )


class FeedbackState(PacketSection):
    """Feedback Event."""
    LENGTH = 25

    PREFIX = bytes([
        0xa0, 0xff, 0x7f, 0xff, 0x7f, 0xff, 0x7f, 0xff,
        0x7f, 0x99, 0x99, 0xff, 0x7f, 0xfe, 0xf7, 0xef,
        0x1f,
    ])

    DEFAULT = {
        "left": {"x": 0, "y": 0},
        "right": {"x": 0, "y": 0},
    }

    class Type(IntEnum):
        """Enums for State."""
        STATE = 0

    def __repr__(self) -> str:
        return (
            f"<RP Feedback State "
            f"state={self.state}>"
        )

    def __init__(self, state_type, **kwargs):
        super().__init__(state_type)
        self.state = kwargs.get("state")

    def pack(self, buf: bytearray):
        """Pack compiled bytes."""
        pack_into(
            "!17shhhh",
            buf,
            FeedbackHeader.LENGTH,
            self.PREFIX,
            self.state["left"]["x"],
            self.state["left"]["y"],
            self.state["right"]["x"],
            self.state["right"]["y"],
        )


class FeedbackEvent(PacketSection):
    """Feedback Event."""
    LENGTH = 3
    PREFIX = 0x80

    class Type(IntEnum):
        """Enums for Buttons."""
        UP = 0x80
        DOWN = 0x81
        LEFT = 0x82
        RIGHT = 0x83
        L1 = 0x84
        R1 = 0x85
        L2 = 0x86
        R2 = 0x87
        CROSS = 0x88
        CIRCLE = 0x89
        SQUARE = 0x8a
        TRIANGLE = 0x8b
        OPTIONS = 0x8c
        SHARE = 0x8d
        PS = 0x8e
        L3 = 0x8f
        R3 = 0x90
        TOUCHPAD = 0x91

    def __repr__(self) -> str:
        return (
            f"<RP Feedback Event "
            f"button={self.type.name} "
            f"state={self.state}>"
        )

    def __init__(self, button_type: int, **kwargs):
        super().__init__(button_type)
        self._is_active = kwargs.get("is_active")
        self._button_id = 0

    def pack(self, buf: bytearray):
        """Pack compiled bytes."""
        pack_into("!BBB", buf, 0, self.PREFIX, self.button_id, self.state)

    def bytes(self) -> bytes:
        """Return compiled bytes."""
        buf = bytearray(self.length)
        self.pack(buf)
        return buf

    @property
    def state(self) -> int:
        """Return State."""
        return 0xff if self.is_active else 0x00

    @property
    def button_id(self) -> int:
        """Return Button ID."""
        if not self._button_id:
            button_id = int(self.type)
            if button_id >= 0x8c and self.is_active:
                # Some buttons have different ID for active.
                self._button_id = button_id + 32
            else:
                self._button_id = button_id
        return self._button_id

    @property
    def is_active(self) -> bool:
        """Return True if button is pressed."""
        return self._is_active


class FeedbackPacket(PacketSection):
    """Feedback Packet."""

    def __repr__(self) -> str:
        return (
            f"<RP Feedback Packet "
            f"type={self.header.type.name}>"
        )

    def __init__(self, feedback_type: int, **kwargs):
        self.header = FeedbackHeader(feedback_type, **kwargs)
        self.chunk = None
        self.data = kwargs.get("data") or b''
        if feedback_type == FeedbackHeader.Type.EVENT:
            if not self.data:
                raise ValueError("Button must to be specified")
        else:
            self.chunk = FeedbackState(FeedbackState.Type.STATE, **kwargs)
        self.params = kwargs

    def bytes(self, cipher=None, encrypt=False) -> bytes:
        """Pack compiled bytes."""
        data_length = len(self.data) if self.header.type == FeedbackHeader.Type.EVENT else FeedbackState.LENGTH
        length = data_length + FeedbackHeader.LENGTH
        buf = bytearray(length)
        self.header.pack(buf)
        if self.header.type == FeedbackHeader.Type.EVENT:
            pack_into(f"!{data_length}s", buf, FeedbackHeader.LENGTH, self.data)
        else:
            self.chunk.pack(buf)

        if cipher is not None:  # Should always require cipher.
            payload = buf[FeedbackHeader.LENGTH:]
            self.header.key_pos = cipher.key_pos
            self.header.pack(buf)
            advance_by = data_length
            if encrypt:
                payload = cipher.encrypt(payload)
                buf[FeedbackHeader.LENGTH:] = payload
            # gmac needs to be 0 when calculating gmac
            gmac = cipher.get_gmac(bytes(buf))
            gmac = int.from_bytes(gmac, "big")
            self.header.gmac = gmac
            self.header.pack(buf)
            cipher.advance_key_pos(advance_by)
        return bytes(buf)


class CongestionPacket(PacketSection):
    """Congestion Packet."""

    LENGTH = 15

    class Type(IntEnum):
        """Enums for Buttons."""
        CONGESTION = Header.Type.CONGESTION

    def __repr__(self) -> str:
        return (
            f"<RP Packet "
            f"type={self.type.name} "
            f"received={self.received} "
            f"lost={self.lost}>"
        )

    def __init__(self, **kwargs):
        super().__init__(CongestionPacket.Type.CONGESTION)
        self.received = kwargs.get("received") or 0
        self.lost = kwargs.get("lost") or 0

    def bytes(self, cipher) -> bytes:
        """Return compiled bytes."""
        key_pos = cipher.key_pos
        buf = bytearray(CongestionPacket.LENGTH)
        gmac = 0
        pack_into("!BxxHHII", buf, 0, self.type, self.received, self.lost, gmac, key_pos)
        gmac = cipher.get_gmac(bytes(buf))
        gmac = int.from_bytes(gmac, "big")
        pack_into("!I", buf, 7, gmac)
        cipher.advance_key_pos(CongestionPacket.LENGTH)
        return bytes(buf)


class ProtoHandler():
    """Handler for Protobuf Messages."""

    def message():
        """Return New Protobuf message."""
        msg = TakionMessage()
        msg.ClearField('type')
        return msg

    def get_payload_type(proto_msg) -> str:
        payload_type = proto_msg.type
        name = proto_msg.DESCRIPTOR.fields_by_name['type']\
            .enum_type.values_by_number[payload_type].name
        return name

    def big_payload(
            client_version=7, session_key=b'', launch_spec=b'',
            encrypted_key=b'', ecdh_pub_key=None, ecdh_sig=None):
        """Big Payload."""
        msg = ProtoHandler.message()
        msg.type = msg.PayloadType.BIG
        msg.big_payload.client_version = client_version
        msg.big_payload.session_key = session_key
        msg.big_payload.launch_spec = launch_spec
        msg.big_payload.encrypted_key = encrypted_key
        if ecdh_pub_key is not None:
            msg.big_payload.ecdh_pub_key = ecdh_pub_key
        if ecdh_sig is not None:
            msg.big_payload.ecdh_sig = ecdh_sig
        data = msg.SerializeToString()
        return data

    def send_corrupt_frame(start: int, end: int):
        """Notify of corrupt or missing frame."""
        msg = ProtoHandler.message()
        msg.type = msg.PayloadType.CORRUPTFRAME
        msg.corrupt_payload.start = start
        msg.corrupt_payload.end = end
        data = msg.SerializeToString()
        return data

    def disconnect_payload():
        """Disconnect Payload."""
        reason = "Client Disconnecting".encode()
        msg = ProtoHandler.message()
        msg.type = msg.PayloadType.DISCONNECT
        msg.disconnect_payload.reason = reason
        data = msg.SerializeToString()
        return data

    def senkusha_echo(enable: bool):
        """Senkusha Echo Payload."""
        msg = ProtoHandler.message()
        msg.type = msg.PayloadType.SENKUSHA
        msg.senkusha_payload.command = SenkushaPayload.Command.ECHO_COMMAND
        msg.senkusha_payload.echo_command.state = enable
        data = msg.SerializeToString()
        return data

    def senkusha_mtu(req_id: int, mtu_req: int, num: int):
        """Senkusha MTU Payload."""
        msg = ProtoHandler.message()
        msg.type = msg.PayloadType.SENKUSHA
        msg.senkusha_payload.command = SenkushaPayload.Command.MTU_COMMAND
        msg.senkusha_payload.mtu_command.id = req_id
        msg.senkusha_payload.mtu_command.mtu_req = mtu_req
        msg.senkusha_payload.mtu_command.num = num
        data = msg.SerializeToString()
        return data

    def senkusha_mtu_client(
            state: bool, mtu_id: int, mtu_req: int,
            mtu_down: int):
        """Senkusha MTU Client Payload."""
        msg = ProtoHandler.message()
        msg.type = msg.PayloadType.SENKUSHA
        msg.senkusha_payload.command =\
            SenkushaPayload.Command.CLIENT_MTU_COMMAND
        msg.senkusha_payload.client_mtu_command.state = state
        msg.senkusha_payload.client_mtu_command.id = mtu_id
        msg.senkusha_payload.client_mtu_command.mtu_req = mtu_req
        msg.senkusha_payload.client_mtu_command.mtu_down = mtu_down
        data = msg.SerializeToString()
        return data

    def __init__(self, stream):
        self._stream = stream
        self._recv_bang = False
        self._recv_info = False

    def handle_exc(self, exc, message, p_type):
        raise exc(message)

    def _ack(self, msg, channel: int):
        chunk_flag = 1
        msg = msg.SerializeToString()
        log_bytes("Proto Send", msg)
        self._stream.send_data(msg, chunk_flag, channel, proto=True)

    def _parse_streaminfo(self, msg, video_header: bytes):
        info = {
            "video_header": video_header,
            "audio_header": msg.audio_header,
            "start_timeout": msg.start_timeout,
            "afk_timeout": msg.afk_timeout,
            "afk_timeout_disconnect": msg.afk_timeout_disconnect,
            "congestion_control_interval": msg.congestion_control_interval,
        }
        self._stream.recv_stream_info(info)

    def handle(self, data: bytes):

        msg = ProtoHandler.message()
        try:
            msg.ParseFromString(data)
        except (DecodeError, RuntimeWarning) as error:
            log_bytes(f"Protobuf Error: {error}", data)
            return
        p_type = ProtoHandler.get_payload_type(msg)
        _LOGGER.debug("RECV Payload Type: %s", p_type)

        if p_type == 'STREAMINFO':
            if not self._recv_info:
                self._recv_info = True
                res = msg.stream_info_payload.resolution[0]
                v_header = res.video_header
                s_width = self._stream.resolution['width']
                s_height = self._stream.resolution['height']
                if s_width != res.width or s_height != res.height:
                    _LOGGER.warning("RECV Unexpected resolution: %s x %s", res.width, res.height)

                _LOGGER.debug("RECV Stream Info")
                self._parse_streaminfo(
                    msg.stream_info_payload, v_header)
            channel = 9
            msg = ProtoHandler.message()
            msg.type = msg.PayloadType.STREAMINFOACK
            self._ack(msg, channel)

        elif p_type == 'BANG' and not self._recv_bang:
            _LOGGER.debug("RECV Bang")
            ecdh_pub_key = b''
            ecdh_sig = b''
            accepted = True
            if not msg.bang_payload.version_accepted:
                _LOGGER.error("Version not accepted")
                accepted = False
            if not msg.bang_payload.encrypted_key_accepted:
                _LOGGER.error("Enrypted Key not accepted")
                accepted = False
            if accepted:
                ecdh_pub_key = msg.bang_payload.ecdh_pub_key
                ecdh_sig = msg.bang_payload.ecdh_sig
                self._recv_bang = True
            self._stream.recv_bang(accepted, ecdh_pub_key, ecdh_sig)

        elif p_type == "BIG":
            return

        elif p_type == 'HEARTBEAT':
            channel = 1
            msg = ProtoHandler.message()
            msg.type = msg.PayloadType.HEARTBEAT
            self._ack(msg, channel)

        elif p_type == 'DISCONNECT':
            _LOGGER.info("Host Disconnected; Reason: %s", msg.disconnect_payload.reason)
            self._stream._stop_event.set()

        # Test Packets
        elif p_type == 'SENKUSHA':
            if self._stream._is_test and self._stream._test:
                mtu_req = msg.senkusha_payload.mtu_command.mtu_req
                mtu_sent = msg.senkusha_payload.mtu_command.mtu_sent
                self._stream._test.recv_mtu_in(mtu_req, mtu_sent)
        else:
            _LOGGER.info("RECV Unhandled Payload Type: %s", p_type)
