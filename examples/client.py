"""Example of running client. We are assuming that we have already linked a PSN profile to our Remote Play device."""

import asyncio
import threading
import atexit
from pyremoteplay import RPDevice
from pyremoteplay.receiver import QueueReceiver


def stop(device, thread):
    loop = device.session.loop
    device.disconnect()
    loop.stop()
    thread.join(3)
    print("stopped")


def worker(device, user):
    loop = asyncio.new_event_loop()
    receiver = QueueReceiver()
    device.create_session(user, receiver=receiver, loop=loop)
    task = loop.create_task(device.connect())
    loop.run_until_complete(task)
    loop.run_forever()


def start(ip_address):
    """Return device. Start Remote Play session."""
    device = RPDevice(ip_address)
    if not device.get_status():  # Device needs a valid status to get users
        print("No Status")
        return None
    users = device.get_users()
    if not users:
        print("No users registered")
        return None
    user = users[0]  # Gets first user name
    thread = threading.Thread(target=worker, args=(device, user), daemon=True)
    thread.start()
    atexit.register(
        lambda: stop(device, thread)
    )  # Make sure we stop the thread on exit.
    return device


# Usage:
#
# Starting session:
# >> ip_address = '192.168.86.2' # ip address of Remote Play device
# >> device = start(ip_address)
#
# Retrieving latest video frames:
# >> device.session.receiver.video_frames
#
# Tap Controller Button:
# >> device.controller.button("cross", "tap")
#
# Start Controller Stick Worker
# >> device.controller.start()
#
# Emulate moving Left Stick all the way right:
# >> device.controller.stick("left", axis="x", value=1.0)
#
# Release Left stick:
# >> device.controller.stick("left", axis="x", value=0)
#
# Move Left stick diagonally left and down halfway
# >> device.controller.stick("left", point=(-0.5, 0.5))
#