"""Utility Methods."""
import inspect
import logging
import select
import time
from binascii import hexlify

_LOGGER = logging.getLogger(__name__)


def log_bytes(name: str, data: bytes):
    """Log bytes."""
    mod = inspect.getmodulename(inspect.stack()[1].filename)
    logging.getLogger(f"{__package__}.{mod}").debug(
        "Length: %s, %s: %s", len(data), name, hexlify(data))


def from_b(_bytes: bytes, order="big") -> int:
    """Return int from hex bytes."""
    return int.from_bytes(_bytes, order)


def to_b(_int: int, length: int = 2, order="big") -> bytes:
    """Return hex bytes from int."""
    return int.to_bytes(_int, length, order)


def listener(name: str, sock, handle, stop_event):
    """Worker for socket."""
    _LOGGER.debug("Thread Started: %s", name)
    stop_event.clear()
    while not stop_event.is_set():
        available, _, _ = select.select([sock], [], [], 0.01)
        if sock in available:
            data = sock.recv(4096)
            # log_bytes(f"{name} RECV", data)
            if len(data) > 0:
                handle(data)
            else:
                stop_event.set()
        time.sleep(0.001)

    sock.close()
    _LOGGER.info(f"{name} Stopped")


def timeit(func):
    """Time Function."""
    def inner(*args, **kwargs):
        start = time.time()
        result = func(*args, **kwargs)
        end = time.time()
        elapsed = round(end - start, 8)
        _LOGGER.debug("Timed %s.%s at %s seconds", func.__module__, func.__name__, elapsed)
        return result
    return inner
