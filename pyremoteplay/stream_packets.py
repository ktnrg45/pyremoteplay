"""Stream Packets for pyremoteplay."""
import abc
import json
import logging
from base64 import b64encode
from enum import IntEnum
from struct import pack, pack_into, unpack_from
from typing import Iterable, Union

from .const import Quality, Resolution, FPS, StreamType
from .crypt import StreamCipher

_LOGGER = logging.getLogger(__name__)

STREAM_START = b"\x00\x00\x00\x40\x01\x00\x00"
A_RWND = 0x019000
OUTBOUND_STREAMS = 0x64
INBOUND_STREAMS = 0x64


class UnexpectedMessage(Exception):
    """Message not Expected."""


class UnexpectedData(Exception):
    """Data incorrect or not expected."""


LAUNCH_SPEC = {
    "sessionId": "sessionId4321",
    "streamResolutions": [
        {"resolution": {"width": None, "height": None}, "maxFps": None, "score": 10}
    ],
    "network": {
        "bwKbpsSent": None,
        "bwLoss": 0.001000,
        "mtu": None,
        "rtt": None,
        "ports": [53, 2053],
    },
    "slotId": 1,
    "appSpecification": {
        "minFps": 30,
        "minBandwidth": 0,
        "extTitleId": "ps3",
        "version": 1,
        "timeLimit": 1,
        "startTimeout": 100,
        "afkTimeout": 100,
        "afkTimeoutDisconnect": 100,
    },
    "konan": {"ps3AccessToken": "accessToken", "ps3RefreshToken": "refreshToken"},
    "requestGameSpecification": {
        "model": "bravia_tv",
        "platform": "android",
        "audioChannels": "5.1",
        "language": "sp",
        "acceptButton": "X",
        "connectedControllers": ["xinput", "ds3", "ds4"],
        "yuvCoefficient": "bt709",  # Changed from bt601
        "videoEncoderProfile": "hw4.1",
        "audioEncoderProfile": "audio1",
    },
    "userProfile": {
        "onlineId": "psnId",
        "npId": "npId",
        "region": "US",
        "languagesUsed": ["en", "jp"],
    },
    "adaptiveStreamMode": "resize",
    "videoCodec": "",
    "dynamicRange": "",
    "handshakeKey": None,
}


def get_launch_spec(
    handshake_key: bytes,
    resolution: Resolution,
    fps: FPS,
    quality: Quality,
    stream_type: StreamType,
    hdr: bool,
    rtt: int,
    mtu_in: int,
) -> bytes:
    """Return launch spec."""
    resolution = Resolution.preset(resolution)
    if quality == Quality.DEFAULT:
        bitrate = resolution["bitrate"]
    else:
        bitrate = Quality.preset(quality)
    fps = FPS.preset(fps)
    codec = StreamType.preset(stream_type)
    hdr = stream_type == StreamType.HEVC_HDR
    _LOGGER.info("Using bitrate: %s kbps", bitrate)
    launch_spec = LAUNCH_SPEC
    launch_spec["streamResolutions"][0]["resolution"]["width"] = resolution["width"]
    launch_spec["streamResolutions"][0]["resolution"]["height"] = resolution["height"]
    launch_spec["streamResolutions"][0]["maxFps"] = fps
    launch_spec["network"]["bwKbpsSent"] = bitrate
    launch_spec["network"]["mtu"] = mtu_in
    launch_spec["network"]["rtt"] = rtt
    launch_spec["videoCodec"] = "hevc" if codec == "hevc" else "avc"
    launch_spec["dynamicRange"] = "HDR" if hdr else "SDR"
    launch_spec["handshakeKey"] = b64encode(handshake_key).decode()
    launch_spec = json.dumps(launch_spec)
    launch_spec = launch_spec.replace(" ", "")  # minify
    launch_spec = launch_spec.replace(":0.001,", ":0.001000,")  # Add three zeros
    _LOGGER.debug("Length: %s, Launch Spec JSON: %s", len(launch_spec), launch_spec)
    launch_spec = launch_spec.encode()
    launch_spec = b"".join([launch_spec, b"\x00"])
    return launch_spec


