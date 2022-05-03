"""Constants for pyremoteplay."""
from __future__ import annotations
from enum import IntEnum
from typing import Union

PROFILE_DIR = ".pyremoteplay"
PROFILE_FILE = ".profile.json"
OPTIONS_FILE = ".options.json"
CONTROLS_FILE = ".controls.json"

RP_CRYPT_SIZE = 16
DEFAULT_POLL_COUNT = 10

OS_TYPE = "Win10.0.0"
USER_AGENT = "remoteplay Windows"
RP_VERSION_PS4 = "10.0"
RP_VERSION_PS5 = "1.0"
TYPE_PS4 = "PS4"
TYPE_PS5 = "PS5"

BROADCAST_IP = "255.255.255.255"
RP_PORT = 9295
DDP_PORT_PS4 = 987
DDP_PORT_PS5 = 9302
UDP_PORT = 0
DEFAULT_UDP_PORT = 9303  # Necessary for PS5
DDP_PORTS = {
    TYPE_PS4: DDP_PORT_PS4,
    TYPE_PS5: DDP_PORT_PS5,
}

DEFAULT_STANDBY_DELAY = 50

FFMPEG_PADDING = 64  # AV_INPUT_BUFFER_PADDING_SIZE


class StreamType(IntEnum):
    """Enums for Stream type. Represents Video stream type.

    Do Not Change.
    """

    H264 = 1
    HEVC = 2
    HEVC_HDR = 3

    @staticmethod
    def parse(value: Union[StreamType, str, int]) -> StreamType:
        """Return Enum from enum, name or value."""
        if isinstance(value, StreamType):
            return value
        if isinstance(value, str):
            _enum = StreamType.__members__.get(value.upper())
            if _enum is not None:
                return _enum
        return StreamType(value)

    @staticmethod
    def preset(value: Union[StreamType, str, int]) -> str:
        """Return Stream Type name."""
        return StreamType.parse(value).name.replace("_HDR", "").lower()


class Quality(IntEnum):
    """Enums for quality. Value represents video bitrate."""

    DEFAULT = 0
    VERY_LOW = 2000
    LOW = 4000
    MEDIUM = 6000
    HIGH = 10000
    VERY_HIGH = 15000

    @staticmethod
    def parse(value: Union[Quality, str, int]) -> Quality:
        """Return Enum from enum, name or value."""
        if isinstance(value, Quality):
            return value
        if isinstance(value, str):
            _enum = Quality.__members__.get(value.upper())
            if _enum is not None:
                return _enum
        return Quality(value)

    @staticmethod
    def preset(value: Union[Quality, str, int]) -> int:
        """Return Quality Value."""
        return Quality.parse(value).value


RESOLUTION_360P = {
    "width": 640,
    "height": 360,
    "bitrate": int(Quality.VERY_LOW),
}

RESOLUTION_540P = {
    "width": 960,
    "height": 540,
    "bitrate": int(Quality.MEDIUM),
}

RESOLUTION_720P = {
    "width": 1280,
    "height": 720,
    "bitrate": int(Quality.HIGH),
}

RESOLUTION_1080P = {
    "width": 1920,
    "height": 1080,
    "bitrate": int(Quality.VERY_HIGH),
}

RESOLUTION_PRESETS = {
    "360p": RESOLUTION_360P,
    "540p": RESOLUTION_540P,
    "720p": RESOLUTION_720P,
    "1080p": RESOLUTION_1080P,
}


class FPS(IntEnum):
    """Enum for FPS."""

    LOW = 30
    HIGH = 60

    @staticmethod
    def parse(value: Union[FPS, str, int]) -> FPS:
        """Return Enum from enum, name or value."""
        if isinstance(value, FPS):
            return value
        if isinstance(value, str):
            _enum = FPS.__members__.get(value.upper())
            if _enum is not None:
                return _enum
        return FPS(value)

    @staticmethod
    def preset(value: Union[FPS, str, int]) -> int:
        """Return FPS Value."""
        return FPS.parse(value).value


class Resolution(IntEnum):
    """Enum for resolution."""

    RESOLUTION_360P = 1
    RESOLUTION_540P = 2
    RESOLUTION_720P = 3
    RESOLUTION_1080P = 4

    @staticmethod
    def parse(value: Union[Resolution, str, int]) -> Resolution:
        """Return Enum from enum, name or value."""
        if isinstance(value, Resolution):
            return value
        if isinstance(value, str):
            _value = f"RESOLUTION_{value}".upper()
            _enum = Resolution.__members__.get(_value.upper())
            if _enum is not None:
                return _enum
        return Resolution(value)

    @staticmethod
    def preset(value: Union[Resolution, str, int]) -> dict:
        """Return Resolution preset dict."""
        enum = Resolution.parse(value)
        return RESOLUTION_PRESETS[enum.name.replace("RESOLUTION_", "").lower()]


# AV_CODEC_OPTIONS_H264 = {
#     # "profile": "high",
#     # "level": "3.2",
#     "tune": "zerolatency",
#     "cabac": "1",
#     "ref": "3",
#     "deblock": "1:0:0",
#     "analyse": "0x3:0x113",
#     "me": "hex",
#     "subme": "7",
#     "psy": "1",
#     "psy_rd": "1.00:0.00",
#     "mixed_ref": "1",
#     "me_range": "16",
#     "chroma_me": "1",
#     "trellis": "1",
#     "8x8dct": "1",
#     "cqm": "0",
#     "deadzone": "21,11",
#     "fast_pskip": "1",
#     "chroma_qp_offset": "-2",
#     "threads": "9",
#     "lookahead_threads": "1",
#     "sliced_threads": "0",
#     "nr": "0",
#     "decimate": "1",
#     "interlaced": "0",
#     "bluray_compat": "0",
#     "constrained_intra": "0",
#     "bframes": "3",
#     "b_pyramid": "2",
#     "b_adapt": "1",
#     "b_bias": "0",
#     "direct": "1",
#     "weightb": "1",
#     "open_gop": "0",
#     "weightp": "2",
#     "keyint": "250",
#     "keyint_min": "25",
#     "scenecut": "40",
#     "intra_refresh": "0",
#     "rc_lookahead": "40",
#     "rc": "crf",
#     "mbtree": "1",
#     "crf": "23.0",
#     "qcomp": "0.60",
#     "qpmin": "0",
#     "qpmax": "69",
#     "qpstep": "4",
#     "ip_ratio": "1.40",
#     "aq": "1:1.00",
# }
