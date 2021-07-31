"""Register methods for pyremoteplay."""
import logging
import socket

from Cryptodome.Random import get_random_bytes

from .const import RP_PORT, RP_VERSION, USER_AGENT
from .crypt import SessionCipher
from .keys import REG_KEY_0, REG_KEY_1
from .util import log_bytes

_LOGGER = logging.getLogger(__name__)

CLIENT_TYPE = "dabfa2ec873de5839bee8d3f4c0239c4282c07c25c6077a2931afcf0adc0d34f"
REG_URL = "http://{}:{}/sie/ps4/rp/sess/rgst"
REG_INIT = b"SRC2"
REG_START = b'RES2'

REG_DATA = bytearray(b'A' * 480)
REG_KEY_SIZE = 16


def gen_key_0(pin: int) -> bytes:
    """Generate key from Key 0."""
    key = bytearray(REG_KEY_SIZE)
    for index in range(0, REG_KEY_SIZE):
        key[index] = REG_KEY_0[index * 32 + 1]
    # Encode PIN into last 4 bytes
    shift = 0
    for index in range(12, REG_KEY_SIZE):
        key[index] ^= (pin >> (24 - (shift * 8)) & 255)
        shift += 1
    log_bytes("Key 0", key)
    return bytes(key)


def gen_key_1(nonce: bytes) -> bytes:
    """Generate key from Key 1."""
    key = bytearray(REG_KEY_SIZE)
    nonce = bytearray(nonce)
    for index in range(0, REG_KEY_SIZE):
        shift = REG_KEY_1[index * 32 + 8]
        key[index] = ((nonce[index] ^ shift) + 41 + index) % 256
    log_bytes("Key 1", key)
    return bytes(key)


def get_regist_payload(key_1: bytes) -> bytes:
    """Return regist payload."""
    payload = b''.join([
        bytes(REG_DATA[0:199]),
        key_1[8:],
        bytes(REG_DATA[207:401]),
        key_1[0:8],
        bytes(REG_DATA[409:]),
    ])
    log_bytes("Payload", payload)
    return payload


def encrypt_payload(cipher, psn_id: str) -> bytes:
    """Return Encrypted Register Payload."""
    payload = (
        f'Client-Type: {CLIENT_TYPE}\r\n'
        f'Np-AccountId: {psn_id}\r\n'
    ).encode("utf-8")
    log_bytes("Enc Payload", payload)
    enc_payload = cipher.encrypt(payload)
    return enc_payload


def get_regist_headers(payload_length: int) -> bytes:
    """Get regist headers."""
    headers = (
        # Appears to use a malformed http request so have to construct it
        'POST /sie/ps4/rp/sess/rgst HTTP/1.1\r\n HTTP/1.1\r\n'
        'HOST: 10.0.2.15\r\n'  # Doesn't Matter
        f'User-Agent: {USER_AGENT}\r\n'
        'Connection: close\r\n'
        f'Content-Length: {payload_length}\r\n'
        f'RP-Version: {RP_VERSION}\r\n\r\n'
    )
    headers = headers.encode("utf-8")
    log_bytes("Regist Headers", headers)
    return headers


def regist_init(host: str, timeout: float) -> bool:
    """Check if device is accepting registrations."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    sock.sendto(REG_INIT, (host, RP_PORT))
    success = False
    try:
        response = sock.recv(32)
    except socket.timeout:
        _LOGGER.error(
            "Device not in Register Mode;\nGo to Settings -> "
            "Remote Play Connection Settings -> Add Device\n"
        )
    else:
        if response is not None and bytearray(response)[0:4] == REG_START:
            _LOGGER.info("Register Started")
            success = True
        else:
            _LOGGER.error("Unknown Register response")
    sock.close()
    return success


def get_register_info(host: str, headers: bytes, payload: bytes, timeout: float) -> bytes:
    """Send Register Packet and receive register info."""
    response = None
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    sock.connect((host, RP_PORT))
    sock.sendall(b''.join([headers, payload]))
    try:
        response = sock.recvfrom(1024)
        response = response[0]
    except socket.timeout:
        _LOGGER.error("No Register Response Received")
    finally:
        sock.close()
    return response


def parse_response(cipher, response: bytes) -> dict:
    """Parse Register Response."""
    info = {}
    response = response.split(b'\r\n')
    if b"200 OK" in response[0]:
        _LOGGER.info("Registered successfully")
        cipher_text = response[-1]
        data = cipher.decrypt(cipher_text).decode()
        data = data.split('\r\n')
        for item in data:
            if item == "":
                continue
            item = item.split(": ")
            info[item[0]] = item[1]
    else:
        _LOGGER.error("Failed to register, Status: %s", response[0])
    _LOGGER.debug("Register Info: %s", info)
    return info


def register(host: str, psn_id: str, pin: str, timeout: float = 2.0) -> dict:
    """Return Register info. Register as Remote Play client."""
    info = {}
    if not regist_init(host, timeout):
        return info
    nonce = get_random_bytes(16)
    key_0 = gen_key_0(int(pin))
    key_1 = gen_key_1(nonce)
    payload = get_regist_payload(key_1)
    cipher = SessionCipher(key_0, nonce, counter=0)
    enc_payload = encrypt_payload(cipher, psn_id)
    payload = b''.join([payload, enc_payload])
    headers = get_regist_headers(len(payload))
    response = get_register_info(host, headers, payload, timeout)
    if response is not None:
        info = parse_response(cipher, response)
    return info
