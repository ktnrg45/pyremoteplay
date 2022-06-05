"""Controller methods."""
from __future__ import annotations
import logging
import threading
import sys
import traceback
from typing import Iterable, TYPE_CHECKING, Union
from collections import deque
from enum import IntEnum, auto
import time

from .stream_packets import FeedbackEvent, FeedbackHeader, ControllerState, StickState

if TYPE_CHECKING:
    from .session import Session

_LOGGER = logging.getLogger(__name__)


class Controller:
    """Controller Interface. Sends user input to Remote Play Session."""

    class ButtonAction(IntEnum):
        """Button Action Types."""

        PRESS = auto()
        RELEASE = auto()
        TAP = auto()

    MAX_EVENTS = 16
    STATE_INTERVAL_MAX_MS = 0.200
    STATE_INTERVAL_MIN_MS = 0.100

    @staticmethod
    def buttons() -> list:
        """Return list of valid buttons."""
        return [button.name for button in FeedbackEvent.Type]

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
        while self.running:
            try:
                self._should_send.acquire(timeout=Controller.STATE_INTERVAL_MAX_MS)
                self.update_sticks()
            except Exception as error:  # pylint: disable=broad-except
                _LOGGER.error("Error in controller thread: %s", error)
                if _LOGGER.level == logging.DEBUG:
                    exc_type, exc_value, exc_traceback = sys.exc_info()
                    traceback.print_exception(
                        exc_type, exc_value, exc_traceback, file=sys.stdout
                    )
        self._session = None
        self._thread = None
        self._stop_event.clear()
        self._should_send = threading.Semaphore()
        _LOGGER.info("Controller stopped")

    def connect(self, session: Session):
        """Connect controller to session."""
        if not isinstance(session, Session):
            raise TypeError(f"Expected instance of {Session}")
        if self._session is not None:
            _LOGGER.warning("Controller already connected. Call disconnect first")
            return
        self._session = session

    def start(self):
        """Start Controller.
        This starts the controller worker which listens for when the sticks changes and sends the state to the host.
        If this is not called, the 'update_sticks()' method needs to be called for the host to receive the state.
        """
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

    def update_sticks(self):
        """Send controller stick state to host.

        Will be called automatically if controller has been started.
        """
        if (
            self.session is None
            or self.session.is_stopped
            or self.stick_state == self._last_state
        ):
            return
        self._last_state.left = self.stick_state.left
        self._last_state.right = self.stick_state.right
        self._session.stream.send_feedback(
            FeedbackHeader.Type.STATE, self._sequence_state, state=self.stick_state
        )
        self._sequence_state += 1

    def _send_event(self):
        """Send controller button event."""
        if self.session is None or self.session.is_stopped:
            return
        data = b"".join(self._event_buf)
        if not data:
            return
        self._session.stream.send_feedback(
            FeedbackHeader.Type.EVENT, self.sequence_event, data=data
        )
        self._sequence_event += 1

    def _add_event_buffer(self, event: FeedbackEvent):
        """Append event to beginning of byte buffer.
        Oldest event is at the end and is removed
        when buffer is full and a new event is added
        """
        buf = bytearray(FeedbackEvent.LENGTH)
        event.pack(buf)
        self._event_buf.appendleft(buf)

    def button(
        self,
        name: Union[str, FeedbackEvent.Type],
        action: Union[str, ButtonAction] = "tap",
        delay=0.1,
    ):
        """Emulate pressing or releasing button.

        If action is `tap` this method will block by delay.

        :param name: The name of button. Use buttons() to show valid buttons.
        :param action: One of `press`, `release`, `tap`, or `Controller.ButtonAction`.
        :param delay: Delay between press and release. Only used when action is `tap`.
        """
        if isinstance(action, self.ButtonAction):
            _action = action
        else:
            try:
                _action = self.ButtonAction[action.upper()]
            except KeyError:
                _LOGGER.error("Invalid Action: %s", action)
                return
        if isinstance(name, FeedbackEvent.Type):
            button = int(name)
        else:
            try:
                button = int(FeedbackEvent.Type[name.upper()])
            except KeyError:
                _LOGGER.error("Invalid button: %s", name)
                return

        if _action == self.ButtonAction.PRESS:
            self._add_event_buffer(FeedbackEvent(button, is_active=True))
        elif _action == self.ButtonAction.RELEASE:
            self._add_event_buffer(FeedbackEvent(button, is_active=False))
        elif _action == self.ButtonAction.TAP:
            self._add_event_buffer(FeedbackEvent(button, is_active=True))
            self._send_event()
            time.sleep(delay)
            self.button(name, self.ButtonAction.RELEASE)
            return
        self._send_event()

    def stick(
        self,
        stick_name: str,
        axis: str = None,
        value: float = None,
        point: Iterable[float, float] = None,
    ):
        """Set Stick State.

        If controller has not been started with `start()`,
        the `update_sticks()` method needs to be called manually to send stick state.

        The value param represents how far to push the stick away from center.

        The direction mapping is shown below:

        X Axis: Left -1.0, Right 1.0

        Y Axis: Up -1.0, Down 1.0

        Center 0.0

        :param stick_name: The stick to move. One of 'left' or 'right'
        :param axis: The axis to move. One of 'x' or 'y'
        :param value: The value to move stick to. Must be between -1.0 and 1.0
        :param point: An iterable of two floats, which represent coordinates.
            Point takes precedence over axis and value.
            The first value represents the x axis and the second represents the y axis
        """
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

    @property
    def running(self) -> bool:
        """Return True if running."""
        if not self._session:
            return False
        return not self._session.is_stopped and not self._stop_event.is_set()

    @property
    def session(self) -> Session:
        """Return Session."""
        return self._session
