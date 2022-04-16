# pylint: disable=c-extension-no-member,invalid-name
"""Device Grid Widget."""
import logging
from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import Qt  # pylint: disable=no-name-in-module
from pyremoteplay.const import DEFAULT_STANDBY_DELAY
from pyremoteplay.device import STATUS_OK


_LOGGER = logging.getLogger(__name__)


class DeviceButton(QtWidgets.QPushButton):
    """Button that represents a Remote Play Device."""

    COLOR_DARK = "#000000"
    COLOR_LIGHT = "#FFFFFF"
    COLOR_BG = "#E9ECEF"

    def __init__(self, main_window, device):
        super().__init__()
        self.info = ""
        self.main_window = main_window
        self.device = device
        self.status = device.status
        self.info_show = False
        self.menu = QtWidgets.QMenu(self)
        self.clicked.connect(self._on_click)
        self.text_color = self.COLOR_DARK
        self.bg_color = self.COLOR_BG
        self.border_color = ("#A3A3A3", "#A3A3A3")

        self._init_actions()
        self._get_info()
        self._get_text()
        self._set_image()
        self._set_style()

    def _on_click(self):
        self.setEnabled(False)
        self.setToolTip("Device unavailable.\nWaiting for session to close...")
        self.main_window.connect_host(self.device)

    def _init_actions(self):
        self.action_info = QtGui.QAction(self)
        self.action_info.triggered.connect(self._toggle_info)
        self.menu.addAction(self.action_info)
        self.action_power = QtGui.QAction(self)
        self.menu.addAction(self.action_power)
        self.action_power.triggered.connect(self._toggle_power)

    def _set_style(self):
        if self.device.is_on:
            self.border_color = ("#6EA8FE", "#0D6EFD")
        else:
            self.border_color = ("#FEB272", "#FFC107")
        self.setStyleSheet(
            "".join(
                [
                    "QPushButton {border-radius:25%;",
                    f"border: 5px solid {self.border_color[0]};",
                    f"color: {self.text_color};",
                    f"background-color: {self.bg_color};",
                    "}",
                    "QPushButton:hover {",
                    f"border: 5px solid {self.border_color[1]};",
                    f"color: {self.text_color};",
                    "}",
                ]
            )
        )

    def _set_image(self):
        self.bg_color = self.COLOR_BG
        title_id = self.device.app_id
        if title_id:
            image = self.device.image
            if image is not None:
                pix = QtGui.QPixmap()
                pix.loadFromData(image)
                self.setIcon(pix)
                self.setIconSize(QtCore.QSize(100, 100))
                img = pix.toImage()
                self.bg_color = img.pixelColor(25, 25).name()
                contrast = self._calc_contrast(self.bg_color)
                if contrast >= 1 / 4.5:
                    self.text_color = self.COLOR_LIGHT
                else:
                    self.text_color = self.COLOR_DARK
        else:
            self.setIcon(QtGui.QIcon())
            self.text_color = self.COLOR_DARK

    def _calc_contrast(self, hex_color):
        colors = (self.COLOR_DARK, hex_color)
        lum = []
        for color in colors:
            lum.append(self._calc_luminance(color))
        lum = sorted(lum)
        contrast = (lum[0] + 0.05) / lum[1] + 0.05
        return contrast

    def _calc_luminance(self, hex_color):
        assert len(hex_color) == 7
        hex_color = hex_color.replace("#", "")
        assert len(hex_color) == 6
        color = []
        for index in range(0, 3):
            start = 2 * index
            rgb = int.from_bytes(
                bytes.fromhex(hex_color[start : start + 2]),
                "little",
            )
            rgb /= 255
            rgb = rgb / 12.92 if rgb <= 0.04045 else ((rgb + 0.055) / 1.055) ** 2.4
            color.append(rgb)
        luminance = (0.2126 * color[0]) + (0.7152 * color[1]) + (0.0722 * color[2])
        return luminance

    def _get_text(self):
        if self.device.host_type == "PS4":
            device_type = "PlayStation 4"
        elif self.device.host_type == "PS5":
            device_type = "PlayStation 5"
        else:
            device_type = ""
        app = self.device.app_name
        if not app:
            app = "Idle" if self.device.is_on else "Standby"
        self.main_text = f"{self.device.host_name}\n" f"{device_type}\n\n" f"{app}"
        if not self.info_show:
            self.setText(self.main_text)

    def _get_info(self):
        self.info = (
            f"Type: {self.device.host_type}\n"
            f"Name: {self.device.host_name}\n"
            f"IP Address: {self.device.host}\n"
            f"Mac Address: {self.device.mac_address}\n\n"
            f"Status: {self.device.status_name}\n"
            f"Playing: {self.device.app_name}"
        )

    def _toggle_info(self):
        text = self.info if not self.info_show else self.main_text
        self.setText(text)
        self.info_show = not self.info_show

    def _toggle_power(self):
        if self.device.is_on:
            self.main_window.standby_host(self.device)
        else:
            self.main_window.wakeup_host(self.device)

    def _enable_toggle_power(self):
        self.action_power.setDisabled(False)

    def update_state(self, state):
        """Callback for when state is updated."""
        cur_id = self.status.get("running-app-titleid")
        new_id = state.get("running-app-titleid")
        cur_status = self.status.get("status-code")
        new_status = state.get("status-code")
        self.status = state
        self._get_info()
        self._get_text()
        if cur_id != new_id:
            self._set_image()
        if cur_status == STATUS_OK and new_status != cur_status:
            # Disable Wakeup since device won't do anything right away.
            self.action_power.setDisabled(True)
            QtCore.QTimer.singleShot(
                DEFAULT_STANDBY_DELAY * 1000, self._enable_toggle_power
            )
        self._set_style()

    def contextMenuEvent(self, event):  # pylint: disable=unused-argument
        """Context Menu Event."""
        text = "View Info" if not self.info_show else "Hide Info"
        self.action_info.setText(text)
        if self.device.is_on:
            self.action_power.setText("Standby")
        else:
            self.action_power.setText("Wakeup")
        self.menu.popup(QtGui.QCursor.pos())


