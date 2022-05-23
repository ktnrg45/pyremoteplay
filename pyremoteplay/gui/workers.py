# pylint: disable=c-extension-no-member,invalid-name
"""Workers for GUI."""
from __future__ import annotations

import asyncio
import logging
import sys
import time


from PySide6 import QtCore
from pyremoteplay.device import RPDevice
from pyremoteplay.protocol import async_create_ddp_endpoint
from pyremoteplay.ddp import async_get_status

_LOGGER = logging.getLogger(__name__)


class RPWorker(QtCore.QObject):
    """Worker to interface with RP Session."""

    finished = QtCore.Signal()
    started = QtCore.Signal()
    standby_done = QtCore.Signal(str)

    def __init__(self):
        super().__init__()
        self._loop = None

    def setLoop(self, loop: asyncio.AbstractEventLoop):
        """Set Loop."""
        self._loop = loop

    def run(self, device: RPDevice):
        """Run Session."""
        if not device:
            _LOGGER.warning("No Device")
            self.stop()
            return
        if not device.session:
            _LOGGER.warning("No Session")
            self.stop()
            return
        device.session.events.on("stop", self.stop)
        self._loop.create_task(self.start(device))

    def stop(self):
        """Stop session."""
        self.finished.emit()

    async def start(self, device: RPDevice):
        """Start Session."""
        _LOGGER.debug("Session Start")
        started = await device.connect()

        if not started:
            _LOGGER.warning("Session Failed to Start")
            self.stop()
            return

        device.controller.start()
        self.started.emit()

        if device.session.stop_event:
            await device.session.stop_event.wait()
            _LOGGER.info("Session Finished")

    def send_stick(
        self,
        device: RPDevice,
        stick: str,
        point: QtCore.QPointF,
    ):
        """Send stick state"""
        if not device or not device.controller:
            return
        device.controller.stick(stick, point=(point.x(), point.y()))

    def send_button(self, device: RPDevice, button, action):
        """Send button."""
        if not device or not device.controller:
            return
        device.controller.button(button, action)

    async def standby(self, device: RPDevice, user: str):
        """Place Device in standby."""
        await device.standby(user)
        self.standby_done.emit(device.session.error)

    @property
    def loop(self) -> asyncio.AbstractEventLoop:
        """Return Loop."""
        return self._loop


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