class StickState:
    """State of a single stick."""

    STICK_STATE_MAX = 0x7FFF
    STICK_STATE_MIN = -0x7FFF

    def __repr__(self):
        return f"{str(self.__class__)[:-1]} x={self.x} y={self.y}>"

    def __eq__(self, other):
        if not isinstance(other, StickState):
            return False
        return self.x == other.x and self.y == other.y

    def __init__(self, x: Union[int, float] = 0, y: Union[int, float] = 0):
        self._x = self.__scale_normalize(x)
        self._y = self.__scale_normalize(y)

    def __scale_normalize(self, value: Union[int, float]):
        if isinstance(value, float):
            if value > 1.0 or value < -1.0:
                raise ValueError("Stick Value must be between -1.0 and 1.0")
            value = int(self.STICK_STATE_MAX * value)
        return max([min([self.STICK_STATE_MAX, value]), self.STICK_STATE_MIN])

    @property
    def x(self) -> int:
        """Return X value."""
        return self._x

    @x.setter
    def x(self, value: Union[int, float]):
        """Set X value."""
        self._x = self.__scale_normalize(value)

    @property
    def y(self) -> int:
        """Return Y value."""
        return self._y

    @y.setter
    def y(self, value: Union[int, float]):
        """Set Y value."""
        self._y = self.__scale_normalize(value)


class ControllerState:
    """State of both controller sticks."""

    def __repr__(self):
        return (
            f"{str(self.__class__)[:-1]} "
            f"left=({self.left.x}, {self.left.y}) "
            f"right=({self.right.x}, {self.right.y})>"
        )

    def __eq__(self, other):
        if not isinstance(other, ControllerState):
            return False
        return self.left == other.left and self.right == other.right

    def __init__(
        self,
        left: Union[StickState, Iterable[Union[int, float]], None] = None,
        right: Union[StickState, Iterable[Union[int, float]], None] = None,
    ):
        self._left: StickState = self.__state_setter(left)
        self._right: StickState = self.__state_setter(right)

    def __state_setter(
        self, state: Union[StickState, Iterable[Union[int, float]], None]
    ) -> StickState:
        _state = None
        if state is None:
            _state = StickState()
        elif isinstance(state, Iterable):
            if len(state) != 2:
                raise TypeError("Expected Iterable of length 2")
            _state = StickState(*state)
        elif isinstance(state, StickState):
            _state = state
        else:
            raise TypeError("Invalid type")
        return _state

    @property
    def left(self) -> StickState:
        """Return left state."""
        return self._left

    @left.setter
    def left(self, state: Union[StickState, Iterable[Union[int, float]], None]):
        """Set left State."""
        self._left = self.__state_setter(state)

    @property
    def right(self) -> StickState:
        """Return right state."""
        return self._right

    @right.setter
    def right(self, state: Union[StickState, Iterable[Union[int, float]], None]):
        """Set right State."""
        self._right = self.__state_setter(state)


class PacketSection(abc.ABC):
    """Abstract Packet Section."""

    LENGTH = 0

    class Type(IntEnum):
        """Abstract Type Class."""

    @staticmethod
    def parse(
        buf: bytes,  # pylint: disable=used-before-assignment
        params: Union[dict, None],
    ):
        """Return new instance from bytes."""
        raise NotImplementedError

    def __init__(self, _type: int):
        self._length = self.LENGTH if self.LENGTH != 0 else 0
        if not self._type_valid(_type):
            raise ValueError(f"Invalid type: {_type} for {self.__class__.__name__}")
        self._set_type(_type)

    def _set_type(self, _type):
        self.__type = self.__class__.Type(_type)

    def __repr__(self) -> str:
        return f"{str(self.__class__)[:-1]} type={self.type.name}>"

    def _type_valid(self, _type: int) -> bool:
        """Return True if type is valid."""
        valid = True
        if issubclass(self.__class__, PacketSection):
            valid = _type in list(self.__class__.Type)
        return valid

    def pack(self, buf: bytearray):
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


