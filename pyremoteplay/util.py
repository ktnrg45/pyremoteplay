"""Utility Methods."""
import inspect
import json
import logging
import pathlib
import select
import time
from binascii import hexlify


from .const import CONTROLS_FILE, OPTIONS_FILE, PROFILE_DIR, PROFILE_FILE

_LOGGER = logging.getLogger(__name__)


def check_dir() -> pathlib.Path:
    """Return path. Check file dir and create dir if not exists."""
    dir_path = pathlib.Path.home() / PROFILE_DIR
    if not dir_path.is_dir():
        dir_path.mkdir()
    return dir_path


def check_file(path: pathlib.Path):
    """Check if file exists and create."""
    if not path.is_file():
        with open(path, "w", encoding="utf-8") as _file:
            json.dump({}, _file)


def get_mapping(path: str = None) -> dict:
    """Return dict of key mapping."""
    data = {}
    if not path:
        dir_path = check_dir()
        path = dir_path / CONTROLS_FILE
    else:
        path = pathlib.Path(path)
    check_file(path)
    with open(path, "r", encoding="utf-8") as _file:
        data = json.load(_file)
    return data


def write_mapping(mapping: dict, path: str = None):
    """Write mapping."""
    if not path:
        path = pathlib.Path.home() / PROFILE_DIR / CONTROLS_FILE
    else:
        path = pathlib.Path(path)
    with open(path, "w", encoding="utf-8") as _file:
        json.dump(mapping, _file, indent=2)


def get_options(path: str = None) -> dict:
    """Return dict of options."""
    data = {}
    if not path:
        dir_path = check_dir()
        path = dir_path / OPTIONS_FILE
    else:
        path = pathlib.Path(path)
    check_file(path)
    with open(path, "r", encoding="utf-8") as _file:
        data = json.load(_file)
    return data


def write_options(options: dict, path: str = None):
    """Write options."""
    if not path:
        path = pathlib.Path.home() / PROFILE_DIR / OPTIONS_FILE
    else:
        path = pathlib.Path(path)
    with open(path, "w", encoding="utf-8") as _file:
        json.dump(options, _file)


def get_profiles(path: str = None) -> dict:
    """Return Profiles."""
    data = []
    if not path:
        dir_path = check_dir()
        path = dir_path / PROFILE_FILE
    else:
        path = pathlib.Path(path)
    check_file(path)
    with open(path, "r", encoding="utf-8") as _file:
        data = json.load(_file)
    return data


def write_profiles(profiles: dict, path: str = None):
    """Write profile data."""
    if not path:
        path = pathlib.Path.home() / PROFILE_DIR / PROFILE_FILE
    else:
        path = pathlib.Path(path)
    with open(path, "w", encoding="utf-8") as _file:
        json.dump(profiles, _file)


def add_profile(profiles: dict, user_data: dict) -> dict:
    """Add profile to profiles and return profiles."""
    user_id = user_data.get("user_rpid")
    if not isinstance(user_id, str) and not user_id:
        _LOGGER.error("Invalid user id or user id not found")
        return dict()
    name = user_data["online_id"]
    profile = {
        name: {
            "id": user_id,
            "hosts": {},
        }
    }
    profiles.update(profile)
    return profiles


def get_users(device_id: str, profiles: dict = None, path: str = None):
    """Return users for device."""
    users = []
    if not profiles:
        profiles = get_profiles(path)
    for user, data in profiles.items():
        hosts = data.get("hosts")
        if not hosts:
            continue
        if hosts.get(device_id):
            users.append(user)
    return users


def add_regist_data(profile: dict, host_status: dict, data: dict) -> dict:
    """Add regist data to profile and return profile."""
    mac_address = host_status["host-id"]
    host_type = host_status["host-type"]
    for key in list(data.keys()):
        if key.startswith(host_type):
            value = data.pop(key)
            new_key = key.split("-")[1]
            data[new_key] = value
    profile["hosts"][mac_address] = {"data": data, "type": host_type}
    return profile


def format_regist_key(regist_key: str) -> bytes:
    """Format Regist Key for wakeup."""
    regist_key = int.from_bytes(
        bytes.fromhex(bytes.fromhex(regist_key).decode()), "big"
    )
    return regist_key


def get_devices(path: str = None) -> dict:
    """Return dict of devices from profiles."""
    devices = {}
    profiles = get_profiles(path)
    for _, data in profiles.items():
        _device_data = data.get("hosts")
        if not _device_data:
            continue
        devices.update(_device_data)
    return devices


def log_bytes(name: str, data: bytes):
    """Log bytes."""
    mod = inspect.getmodulename(inspect.stack()[1].filename)
    logging.getLogger(f"{__package__}.{mod}").debug(
        "Length: %s, %s: %s", len(data), name, hexlify(data)
    )


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
    _LOGGER.info("%s Stopped", name)


def timeit(func):
    """Time Function."""

    def inner(*args, **kwargs):
        start = time.time()
        result = func(*args, **kwargs)
        end = time.time()
        elapsed = round(end - start, 8)
        _LOGGER.info(
            "Timed %s.%s at %s seconds", func.__module__, func.__name__, elapsed
        )
        return result

    return inner
