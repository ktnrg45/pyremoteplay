import asyncio
import logging
import socket
import threading
import time
from base64 import b64decode, b64encode
from enum import IntEnum
from functools import partial
from struct import pack_into

import requests
from Cryptodome.Hash import SHA256
from Cryptodome.Random import get_random_bytes
from pyps4_2ndscreen.ddp import get_status, wakeup

from .av import AVFileReceiver, AVHandler
from .const import (FPS, OS_TYPE, RP_CRYPT_SIZE, RP_PORT, RP_VERSION, TYPE_PS4,
                    TYPE_PS5, USER_AGENT, Resolution)
from .crypt import RPCipher
from .errors import RemotePlayError, RPErrorHandler
from .feedback import Controller
from .keys import CTRL_KEY_0, CTRL_KEY_1
from .stream import RPStream
from .util import from_b, listener, log_bytes

_LOGGER = logging.getLogger(__name__)

RP_INIT_URL = "/sie/ps4/rp/sess/init"
RP_CTRL_URL = "/sie/ps4/rp/sess/ctrl"
REGIST_IP = "0.0.0.0"
BROADCAST_IP = "255.255.255.255"

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


def _get_ctrl_headers(host: str, auth: str, did: str, os_type: str, bitrate: str):
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
    _LOGGER.debug("CTRL Headers: %s", headers)
    return headers


def _get_rp_nonce(nonce: bytes) -> bytes:
    """Return RP nonce."""
    rp_nonce = bytearray(RP_CRYPT_SIZE)
    key = CTRL_KEY_0[((nonce[0] >> 3) * 112):]
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
    key = CTRL_KEY_1[((nonce[7] >> 3) * 112):]
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


