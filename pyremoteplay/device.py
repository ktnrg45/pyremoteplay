"""Remote Play Devices."""
from __future__ import annotations
import logging
from ssl import SSLError
import asyncio
from typing import Callable, Union
import socket
from functools import wraps
import inspect

import aiohttp
from aiohttp.client_exceptions import ContentTypeError
from pyps4_2ndscreen.media_art import async_search_ps_store, ResultItem

from pyremoteplay.receiver import AVReceiver
from .const import (
    DEFAULT_POLL_COUNT,
    DDP_PORTS,
    Quality,
    Resolution,
    FPS,
)
from .ddp import async_get_status, get_status, wakeup, STATUS_OK, search, async_search
from .session import Session
from .util import format_regist_key
from .register import register
from .controller import Controller
from .profile import Profiles, UserProfile


_LOGGER = logging.getLogger(__name__)


def _status_to_device(hosts: list[dict]) -> list[RPDevice]:
    devices = []
    for status in hosts:
        ip_address = status.get("host-ip")
        if ip_address:
            device = RPDevice(ip_address)
            device.get_status()
            devices.append(device)
    return devices


def _load_profiles(func: Callable):
    """Decorator. Load profiles if profiles is None."""

    @wraps(func)
    def wrapped(*args, **kwargs):
        param = "profiles"
        signature = inspect.signature(func)
        bound_args = signature.bind(*args, **kwargs)
        if not isinstance(bound_args.arguments.get("self"), RPDevice):
            raise TypeError(f"Can only wrap a {RPDevice} instance")
        if param not in signature.parameters:
            raise ValueError(f"Method: {func} has no parameter '{param}'")

        profiles = bound_args.arguments.get(param)
        if not profiles:
            param_index = list(signature.parameters.keys()).index(param)
            # Remove profiles from arguments
            if param_index < len(bound_args.args):
                _args = list(args)
                _args.pop(param_index)
                args = tuple(_args)
            kwargs[param] = Profiles.load()
        else:
            if not isinstance(profiles, Profiles):
                raise TypeError(
                    f"Expected {param} to be instance of {Profiles}. Got: {type(profiles)}"
                )
        return func(*args, **kwargs)

    return wrapped


