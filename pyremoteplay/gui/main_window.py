import logging

from pyremoteplay.ctrl import CTRL, send_wakeup
from pyremoteplay.util import timeit
from PySide6 import QtCore, QtWidgets
from PySide6.QtCore import Qt

from .device_grid import DeviceGridWidget
from .options import ControlsWidget, OptionsWidget
from .stream_window import StreamWindow
from .toolbar import ToolbarWidget
from .util import Popup, message

_LOGGER = logging.getLogger(__name__)


class MainWidget(QtWidgets.QWidget):
    def __init__(self, app):
        super().__init__()
        self._app = app
        self.screen = self._app.primaryScreen()
        self.idle = True
        self.thread = None
        self.ctrl_window = StreamWindow(self)
        self.toolbar = None
        self.device_grid = None
        self._init_window()

    def _init_window(self):
        self.setWindowTitle("PyRemotePlay")
        self.device_grid = DeviceGridWidget(self)
        self.toolbar = ToolbarWidget(self)
        self.options = OptionsWidget(self)
        self.controls = ControlsWidget(self)
        self.options.hide()
        self.controls.hide()
        self.center_text = QtWidgets.QLabel("", alignment=Qt.AlignCenter)
        self.center_text.setWordWrap(True)
        self.center_text.setObjectName("center-text")
        self.layout = QtWidgets.QVBoxLayout(self)
        self.layout.addWidget(self.toolbar)
        self.layout.addWidget(self.options)
        self.layout.addWidget(self.center_text)
        self.layout.addWidget(self.device_grid)
        self.layout.addWidget(self.controls)
        self.layout.setAlignment(self.toolbar, Qt.AlignTop)
        self.set_style()
        self.device_grid.discover()
        self.toolbar.refresh.setChecked(True)
        self.toolbar.refresh_click()

    def startup_check_grid(self):
        if not self.device_grid.widgets:
            self.main_window.set_center_text(
                "No Devices Found.\n"
                "Try adding a device in options."
            )

    def set_style(self):
        style = (
            "QPushButton {border: 1px solid #0a58ca;border-radius: 10px;padding: 10px;}"
            "QPushButton:hover {background-color:#6ea8fe;color:black;}"
            "QPushButton:pressed {background-color:#0a58ca;color:white;}"
            "QPushButton:checked {background-color:#0D6EFD;color:white;}"
            "#center-text {font-size: 24px;}"
        )
        self.setStyleSheet(style)

    def show_popup(self):
        self.popup = Popup()
        self.popup.setGeometry(QtCore.QRect(100, 100, 400, 200))
        self.popup.show()

    def check_profile(self, name, host):
        profile = self.options.profiles.get(name)
        if not profile:
            message(self, "Error: No PSN Accounts found", "Click 'Options' -> 'Add Account' to add PSN Account.")
            return None
        if host["host-id"] not in profile["hosts"]:
            text = f"PSN account: {name} has not been registered with this device. Click 'Ok' to register."
            message(self, "Needs Registration", text, "info", cb=lambda: self.options.register(host, name), escape=True)
            return None
        return profile

    def standby_host(self, host):
        name = self.options.options.get("profile")
        profile = self.check_profile(name, host)
        if not profile:
            return
        ip_address = host["host-ip"]
        ctrl = CTRL(ip_address, profile)
        status = ctrl.start(autostart=False)
        if status:
            ctrl.standby()
        ctrl.stop()
        if not status:
            message(self, "Standby Error", ctrl.error)
        else:
            message(self, "Standby Success", f"Set device at {ip_address} to Standby", "info")

    def wakeup_host(self, host):
        name = self.options.options.get("profile")
        profile = self.check_profile(name, host)
        if not profile:
            return
        ip_address = host["host-ip"]
        mac_address = host["host-id"]
        regist_key = profile["hosts"][mac_address]["data"]["RegistKey"]
        send_wakeup(ip_address, regist_key)
        message(self, "Wakeup Sent", f"Sent Wakeup command to device at {ip_address}", "info")

    def connect_host(self, host):
        self.device_grid.timer.stop()
        options = self.options.options
        name = options.get("profile")
        profile = self.check_profile(name, host)
        if not profile:
            return
        ip_address = host["host-ip"]
        resolution = options['resolution']
        fps = options['fps']
        show_fps = options['show_fps']
        fullscreen = options['fullscreen']
        self.ctrl_window.start(
            ip_address,
            name,
            profile,
            fps=fps,
            resolution=resolution,
            show_fps=show_fps,
            fullscreen=fullscreen,
            input_map=self.controls.get_map(),
            input_options=self.controls.get_options(),
        )
        self._app.setActiveWindow(self.ctrl_window)

    def session_stop(self):
        print("Detected Session Stop")
        self._app.setActiveWindow(self)
        self.device_grid.session_stop()

    def set_center_text(self, text):
        self.center_text.setText(text)
        self.center_text.show()

    def closeEvent(self, event):
        self.device_grid.stop_update()
        self.ctrl_window.close()
        event.accept()
