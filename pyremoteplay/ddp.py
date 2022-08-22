"""Device Discovery Protocol for RP Hosts.

    This module contains lower-level functions which don't
    need to be called directly.
"""
from __future__ import annotations
import asyncio
import logging
import re
import select
import socket
import time
from typing import Optional, Union
import ipaddress
import sys

import netifaces

from .socket import AsyncUDPSocket
from .const import TYPE_PS4, UDP_PORT, DDP_PORTS, DEFAULT_UDP_PORT, BROADCAST_IP, UDP_IP

_LOGGER = logging.getLogger(__name__)

DDP_VERSION = "00030010"
DDP_TYPE_SEARCH = "SRCH"
DDP_TYPE_LAUNCH = "LAUNCH"
DDP_TYPE_WAKEUP = "WAKEUP"
DDP_MSG_TYPES = (DDP_TYPE_SEARCH, DDP_TYPE_LAUNCH, DDP_TYPE_WAKEUP)

STATUS_OK = 200
STATUS_STANDBY = 620

# pylint: disable=c-extension-no-member
def _get_broadcast(ip_address: str) -> str:
    """Return broadcast address."""
    if ip_address == UDP_IP:
        return BROADCAST_IP
    for interface in netifaces.interfaces():
        addresses = netifaces.ifaddresses(interface).get(netifaces.AF_INET)
        if not addresses:
            continue
        for address in addresses:
            addr = address.get("addr")
            if addr and addr == ip_address:
                broadcast = address.get("broadcast")
                if broadcast:
                    return broadcast
    return BROADCAST_IP


# pylint: disable=c-extension-no-member
def _get_private_addresses() -> list[str]:
    """Return Private Interface Addresses."""
    interfaces = []
    for interface in netifaces.interfaces():
        addresses = netifaces.ifaddresses(interface).get(netifaces.AF_INET)
        if not addresses:
            continue
        for address in addresses:
            addr = address.get("addr")
            if addr:
                ip_address = ipaddress.IPv4Address(addr)
                if all(
                    [
                        ip_address.is_private,
                        not ip_address.is_link_local,
                        not ip_address.is_loopback,
                        not ip_address.is_multicast,
                        not ip_address.is_unspecified,
                    ]
                ):
                    interfaces.append(addr)
    return interfaces


def get_host_type(response: dict) -> str:
    """Return host type.

    :param response: Response dict from host
    """
    return response.get("host-type")


def get_ddp_message(msg_type: str, data: dict = None):
    """Get DDP message.

    :param msg_type: Message Type
    :param data: Extra data to add
    """
    if msg_type not in DDP_MSG_TYPES:
        raise TypeError(f"DDP MSG type: '{msg_type}' is not a valid type")
    msg = f"{msg_type} * HTTP/1.1\n"
    if data is not None:
        for key, value in data.items():
            msg = f"{msg}{key}:{value}\n"
    msg = f"{msg}device-discovery-protocol-version:{DDP_VERSION}\n"
    return msg


def parse_ddp_response(response: Union[str, bytes], remote_address: str):
    """Parse the response.

    :param response: Raw response from host
    :param remote_address: Remote address of host
    """
    data = {}
    if not isinstance(response, str):
        if not isinstance(response, bytes):
            raise ValueError("Expected str or bytes")
        try:
            response = response.decode("utf-8")
        except UnicodeDecodeError:
            _LOGGER.debug("DDP message is not utf-8: %s", response)
            return data
    if DDP_TYPE_SEARCH in response:
        _LOGGER.info("Received %s message", DDP_TYPE_SEARCH)
        return data
    app_name = None
    for line in response.splitlines():
        if "running-app-name" in line:
            app_name = line
            app_name = app_name.replace("running-app-name:", "")
        re_status = re.compile(r"HTTP/1.1 (?P<code>\d+) (?P<status>.*)")
        line = line.strip()
        # skip empty lines
        if not line:
            continue
        if re_status.match(line):
            data["status-code"] = int(re_status.match(line).group("code"))
            data["status"] = re_status.match(line).group("status")
        else:
            values = line.split(":")
            if len(values) != 2:
                _LOGGER.debug(
                    "Line: %s; does not contain key, value. Response: %s",
                    line,
                    response,
                )
                continue
            data[values[0]] = values[1]
    if app_name is not None:
        data["running-app-name"] = app_name
    data["host-ip"] = remote_address
    return data