class RPDevice:
    """Represents a Remote Play device/host.

    Most, if not all user interactions should be performed with this class.
    Status must be polled manually with `get_status`.
    Most interactions cannot be used unless there is a valid status.

    :param host: IP address of Remote Play Host
    """

    @staticmethod
    def get_all_users(profiles: Profiles = None) -> list[str]:
        """Return all usernames that have been authenticated with OAuth."""
        profiles = Profiles.load()
        return profiles.usernames

    @staticmethod
    def get_profiles(path: str = "") -> Profiles:
        """Return Profiles.

        :param path: Path to file to load profiles. If not given, will load profiles from default path.
        """
        return Profiles.load(path)

    @staticmethod
    def search() -> list[RPDevice]:
        """Return all devices that are discovered."""
        hosts = search()
        return _status_to_device(hosts)

    @staticmethod
    async def async_search() -> list[RPDevice]:
        """Return all devices that are discovered. Async."""
        hosts = await async_search()
        return _status_to_device(hosts)

    def __init__(self, host: str):
        socket.gethostbyname(host)  # Raise Exception if invalid

        self._host = host
        self._max_polls = DEFAULT_POLL_COUNT
        self._host_type = None
        self._host_name = None
        self._mac_address = None
        self._ip_address = None
        self._callback = None
        self._unreachable = False
        self._status = {}
        self._media_info = None
        self._image = None
        self._session = None
        self._controller = None

    @_load_profiles
    def get_users(self, profiles: Profiles = None) -> list[str]:
        """Return Registered Users."""
        if not self.mac_address:
            _LOGGER.error("Device ID is unknown. Status needs to be updated.")
            return []
        users = profiles.get_users(self.mac_address)
        return users

    @_load_profiles
    def get_profile(self, user: str, profiles: Profiles = None) -> UserProfile:
        """Return valid profile for user.

        See:
        :meth:`pyremoteplay.oauth.get_user_account() <pyremoteplay.oauth.get_user_account>`
        :meth:`pyremoteplay.oauth.format_user_account() <pyremoteplay.oauth.format_user_account>`

        :param user: Username of user
        :param profiles: dict of all user profiles. If None, profiles will be retrieved from default location. Optional.
        """
        users = self.get_users(profiles)
        if user not in users:
            _LOGGER.error("User: %s not registered", user)
            return None
        return profiles.get(user)

    def get_status(self) -> dict:
        """Return status."""
        status = get_status(self.host)
        self._set_status(status)
        return status

    async def async_get_status(self):
        """Return status. Async."""
        status = async_get_status(self.host)
        self._set_status(status)
        return status

    def set_unreachable(self, state: bool):
        """Set unreachable attribute."""
        self._unreachable = state

    def set_callback(self, callback: Callable):
        """Set callback for status changes."""
        self._callback = callback

    def _set_status(self, data: dict):
        """Set status."""
        if not data:
            return
        if self.host_type is None:
            self._host_type = data.get("host-type")
        if self.mac_address is None:
            self._mac_address = data.get("host-id")
        if self.host_name is None:
            self._host_name = data.get("host-name")
        if self.ip_address is None:
            self._ip_address = data.get("host-ip")
        old_status = self.status
        self._status = data
        if old_status != data:
            _LOGGER.debug("Status: %s", self.status)
            title_id = self.status.get("running-app-titleid")
            if title_id and asyncio.get_event_loop().is_running:
                asyncio.ensure_future(self._get_media_info(title_id))
            else:
                self._media_info = None
                self._image = None
            if not title_id and self.callback:
                # Call immediately since we don't have to get media.
                self.callback()  # pylint: disable=not-callable

    async def _get_media_info(self, title_id: str, region="United States"):
        """Retrieve Media info."""
        result = await async_search_ps_store(title_id, region)
        self._media_info = result
        if self._media_info.cover_art:
            await self._get_image(self.media_info.cover_art)
        if self.callback:
            self.callback()  # pylint: disable=not-callable

    async def _get_image(self, url: str):
        """Get media image."""
        try:
            async with aiohttp.ClientSession() as session:
                response = await session.get(url, timeout=3)
                if response is not None:
                    self._image = await response.read()
        except (asyncio.TimeoutError, ContentTypeError, SSLError):
            pass

    def create_session(
        self,
        user: str,
        profiles: Profiles = None,
        loop: asyncio.AbstractEventLoop = None,
        receiver: AVReceiver = None,
        resolution: Union[Resolution, str, int] = "360p",
        fps: Union[FPS, str, int] = "low",
        quality: Union[Quality, str, int] = "very_low",
        codec: str = "h264",
        hdr: bool = False,
    ) -> Union[Session, None]:
        """Return initialized session if session created else return None.
        Also connects a controller.

        See :class:`Session <pyremoteplay.session.Session>`  for param details.

        :param user: Name of user to use. Can be found with `get_users`

        """
        if self.session:
            if not self.session.is_stopped:
                _LOGGER.error("Device session already exists. Disconnect first.")
                return None
            self.disconnect()  # Cleanup session
        profile = self.get_profile(user, profiles)
        if not profile:
            _LOGGER.error("Could not find valid user profile")
            return None
        self._session = Session(
            self.host,
            profile,
            loop=loop,
            receiver=receiver,
            resolution=resolution,
            fps=fps,
            quality=quality,
            codec=codec,
            hdr=hdr,
        )
        self.controller = Controller(self.session)
        return self._session

    async def connect(self) -> bool:
        """Connect and start session. Return True if successful."""
        if self.connected:
            _LOGGER.error("Device session already running")
            return False
        if not self.session:
            _LOGGER.error("Session must be initialized first")
            return False
        return await self.session.start()

    def disconnect(self):
        """Disconnect and stop session. This also sets session to None."""
        if self.session:
            if self.connected:
                self.session.stop()
            del self._session
        self._session = None

    async def standby(self, user="", profiles: Profiles = None) -> bool:
        """Place Device in standby. Return True if successful.

        If there is a valid and connected session, no arguments need to be passed.
        Otherwise creates and connects a session first.

        If already connected, the sync method
        :meth:`RPDevice.session.standby() <pyremoteplay.session.Session.standby>`
        is available.

        :param user: Name of user to use. Can be found with `get_users`
        """
        if not self.is_on:
            _LOGGER.error("Device is not on.")
            return False
        if self.session is None:
            if not user:
                _LOGGER.error("User needed")
                return False
            self.create_session(user, profiles)
        if not self.connected:
            if not await self.connect():
                _LOGGER.error("Error connecting")
                return False
            try:
                await asyncio.wait_for(self.session.stream_ready_event.wait(), 5)
            except asyncio.TimeoutError:
                _LOGGER.error("Timed out waiting for stream to start")
                return False
        self.session.standby()  # TODO: Acually determine if this was successful
        return True

    def wakeup(
        self,
        user: str = "",
        profiles: Profiles = None,
        key: str = "",
    ):
        """Send Wakeup.

        Either one of key or user needs to be specified.
        Key takes precedence over user.

        :param user: Name of user to use. Can be found with `get_users`
        :param key: Regist key from registering
        """
        if not key:
            if not user:
                raise ValueError("User must be specified")
            profile = self.get_profile(user, profiles)
            if not profile:
                _LOGGER.error("Profile not found")
                return
            key = profile["hosts"][self.mac_address]["data"]["RegistKey"]
        regist_key = format_regist_key(key)
        wakeup(self.host, regist_key, host_type=self.host_type)

    @_load_profiles
    def register(
        self,
        user: str,
        pin: str,
        timeout: float = 2.0,
        profiles: Profiles = None,
        save: bool = True,
    ) -> UserProfile:
        """Register psn_id with device. Return updated user profile.

        :param user: User name. Can be found with `get_all_users`
        :param pin: PIN for linking found on Remote Play Host
        :param timeout: Timeout to wait for completion
        :param profiles: Profiles to use
        :param save: Save profiles if True
        """
        if not self.status:
            _LOGGER.error("No status")
            return None
        profile = profiles.get_user_profile(user)
        if not profile:
            _LOGGER.error("User: %s not found", user)
            return None
        psn_id = profile.id
        if not psn_id:
            _LOGGER.error("Error retrieving ID for user: %s", user)
            return None
        regist_data = register(self.host, psn_id, pin, timeout)
        if not regist_data:
            _LOGGER.error("Registering failed")
            return None
        profile.add_regist_data(self.status, regist_data)
        profiles.update_user(profile)
        if save:
            profiles.save()
        return profile

    @property
    def host(self) -> str:
        """Return host address."""
        return self._host

    @property
    def host_type(self) -> str:
        """Return Host Type."""
        return self._host_type

    @property
    def host_name(self) -> str:
        """Return Host Name."""
        return self._host_name

    @property
    def mac_address(self) -> str:
        """Return Mac Address"""
        return self._mac_address

    @property
    def ip_address(self) -> str:
        """Return IP Address."""
        return self._ip_address

    @property
    def remote_port(self) -> int:
        """Return DDP port of device."""
        return DDP_PORTS.get(self.host_type)

    @property
    def max_polls(self) -> int:
        """Return max polls."""
        return self._max_polls

    @property
    def unreachable(self) -> bool:
        """Return True if unreachable"""
        return self._unreachable

    @property
    def callback(self) -> Callable:
        """Return callback for status updates."""
        return self._callback

    @property
    def status(self) -> dict:
        """Return Status as dict."""
        return self._status

    @property
    def status_code(self) -> int:
        """Return status code."""
        return self.status.get("status-code")

    @property
    def status_name(self) -> str:
        """Return status name."""
        return self.status.get("status")

    @property
    def is_on(self) -> bool:
        """Return True if device is on."""
        if self.status.get("status-code") == STATUS_OK:
            return True
        return False

    @property
    def app_name(self) -> str:
        """Return App name."""
        return self.status.get("running-app-name")

    @property
    def app_id(self) -> str:
        """Return App ID."""
        return self.status.get("running-app-titleid")

    @property
    def media_info(self) -> ResultItem:
        """Return media info."""
        return self._media_info

    @property
    def image(self) -> bytes:
        """Return raw media image."""
        return self._image

    @property
    def session(self) -> Session:
        """Return Session."""
        return self._session

    @property
    def connected(self) -> bool:
        """Return True if session connected."""
        if self.session is not None and self.session.is_running:
            return True
        return False

    @property
    def controller(self) -> Controller:
        """Return Controller."""
        return self._controller

    @controller.setter
    def controller(self, controller: Controller):
        """Set Controller. Also stops previously connected controller."""
        if not isinstance(controller, Controller):
            raise ValueError(f"Expected an instance of {Controller}")
        if self.controller:
            self.controller.stop()
        self._controller = controller
