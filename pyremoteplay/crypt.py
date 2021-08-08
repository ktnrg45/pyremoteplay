"""Common Crypto Methods."""
import logging
from struct import pack_into

from Cryptodome.Cipher import AES
from Cryptodome.Hash import HMAC, SHA256
from Cryptodome.Random import get_random_bytes
from Cryptodome.Util.strxor import strxor
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.asymmetric import ec
# from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from .errors import CryptError
from .keys import HMAC_KEY
from .util import from_b, log_bytes, timeit, to_b

_LOGGER = logging.getLogger(__name__)

GMAC_REFRESH_IV = 44910
GMAC_REFRESH_KEY_POS = 45000


def get_gmac_key(gmac_index: int, key: bytes, iv: bytes) -> bytes:
    """Return GMAC key."""
    gmac_index *= GMAC_REFRESH_IV
    out_array = counter_add(gmac_index, iv)
    out_key = b''.join([key, out_array])
    gcm = SHA256.new(out_key)
    gcm = gcm.digest()
    gcm = strxor(gcm[0:16], gcm[16:])
    return gcm


def counter_add(counter: int, iv: bytes) -> bytes:
    """Increment IV by counter."""
    # LE to BE -> Add counter -> BE to LE
    # iv = bytes(bytearray(to_b(from_b(bytes(bytearray(iv)[::-1])) + counter, 16))[::-1])
    iv = bytearray(iv)
    for index, value in enumerate(iv):
        add = value + counter
        iv[index] = add & 0xff
        if (counter := add >> 8) <= 0 or index >= 15:
            break
    return bytes(iv)


def get_base_key_iv(secret: bytes, handshake_key: bytes, index: int) -> bytes:
    key_iv = b''.join([
        bytes([0x01, index, 0x00]), handshake_key, bytes([0x01, 0x00])
    ])
    hmac = HMAC.new(key=secret, msg=key_iv, digestmod=SHA256)
    hmac = hmac.digest()
    hmac = bytearray(hmac)
    key = bytes(hmac[0:16])
    iv = bytes(hmac[16:])
    return key, iv


def get_gmac_cipher(key: bytes, iv: bytes) -> bytes:
    """Return GMAC Cipher."""
    cipher = AES.new(key, AES.MODE_GCM, nonce=iv, mac_len=4)
    return cipher


def get_gmac_tag(
        data: bytes, key: bytes, iv: bytes) -> bytes:
    """Return GMAC tag of packet."""
    cipher = AESGCM(key)
    return cipher.encrypt(iv, b'', data)[:4]
    # cipher = get_gmac_cipher(key, iv)
    # cipher.update(data)
    # return cipher.digest()


