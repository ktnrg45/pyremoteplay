"""Feedback for pyremoteplay."""
import logging
import threading
import time
from collections import deque

from .stream_packets import FeedbackEvent, FeedbackHeader

_LOGGER = logging.getLogger(__name__)


class Controller():
    """Emulated controller input."""
    MAX_EVENTS = 16
    ACTION_TAP = "tap"
    ACTION_RELEASE = "release"
    ACTION_PRESS = "press"
    ACTIONS = (ACTION_TAP, ACTION_RELEASE, ACTION_PRESS)
    STATE_INTERVAL_MAX_MS = 0.200
    STATE_INTERVAL_MIN_MS = 0.100
    STICK_STATE_MAX = 0x7fff
    STICK_STATE_MIN = -0x7fff

    def __init__(self, ctrl, **kwargs):
        self._ctrl = ctrl
        self._sequence_event = 0
        self._sequence_state = 0
        self._event_buf = deque([], Controller.MAX_EVENTS)
        self._event_queue = []
        self._buttons = {}
        self._has_sticks = False
        self._params = kwargs
        self._started = False

        self._stick_state = {
            "left": {"x": 0, "y": 0},
            "right": {"x": 0, "y": 0},
        }

    def start(self):
        """Start controller worker."""
        if self._started:
            _LOGGER.error("Controller already started")
            return
        if not self._ctrl._stream.cipher:
            raise RuntimeError("Stream has no cipher")
        self._worker = threading.Thread(
            target=self.worker,
        )
        self._worker.start()

    def worker(self):
        self._started = True
        _LOGGER.info("Controller Started")
        while not self._ctrl.state == self._ctrl.STATE_STOP:
            self.send()
            time.sleep(self.STATE_INTERVAL_MIN_MS)
        _LOGGER.info("Controller Stopped")

    def enable_sticks(self):
        """Enable sticks."""
        self._has_sticks = True

    def send(self):
        if self.has_sticks:
            self._ctrl._stream.send_feedback(FeedbackHeader.Type.STATE, self._sequence_state, state=self.stick_state)
            self._sequence_state += 1
        if self._event_queue:
            self.add_event_buffer(self._event_queue.pop(0))
            data = b"".join(self._event_buf)
            self._ctrl._stream.send_feedback(FeedbackHeader.Type.EVENT, self.sequence_event, data=data)
            self._sequence_event += 1

    def add_event_buffer(self, event: FeedbackEvent):
        """Append event to end of byte buf."""
        msg = event.bytes()
        self._event_buf.appendleft(msg)

    def add_event_queue(self, event: FeedbackEvent):
        """Append event to queue."""
        self._event_queue.append(event)

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
                self.add_event_queue(FeedbackEvent(button, is_active=True))
            elif action == self.ACTION_RELEASE:
                self.add_event_queue(FeedbackEvent(button, is_active=False))
            elif action == self.ACTION_TAP:
                self.add_event_queue(FeedbackEvent(button, is_active=True))
                self.add_event_queue(FeedbackEvent(button, is_active=False))
            self.send()

    def stick(self, stick: str, axis: str, value: int):
        """Set Stick Value."""
        stick = stick.lower()
        axis = axis.lower()

        if stick not in ("left", "right"):
            raise ValueError("Invalid stick: Expected 'left', 'right'")
        if axis not in ("x", "y"):
            raise ValueError("Invalid axis: Expected 'x', 'y'")
        if not isinstance(value, int):
            raise ValueError("Invalid value: Expected Int")
        if value > Controller.STICK_STATE_MAX:
            value = Controller.STICK_STATE_MAX
        elif value < Controller.STICK_STATE_MIN:
            value = Controller.STICK_STATE_MIN

        self._stick_state[stick][axis] = value
        self.send()

    @property
    def sequence_event(self) -> int:
        """Return Sequence Number for events."""
        return self._sequence_event

    @property
    def stick_state(self) -> dict:
        """Return stick state as dict."""
        return self._stick_state

    @property
    def has_sticks(self) -> bool:
        """Return True if has sticks."""
        return self._has_sticks
