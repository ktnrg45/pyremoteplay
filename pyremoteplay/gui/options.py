# pylint: disable=c-extension-no-member,invalid-name
"""Options Widget."""
import socket

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


class OptionsWidget(QtWidgets.QWidget):
    """Widget for Options."""

    def __init__(self, main_window):
        super().__init__(main_window)
        self.main_window = main_window
        self.options = {}
        self.profiles = {}
        self.layout = QtWidgets.QGridLayout(self, alignment=Qt.AlignTop)

        self.audio_output = None
        self.audio_devices = {}
        self.get_audio_devices()
        self._media_devices = QMediaDevices()
        self._media_devices.audioOutputsChanged.connect(self.get_audio_devices)

        self._init_options()
        self.load_options()
        success = self.set_options()
        if not success:
            if not self.set_options():
                raise RuntimeError("Failed to set options")
        self.set_profiles()
        self.set_devices()
        self.main_window.add_devices(self.options["devices"])

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
        set_account.clicked.connect(self._change_profile)
        add_account.clicked.connect(self.new_profile)
        del_account.clicked.connect(self.delete_profile)
        res_label = label(self, "**1080p is for PS4 Pro, PS5 only**", wrap=False)

        devices_label = label(
            self,
            "If your device is not showing up automatically, "
            "try adding the IP Address below.",
            wrap=True,
        )

        self.quality = QtWidgets.QComboBox(self)
        self.quality.addItems([item.name for item in Quality])
        self.quality.currentTextChanged.connect(self._change_quality)
        self.use_opengl = AnimatedToggle("Use OpenGL", self)
        self.use_opengl.stateChanged.connect(self._change_opengl)
        self.use_opengl.setToolTip("Recommended if using Hardware Decoding")
        self.fps = QtWidgets.QComboBox(self)
        self.fps.addItems(["30", "60"])
        self.fps.currentTextChanged.connect(self._change_fps)
        self.fps_show = AnimatedToggle("Show FPS", self)
        self.fps_show.stateChanged.connect(self._change_fps_show)
        self.fullscreen = AnimatedToggle("Show Fullscreen", self)
        self.fullscreen.stateChanged.connect(self._change_fullscreen)
        self.use_hw = AnimatedToggle("Use Hardware Decoding", self)
        self.use_hw.stateChanged.connect(self._change_use_hw)
        self.resolution = QtWidgets.QComboBox(self)
        self.resolution.addItems(list(RESOLUTION_PRESETS.keys()))
        self.resolution.currentTextChanged.connect(self._change_resolution)
        self.accounts = QtWidgets.QTreeWidget()
        self.accounts.itemDoubleClicked.connect(self._change_profile)
        self.devices = QtWidgets.QTreeWidget()
        self.devices.itemSelectionChanged.connect(lambda: del_device.setDisabled(False))
        self.audio_output = QtWidgets.QComboBox(self)
        self.audio_output.addItems(list(self.audio_devices.keys()))
        self.decoder = QtWidgets.QComboBox(self)
        self.decoder.addItems(self.get_decoder())
        self.decoder.currentTextChanged.connect(self._change_decoder)

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
        try:
            self.quality.setCurrentText(str(self.options["quality"]))
            self.use_opengl.setChecked(self.options["use_opengl"])
            self.fps.setCurrentText(str(self.options["fps"]))
            self.fps_show.setChecked(self.options["show_fps"])
            self.use_hw.setChecked(self.options["use_hw"])
            self.resolution.setCurrentText(self.options["resolution"])
            self.fullscreen.setChecked(self.options["fullscreen"])
            self.profile = self.options["profile"]

            decoder = self.options["decoder"]
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
            self.options = self.default_options()
            return False
        return True

    def default_options(self) -> dict:
        """Return Default Options."""
        options = {
            "quality": "Default",
            "use_opengl": False,
            "fps": 30,
            "show_fps": False,
            "resolution": "720p",
            "use_hw": False,
            "fullscreen": False,
            "decoder": "CPU",
            "profile": "",
            "devices": [],
        }
        write_options(options)
        return options

    def load_options(self):
        """Load Options."""
        options = get_options()
        if not options:
            options = self.default_options()
        self.options = options

    def set_devices(self):
        """Set devices."""
        self.devices.clear()
        self.devices.setHeaderLabels(["Devices"])
        for host in self.options["devices"]:
            item = QtWidgets.QTreeWidgetItem(self.devices)
            item.setText(0, host)

    def set_profiles(self):
        """Set Profiles."""
        self.profiles = get_profiles()
        self.accounts.clear()
        self.accounts.setHeaderLabels(["PSN ID", "Active", "Is Registered", "Devices"])
        if not self.profiles:
            return
        accounts = list(self.profiles.keys())
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
        if not self.options["profile"]:
            self.options["profile"] = accounts[0]
            write_options(self.options)
        selected = self.accounts.findItems(
            self.options["profile"], Qt.MatchFixedString, column=0
        )
        if selected and len(selected) == 1:
            selected[0].setSelected(True)
            selected[0].setText(1, "Yes")

    def _change_quality(self, text):
        self.options["quality"] = text
        write_options(self.options)

    def _change_opengl(self):
        self.options["use_opengl"] = self.use_opengl.isChecked()
        write_options(self.options)

    def _change_fps(self, text):
        self.options["fps"] = int(text)
        write_options(self.options)

    def _change_fps_show(self):
        self.options["show_fps"] = self.fps_show.isChecked()
        write_options(self.options)

    def _change_resolution(self, text):
        self.options["resolution"] = text
        write_options(self.options)

    def _change_decoder(self, text):
        if text != "CPU":
            text = text.split(" (")[0]
            self.options["decoder"] = text
            write_options(self.options)

    def _change_use_hw(self):
        self.options["use_hw"] = self.use_hw.isChecked()
        write_options(self.options)

    def _change_fullscreen(self):
        self.options["fullscreen"] = self.fullscreen.isChecked()
        write_options(self.options)

    def _change_profile(self):
        item = self.accounts.selectedItems()[0]
        if not item:
            return
        self.options["profile"] = item.text(0)
        write_options(self.options)
        self.set_profiles()

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
        if host in self.options["devices"]:
            text = "Device is already added."
            message(self.main_window, "Device Already Added", text, "warning")
            return
        # status = get_status(host)
        # if not status:
        #     text = f"Could not find device at: {host}."
        #     message(self.main_window, "Device not found", text, "warning")
        #     return
        self.options["devices"].append(host)
        write_options(self.options)
        self.set_devices()
        self.main_window.add_devices(self.options["devices"])

    def delete_device(self):
        """Delete Device."""
        items = self.devices.selectedItems()
        if not items:
            return
        item = items[0]
        host = item.text(0)
        self.options["devices"].remove(host)
        write_options(self.options)
        self.set_devices()
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
        self.options["profile"] = list(self.profiles.keys())[0] if self.profiles else ""
        write_options(self.options)
        self.set_options()
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
            self.options["profile"] = user_id
            write_options(self.options)
            self.set_options()
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
