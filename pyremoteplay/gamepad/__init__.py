# pylint: disable=no-member
"""Gamepad interface to controlller."""
from __future__ import annotations
import threading
import logging
import warnings
from enum import IntEnum, auto

from pyremoteplay.stream_packets import FeedbackEvent
from pyremoteplay.controller import Controller

try:
    import pygame

    pygame.joystick.init()
except ModuleNotFoundError:
    warnings.warn("pygame not installed")

_LOGGER = logging.getLogger(__name__)

DEFAULT_DEADZONE = 0.1

TRIGGERS = (FeedbackEvent.Type.R2.name, FeedbackEvent.Type.L2.name)


class AxisType(IntEnum):
    """Axis Type Enum."""

    LEFT_X = auto()
    LEFT_Y = auto()
    RIGHT_X = auto()
    RIGHT_Y = auto()


DUALSHOCK4_MAP = {
    "name": "PS4 Controller",
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

# HAT_UP = hy = 1
# HAT_DOWN = hy = -1
# HAT_RIGHT = hx = 1
# HAT_LEFT = hx = -1


class Gamepad:
    """Gamepad. PyGame interface to Controller."""

    @staticmethod
    def joysticks() -> list[pygame.joystick.Joystick]:
        """Return Joysticks."""
        joysticks = [
            pygame.joystick.Joystick(index)
            for index in range(pygame.joystick.get_count())
        ]
        return joysticks

    def __del__(self):
        self.stop()
        self.controller = None
        self._joystick = None

    def __init__(
        self,
        joystick: pygame.joystick.Joystick,
        controller: Controller = None,
        mapping: dict = None,
        deadzone: float = DEFAULT_DEADZONE,
    ):
        self._thread = None
        self._stop_event = threading.Event()
        self._mapping = mapping or DUALSHOCK4_MAP
        self._controller = None
        self._deadzone = deadzone

        self._check_map()

        if not isinstance(joystick, pygame.joystick.Joystick):
            raise ValueError(
                f"Expected instance of {pygame.joystick.Joystick}; Got type {type(joystick)}"
            )

        self._joystick = joystick
        self.controller = controller

    def start(self):
        """Start Gamepad."""
        if self.running:
            _LOGGER.error("Joystick already running")
            return
        self.joystick.init()
        self._thread = threading.Thread(target=self.__worker, daemon=True)
        self._thread.start()

    def stop(self):
        """Stop Gamepad."""
        self._stop_event.set()
        self.joystick.quit()
        self._thread = None

    def _check_map(self):
        valid_buttons = Controller.buttons()
        buttons = self._mapping["button"]
        for button in buttons.values():
            button = button.upper()
            if button not in valid_buttons:
                raise ValueError(f"Invalid button: {button}")

        valid_axes = [item.name for item in AxisType]
        axes = list(self._mapping["axis"].values())
        axes.extend(list(self._mapping["hat"].values()))
        for axis in axes:
            axis = axis.upper()
            if axis in valid_buttons:
                continue
            if axis not in valid_axes:
                raise ValueError(f"Invalid axis: {axis}")

    def __worker(self):
        while not self._stop_event.is_set():
            self._handle_event()
        self._stop_event.clear()

    def _handle_button_event(self, event: pygame.event.Event):
        """Handle Button Event."""
        action = None
        if event.type == pygame.JOYBUTTONDOWN:
            action = Controller.ButtonAction.PRESS
        elif event.type == pygame.JOYBUTTONUP:
            action = Controller.ButtonAction.RELEASE
        else:
            raise RuntimeError("Could not determine Button Action")

        button = self._mapping["button"].get(event.button)
        if action is None or button is None:
            return
        _LOGGER.debug("Button: %s, Action: %s", button, action)
        if self.controller:
            self.controller.button(button, action)

    def _handle_motion_event(self, event: pygame.event.Event):
        """Handle Motion Event."""
        name = None
        if event.type == pygame.JOYAXISMOTION:
            name = self._mapping["axis"].get(event.axis)
        elif event.type == pygame.JOYHATMOTION:
            name = self._mapping["hat"].get(event.hat)
            return  # TODO: Implement
        if not name:
            return

        name = name.upper()
        value = event.value
        if name in Controller.buttons():
            action = (
                Controller.ButtonAction.PRESS
                if value > -1.0 + self.deadzone
                else Controller.ButtonAction.RELEASE
            )
            self.controller.button(name, action)
            return

        try:
            stick, axis = name.split("_")
        except ValueError:
            _LOGGER.warning("Could not determine stick and axis from: %s", name)
            return

        if abs(event.value) > self.deadzone:
            value = 0.0
        else:
            _LOGGER.debug("Stick: %s, Axis: %s, Value: %s", stick, axis, value)

        if self.controller:
            self.controller.stick(stick=stick, axis=axis, value=value)

    def _handle_event(self):
        """Handle event."""
        for event in pygame.event.get():
            if event.type in (pygame.JOYBUTTONDOWN, pygame.JOYBUTTONUP):
                self._handle_button_event(event)
            elif event.type in (
                pygame.JOYAXISMOTION,
                pygame.JOYHATMOTION,
            ):
                self._handle_motion_event(event)

    @property
    def controller(self) -> Controller:
        """Return Controller."""
        return self._controller

    @controller.setter
    def controller(self, controller: Controller):
        """Set Controller."""
        if controller is not None and not isinstance(controller, Controller):
            raise ValueError(
                f"Expected instance of {Controller}; Got type {type(controller)}"
            )
        self._controller = controller

    @property
    def deadzone(self) -> float:
        """Return deadzone. Will be positive."""
        return self._deadzone

    @deadzone.setter
    def deadzone(self, deadzone: float):
        """Set Deadzone."""
        deadzone = abs(float(deadzone))
        if deadzone >= 1.0:
            raise ValueError("Deadzone must be less than 1.0")
        self._deadzone = deadzone

    @property
    def running(self) -> bool:
        """Return True if running."""
        return not self._stop_event.is_set() or not self._thread

    @property
    def joystick(self) -> pygame.joystick.Joystick:
        """Return Joystick."""
        return self._joystick
