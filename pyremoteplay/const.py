"""Constants for pyremoteplay."""
from enum import IntEnum

PROFILE_DIR = ".pyremoteplay"
PROFILE_FILE = ".profile.json"
OPTIONS_FILE = ".options.json"
CONTROLS_FILE = ".controls.json"
RP_PORT = 9295
USER_AGENT = "remoteplay Windows"
RP_VERSION = "10.0"
DDP_PORT = 987
DDP_VERSION = '00020020'
OS_TYPE = "Win10.0.0"
TYPE_PS4 = "PS4"
TYPE_PS5 = "PS5"
RP_CRYPT_SIZE = 16


class Quality(IntEnum):
    """Enums for quality."""
    DEFAULT = 0
    VERY_LOW = 2000
    LOW = 4000
    MEDIUM = 6000
    HIGH = 10000
    VERY_HIGH = 15000


RESOLUTION_360P = {
    'width': 640,
    'height': 360,
    'bitrate': int(Quality.VERY_LOW),
}

RESOLUTION_540P = {
    'width': 960,
    'height': 540,
    'bitrate': int(Quality.MEDIUM),
}

RESOLUTION_720P = {
    'width': 1280,
    'height': 720,
    'bitrate': int(Quality.HIGH),
}

RESOLUTION_1080P = {
    'width': 1920,
    'height': 1080,
    'bitrate': int(Quality.VERY_HIGH),
}

RESOLUTION_PRESETS = {
    '360p': RESOLUTION_360P,
    '540p': RESOLUTION_540P,
    '720p': RESOLUTION_720P,
    '1080p': RESOLUTION_1080P,
}


class FPS(IntEnum):
    """Enum for FPS."""
    LOW = 30
    HIGH = 60

    def preset(fps) -> int:
        """Return FPS Value."""
        if isinstance(fps, str):
            return FPS[fps.upper()].value
        if isinstance(fps, int):
            return FPS(fps).value


class Resolution(IntEnum):
    """Enum for resolution."""
    RESOLUTION_360P = 1
    RESOLUTION_540P = 2
    RESOLUTION_720P = 3
    RESOLUTION_1080P = 4

    def preset(resolution) -> dict:
        """Return Resolution preset dict."""
        if isinstance(resolution, str):
            return RESOLUTION_PRESETS[Resolution[f"RESOLUTION_{resolution}".upper()].name.replace("RESOLUTION_", "").lower()]
        if isinstance(resolution, int):
            return RESOLUTION_PRESETS[Resolution(index).name.replace("RESOLUTION_", "").lower()]

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