def gen_iv_stream(buf: bytearray, iv: bytes, key_pos: int):
    """Pack buf with stream of incremented IVs."""
    length = len(buf)
    assert length % 16 == 0
    blocks = length // 16
    block_offset = (key_pos // 16) + 1  # Start at next block
    stop = blocks + block_offset
    current = 0
    for block in range(block_offset, stop):
        pack_into("!16s", buf, current, counter_add(block, iv))
        current += 16


# TODO: Make more efficient
def get_key_stream(
        key: bytes, iv: bytes, key_pos: int, data_len: int) -> bytes:
    """Return the minimum CTR Keystream at key position."""
    padding = key_pos % 16
    key_pos = key_pos - padding
    assert key_pos % 16 == 0
    key_stream_len = ((padding + data_len + 16 - 1) // 16) * 16

    key_stream = bytearray(key_stream_len)
    gen_iv_stream(key_stream, iv, key_pos)

    # cipher = Cipher(algorithms.AES(key), modes.ECB())
    # encryptor = cipher.encryptor()
    # buf = bytearray(len(key_stream) + 15)
    # len_encrypted = encryptor.update_into(key_stream, buf)
    # key_stream = buf[:len_encrypted] + encryptor.finalize()

    cipher = AES.new(key, AES.MODE_ECB)
    key_stream = cipher.encrypt(key_stream)

    key_stream = key_stream[padding:]  # Align to the overflow of the block.
    key_stream = key_stream[:data_len]  # Truncate to match packet size.
    return key_stream


def decrypt_encrypt(key: bytes, iv: bytes, key_pos: int, data: bytes, key_stream=b''):
    """Return Decrypted or Encrypted packet. Two way."""
    if not key_stream:
        key_stream = get_key_stream(key, iv, key_pos, len(data))
    enc_data = strxor(data, key_stream)
    return enc_data


class BaseCipher():
    """Base AES CTR Cipher."""
    KEYSTREAM_LEN = 0x1000

    def __init__(self, handshake_key, secret):
        self.handshake_key = handshake_key
        self.secret = secret
        self._base_index = 0
        self.base_key = None
        self.base_gmac_key = None
        self.base_iv = None
        self.current_key = None
        self.index = 0
        self.keystreams = []
        self.keystream_index = 0

    def _init_cipher(self):
        self.base_key, self.base_iv = get_base_key_iv(
            self.secret, self.handshake_key, self._base_index)
        self.current_key = self.base_gmac_key = get_gmac_key(self.index, self.base_key, self.base_iv)
        self._next_key_stream()

    def _next_key_stream(self):
        while len(self.keystreams) < 3:
            key_pos = self.keystream_index * BaseCipher.KEYSTREAM_LEN
            key_stream = get_key_stream(self.base_key, self.base_iv, key_pos, BaseCipher.KEYSTREAM_LEN)
            self.keystreams.append((self.keystream_index, key_stream))
            self.keystream_index += 1

    def get_key_stream(self, key_pos: int, data_len: int) -> bytes:
        self._next_key_stream()
        # Remove block if key pos not in queue.
        for index, key_stream in enumerate(self.keystreams):
            ks_index = key_stream[0]
            if key_pos // BaseCipher.KEYSTREAM_LEN > ks_index:
                self.keystreams.pop(index)
            else:
                break
        key_stream = b''
        if self.keystreams:
            requires_additional = False
            start_pos = key_pos % BaseCipher.KEYSTREAM_LEN
            if start_pos + data_len > BaseCipher.KEYSTREAM_LEN:
                requires_additional = True
            end_pos = (key_pos + data_len) % BaseCipher.KEYSTREAM_LEN
            if requires_additional:
                if len(self.keystreams) < 2:
                    return key_stream
                end_pos = data_len - (BaseCipher.KEYSTREAM_LEN - start_pos)
                key_stream = self.keystreams.pop(0)[1][start_pos:]
                key_stream += self.keystreams[0][1][:end_pos]
            else:
                key_stream = self.keystreams[0][1][start_pos:end_pos]
        return key_stream

    def gen_new_key(self):
        if self.base_gmac_key is None:
            raise CryptError("Base GMAC Key is None")
        self.current_key = get_gmac_key(
            self.index, self.base_gmac_key, self.base_iv)
        # _LOGGER.debug("Cipher: %s, Index: %s", self.name, self.index)
        return self.current_key

    def get_gmac(self, data: bytes, key_pos: int):
        """Get GMAC tag of packet."""
        iv = counter_add(key_pos // 16, self.base_iv)
        if key_pos > 0:
            index = (key_pos - 1) // GMAC_REFRESH_KEY_POS
        else:
            index = 0
        if index > self.index:
            self.index = index
            key = self.gen_new_key()
        elif index < self.index:
            key = get_gmac_key(index, self.base_key, self.base_iv)
        else:
            key = self.current_key
        tag = get_gmac_tag(data, key, iv)
        return tag


class RemoteCipher(BaseCipher):
    """Cipher for receiving packets."""

    def __init__(self, handshake_key, secret):
        super().__init__(handshake_key, secret)
        self._base_index = 3
        self.name = "Remote"
        self._init_cipher()

    def decrypt(self, data: bytes, key_pos: int) -> bytes:
        """Decrypt data."""
        key_stream = b''
        key_stream = self.get_key_stream(key_pos, len(data))
        dec = decrypt_encrypt(self.base_key, self.base_iv, key_pos, data, key_stream)
        return dec

    def verify_gmac(self, data: bytes, key_pos: int, gmac: bytes) -> bool:
        """Verify GMAC."""
        tag = self.get_gmac(data, key_pos)
        verified = tag == gmac
        _LOGGER.debug("GMAC Verified: %s", verified)
        if not verified:
            _LOGGER.debug("GMAC Mismatch: Expected %s, RECV: %s", tag.hex(), gmac.hex())
        return verified


class LocalCipher(BaseCipher):
    """Cipher for sending packets."""

    def __init__(self, handshake_key, secret):
        super().__init__(handshake_key, secret)
        self._base_index = 2
        self.name = "Local"
        self._key_pos = 0
        self._init_cipher()

    def get_gmac(self, data: bytes) -> bytes:
        """Return GMAC Tag."""
        tag = super().get_gmac(data, self.key_pos)
        return tag

    def encrypt(self, data: bytes) -> bytes:
        """Encrypt data using key stream."""
        key_stream = b''
        key_stream = self.get_key_stream(self.key_pos, len(data))
        enc = decrypt_encrypt(self.base_key, self.base_iv, self.key_pos, data, key_stream)
        return enc

    def advance_key_pos(self, advance_by: int):
        """Advance key pos by data length."""
        self._key_pos += advance_by
        _LOGGER.debug(
            "Advancing key pos by %s to: %s", advance_by, self.key_pos)

    @property
    def key_pos(self) -> int:
        """Return Key Pos."""
        return self._key_pos


class StreamCipher():
    """Collection of Local and Remote Ciphers."""

    def __init__(self, local_cipher, remote_cipher):
        self._local_cipher = local_cipher
        self._remote_cipher = remote_cipher

    def encrypt(self, data: bytes) -> bytes:
        """Return Encrypted data."""
        return self._local_cipher.encrypt(data)

    def decrypt(self, data: bytes, key_pos: int) -> bytes:
        """Return Decrypted data."""
        return self._remote_cipher.decrypt(data, key_pos)

    def get_gmac(self, data: bytes) -> bytes:
        """Return GMAC Tag."""
        return self._local_cipher.get_gmac(data)

    def verify_gmac(self, data: bytes, key_pos: int, gmac: bytes):
        """Verify GMAC."""
        return self._remote_cipher.verify_gmac(data, key_pos, gmac)

    def advance_key_pos(self, advance_by: int):
        """Advance local key pos by data length."""
        self._local_cipher.advance_key_pos(advance_by)

    @property
    def key_pos(self) -> int:
        """Return local cipher Key Pos."""
        return self._local_cipher.key_pos


def get_aes_cipher(key: bytes, iv: bytes, segment_size=128):
    """Get AES Cipher."""
    # Segment size is in bits. AES-CFB-128.
    cipher = AES.new(
        key, AES.MODE_CFB, iv, segment_size=segment_size)
    return cipher


def get_hmac(nonce: bytes):
    """Return HMAC for the IV."""
    hmac = HMAC.new(key=HMAC_KEY, msg=nonce, digestmod=SHA256)
    current_hmac = hmac.digest()
    # log_bytes('Current HMAC', current_hmac)
    return current_hmac


def get_aes_iv(nonce: bytes, counter: int):
    """Get IV for AES Cipher as the truncated HMAC."""
    # _LOGGER.debug("IV Counter: %s", counter)
    shift = 56
    suffix = bytearray(8)
    for index in range(0, 8):
        suffix[index] = ((counter >> shift) & 0xff)
        shift -= 8
    nonce = b''.join([nonce, bytes(suffix)])
    current_hmac = get_hmac(bytes(nonce))
    iv = current_hmac[:16]  # Truncate to IV length.
    return iv


def get_ciphers(key: bytes, nonce: bytes, counter=0) -> tuple:
    """Return tuple of AES CFB Ciphers."""
    iv = get_aes_iv(nonce, counter=counter)
    enc_cipher = get_aes_cipher(key=key, iv=iv)
    dec_cipher = get_aes_cipher(key=key, iv=iv)
    return enc_cipher, dec_cipher


def get_cipher(key: bytes, nonce: bytes, counter=0):
    """Return a AES CFB Cipher."""
    iv = get_aes_iv(nonce, counter=counter)
    cipher = get_aes_cipher(key=key, iv=iv)
    return cipher


class SessionCipher():
    """AES CFB-128 Cipher pair."""

    def __init__(self, key: bytes, nonce: bytes, counter=0):
        self._enc_cipher = None
        self._dec_cipher = None
        self._key = key
        self._nonce = nonce
        self._enc_counter = self._dec_counter = counter

        self._enc_cipher, self._dec_cipher = get_ciphers(key, nonce, counter)

    def encrypt(self, msg: bytes, counter=None) -> bytes:
        """Return Encrypted Message."""
        if counter is not None:
            _enc_cipher = get_cipher(self._key, self._nonce, counter)
            enc = _enc_cipher.encrypt(msg)
            return enc

        enc = self._enc_cipher.encrypt(msg)
        self._enc_counter += 1
        self._enc_cipher = get_cipher(self._key, self._nonce, self.enc_counter)
        return enc

    def decrypt(self, msg: bytes) -> bytes:
        """Return decrypted Message."""
        dec = self._dec_cipher.decrypt(msg)
        self._dec_counter += 1
        self._dec_cipher = get_cipher(self._key, self._nonce, self.dec_counter)
        return dec

    @property
    def enc_counter(self) -> int:
        """Return encrypt counter."""
        return self._enc_counter

    @property
    def dec_counter(self) -> int:
        """Return decrypt counter."""
        return self._dec_counter


class StreamECDH():
    """ECDH Container for Stream."""

    def get_handshake_key(handshake: bytes = None):
        """Return random key for ECDH."""
        handshake_key = handshake or get_random_bytes(16)
        log_bytes("Handshake Key", handshake_key)
        return handshake_key

    def set_local_ec(key: bytes = None):
        """Init Local EC Key object."""
        key = key or get_random_bytes(32)
        private = from_b(key)
        private_key = ec.derive_private_key(
            private, ec.SECP256K1(), default_backend())
        return private_key

    def set_private_key(local_ec):
        """Return Private Key for ECDH."""
        private_numbers = local_ec.private_numbers().private_value
        private_key = to_b(private_numbers, 32)
        log_bytes("Private Key", private_key)
        return private_key

    def set_public_key(local_ec):
        """Return Public Key for ECDH."""
        pub = local_ec.public_key()
        public_key = pub.public_bytes(
            Encoding.X962, PublicFormat.UncompressedPoint)
        log_bytes("Public Key", public_key)
        return public_key

    def get_key_sig(handshake_key, public_key):
        """Return authenticated signature of public key."""
        hmac = HMAC.new(key=handshake_key, msg=public_key, digestmod=SHA256)
        local_sig = hmac.digest()
        log_bytes("Public Key Sig", local_sig)
        return local_sig

    def get_secret(local_key, remote_key: bytes):
        """Return derived secret from ECDH exchange."""
        remote_key = ec.EllipticCurvePublicKey.from_encoded_point(
            ec.SECP256K1(), remote_key)
        remote_key.public_numbers()
        secret = local_key.exchange(ec.ECDH(), remote_key)
        log_bytes('Secret', secret)
        return secret

    def __init__(self, handshake: bytes = None, private_key: bytes = None):
        self.handshake_key = None
        self._secret = None
        self._local_ec = None
        self._private_key = None
        self.public_key = None
        self.public_sig = None
        self._remote_key = None
        self._remote_sig = None
        self._init_keys(handshake, private_key)

    def _init_keys(self, handshake, private_key):
        self.handshake_key = StreamECDH.get_handshake_key(handshake)
        self._local_ec = StreamECDH.set_local_ec(private_key)
        self._private_key = StreamECDH.set_private_key(self._local_ec)
        self.public_key = StreamECDH.set_public_key(self._local_ec)
        self.public_sig = StreamECDH.get_key_sig(self.handshake_key, self.public_key)

    def _verify_remote_sig(self, remote_key: bytes, remote_sig: bytes) -> bool:
        """Return True if Remote Signature is valid."""
        hmac = HMAC.new(key=self.handshake_key, msg=remote_key, digestmod=SHA256)
        _remote_sig = hmac.digest()
        log_bytes("Public Key Remote", remote_key)
        log_bytes("Public Key Sig", _remote_sig)
        if _remote_sig == remote_sig:
            _LOGGER.debug("Remote Signature Verified")
            return True
        _LOGGER.error("Remote Signature Invalid")
        log_bytes("Expected Sig", remote_sig)
        return False

    def set_secret(self, remote_key: bytes, remote_sig: bytes):
        """Return False if sig invalid. Set the ECDH Secret."""
        if not self._verify_remote_sig(remote_key, remote_sig):
            return False
        self._remote_key = remote_key
        self._remote_sig = remote_sig
        self._secret = StreamECDH.get_secret(self._local_ec, remote_key)
        return True

    def init_ciphers(self) -> StreamCipher:
        """Return Stream Cipher."""
        local_cipher = LocalCipher(
            self.handshake_key, self._secret)
        remote_cipher = RemoteCipher(
            self.handshake_key, self._secret)
        return StreamCipher(local_cipher, remote_cipher)
