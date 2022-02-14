# pylint: disable=c-extension-no-member,invalid-name
"""Main Window for pyremoteplay GUI."""
import asyncio
import logging
import sys
import time

from PySide6 import QtCore, QtWidgets
from PySide6.QtCore import Qt  # pylint: disable=no-name-in-module

from pyremoteplay.__version__ import VERSION
from pyremoteplay.protocol import async_create_ddp_endpoint

from .device_grid import DeviceGridWidget
from .options import OptionsWidget
from .controls import ControlsWidget
from .stream_window import RPWorker, StreamWindow
from .toolbar import ToolbarWidget
from .util import message

_LOGGER = logging.getLogger(__name__)


class AsyncHandler(QtCore.QObject):
    """Handler for async methods."""

    status_updated = QtCore.Signal()

    def __init__(self, main_window):
        super().__init__()
        self.loop = None
        self.protocol = None
        self.main_window = main_window
        self.__task = None

    def start(self):
        """Start and run polling."""
        if sys.platform == "win32":
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        self.loop = asyncio.new_event_loop()
        self.__task = self.loop.create_task(self.run())
        self.loop.run_until_complete(self.__task)
        self.loop.run_forever()

    def poll(self):
        """Start polling."""
        self.protocol.start()

    def stop_poll(self):
        """Stop Polling."""
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

    async def run(self):
        """Start poll service."""
        self.protocol = await async_create_ddp_endpoint(self.status_updated.emit)
        await self.protocol.run()


