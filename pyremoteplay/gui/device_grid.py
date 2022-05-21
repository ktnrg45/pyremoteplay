# pylint: disable=c-extension-no-member,invalid-name
"""Device Grid Widget."""
from __future__ import annotations
import logging
from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import Qt  # pylint: disable=no-name-in-module
from pyremoteplay.device import RPDevice


_LOGGER = logging.getLogger(__name__)


class DeviceButton(QtWidgets.QPushButton):
    """Button that represents a Remote Play Device."""

    COLOR_DARK = "#000000"
    COLOR_LIGHT = "#FFFFFF"
    COLOR_BG = "#E9ECEF"

    power_toggled = QtCore.Signal(RPDevice)
    connect_requested = QtCore.Signal(RPDevice)

    def __init__(self, device: RPDevice):
        super().__init__()
        self.info = ""
        self.device = device
        self.status = device.status
        self._info_show = False
        self.text_color = self.COLOR_DARK
        self.bg_color = self.COLOR_BG
        self.border_color = ("#A3A3A3", "#A3A3A3")

        self._get_info()
        self._get_text()
        self._set_image()
        self._set_style()

        self.clicked.connect(self._on_click)

    def sizeHint(self) -> QtCore.QSize:
        """Return Size Hint."""
        return QtCore.QSize(275, 250)

    def _on_click(self):
        self.setEnabled(False)
        self.setToolTip("Device unavailable.\nWaiting for session to close...")
        self.connect_requested.emit(self.device)

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
            device_type = "Unknown"
        app = self.device.app_name
        if not app:
            app = "On" if self.device.is_on else "Standby"
        self.main_text = f"{self.device.host_name}\n" f"{device_type}\n\n" f"{app}"
        if not self._info_show:
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
        text = self.info if not self._info_show else self.main_text
        self.setText(text)
        self._info_show = not self._info_show

    def _power_toggle(self):
        self.power_toggled.emit(self.device)

    def update_state(self):
        """Callback for when state is updated."""
        state = self.device.status
        cur_id = self.status.get("running-app-titleid")
        new_id = state.get("running-app-titleid")
        self.status = state
        self._get_info()
        self._get_text()
        if cur_id != new_id:
            self._set_image()
        self._set_style()

    def contextMenuEvent(self, event):  # pylint: disable=unused-argument
        """Context Menu Event."""
        info_text = "View Info" if not self._info_show else "Hide Info"
        power_text = "Standby" if self.device.is_on else "Wakeup"
        menu = QtWidgets.QMenu(self)
        action_info = QtGui.QAction(info_text, menu)
        action_power = QtGui.QAction(power_text, menu)
        action_info.triggered.connect(self._toggle_info)
        action_power.triggered.connect(self._power_toggle)
        menu.addActions([action_info, action_power])
        menu.popup(QtGui.QCursor.pos())


class DeviceGridWidget(QtWidgets.QWidget):
    """Widget that contains device buttons."""

    MAX_COLS = 3

    power_toggled = QtCore.Signal(RPDevice)
    connect_requested = QtCore.Signal(RPDevice)
    devices_available = QtCore.Signal()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setStyleSheet("QPushButton {padding: 50px 25px;}")
        self.setLayout(QtWidgets.QGridLayout())
        self.layout().setColumnMinimumWidth(0, 100)

    @QtCore.Slot(RPDevice)
    def _power_toggle(self, device: RPDevice):
        self.power_toggled.emit(device)

    @QtCore.Slot(RPDevice)
    def _connect_request(self, device: RPDevice):
        self.connect_requested.emit(device)

    def add(self, button, row, col):
        """Add button to grid."""
        self.layout().addWidget(button, row, col, Qt.AlignCenter)

    def create_grid(self, devices: dict):
        """Create Button Grid."""
        for widget in self.buttons():
            widget.update_state()
            if widget.device.ip_address in devices:
                devices.pop(widget.device.ip_address)
        if devices:
            count = self.layout().count()
            for index, device in enumerate(devices.values()):
                if not device.status:
                    continue
                cur_index = index + count
                col = cur_index % self.MAX_COLS
                row = cur_index // self.MAX_COLS
                button = DeviceButton(device)
                self.add(button, row, col)
                button.power_toggled.connect(self._power_toggle)
                button.connect_requested.connect(self._connect_request)

        if self.buttons():
            self.devices_available.emit()

    def enable_buttons(self):
        """Enable all buttons."""
        for button in self.buttons():
            button.setDisabled(False)
            button.setToolTip("")

    def buttons(self) -> list[DeviceButton]:
        """Return buttons."""
        count = self.layout().count()
        return [self.layout().itemAt(index).widget() for index in range(0, count)]
