# pylint: disable=c-extension-no-member,invalid-name
"""Options Widget."""
import socket
from dataclasses import dataclass, asdict, field

from PySide6 import QtWidgets
from PySide6.QtCore import Qt  # pylint: disable=no-name-in-module
from PySide6.QtMultimedia import QMediaDevices  # pylint: disable=no-name-in-module

from pyremoteplay.av import AVReceiver
from pyremoteplay.const import RESOLUTION_PRESETS, Quality
from pyremoteplay.oauth import LOGIN_URL, get_user_account
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
    decoder: str = ""
    resolution: str = "720p"
    fullscreen: bool = False
    profile: str = ""
    devices: list = field(default_factory=list)

    def save(self):
        """Save Options."""
        write_options(asdict(self))


class OptionsWidget(QtWidgets.QWidget):
    """Widget for Options."""

    def __init__(self, main_window):
        self.profiles = {}
        self._devices = []
        self.audio_output = None
        self.audio_devices = {}

        super().__init__(main_window)
        self.main_window = main_window
        self.layout = QtWidgets.QGridLayout(self, alignment=Qt.AlignTop)

        self.get_audio_devices()
        self._media_devices = QMediaDevices()
        self.quality = QtWidgets.QComboBox(self)
        self.use_opengl = AnimatedToggle("Use OpenGL", self)
        self.fps = QtWidgets.QComboBox(self)
        self.fps_show = AnimatedToggle("Show FPS", self)
        self.fullscreen = AnimatedToggle("Show Fullscreen", self)
        self.use_hw = AnimatedToggle("Use Hardware Decoding", self)
        self.resolution = QtWidgets.QComboBox(self)
        self.accounts = QtWidgets.QTreeWidget()
        self.audio_output = QtWidgets.QComboBox(self)
        self.decoder = QtWidgets.QComboBox(self)
        self.devices = QtWidgets.QTreeWidget()

        self._init_options()
        self.main_window.add_devices(self._devices)

    def _init_options(self):
        set_account = QtWidgets.QPushButton("Select Account")
        add_account = QtWidgets.QPushButton("Add Account")
        del_account = QtWidgets.QPushButton("Delete Account")
        add_device = QtWidgets.QPushButton("Add Device")
        del_device = QtWidgets.QPushButton("Remove Device")
        del_device.setDisabled(True)
        add_device.clicked.connect(self.new_device)
        del_device.clicked.connect(self.delete_device)
        del_device.clicked.connect(lambda: del_device.setDisabled(True))
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
        self.fps.addItems(["30", "60"])
        self.resolution.addItems(list(RESOLUTION_PRESETS.keys()))
        self.audio_output.addItems(list(self.audio_devices.keys()))
        self.decoder.addItems(self.get_decoder())
        self.devices.itemSelectionChanged.connect(lambda: del_device.setDisabled(False))
        self._media_devices.audioOutputsChanged.connect(self.get_audio_devices)

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
        self.accounts.itemDoubleClicked.connect(self.set_profiles)

        widgets = (
            ("Quality", self.quality, self.use_opengl),
            ("FPS", self.fps, self.fps_show),
            ("Resolution", self.resolution, self.fullscreen),
            (res_label,),
            ("Video Decoder", self.decoder, self.use_hw),
            ("Audio Output", self.audio_output),
        )

        for row_index, _row in enumerate(widgets):
            for col_index, item in enumerate(_row):
                if isinstance(item, str):
                    item = QtWidgets.QLabel(f"{item}:")
                self.layout.addWidget(item, row_index, col_index + 1, 1, 3 // len(_row))

        row = len(widgets)
        self.layout.addWidget(devices_label, row - 2, 5, 2, 2)
        self.layout.addItem(spacer(), row, 0)

        row += 1
        account_layout = QtWidgets.QHBoxLayout()
        account_layout.addWidget(set_account)
        account_layout.addWidget(add_account)
        account_layout.addWidget(del_account)
        self.layout.addLayout(account_layout, row, 1, 1, 3)
        device_layout = QtWidgets.QHBoxLayout()
        device_layout.addWidget(add_device)
        device_layout.addWidget(del_device)
        self.layout.addLayout(device_layout, row, 5, 1, 2)

        self.layout.addItem(spacer(), row, 4)
        self.layout.setColumnStretch(4, 1)
        row += 1

        self.layout.addWidget(self.accounts, row, 1, 2, 3)
        self.layout.addWidget(self.devices, row, 5, 2, 2)
        self.layout.setRowStretch(row + 1, 1)

    def get_decoder(self) -> list:
        """Return HW decoder or CPU if not found."""
        decoders = []
        found = AVReceiver.find_video_decoder(video_format="h264", use_hw=True)
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
        devices = QMediaDevices.audioOutputs()
        default = QMediaDevices.defaultAudioOutput()
        audio_devices = {f"{default.description()} (Default)": default}
        for device in devices:
            if device == default:
                continue
            audio_devices[device.description()] = device
        self.audio_devices = audio_devices
        if self.audio_output:
            self.audio_output.clear()
            self.audio_output.addItems(self.audio_devices)
        return audio_devices

    def get_audio_device(self):
        """Return Selected Audio Device."""
        return self.audio_devices.get(self.audio_output.currentText())

    def set_options(self) -> bool:
        """Set Options."""
        options = get_options()
        try:
            self.quality.setCurrentText(str(options["quality"]))
            self.use_opengl.setChecked(options["use_opengl"])
            self.fps.setCurrentText(str(options["fps"]))
            self.fps_show.setChecked(options["show_fps"])
            self.use_hw.setChecked(options["use_hw"])
            self.resolution.setCurrentText(options["resolution"])
            self.fullscreen.setChecked(options["fullscreen"])
            self._devices = options["devices"]

            decoder = options["decoder"]
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
        self.devices.clear()
        self.devices.setHeaderLabels(["Devices"])
        for host in self._devices:
            item = QtWidgets.QTreeWidgetItem(self.devices)
            item.setText(0, host)

    def set_profiles(self):
        """Set Profiles."""
        profile_name = self.selected_profile
        self.profiles = get_profiles()
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
        self.options_data.save()

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
            message(self.main_window, "Device Already Added", text, "warning")
            return
        # status = get_status(host)
        # if not status:
        #     text = f"Could not find device at: {host}."
        #     message(self.main_window, "Device not found", text, "warning")
        #     return
        self._devices.append(host)
        self.set_devices()
        self._change_options()
        self.main_window.add_devices(self._devices)

    def delete_device(self):
        """Delete Device."""
        items = self.devices.selectedItems()
        if not items:
            return
        item = items[0]
        host = item.text(0)
        self._devices.remove(host)
        self.set_devices()
        self._change_options()
        self.main_window.remove_device(host)

    def delete_profile(self):
        """Delete profile."""
        item = self.accounts.selectedItems()[0]
        if not item:
            return
        name = item.text(0)
        text = f"Are you sure you want to delete account: {name}"
        message(
            self,
            "Delete Account",
            text,
            "warning",
            lambda: self.remove_profile(name),
            escape=True,
        )

    def remove_profile(self, name):
        """Remove profile from config."""
        self.profiles.pop(name)
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
        new_message = message(
            self.main_window, title, text, "info", self.new_account, escape=True
        )
        new_message.hide()

    def new_account(self):
        """Run prompts to add PSN Account."""
        title = "Add PSN Account"
        dialog = QtWidgets.QInputDialog(self)
        dialog.setWindowTitle(title)
        dialog.setInputMode(QtWidgets.QInputDialog.TextInput)
        dialog.setOption(QtWidgets.QInputDialog.UseListViewForComboBoxItems)
        url = ""
        grp = 60
        for ivl in range(0, len(LOGIN_URL) // grp):
            end = grp * (ivl + 1)
            start = grp * ivl
            url = f"{url}{LOGIN_URL[start:end]}\n"
        url = f"{url}{LOGIN_URL[grp * -1]}"
        dialog.setComboBoxItems([url])
        dialog.setLabelText(
            "Go to the following url in a web browser and sign in using your PSN Account:\n\n"
            "You will be redirected after signing in to a blank page that says 'Redirect'.\n"
            "When you reach this page, copy the page URL and click 'Ok' to continue"
        )
        clipboard = QtWidgets.QApplication.clipboard()
        clipboard.clear(mode=clipboard.Clipboard)
        clipboard.setText(LOGIN_URL, mode=clipboard.Clipboard)
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
        message(self, title, text, level)

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
                self.profiles[name] = profile
                write_profiles(self.profiles)
                self.set_profiles()
                title = "Registration Successful"
                text = (
                    f"Successfully registered with device at: {host['host-ip']}\n"
                    f"with PSN Account: {name}."
                )
                level = "info"

        message(self, title, text, level)
        self.main_window.session_stop()

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
        options = Options(
            quality=self.quality.currentText(),
            use_opengl=self.use_opengl.isChecked(),
            fps=int(self.fps.currentText()),
            show_fps=self.fps_show.isChecked(),
            use_hw=self.use_hw.isChecked(),
            decoder=decoder,
            resolution=self.resolution.currentText(),
            fullscreen=self.fullscreen.isChecked(),
            profile=profile,
            devices=self._devices,
        )
        return options

    @property
    def options(self) -> dict:
        """Return Options as dict."""
        return asdict(self.options_data)