class MainWindow(QtWidgets.QMainWindow):
    """Main Window."""

    session_finished = QtCore.Signal()

    def __init__(self, app):
        super().__init__()
        self._app = app
        self.screen = self._app.primaryScreen()
        self.idle = True
        self.toolbar = None
        self.device_grid = None
        self.async_handler = AsyncHandler(self)
        self.async_handler.status_updated.connect(self.event_status_updated)
        self.async_thread = QtCore.QThread()
        self.async_handler.moveToThread(self.async_thread)
        self.async_thread.started.connect(self.async_handler.start)
        self.async_thread.start()
        self._stream_window = None
        self._init_window()
        self._init_rp_worker()

    def _init_window(self):
        self.setWindowTitle("PyRemotePlay")
        self.main_frame = QtWidgets.QWidget(self)
        self.main_frame.layout = QtWidgets.QVBoxLayout(self.main_frame)
        self.center_text = QtWidgets.QLabel(
            "Searching for devices...", alignment=Qt.AlignCenter
        )
        self.center_text.setWordWrap(True)
        self.center_text.setObjectName("center-text")
        self.device_grid = DeviceGridWidget(self.main_frame, self)
        self.toolbar = ToolbarWidget(self)
        self.options = OptionsWidget(self)
        self.controls = ControlsWidget(self)
        self.addToolBar(self.toolbar)
        self.setStatusBar(QtWidgets.QStatusBar())
        self.statusBar().showMessage(f"v{VERSION}")
        self.main_frame.layout.addWidget(self.center_text)
        self.main_frame.layout.addWidget(self.device_grid)
        widget = QtWidgets.QStackedWidget()
        widget.addWidget(self.main_frame)
        widget.addWidget(self.options)
        widget.addWidget(self.controls)
        self.setCentralWidget(widget)
        self._set_style()
        self.toolbar.refresh.setChecked(True)
        self.device_grid.start_update()
        self.session_finished.connect(self.check_error_finish)
        QtCore.QTimer.singleShot(7000, self._startup_check_grid)

    def _init_rp_worker(self):
        self.rp_worker = RPWorker(self)
        self.rp_worker.moveToThread(self.async_thread)

    def _startup_check_grid(self):
        if not self.device_grid.widgets:
            self._set_center_text(
                "No Devices Found.\n" "Try adding a device in options."
            )

    def _set_center_text(self, text):
        self.center_text.setText(text)
        self.center_text.show()

    def _set_style(self):
        style = (
            "QPushButton {border: 1px solid #0a58ca;border-radius: 10px;padding: 10px;}"
            "QPushButton:hover {background-color:#6ea8fe;color:black;}"
            "QPushButton:pressed {background-color:#0a58ca;color:white;}"
            "QPushButton:checked {background-color:#0D6EFD;color:white;}"
            "#center-text {font-size: 24px;}"
        )
        self.setStyleSheet(style)

    def event_status_updated(self):
        """Callback for status updates."""
        devices = self.async_handler.protocol.devices
        self.device_grid.create_grid(devices)

    def check_profile(self, name, device):
        """Return profile if profile is registered."""
        if not self.options.profiles:
            message(
                self,
                "Error: No PSN Accounts found",
                "Click 'Options' -> 'Add Account' to add PSN Account.",
            )
            self.device_grid.enable_buttons()
            return None
        profile = self.options.profiles.get(name)
        if not profile:
            message(
                self,
                "Error: No PSN Account Selected.",
                "Click 'Options' -> and select a PSN Account.",
            )
            self.device_grid.enable_buttons()
            return None
        if device.mac_address not in profile["hosts"]:
            text = (
                f"PSN account: {name} has not been registered with this device. "
                "Click 'Ok' to register."
            )
            message(
                self,
                "Needs Registration",
                text,
                "info",
                callback=lambda: self.options.register(device.status, name),
                escape=True,
            )
            self.device_grid.enable_buttons()
            return None
        return profile

    def standby_host(self, device):
        """Place host in standby mode."""
        user = self.options.options.get("profile")
        options = self.options.options_data
        profile = self.check_profile(user, device)
        if not profile:
            return
        self.rp_worker.setup(None, device, user, options)
        self.rp_worker.run(standby=True)

    def standby_callback(self, host):
        """Callback after attempting standby."""
        if self.rp_worker.error:
            message(self, "Standby Error", self.rp_worker.error)
        else:
            message(self, "Standby Success", f"Set device at {host} to Standby", "info")

    def wakeup_host(self, device):
        """Wakeup Host."""
        user = self.options.options.get("profile")
        profile = self.check_profile(user, device)
        if not profile:
            return
        device.wakeup(user)
        message(
            self,
            "Wakeup Sent",
            f"Sent Wakeup command to device at {device.host}",
            "info",
        )

    def connect_host(self, device):
        """Connect to Host."""
        options = self.options.options
        user = options.get("profile")
        profile = self.check_profile(user, device)
        if not profile:
            return
        self.device_grid.setEnabled(False)
        self.device_grid.stop_update()
        audio_device = self.options.get_audio_device()
        self._stream_window = StreamWindow(self)
        self._stream_window.start(
            device,
            user,
            options,
            audio_device=audio_device,
            input_map=self.controls.get_map(),
            input_options=self.controls.get_options(),
        )
        self._app.setActiveWindow(self._stream_window)

    def session_start(self):
        """Start Session."""
        self.rp_worker.run()

    def session_stop(self):
        """Callback for stopping session."""
        _LOGGER.debug("Detected Session Stop")
        self.rp_worker.stop()
        self._app.setActiveWindow(self)
        self.device_grid.session_stop()
        if self._stream_window:
            self._stream_window.hide()
            self._stream_window = None
            self.session_finished.emit()

    def add_devices(self, devices):
        """Add devices to grid."""
        for host in devices:
            if host not in self.async_handler.protocol.devices:
                self.async_handler.protocol.add_device(host)

    def remove_device(self, host):
        """Remove Device from grid."""
        self.async_handler.protocol.remove_device(host)
        self.event_status_updated()

    def check_error_finish(self):
        """Display error message if session finished with error."""
        if self.rp_worker.error:
            message(self, "Error", self.rp_worker.error)
        self.rp_worker.error = ""

    def closeEvent(self, event):
        """Close Event."""
        self.device_grid.stop_update()
        if self._stream_window:
            self._stream_window.close()
        self.hide()
        self.async_handler.shutdown()
        self.async_thread.quit()
        event.accept()
