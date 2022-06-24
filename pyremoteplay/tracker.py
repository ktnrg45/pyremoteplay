"""Async Device Tracker."""
from __future__ import annotations
import asyncio
import logging
import time
from typing import Callable
import sys

from .const import (
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
    async_get_status,
    STATUS_OK,
    STATUS_STANDBY,
)

_LOGGER = logging.getLogger(__name__)

DEFAULT_PORT = DEFAULT_UDP_PORT + 1


class DeviceTracker:
    """Async Device Tracker."""

    def __init__(
        self,
        default_callback: Callable = None,
        max_polls: int = DEFAULT_POLL_COUNT,
        local_port: int = DEFAULT_PORT,
        directed: bool = False,
    ):
        super().__init__()
        self._socks = []
        self._tasks = set()
        self._directed = directed
        self._devices_data = {}
        self._max_polls = max_polls
        self._local_port = local_port
        self._default_callback = default_callback
        self._message = get_ddp_search_message()
        self._event_stop = asyncio.Event()
        self._event_shutdown = asyncio.Event()
        self._event_shutdown.set()

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
            async_send_msg(sock, host, message, host_type, directed=self._directed)
            if device:
                break

    def datagram_received(self, data: bytes, addr: tuple):
        """When data is received."""
        if data is not None:
            self._handle(data, addr)

    def _handle_windows_get_status(self, future: asyncio.Future):
        """Custom handler for Windows."""
        # TODO: Fix so that this isn't needed
        self._tasks.discard(future)
        status = future.result()
        if status:
            self._update_device(status)

    def _handle(self, data: bytes, addr: tuple):
        status = parse_ddp_response(data, addr[0])
        if status:
            self._update_device(status)

    def _update_device(self, status: dict):
        address = status.get("host-ip")
        if not address:
            return
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
        device._set_status(status)  # pylint: disable=protected-access

    def close(self):
        """Close all sockets."""
        for sock in self._socks:
            sock.close()
        self._socks = []

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
                    if sys.platform == "win32":
                        # TODO: Hack to avoid Windows closing socket.
                        task = asyncio.create_task(
                            async_get_status(device.host, host_type=device.host_type)
                        )
                        task.add_done_callback(self._handle_windows_get_status)
                        self._tasks.add(task)
                    else:
                        await self.send_msg(device)

    async def _setup(self, port: int):
        """Setup Tracker."""
        socks = await async_get_sockets(local_port=port, directed=self._directed)
        if not socks:
            raise RuntimeError("Could not get sockets")
        self._socks = socks
        for sock in self._socks:
            sock.set_callback(self.datagram_received)
            sock.set_broadcast(True)

    async def run(self, interval=1):
        """Run polling."""
        if not self._event_shutdown.is_set():
            return
        if not self._socks:
            await self._setup(self._local_port)
        self._event_shutdown.clear()
        self._event_stop.clear()
        await asyncio.sleep(1)  # Wait for sockets to get setup
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
