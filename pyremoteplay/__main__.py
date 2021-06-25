"""Main methods for pyyremoteplay"""

import argparse
import curses
import json
import logging
import pathlib
import time
from collections import OrderedDict
from enum import IntEnum

from .ctrl import CTRL
from .register import register
from .stream_packets import FeedbackHeader

PROFILE_DIR = ".pyremoteplay"
PROFILE_FILE = ".profile.json"

logging.basicConfig(level=logging.WARNING)
_LOGGER = logging.getLogger(__name__)


def cli():
    parser = argparse.ArgumentParser(description='Start Remote Play.')
    parser.add_argument('host', type=str, help="IP address of Remote Play host")
    parser.add_argument('--profile', type=str, help='Path to profile config.')
    args = parser.parse_args()
    host = args.host
    profile = args.profile
    profiles = get_profiles(profile)
    if profiles:
        data = profiles[0]
        ctrl = CTRL(host, data)
        if not ctrl.start():
            return
        instance = CLIInstance(ctrl)
        ctrl.controller_ready_event.wait(5)
        curses.wrapper(start, instance)
    else:
        _LOGGER.info("No Profiles")


def start(stdscr, instance):
    """Start Instance."""
    instance.run(stdscr)


def get_profiles(path=None) -> list:
    data = []
    if not path:
        path = pathlib.Path.home() / PROFILE_DIR / PROFILE_FILE
    else:
        path = pathlib.Path(path)
    if not path.is_file():
        _LOGGER.error("File not found")
        return data
    with open(path, "r") as f:
        data = json.load(f)
    return data


class CLIInstance():
    """Emulated Keyboard."""

    MAP = {
        "Q": "STANDBY",
        "q": "QUIT",
        "KEY_UP": "UP",
        "KEY_DOWN": "DOWN",
        "KEY_LEFT": "LEFT",
        "KEY_RIGHT": "RIGHT",
        "h": "L1",
        "j": "R1",
        "k": "L2",
        "l": "R2",
        "\n": "CROSS",
        "c": "CIRCLE",
        "r": "SQUARE",
        "t": "TRIANGLE",
        "KEY_BACKSPACE": "OPTIONS",
        "=": "SHARE",
        "p": "PS",
        "n": "L3",
        "m": "R3",
        "y": "TOUCHPAD",
    }

    def __init__(self, ctrl: CTRL):
        self._ctrl = ctrl
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
        while not self._ctrl.state == CTRL.STATE_STOP:
            self.stdscr.refresh()
            try:
                self._handle_key(self.stdscr.getkey())
            except curses.error:
                if self.last_key is not None:
                    self._ctrl.controller.button(self.last_key, "release")
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
            self._ctrl.controller.button(key, "press")
            if key == "QUIT":
                self._ctrl.stop()
            elif key == "STANDBY":
                self._ctrl.standby()
