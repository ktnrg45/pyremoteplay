"""Register methods for pyremoteplay."""
import logging
import socket

from Cryptodome.Random import get_random_bytes

from .const import (
    RP_PORT,
    RP_VERSION_PS4,
    RP_VERSION_PS5,
    TYPE_PS4,
    TYPE_PS5,
    USER_AGENT,
)
from .crypt import SessionCipher
from .ddp import get_host_type, get_status, async_get_status
from .keys import REG_KEY_0_PS4, REG_KEY_1_PS4, REG_KEY_0_PS5, REG_KEY_1_PS5
from .util import log_bytes
from .const import UDP_IP
from .socket import AsyncUDPSocket, AsyncTCPSocket

_LOGGER = logging.getLogger(__name__)

CLIENT_TYPE = "dabfa2ec873de5839bee8d3f4c0239c4282c07c25c6077a2931afcf0adc0d34f"
REG_PATH_PS4 = "/sie/ps4/rp/sess/rgst"
REG_PATH_PS5 = "/sie/ps5/rp/sess/rgst"
REG_INIT_PS4 = b"SRC2"
REG_START_PS4 = b"RES2"
REG_INIT_PS5 = b"SRC3"
REG_START_PS5 = b"RES3"

HOST_TYPES = {
    TYPE_PS4: {
        "init": REG_INIT_PS4,
        "start": REG_START_PS4,
        "path": REG_PATH_PS4,
        "version": RP_VERSION_PS4,
        "reg_key_0": REG_KEY_0_PS4,
        "reg_key_1": REG_KEY_1_PS4,
    },
    TYPE_PS5: {
        "init": REG_INIT_PS5,
        "start": REG_START_PS5,
        "path": REG_PATH_PS5,
        "version": RP_VERSION_PS5,
        "reg_key_0": REG_KEY_0_PS5,
        "reg_key_1": REG_KEY_1_PS5,
    },
}

REG_DATA = bytearray(b"A" * 480)
REG_KEY_SIZE = 16


def _gen_key_0(host_type: str, pin: int) -> bytes:
    """Generate key from Key 0."""
    reg_key = HOST_TYPES[host_type]["reg_key_0"]
    key = bytearray(REG_KEY_SIZE)
    for index in range(0, REG_KEY_SIZE):
        key[index] = reg_key[index * 32 + 1]
    # Encode PIN into last 4 bytes
    shift = 0
    for index in range(12, REG_KEY_SIZE):
        key[index] ^= pin >> (24 - (shift * 8)) & 255
        shift += 1
    log_bytes("Key 0", key)
    return bytes(key)


def _gen_key_1(host_type: str, nonce: bytes) -> bytes:
    """Generate key from Key 1."""
    reg_key = HOST_TYPES[host_type]["reg_key_1"]
    key = bytearray(REG_KEY_SIZE)
    nonce = bytearray(nonce)
    offset = -45 if host_type == TYPE_PS5 else 41
    for index in range(0, REG_KEY_SIZE):
        shift = reg_key[index * 32 + 8]
        key[index] = ((nonce[index] ^ shift) + offset + index) % 256
    log_bytes("Key 1", key)
    return bytes(key)


def _get_regist_payload(key_1: bytes) -> bytes:
    """Return regist payload."""
    payload = b"".join(
        [
            bytes(REG_DATA[0:199]),
            key_1[8:],
            bytes(REG_DATA[207:401]),
            key_1[0:8],
            bytes(REG_DATA[409:]),
        ]
    )
    log_bytes("Payload", payload)
    return payload


def _encrypt_payload(cipher, psn_id: str) -> bytes:
    """Return Encrypted Register Payload."""
    payload = (f"Client-Type: {CLIENT_TYPE}\r\n" f"Np-AccountId: {psn_id}\r\n").encode(
        "utf-8"
    )
    log_bytes("Enc Payload", payload)
    enc_payload = cipher.encrypt(payload)
    return enc_payload


def _get_regist_headers(host_type: str, payload_length: int) -> bytes:
    """Get regist headers."""
    path = HOST_TYPES[host_type]["path"]
    version = HOST_TYPES[host_type]["version"]
    headers = (
        # Appears to use a malformed http request so have to construct it
        f"POST {path} HTTP/1.1\r\n HTTP/1.1\r\n"
        "HOST: 10.0.2.15\r\n"  # Doesn't Matter
        f"User-Agent: {USER_AGENT}\r\n"
        "Connection: close\r\n"
        f"Content-Length: {payload_length}\r\n"
        f"RP-Version: {version}\r\n\r\n"
    )
    headers = headers.encode("utf-8")
    log_bytes("Regist Headers", headers)
    return headers


def _get_host_type_data(host_type: str) -> dict:
    data = HOST_TYPES.get(host_type)
    if not data:
        _LOGGER.error("Invalid host_type: %s", host_type)
    return data


