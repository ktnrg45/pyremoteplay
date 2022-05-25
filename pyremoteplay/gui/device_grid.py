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

    BORDER_COLOR_ON = ("#6EA8FE", "#0D6EFD")
    BORDER_COLOR_OFF = ("#FEB272", "#FFC107")
    BORDER_COLOR_UNKNOWN = ("#A3A3A3", "#A3A3A3")

    power_toggled = QtCore.Signal(RPDevice)
    connect_requested = QtCore.Signal(RPDevice)

    def __init__(self, device: RPDevice):
        super().__init__()
        self._device = device
        self._status = device.status
        self._info_show = False
        self._text_color = self.COLOR_DARK
        self._bg_color = self.COLOR_BG

        self._update_text()
        self._set_image()
        self._set_style()

        self.clicked.connect(self._on_click)

    def sizeHint(self) -> QtCore.QSize:
        """Return Size Hint."""
        return QtCore.QSize(275, 275)

    def contextMenuEvent(self, event):  # pylint: disable=unused-argument
        """Context Menu Event."""
        info_text = "View Info" if not self._info_show else "Hide Info"
        power_text = "Standby" if self._device.is_on else "Wakeup"
        menu = QtWidgets.QMenu(self)
        action_info = QtGui.QAction(info_text, menu)
        action_power = QtGui.QAction(power_text, menu)
        action_info.triggered.connect(self._toggle_info)
        action_power.triggered.connect(self._power_toggle)
        menu.addActions([action_info, action_power])
        if self.state_unknown():
            action_power.setDisabled(True)
        menu.popup(QtGui.QCursor.pos())

    def update_state(self):
        """Callback for when state is updated."""
        state = self._device.status
        cur_id = self._status.get("running-app-titleid")
        new_id = state.get("running-app-titleid")
        self._status = state
        self._update_text()
        if cur_id != new_id:
            self._set_image()
        self._set_style()

    def state_unknown(self) -> bool:
        """Return True if state unknown."""
        return not self._device.status_name

    def _on_click(self):
        if self.state_unknown():
            return
        self.setEnabled(False)
        self.setToolTip("Device unavailable.\nWaiting for session to close...")
        self.connect_requested.emit(self._device)

    def _set_style(self):
        if self.state_unknown():
            border_color = DeviceButton.BORDER_COLOR_UNKNOWN
        else:
            if self._device.is_on:
                border_color = DeviceButton.BORDER_COLOR_ON
            else:
                border_color = DeviceButton.BORDER_COLOR_OFF
        self.setStyleSheet(
            "".join(
                [
                    "QPushButton {border-radius:25%;",
                    f"border: 5px solid {border_color[0]};",
                    f"color: {self._text_color};",
                    f"background-color: {self._bg_color};",
                    "}",
                    "QPushButton:hover {",
                    f"border: 5px solid {border_color[1]};",
                    f"color: {self._text_color};",
                    "}",
                ]
            )
        )

    def _set_image(self):
        self._bg_color = self.COLOR_BG
        title_id = self._device.app_id
        if title_id:
            image = self._device.image
            if image is not None:
                pix = QtGui.QPixmap()
                pix.loadFromData(image)
                self.setIcon(pix)
                self.setIconSize(QtCore.QSize(100, 100))
                img = pix.toImage()
                self._bg_color = img.pixelColor(25, 25).name()
                contrast = self._calc_contrast(self._bg_color)
                if contrast >= 1 / 4.5:
                    self._text_color = self.COLOR_LIGHT
                else:
                    self._text_color = self.COLOR_DARK
        else:
            self.setIcon(QtGui.QIcon())
            self._text_color = self.COLOR_DARK

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

    def _update_text(self):
        text = ""
        if self._info_show:
            text = self._get_info_text()
        else:
            text = self._get_main_text()
        self.setText(text)

    def _get_main_text(self) -> str:
        if self._device.host_type == "PS4":
            device_type = "PlayStation 4"
        elif self._device.host_type == "PS5":
            device_type = "PlayStation 5"
        else:
            device_type = "Unknown"
        app = self._device.app_name
        if not app:
            if self.state_unknown():
                app = "Unknown"
            else:
                if self._device.is_on:
                    app = "Idle"
                else:
                    app = "Standby"

        return f"{self._device.host_name}\n" f"{device_type}\n\n" f"{app}"

    def _get_info_text(self) -> str:
        text = (
            f"Type: {self._device.host_type}\n"
            f"Name: {self._device.host_name}\n"
            f"IP Address: {self._device.host}\n"
            f"Mac Address: {self._device.mac_address}\n\n"
            f"Status: {self._device.status_name}\n"
            f"Playing: {self._device.app_name}"
        )
        return text

    def _toggle_info(self):
        self._info_show = not self._info_show
        self._update_text()

    def _power_toggle(self):
        self.power_toggled.emit(self._device)

    @property
    def device(self) -> RPDevice:
        """Return Device."""
        return self._device


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
