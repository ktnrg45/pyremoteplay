"""Remote Play Session."""
from __future__ import annotations
import asyncio
import logging
import socket
import time
from typing import Union
from base64 import b64decode, b64encode
from concurrent.futures import ThreadPoolExecutor
from enum import IntEnum
from functools import partial
from struct import pack_into

import requests
from Cryptodome.Random import get_random_bytes
from pyee import ExecutorEventEmitter

from pyremoteplay.receiver import AVReceiver
from .const import (
    FPS,
    OS_TYPE,
    RP_CRYPT_SIZE,
    RP_PORT,
    RP_VERSION_PS4,
    RP_VERSION_PS5,
    TYPE_PS4,
    TYPE_PS5,
    USER_AGENT,
    Resolution,
    StreamType,
    Quality,
)
from .crypt import SessionCipher
from .ddp import async_get_status, async_wakeup
from .errors import RemotePlayError, RPErrorHandler
from .keys import (
    SESSION_KEY_0_PS4,
    SESSION_KEY_1_PS4,
    SESSION_KEY_0_PS5,
    SESSION_KEY_1_PS5,
)
from .stream import RPStream
from .util import format_regist_key

_LOGGER = logging.getLogger(__name__)

RP_INIT_URL = "/sie/{}/rp/sess/init"
RP_SESSION_URL = "/sie/{}/rp/sess/ctrl"


DID_PREFIX = b"\x00\x18\x00\x00\x00\x07\x00\x40\x00\x80"

HEARTBEAT_RESPONSE = b"\x00\x00\x00\x00\x01\xfe\x00\x00"

RP_ERROR = RPErrorHandler()

HOST_TYPES = {
    TYPE_PS4: {
        "keys": (SESSION_KEY_0_PS4, SESSION_KEY_1_PS4),
        "version": RP_VERSION_PS4,
    },
    TYPE_PS5: {
        "keys": (SESSION_KEY_0_PS5, SESSION_KEY_1_PS5),
        "version": RP_VERSION_PS5,
    },
}


def _get_headers(host_type: str, host: str, regist_key: str) -> dict:
    """Return headers."""
    headers = {
        "Host": f"{host}:{RP_PORT}",
        "User-Agent": USER_AGENT,
        "Connection": "close",
        "Content-Length": "0",
        "RP-Registkey": regist_key,
        "Rp-Version": HOST_TYPES[host_type]["version"],
    }
    return headers


def _get_session_headers(
    host_type: str,
    host: str,
    auth: str,
    did: str,
    os_type: str,
    bitrate: str,
    stream_type: str,
):
    """Return Connect Headers."""
    headers = {
        "Host": f"{host}:{RP_PORT}",
        "User-Agent": USER_AGENT,
        "Connection": "keep-alive",
        "Content-Length": "0",
        "RP-Auth": auth,
        "RP-Version": HOST_TYPES[host_type]["version"],
        "RP-Did": did,
        "RP-ControllerType": "3",
        "RP-ClientType": "11",
        "RP-OSType": os_type,
        "RP-ConPath": "1",
        "RP-StartBitrate": bitrate,
    }
    if host_type == TYPE_PS5:
        headers["RP-StreamingType"] = stream_type
    return headers


def _get_stream_type(stream_type: StreamType) -> bytes:
    """Return Stream Type."""
    stream_type = int(stream_type)
    return bytes(
        bytearray(
            [
                stream_type & 255,
                (stream_type >> 8) & 255,
                (stream_type >> 16) & 255,
                (stream_type >> 24) & 255,
            ]
        )
    )


def _get_rp_nonce(host_type: str, nonce: bytes) -> bytes:
    """Return RP nonce."""
    session_key = HOST_TYPES[host_type]["keys"][0]
    rp_nonce = bytearray(RP_CRYPT_SIZE)
    key = session_key[((nonce[0] >> 3) * 112) :]
    for index in range(0, RP_CRYPT_SIZE):
        if host_type == TYPE_PS5:
            shift = nonce[index] - 45 - index
        else:
            shift = nonce[index] + 54 + index
        shift ^= key[index]
        rp_nonce[index] = shift % 256
    rp_nonce = bytes(rp_nonce)
    # log_bytes("RP Nonce", rp_nonce)
    return rp_nonce


