"""Async DDP Protocol."""

import asyncio
import logging
import socket

from .const import (
    UDP_PORT,
    DDP_PORTS,
    DEFAULT_UDP_PORT,
    BROADCAST_IP,
    DEFAULT_POLL_COUNT,
)
from .device import RPDevice
from .ddp import get_ddp_search_message, parse_ddp_response, get_socket

_LOGGER = logging.getLogger(__name__)


class DDPProtocol(asyncio.DatagramProtocol):
    """Async UDP Client."""

    def __init__(self, default_callback=None, max_polls=DEFAULT_POLL_COUNT):
        """Init Instance."""
        super().__init__()
        self._devices = {}
        self.max_polls = max_polls
        self._transport = None
        self._local_port = UDP_PORT
        self._default_callback = default_callback
        self._message = get_ddp_search_message()
        self._standby_start = 0
        self._event_stop = asyncio.Event()
        self._event_stop.clear()
        self._remote_port = None

    def __repr__(self):
        return (
            f"<{self.__module__}.{self.__class__.__name__} "
            f"local_port={self.local_port} "
            f"max_polls={self.max_polls}>"
        )

    def _set_write_port(self, port):
        """Only used for tests."""
        self._remote_port = port

    def set_max_polls(self, poll_count: int):
        """Set number of unreturned polls neeeded to assume no status."""
        self.max_polls = poll_count

    def connection_made(self, transport):
        """On Connection."""
        self._transport = transport
        sock = self._transport.get_extra_info("socket")
        self._local_port = sock.getsockname()[1]
        _LOGGER.debug("DDP Transport created with port: %s", self.local_port)

    def send_msg(self, device=None, message=None):
        """Send Message."""
        sock = self._transport.get_extra_info("socket")
        ports = []
        if message is None:
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

    def datagram_received(self, data, addr):
        """When data is received."""
        if data is not None:
            sock = self._transport.get_extra_info("socket")
            _LOGGER.debug(
                "RECV MSG @ DDP Proto DPORT=%s SRC=%s", sock.getsockname()[1], addr
            )
            self._handle(data, addr)

    def _handle(self, data, addr):
        data = parse_ddp_response(data.decode("utf-8"))
        data["host-ip"] = addr[0]
        address = addr[0]

        if address in self._devices:
            device = self._devices.get(address)
        else:
            device = self.add_device(address, direct=False)
        device.set_status(data)

    def connection_lost(self, exc):
        """On Connection Lost."""
        if self._transport is not None:
            _LOGGER.error("DDP Transport Closed")
            self._transport.close()

    def error_received(self, exc):
        """Handle Exceptions."""
        _LOGGER.warning("Error received at DDP Transport: %s", exc)

    def close(self):
        """Close Transport."""
        self._transport.close()
        self._transport = None
        _LOGGER.debug("Closing DDP Transport: Port=%s", self._local_port)

    def add_device(self, host, callback=None, direct=True):
        """Add device to track."""
        if host in self._devices:
            return None
        self._devices[host] = RPDevice(host, self, direct)
        if callback is None and self._default_callback is not None:
            callback = self._default_callback
        self.add_callback(host, callback)
        return self._devices[host]

    def remove_device(self, host):
        """Remove device from tracking."""
        if host in self.devices:
            self._devices.pop(host)

    def add_callback(self, host, callback):
        """Add callback. One per host."""
        if host not in self._devices:
            return
        self._devices[host].set_callback(callback)

    def remove_callback(self, host):
        """Remove callback from list."""
        if host not in self._devices:
            return
        self._devices[host].set_callback(None)

    async def run(self, interval=1):
        """Run polling."""
        while True:
            if not self._event_stop.is_set():
                self.send_msg()
                for device in self._devices.values():
                    if device.polls_disabled:
                        continue
                    if device.direct:
                        self.send_msg(device)
            await asyncio.sleep(interval)

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
        return self._devices

    @property
    def device_status(self) -> list:
        """Return all device status."""
        return [device.status for device in self._devices.values()]


async def async_create_ddp_endpoint(
    callback=None, sock=None, port=DEFAULT_UDP_PORT
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
