"""Async UDP Sockets. Based on asyncudp (https://github.com/eerimoq/asyncudp)."""
from __future__ import annotations
import asyncio
import logging
from typing import Any, Optional
import socket

from .const import BROADCAST_IP

_LOGGER = logging.getLogger(__name__)


class _AsyncUDPProtocol(asyncio.DatagramProtocol):
    """UDP Protocol."""

    def __init__(self):
        self._packets = asyncio.Queue()
        self._transport = None

    def connection_made(self, transport: asyncio.DatagramTransport):
        """Connection Made."""
        self._transport = transport

    def connection_lost(self, exc: Exception):
        """Connection Lost."""
        if exc:
            _LOGGER.error("Connection Error: %s", exc)
        if self._transport:
            self._transport.close()
            self._packets.put_nowait(None)

    def datagram_received(self, data: bytes, addr: tuple[str, int]):
        """Datagram Received."""
        self._packets.put_nowait((data, addr))

    def error_received(self, exc: Exception):
        """Error Received."""
        _LOGGER.error("Socket received error: %s", exc)

    def close(self):
        """Close transport."""
        if self._transport:
            self._transport.close()
            self._packets.put_nowait(None)

    async def recv(
        self, timeout: float = None
    ) -> Optional[tuple[bytes, tuple[str, int]]]:
        """Return received data."""
        try:
            return await asyncio.wait_for(self._packets.get(), timeout=timeout)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            pass
        return None

    def sendto(self, data: bytes, addr: tuple[str, int] = None):
        """Send packet."""
        self._transport.sendto(data, addr)

    def get_extra_info(self, name: str, default: Any = None) -> Any:
        """Return Extra Info."""
        return self._transport.get_extra_info(name, default)

    @property
    def opened(self) -> bool:
        """Return True if opened."""
        return self._transport is not None

    @property
    def closed(self) -> bool:
        """Return True if closed."""
        if not self._transport:
            return False
        return self._transport.is_closing()

    @property
    def sock(self) -> socket.socket:
        """Return sock."""
        return self._transport.get_extra_info("socket")


class AsyncUDPSocket:
    """Async UDP socket."""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        self.close()

    def __init__(self, protocol: _AsyncUDPProtocol):
        self._protocol = protocol

    def close(self):
        """Close the socket."""
        self._protocol.close()

    def sendto(self, data: bytes, addr: tuple[str, int] = None):
        """Send Packet"""
        self._protocol.sendto(data, addr)

    async def recv(self, timeout: float = None):
        """Receive a UDP packet."""

        packet = await self._protocol.recv(timeout)

        if packet is None and self._protocol.closed:
            raise OSError("Socket is closed")

        return packet

    def get_extra_info(self, name: str, default: Any = None) -> Any:
        """Return Extra Info."""
        return self._protocol.get_extra_info(name, default)

    def set_broadcast(self, enabled: bool):
        """Set Broadcast enabled."""
        self._protocol.sock.setsockopt(
            socket.SOL_SOCKET, socket.SO_BROADCAST, int(enabled)
        )

    @property
    def opened(self) -> bool:
        """Return True if opened."""
        return self._protocol.opened

    @property
    def closed(self) -> bool:
        """Return True if closed."""
        return self._protocol.closed


async def udp_socket(
    local_addr: tuple[str, int] = None,
    remote_addr: tuple[str, int] = None,
    *,
    reuse_port: bool = None,
    allow_broadcast: bool = None,
    sock: socket.socket = None,
    **kwargs,
) -> AsyncUDPSocket:
    """Return UDP Socket."""
    loop = asyncio.get_running_loop()
    _, protocol = await loop.create_datagram_endpoint(
        _AsyncUDPProtocol,
        local_addr=local_addr,
        remote_addr=remote_addr,
        reuse_port=reuse_port,
        allow_broadcast=allow_broadcast,
        sock=sock,
        **kwargs,
    )

    return AsyncUDPSocket(protocol)
