"""Main methods for pyyremoteplay"""

import argparse
import asyncio
import curses
import logging
import sys
import threading
from collections import OrderedDict
import socket
import atexit

from .ddp import get_status, search
from .oauth import prompt as oauth_prompt
from .register import register
from .util import add_profile, add_regist_data, get_profiles, write_profiles
from . import RPDevice

NEW_PROFILE = "New Profile"
CANCEL = "Cancel"
# RESOLUTIONS = ["360p", "540p", "720p", "1080p"]
# FPS_CHOICES = ["high", "low", "60", "30"]
_LOGGER = logging.getLogger(__name__)


def main():
    """Main entrypoint."""
    logging.basicConfig(level=logging.WARNING)
    if "-l" in sys.argv or "--list" in sys.argv:
        show_devices()
        return
    parser = argparse.ArgumentParser(description="Start Remote Play.")
    parser.add_argument(
        "host", type=str, default="", help="IP address of Remote Play host"
    )
    # parser.add_argument(
    #     "-r",
    #     "--resolution",
    #     default="720p",
    #     type=str,
    #     choices=RESOLUTIONS,
    #     help="Resolution to use",
    # )
    # parser.add_argument(
    #     "-f",
    #     "--fps",
    #     default="high",
    #     type=str,
    #     choices=FPS_CHOICES,
    #     help="Max FPS to use",
    # )

    # Just to show in help
    parser.add_argument(
        "-l",
        "--list",
        action="store_true",
        help="List Devices",
    )
    parser.add_argument(
        "--register", action="store_true", help="Register with Remote Play host."
    )

    args = parser.parse_args()
    host = args.host
    should_register = args.register
    # resolution = "360p"
    # fps = 30
    # resolution = args.resolution
    # fps = args.fps
    # if fps.isnumeric():
    #     fps = int(fps)

    try:
        socket.gethostbyname(host)
    except socket.gaierror:
        print(f"\nError: Could not find host with address: {host}\n\n")
        parser.print_help()
        return
    if should_register:
        register_profile(host)
        return
    cli(host)


def show_devices():
    """Print all devices."""
    devices = search()
    print(f"\nFound {len(devices)} devices:\n")
    for device in devices:
        print(
            f"\tIP Address: {device.get('host-ip')}\n"
            f"\tName: {device.get('host-name')}\n"
            f"\tType: {device.get('host-type')}\n\n"
        )


def select_profile(profiles: dict, use_single: bool, get_new: bool) -> str:
    """Return profile name."""
    name = NEW_PROFILE
    if len(profiles) == 1 and use_single:
        name = list(profiles.keys())[0]
        return name
    print("Found Profiles")
    names = list(profiles.keys())
    if get_new:
        names.append(NEW_PROFILE)
    names.append(CANCEL)
    prompt = ""
    for _index, item in enumerate(names):
        prompt = f"{prompt}{_index}: {item}\n"
    while True:
        index = int(input(f"Select a profile to use:\n{prompt}>> "))
        try:
            name = names[index]
        except IndexError:
            print("Invalid Selection")
            continue
        if name == CANCEL:
            sys.exit()
        break
    return name


def register_profile(host: str):
    """Register with host."""
    name = ""
    status = get_status(host)
    if not status:
        print("Host is not reachable")
        return
    if status.get("status-code") != 200:
        return

    profiles = get_profiles()
    if profiles:
        name = select_profile(profiles, False, True)
    if name == NEW_PROFILE or not profiles:
        user_data = oauth_prompt()
        if user_data is None:
            sys.exit()
        profiles = add_profile(profiles, user_data)
        if not profiles:
            print("Could not parse user data")
            return
        write_profiles(profiles)
        name = user_data["online_id"]

    user_id = profiles[name]["id"]
    if not user_id:
        _LOGGER.error("No User ID")
        sys.exit()
    pin = ""
    while True:
        pin = input(
            f"On Remote Play host, Login to your PSN Account: {name}\n"
            "Then go to Settings -> "
            "Remote Play Connection Settings -> "
            "Add Device and enter the PIN shown\n>> "
        )
        if pin.isnumeric() and len(pin) == 8:
            break
        print("Invalid PIN. PIN must be only 8 numbers")

    data = register(host, user_id, pin)
    if not data:
        sys.exit()
    profile = profiles[name]
    profile = add_regist_data(profile, status, data)
    write_profiles(profiles)


def cli(host: str):
    """Start CLI."""
    device = RPDevice(host)
    device.get_status()
    profiles = get_profiles()
    if profiles:
        user = select_profile(profiles, True, False)
        if user not in device.get_users():
            _LOGGER.error("User: %s not registered with this device", user)
        setup_worker(device, user)
    else:
        _LOGGER.info("No Profiles")


def worker(device: RPDevice, user: str, event: threading.Event):
    """Worker."""
    loop = asyncio.new_event_loop()
    device.create_session(user, loop=loop)
    task = loop.create_task(async_start(device, event))
    atexit.register(loop.stop)
    loop.run_until_complete(task)
    loop.run_forever()