def get_ddp_search_message() -> str:
    """Get DDP search message."""
    return get_ddp_message(DDP_TYPE_SEARCH)


def get_ddp_wake_message(credential: str) -> str:
    """Get DDP wake message.

    :param credential: User Credential from User Profile
    """
    data = {
        "user-credential": credential,
        "client-type": "vr",
        "auth-type": "R",
        "model": "w",
        "app-type": "r",
    }
    return get_ddp_message(DDP_TYPE_WAKEUP, data)


def get_ddp_launch_message(credential: str) -> str:
    """Get DDP launch message.

    :param credential: User Credential from User Profile
    """
    data = {
        "user-credential": credential,
        "client-type": "a",
        "auth-type": "C",
    }
    return get_ddp_message(DDP_TYPE_LAUNCH, data)


def get_socket(
    local_address: Optional[str] = UDP_IP, local_port: Optional[int] = DEFAULT_UDP_PORT
) -> socket.socket:
    """Return DDP socket.

    :param local_address: Local address to use
    :param local_port: Local port to use
    """

    def _create_socket(address: str, port: int) -> socket.socket:
        _sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        _sock.settimeout(0)
        if hasattr(socket, "SO_REUSEPORT"):
            _sock.setsockopt(
                socket.SOL_SOCKET, socket.SO_REUSEPORT, 1
            )  # noqa: pylint: disable=no-member
        _sock.bind((address, port))
        _LOGGER.debug("Bound socket to address: (%s, %s)", address, port)
        return _sock

    try:
        sock = _create_socket(local_address, local_port)
    except socket.error as error:
        _LOGGER.error("Error getting DDP socket with port: %s: %s", local_port, error)
        sock = _create_socket(UDP_IP, UDP_PORT)
        _LOGGER.debug("Created socket with random port")
    return sock


def get_sockets(
    local_port: int = UDP_PORT, directed: bool = None
) -> tuple[list[socket.socket], list[str]]:
    """Return list of sockets needed.

    :param local_port: Local port to use
    :param directed: If True will use directed broadcast with all local interfaces.
    """
    if directed is None:
        directed = True if sys.platform == "win32" else False

    addresses = []
    if directed:
        addresses = _get_private_addresses()

    if not addresses:
        addresses = [UDP_IP]

    socks = [get_socket(address, local_port) for address in addresses]
    return socks, addresses


def _send_recv_msg(
    host: str,
    msg: str,
    host_type: str = TYPE_PS4,
    receive: bool = True,
    send: bool = True,
    sock: socket.socket = None,
    close: bool = True,
):
    """Send a ddp message and receive the response."""
    response = None
    if sock is None:
        if not close:
            raise ValueError("Unspecified sockets must be closed")
        sock = get_socket()

    if send:
        if host == BROADCAST_IP:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            _LOGGER.debug("Broadcast enabled")
        port = DDP_PORTS.get(host_type)
        if port is None:
            raise ValueError(f"Invalid host type: {host_type}")

        sock.sendto(msg.encode("utf-8"), (host, port))
        _LOGGER.debug(
            "SENT DDP MSG: SPORT=%s DEST=%s", sock.getsockname()[1], (host, port)
        )

    if receive:
        available, _, _ = select.select([sock], [], [], 0.01)
        if sock in available:
            response = sock.recvfrom(1024)
            _LOGGER.debug(
                "RECV DDP MSG: DPORT=%s SRC=%s", sock.getsockname()[1], response[1]
            )
    if close:
        sock.close()
    return response


def _send_msg(
    host: str,
    msg: str,
    host_type: str = TYPE_PS4,
    sock: socket.socket = None,
    close: bool = True,
):
    """Send a ddp message."""
    return _send_recv_msg(
        host,
        msg,
        host_type=host_type,
        receive=False,
        send=True,
        sock=sock,
        close=close,
    )


def _recv_msg(host: str, msg: str, sock: socket.socket = None, close: bool = True):
    """Send a ddp message."""
    return _send_recv_msg(
        host,
        msg,
        receive=True,
        send=False,
        sock=sock,
        close=close,
    )


