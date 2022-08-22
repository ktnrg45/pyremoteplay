"""Async UDP Sockets. Based on asyncudp (https://github.com/eerimoq/asyncudp)."""
from __future__ import annotations
import asyncio
import logging
from typing import Any, Optional, Callable, Union
import socket

_LOGGER = logging.getLogger(__name__)


class AsyncBaseProtocol(asyncio.BaseProtocol):
    """Base Protocol. Do not use directly."""

    def __del__(self):
        self.close()

    def __init__(self):
        super().__init__()
        self._packets = asyncio.Queue()
        self._transport = None
        self._callback = None

    def connection_made(self, transport: asyncio.BaseTransport):
        """Connection Made."""
        self._transport = transport

    def connection_lost(self, exc: Exception):
        """Connection Lost."""
        if exc:
            _LOGGER.error("Connection Lost: %s", exc)
        self.close()
        self._packets.put_nowait(None)

    def error_received(self, exc: Exception):
        """Error Received."""
        _LOGGER.error("Socket at: %s received error: %s", self.sock.getsockname(), exc)

    async def recvfrom(
        self, timeout: float = None
    ) -> Optional[tuple[bytes, tuple[str, int]]]:
        """Return received data and addr."""
        if self.has_callback:
            return None
        try:
            return await asyncio.wait_for(self._packets.get(), timeout=timeout)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            pass
        return None

    async def recv(self, timeout: float = None) -> Optional[bytes]:
        """Return received data."""
        if self.has_callback:
            return None
        response = await self.recvfrom(timeout=timeout)
        if response:
            return response[0]
        return None

    def sendto(self, data: bytes, *_):
        """Send packet."""
        raise NotImplementedError

    def set_callback(
        self, callback: Union[Callable[[bytes, tuple[str, int]], None], None]
    ):
        """Set callback for data received.

        Setting this will flush packet received packet queue.
        :meth:`recv() <pyremoteplay.socket.AsyncBaseSocket.recv>`
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
            self._packets.put_nowait(None)
            try:
                self._transport.close()
            # pylint: disable=broad-except
            except Exception:
                self.sock.close()

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


class AsyncTCPProtocol(asyncio.Protocol, AsyncBaseProtocol):
    """UDP Protocol."""

    def connection_made(self, transport: asyncio.Transport):
        """Connection Made."""
        self._transport = transport

    def data_received(self, data: bytes):
        peername = self._transport.get_extra_info("peername")
        item = (data, peername)
        if self.has_callback:
            self._callback(*item)
        else:
            self._packets.put_nowait(item)

    def sendto(self, data: bytes, *_):
        """Send packet to address."""
        self._transport.write(data)


class AsyncUDPProtocol(asyncio.DatagramProtocol, AsyncBaseProtocol):
    """UDP Protocol."""

    def connection_made(self, transport: asyncio.DatagramTransport):
        """Connection Made."""
        self._transport = transport

    def datagram_received(self, data: bytes, addr: tuple[str, int]):
        """Datagram Received."""
        item = (data, addr)
        if self.has_callback:
            self._callback(*item)
        else:
            self._packets.put_nowait(item)

    # pylint: disable=arguments-differ
    def sendto(self, data: bytes, addr: tuple[str, int] = None):
        """Send packet to address."""
        self._transport.sendto(data, addr)


class AsyncBaseSocket:
    """Async Base socket. Do not use directly."""

    @classmethod
    async def create(
        cls,
        local_addr: tuple[str, int] = None,
        remote_addr: tuple[str, int] = None,
        *,
        sock: socket.socket = None,
        **kwargs,
    ) -> AsyncBaseSocket:
        """Create and return Socket."""
        raise NotImplementedError

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        self.close()

    def __del__(self):
        self.close()

    def __init__(self, protocol: AsyncBaseProtocol, local_addr: tuple[str, int] = None):
        self._protocol = protocol
        self._ip_address = local_addr[0] if local_addr else None

    def close(self):
        """Close the socket."""
        self._protocol.close()

    def sendto(self, data: bytes, addr: tuple[str, int] = None):
        """Send Packet"""
        self._protocol.sendto(data, addr)

    async def recv(self, timeout: float = None) -> Optional[bytes]:
        """Receive a packet."""

        packet = await self._protocol.recv(timeout)

        if packet is None and self._protocol.closed:
            raise OSError("Socket is closed")

        return packet

    async def recvfrom(
        self, timeout: float = None
    ) -> Optional[tuple[bytes, tuple[str, int]]]:
        """Receive a packet and address."""

        response = await self._protocol.recvfrom(timeout)

        if response is None and self._protocol.closed:
            raise OSError("Socket is closed")

        return response

    def get_extra_info(self, name: str, default: Any = None) -> Any:
        """Return Extra Info."""
        return self._protocol.get_extra_info(name, default)

    def setsockopt(self, __level: int, __optname: int, __value: int | bytes, /):
        """Set Sock Opt."""
        self.sock.setsockopt(__level, __optname, __value)

    def set_callback(
        self, callback: Union[Callable[[bytes, tuple[str, int]], None], None]
    ):
        """Set callback for data received.

        Setting this will flush packet received packet queue.
        :meth:`recv() <pyremoteplay.socket.AsyncBaseSocket.recv>`
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


class AsyncTCPSocket(AsyncBaseSocket):
    """Async TCP socket."""

    @classmethod
    async def create(
        cls,
        local_addr: tuple[str, int] = None,
        remote_addr: tuple[str, int] = None,
        *,
        sock: socket.socket = None,
        **kwargs,
    ) -> AsyncTCPSocket:
        """Create and return Socket."""
        _local_addr = local_addr
        host = port = None
        if sock is not None:
            local_addr = remote_addr = None
        if remote_addr:
            host, port = remote_addr

        if sock is None and not remote_addr:
            raise ValueError("Both sock and remote_addr cannot be None")
        loop = asyncio.get_running_loop()
        _, protocol = await loop.create_connection(
            AsyncTCPProtocol,
            host=host,
            port=port,
            sock=sock,
            local_addr=local_addr,
            **kwargs,
        )

        return cls(protocol, _local_addr)

    def send(self, data: bytes):
        """Send Packet."""
        self.sendto(data)


class AsyncUDPSocket(AsyncBaseSocket):
    """Async UDP socket."""

    @classmethod
    async def create(
        cls,
        local_addr: tuple[str, int] = None,
        remote_addr: tuple[str, int] = None,
        *,
        sock: socket.socket = None,
        reuse_port: bool = None,
        allow_broadcast: bool = None,
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

    def set_broadcast(self, enabled: bool):
        """Set Broadcast enabled."""
        self._protocol.sock.setsockopt(
            socket.SOL_SOCKET, socket.SO_BROADCAST, int(enabled)
        )
