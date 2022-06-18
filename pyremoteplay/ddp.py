"""Device Discovery Protocol for RP Hosts."""
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

from .socket import AsyncUDPSocket, udp_socket
from .const import (
    TYPE_PS4,
    UDP_PORT,
    DDP_PORTS,
    DEFAULT_UDP_PORT,
    BROADCAST_IP,
)

_LOGGER = logging.getLogger(__name__)

UDP_IP = "0.0.0.0"

DDP_VERSION = "00030010"
DDP_TYPE_SEARCH = "SRCH"
DDP_TYPE_LAUNCH = "LAUNCH"
DDP_TYPE_WAKEUP = "WAKEUP"
DDP_MSG_TYPES = (DDP_TYPE_SEARCH, DDP_TYPE_LAUNCH, DDP_TYPE_WAKEUP)

STATUS_OK = 200
STATUS_STANDBY = 620


# pylint: disable=c-extension-no-member
def get_private_addresses() -> list[str]:
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
    """Return host type."""
    return response.get("host-type")


def get_ddp_message(msg_type: str, data: dict = None):
    """Get DDP message."""
    if msg_type not in DDP_MSG_TYPES:
        raise TypeError(f"DDP MSG type: '{msg_type}' is not a valid type")
    msg = f"{msg_type} * HTTP/1.1\n"
    if data is not None:
        for key, value in data.items():
            msg = f"{msg}{key}:{value}\n"
    msg = f"{msg}device-discovery-protocol-version:{DDP_VERSION}\n"
    return msg


def parse_ddp_response(rsp: Union[str, bytes]):
    """Parse the response."""
    data = {}
    if not isinstance(rsp, str):
        if not isinstance(rsp, bytes):
            raise ValueError("Expected str or bytes")
        try:
            rsp = rsp.decode("utf-8")
        except UnicodeDecodeError:
            _LOGGER.debug("DDP message is not utf-8: %s", rsp)
            return data
    if DDP_TYPE_SEARCH in rsp:
        _LOGGER.info("Received %s message", DDP_TYPE_SEARCH)
        return data
    app_name = None
    for line in rsp.splitlines():
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
                    "Line: %s; does not contain key, value. Response: %s", line, rsp
                )
                continue
            data[values[0]] = values[1]
    if app_name is not None:
        data["running-app-name"] = app_name
    return data


def get_ddp_search_message() -> str:
    """Get DDP search message."""
    return get_ddp_message(DDP_TYPE_SEARCH)


def get_ddp_wake_message(credential: str) -> str:
    """Get DDP wake message."""
    data = {
        "user-credential": credential,
        "client-type": "vr",
        "auth-type": "R",
        "model": "w",
        "app-type": "r",
    }
    return get_ddp_message(DDP_TYPE_WAKEUP, data)


def get_ddp_launch_message(credential: str) -> str:
    """Get DDP launch message."""
    data = {
        "user-credential": credential,
        "client-type": "a",
        "auth-type": "C",
    }
    return get_ddp_message(DDP_TYPE_LAUNCH, data)


def get_socket(
    address: Optional[str] = UDP_IP, port: Optional[int] = DEFAULT_UDP_PORT
) -> socket.socket:
    """Return DDP socket object."""
    retries = 0
    sock = None
    while retries <= 1:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(0)
        try:
            if hasattr(socket, "SO_REUSEPORT"):
                sock.setsockopt(
                    socket.SOL_SOCKET, socket.SO_REUSEPORT, 1
                )  # noqa: pylint: disable=no-member
            sock.bind((address, port))
        except socket.error as error:
            _LOGGER.error("Error getting DDP socket with port: %s: %s", port, error)
            sock = None
            retries += 1
            port = UDP_PORT
            address = UDP_IP
        else:
            return sock
    return sock


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


def send_search_msg(host: str, host_type: str = TYPE_PS4, sock: socket.socket = None):
    """Send SRCH message only."""
    msg = get_ddp_search_message()
    return _send_msg(host, msg, host_type=host_type, sock=sock)


def search(
    host: str = BROADCAST_IP,
    port: int = UDP_PORT,
    host_type: str = "",
    sock: socket.socket = None,
    timeout: int = 3,
) -> list:
    """Return list of discovered devices."""
    ps_list = []
    found = set()
    close = True
    msg = get_ddp_search_message()

    if host is None:
        host = BROADCAST_IP
    _LOGGER.debug("Sending search message")
    if sock is None:
        if sys.platform == "win32" and host == BROADCAST_IP:
            # Windows doesn't seem to send broadcast out over all interfaces
            addresses = get_private_addresses()
            socks = [get_socket(address=_address, port=port) for _address in addresses]
        else:
            socks = [get_socket(port=port)]
    else:
        socks = [sock]
        close = False
    host_types = [host_type] if host_type else DDP_PORTS.keys()
    for host_type in host_types:
        for _sock in socks:
            _send_msg(host, msg, host_type=host_type, sock=_sock, close=False)

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
                data = parse_ddp_response(data)
                if ip_address not in found and data:
                    found.add(ip_address)
                    data["host-ip"] = ip_address
                    ps_list.append(data)
                if host != BROADCAST_IP:
                    break
    if close:
        for _sock in socks:
            _sock.close()
    return ps_list


