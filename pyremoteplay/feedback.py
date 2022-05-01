"""Feedback for pyremoteplay."""
import logging
import threading
from collections import deque

from .stream_packets import FeedbackEvent, FeedbackHeader, ControllerState, StickState

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

    def __init__(self, session=None, **kwargs):
        self._session = session
        self._sequence_event = 0
        self._sequence_state = 0
        self._event_buf = deque([], Controller.MAX_EVENTS)
        self._buttons = {}
        self._params = kwargs
        self._started = False
        self._should_send = threading.Semaphore()
        self._stop_event = threading.Event()
        self._thread: threading.Thread = None
        self._last_state = ControllerState()
        self._stick_state = ControllerState()

    def __del__(self):
        self.disconnect()

    def __worker(self):
        """Worker for sending feedback packets. Run in thread."""
        self._should_send.acquire(timeout=1)
        while not self._session.is_stopped and not self._stop_event.is_set():
            self._should_send.acquire(timeout=Controller.STATE_INTERVAL_MAX_MS)
            self.send_state()
        self._session = None
        self._thread = None
        self._stop_event.clear()
        self._should_send = threading.Semaphore()
        _LOGGER.info("Controller stopped")

    def connect(self, session):
        """Connect controller to session."""
        if self._session is not None:
            _LOGGER.warning("Controller already connected. Call disconnect first")
            return
        self._session = session

    def start(self):
        """Start Controller."""
        if self._thread is not None:
            _LOGGER.warning("Controller is running. Call stop first")
            return
        if self._session is None:
            _LOGGER.warning("Controller has no session. Call connect first")
            return
        self._thread = threading.Thread(target=self.__worker, daemon=True)
        self._thread.start()

    def stop(self):
        """Stop Controller."""
        self._stop_event.set()

    def disconnect(self):
        """Stop and Disconnect Controller."""
        self.stop()
        self._session = None

    def send_state(self):
        """Send controller stick state."""
        if (
            self._session is None
            or self._session.is_stopped
            or self.stick_state == self._last_state
        ):
            return
        self._last_state.left = self.stick_state.left
        self._last_state.right = self.stick_state.right
        self._session.stream.send_feedback(
            FeedbackHeader.Type.STATE, self._sequence_state, state=self.stick_state
        )
        self._sequence_state += 1

    def send_event(self):
        """Send controller button event."""
        data = b"".join(self._event_buf)
        if not data:
            return
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
        stick_name = stick_name.lower()
        if stick_name == "left":
            stick = self._stick_state.left
        elif stick_name == "right":
            stick = self._stick_state.right
        else:
            raise ValueError("Invalid stick: Expected 'left', 'right'")

        if point is not None:
            state = StickState(*point)
            if stick_name == "left":
                self._stick_state.left = state
            else:
                self._stick_state.right = state
            self._should_send.release()
            return

        if axis is None or value is None:
            raise ValueError("Axis and Value can not be None")
        axis = axis.lower()
        values = [stick.x, stick.y]
        if axis == "x":
            values[0] = value
        elif axis == "y":
            values[1] = value
        else:
            raise ValueError("Invalid axis: Expected 'x', 'y'")
        state = StickState(*values)
        if stick_name == "left":
            self._stick_state.left = state
        else:
            self._stick_state.right = state
        self._should_send.release()

    @property
    def sequence_event(self) -> int:
        """Return Sequence Number for events."""
        return self._sequence_event

    @property
    def stick_state(self) -> ControllerState:
        """Return stick state."""
        return self._stick_state
