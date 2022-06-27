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

from .ddp import search
from .oauth import prompt as oauth_prompt
from .profile import Profiles, format_user_account
from .device import RPDevice
from .__version__ import VERSION

NEW_PROFILE = "New Profile"
CANCEL = "Cancel"
# RESOLUTIONS = ["360p", "540p", "720p", "1080p"]
# FPS_CHOICES = ["high", "low", "60", "30"]
_LOGGER = logging.getLogger(__name__)


def main():
    """Main entrypoint."""
    if "-l" in sys.argv or "--list" in sys.argv:
        show_devices()
        return
    if "-v" in sys.argv or "--version" in sys.argv:
        print(VERSION)
        return
    parser = argparse.ArgumentParser(description="Start Remote Play.")
    parser.add_argument(
        "host", type=str, default="", help="IP address of Remote Play host"
    )

    parser.add_argument(
        "-r", "--register", action="store_true", help="Register with Remote Play host"
    )

    parser.add_argument(
        "-t",
        "--test",
        action="store_true",
        help="Test connecting to device with verbose logging",
    )

    ######## Just to show in help
    parser.add_argument(
        "-l",
        "--list",
        action="store_true",
        help="List Devices",
    )

    parser.add_argument(
        "-v",
        "--version",
        action="store_true",
        help="Print Version",
    )
    ########

    args = parser.parse_args()
    host = args.host
    should_register = args.register
    test = args.test

    level = logging.DEBUG if test else logging.WARNING
    logging.basicConfig(level=level)

    try:
        socket.gethostbyname(host)
    except socket.gaierror:
        print(f"\nError: Could not find host with address: {host}\n\n")
        parser.print_help()
        return
    device = RPDevice(host)
    if should_register:
        register_profile(device)
        return
    cli(device, test)


def show_devices():
    """Print all devices."""
    print("\nSearching for devices...")
    devices = search()
    print(f"\nFound {len(devices)} devices:\n")
    for device in devices:
        print(
            f"\tIP Address: {device.get('host-ip')}\n"
            f"\tName: {device.get('host-name')}\n"
            f"\tType: {device.get('host-type')}\n\n"
        )


def select_profile(profiles: Profiles, use_single: bool, get_new: bool) -> str:
    """Return profile name."""
    name = NEW_PROFILE
    names = profiles.usernames
    if names == 1 and use_single:
        name = names[0]
        return name
    print("\nFound Profiles")
    if get_new:
        names.append(NEW_PROFILE)
    names.append(CANCEL)
    prompt = ""
    for _index, item in enumerate(names):
        prompt = f"{prompt}{_index}: {item}\n"
    while True:
        try:
            index = int(input(f"Select a profile to use:\n{prompt}>> "))
        except (KeyboardInterrupt, EOFError):
            print("")
            sys.exit()
        try:
            name = names[index]
        except IndexError:
            print("Invalid Selection")
            continue
        if name == CANCEL:
            sys.exit()
        break
    print("")
    return name


def register_profile(device: RPDevice):
    """Register with host."""
    user = ""
    status = device.get_status()
    if not status:
        print("Host is not reachable")
        return
    if status.get("status-code") != 200:
        return

    profiles = RPDevice.get_profiles()
    if profiles:
        user = select_profile(profiles, False, True)
    if user == NEW_PROFILE or not profiles:
        try:
            account_data = oauth_prompt()
        except (KeyboardInterrupt, EOFError):
            print("")
            sys.exit()
        print("")
        if not account_data:
            print("Could not get PSN account data")
            sys.exit()
        user_profile = format_user_account(account_data)
        if user_profile is None:
            print("Error: Could not get user profile.")
            sys.exit()
        profiles.update_user(user_profile)
        profiles.save()
        user = user_profile.name
        print(f"PSN User: {user} added.\n\n")
    link_profile(device, user)


def link_profile(device: RPDevice, user: str):
    """Link User Profile with device."""
    profiles = RPDevice.get_profiles()
    user_profile = profiles.get_user_profile(user)
    if not user_profile:
        print(f"Profile not found for user: {user}")
        sys.exit()
    user_id = user_profile.id
    if not user_id:
        _LOGGER.error("No User ID")
        sys.exit()
    pin = ""
    while True:
        try:
            pin = input(
                f"On Remote Play host, Login to your PSN Account: {user}\n"
                "Then go to Settings -> "
                "Remote Play Connection Settings -> "
                "Add Device and enter the PIN shown\n>> "
            )
        except (KeyboardInterrupt, EOFError):
            print("")
            sys.exit()
        if pin.isnumeric() and len(pin) == 8:
            break
        print("Invalid PIN. PIN must be only 8 numbers\n")
    print("")

    regist_profile = device.register(user, pin)
    if not regist_profile:
        print("Error: Registering with host.")
        sys.exit()


def cli(device: RPDevice, test: bool = False):
    """Start CLI."""
    status = device.get_status()
    if not status:
        print(f"Could not reach host at: {device.host}")
        sys.exit()
    profiles = RPDevice.get_profiles()
    if profiles:
        user = select_profile(profiles, True, False)
        if user not in device.get_users():
            print(f"User: {user} not registered with this device.\n")
            link_profile(device, user)
        if test:
            print("Starting test...\n")
        setup_worker(device, user, test)
    else:
        try:
            selection = input("No Profiles Found. Enter 'Y' to create profile.\n>> ")
        except (KeyboardInterrupt, EOFError):
            print("")
            sys.exit()
        print("")
        if selection.upper() == "Y":
            register_profile(device)


def worker(device: RPDevice, user: str, event: threading.Event):
    """Worker."""
    loop = asyncio.new_event_loop()
    device.create_session(user, loop=loop)
    task = loop.create_task(async_start(device, event))
    atexit.register(loop.stop)
    loop.run_until_complete(task)
    loop.run_forever()


def setup_worker(device: RPDevice, user: str, test: bool):
    """Sync method for starting session."""
    event = threading.Event()
    thread = threading.Thread(target=worker, args=(device, user, event), daemon=True)
    thread.start()
    event.wait(timeout=5)
    if not test:
        curses.wrapper(start, device)
    else:
        result = "Pass" if device.session.is_ready else "Fail"
        loop = device.session.loop
        device.disconnect()
        loop.stop()
        print(f"\nTest Result: {result}\n")


async def async_start(device: RPDevice, event: threading.Event):
    """Start Session."""
    loop = asyncio.get_running_loop()
    started = await device.connect()
    if not started:
        loop.stop()
        print(f"Session Failed to Start: {device.session.error}")
    ready = await device.async_wait_for_session()
    if not ready:
        print("Timed out waiting for session to start")
    event.set()


def start(stdscr, device: RPDevice):
    """Start Instance."""
    instance = CLIInstance(device)
    if not device.session.is_ready:
        curses.endwin()
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
            except (KeyboardInterrupt, EOFError):
                self.quit()

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
                self.quit()
            elif key == "STANDBY":
                self._write_str(key, 3)
                self.stdscr.refresh()
                self._device.session.standby()
                self.quit()
                # self._loop.call_soon_threadsafe(self._loop.stop)
                # curses.endwin()
                # sys.exit()
            self.controller.button(key, "press")
        return key

    def quit(self):
        """Quit."""
        self._device.disconnect()
        self._loop.call_soon_threadsafe(self._loop.stop)
        curses.endwin()
        sys.exit()


if __name__ == "__main__":
    main()
