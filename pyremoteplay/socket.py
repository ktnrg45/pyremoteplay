"""Async UDP Sockets. Based on asyncudp (https://github.com/eerimoq/asyncudp)."""
from __future__ import annotations
import asyncio
import logging
from typing import Any, Optional, Callable, Union
import socket

_LOGGER = logging.getLogger(__name__)


class AsyncUDPProtocol(asyncio.DatagramProtocol):
    """UDP Protocol."""

    def __del__(self):
        self.close()

    def __init__(self):
        super().__init__()
        self._packets = asyncio.Queue()
        self._transport = None
        self._callback = None

    def connection_made(self, transport: asyncio.DatagramTransport):
        """Connection Made."""
        self._transport = transport

    def connection_lost(self, exc: Exception):
        """Connection Lost."""
        if exc:
            _LOGGER.error("Connection Lost: %s", exc)
        self.close()
        self._packets.put_nowait(None)

    def datagram_received(self, data: bytes, addr: tuple[str, int]):
        """Datagram Received."""
        item = (data, addr)
        if self.has_callback:
            self._callback(*item)
        else:
            self._packets.put_nowait(item)

    def error_received(self, exc: Exception):
        """Error Received."""
        _LOGGER.error("Socket at: %s received error: %s", self.sock.getsockname(), exc)

    def set_callback(
        self, callback: Union[Callable[[bytes, tuple[str, int]], None], None]
    ):
        """Set callback for datagram received.

        Setting this will flush packet received packet queue.
        :meth:`recv() <pyremoteplay.socket.AsyncUDPProtocol.recv>`
        will always return None.

        :param callback: callback for data received
        """
        if callback is not None:
            if not isinstance(callback, Callable):
                raise TypeError(f"Expected callable. Got: {type(callback)}")
            self._packets = asyncio.Queue()
        self._callback = callback

    def close(self):
        """Close transport."""
        if not self.closed:
            self._transport.close()
            self._packets.put_nowait(None)

    async def recv(
        self, timeout: float = None
    ) -> Optional[tuple[bytes, tuple[str, int]]]:
        """Return received data."""
        if self.has_callback:
            return None
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
    def has_callback(self):
        """Return True if callback is set."""
        return self._callback is not None

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

    @classmethod
    async def create(
        cls,
        local_addr: tuple[str, int] = None,
        remote_addr: tuple[str, int] = None,
        *,
        reuse_port: bool = None,
        allow_broadcast: bool = None,
        sock: socket.socket = None,
        **kwargs,
    ) -> AsyncUDPSocket:
        """Create and return UDP Socket."""
        _local_addr = local_addr
        if sock is not None:
            local_addr = remote_addr = None
        loop = asyncio.get_running_loop()
        if not hasattr(socket, "SO_REUSEPORT"):
            reuse_port = None
        _, protocol = await loop.create_datagram_endpoint(
            AsyncUDPProtocol,
            local_addr=local_addr,
            remote_addr=remote_addr,
            reuse_port=reuse_port,
            allow_broadcast=allow_broadcast,
            sock=sock,
            **kwargs,
        )

        return cls(protocol, _local_addr)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        self.close()

    def __del__(self):
        self.close()

    def __init__(self, protocol: AsyncUDPProtocol, local_addr: tuple[str, int] = None):
        self._protocol = protocol
        self._ip_address = local_addr[0] if local_addr else None

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

    def set_callback(
        self, callback: Union[Callable[[bytes, tuple[str, int]], None], None]
    ):
        """Set callback for datagram received.

        Setting this will flush packet received packet queue.
        :meth:`recv() <pyremoteplay.socket.AsyncUDPSocket.recv>`
        will always return None.

        :param callback: callback for data received
        """
        self._protocol.set_callback(callback)

    @property
    def opened(self) -> bool:
        """Return True if opened."""
        return self._protocol.opened

    @property
    def closed(self) -> bool:
        """Return True if closed."""
        return self._protocol.closed

    @property
    def sock(self) -> socket.socket:
        """Return socket."""
        return self._protocol.sock

    @property
    def local_addr(self) -> tuple[str, int]:
        """Return local address."""
        addr = self.sock.getsockname()
        if self._ip_address:
            return (self._ip_address, addr[1])
        return addr
