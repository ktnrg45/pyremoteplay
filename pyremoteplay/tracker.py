"""Async DDP Protocol."""
from __future__ import annotations
import asyncio
import logging
import time
from typing import Callable

from .const import (
    UDP_PORT,
    DDP_PORTS,
    DEFAULT_UDP_PORT,
    BROADCAST_IP,
    DEFAULT_POLL_COUNT,
    DEFAULT_STANDBY_DELAY,
)
from .device import RPDevice
from .ddp import (
    get_ddp_search_message,
    parse_ddp_response,
    async_get_sockets,
    async_send_msg,
    STATUS_OK,
    STATUS_STANDBY,
)
from .socket import AsyncUDPSocket

_LOGGER = logging.getLogger(__name__)


class DeviceTracker:
    """Async Device Tracker."""

    @classmethod
    async def create(
        cls, callback: Callable = None, port: int = DEFAULT_UDP_PORT
    ) -> DeviceTracker:
        """Create and return Tracker."""
        socks = await async_get_sockets(local_port=port)
        if not socks:
            raise RuntimeError("Could not get sockets")
        return cls(socks, callback, port)

    def __init__(
        self,
        socks: list[AsyncUDPSocket],
        default_callback: Callable = None,
        max_polls: int = DEFAULT_POLL_COUNT,
    ):
        super().__init__()
        self._socks = socks
        self._devices_data = {}
        self._max_polls = max_polls
        self._local_port = UDP_PORT
        self._default_callback = default_callback
        self._message = get_ddp_search_message()
        self._event_stop = asyncio.Event()
        self._event_shutdown = asyncio.Event()

        for sock in self._socks:
            sock.set_callback(self.datagram_received)

    def __repr__(self):
        return (
            f"<{self.__module__}.{self.__class__.__name__} "
            f"local_port={self.local_port} "
            f"max_polls={self._max_polls}>"
        )

    def set_max_polls(self, poll_count: int):
        """Set number of unreturned polls neeeded to assume no status."""
        self._max_polls = poll_count

    async def send_msg(self, device: RPDevice = None, message: str = ""):
        """Send Message."""
        host = BROADCAST_IP
        host_type = ""
        if not message:
            message = self._message

        if device is not None:
            host = device.host
            host_type = device.host_type
        for sock in self._socks:
            await async_send_msg(sock, host, message, host_type)
            if device:
                break

    def datagram_received(self, data: bytes, addr: tuple):
        """When data is received."""
        if data is not None:
            self._handle(data, addr)

    def _handle(self, data: bytes, addr: tuple):
        data = parse_ddp_response(data)
        data["host-ip"] = addr[0]
        address = addr[0]

        if address in self._devices_data:
            device_data = self._devices_data[address]
        else:
            device_data = self.add_device(address, discovered=True)

        device = device_data["device"]
        old_status = device.status
        # Status changed from OK to Standby/Turned Off
        if (
            old_status is not None
            and old_status.get("status-code") == STATUS_OK
            and device.status.get("status-code") == STATUS_STANDBY
        ):
            device_data["standby_start"] = time.time()
        device_data["poll_count"] = 0
        device_data["device"]._set_status(data)  # pylint: disable=protected-access

    def close(self):
        """Close all sockets."""
        for sock in self._socks:
            sock.close()

    def add_device(
        self, host: str, callback: Callable = None, discovered: bool = False
    ):
        """Add device to track."""
        if host in self._devices_data:
            return None
        self._devices_data[host] = {
            "device": RPDevice(host),
            "discovered": discovered,
            "polls_disabled": False,
            "poll_count": 0,
            "standby_start": 0,
        }
        if callback is None and self._default_callback is not None:
            callback = self._default_callback
        self.add_callback(host, callback)
        return self._devices_data[host]

    def remove_device(self, host: str):
        """Remove device from tracking."""
        if host in self.devices:
            self._devices_data.pop(host)

    def add_callback(self, host: str, callback: Callable):
        """Add callback. One per host."""
        if host not in self._devices_data:
            return
        self._devices_data[host]["device"].set_callback(callback)

    def remove_callback(self, host: str):
        """Remove callback from list."""
        if host not in self._devices_data:
            return
        self._devices_data[host]["device"].set_callback(None)

    async def _poll(self):
        await self.send_msg()
        for device_data in self._devices_data.values():
            device = device_data["device"]
            # Device won't respond to polls right after standby
            if device_data["polls_disabled"]:
                elapsed = time.time() - device_data["standby_start"]
                seconds = DEFAULT_STANDBY_DELAY - elapsed
                if seconds > 0:
                    _LOGGER.debug("Polls disabled for %s seconds", round(seconds, 2))
                    continue
                device_data["polls_disabled"] = False

            # Track polls that were never returned.
            device_data["poll_count"] += 1
            # Assume Device is not available.
            if device_data["poll_count"] > self._max_polls:
                device._set_status({})  # pylint: disable=protected-access
                device_data["poll_count"] = 0
                if device.callback:
                    device.callback()  # pylint: disable=not-callable
            if not device_data["discovered"]:
                # Explicitly poll device in case it cannot be reached by broadcast.
                if not device_data["polls_disabled"]:
                    await self.send_msg(device)

    async def run(self, interval=1):
        """Run polling."""
        while not self._event_shutdown.is_set():
            if not self._event_stop.is_set():
                await self._poll()
            await asyncio.sleep(interval)
        self.close()

    def shutdown(self):
        """Shutdown protocol."""
        self._event_shutdown.set()

    def stop(self):
        """Stop Polling."""
        self._event_stop.set()

    def start(self):
        """Start polling."""
        self._event_stop.clear()

    @property
    def local_port(self) -> int:
        """Return local port."""
        return self._local_port

    @property
    def remote_ports(self) -> dict:
        """Return remote ports."""
        return DDP_PORTS

    @property
    def devices(self) -> dict:
        """Return devices that are tracked."""
        return {
            ip_address: data["device"]
            for ip_address, data in self._devices_data.items()
        }

    @property
    def device_status(self) -> list:
        """Return all device status."""
        return [device["device"].status for device in self._devices_data.values()]
