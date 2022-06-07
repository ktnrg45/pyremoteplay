# pylint: disable=c-extension-no-member,invalid-name
"""Options Widget."""
from __future__ import annotations
import socket
from dataclasses import dataclass, asdict, field

import sounddevice

from PySide6 import QtWidgets, QtCore
from PySide6.QtCore import Qt, QTimer  # pylint: disable=no-name-in-module
from PySide6.QtMultimedia import QMediaDevices  # pylint: disable=no-name-in-module

from pyremoteplay.receiver import AVReceiver
from pyremoteplay.const import Resolution, Quality, StreamType, FPS
from pyremoteplay.oauth import get_login_url, get_user_account
from pyremoteplay.register import register
from pyremoteplay.util import (
    add_profile,
    add_regist_data,
    get_options,
    get_profiles,
    write_options,
    write_profiles,
)

from .util import label, message, spacer
from .widgets import AnimatedToggle


@dataclass
class Options:
    """Class for Options."""

    quality: str = "Default"
    use_opengl: bool = False
    fps: int = 30
    show_fps: bool = False
    use_hw: bool = False
    codec: str = ""
    hdr: bool = False
    resolution: str = "720p"
    fullscreen: bool = False
    profile: str = ""
    devices: list = field(default_factory=list)
    use_qt_audio: bool = False

    def save(self):
        """Save Options."""
        write_options(asdict(self))


