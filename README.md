# pyremoteplay #
Python PlayStation Remote Play API

## About ##
This project provides an API to programmatically connect to and control Remote Play hosts (Only PS4 Currently). In addition it includes an optional GUI, allowing to view the live stream and control the host through keyboard/mouse input. This library is based on the C/C++ project [Chiaki](https://github.com/thestr4ng3r/chiaki).

## Features ##
- Registering client for Remote Play on the host 
- Interface for controlling the host, which emulates a DualShock controller
- Ability to power off/on the host if standby is enabled
- GUI which displays the live stream and supports keyboard/mouse input

## Requirements ##
- Python 3.8+
- OS: Linux, Windows 10

## GUI Dependencies ##
The GUI requires dependencies that may be complex to install.
Below is a list of such dependencies.
- pyav (May require FFMPEG to be installed)
- opuslib (Requires libopus to be installed)
- PySide6

## Installation ##
It is recommended to install in a virtual environment.

```
  python3 -m venv .
  source bin/activate
```

### From pip ###
To install core package run:
```
pip install pyremoteplay
```

To install with optional GUI run:
```
pip install pyremoteplay[gui]
```

### From Source ###
To Install from source, clone this repo and navigate to the top level directory.

```
  pip install -r requirements.txt
  python setup.py install
```

To Install GUI dependencies run:
```
  pip install -r requirements-gui.txt
```

## Setup ##
There are some steps that must be completed to use this library from a user standpoint.
- Registering a PSN Account
- Linking PSN Account and client to the Remote Play Host

Configuration files are saved in the `.pyremoteplay` folder in the users home directory. Both the CLI and GUI utilize the same files.

### CLI Setup ###
Registering and linking can be completed through the cli by following the prompts after using the below command:

`pyremoteplay {host IP Address} --register`

Replace `{host IP Address}` with the IP Address of the Remote Play host.

### GUI Setup ###
Registering and linking can be performed in the options screen.

## Usage ##
To run the terminal only CLI use the following command:
`pyremoteplay {host IP Address}`

To run the GUI use the following command:
`pyremoteplay-gui`

## Notes ##
- Video decoding is performed by the CPU by default. Hardware Acceleration can be enabled in the options screen in the GUI.

## Baseline measurements ##
The CLI instance runs at 5-10% CPU usage with around 50Mb memory usage according to `top` on this author's machine: ODroid N2.

## Known Issues/To Do ##
- Text sending functions
- Congestion Control
- Add support for PS5. (Don't have one yet)
- Implement audio stream for GUI