class CTRL():
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
            f"<RP CTRL host={self._host} "
            f"resolution={self.resolution} "
            f"fps={self.fps}>"
        )

    def __init__(self, host: str, profile: dict, resolution="720p", fps="high", av_receiver=None):
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
        self._state = CTRL.STATE_INIT
        self._stream = None
        self.fps = FPS.preset(fps)
        self.resolution = Resolution.preset(resolution)
        self.error = ""
        self.controller = None
        self.av_receiver = av_receiver(self) if av_receiver is not None else None
        self.av_handler = AVHandler(self)

        self._stop_event = threading.Event()
        self.receiver_started = threading.Event()
        self.controller_ready_event = threading.Event()

        self._get_creds()
        self.controller_ready_event.clear()

    def _get_creds(self):
        creds = self._profile["id"]
        self._creds = SHA256.new(
            str(int.from_bytes(b64decode(creds), "little")).encode()
        ).hexdigest()

    def _init_attrs(self, mac_address):
        """Init Class attrs."""
        regist_data = self._profile["hosts"].get(mac_address)
        if not regist_data:
            return False
        self._regist_data = regist_data["data"]

        _regist_str = "".join(list(self._regist_data.keys()))
        if TYPE_PS4 in _regist_str:
            self._type = TYPE_PS4
        elif TYPE_PS5 in _regist_str:
            self._type = TYPE_PS5

        self._mac_address = self._regist_data[f"{self.type}-Mac"]
        self._name = self._regist_data[f"{self.type}-Nickname"]
        self._regist_key = self._regist_data[f"{self.type}-RegistKey"]
        self._rp_key = bytes.fromhex(self._regist_data["RP-Key"])
        return True

    def _init_ctrl(self) -> requests.models.Response:
        """Init Connect."""
        response = None
        headers = _get_headers(self.host, self._regist_key)
        url = f"http://{self.host}:{RP_PORT}{RP_INIT_URL}"
        response = requests.get(url, headers=headers, timeout=3)
        if response is None:
            _LOGGER.error("Timeout: Auth")
        return response

    def _parse_init(self, response: requests.models.Response) -> bytes:
        """Parse init response."""
        nonce = None
        _LOGGER.debug(response.headers)
        if response.status_code != 200:
            reason = response.headers.get("RP-Application-Reason")
            reason = int.from_bytes(bytes.fromhex(reason), "big")
            reason = RP_ERROR(reason)
            _LOGGER.error(
                "Failed to Init CTRL; Reason: %s",
                reason,
            )
            self.error = reason
        nonce = response.headers.get("RP-Nonce")
        if nonce is not None:
            nonce = b64decode(nonce.encode())
            log_bytes("Nonce", nonce)
        return nonce

    def _check_host(self) -> tuple:
        """Return True, True if host is available."""
        device = get_status(self._host)
        if not device:
            _LOGGER.error("Could not detect host at: %s", self._host)
            return (False, False, None)
        mac_address = device.get("host-id")
        if device.get("status_code") != 200:
            _LOGGER.info("Host: %s is not on", self._host)
            return (True, False, mac_address)
        return (True, True, mac_address)

    def _get_ctrl_headers(self, nonce: bytes) -> dict:
        """Return CTRL headers."""
        rp_nonce = _get_rp_nonce(nonce)
        aes_key = _get_aes_key(nonce, self._rp_key)
        self._cipher = RPCipher(aes_key, rp_nonce, counter=0)

        regist_key = b''.join([bytes.fromhex(self._regist_key), bytes(8)])
        auth = b64encode(self._cipher.encrypt(regist_key)).decode()
        did = b64encode(self._cipher.encrypt(_gen_did())).decode()
        os_type = b64encode(self._cipher.encrypt(OS_TYPE.encode().ljust(10, b'\x00'))).decode()
        bitrate = b64encode(self._cipher.encrypt(bytes(4))).decode()
        return _get_ctrl_headers(self.host, auth, did, os_type, bitrate)

    def _send_auth(self, headers: dict) -> bool:
        """Send CTRL Auth."""
        url = f"http://{self.host}:{RP_PORT}{RP_CTRL_URL}"
        response = None
        response = requests.get(url, headers=headers, stream=True, timeout=3)
        if response is None:
            _LOGGER.error("Timeout: Auth")
            return False
        _LOGGER.debug("CTRL Auth Headers: %s", response.headers)
        server_type = response.headers.get("RP-Server-Type")
        if response.status_code != 200 or server_type is None:
            return False
        self._server_type = from_b(
            self._cipher.decrypt(b64decode(server_type)), 'little')
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

        log_bytes("CTRL RECV Data", data)
        payload = data[8:]
        if payload:
            payload = self._cipher.decrypt(payload)
            log_bytes("CTRL PAYLOAD", payload)
        try:
            msg_type = CTRL.MessageType(data[5])
            _LOGGER.debug("RECV %s", CTRL.MessageType(msg_type).name)
        except ValueError:
            _LOGGER.debug("CTRL RECV invalid Message Type: %s", data[5])
            return
        if msg_type == CTRL.MessageType.HEARTBEAT_REQUEST:
            self._hb_last = time.time()
            self._send_hb_response()
        if msg_type == CTRL.MessageType.HEARTBEAT_RESPONSE:
            self._hb_last = time.time()
        elif msg_type == CTRL.MessageType.SESSION_ID:
            if self.session_id:
                _LOGGER.warning("RECV Session ID again")
                return
            session_id = payload[2:]
            log_bytes("Session ID", session_id)
            try:
                session_id.decode()
            except UnicodeDecodeError:
                _LOGGER.warning("CTRL RECV Malformed Session ID")
                session_id = invalid_session_id(session_id)
            finally:
                self._session_id = session_id
            self.start_stream()

        if time.time() - self._hb_last > 5:
            _LOGGER.info("CTRL HB Timeout. Sending HB")
            self._send_hb_request()

    def _build_msg(self, msg_type: int, payload=b'') -> bytes:
        """Return Message."""
        payload_size = len(payload)
        self._cipher.encrypt(payload)
        buf = bytearray(CTRL.HEADER_LENGTH + payload_size)
        pack_into(f"!IHxx{payload_size}s", buf, 0, payload_size, msg_type, payload)
        return bytes(buf)

    def _send_hb_response(self):
        msg = self._build_msg(CTRL.MessageType.HEARTBEAT_RESPONSE, HEARTBEAT_RESPONSE)
        self.send(msg)

    def _send_hb_request(self):
        msg = self._build_msg(CTRL.MessageType.HEARTBEAT_REQUEST)
        self.send(msg)

    def standby(self):
        """Set host to standby."""
        msg = self._build_msg(CTRL.MessageType.STANDBY)
        self.send(msg)
        _LOGGER.info("Sending Standby")
        self.stop()

    def send(self, data: bytes):
        """Send Data."""
        self._sock.send(data)
        log_bytes(f"CTRL Send", data)

    def wakeup(self):
        """Wakeup Host."""
        wakeup(self._host, self._creds)

    def start(self, wakeup=True) -> bool:
        """Start CTRL/RP Session."""
        status = self._check_host()
        if not status[0]:
            self.error = f"Host @ {self._host} is not reachable."
            return False
        if not status[1]:
            if wakeup:
                self.wakeup()
                _LOGGER.info("Sent Wakeup to host")
                self.error = "Host is in Standby. Attempting to wakeup."
            return False
        if not self._init_attrs(status[2]):
            self.error = "Profile is not registered with host"
            return False
        if not self.connect():
            _LOGGER.error("CTRL Auth Failed")
            return False
        _LOGGER.info("CTRL Auth Success")
        self._state = CTRL.STATE_READY
        self._worker = threading.Thread(
            target=listener,
            args=("CTRL", self._sock, self._handle, self._stop_event),
        )
        self._worker.start()
        return True

    def connect(self) -> bool:
        """Connect to Host."""
        response = self._init_ctrl()
        if response is None:
            return False
        nonce = self._parse_init(response)
        if nonce is None:
            return False
        headers = self._get_ctrl_headers(nonce)
        return self._send_auth(headers)

    def _cb_stop_test(self):
        """Stop test and get MTU and RTT and start stream."""
        mtu = self._stream.mtu
        rtt = self._stream.rtt
        _LOGGER.info("Tested network and got MTU: %s; RTT: %sms", mtu, rtt * 1000)
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
        self._stream = RPStream(self._host, stop_event, self, is_test=test, cb_stop=cb_stop, mtu=mtu, rtt=rtt)
        self._stream.connect()

    def stop(self):
        """Stop Stream."""
        if self.state == CTRL.STATE_STOP:
            _LOGGER.debug("CTRL already stopping")
            return
        self._stop_event.set()

    def init_controller(self):
        self.controller = Controller(self._stream, self._stop_event)
        self.controller.start()
        self.controller_ready_event.set()

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
        if self._stop_event.is_set():
            return CTRL.STATE_STOP
        return self._state

    @property
    def session_id(self) -> bytes:
        """Return Session ID."""
        return self._session_id