class OptionsWidget(QtWidgets.QWidget):
    """Widget for Options."""

    search_requested = QtCore.Signal(str)
    device_added = QtCore.Signal()
    device_removed = QtCore.Signal(str)
    register_finished = QtCore.Signal()

    def __init__(self, *args, **kwargs):
        self._profiles = {}
        self._devices = []
        self.audio_output = None
        self.audio_devices = {}
        self._device_search_timer = None
        self._search_dialog = None

        super().__init__(*args, **kwargs)
        self.setLayout(QtWidgets.QGridLayout(self, alignment=Qt.AlignTop))

        self._media_devices = QMediaDevices()
        self.quality = QtWidgets.QComboBox(self)
        self.use_opengl = AnimatedToggle("Use OpenGL", self)
        self.fps = QtWidgets.QComboBox(self)
        self.fps_show = AnimatedToggle("Show FPS", self)
        self.fullscreen = AnimatedToggle("Show Fullscreen", self)
        self.use_hw = AnimatedToggle("Use Hardware Decoding", self)
        self.hdr = AnimatedToggle("Use HDR", self)
        self.resolution = QtWidgets.QComboBox(self)
        self.accounts = QtWidgets.QTreeWidget()
        self.audio_output = QtWidgets.QComboBox(self)
        self.decoder = QtWidgets.QComboBox(self)
        self.codec = QtWidgets.QComboBox(self)
        self.device_tree = QtWidgets.QTreeWidget()
        self.use_qt_audio = AnimatedToggle("Use QT Audio", self)

        self._init_options()
        self.get_audio_devices()

    def _init_options(self):
        set_account = QtWidgets.QPushButton("Select Account")
        add_account = QtWidgets.QPushButton("Add Account")
        del_account = QtWidgets.QPushButton("Delete Account")
        add_device = QtWidgets.QPushButton("Add Device")
        del_device = QtWidgets.QPushButton("Remove Device")
        del_device.setDisabled(True)
        add_device.clicked.connect(self.new_device)
        del_device.clicked.connect(self.delete_device)
        set_account.clicked.connect(self.set_profiles)
        add_account.clicked.connect(self.new_profile)
        del_account.clicked.connect(self.delete_profile)
        res_label = label(self, "**1080p is for PS4 Pro, PS5 only**", wrap=False)

        devices_label = label(
            self,
            "If your device is not showing up automatically, "
            "try adding the IP Address below.",
            wrap=True,
        )

        self.quality.addItems([item.name for item in Quality])
        self.use_opengl.setToolTip("Recommended if using Hardware Decoding")
        self.fps.addItems([str(item.value) for item in FPS])
        self.resolution.addItems(
            [item.name.replace("RESOLUTION_", "").lower() for item in Resolution]
        )
        self.audio_output.addItems(list(self.audio_devices.keys()))
        self.decoder.addItems(self.get_decoder())
        self.codec.addItems({StreamType.preset(item) for item in StreamType})
        self.use_qt_audio.setToolTip("Uses PortAudio if disabled")

        self.set_options()
        self.set_devices()
        self.set_profiles()

        self.quality.currentTextChanged.connect(self._change_options)
        self.use_opengl.stateChanged.connect(self._change_options)
        self.fps.currentTextChanged.connect(self._change_options)
        self.fps_show.stateChanged.connect(self._change_options)
        self.fullscreen.stateChanged.connect(self._change_options)
        self.use_hw.stateChanged.connect(self._change_options)
        self.resolution.currentTextChanged.connect(self._change_options)
        self.decoder.currentTextChanged.connect(self._change_options)
        self.codec.currentTextChanged.connect(self._change_options)
        self.hdr.stateChanged.connect(self._change_options)
        self.accounts.itemDoubleClicked.connect(self.set_profiles)
        self.device_tree.itemSelectionChanged.connect(
            lambda: del_device.setDisabled(False)
        )
        self.use_qt_audio.stateChanged.connect(self._qt_audio_changed)

        self._media_devices.audioOutputsChanged.connect(self.get_audio_devices)

        widgets = (
            ("Quality", self.quality, self.use_opengl),
            ("FPS", self.fps, self.fps_show),
            ("Resolution", self.resolution, self.fullscreen),
            (res_label,),
            ("Video Decoder", self.decoder, self.use_hw),
            ("Video Codec", self.codec, self.hdr),
            ("Audio Output", self.audio_output, self.use_qt_audio),
        )

        for row_index, _row in enumerate(widgets):
            for col_index, item in enumerate(_row):
                if isinstance(item, str):
                    item = QtWidgets.QLabel(f"{item}:")
                self.layout().addWidget(
                    item, row_index, col_index + 1, 1, 3 // len(_row)
                )

        row = len(widgets)
        self.layout().addWidget(devices_label, row - 2, 5, 2, 2)
        self.layout().addItem(spacer(), row, 0)

        row += 1
        account_layout = QtWidgets.QHBoxLayout()
        account_layout.addWidget(set_account)
        account_layout.addWidget(add_account)
        account_layout.addWidget(del_account)
        self.layout().addLayout(account_layout, row, 1, 1, 3)
        device_layout = QtWidgets.QHBoxLayout()
        device_layout.addWidget(add_device)
        device_layout.addWidget(del_device)
        self.layout().addLayout(device_layout, row, 5, 1, 2)

        self.layout().addItem(spacer(), row, 4)
        self.layout().setColumnStretch(4, 1)
        row += 1

        self.layout().addWidget(self.accounts, row, 1, 2, 3)
        self.layout().addWidget(self.device_tree, row, 5, 2, 2)
        self.layout().setRowStretch(row + 1, 1)

    def get_decoder(self) -> list:
        """Return HW decoder or CPU if not found."""
        decoders = []
        found = AVReceiver.find_video_decoder(codec_name="h264", use_hw=True)
        for decoder, alias in found:
            decoder = decoder.replace("h264", "").replace("_", "")
            if alias == "CPU":
                decoders.append("CPU")
                continue
            name = f"{decoder} ({alias})"
            decoders.append(name)
        return decoders

    def get_audio_devices(self):
        """Return Audio devices."""
        audio_devices = {}
        if self.use_qt_audio.isChecked():
            audio_devices = self.get_qt_audio_devices()
        else:
            audio_devices = self.get_sd_audio_devices()
        self.audio_devices = audio_devices
        if self.audio_output:
            self.audio_output.clear()
            self.audio_output.addItems(self.audio_devices)
        return audio_devices

    def get_qt_audio_devices(self) -> dict:
        """Return Qt Audio Devices."""
        devices = QMediaDevices.audioOutputs()
        default = QMediaDevices.defaultAudioOutput()
        audio_devices = {f"{default.description()} (Default)": default}
        for device in devices:
            if device == default:
                continue
            audio_devices[device.description()] = device
        return audio_devices

    def get_sd_audio_devices(self) -> dict:
        """Return SoundDevice audio devices."""
        devices = sounddevice.query_devices()
        default_index = sounddevice.default.device[1]
        default_name = ""
        if default_index is not None:
            default_name = f"{devices[default_index].get('name')} (Default)"
            audio_devices = {default_name: devices[default_index]}
        else:
            audio_devices = {}
        for index, device in enumerate(devices):
            if default_index == index:
                name = default_name
            else:
                name = device.get("name")
            if device.get("max_output_channels"):
                device["index"] = index
                audio_devices[name] = device
        return audio_devices

    def get_audio_device(self):
        """Return Selected Audio Device."""
        return self.audio_devices.get(self.audio_output.currentText())

    def set_options(self) -> bool:
        """Set Options."""
        options = get_options()
        try:
            codec = options["codec"]
            _codec = codec.split("_")
            codec = _codec[0]
            if len(_codec) > 1:
                decoder = _codec[1]
            else:
                decoder = "CPU"

            self._devices = options["devices"]
            self.quality.setCurrentText(str(options["quality"]))
            self.use_opengl.setChecked(options["use_opengl"])
            self.fps.setCurrentText(str(options["fps"]))
            self.fps_show.setChecked(options["show_fps"])
            self.use_hw.setChecked(options["use_hw"])
            self.resolution.setCurrentText(options["resolution"])
            self.fullscreen.setChecked(options["fullscreen"])
            self.use_qt_audio.setChecked(options["use_qt_audio"])
            self.codec.setCurrentText(codec)
            self.hdr.setChecked(options["hdr"])

            found = False
            for index in range(0, self.decoder.count()):
                item = self.decoder.itemText(index)
                if item.startswith(decoder):
                    found = True
                    self.decoder.setCurrentText(item)
                    break
            if not found:
                self.decoder.setCurrentText("CPU")
        except KeyError:
            self.options_data.save()
            return False
        return True

    def set_devices(self):
        """Set devices."""
        self.device_tree.clear()
        self.device_tree.setHeaderLabels(["Devices"])
        for host in self._devices:
            item = QtWidgets.QTreeWidgetItem(self.device_tree)
            item.setText(0, host)

    def set_profiles(self):
        """Set Profiles."""
        profile_name = self.selected_profile
        self._profiles = get_profiles()
        self.accounts.clear()
        self.accounts.setHeaderLabels(["PSN ID", "Active", "Is Registered", "Devices"])
        if not self.profiles:
            return
        for profile, data in self.profiles.items():
            item = QtWidgets.QTreeWidgetItem(self.accounts)
            hosts = data.get("hosts")
            mac_addresses = []
            if hosts:
                for host in hosts.keys():
                    mac_addresses.append(host)
            is_registered = "Yes" if data.get("hosts") else "No"
            item.setText(0, profile)
            item.setText(1, "No")
            item.setText(2, is_registered)
            item.setText(3, ", ".join(mac_addresses))

        if not profile_name:
            profile_name = get_options().get("profile")
        self._select_profile(profile_name)
        self._change_options()

    def _select_profile(self, profile_name):
        if profile_name:
            selected = self.accounts.findItems(
                profile_name, Qt.MatchFixedString, column=0
            )
            if selected and len(selected) == 1:
                selected[0].setSelected(True)
                selected[0].setText(1, "Yes")

    # pylint: disable=unused-argument
    def _change_options(self, *args):
        self.use_hw.setDisabled(self.decoder.currentText() == "CPU")
        self.hdr.setDisabled(self.codec.currentText() == StreamType.H264.name.lower())
        self.options_data.save()

    # pylint: disable=unused-argument
    def _qt_audio_changed(self, *args):
        self.get_audio_devices()
        self._change_options()

    def new_device(self):
        """Run New Device flow."""
        title = "Add Device"
        dialog = QtWidgets.QInputDialog(self)
        dialog.setWindowTitle(title)
        dialog.setInputMode(QtWidgets.QInputDialog.TextInput)
        dialog.setLabelText("Enter IP Address of device to manually search for.")
        if not dialog.exec():
            return
        host = dialog.textValue()
        if not host:
            return
        try:
            socket.getaddrinfo(host, None)
        except socket.gaierror:
            QtWidgets.QMessageBox.critical(
                self, "Invalid IP Address", f"Could not find device at: {host}"
            )
            return
        if host in self._devices:
            text = "Device is already added."
            message(self.window(), "Device Already Added", text, "warning")
            return
        self._device_search_timer = QTimer()
        self._device_search_timer.setSingleShot(True)
        self._device_search_timer.timeout.connect(lambda: self._search_failed(host))
        self.search_requested.emit(host)
        self._device_search_timer.start(5000)
        self._search_dialog = QtWidgets.QMessageBox(
            QtWidgets.QMessageBox.Information,
            "Searching",
            f"Searching for host: {host}",
            QtWidgets.QMessageBox.Cancel,
            self.window(),
        )
        self._search_dialog.buttonClicked.connect(self._search_aborted)
        self._search_dialog.exec()

    def search_complete(self, host, status):
        """Callback for search complete."""
        if self._search_dialog:
            self._search_dialog.close()
            self._search_dialog = None
        if self._device_search_timer:
            self._device_search_timer.stop()
            self._device_search_timer = None
        else:
            return
        if status:
            ip_address = status["host-ip"]
            self._devices.append(ip_address)
            self.set_devices()
            self._change_options()
            self.device_added.emit()
            message(self.window(), "Device found", f"Found Device at {host}", "info")
        else:
            self._search_failed(host)

    def _search_failed(self, host):
        self._device_search_timer = None
        text = f"Could not find device at: {host}."
        message(self.window(), "Device not found", text, "warning")

    def _search_aborted(self):
        if self._search_dialog:
            self._search_dialog.close()
            self._search_dialog = None
        if self._device_search_timer:
            self._device_search_timer.stop()
        self._device_search_timer = None

    def delete_device(self):
        """Delete Device."""
        button = self.sender()
        if button:
            button.setDisabled(True)
        items = self.device_tree.selectedItems()
        if not items:
            return
        item = items[0]
        host = item.text(0)
        self._devices.remove(host)
        self.set_devices()
        self._change_options()
        self.device_removed.emit(host)

    def delete_profile(self):
        """Delete profile."""
        item = self.accounts.selectedItems()[0]
        if not item:
            return
        name = item.text(0)
        text = f"Are you sure you want to delete account: {name}"
        message(
            self.window(),
            "Delete Account",
            text,
            "warning",
            lambda: self.remove_profile(name),
            escape=True,
        )

    def remove_profile(self, name):
        """Remove profile from config."""
        self._profiles.pop(name)
        write_profiles(self.profiles)
        profile_name = list(self.profiles.keys())[0] if self.profiles else ""
        self.set_profiles()
        self._select_profile(profile_name)
        # Select a profile if it exists
        self.set_profiles()

    def new_profile(self):
        """Run new profile flow."""
        title = "Add PSN Account"
        text = "To Add a New PSN Account you will have to sign in to your PSN Account. Continue?"
        message(self.window(), title, text, "info", self.new_account, escape=True)

    def new_account(self):
        """Run prompts to add PSN Account."""
        title = "Add PSN Account"
        dialog = QtWidgets.QInputDialog(self)
        dialog.setWindowTitle(title)
        dialog.setInputMode(QtWidgets.QInputDialog.TextInput)
        dialog.setOption(QtWidgets.QInputDialog.UseListViewForComboBoxItems)
        url = ""
        grp = 60
        login_url = get_login_url()
        for ivl in range(0, len(login_url) // grp):
            end = grp * (ivl + 1)
            start = grp * ivl
            url = f"{url}{login_url[start:end]}\n"
        url = f"{url}{login_url[grp * -1]}"
        dialog.setComboBoxItems([url])
        dialog.setLabelText(
            "Go to the following url in a web browser and sign in using your PSN Account:\n\n"
            "You will be redirected after signing in to a blank page that says 'Redirect'.\n"
            "When you reach this page, copy the page URL and click 'Ok' to continue"
        )
        clipboard = QtWidgets.QApplication.clipboard()
        clipboard.clear(mode=clipboard.Clipboard)
        clipboard.setText(login_url, mode=clipboard.Clipboard)
        if not dialog.exec():
            return

        dialog = QtWidgets.QInputDialog(self)
        dialog.setWindowTitle(title)
        dialog.setInputMode(QtWidgets.QInputDialog.TextInput)
        dialog.setLabelText(
            "Copy and Paste the URL of the page that says 'Redirect' below:"
        )
        if not dialog.exec():
            return
        url = dialog.textValue()
        if not url:
            return
        account = get_user_account(url)
        profiles = add_profile(self.profiles, account)
        if not profiles:
            text = "Error getting account data"
            level = "critical"
        else:
            user_id = account.get("online_id")
            assert user_id in profiles
            write_profiles(profiles)
            self.set_profiles()
            text = f"Successfully added PSN account: {user_id}"
            level = "info"
        message(self.window(), title, text, level)

    def register(self, host, name):
        """Register profile with RP Host."""
        user_id = self.profiles[name]["id"]
        dialog = QtWidgets.QInputDialog(self)
        dialog.setWindowTitle("Register")
        dialog.setInputMode(QtWidgets.QInputDialog.TextInput)
        dialog.setLabelText(
            f"On Remote Play host, Login to your PSN Account: {name}\n"
            "Then go to Settings -> Remote Play Connection Settings ->\n"
            "Add Device and enter the PIN shown.\n\n"
        )
        if not dialog.exec():
            return
        dialog.hide()
        pin = dialog.textValue()
        pin = pin.replace(" ", "").replace("-", "")
        if not pin.isnumeric() or len(pin) != 8:
            title = "Invalid PIN"
            text = "PIN must be 8 numbers."
            level = "critical"
        else:
            data = register(host["host-ip"], user_id, pin)
            if not data:
                title = "Error registering"
                text = (
                    f"Could not register with device at: {host['host-ip']}.\n"
                    "Make sure that you are logged in on your device with\n"
                    f"PSN account: {name} and that you are entering the PIN correctly."
                )
                level = "critical"
            else:
                profile = self.profiles[name]
                profile = add_regist_data(profile, host, data)
                self._profiles[name] = profile
                write_profiles(self.profiles)
                self.set_profiles()
                title = "Registration Successful"
                text = (
                    f"Successfully registered with device at: {host['host-ip']}\n"
                    f"with PSN Account: {name}."
                )
                level = "info"

        message(self.window(), title, text, level)
        self.register_finished.emit()

    @property
    def selected_profile(self):
        """Return Selected Profile."""
        profile = ""
        account = self.accounts.selectedItems()
        if account:
            profile = account[0].text(0)
        return profile

    @property
    def options_data(self):
        """Return Options."""
        profile = self.selected_profile
        decoder = self.decoder.currentText()
        decoder = decoder.split(" (")[0]
        codec = self.codec.currentText()
        if "CPU" not in decoder:
            codec = f"{codec}_{decoder}"

        options = Options(
            quality=self.quality.currentText(),
            use_opengl=self.use_opengl.isChecked(),
            fps=int(self.fps.currentText()),
            show_fps=self.fps_show.isChecked(),
            use_hw=self.use_hw.isChecked(),
            codec=codec,
            hdr=self.hdr.isChecked(),
            resolution=self.resolution.currentText(),
            fullscreen=self.fullscreen.isChecked(),
            profile=profile,
            devices=self._devices,
            use_qt_audio=self.use_qt_audio.isChecked(),
        )
        return options

    @property
    def options(self) -> dict:
        """Return Options as dict."""
        return asdict(self.options_data)

    @property
    def devices(self) -> list[str]:
        """Return devices."""
        return list(self._devices)

    @property
    def profiles(self) -> dict:
        """Return profiles."""
        return dict(self._profiles)
