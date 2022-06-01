# pylint: disable=c-extension-no-member,invalid-name
"""Main Window for pyremoteplay GUI."""
from __future__ import annotations
import logging

from PySide6 import QtCore, QtWidgets
from PySide6.QtCore import Qt  # pylint: disable=no-name-in-module

from pyremoteplay.__version__ import VERSION
from pyremoteplay.device import RPDevice

from .device_grid import DeviceGridWidget
from .options import OptionsWidget
from .controls import ControlsWidget
from .stream_window import StreamWindow
from .toolbar import ToolbarWidget
from .util import message
from .workers import AsyncHandler

_LOGGER = logging.getLogger(__name__)


class MainWindow(QtWidgets.QMainWindow):
    """Main Window."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("PyRemotePlay")
        self._toolbar = None
        self._device_grid = None
        self.async_handler = AsyncHandler()
        self._stream_window = None
        self.rp_worker = self.async_handler.rp_worker

        self._center_text = QtWidgets.QLabel(
            "Searching for devices...", alignment=Qt.AlignCenter
        )
        self._center_text.setWordWrap(True)
        self._center_text.setObjectName("center-text")
        self._device_grid = DeviceGridWidget()
        self._device_grid.hide()

        self._main_frame = QtWidgets.QWidget(self)
        self._main_frame.setLayout(QtWidgets.QVBoxLayout())
        self._main_frame.layout().addWidget(self._center_text)
        self._main_frame.layout().addWidget(self._device_grid)

        self._toolbar = ToolbarWidget(self)
        self._options = OptionsWidget(self)
        self._controls = ControlsWidget(self)
        self.addToolBar(self._toolbar)
        self.setStatusBar(QtWidgets.QStatusBar())
        self.statusBar().showMessage(f"v{VERSION}")

        widget = QtWidgets.QStackedWidget()
        widget.addWidget(self._main_frame)
        widget.addWidget(self._options)
        widget.addWidget(self._controls)
        self.setCentralWidget(widget)
        self._set_style()

        self.async_handler.rp_worker.standby_done.connect(self._standby_callback)
        self.async_handler.status_updated.connect(self._event_status_updated)
        self.async_handler.manual_search_done.connect(self._options.search_complete)
        self._toolbar.buttonClicked.connect(self._toolbar_button_clicked)
        self._device_grid.power_toggled.connect(self._power_toggle)
        self._device_grid.connect_requested.connect(self.connect_host)
        self._device_grid.devices_available.connect(self._devices_available)
        self._options.search_requested.connect(self._manual_search)
        self._options.device_added.connect(self.add_devices)
        self._options.device_removed.connect(self.remove_device)
        self._options.register_finished.connect(self.session_stop)

        self.add_devices()

        self._toolbar.refresh().setChecked(True)
        self._start_update()
        QtCore.QTimer.singleShot(7000, self._startup_check_grid)

    def closeEvent(self, event):
        """Close Event."""
        self._stop_update()
        if self._stream_window:
            self._stream_window.close()
        self.hide()
        self.async_handler.shutdown()
        event.accept()

    def wakeup(self, device):
        """Wakeup Host."""
        user = self._options.options.get("profile")
        profile = self.check_profile(user, device)
        if not profile:
            return
        device.wakeup(user=user)
        message(
            self,
            "Wakeup Sent",
            f"Sent Wakeup command to device at {device.host}",
            "info",
        )

    def connect_host(self, device: RPDevice):
        """Connect to Host."""
        options = self._options.options_data
        user = options.profile
        profile = self.check_profile(user, device)
        if not profile:
            return
        self._device_grid.setEnabled(False)
        self._stop_update()
        audio_device = self._options.get_audio_device()
        gamepad = None
        if self._controls.use_gamepad():
            gamepad = self._controls.get_gamepad()
        self._stream_window = StreamWindow(
            self.rp_worker,
            device,
            options,
            audio_device,
            self._controls.get_keyboard_map(),
            self._controls.get_keyboard_options(),
            gamepad,
        )
        self._stream_window.started.connect(self.session_start)
        self._stream_window.stopped.connect(self.session_stop)
        self._stream_window.start()
        QtWidgets.QApplication.instance().setActiveWindow(self._stream_window)

    def session_start(self, device: RPDevice):
        """Start Session."""
        self.rp_worker.run(device)

    def session_stop(self, error: str = ""):
        """Callback for stopping session."""
        _LOGGER.debug("Detected Session Stop")
        self.rp_worker.stop()

        QtWidgets.QApplication.instance().setActiveWindow(self)

        if self._toolbar.refresh().isChecked():
            self._start_update()
        self._device_grid.setDisabled(False)
        QtCore.QTimer.singleShot(10000, self._device_grid.enable_buttons)

        if self._stream_window:
            self._stream_window.hide()
            self._stream_window = None
        if error:
            message(self, "Error", error)

    @QtCore.Slot()
    def add_devices(self):
        """Add devices to grid."""
        for host in self._options.devices:
            if host not in self.async_handler.protocol.devices:
                self.async_handler.protocol.add_device(host)

    @QtCore.Slot(str)
    def remove_device(self, host: str):
        """Remove Device from grid."""
        self.async_handler.protocol.remove_device(host)
        self._event_status_updated()

    def standby(self, device: RPDevice):
        """Place host in standby mode."""
        options = self._options.options
        user = options.get("profile")
        profile = self.check_profile(user, device)
        if not profile:
            return
        self.async_handler.standby(device, user)

    @QtCore.Slot(QtWidgets.QPushButton)
    def _toolbar_button_clicked(self, button):
        if button == self._toolbar.home():
            self.centralWidget().setCurrentWidget(self._main_frame)
        elif button == self._toolbar.options():
            self.centralWidget().setCurrentWidget(self._options)
        elif button == self._toolbar.controls():
            self.centralWidget().setCurrentWidget(self._controls)
        elif button == self._toolbar.refresh():
            if button.isChecked():
                self._start_update()
            else:
                self._stop_update()

    def _devices_available(self):
        self._center_text.hide()
        self._device_grid.show()

    def _startup_check_grid(self):
        if not self._device_grid.buttons():
            self._center_text.setText(
                "No Devices Found.\n" "Try adding a device in options."
            )

    def _set_style(self):
        style = (
            "QPushButton {border: 1px solid #0a58ca;border-radius: 10px;padding: 10px;margin: 5px;}"
            "QPushButton:hover {background-color:#6ea8fe;color:black;}"
            "QPushButton:pressed {background-color:#0a58ca;color:white;}"
            "QPushButton:checked {background-color:#0D6EFD;color:white;}"
            "#center-text {font-size: 24px;}"
        )
        self.setStyleSheet(style)

    def _event_status_updated(self):
        """Callback for status updates."""
        devices = self.async_handler.protocol.devices
        self._device_grid.create_grid(devices)

    def check_profile(self, name, device):
        """Return profile if profile is registered."""
        if not self._options.profiles:
            message(
                self,
                "Error: No PSN Accounts found",
                "Click 'Options' -> 'Add Account' to add PSN Account.",
            )
            self._device_grid.enable_buttons()
            return None
        profile = self._options.profiles.get(name)
        if not profile:
            message(
                self,
                "Error: No PSN Account Selected.",
                "Click 'Options' -> and select a PSN Account.",
            )
            self._device_grid.enable_buttons()
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
                callback=lambda: self._options.register(device.status, name),
                escape=True,
            )
            self._device_grid.enable_buttons()
            return None
        return profile

    @QtCore.Slot(str)
    def _standby_callback(self, error: str):
        """Callback after attempting standby."""
        if error:
            message(self, "Standby Error", error)
        else:
            message(self, "Standby Success", "Set device to Standby", "info")

    def _power_toggle(self, device: RPDevice):
        if device.is_on:
            self.standby(device)
        else:
            self.wakeup(device)

    def _start_update(self):
        """Start update service."""
        self.async_handler.poll()

    def _stop_update(self):
        """Stop Updatw Service."""
        self.async_handler.stop_poll()

    @QtCore.Slot(str)
    def _manual_search(self, host: str):
        self.async_handler.manual_search(host)
