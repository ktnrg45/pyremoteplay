"""Example of using gamepad.

We are assuming that we have connected to a session like in 'client.py'
"""

from pyremoteplay import RPDevice
from pyremoteplay.gamepad import Gamepad

ip_address = "192.168.0.2"
device = RPDevice(ip_address)
gamepads = Gamepad.get_all()
gamepad = gamepads[0]  # Use first gamepad

###########
# After connecting to device session.
###########

if not gamepad.available:
    print("Gamepad not available")
gamepad.controller = device.controller

# We can now use the gamepad.

# Load custom mapping.
gamepad.load_map("path-to-mapping.yaml")


# When done using
gamepad.close()
