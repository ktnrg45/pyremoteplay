"""Async Client Example.

This example is meant to be run as script.

We are assuming that we have already linked a PSN profile to our Remote Play device.
"""

import asyncio
import argparse

from pyremoteplay import RPDevice


async def task(device):
    """Task to run. This presses D-Pad buttons repeatedly."""
    buttons = ("LEFT", "RIGHT", "UP", "DOWN")

    # Wait for session to be ready.
    await device.async_wait_for_session()
    while device.connected:
        for button in buttons:
            await device.controller.async_button(button)
            await asyncio.sleep(1)
    print("Device disconnected")


async def get_user(device):
    """Return user."""
    if not await device.async_get_status():
        print("Could not get device status")
        return None
    users = device.get_users()
    if not users:
        print("No Users")
        return None
    user = users[0]
    return user


async def runner(host, standby):
    """Run client."""
    device = RPDevice(host)
    user = await get_user(device)
    if not user:
        return

    if standby:
        await device.standby(user)
        print("Device set to standby")
        return

    # If device is not on, Turn On and wait for a 'On' status
    if not device.is_on:
        device.wakeup(user)
        if not await device.async_wait_for_wakeup():
            print("Timed out waiting for device to wakeup")
            return

    device.create_session(user)
    if not await device.connect():
        print("Failed to start Session")
        return

    # Now that we have connected to session we can run our task.
    asyncio.create_task(task(device))

    # This is included to keep the asyncio loop running.
    while device.connected:
        try:
            await asyncio.sleep(0)
        except KeyboardInterrupt:
            device.disconnect()
            break


def main():
    parser = argparse.ArgumentParser(description="Async Remote Play Client.")
    parser.add_argument("host", type=str, help="IP address of Remote Play host")
    parser.add_argument(
        "-s", "--standby", action="store_true", help="Place host in standby"
    )
    args = parser.parse_args()
    host = args.host
    standby = args.standby
    loop = asyncio.get_event_loop()
    loop.run_until_complete(runner(host, standby))


if __name__ == "__main__":
    main()
