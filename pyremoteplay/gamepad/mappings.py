"""Mappings for Gamepad."""
from enum import IntEnum, auto
from pyremoteplay.stream_packets import FeedbackEvent

DEFAULT_DEADZONE = 0.1

TRIGGERS = (FeedbackEvent.Type.R2.name, FeedbackEvent.Type.L2.name)


class AxisType(IntEnum):
    """Axis Type Enum."""

    LEFT_X = auto()
    LEFT_Y = auto()
    RIGHT_X = auto()
    RIGHT_Y = auto()


DUALSHOCK4_MAP = {
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

DUALSENSE_MAP = {
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
        # 16: MIC Button
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

# HAT_UP = hy = 1
# HAT_DOWN = hy = -1
# HAT_RIGHT = hx = 1
# HAT_LEFT = hx = -1

DEFAULT_MAPS = {
    "PS4 Controller": DUALSHOCK4_MAP,
    "PS5 Controller": DUALSENSE_MAP,
}