def _check_init(data: dict, response: bytes) -> bool:
    if not response:
        _LOGGER.error(
            "Device not in Register Mode;\nGo to Settings -> "
            "Remote Play Connection Settings -> Add Device\n"
        )
    else:
        if bytearray(response)[0:4] == data["start"]:
            _LOGGER.info("Register Started")
            return True
        else:
            _LOGGER.error("Unknown Register response")
    return False


def _regist_init(host: str, host_type: str, timeout: float) -> bool:
    """Check if device is accepting registrations."""
    success = False
    response = None
    data = _get_host_type_data(host_type)
    if not data:
        return success

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    sock.sendto(data["init"], (host, RP_PORT))

    try:
        response = sock.recv(32)
    except socket.timeout:
        pass
    success = _check_init(data, response)
    sock.close()
    return success


async def _async_regist_init(host: str, host_type: str, timeout: float) -> bool:
    """Check if device is accepting registrations."""
    success = False
    response = None
    data = _get_host_type_data(host_type)
    if not data:
        return success

    sock = await AsyncUDPSocket.create(
        local_addr=(UDP_IP, 0), remote_addr=(host, RP_PORT)
    )
    sock.sendto(data["init"], (host, RP_PORT))
    response = await sock.recv(timeout=timeout)
    success = _check_init(data, response)
    sock.close()
    return success


def _get_register_info(
    host: str, headers: bytes, payload: bytes, timeout: float
) -> bytes:
    """Send Register Packet and receive register info."""
    response = None
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    sock.connect((host, RP_PORT))
    sock.sendall(b"".join([headers, payload]))
    try:
        response = sock.recvfrom(1024)
        response = response[0]
    except socket.timeout:
        _LOGGER.error("No Register Response Received")
    finally:
        sock.close()
    return response


async def _async_get_register_info(
    host: str, headers: bytes, payload: bytes, timeout: float
) -> bytes:
    """Send Register Packet and receive register info."""
    response = None
    sock = await AsyncTCPSocket.create(remote_addr=(host, RP_PORT))
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    sock.send(b"".join([headers, payload]))
    response = await sock.recv(timeout)
    if not response:
        _LOGGER.error("No Register Response Received")
    sock.close()
    return response


def _parse_response(cipher, response: bytes) -> dict:
    """Parse Register Response."""
    info = {}
    response = response.split(b"\r\n")
    if b"200 OK" in response[0]:
        _LOGGER.info("Registered successfully")
        cipher_text = response[-1]
        data = cipher.decrypt(cipher_text).decode()
        data = data.split("\r\n")
        for item in data:
            if item == "":
                continue
            item = item.split(": ")
            info[item[0]] = item[1]
    else:
        _LOGGER.error("Failed to register, Status: %s", response[0])
    _LOGGER.debug("Register Info: %s", info)
    return info


def _get_regist_cipher_headers_payload(host_type: str, psn_id: str, pin: str):
    nonce = get_random_bytes(16)
    key_0 = _gen_key_0(host_type, int(pin))
    key_1 = _gen_key_1(host_type, nonce)
    payload = _get_regist_payload(key_1)
    cipher = SessionCipher(host_type, key_0, nonce, counter=0)
    enc_payload = _encrypt_payload(cipher, psn_id)
    payload = b"".join([payload, enc_payload])
    headers = _get_regist_headers(host_type, len(payload))
    return cipher, headers, payload


def register(host: str, psn_id: str, pin: str, timeout: float = 2.0) -> dict:
    """Return Register info.
    Register this client and a PSN Account with a Remote Play Device.

    :param host: IP Address of Remote Play Device
    :param psn_id: Base64 encoded PSN ID from completing OAuth login
    :param pin: PIN for linking found on Remote Play Host
    :param timeout: Timeout to wait for completion
    """
    info = {}
    status = get_status(host)
    if not status:
        _LOGGER.error("Host: %s not found", host)
        return info
    host_type = get_host_type(status).upper()
    if not _regist_init(host, host_type, timeout):
        return info

    cipher, headers, payload = _get_regist_cipher_headers_payload(
        host_type, psn_id, pin
    )

    response = _get_register_info(host, headers, payload, timeout)
    if response is not None:
        info = _parse_response(cipher, response)
    return info


async def async_register(
    host: str, psn_id: str, pin: str, timeout: float = 2.0
) -> dict:
    """Return Register info.
    Register this client and a PSN Account with a Remote Play Device.

    :param host: IP Address of Remote Play Device
    :param psn_id: Base64 encoded PSN ID from completing OAuth login
    :param pin: PIN for linking found on Remote Play Host
    :param timeout: Timeout to wait for completion
    """
    info = {}
    status = await async_get_status(host)
    if not status:
        _LOGGER.error("Host: %s not found", host)
        return info
    host_type = get_host_type(status).upper()
    if not await _async_regist_init(host, host_type, timeout):
        return info
    cipher, headers, payload = _get_regist_cipher_headers_payload(
        host_type, psn_id, pin
    )
    response = await _async_get_register_info(host, headers, payload, timeout)
    if response is not None:
        info = _parse_response(cipher, response)
    return info
