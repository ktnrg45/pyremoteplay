"""Feedback for pyremoteplay."""
import logging
import threading
from collections import deque

from .stream_packets import FeedbackEvent, FeedbackHeader, ControllerState

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
        self._should_send = threading.Semaphore()

        self._stick_state = ControllerState()

    def worker(self):
        """Worker for sending feedback packets. Run in thread."""
        self._should_send.acquire(timeout=1)
        while not self._session.is_stopped:
            if not self._should_send.acquire(timeout=Controller.STATE_INTERVAL_MAX_MS):
                self.send_state()
                continue
            self.send_state()
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

    def stick(self, stick_name: str, axis: str = None, value: float = None, point=None):
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

        stick_name = stick_name.lower()
        if stick_name == "left":
            stick = self._stick_state.left
        elif stick_name == "right":
            stick = self._stick_state.right
        else:
            raise ValueError("Invalid stick: Expected 'left', 'right'")

        if point is not None:
            if len(point) != 2:
                raise ValueError("Point must have two values")
            for val in point:
                check_value(val)
            val_x, val_y = point
            val_x = scale_value(val_x)
            val_y = scale_value(val_y)
            stick.x = val_x
            stick.y = val_y
            self._should_send.release()
            return

        if axis is None or value is None:
            raise ValueError("Axis and Value can not be None")
        axis = axis.lower()
        check_value(value)
        value = scale_value(value)
        if axis == "x":
            stick.x = value
        elif axis == "y":
            stick.y = value
        else:
            raise ValueError("Invalid axis: Expected 'x', 'y'")
        self._should_send.release()

    @property
    def sequence_event(self) -> int:
        """Return Sequence Number for events."""
        return self._sequence_event

    @property
    def stick_state(self) -> ControllerState:
        """Return stick state."""
        return self._stick_state
