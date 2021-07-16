"""Constants for pyremoteplay."""
from enum import IntEnum

PROFILE_DIR = ".pyremoteplay"
PROFILE_FILE = ".profile.json"
OPTIONS_FILE = ".options.json"
MAPPING_FILE = ".mapping.json"
RP_PORT = 9295
USER_AGENT = "remoteplay Windows"
RP_VERSION = "10.0"
DDP_PORT = 987
DDP_VERSION = '00020020'
OS_TYPE = "Win10.0.0"
TYPE_PS4 = "PS4"
TYPE_PS5 = "PS5"
RP_CRYPT_SIZE = 16


RESOLUTION_360P = {
    'width': 640,
    'height': 360,
    'bitrate': 2000,
}

RESOLUTION_540P = {
    'width': 960,
    'height': 540,
    'bitrate': 6000,
}

RESOLUTION_720P = {
    'width': 1280,
    'height': 720,
    'bitrate': 10000,
}

RESOLUTION_1080P = {
    'width': 1920,
    'height': 1080,
    'bitrate': 10000,
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
