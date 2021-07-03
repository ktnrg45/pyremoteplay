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
        self._left_x = 0
        self._left_y = 0
        self._right_x = 0
        self._right_y = 0
        self._sequence_event = 0
        self._sequence_state = 0
        self._event_buf = deque([], Controller.MAX_EVENTS)
        self._event_queue = []
        self._buttons = {}
        self._params = kwargs
        self._started = False

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

    @property
    def sequence_event(self) -> int:
        """Return Sequence Number for events."""
        return self._sequence_event

    @property
    def stick_state(self) -> dict:
        """Return stick state as dict."""
        return {
            "left": {"x": self._left_x, "y": self._left_y},
            "right": {"x": self._right_x, "y": self._right_y},
        }

    @property
    def has_sticks(self) -> bool:
        """Return True if has sticks."""
        has_sticks = self._params.get("has_sticks")
        if has_sticks:
            return True
        return False
