#!/usr/bin/env python
"""Setup for pyremoteplay."""
from pathlib import Path
from setuptools import setup

SRC_DIR = "pyremoteplay"
version_data = {}
version_path = Path.cwd() / SRC_DIR / "__version__.py"
with open(version_path, encoding="utf-8") as fp:
    exec(fp.read(), version_data)

VERSION = version_data["VERSION"]
MIN_PY_VERSION = version_data["MIN_PY_VERSION"]

REQUIRES = list(open("requirements.txt"))
REQUIRES_GUI = list(open("requirements-gui.txt"))
REQUIRES_DEV = list(open("requirements-dev.txt"))
REQUIRES_DEV.extend(REQUIRES_GUI)

CLASSIFIERS = [
    "Development Status :: 4 - Beta",
    "Environment :: Console",
    "Environment :: Console :: Curses",
    "License :: OSI Approved :: GNU General Public License v3 (GPLv3)",
    "Natural Language :: English",
    "Operating System :: OS Independent",
    "Programming Language :: Python :: 3.8",
    "Topic :: Games/Entertainment",
    "Topic :: Home Automation",
    "Topic :: Software Development :: Libraries :: Python Modules",
    "Topic :: System :: Hardware",
]

with open("README.md") as f:
    README = f.read()


setup_kwargs = {
    "name": "pyremoteplay",
    "version": VERSION,
    "description": "Remote Play Library and API",
    "long_description": README,
    "long_description_content_type": "text/markdown",
    "author": "ktnrg45",
    "author_email": "ktnrg45dev@gmail.com",
    "packages": [
        "pyremoteplay",
        "pyremoteplay.gui",
        "pyremoteplay.receiver",
        "pyremoteplay.gamepad",
    ],
    "url": "https://github.com/ktnrg45/pyremoteplay",
    "license": "GPLv3",
    "classifiers": CLASSIFIERS,
    "keywords": "playstation sony ps4 ps5 remote play remoteplay rp",
    "install_requires": REQUIRES,
    "extras_require": {"GUI": REQUIRES_GUI, "DEV": REQUIRES_DEV},
    "python_requires": ">={}".format(MIN_PY_VERSION),
    "test_suite": "tests",
    # "include_package_data": True,
    "entry_points": {
        "console_scripts": [
            "pyremoteplay = pyremoteplay.__main__:main",
        ],
        "gui_scripts": [
            "pyremoteplay-gui = pyremoteplay.gui.__main__:main [GUI]",
        ],
    },
}

setup(**setup_kwargs)
