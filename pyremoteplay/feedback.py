"""Feedback for pyremoteplay."""
import logging
import threading

from construct import Bytes, Const, Struct

_LOGGER = logging.getLogger(__name__)


STICK_STATE_HEADER = bytes([
    0xa0, 0xff, 0x7f, 0xff, 0x10, 0x7f, 0xff, 0x7f, 0xff,
    0x7f, 0x99, 0x99, 0xff, 0x7f, 0xfe, 0xf7, 0xef, 0x1f,
])

STICK_STATE_STRUCT = Struct(
    "header" / Const(STICK_STATE_HEADER),
    "left_x" / Bytes(2),
    "left_y" / Bytes(2),
    "right_x" / Bytes(2),
    "right_y" / Bytes(2),
)

BUTTON_PREFIX = b'\x80'

BUTTON_ID = {
    'd_up': 0x80,
    'd_down': 0x81,
    'd_left': 0x82,
    'd_right': 0x83,
    'l1': 0x84,
    'r1': 0x85,
    'l2': 0x86,
    'r2': 0x87,
    'cross': 0x88,
    'circle': 0x89,
    'square': 0x8a,
    'triangle': 0x8b,
    'options': 0x8c,
    'share': 0x8d,
    'ps': 0x8e,
    'l3': 0x8f,
    'r3': 0x90,
    'touchpad': 0x91,
}

BUTTON_EVENT_STRUCT = Struct(
    "prefix" / Const(BUTTON_PREFIX),
    "id" / Bytes(1),
    "state" / Bytes(1),
)


def get_stick_state(stick_state: dict) -> bytes:
    """Return Stick State Packet."""
    state = STICK_STATE_STRUCT.build(stick_state)
    return state


def get_button_event(button: str, active: bool = False) -> bytes:
    """Return button event packet."""
    button_id = BUTTON_ID.get(button)
    if button_id >= 0x8c and active:
        # Some buttons have different ID for active.
        button_id += 32
    event = BUTTON_EVENT_STRUCT.build({
        "id": button_id,
        "state": 0xff if active else 0x00,
    })
    return event


# def controller_worker(controller, stop_event):
#     """Worker for socket."""
#     _LOGGER.debug("Thread Started: Controller")
#     stop_event.clear()
#     while not stop_event.is_set():
#         controller.send_state()
#     _LOGGER.info("Controller Stopped")


class Controller():
    """Stateful controller object."""

    def __init__(self, stream):
        self._stream = stream
        self._worker = None
        self._left_x = 0
        self._left_y = 0
        self._right_x = 0
        self._right_y = 0
        self._buttons = BUTTON_ID.fromkeys(BUTTON_ID, False)
        self.sequence_num = 0

    def set_button(self, button, state):
        """Update button state."""
        if not isinstance(state, bool):
            raise ValueError("Invalid State; Must be bool")
        if button not in self._buttons:
            raise ValueError(f"Invalid button: {button}")
        if self._buttons[button] != state:
            self._buttons[button] = state
            event = get_button_event(button, state)
            self.send_button_event(event)

    def send_button_event(self, event: bytes):
        """Send Button Event."""
        self._stream.send_feedback(True, event)

    def send_state(self):
        """Send state."""
        state = get_stick_state(self.stick_state)
        self._stream.send_feedback(False, state)

    @property
    def stick_state(self) -> dict:
        """Return stick state as dict."""
        return {
            "left_x": self._left_x,
            "left_y": self._left_y,
            "right_x": self._right_x,
            "right_y": self._right_y,
        }
