"""Constants for pyremoteplay."""

RP_PORT = 9295
USER_AGENT = "remoteplay Windows"
RP_VERSION = "10.0"
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
    '1080p': RESOLUTION_1080P
}

FPS_PRESETS = {
    'fps_30': 30,
    'fps_60': 60,
}
