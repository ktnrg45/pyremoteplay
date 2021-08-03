"""Main methods for pyyremoteplay"""

import argparse
import asyncio
import curses
import logging
import sys
import threading
from collections import OrderedDict

from pyps4_2ndscreen.ddp import get_status

from .oauth import prompt as oauth_prompt
from .register import register
from .session import Session, SessionAsync
from .util import add_profile, add_regist_data, get_profiles, write_profiles

NEW_PROFILE = "New Profile"
CANCEL = "Cancel"
RESOLUTIONS = ["360p", "540p", "720p", "1080p"]
FPS_CHOICES = ["high", "low", "60", "30"]
logging.basicConfig(level=logging.WARNING)
_LOGGER = logging.getLogger(__name__)


def main():
    """Main entrypoint."""
    parser = argparse.ArgumentParser(description='Start Remote Play.')
    parser.add_argument('host', type=str, help="IP address of Remote Play host")
    parser.add_argument('-r', '--resolution', default="720p", type=str, choices=RESOLUTIONS, help="Resolution to use")
    parser.add_argument('-f', '--fps', default="high", type=str, choices=FPS_CHOICES, help="Max FPS to use")
    parser.add_argument('--register', action="store_true", help='Register with Remote Play host.')

    args = parser.parse_args()
    host = args.host

    should_register = args.register
    resolution = args.resolution
    fps = args.fps
    if fps.isnumeric():
        fps = int(fps)

    if should_register:
        register_profile(host)
        return
    cli(host, resolution, fps)


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
    if status.get("status_code") != 200:
        return
    mac_address = status.get("host-id")

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


def cli(host: str, resolution: str, fps: str):
    profiles = get_profiles()
    if profiles:
        name = select_profile(profiles, True, False)
        profile = profiles[name]
        session = SessionAsync(host, profile, resolution=resolution, fps=fps)
        async_start(session, cb_curses)
    else:
        _LOGGER.info("No Profiles")


def cb_curses(session, status: bool):
    if status:
        instance = CLIInstance(session)
        worker = threading.Thread(
            target=curses.wrapper,
            args=(start, instance)
        )
        worker.start()
    else:
        _LOGGER.error("Session Failed to Start: %s", session.error)


def async_start(session, cb: callable):
    loop = asyncio.get_event_loop()
    session.loop = loop
    task = loop.create_task(async_start_session(session, cb))
    loop.run_until_complete(task)
    loop.run_forever()


async def async_start_session(session, cb: callable):
    status = await session.start()
    if status:
        cb(session, status)
    else:
        asyncio.get_event_loop().stop()


def start(stdscr, instance):
    """Start Instance."""
    instance.run(stdscr)


class CLIInstance():
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

    def __init__(self, session: Session):
        self._session = session
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
        self.stdscr.addstr('\n')
        _key_mapping = OrderedDict()
        _key_mapping.update({'Key': 'Action'})
        _key_mapping.update(self.map)
        item = 0
        for key, value in _key_mapping.items():
            if key == '\n':
                key = 'KEY_ENTER'
            self.stdscr.addstr(key, curses.color_pair(5))
            self.stdscr.addstr(' : ')
            self.stdscr.addstr(value, curses.color_pair(4))
            item += 1
            if item >= 4:
                item = 0
                self.stdscr.addstr('\n')
            else:
                self.stdscr.addstr(' | ')
        self.stdscr.addstr('\n\n')
        self._pos = self.stdscr.getyx()
        self.stdscr.clrtobot()
        self.stdscr.refresh()

    def _init_window(self):
        win_size = self.stdscr.getmaxyx()
        self.stdscr.setscrreg(self.stdscr.getyx()[0], win_size[0] - 1)
        self.stdscr.clear()
        self.stdscr.refresh()
        self.stdscr.addstr(
            0, 0,
            "Remote Play - Interactive mode, press 'q' to exit\n",
        )
        self._show_mapping()

    def run(self, stdscr):
        """Run CLI Instance."""
        self.stdscr = stdscr
        self._init_color()
        self.stdscr.scrollok(True)
        self._init_window()
        timeout = 50
        self.stdscr.timeout(timeout)
        while not self._session.state == Session.STATE_STOP:
            self.stdscr.refresh()
            try:
                self._handle_key(self.stdscr.getkey())
            except curses.error:
                if self.last_key is not None:
                    self._session.controller.button(self.last_key, "release")
                    self.last_key = None

    def _write_str(self, text, color=1):
        self.stdscr.move(self._pos[0], self._pos[1])
        self.stdscr.clrtobot()
        self.stdscr.addstr(text, curses.color_pair(color))
        self.stdscr.move(self._pos[0] + 1, self._pos[1])

    def _handle_key(self, key):
        key = self.map.get(key)
        if key and self.last_key is None:
            self._write_str(key, 5)
            self.last_key = key
            self._session.controller.button(key, "press")
            if key == "QUIT":
                self._session.stop()
                self._session.loop.stop()
                sys.exit()
            elif key == "STANDBY":
                self._session.standby()
                self._session.loop.stop()
                sys.exit()
