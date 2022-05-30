# pylint: disable=no-member
"""Gamepad interface to controlller."""
from __future__ import annotations
import threading
import logging
import warnings
from typing import Union
import atexit

from pyremoteplay.controller import Controller
from .mappings import AxisType, DEFAULT_DEADZONE, DEFAULT_MAPS, DUALSHOCK4_MAP

try:
    import pygame

    pygame.init()
    pygame.joystick.init()
    pygame.event.set_allowed(
        [
            pygame.JOYBUTTONDOWN,
            pygame.JOYBUTTONUP,
            pygame.JOYAXISMOTION,
            pygame.JOYHATMOTION,
            pygame.JOYDEVICEADDED,
            pygame.JOYDEVICEREMOVED,
        ]
    )
except ModuleNotFoundError:
    warnings.warn("pygame not installed")

_LOGGER = logging.getLogger(__name__)


class Gamepad:
    """Gamepad. PyGame interface to Controller."""

    __thread: threading.Thread = None
    __stop_event = threading.Event()
    __instances = set()

    @staticmethod
    def joysticks() -> list[pygame.joystick.Joystick]:
        """Return Joysticks."""
        joysticks = [
            pygame.joystick.Joystick(index)
            for index in range(pygame.joystick.get_count())
        ]
        return joysticks

    @classmethod
    def start(cls):
        """Start Gamepad loop. Called automatically"""
        if cls.running():
            return
        _LOGGER.debug("Starting Gamepad loop")
        cls.__stop_event.clear()
        cls.__thread = threading.Thread(target=cls.__worker, daemon=True)
        cls.__thread.start()
        atexit.register(cls.stop_all)

    @classmethod
    def stop(cls):
        """Stop Gamepad loop."""
        cls.__stop_event.set()
        cls.__thread = None
        _LOGGER.debug("Stopped Gamepad loop")

    @classmethod
    def stop_all(cls):
        """Stop all instances."""
        _LOGGER.debug("Stopping all")
        for instance in list(cls.__instances):
            instance.quit()
        cls.stop()

    @classmethod
    def running(cls) -> bool:
        """Return True if running."""
        return cls.__thread is not None and cls.__thread.is_alive()

    @classmethod
    def __worker(cls):
        while not cls.__stop_event.is_set():
            event = pygame.event.wait(timeout=1)
            if event.type == pygame.NOEVENT or not hasattr(event, "instance_id"):
                continue
            for instance in cls.__instances:
                if instance.instance_id == event.instance_id:
                    instance._handle_event(event)  # pylint: disable=protected-access
                    break

    @classmethod
    def __add_ref(cls, instance: Gamepad):
        cls.__instances.add(instance)
        cls.start()

    @classmethod
    def __del_ref(cls, instance: Gamepad):
        try:
            cls.__instances.remove(instance)
        except KeyError:
            pass
        if not cls.__instances:
            cls.stop()

    def __del__(self):
        self.quit()

    def __init__(
        self,
        joystick: Union[int, pygame.joystick.Joystick],
        controller: Controller = None,
        mapping: dict = None,
        deadzone: float = DEFAULT_DEADZONE,
    ):
        self._thread = None
        self._stop_event = threading.Event()
        self._joystick = None
        self._controller = None
        self._deadzone = deadzone
        self.__last_button = ()

        if isinstance(joystick, int):
            joystick = pygame.joystick.Joystick(joystick)
        else:
            # Hack to check we do have a Joystick object
            try:
                old_id = joystick.get_instance_id()
                joystick = pygame.joystick.Joystick(old_id)
                new_id = joystick.get_instance_id()
                if old_id != new_id:
                    raise RuntimeError(f"Joystick ID changed from {old_id} to {new_id}")
            except AttributeError as error:
                raise TypeError(
                    f"Expected an int or an instance of 'pygame.joystick.Joystick'. Got: {type(joystick)}"
                ) from error

        if not mapping:
            mapping = DEFAULT_MAPS.get(joystick.get_name())
            mapping = mapping or DUALSHOCK4_MAP
        self._mapping = mapping
        self._check_map()

        self._joystick = joystick
        self.controller = controller
        Gamepad.__add_ref(self)  # pylint: disable=protected-access

    def quit(self):
        """Quit handling events."""
        self.controller = None
        if self.joystick is not None and self.joystick.get_init():
            self.joystick.quit()
        self._joystick = None
        Gamepad.__del_ref(self)

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

    def _send_button(self, button: str, action: controller.ButtonAction):
        current = (button, action)
        if self.__last_button == current:
            return
        self.__last_button = current
        _LOGGER.debug("Button: %s, Action: %s", button, action)
        if self.controller:
            self.controller.button(button, action)

    def _send_stick(self, stick: str, axis: str, value: float):
        if self.controller:
            self.controller.stick(stick, axis=axis, value=value)

    def _handle_event(self, event: pygame.event.Event):
        """Handle event."""
        if not self.controller:
            return
        if not self.controller.session:
            return
        if event.type in (pygame.JOYBUTTONDOWN, pygame.JOYBUTTONUP):
            self._handle_button_event(event)
        elif event.type in (
            pygame.JOYAXISMOTION,
            pygame.JOYHATMOTION,
        ):
            self._handle_motion_event(event)

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
        self._send_button(button, action)

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
        value = min(max(event.value, -1.0), 1.0)

        if name in Controller.buttons():
            if event.type == pygame.JOYAXISMOTION:
                action = (
                    Controller.ButtonAction.PRESS
                    if value > -1.0 + self.deadzone
                    else Controller.ButtonAction.RELEASE
                )
            elif event.type == pygame.JOYHATMOTION:
                return  # TODO: Implement
            else:
                return
            self._send_button(name, action)
            return

        try:
            stick, axis = name.split("_")
        except ValueError:
            _LOGGER.warning("Could not determine stick and axis from: %s", name)
            return

        if abs(event.value) < self.deadzone:
            value = 0.0
        self._send_stick(stick, axis, value)

    @property
    def controller(self) -> Controller:
        """Return Controller."""
        return self._controller

    @controller.setter
    def controller(self, controller: Controller):
        """Set Controller."""
        if controller is not None and not isinstance(controller, Controller):
            raise TypeError(
                f"Expected instance of {Controller}; Got type {type(controller)}"
            )
        self._controller = controller

    @property
    def deadzone(self) -> float:
        """Return stick deadzone. Will be positive."""
        return self._deadzone

    @deadzone.setter
    def deadzone(self, deadzone: float):
        """Set Deadzone."""
        deadzone = abs(float(deadzone))
        if deadzone >= 1.0:
            raise ValueError("Deadzone must be less than 1.0")
        self._deadzone = deadzone

    @property
    def joystick(self) -> pygame.joystick.Joystick:
        """Return Joystick."""
        return self._joystick

    @property
    def instance_id(self) -> int:
        """Return instance id."""
        if not self.joystick:
            return None
        return self.joystick.get_instance_id()
