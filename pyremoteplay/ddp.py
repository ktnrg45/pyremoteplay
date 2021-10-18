"""Device Discovery Protocol for RP Hosts."""
import asyncio
import logging
import re
import select
import socket
import time
from ssl import SSLError
from typing import Optional

import aiohttp
from aiohttp.client_exceptions import ContentTypeError
from pyps4_2ndscreen.media_art import async_search_ps_store

from .const import TYPE_PS4, TYPE_PS5

_LOGGER = logging.getLogger(__name__)

BROADCAST_IP = '255.255.255.255'
UDP_IP = '0.0.0.0'
UDP_PORT = 0
DEFAULT_UDP_PORT = 9103

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


class DDPDevice():
    def __init__(self, host, protocol, direct=False):
        self._host = host
        self._direct = direct
        self._host_type = None
        self._callback = None
        self._protocol = None
        self._standby_start = 0
        self._poll_count = 0
        self._unreachable = False
        self._status = {}
        self._media_info = None
        self._image = None

    def set_unreachable(self, state: bool):
        self._unreachable = state

    def set_callback(self, callback):
        self._callback = callback

    def set_status(self, data):
        # Device won't respond to polls right after standby
        if self.polls_disabled:
            elapsed = time.time() - self._standby_start
            seconds = DEFAULT_STANDBY_DELAY - elapsed
            _LOGGER.debug("Polls disabled for %s seconds", round(seconds, 2))
            return

        # Track polls that were never returned.
        self._poll_count += 1

        # Assume Device is not available.
        if not data:
            if self.poll_count > self._protocol.max_polls:
                self._status = {}
                if self.callback:
                    self.callback()
            return

        if self.host_type is None:
            self._host_type = data.get("host-type")
        self.poll_count = 0
        self._unreachable = False
        old_status = self.status
        self._status = data
        if old_status != data:
            _LOGGER.debug("Status: %s", self.status)
            title_id = self.status.get("running-app-titleid")
            if title_id:
                asyncio.ensure_future(self.get_media_info(title_id))
            else:
                self._media_info = None
                self._image = None
            if not title_id and self.callback:
                self.callback()
            # Status changed from OK to Standby/Turned Off
            if old_status is not None and \
                    old_status.get('status_code') == STATUS_OK and \
                    self.status.get('status_code') == STATUS_STANDBY:
                self._standby_start = time.time()
                _LOGGER.debug(
                    "Status changed from OK to Standby."
                    "Disabling polls for %s seconds",
                    DEFAULT_STANDBY_DELAY)

    async def get_media_info(self, title_id):
        result = await async_search_ps_store(title_id, "United States")
        self._media_info = result
        if self._media_info.cover_art:
            await self.get_image(self.media_info.cover_art)
        if self.callback:
            self.callback()

    async def get_image(self, url):
        try:
            async with aiohttp.ClientSession() as session:
                response = await session.get(url, timeout=3)
                if response is not None:
                    self._image = await response.read()
        except (asyncio.TimeoutError, ContentTypeError, SSLError):
            pass

    @property
    def host(self):
        return self._host

    @property
    def host_type(self):
        return self._host_type

    @property
    def remote_port(self):
        return DDP_PORTS.get(self.host_type)

    @property
    def polls_disabled(self):
        """Return true if polls disabled."""
        elapsed = time.time() - self._standby_start
        if elapsed < DEFAULT_STANDBY_DELAY:
            return True
        self._standby_start = 0
        return False

    @property
    def unreachable(self):
        return self._unreachable

    @property
    def callback(self):
        return self._callback

    @property
    def status(self):
        return self._status

    @property
    def direct(self) -> bool:
        return self._direct

    @property
    def media_info(self):
        return self._media_info

    @property
    def image(self):
        return self._image
    

class DDPProtocol(asyncio.DatagramProtocol):
    """Async UDP Client."""

    def __init__(self, callback, max_polls=DEFAULT_POLL_COUNT):
        """Init Instance."""
        super().__init__()
        self._devices = {}
        self.max_polls = max_polls
        self._transport = None
        self._local_port = UDP_PORT
        self._default_callback = callback
        self._message = get_ddp_search_message()
        self._standby_start = 0
        self._event_stop = asyncio.Event()
        self._event_stop.clear()

    def __repr__(self):
        return (
            "<{}.{} local_port={} max_polls={}>".format(
                self.__module__,
                self.__class__.__name__,
                self.local_port,
                self.max_polls,
            )
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
        sock = self._transport.get_extra_info('socket')
        self._local_port = sock.getsockname()[1]
        _LOGGER.debug("DDP Transport created with port: %s", self.local_port)

    def send_msg(self, device=None, message=None):
        """Send Message."""
        sock = self._transport.get_extra_info('socket')
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
                sock.getsockname()[1], (host, port))
            self._transport.sendto(
                message.encode('utf-8'),
                (host, port))

    def datagram_received(self, data, addr):
        """When data is received."""
        if data is not None:
            sock = self._transport.get_extra_info('socket')
            _LOGGER.debug(
                "RECV MSG @ DDP Proto DPORT=%s SRC=%s",
                sock.getsockname()[1], addr)
            self._handle(data, addr)

    def _handle(self, data, addr):
        data = parse_ddp_response(data.decode('utf-8'))
        data['host-ip'] = addr[0]
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
        _LOGGER.debug(
            "Closing DDP Transport: Port=%s",
            self._local_port)

    def add_device(self, host, callback=None, direct=True):
        if host in self._devices:
            return None
        self._devices[host] = DDPDevice(host, self, direct)
        if callback is None:
            callback = self._default_callback
        self.add_callback(host, callback)
        return self._devices[host]

    def remove_device(self, host):
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
        self._event_stop.clear()

    @property
    def local_port(self):
        """Return local port."""
        return self._local_port

    @property
    def remote_ports(self):
        """Return remote ports."""
        return DDP_PORTS

    @property
    def devices(self):
        return self._devices
    
    @property
    def device_status(self):
        return [device.status for device in self._devices.values()]


async def async_create_ddp_endpoint(callback, sock=None, port=DEFAULT_UDP_PORT):
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
    transport, protocol = await loop.create_task(connect)
    return transport, protocol


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
        "user-credential": credential,
        "client-type": "vr",
        "auth-type": "R",
        "model": "w",
        "app-type": "r",
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


def search(host=BROADCAST_IP, port=UDP_PORT, host_type=None, sock=None, timeout=3) -> list:
    """Return list of discovered PS4s."""
    ps_list = []
    msg = get_ddp_search_message()
    start = time.time()

    if host is None:
        host = BROADCAST_IP
    if sock is None:
        sock = get_socket(port=port)
    _LOGGER.debug("Sending search message")
    host_types = [host_type] if host_type else DDP_PORTS.keys()
    for host_type in host_types:
        _send_msg(host, msg, host_type=host_type, sock=sock, close=False)
    while time.time() - start < timeout:
        data = addr = None
        try:
            response = _recv_msg(host, msg, sock=sock, close=False)
        except ConnectionResetError:
            continue
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


def get_status(host, port=UDP_PORT, host_type=None, sock=None):
    """Return status dict."""
    ps_list = search(host=host, port=port, host_type=host_type, sock=sock)
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
