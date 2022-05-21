# pylint: disable=c-extension-no-member,invalid-name
"""Workers for GUI."""
from __future__ import annotations
from typing import TYPE_CHECKING
import asyncio
import logging
import sys
import time


from PySide6 import QtCore
from pyremoteplay.device import RPDevice
from pyremoteplay.protocol import async_create_ddp_endpoint
from pyremoteplay.ddp import async_get_status

if TYPE_CHECKING:
    from .stream_window import QtReceiver

_LOGGER = logging.getLogger(__name__)


class RPWorker(QtCore.QObject):
    """Worker to interface with RP Session."""

    finished = QtCore.Signal()
    started = QtCore.Signal()
    standby_done = QtCore.Signal(str)

    def __init__(self):
        super().__init__()
        self._loop = None
        self.device = None
        self.error = ""

    def setLoop(self, loop: asyncio.AbstractEventLoop):
        """Set Loop."""
        self._loop = loop

    def run(self, standby=False):
        """Run Session."""
        if not self.device:
            _LOGGER.warning("No Device")
            self.stop()
            return
        if not self.device.session:
            _LOGGER.warning("No Session")
            self.stop()
            return

        self.device.session.events.on("stop", self.stop)
        self._loop.create_task(self.start(standby))

    def stop(self, standby=False):
        """Stop session."""
        if self.device and self.device.session:
            self.error = self.device.session.error
            _LOGGER.info("Stopping Session @ %s", self.device.host)
            self.device.disconnect()
            if standby:
                self.standby_done.emit(self.error)
        self.device = None
        self.finished.emit()

    def setup(
        self,
        device: RPDevice,
        user: str,
        options: dict,
        receiver: QtReceiver,
    ):
        """Setup session."""
        self.device = device
        codec = options.get("codec")
        if not options.get("use_hw"):
            codec = codec.split("_")[0]
        hdr = options.get("hdr")
        if hdr and codec == "hevc":
            codec = "hevc_hdr"

        self.device.create_session(
            user,
            resolution=options.get("resolution"),
            fps=options.get("fps"),
            receiver=receiver,
            codec=codec,
            quality=options.get("quality"),
            loop=self._loop,
        )

    async def start(self, standby=False):
        """Start Session."""
        _LOGGER.debug("Session Start")
        if standby:
            self.device.session.receiver = None
        started = await self.device.connect()

        if not started:
            _LOGGER.warning("Session Failed to Start")
            self.stop()
            return

        if standby:
            result = await self.device.standby()
            _LOGGER.info("Standby Success: %s", result)
            self.stop(standby=True)
            return

        self.device.controller.start()
        self.started.emit()

        if self.device.session.stop_event:
            await self.device.session.stop_event.wait()
            _LOGGER.info("Session Finished")

    def stick_state(
        self, stick: str, direction: str = None, value: float = None, point=None
    ):
        """Send stick state"""
        if point is not None:
            self.device.controller.stick(stick, point=point)
            return

        if direction in ("LEFT", "RIGHT"):
            axis = "X"
        else:
            axis = "Y"
        if direction in ("UP", "LEFT") and value != 0.0:
            value *= -1.0
        self.device.controller.stick(stick, axis, value)

    def send_button(self, button, action):
        """Send button."""
        self.device.controller.button(button, action)

    async def standby(self, device: RPDevice, user: str):
        """Place Device in standby."""
        await device.standby(user)
        self.standby_done.emit(device.session.error)


class AsyncHandler(QtCore.QObject):
    """Handler for async methods."""

    status_updated = QtCore.Signal()
    manual_search_done = QtCore.Signal(str, dict)

    def __init__(self):
        super().__init__()
        self.loop = None
        self.protocol = None
        self.rp_worker = RPWorker()
        self.__task = None
        self._thread = QtCore.QThread()

        self.moveToThread(self._thread)
        self.rp_worker.moveToThread(self._thread)
        self._thread.started.connect(self.start)
        self._thread.start()

    def start(self):
        """Start and run polling."""
        if sys.platform == "win32":
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        self.loop = asyncio.new_event_loop()
        self.rp_worker.setLoop(self.loop)
        self.__task = self.loop.create_task(self.run())
        self.loop.run_until_complete(self.__task)
        self.loop.run_forever()

    def poll(self):
        """Start polling."""
        if self.protocol:
            self.protocol.start()

    def stop_poll(self):
        """Stop Polling."""
        if self.protocol:
            self.protocol.stop()

    def shutdown(self):
        """Shutdown handler."""
        self.stop_poll()
        self.protocol.shutdown()
        if self.__task is not None:
            self.__task.cancel()
        _LOGGER.debug("Shutting down async event loop")
        self.loop.stop()
        start = time.time()
        while self.loop.is_running():
            if time.time() - start > 5:
                break
        self._thread.quit()

    async def run(self):
        """Start poll service."""
        self.protocol = await async_create_ddp_endpoint(self.status_updated.emit)
        await self.protocol.run()

    async def _manual_search(self, host: str):
        """Search for device."""
        _LOGGER.info("Manual Search: %s", host)
        status = await async_get_status(host)
        self.manual_search_done.emit(host, status)

    def manual_search(self, host: str):
        """Search for device."""
        self.run_coro(self._manual_search, host)

    def run_coro(self, coro, *args, **kwargs):
        """Run coroutine."""
        asyncio.run_coroutine_threadsafe(coro(*args, **kwargs), self.loop)

    def standby(self, device: RPDevice, user: str):
        """Place host in standby."""
        self.run_coro(self.rp_worker.standby, device, user)
