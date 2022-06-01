"""Mappings for Gamepad."""
from __future__ import annotations
from enum import IntEnum, auto
from pyremoteplay.stream_packets import FeedbackEvent

TRIGGERS = (FeedbackEvent.Type.R2.name, FeedbackEvent.Type.L2.name)


class AxisType(IntEnum):
    """Axis Type Enum."""

    LEFT_X = auto()
    LEFT_Y = auto()
    RIGHT_X = auto()
    RIGHT_Y = auto()


class HatType(IntEnum):
    """Hat Type Enum."""

    left = auto()
    right = auto()
    down = auto()
    up = auto()

    # HAT_UP = hy = 1
    # HAT_DOWN = hy = -1
    # HAT_RIGHT = hx = 1
    # HAT_LEFT = hx = -1


def rp_map_keys() -> list[str]:
    """Return RP Mapping Keys."""
    keys = [item.name for item in FeedbackEvent.Type]
    keys.extend([item.name for item in AxisType])
    return keys


def dualshock4_map() -> dict:
    """Return Dualshock4 Map."""
    return {
        "button": {
            0: FeedbackEvent.Type.CROSS.name,
            1: FeedbackEvent.Type.CIRCLE.name,
            2: FeedbackEvent.Type.SQUARE.name,
            3: FeedbackEvent.Type.TRIANGLE.name,
            4: FeedbackEvent.Type.SHARE.name,
            5: FeedbackEvent.Type.PS.name,
            6: FeedbackEvent.Type.OPTIONS.name,
            7: FeedbackEvent.Type.L3.name,
            8: FeedbackEvent.Type.R3.name,
            9: FeedbackEvent.Type.L1.name,
            10: FeedbackEvent.Type.R1.name,
            11: FeedbackEvent.Type.UP.name,
            12: FeedbackEvent.Type.DOWN.name,
            13: FeedbackEvent.Type.LEFT.name,
            14: FeedbackEvent.Type.RIGHT.name,
            15: FeedbackEvent.Type.TOUCHPAD.name,
        },
        "axis": {
            0: AxisType.LEFT_X.name,
            1: AxisType.LEFT_Y.name,
            2: AxisType.RIGHT_X.name,
            3: AxisType.RIGHT_Y.name,
            4: FeedbackEvent.Type.L2.name,
            5: FeedbackEvent.Type.R2.name,
        },
        "hat": {},
    }


def dualsense_map() -> dict:
    """Return DualSense Map."""
    return {
        "button": {
            0: FeedbackEvent.Type.CROSS.name,
            1: FeedbackEvent.Type.CIRCLE.name,
            2: FeedbackEvent.Type.SQUARE.name,
            3: FeedbackEvent.Type.TRIANGLE.name,
            4: FeedbackEvent.Type.SHARE.name,  # CREATE
            5: FeedbackEvent.Type.PS.name,
            6: FeedbackEvent.Type.OPTIONS.name,
            7: FeedbackEvent.Type.L3.name,
            8: FeedbackEvent.Type.R3.name,
            9: FeedbackEvent.Type.L1.name,
            10: FeedbackEvent.Type.R1.name,
            11: FeedbackEvent.Type.UP.name,
            12: FeedbackEvent.Type.DOWN.name,
            13: FeedbackEvent.Type.LEFT.name,
            14: FeedbackEvent.Type.RIGHT.name,
            15: FeedbackEvent.Type.TOUCHPAD.name,
            16: None,  # MIC Button
        },
        "axis": {
            0: AxisType.LEFT_X.name,
            1: AxisType.LEFT_Y.name,
            2: AxisType.RIGHT_X.name,
            3: AxisType.RIGHT_Y.name,
            4: FeedbackEvent.Type.L2.name,
            5: FeedbackEvent.Type.R2.name,
        },
        "hat": {},
    }


def xbox360_map() -> dict:
    """Return XBOX 360 Map."""
    return {
        "button": {
            0: FeedbackEvent.Type.CROSS.name,
            1: FeedbackEvent.Type.CIRCLE.name,
            2: FeedbackEvent.Type.SQUARE.name,
            3: FeedbackEvent.Type.TRIANGLE.name,
            4: FeedbackEvent.Type.L1.name,
            5: FeedbackEvent.Type.R1.name,
            6: FeedbackEvent.Type.SHARE.name,
            7: FeedbackEvent.Type.OPTIONS.name,
            8: FeedbackEvent.Type.L3.name,
            9: FeedbackEvent.Type.R3.name,
            10: FeedbackEvent.Type.PS.name,
        },
        "axis": {
            0: AxisType.LEFT_X.name,
            1: AxisType.LEFT_Y.name,
            2: FeedbackEvent.Type.L2.name,
            3: AxisType.RIGHT_X.name,
            4: AxisType.RIGHT_Y.name,
            5: FeedbackEvent.Type.R2.name,
        },
        "hat": {
            0: {
                HatType.left.name: FeedbackEvent.Type.LEFT.name,
                HatType.right.name: FeedbackEvent.Type.RIGHT.name,
                HatType.down.name: FeedbackEvent.Type.DOWN.name,
                HatType.up.name: FeedbackEvent.Type.UP.name,
            },
        },
    }


def default_maps():
    """Return Default Maps."""
    return {
        "PS4 Controller": dualshock4_map(),
        "PS5 Controller": dualsense_map(),
        "Xbox 360 Controller": xbox360_map(),
    }
