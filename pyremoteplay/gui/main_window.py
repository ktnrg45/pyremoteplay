# pylint: disable=c-extension-no-member,invalid-name
"""Main Window for pyremoteplay GUI."""
import logging

from PySide6 import QtCore, QtWidgets
from PySide6.QtCore import Qt  # pylint: disable=no-name-in-module

from pyremoteplay.__version__ import VERSION

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
        self.idle = True
        self.toolbar = None
        self.device_grid = None
        self.async_handler = AsyncHandler()
        self._stream_window = None
        self.rp_worker = self.async_handler.rp_worker

        self._init_window()
        self.async_handler.rp_worker.standby_done.connect(self.standby_callback)
        self.async_handler.status_updated.connect(self.event_status_updated)
        self.toolbar.buttonClicked.connect(self._toolbar_button_clicked)

    def _init_window(self):
        self.setWindowTitle("PyRemotePlay")
        self.main_frame = QtWidgets.QWidget(self)
        self.main_frame.setLayout(QtWidgets.QVBoxLayout())
        self.center_text = QtWidgets.QLabel(
            "Searching for devices...", alignment=Qt.AlignCenter
        )
        self.center_text.setWordWrap(True)
        self.center_text.setObjectName("center-text")
        self.device_grid = DeviceGridWidget(self.main_frame)
        self.toolbar = ToolbarWidget(self)
        self.options = OptionsWidget(self)
        self.controls = ControlsWidget(self)
        self.addToolBar(self.toolbar)
        self.setStatusBar(QtWidgets.QStatusBar())
        self.statusBar().showMessage(f"v{VERSION}")
        self.main_frame.layout().addWidget(self.center_text)
        self.main_frame.layout().addWidget(self.device_grid)
        widget = QtWidgets.QStackedWidget()
        widget.addWidget(self.main_frame)
        widget.addWidget(self.options)
        widget.addWidget(self.controls)
        self.setCentralWidget(widget)
        self._set_style()
        self.device_grid.start_update()
        QtCore.QTimer.singleShot(7000, self._startup_check_grid)

    def _toolbar_button_clicked(self, button):
        if button == self.toolbar.home:
            self.centralWidget().setCurrentWidget(self.main_frame)
        elif button == self.toolbar.options:
            self.centralWidget().setCurrentWidget(self.options)
        elif button == self.toolbar.controls:
            self.centralWidget().setCurrentWidget(self.controls)
        elif button == self.toolbar.refresh:
            if button.isChecked():
                self.device_grid.start_update()
            else:
                self.device_grid.stop_update()

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
            "QPushButton {border: 1px solid #0a58ca;border-radius: 10px;padding: 10px;margin: 5px;}"
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
        options = self.options.options
        user = options.get("profile")
        profile = self.check_profile(user, device)
        if not profile:
            return
        self.async_handler.run_coro(
            self.async_handler.standby_host, device, user, self.options.profiles
        )
        # self.rp_worker.setup(None, device, user, options)
        # self.rp_worker.run(standby=True)

    @QtCore.Slot(str)
    def standby_callback(self, error: str):
        """Callback after attempting standby."""
        if error:
            message(self, "Standby Error", error)
        else:
            message(self, "Standby Success", "Set device to Standby", "info")

    def wakeup_host(self, device):
        """Wakeup Host."""
        user = self.options.options.get("profile")
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
        self._stream_window.stopped.connect(self.session_stop)
        self._stream_window.start(
            device,
            user,
            options,
            audio_device=audio_device,
            input_map=self.controls.get_map(),
            input_options=self.controls.get_options(),
        )
        QtWidgets.QApplication.instance().setActiveWindow(self._stream_window)

    def session_start(self):
        """Start Session."""
        self.rp_worker.run()

    def session_stop(self):
        """Callback for stopping session."""
        _LOGGER.debug("Detected Session Stop")
        self.rp_worker.stop()
        QtWidgets.QApplication.instance().setActiveWindow(self)
        self.device_grid.session_stop()
        if self._stream_window:
            self._stream_window.hide()
            self._stream_window = None
        self.check_error_finish()

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
        event.accept()