def search(
    host: str = BROADCAST_IP,
    local_port: int = DEFAULT_UDP_PORT,
    host_type: str = "",
    sock: socket.socket = None,
    timeout: int = 3,
    directed: bool = None,
) -> list[dict]:
    """Return list of statuses for discovered devices.

    :param host: Remote host to send message to. Defaults to `255.255.255.255`.
    :param local_port: Local port to use. Defaults to any.
    :param host_type: Host type. Specific host type to search for.
    :param sock: Socket. Socket will not be closed if specified.
    :param timeout: Timeout in seconds.
    :param directed: If True will use directed broadcast with all local interfaces. \
    Sock will be ignored.
    """
    ps_list = []
    addresses = []
    found = set()
    close = not sock
    if directed is None:
        directed = True if sys.platform == "win32" and host == BROADCAST_IP else False
    if directed:
        sock = None
    msg = get_ddp_search_message()

    if not host:
        host = BROADCAST_IP
    _LOGGER.debug("Sending search message")
    if not sock:
        socks, addresses = get_sockets(local_port, directed)
    else:
        socks = [socks]
    if not socks:
        raise RuntimeError("Could not get sockets")

    if not addresses:
        addresses = [host]
    host_types = [host_type] if host_type else DDP_PORTS.keys()
    for host_type in host_types:
        for index, _sock in enumerate(socks):
            if host == BROADCAST_IP:
                _sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            if directed:
                _host = _get_broadcast(addresses[index])
            else:
                _host = host
            _send_msg(_host, msg, host_type=host_type, sock=_sock, close=False)

    start = time.time()
    while time.time() - start < timeout:
        if host != BROADCAST_IP and ps_list:
            break
        for _sock in socks:
            data = addr = None
            try:
                response = _recv_msg(host, msg, sock=_sock, close=False)
            except ConnectionResetError:
                continue
            if response is not None:
                data, addr = response
            if data is not None and addr is not None:
                ip_address = addr[0]
                data = parse_ddp_response(data, ip_address)
                if ip_address not in found and data:
                    found.add(ip_address)
                    ps_list.append(data)
                if host != BROADCAST_IP:
                    break
    if close:
        for _sock in socks:
            _sock.close()
    return ps_list


def get_status(
    host: str,
    local_port: int = DEFAULT_UDP_PORT,
    host_type: str = "",
    sock: socket.socket = None,
) -> dict:
    """Return host status dict.

    :param host: Host address
    :param local_port: Local port to use
    :param host_type: Host type to use
    :param sock: Socket to use
    """
    ps_list = search(host=host, local_port=local_port, host_type=host_type, sock=sock)
    if not ps_list:
        return None
    return ps_list[0]


def wakeup(
    host: str,
    credential: str,
    local_port: int = DEFAULT_UDP_PORT,
    host_type: str = TYPE_PS4,
    sock: socket.socket = None,
):
    """Wakeup Host.

    :param host: Host address
    :param credential: User Credential from User Profile
    :param local_port: Local port to use
    :param host_type: Host type to use
    :param sock: Socket to use
    """
    close = False
    if not sock:
        sock = get_socket(local_port=local_port)
        close = True
    msg = get_ddp_wake_message(credential)
    _send_msg(host, msg, host_type=host_type, sock=sock)
    if close:
        sock.close()


def launch(
    host: str,
    credential: str,
    local_port: int = DEFAULT_UDP_PORT,
    host_type: str = TYPE_PS4,
    sock: socket.socket = None,
):
    """Send Launch message.

    :param host: Host address
    :param credential: User Credential from User Profile
    :param local_port: Local port to use
    :param host_type: Host type to use
    :param sock: Socket to use
    """
    close = False
    if not sock:
        sock = get_socket(local_port=local_port)
        close = True
    msg = get_ddp_launch_message(credential)
    _send_msg(host, msg, host_type=host_type, sock=sock)
    if close:
        sock.close()


async def async_get_socket(
    local_address: str = UDP_IP, local_port: int = UDP_PORT
) -> AsyncUDPSocket:
    """Return async socket.

    :param local_address: Local address to use
    :param local_port: Local port to use
    """
    sock = get_socket(local_address, local_port)
    return await AsyncUDPSocket.create(
        local_addr=(local_address, local_port), sock=sock
    )


async def async_get_sockets(
    local_port: int = UDP_PORT, directed: bool = False
) -> list[AsyncUDPSocket]:
    """Return list of sockets needed.

    :param local_port: Local port to use
    :param directed: If True will use directed broadcast with all local interfaces.
    """
    addresses = []
    if directed:
        addresses = _get_private_addresses()

    if not addresses:
        addresses = [UDP_IP]

    socks = await asyncio.gather(
        *[async_get_socket(address, local_port) for address in addresses]
    )
    return socks