class CTRLAsync(CTRL):

    class Protocol(asyncio.Protocol):

        def __init__(self, ctrl):
            self.transport = None
            self.ctrl = ctrl

        def connection_made(self, transport):
            _LOGGER.debug("Connected")
            self.transport = transport

        def data_received(self, data):
            self.ctrl._handle(data)



    def __init__(self, host: str, profile: dict, resolution="720p", fps="high", av_receiver=None, loop=None):
        super().__init__(host, profile, resolution, fps, av_receiver)
        self.loop = asyncio.get_event_loop() if loop is None else loop
        self._protocol = None
        self._transport = None

        self._stop_event = asyncio.Event()
        self.receiver_started = asyncio.Event()
        self.controller_ready_event = asyncio.Event()

    async def run_io(self, func, *args, **kwargs):
        return await self.loop.run_in_executor(None, partial(func, *args, **kwargs))

    async def start(self, wakeup=True) -> bool:
        """Start CTRL/RP Session."""
        _LOGGER.debug("Running Async")
        status = await self.run_io(self._check_host)
        if not status[0]:
            self.error = f"Host @ {self._host} is not reachable."
            return False
        if not status[1]:
            if wakeup:
                await self.run_io(self.wakeup)
                _LOGGER.info("Sent Wakeup to host")
                self.error = "Host is in Standby. Attempting to wakeup."
            return False
        if not self._init_attrs(status[2]):
            self.error = "Profile is not registered with host"
            return False
        if not await self.run_io(self.connect):
            _LOGGER.error("CTRL Auth Failed")
            return False
        _LOGGER.info("CTRL Auth Success")
        self._state = CTRL.STATE_READY
        _, self._protocol = await self.loop.connect_accepted_socket(lambda: CTRLAsync.Protocol(self), self._sock)
        return True

    def send(self, data: bytes):
        """Send Data."""
        self._protocol.transport.write(data)
        log_bytes(f"CTRL Send", data)

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
            self.receiver_started.wait()
        self._stream = RPStream(self._host, stop_event, self, is_test=test, cb_stop=cb_stop, mtu=mtu, rtt=rtt)
        self.loop.create_task(self._stream.async_connect())

    def init_controller(self):
        self.controller = Controller(self._stream, self._stop_event)
        self.controller_ready_event.set()
