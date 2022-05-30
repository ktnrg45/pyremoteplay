# pylint: disable=no-member
"""Gamepad interface to controlller."""
from __future__ import annotations
import threading
import logging
import warnings
from typing import Union
import atexit

from pyremoteplay.controller import Controller
from .mappings import AxisType, HatType, default_maps, dualshock4_map

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

DEFAULT_DEADZONE = 0.1


class Gamepad:
    """Gamepad. PyGame interface to Controller.
    Instances are not re-entrant after calling `close`.
    Creating an instance automatically starts the event loop.
    User should ensure that dangling instances are stopped with `close`.

    :param joystick: Either the id from `pygame.joystick.Joystick.get_instance_id()` or an instance of `pygame.joystick.Joystick`.
    :param controller: Instance of `Controller`.
    :param mapping: Dict which maps pygame Joystick to Remote Play keys. See default maps in `mappings` module.
    :param deadzone: The deadzone for analog axes. Absolute Axis Values less than this are considered to be 0.0.
    :param auto_close: If True, `close` will be called automatically when `Session` ends.
    """

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

    @staticmethod
    def check_map(mapping: dict) -> bool:
        """Check map. Return True if valid."""
        is_valid = True
        valid_buttons = Controller.buttons()
        buttons = mapping["button"]
        for button in buttons.values():
            if button is None:
                continue
            button = button.upper()
            if button not in valid_buttons:
                _LOGGER.error("Invalid button: %s", button)
                is_valid = False

        valid_axes = [item.name for item in AxisType]
        axes = list(mapping["axis"].values())
        axes.extend(list(mapping["hat"].values()))
        for axis in axes:
            if axis is None:
                continue
            axis = axis.upper()
            if axis in valid_buttons:
                continue
            if axis not in valid_axes:
                _LOGGER.error("Invalid axis: %s", axis)
                is_valid = False
        return is_valid

    @classmethod
    def start(cls):
        """Start Gamepad loop. Called automatically when an instance is created."""
        if cls.running():
            return
        _LOGGER.debug("Starting Gamepad loop")
        cls.__stop_event.clear()
        cls.__thread = threading.Thread(target=cls.__worker, daemon=True)
        cls.__thread.start()
        atexit.register(cls.stop_all)

    @classmethod
    def stop(cls):
        """Stop Gamepad loop. Called when all instances have called `quit` or when all instances are deleted."""
        cls.__stop_event.set()
        cls.__thread = None
        _LOGGER.debug("Stopped Gamepad loop")

    @classmethod
    def stop_all(cls):
        """Stop all instances. Stop Event loop."""
        _LOGGER.debug("Stopping all")
        for instance in list(cls.__instances):
            instance.close()
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
        self.close()

    def __init__(
        self,
        joystick: Union[int, pygame.joystick.Joystick],
        controller: Controller = None,
        mapping: dict = None,
        deadzone: float = DEFAULT_DEADZONE,
        auto_close: bool = True,
    ):
        self._thread = None
        self._stop_event = threading.Event()
        self._joystick = None
        self._controller = None
        self._deadzone = deadzone
        self._auto_close = False
        self.__last_button = ()
        self.__last_hat = {}

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
            mapping = default_maps().get(joystick.get_name())
            mapping = mapping or dualshock4_map()
        self._mapping = mapping
        if not Gamepad.check_map(self._mapping):
            raise ValueError("Invalid Mapping")

        self._joystick = joystick
        self.controller = controller
        Gamepad.__add_ref(self)  # pylint: disable=protected-access

    def close(self):
        """Close. Quit handling events."""
        self.controller = None
        if self.joystick is not None and self.joystick.get_init():
            _LOGGER.info("Gamepad with joystick closed: %s", self.joystick.get_guid())
            self.joystick.quit()
        self._joystick = None
        Gamepad.__del_ref(self)

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
            self._handle_hat(event)
            return

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

    def _handle_hat(self, event: pygame.event.Event):
        assert event.type == pygame.JOYHATMOTION
        hat_map = self._mapping["hat"].get(event.hat)
        if not hat_map:
            return
        values = tuple(event.value)
        action = Controller.ButtonAction.PRESS
        hat_type = None
        name = None

        # We're assuming that only one hat direction can be active at a time
        if values == (-1, 0):
            hat_type = HatType.LEFT
        elif values == (1, 0):
            hat_type = HatType.RIGHT
        elif values == (0, -1):
            hat_type = HatType.DOWN
        elif values == (0, 1):
            hat_type = HatType.UP
        else:
            # (0, 0)
            hat_type = self.__last_hat.get(event.hat)
            action = Controller.ButtonAction.RELEASE

        if hat_type is None:
            return
        name = hat_map.get(hat_type.name)
        if name is None:
            return
        self.__last_hat[event.hat] = hat_type
        self._send_button(name, action)

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
        if controller is not None:
            if self.controller is not None:
                _LOGGER.error("Cannot change controller once set")
            else:
                if controller.session and self._auto_close:
                    controller.session.events.on("stop", self.close)
        else:
            if self.controller and self._auto_close:
                self.close()
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
