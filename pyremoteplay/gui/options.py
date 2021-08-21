from pyps4_2ndscreen.ddp import get_status
from pyremoteplay.av import AVReceiver
from pyremoteplay.const import RESOLUTION_PRESETS, Quality
from pyremoteplay.oauth import LOGIN_URL, get_user_account
from pyremoteplay.register import register
from pyremoteplay.util import (add_profile, add_regist_data, get_mapping,
                               get_options, get_profiles, write_mapping,
                               write_options, write_profiles)
from PySide6 import QtCore, QtWidgets
from PySide6.QtCore import Qt

from .util import label, message, spacer


class ControlsTable(QtWidgets.QTableWidget):

    def mousePressEvent(self, event):
        super().mousePressEvent(event)
        self.parent().table_mousePressEvent(event)

    def keyPressEvent(self, event):
        self.parent().keyPressEvent(event)


class ControlsWidget(QtWidgets.QWidget):
    KEYS = (
        'STANDBY',
        'QUIT',
        'STICK_RIGHT_UP',
        'STICK_RIGHT_DOWN',
        'STICK_RIGHT_LEFT',
        'STICK_RIGHT_RIGHT',
        'STICK_LEFT_UP',
        'STICK_LEFT_DOWN',
        'STICK_LEFT_LEFT',
        'STICK_LEFT_RIGHT',
        'UP',
        'DOWN',
        'LEFT',
        'RIGHT',
        'L1',
        'L2',
        'L3',
        'R1',
        'R2',
        'R3',
        'CROSS',
        'CIRCLE',
        'SQUARE',
        'TRIANGLE',
        'OPTIONS',
        'SHARE',
        'PS',
        'TOUCHPAD'
    )

    DEFAULT_MAPPING = {
        "Key_Escape": "STANDBY",
        "Key_Q": "QUIT",
        "Key_Up": "STICK_RIGHT_UP",
        "Key_Down": "STICK_RIGHT_DOWN",
        "Key_Left": "STICK_RIGHT_LEFT",
        "Key_Right": "STICK_RIGHT_RIGHT",
        "Key_W": "STICK_LEFT_UP",
        "Key_S": "STICK_LEFT_DOWN",
        "Key_A": "STICK_LEFT_LEFT",
        "Key_D": "STICK_LEFT_RIGHT",
        "Key_1": "L1",
        "Key_2": "L2",
        "Key_3": "L3",
        "Key_4": "R1",
        "Key_5": "R2",
        "Key_6": "R3",
        "Key_Return": "CROSS",
        "Key_C": "CIRCLE",
        "Key_R": "SQUARE",
        "Key_T": "TRIANGLE",
        "Key_Backspace": "OPTIONS",
        "Key_Equal": "SHARE",
        "Key_P": "PS",
        "Key_0": "TOUCHPAD",
    }

    def __init__(self, main_window):
        super().__init__(main_window)
        self.main_window = main_window
        self.mapping = None
        self.options = None
        self.selected_map = ""
        self.layout = QtWidgets.QGridLayout(self, alignment=Qt.AlignTop)
        self.layout.setColumnMinimumWidth(0, 30)
        self.layout.setColumnStretch(0, 1)
        self.layout.setColumnStretch(1, 1)
        self.layout.setColumnStretch(2, 1)
        self.layout.setColumnStretch(3, 1)
        self.layout.setRowStretch(1, 1)
        self.table = ControlsTable(self)
        self.table.setRowCount(len(ControlsWidget.KEYS))
        self.table.setColumnCount(2)
        self.table.setHorizontalHeaderLabels(["Control", "Remote Play Control"])
        self.input = None
        header = self.table.horizontalHeader()       
        header.setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        header.setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeToContents)
        self.left_joystick = QtWidgets.QCheckBox("Show Left Joystick", self)
        self.right_joystick = QtWidgets.QCheckBox("Show Right Joystick", self)
        self.reset = QtWidgets.QPushButton("Reset to Default")
        self.clear = QtWidgets.QPushButton("Clear")
        self.cancel = QtWidgets.QPushButton("Cancel")
        self.init_controls()
        self.instructions()
        self.layout.addWidget(self.left_joystick, 0, 0)
        self.layout.addWidget(self.right_joystick, 0, 1)
        self.layout.addWidget(self.reset, 1, 0)
        self.layout.addWidget(self.clear, 1, 1)
        self.layout.addWidget(self.cancel, 1, 2)
        self.layout.addWidget(self.table, 2, 0, 1, 3)
        self.layout.addWidget(self.label, 2, 3)
        self.cancel.hide()
        self.clear.hide()
        self.left_joystick.clicked.connect(lambda: self.click_joystick("left"))
        self.right_joystick.clicked.connect(lambda: self.click_joystick("right"))
        self.cancel.clicked.connect(self.click_cancel)
        self.reset.clicked.connect(self.click_reset)
        self.clear.clicked.connect(self.click_clear)
        self.table.clicked.connect(self.click_table)

    def hide(self):
        self.click_cancel()
        super().hide()

    def get_map(self):
        return self.mapping['maps'][self.selected_map]["map"]

    def get_options(self):
        return self.mapping['maps'][self.selected_map]["options"]

    def default_mapping(self):
        if not self.options:
            options = {"joysticks": {"left": False, "right": False}}
        self.mapping = {}
        self.mapping.update(
            {
                "selected": "keyboard",
                "maps": {
                    "keyboard": {
                        "map": ControlsWidget.DEFAULT_MAPPING.copy(),
                        "options": options,
                    }
                }
            }
        )
        write_mapping(self.mapping)
        self.set_map(self.get_map())
        self.set_table()

    def init_controls(self):
        self.mapping = get_mapping()
        if not self.mapping:
            self.default_mapping()
        self.selected_map = self.mapping['selected']
        self.set_map(self.get_map())
        self.set_options(self.get_options())
        self.set_table()
        self.set_joysticks()

    def set_table(self):
        self.table.clearContents()
        self.click_cancel()
        if self.selected_map == "keyboard":
            self.set_keyboard()

    def set_joysticks(self):
        options = self.get_options()
        joysticks = options['joysticks']
        if joysticks['left']:
            self.left_joystick.setChecked(True)
        if joysticks['right']:
            self.right_joystick.setChecked(True)

    def set_options(self, options):
        self.mapping['maps'][self.selected_map]['options'] = options
        write_mapping(self.mapping)

    def set_map(self, _map):
        self.input = None
        self.mapping['maps'][self.selected_map]["map"] = _map
        write_mapping(self.mapping)

    def set_keyboard(self):
        remove_keys = []
        _map = self.get_map()

        for index, rp_key in enumerate(ControlsWidget.KEYS):
            item = QtWidgets.QTableWidgetItem(rp_key)
            item.setFlags(Qt.ItemIsEnabled)
            blank = QtWidgets.QTableWidgetItem()
            blank.setFlags(Qt.ItemIsEnabled)
            self.table.setItem(index, 1, item)
            self.table.setItem(index, 0, blank)
        for key, rp_key in _map.items():
            if rp_key not in ControlsWidget.KEYS:
                remove_keys.append(key)
                continue
            item = QtWidgets.QTableWidgetItem(key.replace("Key_", "").replace("Button", " Click"))
            item.setFlags(Qt.ItemIsEnabled)
            self.table.setItem(ControlsWidget.KEYS.index(rp_key), 0, item)
        if remove_keys:
            for key in remove_keys:
                _map.pop(key)
        self.set_map(_map)

    def instructions(self):
        text = (
            "To set a Control, click on the corresponding row "
            "and then press the key that you would like to map "
            "to the Remote Play Control."
        )
        self.label = QtWidgets.QLabel(text)
        self.label.setWordWrap(True)

    def click_joystick(self, stick):
        options = self.get_options()
        value = not options['joysticks'][stick]
        options['joysticks'][stick] = value
        self.set_options(options)

    def click_table(self, item):
        self.input = item.row()
        self.cancel.show()
        self.clear.show()

    def click_cancel(self):
        self.input = None
        self.cancel.hide()
        self.clear.hide()

    def click_clear(self):
        if self.input is None:
            return
        item = self.table.item(self.input, 0).text()
        if "Click" in item:
            key = item.replace(" Click", "Button")
        else:
            key = f"Key_{item}"
        _map = self.get_map()
        if key in _map:
            _map.pop(key)
        self.set_map(_map)
        self.set_table()

    def click_reset(self):
        text = "Reset input mapping to default?"
        message(self, "Reset Mapping", text, "warning", self.default_mapping, escape=True)

    def get_current_map_key(self, rp_key):
        _map = self.get_map()
        rp_keys = list(_map.values())
        if rp_key not in rp_keys:
            return None 
        index = rp_keys.index(rp_key)
        key = list(_map.keys())[index]
        return key

    def table_mousePressEvent(self, event):
        button = event.button().name.decode()
        self.set_item(button)

    def keyPressEvent(self, event):
        if self.input is not None:
            key = Qt.Key(event.key()).name.decode()
            self.set_item(key)

    def set_item(self, key):
        if self.input is not None:
            item = self.table.item(self.input, 0)
            rp_key = self.table.item(self.input, 1).text()
            current = self.get_current_map_key(rp_key)
            _map = self.get_map()

            # Delete the current key
            if current is not None:
                assert _map.get(current) == rp_key
                _map.pop(current)

            _map[key] = rp_key
            item.setText(key.replace("Key_", ""))
            self.set_map(_map)
            self.set_table()


