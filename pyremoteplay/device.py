"""Remote Play Devices."""

import logging
from ssl import SSLError
import time
import asyncio

import aiohttp
from aiohttp.client_exceptions import ContentTypeError
from pyps4_2ndscreen.media_art import async_search_ps_store, ResultItem

from .const import DEFAULT_POLL_COUNT, DDP_PORTS, DEFAULT_STANDBY_DELAY
from .ddp import async_get_status, wakeup
from .session import Session
from .util import get_users, get_profiles, format_regist_key

_LOGGER = logging.getLogger(__name__)

STATUS_OK = 200
STATUS_STANDBY = 620


class RPDevice:
    """Represents a Remote Play device."""

    def __init__(self, host, discovered=False, max_polls=DEFAULT_POLL_COUNT):
        self._host = host
        self._discovered = discovered
        self._max_polls = max_polls
        self._host_type = None
        self._mac_address = None
        self._callback = None
        self._standby_start = 0
        self._poll_count = 0
        self._unreachable = False
        self._status = {}
        self._media_info = None
        self._image = None
        self._session = None

    def get_users(self, profiles=None, profile_path=None):
        """Return Registered Users."""
        if not self.mac_address:
            _LOGGER.error("Device ID is unknown. Status needs to be updated.")
            return []
        users = get_users(self.mac_address, profiles, profile_path)
        return users

    def get_profile(self, user: str, profiles=None, profile_path=None):
        """Return valid profile for user."""
        if not profiles:
            profiles = get_profiles(profile_path)
        users = self.get_users(profiles)
        if user not in users:
            _LOGGER.error("User: %s not valid", user)
            return None
        return profiles.get(user)

    def set_unreachable(self, state: bool):
        """Set unreachable attribute."""
        self._unreachable = state

    def set_callback(self, callback: callable):
        """Set callback for status changes."""
        self._callback = callback

    async def get_status(self):
        """Return status."""
        status = async_get_status(self.host)
        self.set_status(status)
        return status

    def set_status(self, data):
        """Set status."""
        if self.host_type is None:
            self._host_type = data.get("host-type")
        if self.mac_address is None:
            self._mac_address = data.get("host-id")
        self._poll_count = 0
        self._unreachable = False
        old_status = self.status
        self._status = data
        if old_status != data:
            _LOGGER.debug("Status: %s", self.status)
            title_id = self.status.get("running-app-titleid")
            if title_id:
                asyncio.ensure_future(self.get_media_info(title_id))
            else:
                self._media_info = None
                self._image = None
            if not title_id and self.callback:
                self.callback()  # pylint: disable=not-callable
            # Status changed from OK to Standby/Turned Off
            if (
                old_status is not None
                and old_status.get("status-code") == STATUS_OK
                and self.status.get("status-code") == STATUS_STANDBY
            ):
                self._standby_start = time.time()
                _LOGGER.debug(
                    "Status changed from OK to Standby."
                    "Disabling polls for %s seconds",
                    DEFAULT_STANDBY_DELAY,
                )

    async def get_media_info(self, title_id, region="United States"):
        """Retrieve Media info."""
        result = await async_search_ps_store(title_id, region)
        self._media_info = result
        if self._media_info.cover_art:
            await self.get_image(self.media_info.cover_art)
        if self.callback:
            self.callback()  # pylint: disable=not-callable

    async def get_image(self, url):
        """Get media image."""
        try:
            async with aiohttp.ClientSession() as session:
                response = await session.get(url, timeout=3)
                if response is not None:
                    self._image = await response.read()
        except (asyncio.TimeoutError, ContentTypeError, SSLError):
            pass

    def create_session(
        self, user, profiles=None, profile_path=None, **kwargs
    ) -> Session:
        """Return initialized session."""
        if self.session:
            if not self.session.is_stopped:
                _LOGGER.error("Device session already exists. Disconnect first.")
                return
            self.disconnect()
        profile = self.get_profile(user, profiles, profile_path)
        if not profile:
            _LOGGER.error("Could not find valid user profile")
            return None
        self._session = Session(
            self.host,
            profile,
            **kwargs,
        )
        return self._session

    async def connect(self):
        """Connect and start session."""
        if self.connected:
            _LOGGER.error("Device session already running")
            return
        success = await self.session.start()
        return success

    def disconnect(self):
        """Disconnect and stop session."""
        if self.session:
            if self.connected:
                self.session.stop()
            del self._session
        self._session = None

    async def standby(self, user="", profiles=None, profile_path=None) -> bool:
        """Place Device in standby. Return True if successful."""
        if not self.is_on:
            _LOGGER.error("Device is not on.")
            return False

        if self.session is None:
            if not user:
                _LOGGER.error("User needed")
                return False
            await self.create_session(user, profiles, profile_path)
        if not self.connected:
            if not await self.connect():
                _LOGGER.error("Error connecting")
                return False
            try:
                await asyncio.wait_for(self.session.stream_ready.wait(), 5)
            except asyncio.TimeoutError:
                _LOGGER.error("Timed out waiting for stream to start")
                return False
        self.session.standby()
        return True

    def wakeup(self, user: str, profiles=None, profile_path=None):
        """Send Wakeup."""
        profile = self.get_profile(user, profiles, profile_path)
        regist_key = profile["hosts"][self.mac_address]["data"]["RegistKey"]
        regist_key = format_regist_key(regist_key)
        wakeup(self.host, regist_key, host_type=self.host_type)

    @property
    def host(self) -> str:
        """Return host address."""
        return self._host

    @property
    def host_type(self) -> str:
        """Return Host Type."""
        return self._host_type

    @property
    def mac_address(self) -> str:
        """Return Mac Address"""
        return self._mac_address

    @property
    def remote_port(self) -> int:
        """Return DDP port of device."""
        return DDP_PORTS.get(self.host_type)

    @property
    def polls_disabled(self) -> bool:
        """Return true if polls disabled."""
        elapsed = time.time() - self._standby_start
        if elapsed < DEFAULT_STANDBY_DELAY:
            return True
        self._standby_start = 0
        return False

    @property
    def max_polls(self) -> int:
        """Return max polls."""
        return self._max_polls

    @property
    def unreachable(self) -> bool:
        """Return True if unreachable"""
        return self._unreachable

    @property
    def callback(self) -> callable:
        """Return callback for status updates."""
        return self._callback

    @property
    def status(self) -> dict:
        """Return Status as dict."""
        return self._status

    @property
    def is_on(self) -> bool:
        """Return True if device is on."""
        if self.status.get("status-code") == 200:
            return True
        return False

    @property
    def discovered(self) -> bool:
        """Return True if discovered."""
        return self._discovered

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