def get_status(
    host: str, port: int = UDP_PORT, host_type: str = "", sock: socket.socket = None
):
    """Return status dict."""
    ps_list = search(host=host, port=port, host_type=host_type, sock=sock)
    if not ps_list:
        return None
    return ps_list[0]


def wakeup(
    host: str, credential: str, host_type: str = TYPE_PS4, sock: socket.socket = None
):
    """Wakeup Host."""
    msg = get_ddp_wake_message(credential)
    _send_msg(host, msg, host_type=host_type, sock=sock)


def launch(
    host: str, credential: str, host_type: str = TYPE_PS4, sock: socket.socket = None
):
    """Launch."""
    msg = get_ddp_launch_message(credential)
    _send_msg(host, msg, host_type=host_type, sock=sock)


async def _async_get_socket(
    address: str = UDP_IP,
    port: int = DEFAULT_UDP_PORT,
    remote_addr: tuple[str, int] = None,
) -> AsyncUDPSocket:
    try:
        sock = await udp_socket(
            local_addr=(address, port), remote_addr=remote_addr, reuse_port=True
        )
    except OSError:
        _LOGGER.warning("Port %s in use. Using random port", port)
        sock = await udp_socket(local_addr=(UDP_IP, UDP_PORT), remote_addr=remote_addr)
    return sock


async def _async_send_msg(
    addresses: list[str],
    port: int,
    host: str,
    msg: str,
    host_type: str = "",
) -> list[AsyncUDPSocket]:
    """Send a ddp message."""
    remote_ports = []
    socks = []
    host_types = [host_type] if host_type else DDP_PORTS.keys()
    for host_type in host_types:
        _port = DDP_PORTS.get(host_type)
        if _port:
            remote_ports.append(_port)
    if not remote_ports:
        raise ValueError(f"Invalid host type: {host_type}")

    for address in addresses:
        if host == BROADCAST_IP:
            sock = await _async_get_socket(address, port)
            sock.set_broadcast(True)
        else:
            sock = await _async_get_socket(address, port)
        for _port in remote_ports:
            remote = (host, _port)
        sock.sendto(msg.encode(), remote)
        socks.append(sock)
    return socks


async def _async_recv_search_msg(
    host: str, sock: AsyncUDPSocket, timeout: float, stop: asyncio.Event
) -> dict:
    devices = {}
    start = time.time()
    while time.time() - start < timeout:
        data = addr = response = None
        if stop.is_set():
            return devices
        response = await sock.recv(0.01)
        if response is not None:
            data, addr = response
        if data is not None and addr is not None:
            data = parse_ddp_response(data)
            ip_address = addr[0]
            if host != BROADCAST_IP and ip_address != host:
                continue
            if ip_address not in devices and data:
                data["host-ip"] = ip_address
                devices[ip_address] = data
            if host != BROADCAST_IP:
                stop.set()
                return devices
        await asyncio.sleep(0)
    return devices


async def async_search(
    host: str = BROADCAST_IP,
    port: int = DEFAULT_UDP_PORT,
    host_type: str = "",
    timeout: int = 3,
) -> list[dict]:
    """Return list of discovered devices."""
    addresses = []
    msg = get_ddp_search_message()
    _LOGGER.debug("Sending search message")

    if sys.platform == "win32":
        if host == BROADCAST_IP:
            addresses = get_private_addresses()

    if not addresses:
        addresses = [UDP_IP]
    # Using this to return status as soon as possible if targeting a specific device
    stop = asyncio.Event()

    socks = await _async_send_msg(addresses, port, host, msg, host_type)

    if not socks:
        raise RuntimeError("Could not get async sockets")

    results = await asyncio.gather(
        *[_async_recv_search_msg(host, _sock, timeout, stop) for _sock in socks]
    )
    devices = {}
    for result in results:
        if result:
            devices.update(result)
    for sock in socks:
        sock.close()
    return list(devices.values())


async def async_get_status(
    host: str,
    port: int = DEFAULT_UDP_PORT,
    host_type: str = "",
):
    """Return status dict."""
    device_list = await async_search(
        host=host,
        port=port,
        host_type=host_type,
    )
    if not device_list:
        return None
    return device_list[0]


async def async_wakeup(
    host: str,
    credential: str,
    port: int = DEFAULT_UDP_PORT,
    host_type: str = "",
    address: str = UDP_IP,
):
    """Wakeup Host."""
    msg = get_ddp_wake_message(credential)
    socks = await _async_send_msg([address], port, host, msg, host_type)
    for sock in socks:
        sock.close()


async def async_launch(
    host: str,
    credential: str,
    port: int = DEFAULT_UDP_PORT,
    host_type: str = "",
    address: str = UDP_IP,
):
    """Launch."""
    msg = get_ddp_launch_message(credential)
    socks = await _async_send_msg([address], port, host, msg, host_type)
    for sock in socks:
        sock.close()