class AbstractPacket(PacketSection):
    """Abstract Packet."""

    def _set_type(self, _type):
        self.__type = Header.Type(_type)

    def _type_valid(self, _type: int) -> bool:
        """Return True if type is valid."""
        valid = True
        if issubclass(self.__class__, AbstractPacket):
            if len(self.__class__.Type) == 0:
                valid = _type in list(Header.Type)
            else:
                valid = _type in list(self.__class__.Type)
        return valid

    def bytes(
        self, cipher: Union[StreamCipher, None] = None, encrypt=False, advance_by=0
    ) -> bytes:
        """Abstract method. Return compiled bytes."""
        raise NotImplementedError

    @property
    def type(self) -> PacketSection.Type:
        """Return Packet Type."""
        return self.__type


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

    @staticmethod
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

    @staticmethod
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

    @staticmethod
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
        return pack(
            "!IIHHI", tag_local, A_RWND, OUTBOUND_STREAMS, INBOUND_STREAMS, init_tsn
        )

    @staticmethod
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

    @staticmethod
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

    @staticmethod
    def cookie(**kwargs) -> bytes:
        """Return Cookie PL. No parsing."""
        return kwargs.get("data") or b""

    @staticmethod
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
        0x00: data.__func__,
        0x01: init.__func__,
        0x02: init_ack.__func__,
        0x03: data_ack.__func__,
        0x0A: cookie.__func__,
        0x0B: cookie_ack.__func__,
    }

    @staticmethod
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
        self.payload = Chunk.PAYLOADS[chunk_type](**kwargs)

    def pack(self, buf: bytearray):
        """Pack buffer with compiled bytes."""
        if self.flag < 0 or not isinstance(self.flag, int):
            raise ValueError(f"Chunk flag: {self.flag} is not valid")
        pack_into("!BB", buf, Header.LENGTH, self.type, self.flag)

    @property
    def length(self) -> int:
        """Return size in bytes."""
        return len(self.payload) + 4


class Packet(AbstractPacket):
    """Generic RP Packet."""

    @staticmethod
    def is_av(header_type: bytes) -> int:  # pylint: disable=used-before-assignment
        """Return AV type if packet is AV else return 0."""
        av_mask = int.from_bytes(header_type, "big") & 0x0F
        if av_mask in [Header.Type.VIDEO, Header.Type.AUDIO]:
            return av_mask
        return 0

    @staticmethod
    def parse(buf: bytes, params=None):
        """Return new instance from bytes.

        Params is updated when parsing each section.
        """
        buf = bytearray(buf)
        params = {}
        av_mask = Packet.is_av(buf[:1])
        if av_mask:
            return AVPacket(av_mask, buf, **params)
        h_type = Header.parse(buf, params)
        c_type = Chunk.parse(buf, params)
        return Packet(h_type, c_type, **params)

    def __repr__(self) -> str:
        return (
            f"{str(self.__class__)[:-1]} "
            f"type={self.type.name} chunk={self.chunk.type.name} "
            f"flag={self.chunk.flag}>"
        )

    def __init__(self, header_type: int, chunk_type: int, **kwargs):
        super().__init__(header_type)
        self.header = Header(header_type, **kwargs)
        self.chunk = Chunk(chunk_type, **kwargs)
        self.params = kwargs

    def bytes(self, cipher=None, encrypt=False, advance_by=0) -> bytes:
        """Pack compiled bytes."""
        buf = bytearray(self.header.length + self.chunk.length)
        self.header.pack(buf)
        self.chunk.pack(buf)
        pack_into(
            "!H", buf, self.header.length + 2, self.chunk.length
        )  # Pack Chunk length
        payload = self.chunk.payload
        if cipher is not None:
            key_pos = cipher.key_pos
            if encrypt:
                payload = cipher.encrypt(payload)
        buf[self.header.length + 4 :] = payload
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