class OptionsWidget(QtWidgets.QWidget):
    def __init__(self, main_window):
        super().__init__(main_window)
        self.main_window = main_window
        self.options = {}
        self.profiles = {}
        self.layout = QtWidgets.QGridLayout(self, alignment=Qt.AlignTop)
        self.layout.setColumnMinimumWidth(0, 30)
        self.layout.setColumnStretch(0, 1)
        self.layout.setColumnStretch(1, 1)
        self.layout.setColumnStretch(2, 1)
        self.layout.setColumnStretch(3, 1)
        self.layout.setColumnStretch(4, 1)
        self.layout.setColumnStretch(5, 1)

        # self.layout.setRowMinimumHeight(3, 20)
        # self.layout.setRowStretch(4, 1)

        self.init_options()
        self.load_options()
        success = self.set_options()
        if not success:
            if not self.set_options():
                raise RuntimeError("Failed to set options")
        self.set_profiles()
        self.set_devices()

    def init_options(self):
        set_account = QtWidgets.QPushButton("Select Account")
        add_account = QtWidgets.QPushButton("Add Account")
        del_account = QtWidgets.QPushButton("Delete Account")
        add_device = QtWidgets.QPushButton("Add Device")
        del_device = QtWidgets.QPushButton("Remove Device")
        add_device.clicked.connect(self.new_device)
        del_device.clicked.connect(self.delete_device)
        del_device.clicked.connect(del_device.hide)
        set_account.clicked.connect(self.change_profile)
        add_account.clicked.connect(self.new_profile)
        del_account.clicked.connect(self.delete_profile)
        res_label = label(self, "**1080p is for PS4 Pro, PS5 only**", wrap=False)
        hw_label = QtWidgets.QLineEdit(self)
        hw_label.setText(self.get_decoder())
        hw_label.setAlignment(Qt.AlignCenter)
        hw_label.setReadOnly(True)
        devices_label = label(
            self,
            "If your device is not showing up automatically, "
            "try adding the IP Address below.",
            wrap=True,
        )
        self.quality = QtWidgets.QComboBox(self)
        self.quality.addItems([item.name for item in Quality])
        self.quality.currentTextChanged.connect(self.change_quality)
        self.fps = QtWidgets.QComboBox(self)
        self.fps.addItems(["30", "60"])
        self.fps.currentTextChanged.connect(self.change_fps)
        self.fps_show = QtWidgets.QCheckBox("Show FPS", self)
        self.fps_show.stateChanged.connect(self.change_fps_show)
        self.fullscreen = QtWidgets.QCheckBox("Show Fullscreen", self)
        self.fullscreen.stateChanged.connect(self.change_fullscreen)
        self.use_hw = QtWidgets.QCheckBox("Use Hardware Decoding", self)
        self.use_hw.stateChanged.connect(self.change_use_hw)
        self.resolution = QtWidgets.QComboBox(self)
        self.resolution.addItems(list(RESOLUTION_PRESETS.keys()))
        self.resolution.currentTextChanged.connect(self.change_resolution)
        self.accounts = QtWidgets.QTreeWidget()
        self.accounts.itemDoubleClicked.connect(self.change_profile)
        self.devices = QtWidgets.QTreeWidget()
        self.devices.itemClicked.connect(del_device.show)

        self.add(self.quality, 0, 1, label=label(self, "Quality:"))
        self.add(self.fps, 1, 1, label=label(self, "FPS:"))
        self.add(self.fps_show, 1, 2)
        self.add(self.resolution, 2, 1, label=label(self, "Resolution:"))
        self.add(self.fullscreen, 2, 2)
        self.layout.addWidget(res_label, 3, 0, 1, 2)
        self.add(hw_label, 4, 1, label=label(self, "Available Decoder:"))
        self.layout.addWidget(self.use_hw, 4, 2)
        self.layout.addWidget(devices_label, 4, 4, 1, 2)
        self.layout.addItem(spacer(), 5, 0)
        self.add(set_account, 6, 0)
        self.add(add_account, 6, 1)
        self.add(del_account, 6, 2)
        self.add(add_device, 6, 4)
        self.add(del_device, 6, 5)
        self.layout.addWidget(self.accounts, 7, 0, 3, 3)
        self.layout.addItem(spacer(), 7, 3)
        self.layout.addWidget(self.devices, 7, 4, 3, 2)
        del_device.hide()

    def get_decoder(self):
        decoder = AVReceiver.find_video_decoder(video_format="h264", use_hw=True)
        decoder = decoder.replace("h264", "").replace("_", "")
        if not decoder:
            decoder = "CPU"
        return decoder

    def set_options(self) -> bool:
        try:
            self.quality.setCurrentText(str(self.options["quality"]))
            self.fps.setCurrentText(str(self.options["fps"]))
            self.fps_show.setChecked(self.options["show_fps"])
            self.use_hw.setChecked(self.options["use_hw"])
            self.resolution.setCurrentText(self.options["resolution"])
            self.fullscreen.setChecked(self.options["fullscreen"])
            self.profile = self.options["profile"]
        except KeyError:
            self.options = self.default_options()
            return False
        return True

    def default_options(self) -> dict:
        options = {
            "quality": "Default",
            "fps": 30,
            "show_fps": False,
            "resolution": "720p",
            "use_hw": False,
            "fullscreen": False,
            "profile": "",
            "devices": [],
        }
        write_options(options)
        return options

    def load_options(self):
        options = get_options()
        if not options:
            options = self.default_options()
        self.options = options

    def set_devices(self):
        if not self.options.get("devices"):
            self.options["devices"] = []
        self.devices.clear()
        self.devices.setHeaderLabels(["Devices"])
        for host in self.options["devices"]:
            item = QtWidgets.QTreeWidgetItem(self.devices)
            item.setText(0, host)

    def set_profiles(self):
        self.profiles = get_profiles()
        self.accounts.clear()
        self.accounts.setHeaderLabels(["PSN ID", "Active", "Is Registered", "Devices"])
        if not self.profiles:
            return
        accounts = list(self.profiles.keys())
        for profile, data in self.profiles.items():
            item = QtWidgets.QTreeWidgetItem(self.accounts)
            hosts = data.get('hosts')
            mac_addresses = []
            if hosts:
                for host in hosts.keys():
                    mac_addresses.append(host)
            is_registered = "Yes" if data.get('hosts') else "No"
            item.setText(0, profile)
            item.setText(1, "No")
            item.setText(2, is_registered)
            item.setText(3, ", ".join(mac_addresses))
        if not self.options['profile']:
            self.options['profile'] = accounts[0]
            write_options(self.options)
        selected = self.accounts.findItems(self.options['profile'], Qt.MatchFixedString, column=0)
        if selected and len(selected) == 1:
            selected[0].setSelected(True)
            selected[0].setText(1, "Yes")

    def add(self, item, row, col, label=None):
        self.layout.addWidget(item, row, col, Qt.AlignLeft)
        if label is not None:
            self.layout.addWidget(label, row, col - 1, Qt.AlignLeft)

    def change_quality(self, text):
        self.options["quality"] = text
        write_options(self.options)

    def change_fps(self, text):
        self.options["fps"] = int(text)
        write_options(self.options)

    def change_fps_show(self):
        self.options["show_fps"] = self.fps_show.isChecked()
        write_options(self.options)

    def change_resolution(self, text):
        self.options["resolution"] = text
        write_options(self.options)

    def change_use_hw(self):
        self.options["use_hw"] = self.use_hw.isChecked()
        write_options(self.options)

    def change_fullscreen(self):
        self.options["fullscreen"] = self.fullscreen.isChecked()
        write_options(self.options)

    def change_profile(self):
        item = self.accounts.selectedItems()[0]
        if not item:
            return
        self.options["profile"] = item.text(0)
        write_options(self.options)
        self.set_profiles()

    def new_device(self):
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
        if host in self.options["devices"]:
            text = "Device is already added."
            message(self.main_window, "Device Already Added", text, "warning")
            return
        status = get_status(host)
        if not status:
            text = f"Could not find device at: {host}."
            message(self.main_window, "Device not found", text, "warning")
            return
        self.options["devices"].append(host)
        write_options(self.options)
        self.set_devices()

    def delete_device(self):
        items = self.devices.selectedItems()
        if not items:
            return
        item = items[0]
        host = item.text(0)
        self.options["devices"].remove(host)
        write_options(self.options)
        self.set_devices()

    def delete_profile(self):
        item = self.accounts.selectedItems()[0]
        if not item:
            return
        name = item.text(0)
        text = f"Are you sure you want to delete account: {name}"
        message(self, "Delete Account" , text, "warning", lambda: self.remove_profile(name), escape=True)

    def remove_profile(self, name):
        self.profiles.pop(name)
        write_profiles(self.profiles)
        self.options['profile'] = list(self.profiles.keys())[0] if self.profiles else ""
        write_options(self.options)
        self.set_options()
        self.set_profiles()

    def new_profile(self):
        title = "Add PSN Account"
        text = "To Add a New PSN Account you will have to sign in to your PSN Account. Continue?"
        new_message = message(self.main_window, title, text, "info", self.new_account, escape=True)
        new_message.hide()

    def new_account(self):
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
        cb = QtWidgets.QApplication.clipboard()
        cb.clear(mode=cb.Clipboard)
        cb.setText(LOGIN_URL, mode=cb.Clipboard)
        if not dialog.exec():
            return

        dialog = QtWidgets.QInputDialog(self)
        dialog.setWindowTitle(title)
        dialog.setInputMode(QtWidgets.QInputDialog.TextInput)
        dialog.setLabelText("Copy and Paste the URL of the page that says 'Redirect' below:")
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
            self.options['profile'] = user_id
            write_options(self.options)
            self.set_options()
            self.set_profiles()
            text = f"Successfully added PSN account: {user_id}"
            level = "info"
        message(self, title, text, level)

    def register(self, host, name):
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
