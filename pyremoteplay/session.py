import asyncio
import logging
import socket
import threading
import time
from base64 import b64decode, b64encode
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from enum import IntEnum
from functools import partial
from struct import pack_into

import requests
from Cryptodome.Random import get_random_bytes
from pyps4_2ndscreen.ddp import get_status

from .av import AVFileReceiver, AVHandler
from .const import (DDP_PORT, DDP_VERSION, FPS, OS_TYPE, RP_CRYPT_SIZE,
                    RP_PORT, RP_VERSION, TYPE_PS4, TYPE_PS5, USER_AGENT,
                    Resolution)
from .crypt import SessionCipher
from .errors import RemotePlayError, RPErrorHandler
from .feedback import Controller
from .keys import SESSION_KEY_0, SESSION_KEY_1
from .stream import RPStream
from .util import listener, log_bytes

_LOGGER = logging.getLogger(__name__)

RP_INIT_URL = "/sie/ps4/rp/sess/init"
RP_SESSION_URL = "/sie/ps4/rp/sess/ctrl"

DID_PREFIX = b'\x00\x18\x00\x00\x00\x07\x00\x40\x00\x80'

HEARTBEAT_RESPONSE = b"\x00\x00\x00\x00\x01\xfe\x00\x00"

RP_ERROR = RPErrorHandler()


def _get_headers(host: str, regist_key: str) -> dict:
    """Return headers."""
    headers = {
        "Host": f"{host}:{RP_PORT}",
        "User-Agent": USER_AGENT,
        "Connection": "close",
        "Content-Length": "0",
        "RP-Registkey": regist_key,
        "Rp-Version": RP_VERSION,
    }
    return headers


def _get_session_headers(host: str, auth: str, did: str, os_type: str, bitrate: str):
    """Return Connect Headers."""
    headers = {
        "Host": f"{host}:{RP_PORT}",
        "User-Agent": USER_AGENT,
        "Connection": "keep-alive",
        "Content-Length": "0",
        "RP-Auth": auth,
        "RP-Version": RP_VERSION,
        "RP-Did": did,
        "RP-ControllerType": "3",
        "RP-ClientType": "11",
        "RP-OSType": os_type,
        "RP-ConPath": "1",
        "RP-StartBitrate": bitrate,
    }
    _LOGGER.debug("Session Headers: %s", headers)
    return headers


def _get_rp_nonce(nonce: bytes) -> bytes:
    """Return RP nonce."""
    rp_nonce = bytearray(RP_CRYPT_SIZE)
    key = SESSION_KEY_0[((nonce[0] >> 3) * 112):]
    for index in range(0, RP_CRYPT_SIZE):
        shift = nonce[index] + 54 + index
        shift ^= key[index]
        rp_nonce[index] = shift % 256
    rp_nonce = bytes(rp_nonce)
    log_bytes("RP Nonce", rp_nonce)
    return rp_nonce


def _get_aes_key(nonce: bytes, rp_key: bytes) -> bytes:
    """Return AES key."""
    aes_key = bytearray(16)
    key = SESSION_KEY_1[((nonce[7] >> 3) * 112):]
    for index in range(0, RP_CRYPT_SIZE):
        shift = (key[index] ^ rp_key[index]) + 33 + index
        shift ^= nonce[index]
        aes_key[index] = shift % 256
    aes_key = bytes(aes_key)
    log_bytes("AES Key", aes_key)
    return aes_key


def _gen_did() -> bytes:
    """Generate Device ID."""
    did = b"".join([DID_PREFIX, get_random_bytes(16), bytes(6)])
    log_bytes("Device ID", did)
    return did


def get_wakeup_packet(regist_key: str) -> bytes:
    regist_key = int.from_bytes(bytes.fromhex(bytes.fromhex(regist_key).decode()), "big")

    data = (
        "WAKEUP * HTTP/1.1\n"
        "client-type:vr\n"
        "auth-type:R\n"
        "model:w\n"
        "app-type:r\n"
        f"user-credential:{regist_key}\n"
        f"device-discovery-protocol-version:{DDP_VERSION}\n"
    )
    return data.encode()