class AVPacket(AbstractPacket):
    """AV Packet. Parsing capability only."""

    class Type(IntEnum):
        """Enums for AV Packet."""

        VIDEO = Header.Type.VIDEO
        AUDIO = Header.Type.AUDIO

    def __repr__(self) -> str:
        nalu = self.nalu.hex() if self.has_nalu else None
        return (
            f"{str(self.__class__)[:-1]} "
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

    # pylint: disable=unused-argument
    def __init__(self, av_type: int, buf: bytearray, **kwargs):
        super().__init__(av_type)
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

        offset = 1
        if self.type == Header.Type.VIDEO:
            offset = 3
            self._unit_index = (self._dword2 >> 0x15) & 0x7FF
            self._adaptive_stream_index = unpack_from("!b", buf, 20)[0] >> 5
        else:
            self._unit_index = (self._dword2 >> 0x18) & 0xFF
        if self.has_nalu:
            # Unknown ushort at 18
            self._nalu = buf[18 + offset + 1 : 18 + offset + 3]
            offset += 3
        self._data = buf[18 + offset :]

        self._get_frame_meta()

    def _get_frame_meta(self):
        self._frame_meta = {
            "frame": self.frame_index,
            "index": self.unit_index,
        }
        if self.type == Header.Type.VIDEO:
            total = ((self._dword2 >> 0xA) & 0x7FF) + 1
            fec = self._dword2 & 0x3FF
            src = total - fec
            units = {
                "total": total,
                "fec": fec,
                "src": src,
            }
        else:
            _dword2 = self._dword2 & 0xFFFF
            total = ((self._dword2 >> 0x10) & 0xFF) + 1
            fec = (_dword2 >> 4) & 0x0F
            src = _dword2 & 0x0F
            size = _dword2 >> 8
            units = {
                "total": total,
                "fec": fec,
                "src": src,
                "size": size,
            }
        self._frame_meta["units"] = units

    def decrypt(self, cipher):
        """Decrypt AV Data."""
        if self.encrypted:
            self._data = cipher.decrypt(self.data, self.key_pos)
            self._encrypted = False

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
        return self._frame_meta["units"]["total"]

    @property
    def frame_length_src(self) -> int:
        """Return the length of src units."""
        return self._frame_meta["units"]["src"]

    @property
    def frame_length_fec(self) -> int:
        """Return the length of fec units."""
        return self._frame_meta["units"]["fec"]

    @property
    def frame_size_audio(self) -> int:
        """Return frame size for audio packet."""
        return self._frame_meta["units"]["size"]

    @property
    def codec(self) -> int:
        """Return the codec value."""
        return self._codec

    @property
    def key_pos(self) -> int:
        """Return the key position for decryption."""
        return self._key_pos

    @property
    def adaptive_stream_index(self) -> int:
        """Return the Adaptive Stream Index."""
        return self._adaptive_stream_index

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
        return f"{str(self.__class__)[:-1]} type={self.type}>"

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

    PREFIX = bytes(
        [
            0xA0,
            0xFF,
            0x7F,
            0xFF,
            0x7F,
            0xFF,
            0x7F,
            0xFF,
            0x7F,
            0x99,
            0x99,
            0xFF,
            0x7F,
            0xFE,
            0xF7,
            0xEF,
            0x1F,
        ]
    )

    class Type(IntEnum):
        """Enums for State."""

        STATE = 0

    def __repr__(self) -> str:
        return f"{str(self.__class__)[:-1]} state={self.state}>"

    def __init__(self, state_type, **kwargs):
        super().__init__(state_type)
        self.state: ControllerState = kwargs.get("state") or ControllerState()

    def pack(self, buf: bytearray):
        """Pack compiled bytes."""
        pack_into(
            "!17shhhh",
            buf,
            FeedbackHeader.LENGTH,
            self.PREFIX,
            self.state.left.x,
            self.state.left.y,
            self.state.right.x,
            self.state.right.y,
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
        SQUARE = 0x8A
        TRIANGLE = 0x8B
        OPTIONS = 0x8C
        SHARE = 0x8D
        PS = 0x8E
        L3 = 0x8F
        R3 = 0x90
        TOUCHPAD = 0x91

    def __repr__(self) -> str:
        return f"{str(self.__class__)[:-1]} button={self.type.name} state={self.state}>"

    def __init__(self, button_type: int, **kwargs):
        super().__init__(button_type)
        self._is_active = kwargs.get("is_active")
        self._button_id = 0

    def pack(self, buf: bytearray):
        """Pack compiled bytes."""
        pack_into("!BBB", buf, 0, self.PREFIX, self.button_id, self.state)

    @property
    def state(self) -> int:
        """Return State."""
        return 0xFF if self.is_active else 0x00

    @property
    def button_id(self) -> int:
        """Return Button ID."""
        if not self._button_id:
            button_id = int(self.type)
            if button_id >= 0x8C and self.is_active:
                # Some buttons have different ID for active.
                self._button_id = button_id + 32
            else:
                self._button_id = button_id
        return self._button_id

    @property
    def is_active(self) -> bool:
        """Return True if button is pressed."""
        return self._is_active


class FeedbackPacket(AbstractPacket):
    """Feedback Packet."""

    class Type(IntEnum):
        """Enums for Feedback Packet."""

        EVENT = FeedbackHeader.Type.EVENT
        STATE = FeedbackHeader.Type.STATE

    def __repr__(self) -> str:
        return f"{str(self.__class__)[:-1]} type={self.header.type.name}>"

    def __init__(self, feedback_type: int, **kwargs):
        super().__init__(feedback_type)
        self.header = FeedbackHeader(feedback_type, **kwargs)
        self.chunk = None
        self.data = kwargs.get("data") or b""
        if feedback_type == FeedbackHeader.Type.EVENT:
            if not self.data:
                raise ValueError("Button must to be specified")
        else:
            self.chunk = FeedbackState(FeedbackState.Type.STATE, **kwargs)
        self.params = kwargs

    def bytes(self, cipher=None, encrypt=False, advance_by=0) -> bytes:
        """Pack compiled bytes."""
        data_length = (
            len(self.data)
            if self.header.type == FeedbackHeader.Type.EVENT
            else FeedbackState.LENGTH
        )
        length = data_length + FeedbackHeader.LENGTH
        buf = bytearray(length)
        self.header.pack(buf)
        if self.header.type == FeedbackHeader.Type.EVENT:
            pack_into(f"!{data_length}s", buf, FeedbackHeader.LENGTH, self.data)
        else:
            self.chunk.pack(buf)

        if cipher is not None:  # Should always require cipher.
            payload = buf[FeedbackHeader.LENGTH :]
            self.header.key_pos = cipher.key_pos
            self.header.pack(buf)
            advance_by = data_length
            if encrypt:
                payload = cipher.encrypt(payload)
                buf[FeedbackHeader.LENGTH :] = payload
            # gmac needs to be 0 when calculating gmac
            gmac = cipher.get_gmac(bytes(buf))
            gmac = int.from_bytes(gmac, "big")
            self.header.gmac = gmac
            self.header.pack(buf)
            cipher.advance_key_pos(advance_by)
        return bytes(buf)


class CongestionPacket(AbstractPacket):
    """Congestion Packet."""

    LENGTH = 15

    class Type(IntEnum):
        """Enums for Buttons."""

        CONGESTION = Header.Type.CONGESTION

    def __repr__(self) -> str:
        return (
            f"{str(self.__class__)[:-1]} "
            f"type={self.type.name} "
            f"received={self.received} "
            f"lost={self.lost}>"
        )

    def __init__(self, **kwargs):
        super().__init__(CongestionPacket.Type.CONGESTION)
        self.received = kwargs.get("received") or 0
        self.lost = kwargs.get("lost") or 0

    def bytes(self, cipher=None, encrypt=False, advance_by=0) -> bytes:
        """Return compiled bytes."""
        key_pos = cipher.key_pos if cipher else 0
        buf = bytearray(CongestionPacket.LENGTH)
        gmac = 0
        pack_into(
            "!BxxHHII", buf, 0, self.type, self.received, self.lost, gmac, key_pos
        )
        if cipher:
            gmac = cipher.get_gmac(bytes(buf))
            gmac = int.from_bytes(gmac, "big")
            pack_into("!I", buf, 7, gmac)
            cipher.advance_key_pos(CongestionPacket.LENGTH)
        return bytes(buf)