def setup_worker(device: RPDevice, user: str):
    """Sync method for starting session."""
    event = threading.Event()
    thread = threading.Thread(target=worker, args=(device, user, event), daemon=True)
    thread.start()
    curses.wrapper(start, device, event)


async def async_start(device: RPDevice, event: threading.Event):
    """Start Session."""
    loop = asyncio.get_running_loop()
    started = await device.connect()
    if not started:
        loop.stop()
    event.set()


def start(stdscr, device: RPDevice, event: threading.Event):
    """Start Instance."""
    event.wait(timeout=5)
    instance = CLIInstance(device)
    if not device.session.is_running:
        _LOGGER.error("Session Failed to Start: %s", device.session.error)
        return
    instance.run(stdscr)


class CLIInstance:
    """Emulated Keyboard."""

    MAP = {
        "Q": "STANDBY",
        "q": "QUIT",
        "KEY_UP": "UP",
        "KEY_DOWN": "DOWN",
        "KEY_LEFT": "LEFT",
        "KEY_RIGHT": "RIGHT",
        "1": "L1",
        "2": "L2",
        "3": "L3",
        "4": "R1",
        "5": "R2",
        "6": "R3",
        "\n": "CROSS",
        "c": "CIRCLE",
        "r": "SQUARE",
        "t": "TRIANGLE",
        "KEY_BACKSPACE": "OPTIONS",
        "=": "SHARE",
        "p": "PS",
        "y": "TOUCHPAD",
    }

    def __init__(self, device: RPDevice):
        self._device = device
        self._loop = self._device.session.loop
        self.controller = self._device.controller
        self.stdscr = None
        self.last_key = None
        self.map = self.MAP
        self._pos = (0, 0)

    def _init_color(self):
        curses.start_color()
        curses.init_pair(1, curses.COLOR_WHITE, curses.COLOR_BLACK)
        curses.init_pair(2, curses.COLOR_CYAN, curses.COLOR_BLACK)
        curses.init_pair(3, curses.COLOR_MAGENTA, curses.COLOR_BLACK)
        curses.init_pair(4, curses.COLOR_YELLOW, curses.COLOR_BLACK)
        curses.init_pair(5, curses.COLOR_GREEN, curses.COLOR_BLACK)

    def _show_mapping(self):
        self.stdscr.addstr("\n")
        _key_mapping = OrderedDict()
        _key_mapping.update({"Key": "Action"})
        _key_mapping.update(self.map)
        item = 0
        for key, value in _key_mapping.items():
            if key == "\n":
                key = "KEY_ENTER"
            self.stdscr.addstr(key, curses.color_pair(5))
            self.stdscr.addstr(" : ")
            self.stdscr.addstr(value, curses.color_pair(4))
            item += 1
            if item >= 4:
                item = 0
                self.stdscr.addstr("\n")
            else:
                self.stdscr.addstr(" | ")
        self.stdscr.addstr("\n\n")
        self._pos = self.stdscr.getyx()
        self.stdscr.clrtobot()
        self.stdscr.refresh()

    def _init_window(self):
        win_size = self.stdscr.getmaxyx()
        self.stdscr.setscrreg(self.stdscr.getyx()[0], win_size[0] - 1)
        self.stdscr.clear()
        self.stdscr.refresh()
        self.stdscr.addstr(
            0,
            0,
            "Remote Play - Interactive mode, press 'q' to exit\n",
        )
        self._show_mapping()

    def run(self, stdscr):
        """Run CLI Instance."""
        self.controller.start()
        self.stdscr = stdscr
        self._init_color()
        self.stdscr.scrollok(True)
        self._init_window()
        timeout = 50
        self.stdscr.timeout(timeout)
        self.stdscr.clrtobot()
        _last = ""
        while self._device.session.is_running:
            self._init_window()
            if _last:
                self._write_str(_last, 5)
            try:
                key = self._handle_key(self.stdscr.getkey())
                if key:
                    _last = key
            except curses.error:
                if self.last_key is not None:
                    self.controller.button(self.last_key, "release")
                    self.last_key = None

    def _write_str(self, text, color=1):
        self.stdscr.move(self._pos[0], self._pos[1])
        self.stdscr.clrtobot()
        self.stdscr.addstr(text, curses.color_pair(color))
        self.stdscr.move(self._pos[0] + 1, self._pos[1])

    def _handle_key(self, key):
        key = self.map.get(key)
        if key and self.last_key is None:
            self.last_key = key
            if key == "QUIT":
                self._write_str(key, 3)
                self.stdscr.refresh()
                self._device.disconnect()
                self._loop.call_soon_threadsafe(self._loop.stop)
                sys.exit()
            elif key == "STANDBY":
                self._write_str(key, 3)
                self.stdscr.refresh()
                self._device.session.standby()
                self._loop.call_soon_threadsafe(self._loop.stop)
                sys.exit()
            self.controller.button(key, "press")
        return key
