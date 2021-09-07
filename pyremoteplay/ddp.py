"""Device Discovery Protocol for RP Hosts."""

import logging
import re
import select
import socket
import time
from typing import Optional

from .const import TYPE_PS4, TYPE_PS5

_LOGGER = logging.getLogger(__name__)

BROADCAST_IP = '255.255.255.255'
UDP_IP = '0.0.0.0'
UDP_PORT = 0
DEFAULT_UDP_PORT = 1987

DDP_PORT_PS4 = 987
DDP_PORT_PS5 = 9032
DDP_VERSION = '00030010'
DDP_TYPE_SEARCH = 'SRCH'
DDP_TYPE_LAUNCH = 'LAUNCH'
DDP_TYPE_WAKEUP = 'WAKEUP'
DDP_MSG_TYPES = (DDP_TYPE_SEARCH, DDP_TYPE_LAUNCH, DDP_TYPE_WAKEUP)

DDP_PORTS = {
    TYPE_PS4: DDP_PORT_PS4,
    TYPE_PS5: DDP_PORT_PS5,
}

DEFAULT_POLL_COUNT = 5

DEFAULT_STANDBY_DELAY = 50

STATUS_OK = 200
STATUS_STANDBY = 620


def get_host_type(response: dict) -> str:
    """Return host type."""
    return response.get("host-type")


def get_ddp_message(msg_type, data=None):
    """Get DDP message."""
    if msg_type not in DDP_MSG_TYPES:
        raise TypeError(
            "DDP MSG type: '{}' is not a valid type".format(msg_type))
    msg = u'{} * HTTP/1.1\n'.format(msg_type)
    if data is not None:
        for key, value in data.items():
            msg += '{}:{}\n'.format(key, value)
    msg += 'device-discovery-protocol-version:{}\n'.format(DDP_VERSION)
    return msg


def parse_ddp_response(rsp):
    """Parse the response."""
    data = {}
    if DDP_TYPE_SEARCH in rsp:
        _LOGGER.info("Received %s message", DDP_TYPE_SEARCH)
        return data
    app_name = None
    for line in rsp.splitlines():
        if 'running-app-name' in line:
            app_name = line
            app_name = app_name.replace('running-app-name:', '')
        re_status = re.compile(r'HTTP/1.1 (?P<code>\d+) (?P<status>.*)')
        line = line.strip()
        # skip empty lines
        if not line:
            continue
        if re_status.match(line):
            data[u'status_code'] = int(re_status.match(line).group('code'))
            data[u'status'] = re_status.match(line).group('status')
        else:
            values = line.split(':')
            data[values[0]] = values[1]
    if app_name is not None:
        data['running-app-name'] = app_name
    return data


def get_ddp_search_message():
    """Get DDP search message."""
    return get_ddp_message(DDP_TYPE_SEARCH)


def get_ddp_wake_message(credential):
    """Get DDP wake message."""
    data = {
        'user-credential': credential,
        'client-type': 'a',
        'auth-type': 'C',
    }
    return get_ddp_message(DDP_TYPE_WAKEUP, data)


def get_ddp_launch_message(credential):
    """Get DDP launch message."""
    data = {
        'user-credential': credential,
        'client-type': 'a',
        'auth-type': 'C',
    }
    return get_ddp_message(DDP_TYPE_LAUNCH, data)


def get_socket(port: Optional[int] = DEFAULT_UDP_PORT):
    """Return DDP socket object."""
    retries = 0
    sock = None
    while retries <= 1:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(0)
        try:
            if hasattr(socket, "SO_REUSEPORT"):
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)  # noqa: pylint: disable=no-member
            sock.bind((UDP_IP, port))
        except socket.error as error:
            _LOGGER.error(
                "Error getting DDP socket with port: %s: %s", port, error)
            sock = None
            retries += 1
            port = UDP_PORT
        else:
            return sock
    return sock


def _send_recv_msg(
        host,
        msg,
        host_type=TYPE_PS4,
        receive=True,
        send=True,
        sock=None,
        close=True,
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

        sock.sendto(msg.encode('utf-8'), (host, port))
        _LOGGER.debug(
            "SENT DDP MSG: SPORT=%s DEST=%s",
            sock.getsockname()[1], (host, port))

    if receive:
        available, _, _ = select.select([sock], [], [], 0.01)
        if sock in available:
            response = sock.recvfrom(1024)
            _LOGGER.debug(
                "RECV DDP MSG: DPORT=%s SRC=%s",
                sock.getsockname()[1], response[1])
    if close:
        sock.close()
    return response


def _send_msg(host, msg, host_type=TYPE_PS4, sock=None, close=True):
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


def _recv_msg(host, msg, sock=None, close=True):
    """Send a ddp message."""
    return _send_recv_msg(
        host,
        msg,
        receive=True,
        send=False,
        sock=sock,
        close=close,
    )


def send_search_msg(host, host_type=TYPE_PS4, sock=None):
    """Send SRCH message only."""
    msg = get_ddp_search_message()
    return _send_msg(host, msg, host_type=host_type, sock=sock)


def search(host=BROADCAST_IP, port=UDP_PORT, sock=None, timeout=3) -> list:
    """Return list of discovered PS4s."""
    ps_list = []
    msg = get_ddp_search_message()
    start = time.time()

    if host is None:
        host = BROADCAST_IP
    if sock is None:
        sock = get_socket(port=port)
    _LOGGER.debug("Sending search message")
    for host_type in DDP_PORTS.keys():
        _send_msg(host, msg, host_type=host_type, sock=sock, close=False)
    while time.time() - start < timeout:
        data = addr = None
        response = _recv_msg(host, msg, sock=sock, close=False)
        if response is not None:
            data, addr = response
        if data is not None and addr is not None:
            data = parse_ddp_response(data.decode('utf-8'))
            if data not in ps_list and data:
                data[u'host-ip'] = addr[0]
                ps_list.append(data)
            if host != BROADCAST_IP:
                break
    sock.close()
    return ps_list


def get_status(host, port=UDP_PORT, sock=None):
    """Return status dict."""
    ps_list = search(host=host, port=port, sock=sock)
    if not ps_list:
        return None
    return ps_list[0]


def wakeup(host, credential, host_type=TYPE_PS4, sock=None):
    """Wakeup Host."""
    msg = get_ddp_wake_message(credential)
    _send_msg(host, msg, host_type=host_type, sock=sock)


def launch(host, credential, host_type=TYPE_PS4, sock=None):
    """Launch."""
    msg = get_ddp_launch_message(credential)
    _send_msg(host, msg, host_type=host_type, sock=sock)