def _get_aes_key(host_type: str, nonce: bytes, rp_key: bytes) -> bytes:
    """Return AES key."""
    aes_key = bytearray(16)
    session_key = HOST_TYPES[host_type]["keys"][1]
    key = session_key[((nonce[7] >> 3) * 112) :]
    for index in range(0, RP_CRYPT_SIZE):
        if host_type == TYPE_PS5:
            shift = rp_key[index] + 24 + index
            shift ^= nonce[index]
            shift ^= key[index]
        else:
            shift = (key[index] ^ rp_key[index]) + 33 + index
            shift ^= nonce[index]
        aes_key[index] = shift % 256
    aes_key = bytes(aes_key)
    # log_bytes("AES Key", aes_key)
    return aes_key


def _gen_did() -> bytes:
    """Generate Device ID."""
    did = b"".join([DID_PREFIX, get_random_bytes(16), bytes(6)])
    # log_bytes("Device ID", did)
    return did


class Session:
    """Remote Play Session Async.

    :param host: IP Address of Remote Play Host
    :param profile: Profile data to connect with. From registering
    :param loop: A running asyncio event loop. If None, loop will be the current running loop
    :param receiver: A receiver for handling video and audio frames
    :param resolution: The resolution of video stream.
        Name of or value of or `Resolution` enum
    :param fps: Frames per second for video stream.
        Name of or value of or `FPS` enum
    :param quality: Quality of video stream. Name of or value of or `Quality` enum
    :param codec: Name of FFMPEG video codec to use. i.e. 'h264', 'h264_cuvid'.
        Video codec should be 'h264' or 'hevc'. PS4 hosts will always use h264.
    :param hdr: Uses HDR if True. Has no effect if codec is 'h264'
    """

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
        HEARTBEAT_REQUEST = 0xFE
        HEARTBEAT_RESPONSE = 0x1FE
        STANDBY = 0x50
        KEYBOARD_ENABLE_TOGGLE = 0x20
        KEYBOARD_OPEN = 0x21
        KEYBOARD_CLOSE_REMOTE = 0x22
        KEYBOARD_TEXT_CHANGE_REQ = 0x23
        KEYBOARD_TEXT_CHANGE_RES = 0x24
        KEYBOARD_CLOSE_REQ = 0x25

    class Protocol(asyncio.Protocol):
        """Protocol for session."""

        def __init__(self, session: Session):
            self._transport = None
            self._session = session

        def connection_made(self, transport: asyncio.BaseTransport):
            """Callback for connection made."""
            _LOGGER.debug("Connected")
            self._transport = transport

        def data_received(self, data: bytes):
            """Callback for data received."""
            # pylint: disable=protected-access
            self._session._handle(data)

        def close(self):
            """Close Transport."""
            self._transport.close()

        @property
        def transport(self) -> asyncio.BaseTransport:
            """Return Transport."""
            return self._transport

    def __repr__(self):
        return (
            f"<RP Session host={self._host} "
            f"resolution={self.resolution} "
            f"fps={self.fps}>"
        )

    def __del__(self):
        self.stop()

    def __init__(
        self,
        host: str,
        profile: dict,
        loop: asyncio.AbstractEventLoop = None,
        receiver: AVReceiver = None,
        resolution: Union[Resolution, str, int] = "360p",
        fps: Union[FPS, str, int] = "low",
        quality: Union[Quality, str, int] = "very_low",
        codec: str = "h264",
        hdr: bool = False,
    ):
        self.error = ""
        self._host = host
        self._profile = profile
        self._regist_data = {}
        self._session_id = b""
        self._server_type = None
        self._type = ""
        self._regist_key = None
        self._rp_key = None
        self._sock = None
        self._hb_last = 0
        self._cipher = None
        self._state = Session.STATE_INIT
        self._stream = None
        self._receiver = None
        self._events = ExecutorEventEmitter()
        self._loop = loop
        self._protocol = None
        self._transport = None
        self._tasks = []
        self._thread_executor = ThreadPoolExecutor()

        self._ready_event = None
        self._stop_event = None
        self._stream_ready_event = None

        self._quality = Quality.parse(quality)
        self._fps = FPS.parse(fps)
        self._resolution = Resolution.parse(resolution)

        if not codec:
            codec = "h264"
        self._codec = codec.lower()

        stream_type = codec.split("_")[0]
        if hdr and not codec.startswith("h264"):
            stream_type = f"{stream_type}_hdr"
        self._stream_type = StreamType.parse(stream_type)

        if self._codec.split("_")[0].upper() not in self._stream_type.name:
            raise ValueError(
                f"Codec: {self._codec} does not seem to match stream type: {self._stream_type.name}"
            )
        self.set_receiver(receiver)

    def _init_profile(self, status: dict) -> bool:
        """Return True if Init profile."""
        mac_address = status.get("host-id")
        regist_data = self._profile["hosts"].get(mac_address)
        if not regist_data:
            return False
        self._regist_data = regist_data["data"]
        self._type = status.get("host-type")
        self._regist_key = self._regist_data["RegistKey"]
        self._rp_key = bytes.fromhex(self._regist_data["RP-Key"])
        return True

    async def _check_host(self) -> tuple:
        """Return True, True if host is available."""
        device = await async_get_status(self._host, host_type=self.type)
        if not device:
            _LOGGER.error("Could not detect host at: %s", self._host)
            return (False, False, None)
        if device.get("status-code") != 200:
            _LOGGER.info("Host: %s is not on", self._host)
            return (True, False, device)
        return (True, True, device)

    def _get_rp_url(self, request_type: str) -> str:
        valid_types = ["init", "session"]
        if request_type not in valid_types:
            raise RemotePlayError("Unknown request type")
        url_slug = RP_INIT_URL if request_type == "init" else RP_SESSION_URL
        url_slug = url_slug.format(self.type.lower())
        url = f"http://{self.host}:{RP_PORT}{url_slug}"
        return url

    def _send_auth_request(
        self, request_type: str, headers: dict, stream: bool
    ) -> requests.models.Response:
        """Return response. Send Auth Request."""
        response = None
        url = self._get_rp_url(request_type)
        # Using requests as it is easier to get the socket later.
        response = requests.get(url, headers=headers, stream=stream, timeout=3)
        if response is None:
            _LOGGER.error("Timeout: Auth")
        return response

    def _parse_init(self, response: requests.models.Response) -> bytes:
        """Return nonce. Parse init response."""
        nonce = None
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
            # log_bytes("Nonce", nonce)
        return nonce

    def _get_session_headers(self, nonce: bytes) -> dict:
        """Return Session headers."""
        stream_type = _get_stream_type(self.stream_type)
        rp_nonce = _get_rp_nonce(self.type, nonce)
        aes_key = _get_aes_key(self.type, nonce, self._rp_key)
        self._cipher = SessionCipher(self.type, aes_key, rp_nonce, counter=0)

        regist_key = b"".join([bytes.fromhex(self._regist_key), bytes(8)])
        auth = b64encode(self._cipher.encrypt(regist_key)).decode()
        did = b64encode(self._cipher.encrypt(_gen_did())).decode()
        os_type = b64encode(
            self._cipher.encrypt(OS_TYPE.encode().ljust(10, b"\x00"))
        ).decode()
        bitrate = b64encode(self._cipher.encrypt(bytes(4))).decode()
        stream_type = b64encode(self._cipher.encrypt(stream_type)).decode()
        return _get_session_headers(
            self.type, self.host, auth, did, os_type, bitrate, stream_type
        )

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
        self._server_type = int.from_bytes(
            self._cipher.decrypt(b64decode(server_type)), "little"
        )
        _LOGGER.debug("Server Type: %s", self._server_type)
        self._sock = socket.fromfd(
            response.raw.fileno(), socket.AF_INET, socket.SOCK_STREAM
        )
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
            new_id = b"".join(new_id)
            # log_bytes("New Session ID", new_id)
            return new_id

        # log_bytes("Session RECV Data", data)
        payload = data[8:]
        if payload:
            payload = self._cipher.decrypt(payload)
            # log_bytes("Session PAYLOAD", payload)
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
            # log_bytes("Session ID", session_id)
            try:
                session_id.decode()
            except UnicodeDecodeError:
                _LOGGER.warning("Session RECV Malformed Session ID")
                session_id = invalid_session_id(session_id)
            finally:
                self._session_id = session_id
            self._ready_event.set()

        if time.time() - self._hb_last > 5:
            _LOGGER.debug("Session HB Timeout. Sending HB")
            self._send_hb_request()

    def _build_msg(self, msg_type: int, payload=b"") -> bytes:
        """Return Message."""
        payload_size = len(payload)
        self._cipher.encrypt(payload)
        buf = bytearray(Session.HEADER_LENGTH + payload_size)
        pack_into(f"!IHxx{payload_size}s", buf, 0, payload_size, msg_type, payload)
        return bytes(buf)

    def _send_hb_response(self):
        msg = self._build_msg(
            Session.MessageType.HEARTBEAT_RESPONSE, HEARTBEAT_RESPONSE
        )
        self._send(msg)

    def _send_hb_request(self):
        msg = self._build_msg(Session.MessageType.HEARTBEAT_REQUEST)
        self._send(msg)

    def _encrypt(self, data: bytes, counter: int = None):
        """Return Encypted Data."""
        if not self._cipher:
            raise RemotePlayError("Session cipher not created")
        return self._cipher.encrypt(data, counter=counter)

    def _send(self, data: bytes):
        """Send Data."""
        self._protocol.transport.write(data)
        # log_bytes("Session Send", data)

    def _connect(self) -> bool:
        """Connect to Host."""
        headers = _get_headers(self.type, self.host, self._regist_key)
        response = self._send_auth_request("init", headers, stream=False)
        if response is None:
            _LOGGER.error("No response for Init")
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
        self._start_stream(test=False, mtu=mtu, rtt=rtt)

    def _start_stream(self, test=True, mtu=None, rtt=None):
        """Start Stream."""
        if not self.session_id:
            _LOGGER.error("Session ID not received")
            return
        stop_event = self._stop_event if not test else asyncio.Event()
        cb_stop = self._cb_stop_test if test else None
        self._stream = RPStream(
            self, stop_event, is_test=test, cb_stop=cb_stop, mtu=mtu, rtt=rtt
        )
        if not test and self.receiver:
            self._stream.add_receiver(self.receiver)
        self.loop.create_task(self._stream.async_connect())
        if test:
            self.loop.create_task(self._wait_for_test(stop_event))

    async def _wait_for_test(self, stop_event):
        """Wait for network test to complete. Uses defaults if timed out."""
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=3.0)
        except asyncio.exceptions.TimeoutError:
            _LOGGER.warning("Network Test timed out. Using Default MTU and RTT")
            self._stream.stop()

    def _init_av_handler(self):
        """Run AV Handler."""
        self._tasks.append(self.loop.create_task(self._run_io(self._stream.run_av)))

    def _sync_run_io(self, func, *args, **kwargs):
        """Run blocking function in executor. Called from sync method."""
        asyncio.ensure_future(self._run_io(func, *args, **kwargs))

    async def _run_io(self, func, *args, **kwargs):
        """Run blocking function in executor."""
        if not self._thread_executor:
            if not self.is_stopped:
                _LOGGER.warning("No Executor and session is not stopped")
            return None
        return await self.loop.run_in_executor(
            self._thread_executor, partial(func, *args, **kwargs)
        )

    def standby(self):
        """Set host to standby."""

        msg = self._build_msg(Session.MessageType.STANDBY)
        self._send(msg)
        _LOGGER.info("Sending Standby")
        self.stop()

    async def wakeup(self):
        """Wakeup Host."""
        regist_key = format_regist_key(self._regist_key)
        await async_wakeup(self.host, regist_key, host_type=self.type)

    async def start(self, wakeup=True, autostart=True) -> bool:
        """Start Session/RP Session."""
        _LOGGER.info("Session Started")
        self._ready_event = asyncio.Event()
        self._stop_event = asyncio.Event()
        self._stream_ready_event = asyncio.Event()

        self.events.on("av_ready", self._init_av_handler)

        if not self.loop:
            self._loop = asyncio.get_running_loop()
        status = await self._check_host()
        if not status[0]:
            self.error = f"Host @ {self._host} is not reachable."
            return False
        if not self._init_profile(status[2]):
            self.error = "Profile is not registered with host"
            return False
        if not status[1]:
            if wakeup:
                await self.wakeup()
                self.error = "Host is in Standby. Attempting to wakeup."
            return False
        if not await self._run_io(self._connect):
            _LOGGER.error("Session Auth Failed")
            if not self.error:
                self.error = "Auth Failed."
            return False
        _LOGGER.info("Session Auth Success")
        self._state = Session.STATE_READY
        _, self._protocol = await self.loop.connect_accepted_socket(
            lambda: Session.Protocol(self), self._sock
        )
        await self._ready_event.wait()
        if autostart:
            self._start_stream()
        return True

    def stop(self):
        """Stop Session."""
        if self.state == Session.STATE_STOP:
            _LOGGER.debug("Session already stopping")
            return
        _LOGGER.debug("Session Received Stop Signal")
        if self._stream:
            self._stream.stop()
        if self._stop_event:
            self._stop_event.set()
        if self._tasks:
            for task in self._tasks:
                task.cancel()
        if self._thread_executor:
            self._thread_executor.shutdown()
        if self._protocol:
            self._protocol.close()
        if self.events:
            self.events.emit("stop")
            self.events.remove_all_listeners()

        self._tasks = []
        self._stream = None
        self._thread_executor = None
        self._protocol = None
        self._events = None

    def set_receiver(self, receiver: AVReceiver):
        """Set AV Receiver. Should be set before starting session."""
        cls = AVReceiver
        if receiver is None:
            return
        if not isinstance(receiver, AVReceiver):
            raise ValueError(f"Receiver must be a subclass of {cls}")
        if receiver.__class__ == AVReceiver:
            raise ValueError(f"Cannot set receiver of abstract class {cls}")
        old_receiver = self._receiver
        self._receiver = receiver
        if old_receiver:
            old_receiver.close()

    @property
    def host(self) -> str:
        """Return host address."""
        return self._host

    @property
    def type(self) -> str:
        """Return host type."""
        return self._type

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
        return self.state == Session.STATE_READY

    @property
    def is_stopped(self) -> bool:
        """Return True if stopped."""
        return self.state == Session.STATE_STOP

    @property
    def session_id(self) -> bytes:
        """Return Session ID."""
        return self._session_id

    @property
    def stream(self):
        """Return Stream."""
        return self._stream

    @property
    def stop_event(self):
        """Return Stop Event."""
        return self._stop_event

    @property
    def stream_ready_event(self) -> asyncio.Event:
        """Return Stream Ready Event."""
        return self._stream_ready_event

    @property
    def resolution(self) -> Resolution:
        """Return resolution."""
        return self._resolution

    @property
    def quality(self) -> Quality:
        """Return Quality."""
        return self._quality

    @property
    def fps(self) -> FPS:
        """Return FPS."""
        return self._fps

    @property
    def codec(self) -> str:
        """Return video codec."""
        return self._codec

    @property
    def hdr(self) -> bool:
        """Return True if HDR."""
        return self.stream_type == StreamType.HEVC_HDR

    @property
    def stream_type(self) -> StreamType:
        """Return Stream Type."""
        if self.type == TYPE_PS4:
            return StreamType.H264
        return self._stream_type

    @property
    def receiver(self) -> AVReceiver:
        """Return AV Receiver."""
        return self._receiver

    @property
    def events(self) -> ExecutorEventEmitter:
        """Return Event Emitter."""
        return self._events

    @property
    def loop(self) -> asyncio.AbstractEventLoop:
        """Return loop."""
        return self._loop
