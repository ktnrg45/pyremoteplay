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

import asyncudp
import netifaces


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
    address: str = UDP_IP, port: int = DEFAULT_UDP_PORT
) -> asyncudp.Socket:
    try:
        sock = await asyncudp.create_socket(local_addr=(address, port))
    except OSError:
        _LOGGER.warning("Port %s in use. Using random port", port)
        sock = await asyncudp.create_socket(local_addr=(UDP_IP, UDP_PORT))
    return sock


async def _async_send_msg(
    sock: asyncudp.Socket, host: str, msg: str, host_type: str = ""
):
    """Send a ddp message."""
    if host == BROADCAST_IP:
        # pylint: disable=protected-access
        sock._transport.get_extra_info("socket").setsockopt(
            socket.SOL_SOCKET, socket.SO_BROADCAST, 1
        )
    host_types = [host_type] if host_type else DDP_PORTS.keys()
    for host_type in host_types:
        port = DDP_PORTS.get(host_type)
        sock.sendto(msg.encode(), (host, port))


async def _async_recv_msg(host: str, sock: asyncudp.Socket, timeout: float) -> dict:
    devices = {}
    start = time.time()
    while time.time() - start < timeout:
        data = addr = response = None
        remaining = timeout - (time.time() - start)
        try:
            response = await asyncio.wait_for(sock.recvfrom(), remaining)
        except ConnectionResetError:
            continue
        except asyncio.TimeoutError:
            pass
        if response is not None:
            data, addr = response
        if data is not None and addr is not None:
            data = parse_ddp_response(data)
            ip_address = addr[0]
            if ip_address not in devices and data:
                data["host-ip"] = ip_address
                devices[ip_address] = data
            if host != BROADCAST_IP:
                break
        await asyncio.sleep(0)
    return devices


async def async_search(
    host: str = BROADCAST_IP,
    port: int = DEFAULT_UDP_PORT,
    host_type: str = "",
    sock: asyncudp.Socket = None,
    timeout: int = 3,
) -> list[dict]:
    """Return list of discovered devices."""
    close = True
    msg = get_ddp_search_message()
    _LOGGER.debug("Sending search message")

    if sock is None:
        if sys.platform == "win32" and host == BROADCAST_IP:
            addresses = get_private_addresses()

            tasks = [
                _async_get_socket(address=_address, port=port) for _address in addresses
            ]
        else:
            tasks = [_async_get_socket(port=port)]
        socks = await asyncio.gather(*tasks)
    else:
        socks = [sock]
        close = False

    for _sock in socks:
        await _async_send_msg(_sock, host, msg, host_type)

    results = await asyncio.gather(
        *[_async_recv_msg(host, _sock, timeout) for _sock in socks]
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
    port: int = DEFAULT_UDP_PORT,
    host_type: str = "",
    sock: asyncudp.Socket = None,
):
    """Return status dict."""
    device_list = await async_search(
        host=host, port=port, host_type=host_type, sock=sock
    )
    if not device_list:
        return None
    return device_list[0]


async def async_wakeup(
    host: str,
    credential: str,
    port: int = DEFAULT_UDP_PORT,
    host_type: str = "",
    sock: asyncudp.Socket = None,
):
    """Wakeup Host."""
    msg = get_ddp_wake_message(credential)
    if sock is None:
        sock = await _async_get_socket(host, port)
    await _async_send_msg(sock, host, msg, host_type)
    sock.close()


async def async_launch(
    host: str,
    credential: str,
    port: int = DEFAULT_UDP_PORT,
    host_type: str = "",
    sock: asyncudp.Socket = None,
):
    """Launch."""
    msg = get_ddp_launch_message(credential)
    if sock is None:
        sock = await _async_get_socket(host, port)
    await _async_send_msg(sock, host, msg, host_type)
    sock.close()