class DeviceGridWidget(QtWidgets.QWidget):
    """Widget that contains device buttons."""

    MAX_COLS = 3

    def __init__(self, parent):
        super().__init__(parent)
        self.setStyleSheet("QPushButton {padding: 50px 25px;}")
        self.setLayout(QtWidgets.QGridLayout())
        self.layout().setColumnMinimumWidth(0, 100)
        self.widgets = set()

    def add(self, button, row, col):
        """Add button to grid."""
        self.layout().addWidget(button, row, col, Qt.AlignCenter)
        self.widgets.add(button)

    def create_grid(self, devices: dict):
        """Create Button Grid."""
        # buttons = self._check_buttons(devices)
        for widget in self.widgets:
            if widget.device.ip_address in devices:
                devices.pop(widget.device.ip_address)
        if self.widgets or devices:
            count = len(self.widgets)
            for index, device in enumerate(devices.values()):
                if not device.status:
                    count -= 1
                    continue
                cur_index = index + count
                col = cur_index % self.MAX_COLS
                row = cur_index // self.MAX_COLS
                button = DeviceButton(self.window(), device)
                self.add(button, row, col)
            self.show()
            self.window().center_text.hide()
        else:
            self.hide()
            self.window().center_text.show()

    def session_stop(self):
        """Handle session stopped."""
        if self.window().toolbar.refresh.isChecked():
            self.start_update()
        self.setDisabled(False)
        QtCore.QTimer.singleShot(10000, self.enable_buttons)

    def enable_buttons(self):
        """Enable all buttons."""
        for button in self.widgets:
            button.setDisabled(False)
            button.setToolTip("")

    def start_update(self):
        """Start update service."""
        self.window().async_handler.poll()

    def stop_update(self):
        """Stop Updata Service."""
        self.window().async_handler.stop_poll()
