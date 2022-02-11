"""Feedback for pyremoteplay."""
import logging
import threading
from collections import deque

from .stream_packets import FeedbackEvent, FeedbackHeader

_LOGGER = logging.getLogger(__name__)


class Controller:
    """Emulated controller input."""

    MAX_EVENTS = 16
    ACTION_TAP = "tap"
    ACTION_RELEASE = "release"
    ACTION_PRESS = "press"
    ACTIONS = (ACTION_TAP, ACTION_RELEASE, ACTION_PRESS)
    STATE_INTERVAL_MAX_MS = 0.200
    STATE_INTERVAL_MIN_MS = 0.100
    STICK_STATE_MAX = 0x7FFF
    STICK_STATE_MIN = -0x7FFF

    def __init__(self, session, **kwargs):
        self._session = session
        self._sequence_event = 0
        self._sequence_state = 0
        self._event_buf = deque([], Controller.MAX_EVENTS)
        self._buttons = {}
        self._params = kwargs
        self._started = False
        self._should_send = threading.Event()

        self._stick_state = {
            "left": {"x": 0, "y": 0},
            "right": {"x": 0, "y": 0},
        }

    def worker(self):
        """Worker for sending feedback packets. Run in thread."""
        while not self._session.is_stopped:
            try:
                self._should_send.wait(timeout=1.0)
            except Exception as error:
                _LOGGER.error(error)
                continue
            self.send_state()
            self._should_send.clear()
        _LOGGER.info("Controller stopped")

    def send_state(self):
        """Send controller stick state."""
        self._session.stream.send_feedback(
            FeedbackHeader.Type.STATE, self._sequence_state, state=self.stick_state
        )
        self._sequence_state += 1

    def send_event(self):
        """Send controller button event."""
        data = b"".join(self._event_buf)
        self._session.stream.send_feedback(
            FeedbackHeader.Type.EVENT, self.sequence_event, data=data
        )
        self._sequence_event += 1

    def add_event_buffer(self, event: FeedbackEvent):
        """Append event to beginning of byte buf."""
        buf = bytearray(FeedbackEvent.LENGTH)
        event.pack(buf)
        self._event_buf.appendleft(buf)

    def button(self, name: str, action="tap"):
        """Emulate pressing or releasing button."""
        if action not in self.ACTIONS:
            raise ValueError(f"Invalid Action: {action}")
        try:
            button = int(FeedbackEvent.Type[name.upper()])
        except KeyError:
            _LOGGER.error("Invalid button: %s", name)
        else:
            if action == self.ACTION_PRESS:
                self.add_event_buffer(FeedbackEvent(button, is_active=True))
            elif action == self.ACTION_RELEASE:
                self.add_event_buffer(FeedbackEvent(button, is_active=False))
            elif action == self.ACTION_TAP:
                self.add_event_buffer(FeedbackEvent(button, is_active=True))
                self.add_event_buffer(FeedbackEvent(button, is_active=False))
            self.send_event()

    def stick(self, stick: str, axis: str = None, value: float = None, point=None):
        """Set Stick Value."""

        def check_value(value):
            if not isinstance(value, float):
                raise ValueError("Invalid value: Expected float")
            if value > 1.0 or value < -1.0:
                raise ValueError("Stick Value must be between -1.0 and 1.0")

        def scale_value(value):
            value = int(Controller.STICK_STATE_MAX * value)
            return max(
                [min([Controller.STICK_STATE_MAX, value]), Controller.STICK_STATE_MIN]
            )

        stick = stick.lower()
        if stick not in ("left", "right"):
            raise ValueError("Invalid stick: Expected 'left', 'right'")

        if point is not None:
            if len(point) != 2:
                raise ValueError("Point must have two values")
            for val in point:
                check_value(val)
            val_x, val_y = point
            val_x = scale_value(val_x)
            val_y = scale_value(val_y)
            self._stick_state[stick]["x"] = val_x
            self._stick_state[stick]["y"] = val_y
            # Try to avoid deadlock
            if not self._should_send.is_set():
                self._should_send.set()
            return

        if axis is None or value is None:
            raise ValueError("Axis and Value can not be None")
        axis = axis.lower()
        if axis not in ("x", "y"):
            raise ValueError("Invalid axis: Expected 'x', 'y'")
        check_value(value)
        value = scale_value(value)
        self._stick_state[stick][axis] = value
        # Try to avoid deadlock
        if not self._should_send.is_set():
            self._should_send.set()

    @property
    def sequence_event(self) -> int:
        """Return Sequence Number for events."""
        return self._sequence_event

    @property
    def stick_state(self) -> dict:
        """Return stick state as dict."""
        return self._stick_state