def async_send_msg(
    sock: AsyncUDPSocket,
    host: str,
    msg: str,
    host_type: str = "",
    directed: bool = False,
):
    """Send a ddp message using async socket.

    :param sock: Socket to use.
    :param host: Remote host to send message to.
    :param msg: Message to send.
    :param host_type: Host type.
    :param directed: If True will use directed broadcast with all local interfaces
    """
    if host == BROADCAST_IP:
        sock.set_broadcast(True)
        if directed:
            host = _get_broadcast(sock.local_addr[0])
    host_types = [host_type] if host_type else DDP_PORTS.keys()
    for host_type in host_types:
        port = DDP_PORTS.get(host_type)
        if port is None:
            raise ValueError(f"Invalid host type: {host_type}")

        sock.sendto(msg.encode(), (host, port))


async def _async_recv_search_msg(
    sock: AsyncUDPSocket, host: str, timeout: float, stop_event: asyncio.Event
) -> dict:
    devices = {}
    start = time.time()
    stop_event.clear()
    while time.time() - start < timeout:
        data = addr = response = None
        if stop_event.is_set():
            return devices
        response = await sock.recvfrom(0.01)
        if response is not None:
            data, addr = response
        if data is not None and addr is not None:
            ip_address = addr[0]
            data = parse_ddp_response(data, ip_address)
            if host not in (BROADCAST_IP, ip_address):
                continue
            if ip_address not in devices and data:
                devices[ip_address] = data
            if host != BROADCAST_IP:
                stop_event.set()
                return devices
        await asyncio.sleep(0)
    return devices


async def async_search(
    host: str = BROADCAST_IP,
    local_port: int = DEFAULT_UDP_PORT,
    host_type: str = "",
    sock: AsyncUDPSocket = None,
    timeout: int = 3,
    directed: bool = None,
) -> list[dict]:
    """Return list of statuses for discovered devices.

    :param host: Remote host to send message to. Defaults to `255.255.255.255`.
    :param local_port: Local port to use. Defaults to any.
    :param host_type: Host type. Specific host type to search for.
    :param sock: Socket. Socket will not be closed if specified.
    :param timeout: Timeout in seconds.
    :param directed: If True will use directed broadcast with all local interfaces. \
    Sock will be ignored.
    """
    close = not sock
    if directed is None:
        directed = True if sys.platform == "win32" and host == BROADCAST_IP else False
    if directed:
        sock = None
    msg = get_ddp_search_message()
    _LOGGER.debug("Searching Async")

    # Using this to return status as soon as possible if targeting a specific device
    stop = asyncio.Event()
    if not sock:
        socks = await async_get_sockets(local_port, directed)
    else:
        socks = [sock]
    if not socks:
        raise RuntimeError("Could not get async sockets")
    host_types = [host_type] if host_type else DDP_PORTS.keys()
    for host_type in host_types:
        for _sock in socks:
            _LOGGER.debug("Using Socket: %s", _sock.local_addr)
            async_send_msg(_sock, host, msg, host_type, directed=directed)

    results = await asyncio.gather(
        *[_async_recv_search_msg(_sock, host, timeout, stop) for _sock in socks]
    )
    devices = {}
    for result in results:
        if result:
            devices.update(result)
    if close:
        for _sock in socks:
            _sock.close()
    return list(devices.values())


async def async_get_status(
    host: str,
    local_port: int = DEFAULT_UDP_PORT,
    host_type: str = "",
    sock: AsyncUDPSocket = None,
) -> dict:
    """Return host status dict. Async.

    :param host: Host address
    :param local_port: Local port to use
    :param host_type: Host type to use
    :param sock: Socket to use
    """
    device_list = []

    if sys.platform == "win32":
        # TODO: Workaround for Windows
        # Windows has error:
        #   [WinError 1234] No service is operating at the destination network endpoint on the remote system

        loop = asyncio.get_running_loop()
        device_list = await loop.run_in_executor(
            None,
            search,
            host,
            local_port,
            host_type,
            sock,
        )
    else:
        device_list = await async_search(
            host=host,
            local_port=local_port,
            host_type=host_type,
            sock=sock,
            directed=False,
        )
    if not device_list:
        return {}
    return device_list[0]
