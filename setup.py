#!/usr/bin/env python
"""Setup for pyremoteplay."""
import sys
from pathlib import Path
import logging
import subprocess
from setuptools import find_packages, setup, Extension

ARG_BUILD = "--build"
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


def get_include_path():
    """Find default gcc include path.

    jerasure.h expects galois.h to be in the same directory so we need to specify the header directory.
    """
    default = "/usr/include/jerasure"
    try:
        process = subprocess.run(
            ["gcc", "-E", "-Wp," "-v", "-xc", "/dev/null"],
            check=True,
            timeout=1,
            capture_output=True,
        )
    except Exception:
        return default
    output = process.stderr
    if not output:
        return default
    lines = output.decode().splitlines()
    for line in lines:
        path = Path(line.lstrip())
        if path.is_dir():
            include_path = path / "jerasure"
            if include_path.is_dir():
                return str(include_path)
    return default


def build_extensions():
    """Return list of built extensions."""
    modules = []
    include_path = get_include_path()
    if ARG_BUILD in sys.argv:
        sys.argv.remove(ARG_BUILD)
        try:
            # pylint: disable=import-outside-toplevel
            from Cython.Build import (
                cythonize,
            )

            modules = cythonize(
                [
                    Extension(
                        "pyremoteplay.fec_utils.fec",
                        ["pyremoteplay/fec_utils/fec.pyx"],
                        include_dirs=[include_path],
                        libraries=["Jerasure"],
                    )
                ]
            )
        except ModuleNotFoundError:
            pass
    modules = [
        Extension(
            "pyremoteplay.fec_utils.fec",
            ["pyremoteplay/fec_utils/fec.c"],
            include_dirs=[include_path],
            libraries=["Jerasure"],
        )
    ]
    return modules


def setup_install(kwargs):
    """Setup and install."""
    ext_modules = build_extensions()
    if ext_modules:
        try:
            setup(ext_modules=ext_modules, zip_safe=False, **kwargs)
            return
        except SystemExit:
            logging.warning("Failed building optional extensions. Skipping...")

    setup(**kwargs)


setup_kwargs = {
    "name": "pyremoteplay",
    "version": VERSION,
    "description": "Remote Play Library and API",
    "long_description": README,
    "long_description_content_type": "text/markdown",
    "author": "ktnrg45",
    "author_email": "ktnrg45dev@gmail.com",
    "packages": find_packages(exclude=["tests"]),
    "url": "https://github.com/ktnrg45/pyremoteplay",
    "license": "GPLv3",
    "classifiers": CLASSIFIERS,
    "keywords": "playstation sony ps4 ps5 remote play remoteplay rp",
    "setup_requires": ["wheel"],
    "install_requires": REQUIRES,
    "extras_require": {"GUI": REQUIRES_GUI, "DEV": REQUIRES_DEV},
    "python_requires": ">={}".format(MIN_PY_VERSION),
    "test_suite": "tests",
    "include_package_data": True,
    "entry_points": {
        "console_scripts": [
            "pyremoteplay = pyremoteplay.__main__:main",
        ],
        "gui_scripts": [
            "pyremoteplay-gui = pyremoteplay.gui.__main__:main [GUI]",
        ],
    },
}

setup_install(setup_kwargs)
