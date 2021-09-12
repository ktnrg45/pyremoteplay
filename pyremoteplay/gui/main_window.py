import asyncio
import logging
import sys
import time

from pyremoteplay.__version__ import VERSION
from pyremoteplay.ddp import async_create_ddp_endpoint, wakeup
from pyremoteplay.session import Session
from pyremoteplay.util import format_regist_key, timeit
from PySide6 import QtCore, QtWidgets
from PySide6.QtCore import Qt

from .device_grid import DeviceGridWidget
from .options import ControlsWidget, OptionsWidget
from .stream_window import RPWorker, StreamWindow
from .toolbar import ToolbarWidget
from .util import Popup, message

_LOGGER = logging.getLogger(__name__)


class AsyncHandler(QtCore.QObject):
    status_updated = QtCore.Signal()

    def __init__(self, main_window):
        super().__init__()
        self.loop = None
        self.protocol = None
        self.main_window = main_window

    def start(self):
        if sys.platform == "win32":
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        self.loop = asyncio.new_event_loop()
        task = self.loop.create_task(self.run())
        self.loop.run_until_complete(task)
        self.loop.run_forever()

    def poll(self):
        self.protocol.start()

    def stop_poll(self):
        self.protocol.stop()

    async def run(self):
        _, self.protocol = await async_create_ddp_endpoint(self.status_updated.emit)
        await self.protocol.run()


class MainWindow(QtWidgets.QWidget):
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
        self.center_text = QtWidgets.QLabel("Searching for devices...", alignment=Qt.AlignCenter)
        self.center_text.setWordWrap(True)
        self.center_text.setObjectName("center-text")
        self.device_grid = DeviceGridWidget(self.main_frame, self)
        self.toolbar = ToolbarWidget(self)
        self.options = OptionsWidget(self)
        self.controls = ControlsWidget(self)
        self.options.hide()
        self.controls.hide()
        self.main_frame.layout.addWidget(self.center_text)
        self.main_frame.layout.addWidget(self.device_grid)
        self.layout = QtWidgets.QVBoxLayout(self)
        self.layout.addWidget(self.toolbar)
        self.layout.addWidget(self.options)
        self.layout.addWidget(self.controls)
        self.layout.setAlignment(self.toolbar, Qt.AlignTop)
        self.layout.addWidget(self.main_frame)
        self.layout.addWidget(QtWidgets.QLabel(f"v{VERSION}", alignment=Qt.AlignBottom))
        self.set_style()
        self.toolbar.refresh.setChecked(True)
        self.toolbar.refresh_click()
        self.session_finished.connect(self.check_error_finish)
        QtCore.QTimer.singleShot(7000, self.startup_check_grid)

    def event_status_updated(self):
        devices = self.async_handler.protocol.devices
        self.device_grid.create_grid(devices)

    def _init_rp_worker(self):
        self.rp_worker = RPWorker(self)
        self.rp_worker.moveToThread(self.async_thread)

    def startup_check_grid(self):
        if not self.device_grid.widgets:
            self.set_center_text(
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
        session = Session(ip_address, profile)
        status = session.start(autostart=False)
        if status:
            session.standby()
        session.stop()
        if not status:
            message(self, "Standby Error", session.error)
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
        regist_key = format_regist_key(regist_key)
        wakeup(ip_address, regist_key, host_type=host["host-type"])
        message(self, "Wakeup Sent", f"Sent Wakeup command to device at {ip_address}", "info")

    def connect_host(self, host):
        self.device_grid.stop_update()
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
        use_hw = options['use_hw']
        quality = options['quality']
        self._stream_window = StreamWindow(self)
        self._stream_window.start(
            ip_address,
            name,
            profile,
            fps=fps,
            resolution=resolution,
            show_fps=show_fps,
            fullscreen=fullscreen,
            input_map=self.controls.get_map(),
            input_options=self.controls.get_options(),
            use_hw=use_hw,
            quality=quality, 
        )
        self._app.setActiveWindow(self._stream_window)

    def session_start(self):
        self.rp_worker.run()

    def session_stop(self):
        _LOGGER.debug("Detected Session Stop")
        self.rp_worker.stop()
        self._stream_window = None
        self._app.setActiveWindow(self)
        self.device_grid.session_stop()
        self.session_finished.emit()

    def add_devices(self, devices):
        for host in devices:
            if host not in self.async_handler.protocol.devices:
                self.async_handler.protocol.add_device(host)

    def remove_device(self, host):
        self.async_handler.protocol.remove_device(host)
        self.event_status_updated()

    def check_error_finish(self):
        if self.rp_worker.error:
            message(self, "Error", self.rp_worker.error)
        self.rp_worker.error = ""

    def set_center_text(self, text):
        self.center_text.setText(text)
        self.center_text.show()

    def closeEvent(self, event):
        self.device_grid.stop_update()
        if self._stream_window:
            self._stream_window.close()
        self.async_thread.quit()
        self.async_handler.loop.stop()
        self.hide()
        start = time.time()
        while self.async_handler.loop.is_running():
            if time.time() - start > 5:
                break
        event.accept()
