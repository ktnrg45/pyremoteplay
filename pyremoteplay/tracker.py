"""Async DDP Protocol."""

import asyncio
import logging
import socket
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
    get_socket,
    STATUS_OK,
    STATUS_STANDBY,
)

_LOGGER = logging.getLogger(__name__)


class DDPProtocol(asyncio.DatagramProtocol):
    """Async UDP Client. Polls device status."""

    def __init__(
        self, default_callback: Callable = None, max_polls: int = DEFAULT_POLL_COUNT
    ):
        super().__init__()
        self._devices_data = {}
        self._max_polls = max_polls
        self._transport = None
        self._local_port = UDP_PORT
        self._default_callback = default_callback
        self._message = get_ddp_search_message()
        self._event_stop = asyncio.Event()
        self._event_shutdown = asyncio.Event()
        self._remote_port = None

    def __repr__(self):
        return (
            f"<{self.__module__}.{self.__class__.__name__} "
            f"local_port={self.local_port} "
            f"max_polls={self._max_polls}>"
        )

    def _set_write_port(self, port: int):
        """Only used for tests."""
        self._remote_port = port

    def set_max_polls(self, poll_count: int):
        """Set number of unreturned polls neeeded to assume no status."""
        self._max_polls = poll_count

    def connection_made(self, transport: asyncio.BaseTransport):
        """On Connection."""
        self._transport = transport
        sock = self._transport.get_extra_info("socket")
        self._local_port = sock.getsockname()[1]
        _LOGGER.debug("DDP Transport created with port: %s", self.local_port)

    async def send_msg(self, device: RPDevice = None, message: str = ""):
        """Send Message."""
        sock = self._transport.get_extra_info("socket")
        ports = []
        if not message:
            message = self._message
        if device is not None:
            ports = [device.remote_port] if device.remote_port else []
            host = device.host
        else:
            host = BROADCAST_IP
        if not ports:
            ports = list(self.remote_ports.values())
        for port in ports:
            _LOGGER.debug(
                "SENT MSG @ DDP Proto SPORT=%s DEST=%s",
                sock.getsockname()[1],
                (host, port),
            )
            self._transport.sendto(message.encode("utf-8"), (host, port))
            if len(ports) > 1:
                # Device will not respond sometimes if sent packets to quickly
                await asyncio.sleep(0.5)

    def datagram_received(self, data: bytes, addr: tuple):
        """When data is received."""
        if data is not None:
            sock = self._transport.get_extra_info("socket")
            _LOGGER.debug(
                "RECV MSG @ DDP Proto DPORT=%s SRC=%s", sock.getsockname()[1], addr
            )
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

    def connection_lost(self, exc: Exception):
        """On Connection Lost."""
        if self._transport is not None:
            _LOGGER.error("DDP Transport Closed")
            self._transport.close()

    def error_received(self, exc: Exception):
        """Handle Exceptions."""
        _LOGGER.warning("Error received at DDP Transport: %s", exc)

    def close(self):
        """Close Transport."""
        self._transport.close()
        self._transport = None
        _LOGGER.debug("Closing DDP Transport: Port=%s", self._local_port)

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
        self._transport.close()

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


async def async_create_ddp_endpoint(
    callback: Callable = None, sock: socket.socket = None, port: int = DEFAULT_UDP_PORT
) -> DDPProtocol:
    """Create Async UDP endpoint."""
    loop = asyncio.get_event_loop()
    if sock is None:
        sock = get_socket(port=port)
    sock.settimeout(0)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    connect = loop.create_datagram_endpoint(
        lambda: DDPProtocol(callback),  # noqa: pylint: disable=unnecessary-lambda
        sock=sock,
    )
    _, protocol = await loop.create_task(connect)
    return protocol