def send_wakeup(host: str, regist_key: str):
    """Send Wakeup Packet."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(0)
    sock.sendto(get_wakeup_packet(regist_key), (host, DDP_PORT))
    _LOGGER.info("Sent Wakeup to host")
    sock.close()


class Session():
    """Controller for RP Session."""
    STATE_INIT = "init"
    STATE_READY = "ready"
    STATE_STOP = "stop"
    HEADER_LENGTH = 8

    class MessageType(IntEnum):
        """Enum for Message Types."""
        LOGIN_PIN_REQUEST = 0x04
        LOGIN_PIN_RESPONSE = 0x8004
        LOGIN = 0x05
        SESSION_ID = 0x33
        HEARTBEAT_REQUEST = 0xfe
        HEARTBEAT_RESPONSE = 0x1fe
        STANDBY = 0x50
        KEYBOARD_ENABLE_TOGGLE = 0x20
        KEYBOARD_OPEN = 0x21
        KEYBOARD_CLOSE_REMOTE = 0x22
        KEYBOARD_TEXT_CHANGE_REQ = 0x23
        KEYBOARD_TEXT_CHANGE_RES = 0x24
        KEYBOARD_CLOSE_REQ = 0x25

    def __repr__(self):
        return (
            f"<RP Session host={self._host} "
            f"resolution={self.resolution} "
            f"fps={self.fps}>"
        )

    def __init__(self, host: str, profile: dict, resolution="720p", fps="high", av_receiver=None, use_hw=False, quality="default", **kwargs):
        self._host = host
        self._profile = profile
        self._regist_data = {}
        self._session_id = b''
        self._type = ""
        self._mac_address = ""
        self._name = ""
        self._creds = ""
        self._regist_key = None
        self._rp_key = None
        self._sock = None
        self._hb_last = 0
        self._cipher = None
        self._state = Session.STATE_INIT
        self._stream = None
        self._kwargs = kwargs
        self.quality = quality
        self.use_hw = use_hw
        self.max_width = self.max_height = None
        self.fps = FPS.preset(fps)
        self.resolution = Resolution.preset(resolution)
        self.error = ""
        self.av_receiver = av_receiver(self) if av_receiver is not None else None
        self.av_handler = AVHandler(self)
        self.controller = Controller(self)

        self._ready_event = None
        self._stop_event = None
        self.receiver_started = None

    def _init_profile_kwargs(self, device: dict) -> bool:
        """Return True if can init profile from kwargs."""
        if not self._kwargs:
            return False
        self._regist_key = self._kwargs.get("regist_key")
        try:
            self._rp_key = bytes.fromhex(self._keargs.get("rp_key"))
        except ValueError:
            _LOGGER.error("Invalid RP Key")
            return False
        if not self._regist_key or not self._rp_key:
            return False
        self._type = device.get("host-type")
        self._mac_address = device.get("host-id")
        self._name = device.get("host-name")
        return True

    def _init_profile(self, device: dict) -> bool:
        """Return True if Init profile."""
        self._mac_address = device.get("host-id")
        regist_data = self._profile["hosts"].get(self._mac_address)
        if not regist_data:
            return False
        self._regist_data = regist_data["data"]
        self._type = device.get("host-type")
        self._name = device.get("host-name")
        self._regist_key = self._regist_data["RegistKey"]
        self._rp_key = bytes.fromhex(self._regist_data["RP-Key"])
        return True

    def _get_status(self) -> dict:
        """Return dict of device status."""
        return get_status(self._host)

    def _check_host(self) -> tuple:
        """Return True, True if host is available."""
        device = self._get_status()
        if not device:
            _LOGGER.error("Could not detect host at: %s", self._host)
            return (False, False, None)
        if device.get("status_code") != 200:
            _LOGGER.info("Host: %s is not on", self._host)
            return (True, False, device)
        return (True, True, device)

    def _get_rp_url(self, request_type: str) -> str:
        valid_types = ["init", "session"]
        if request_type not in valid_types:
            raise ValueError("Unknown ")
        url_slug = RP_INIT_URL if request_type == "init" else RP_SESSION_URL
        url = f"http://{self.host}:{RP_PORT}{url_slug}"
        return url

    def _send_auth_request(self, request_type: str, headers: dict, stream: bool) -> requests.models.Response:
        """Return response. Send Auth Request."""
        response = None
        url = self._get_rp_url(request_type)
        response = requests.get(url, headers=headers, stream=stream, timeout=3)
        if response is None:
            _LOGGER.error("Timeout: Auth")
        return response

    def _parse_init(self, response: requests.models.Response) -> bytes:
        """Return nonce. Parse init response."""
        nonce = None
        _LOGGER.debug(response.headers)
        if response.status_code != 200:
            reason = response.headers.get("RP-Application-Reason")
            reason = int.from_bytes(bytes.fromhex(reason), "big")
            reason = RP_ERROR(reason)
            _LOGGER.error(
                "Failed to Init Session; Reason: %s",
                reason,
            )
            self.error = reason
        nonce = response.headers.get("RP-Nonce")
        if nonce is not None:
            nonce = b64decode(nonce.encode())
            log_bytes("Nonce", nonce)
        return nonce

    def _get_session_headers(self, nonce: bytes) -> dict:
        """Return Session headers."""
        rp_nonce = _get_rp_nonce(nonce)
        aes_key = _get_aes_key(nonce, self._rp_key)
        self._cipher = SessionCipher(aes_key, rp_nonce, counter=0)

        regist_key = b''.join([bytes.fromhex(self._regist_key), bytes(8)])
        auth = b64encode(self._cipher.encrypt(regist_key)).decode()
        did = b64encode(self._cipher.encrypt(_gen_did())).decode()
        os_type = b64encode(self._cipher.encrypt(OS_TYPE.encode().ljust(10, b'\x00'))).decode()
        bitrate = b64encode(self._cipher.encrypt(bytes(4))).decode()
        return _get_session_headers(self.host, auth, did, os_type, bitrate)

    def _authenticate(self, nonce: bytes) -> bool:
        """Return True if successful. Send Session Auth."""
        headers = self._get_session_headers(nonce)
        response = self._send_auth_request("session", headers, stream=True)
        if response is None:
            return False
        _LOGGER.debug("Session Auth Headers: %s", response.headers)
        server_type = response.headers.get("RP-Server-Type")
        if response.status_code != 200 or server_type is None:
            return False
        self._server_type = int.from_bytes(self._cipher.decrypt(b64decode(server_type)), 'little')
        _LOGGER.debug("Server Type: %s", self._server_type)
        self._sock = socket.fromfd(
            response.raw.fileno(), socket.AF_INET, socket.SOCK_STREAM)
        return True

    def _handle(self, data: bytes):
        """Handle Data."""
        def invalid_session_id(session_id: bytes) -> bytes:
            """Return a valid session id.

            Expecting a session id that is utf-8 but sometimes will receive
            a session id with some utf-16 bytes mixed in.
            Converts the utf-16 bytes to utf-8.

            """
            new_id = []
            session_id = bytearray(session_id)
            for char in session_id:
                new_id.append(chr(char).encode())
            new_id = b''.join(new_id)
            log_bytes("New Session ID", new_id)
            return new_id

        log_bytes("Session RECV Data", data)
        payload = data[8:]
        if payload:
            payload = self._cipher.decrypt(payload)
            log_bytes("Session PAYLOAD", payload)
        try:
            msg_type = Session.MessageType(data[5])
            _LOGGER.debug("RECV %s", Session.MessageType(msg_type).name)
        except ValueError:
            _LOGGER.debug("Session RECV invalid Message Type: %s", data[5])
            return
        if msg_type == Session.MessageType.HEARTBEAT_REQUEST:
            self._hb_last = time.time()
            self._send_hb_response()
        if msg_type == Session.MessageType.HEARTBEAT_RESPONSE:
            self._hb_last = time.time()
        elif msg_type == Session.MessageType.SESSION_ID:
            if self.session_id:
                _LOGGER.warning("RECV Session ID again")
                return
            session_id = payload[2:]
            log_bytes("Session ID", session_id)
            try:
                session_id.decode()
            except UnicodeDecodeError:
                _LOGGER.warning("Session RECV Malformed Session ID")
                session_id = invalid_session_id(session_id)
            finally:
                self._session_id = session_id
            self._ready_event.set()

        if time.time() - self._hb_last > 5:
            _LOGGER.info("Session HB Timeout. Sending HB")
            self._send_hb_request()

    def _build_msg(self, msg_type: int, payload=b'') -> bytes:
        """Return Message."""
        payload_size = len(payload)
        self._cipher.encrypt(payload)
        buf = bytearray(Session.HEADER_LENGTH + payload_size)
        pack_into(f"!IHxx{payload_size}s", buf, 0, payload_size, msg_type, payload)
        return bytes(buf)

    def _send_hb_response(self):
        msg = self._build_msg(Session.MessageType.HEARTBEAT_RESPONSE, HEARTBEAT_RESPONSE)
        self.send(msg)

    def _send_hb_request(self):
        msg = self._build_msg(Session.MessageType.HEARTBEAT_REQUEST)
        self.send(msg)

    def standby(self):
        """Set host to standby."""
        msg = self._build_msg(Session.MessageType.STANDBY)
        self.send(msg)
        _LOGGER.info("Sending Standby")
        self.stop()

    def send(self, data: bytes):
        """Send Data."""
        self._sock.send(data)
        log_bytes("Session Send", data)

    def wakeup(self):
        """Wakeup Host."""
        send_wakeup(self._host, self._regist_key)

    def start(self, wakeup=True, autostart=True) -> bool:
        """Start Session/RP Session."""
        self._ready_event = threading.Event()
        self._stop_event = threading.Event()
        self.receiver_started = threading.Event()
        status = self._check_host()
        if not status[0]:
            self.error = f"Host @ {self._host} is not reachable."
            return False
        if not self._init_profile(status[2]):
            self.error = "Profile is not registered with host"
            if not self._init_profile_kwargs(status[2]):
                return False
        if not status[1]:
            if wakeup:
                self.wakeup()
                self.error = "Host is in Standby. Attempting to wakeup."
            return False
        if not self.connect():
            _LOGGER.error("Session Auth Failed")
            return False
        _LOGGER.info("Session Auth Success")
        self._state = Session.STATE_READY
        self._worker = threading.Thread(
            target=listener,
            args=("Session", self._sock, self._handle, self._stop_event),
        )
        self._worker.start()
        self._ready_event.wait()
        if autostart:
            self.start_stream()
        return True

    def connect(self) -> bool:
        """Connect to Host."""
        headers = _get_headers(self.host, self._regist_key)
        response = self._send_auth_request("init", headers, stream=False)
        if response is None:
            return False
        nonce = self._parse_init(response)
        if nonce is None:
            return False
        return self._authenticate(nonce)

    def _cb_stop_test(self):
        """Stop test and get MTU and RTT and start stream."""
        mtu = self._stream.mtu
        rtt = self._stream.rtt
        _LOGGER.info("Using MTU: %s; RTT: %sms", mtu, rtt * 1000)
        self._stream = None
        self.start_stream(test=False, mtu=mtu, rtt=rtt)

    def start_stream(self, test=True, mtu=None, rtt=None):
        """Start Stream."""
        if not self.session_id:
            _LOGGER.error("Session ID not received")
            return
        stop_event = self._stop_event if not test else threading.Event()
        cb_stop = self._cb_stop_test if test else None
        if not test and self.av_receiver:
            self.av_handler.add_receiver(self.av_receiver)
            _LOGGER.info("Waiting for Receiver...")
            self.receiver_started.wait()
        self._stream = RPStream(self, stop_event, is_test=test, cb_stop=cb_stop, mtu=mtu, rtt=rtt)
        self._stream.connect()

    def stop(self):
        """Stop Stream."""
        if self.state == Session.STATE_STOP:
            _LOGGER.debug("Session already stopping")
            return
        _LOGGER.info("Session Received Stop Signal")
        self._stop_event.set()

    def init_controller(self):
        self.controller.start()

    @property
    def host(self) -> str:
        """Return host address."""
        return self._host

    @property
    def type(self) -> str:
        """Return host type."""
        return self._type

    @property
    def name(self) -> str:
        """Return host name."""
        return self._name

    @property
    def mac_address(self) -> str:
        """Return host MAC Adddress."""
        return self._mac_address

    @property
    def state(self) -> str:
        """Return State."""
        if self._stop_event is None:
            return self._state
        if self._stop_event.is_set():
            return Session.STATE_STOP
        return self._state

    @property
    def is_running(self) -> bool:
        """Return True if running."""
        return self.state != Session.STATE_STOP

    @property
    def is_stopped(self) -> bool:
        """Return True if stopped."""
        return self.state == Session.STATE_STOP

    @property
    def session_id(self) -> bytes:
        """Return Session ID."""
        return self._session_id

    @property
    def video_format(self):
        formats = {
            "ps4": "h264",
            "ps5": "h265",
        }
        return formats.get(self.type.lower())
    

class SessionAsync(Session):

    class Protocol(asyncio.Protocol):

        def __init__(self, session):
            self.transport = None
            self.session = session

        def connection_made(self, transport):
            _LOGGER.debug("Connected")
            self.transport = transport

        def data_received(self, data):
            self.session._handle(data)

        def close(self):
            self.transport.close()

    def __init__(self, host: str, profile: dict, resolution="720p", fps="high", av_receiver=None, use_hw=False, quality="default", loop=None, **kwargs):
        super().__init__(host, profile, resolution, fps, av_receiver, use_hw, quality, **kwargs)
        self.loop = asyncio.get_event_loop() if loop is None else loop
        self._protocol = None
        self._transport = None
        self._tasks = []
        self._thread_executor = ThreadPoolExecutor()

    async def run_io(self, func, *args, **kwargs):
        return await self.loop.run_in_executor(self._thread_executor, partial(func, *args, **kwargs))

    def sync_run_io(self, func, *args, **kwargs):
        asyncio.ensure_future(self.run_io(func, *args, **kwargs))

    async def start(self, wakeup=True, autostart=True) -> bool:
        """Start Session/RP Session."""
        _LOGGER.info("Session Started")
        self._ready_event = asyncio.Event()
        self._stop_event = asyncio.Event()
        self.receiver_started = asyncio.Event()

        _LOGGER.debug("Running Async")
        status = await self.run_io(self._check_host)
        if not status[0]:
            self.error = f"Host @ {self._host} is not reachable."
            return False
        if not self._init_profile(status[2]):
            self.error = "Profile is not registered with host"
            return False
        if not status[1]:
            if wakeup:
                await self.run_io(self.wakeup)
                self.error = "Host is in Standby. Attempting to wakeup."
            return False
        if not await self.run_io(self.connect):
            _LOGGER.error("Session Auth Failed")
            return False
        _LOGGER.info("Session Auth Success")
        self._state = Session.STATE_READY
        _, self._protocol = await self.loop.connect_accepted_socket(lambda: SessionAsync.Protocol(self), self._sock)
        await self._ready_event.wait()
        if autostart:
            self.start_stream()
        return True

    def send(self, data: bytes):
        """Send Data."""
        self._protocol.transport.write(data)
        log_bytes(f"Session Send", data)

    def start_stream(self, test=True, mtu=None, rtt=None):
        """Start Stream."""
        if not self.session_id:
            _LOGGER.error("Session ID not received")
            return
        stop_event = self._stop_event if not test else asyncio.Event()
        cb_stop = self._cb_stop_test if test else None
        if not test and self.av_receiver:
            self.av_handler.add_receiver(self.av_receiver)
            _LOGGER.info("Waiting for Receiver...")
            self.loop.create_task(self.receiver_started.wait())
        self._stream = RPStream(self, stop_event, is_test=test, cb_stop=cb_stop, mtu=mtu, rtt=rtt)
        self.loop.create_task(self._stream.async_connect())
        if test:
            self.loop.create_task(self.wait_for_test(stop_event))
        else:
            self._tasks.append(self.loop.create_task(self.run_io(self.controller.worker)))

    async def wait_for_test(self, stop_event):
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=3.0)
        except asyncio.exceptions.TimeoutError:
            _LOGGER.warning("Network Test timed out. Using Default MTU and RTT")
            self._stream.stop()

    def init_av_handler(self):
        self._tasks.append(self.loop.create_task(self.run_io(self.av_handler.worker)))

    def stop(self):
        """Stop Stream."""
        if self.state == Session.STATE_STOP:
            _LOGGER.debug("Session already stopping")
            return
        _LOGGER.info("Session Received Stop Signal")
        if self._stream:
            self._stream.stop()
        self._stop_event.set()
        if self._tasks:
            for task in self._tasks:
                task.cancel()
        self._thread_executor.shutdown()
        if self._protocol:
            self._protocol.close()
