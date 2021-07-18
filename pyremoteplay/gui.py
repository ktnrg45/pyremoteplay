import asyncio
import sys
import threading
import time

import aiohttp
from pyps4_2ndscreen.ddp import search, get_status
from pyps4_2ndscreen.media_art import async_search_ps_store

from .av import GUIReceiver, QueueReceiver
from .const import RESOLUTION_PRESETS
from .ctrl import CTRLAsync, CTRL, send_wakeup
from .oauth import LOGIN_URL, get_user_account
from .register import register
from .util import (add_profile, get_options, get_profiles, write_options,
                   write_profiles, get_mapping, write_mapping)

try:
    from PySide6 import QtCore, QtGui, QtWidgets
    from PySide6.QtCore import Qt
except ModuleNotFoundError:
    pass


class CTRLWorker(QtCore.QObject):
    finished = QtCore.Signal()

    def __init__(self, window, ctrl):
        super().__init__()
        self.window = window
        self.ctrl = ctrl
        self.worker = None
        self.loop = None

    def run(self):
        self.worker = threading.Thread(
            target=self.worker_target,
        )
        self.worker.start()

    def worker_target(self):
        self.ctrl.loop = asyncio.new_event_loop()
        task = self.ctrl.loop.create_task(self.start())
        print("CTRL Start")
        self.ctrl.loop.run_until_complete(task)
        print("CTRL Finished")
        if self.ctrl.loop.is_running():
            self.ctrl.loop.stop()
        if not self.ctrl.loop.is_closed():
            self.ctrl.loop.close()
        del self.ctrl
        self.finished.emit()

    async def start(self):
        status = await self.ctrl.start()
        if not status:
            print("CTRL Failed to Start")
            message(None, "Error", self.ctrl.error)
            self.ctrl.loop.stop()
        while not self.ctrl.is_stopped:
            await asyncio.sleep(0.001)


class AVProcessor(QtCore.QObject):
    frame = QtCore.Signal()

    def __init__(self, window):
        super().__init__()
        self.window = window
        self.video_output = self.window.video_output
        self.v_queue = self.window.v_queue
        self.frame_mutex = QtCore.QMutex()

    def next_frame(self):
        self.frame_mutex.lock()
        try:
            frame = self.v_queue.popleft()
        except IndexError:
            self.frame_mutex.unlock()
            return
        img = QtGui.QImage(frame, frame.shape[1], frame.shape[0], QtGui.QImage.Format_RGB888)
        pix = QtGui.QPixmap.fromImage(img)
        self.video_output.setPixmap(pix)
        self.frame.emit()
        self.frame_mutex.unlock()


class CTRLWindow(QtWidgets.QWidget):
    def __init__(self, main_window, host, name, profile, resolution='720p', fps=60, show_fps=False, fullscreen=False, input_map=None):
        super().__init__()
        self.setAttribute(QtCore.Qt.WA_DeleteOnClose)
        self._main_window = main_window
        self.mapping = ControlsWidget.DEFAULT_MAPPING if input_map is None else input_map
        self.host = host
        self.profile = profile
        self.fps = fps
        self.ctrl = CTRLAsync(self.host, self.profile, resolution=resolution, fps=fps, av_receiver=GUIReceiver)
        self.width = self.ctrl.resolution["width"]
        self.height = self.ctrl.resolution["height"]
        self.v_queue = self.ctrl.av_receiver.v_queue
        # self.ctrl.av_receiver.add_audio_cb(self.handle_audio)
        self.controller = self.ctrl.controller

        self.setWindowTitle(f"Session {name} @ {host}")
        self.setStyleSheet("background-color: black")
        self.resize(self.width, self.height)
        self.video_output = QtWidgets.QLabel(alignment=QtCore.Qt.AlignCenter)
        self.audio_output = None
        self.layout = QtWidgets.QVBoxLayout(self)
        self.layout.addWidget(self.video_output)

        self.worker = CTRLWorker(self, self.ctrl)
        self.av_worker = AVProcessor(self)

        self.thread = QtCore.QThread()
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.finished.connect(self.close)

        self.av_thread = QtCore.QThread()
        self.av_worker.moveToThread(self.av_thread)
        self.av_thread.started.connect(self.start_timer)
        self.av_worker.frame.connect(self.set_fps)

        self.fps_label = None
        if show_fps:
            self.init_fps()
        if fullscreen:
            self.showMaximized()


# Waiting on pyside6.2
#    def init_audio(self):
#        config = self._a_stream._audio_config
#        format = QtMultimedia.QAudioFormat()
#        format.setChannels(config['channels'])
#        format.setFrequency(config['rate'])
#        format.setSampleSize(config['bits'])
#        format.setCodec("audio/pcm")
#        format.setByteOrder(QtMultimedia.QAudioFormat.LittleEndian)
#        format.setSampleType(QtMultimedia.QAudioFormat.SignedInt)
#        output = QtMultimedia.QAudioOutput(format)
#        self.audio_output = output.start()

    def init_fps(self):
        self.last_time = time.time()
        self.fps_label = get_label("FPS: ", self)
        self.fps_label.move(20, 20)
        self.fps_label.setStyleSheet("background-color:#33333333;color:white;padding-left:5px;")
        self.fps_sample = 0

    def set_fps(self):
        if self.fps_label is not None:
            self.fps_sample += 1
            if self.fps_sample < self.fps:
                return
            now = time.time()
            delta = now - self.last_time
            self.last_time = now
            self.fps_label.setText(f"FPS: {int(self.fps/delta)}")
            self.fps_sample = 0

    def handle_audio(self, data):
        if self.audio_output is None:
            self.init_audio()
        self.audio_output.write()

    def stick_state(self, button: str, release=False):
        button = button.split("_")
        stick = button[1]
        direction = button[2]
        if direction in ("LEFT", "RIGHT"):
            axis = "X"
        else:
            axis = "Y"
        if release:
            value = 0
        elif direction in ("UP", "LEFT"):
            value = self.controller.STICK_STATE_MIN
        else:
            value = self.controller.STICK_STATE_MAX
        return stick, axis, value

    def keyPressEvent(self, event):
        key = Qt.Key(event.key()).name.decode()
        button = self.mapping.get(key)
        if button is None:
            print(f"Button Invalid: {key}")
            return
        if button == "QUIT":
            self.close()
            return
        if button == "STANDBY":
            message(self, "Standby", "Set host to standby?", level="info", cb=self.standby, escape=True)
            return
        if "STICK" in button:
            stick, axis, value = self.stick_state(button, release=False)
            self.controller.stick(stick, axis, value)
        else:
            if not event.isAutoRepeat():
                self.controller.button(button, "press")
        event.accept()

    def keyReleaseEvent(self, event):
        if event.isAutoRepeat():
            return
        key = Qt.Key(event.key()).name.decode()
        button = self.mapping.get(key)
        if button is None:
            print(f"Button Invalid: {key}")
            return
        if button in ["QUIT", "STANDBY"]:
            return
        if "STICK" in button:
            stick, axis, value = self.stick_state(button, release=True)
            self.controller.stick(stick, axis, value)
        else:
            self.controller.button(button, "release")
        event.accept()

    def standby(self):
        self.ctrl.standby()
        self.stop()

    def start(self):
        self.thread.start()
        self.av_thread.start()

    def closeEvent(self, event):
        self.stop()
        event.accept()

    def stop(self):
        self.timer.stop()
        self.ctrl.stop()
        print(f"Stopping Session @ {self.host}")
        self.thread.quit()
        self.av_thread.quit()
        self.v_queue = None
        self.ctrl = None
        self.thread.started.disconnect(self.worker.run)
        self.worker.finished.disconnect(self.close)
        self.av_thread.started.disconnect(self.start_timer)
        self.av_worker.frame.disconnect(self.set_fps)
        self._main_window.session_stop()

    def start_timer(self):
        print("AV Processor Started")
        self.timer = QtCore.QTimer()
        self.timer.setTimerType(QtCore.Qt.PreciseTimer)
        self.timer.timeout.connect(self.av_worker.next_frame)
        self.timer.start(1000.0/self.fps)


class ToolbarWidget(QtWidgets.QWidget):
    def __init__(self, main_window):
        super().__init__(main_window)
        self.main_window = main_window
        self.layout = QtWidgets.QHBoxLayout(self)
        self.layout.setAlignment(QtCore.Qt.AlignTop | QtCore.Qt.AlignRight)
        self.refresh = QtWidgets.QPushButton("Auto Refresh")
        self.controls = QtWidgets.QPushButton("Controls")
        self.options = QtWidgets.QPushButton("Options")
        self.home = QtWidgets.QPushButton("Home")
        self.home.hide()
        self.buttons = [self.home, self.refresh, self.controls, self.options]
        self.options.clicked.connect(self.options_click)
        self.refresh.clicked.connect(self.refresh_click)
        self.controls.clicked.connect(self.controls_click)
        self.home.clicked.connect(self.home_click)

        for button in self.buttons:
            button.setMaximumWidth(200)
            button.setCheckable(True)
            self.layout.addWidget(button)
        self.home.setCheckable(False)

    def main_hide(self):
        self.main_window.device_grid.hide()
        self.refresh.hide()
        self.home.show()
        self.main_window.center_text.hide()

    def main_show(self):
        self.main_window.device_grid.show()
        self.refresh.show()

    def home_click(self):
        self.main_show()
        self.options_hide()
        self.controls_hide()
        self.home.hide()

    def options_click(self):
        if self.options.isChecked():
            self.main_hide()
            self.options_show()
            self.controls_hide()
        else:
            self.home_click()

    def options_show(self):
        self.main_window.options.show()

    def options_hide(self):
        self.options.setChecked(False)
        self.main_window.options.hide()

    def controls_click(self):
        if self.controls.isChecked():
            self.main_hide()
            self.controls_show()
            self.options_hide()
        else:
            self.home_click()

    def controls_show(self):
        self.main_window.controls.show()

    def controls_hide(self):
        self.controls.setChecked(False)
        self.main_window.controls.hide()

    def refresh_click(self):
        if self.refresh.isChecked():
            self.main_window.device_grid.start_timer()
        else:
            self.refresh_reset()

    def refresh_reset(self):
        self.refresh.setChecked(False)
        self.main_window.device_grid.timer.stop()


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
        self.selected_map = ""
        self.layout = QtWidgets.QGridLayout(self, alignment=QtCore.Qt.AlignTop)
        self.layout.setColumnMinimumWidth(0, 30)
        self.layout.setColumnStretch(0, 1)
        self.layout.setColumnStretch(1, 1)
        self.layout.setColumnStretch(2, 1)
        self.layout.setColumnStretch(3, 1)
        self.layout.setRowStretch(1, 1)
        self.table = QtWidgets.QTableWidget(self)
        self.table.setRowCount(len(ControlsWidget.KEYS))
        self.table.setColumnCount(2)
        self.table.setHorizontalHeaderLabels(["Control", "Remote Play Control"])
        self.input = None
        header = self.table.horizontalHeader()       
        header.setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        header.setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeToContents)
        self.reset = QtWidgets.QPushButton("Reset to Default")
        self.clear = QtWidgets.QPushButton("Clear")
        self.cancel = QtWidgets.QPushButton("Cancel")
        self.set_table()
        self.instructions()
        self.layout.addWidget(self.table, 1, 0, 1, 3)
        self.layout.addWidget(self.reset, 0, 0)
        self.layout.addWidget(self.clear, 0, 1)
        self.layout.addWidget(self.cancel, 0, 2)
        self.layout.addWidget(self.label, 1, 3)
        self.cancel.hide()
        self.clear.hide()
        self.cancel.clicked.connect(self.click_cancel)
        self.reset.clicked.connect(self.click_reset)
        self.clear.clicked.connect(self.click_clear)
        self.table.clicked.connect(self.click_table)
        self.table.keyPressEvent = self.table_keypress

    def hide(self):
        self.click_cancel()
        super().hide()

    def default_mapping(self):
        self.mapping = {
            "selected" : "keyboard",
            "maps": {
                "keyboard": ControlsWidget.DEFAULT_MAPPING,
            }
        }
        write_mapping(self.mapping)

    def set_table(self):
        self.table.clearContents()
        self.click_cancel()
        self.mapping = get_mapping()
        if not self.mapping:
            self.default_mapping()
        self.selected_map = self.mapping['selected']
        self.map = self.mapping['maps'][self.selected_map]
        if self.selected_map == "keyboard":
            self.set_keyboard()

    def set_map(self):
        self.input = None
        self.mapping['maps'][self.selected_map] = self.map
        write_mapping(self.mapping)

    def set_keyboard(self):
        remove_keys = []
        for index, rp_key in enumerate(ControlsWidget.KEYS):
            item = QtWidgets.QTableWidgetItem(rp_key)
            item.setFlags(QtCore.Qt.ItemIsEnabled)
            blank = QtWidgets.QTableWidgetItem()
            blank.setFlags(QtCore.Qt.ItemIsEnabled)
            self.table.setItem(index, 1, item)
            self.table.setItem(index, 0, blank)
        for key, rp_key in self.map.items():
            if rp_key not in ControlsWidget.KEYS:
                remove_keys.append(key)
                continue
            item = QtWidgets.QTableWidgetItem(key.replace("Key_", ""))
            item.setFlags(Qt.ItemIsEnabled)
            self.table.setItem(ControlsWidget.KEYS.index(rp_key), 0, item)
        if remove_keys:
            for key in remove_keys:
                self.map.pop(key)
        self.set_map()

    def instructions(self):
        text = (
            "To set a Control, click on the corresponding row "
            "and then press the key that you would like to map "
            "to the Remote Play Control."
        )
        self.label = QtWidgets.QLabel(text)
        self.label.setWordWrap(True)

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
        key = f"Key_{item}"
        if key in self.map:
            self.map.pop(key)
        self.set_map()
        self.set_table()

    def click_reset(self):
        text = "Reset input mapping to default?"
        msg = message(self, "Reset Mapping" , text, "warning", self.default_mapping, escape=True)
        self.set_table()

    def get_current_map_key(self, rp_key):
        rp_keys = list(self.map.values())
        if rp_key not in rp_keys:
            return None 
        index = rp_keys.index(rp_key)
        key = list(self.map.keys())[index]
        return key

    def table_keypress(self, event):
        if self.input is not None:
            _map = self.mapping['maps']['keyboard'] 
            key = Qt.Key(event.key()).name.decode()
            item = self.table.item(self.input, 0)
            rp_key = self.table.item(self.input, 1).text()
            current = self.get_current_map_key(rp_key)

            # Delete the current key
            if current is not None:
                assert self.map.get(current) == rp_key
                self.map.pop(current)

            self.map[key] = rp_key
            item.setText(key.replace("Key_", ""))
            self.set_map()
            self.set_table()


class OptionsWidget(QtWidgets.QWidget):
    def __init__(self, main_window):
        super().__init__(main_window)
        self.main_window = main_window
        self.options = {}
        self.profiles = {}
        self.layout = QtWidgets.QGridLayout(self, alignment=QtCore.Qt.AlignTop)
        self.layout.setColumnMinimumWidth(0, 30)
        self.layout.setColumnStretch(0, 1)
        self.layout.setColumnStretch(1, 1)
        self.layout.setColumnStretch(2, 1)
        self.layout.setColumnStretch(3, 1)
        self.layout.setColumnStretch(4, 2)

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
        set_account.clicked.connect(self.change_profile)
        add_account.clicked.connect(self.new_profile)
        del_account.clicked.connect(self.delete_profile)
        self.fps = QtWidgets.QComboBox(self)
        self.fps.addItems(["30", "60"])
        self.fps.currentTextChanged.connect(self.change_fps)
        self.fps_show = QtWidgets.QCheckBox("Show FPS", self)
        self.fps_show.stateChanged.connect(self.change_fps_show)
        self.fullscreen = QtWidgets.QCheckBox("Show Fullscreen", self)
        self.fullscreen.stateChanged.connect(self.change_fullscreen)
        self.resolution = QtWidgets.QComboBox(self)
        self.resolution.addItems(list(RESOLUTION_PRESETS.keys()))
        self.resolution.currentTextChanged.connect(self.change_resolution)
        self.accounts = QtWidgets.QTreeWidget()
        self.accounts.itemDoubleClicked.connect(self.change_profile)
        self.devices = QtWidgets.QTreeWidget()

        self.add(self.fps, 0, 1, label=get_label("FPS:", self))
        self.add(self.fps_show, 0, 2)
        self.add(self.resolution, 1, 1, label=get_label("Resolution:", self))
        self.add(self.fullscreen, 1, 2)
        self.layout.addItem(spacer(), 2, 0)
        self.add(set_account, 3, 0)
        self.add(add_account, 3, 1)
        self.add(del_account, 3, 2)
        self.layout.addWidget(self.accounts, 4, 0, 3, 3)
        self.layout.addItem(spacer(), 4, 3)
        self.add(add_device, 3, 4)
        self.add(del_device, 3, 5)
        self.layout.addWidget(self.devices, 4, 4, 3, 2)

    def set_options(self) -> bool:
        try:
            self.fps.setCurrentText(str(self.options["fps"]))
            self.fps_show.setChecked(self.options["show_fps"])
            self.resolution.setCurrentText(self.options["resolution"])
            self.fullscreen.setChecked(self.options["fullscreen"])
            self.profile = self.options["profile"]
        except KeyError:
            self.options = self.default_options()
            return False
        return True

    def default_options(self) -> dict:
        options = {
            "fps": 60,
            "show_fps": False,
            "resolution": "720p",
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
        selected = self.accounts.findItems(self.options['profile'], QtCore.Qt.MatchFixedString, column=0)
        if selected and len(selected) == 1:
            selected[0].setSelected(True)
            selected[0].setText(1, "Yes")

    def add(self, item, row, col, label=None):
        self.layout.addWidget(item, row, col, QtCore.Qt.AlignLeft)
        if label is not None:
            self.layout.addWidget(label, row, col - 1, QtCore.Qt.AlignLeft)

    def change_fps(self, text):
        self.options["fps"] = int(text)
        write_options(self.options)

    def change_fps_show(self):
        self.options["show_fps"] = self.fps_show.isChecked()
        write_options(self.options)

    def change_resolution(self, text):
        self.options["resolution"] = text
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
        item = self.devices.selectedItems()[0]
        if not item:
            return
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
        msg = message(self, "Delete Account" , text, "warning", lambda: self.remove_profile(name), escape=True)

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
        profiles = add_profile(self.main_window.profiles, account)
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
                mac_address = host['host-id']
                profile['hosts'][mac_address] = {'data': data, 'type': ''}
                for h_type in ['PS4', 'PS5']:
                    if f"{h_type}-RegistKey" in list(data.keys()):
                        profile['hosts'][mac_address]['type'] = h_type
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


class DeviceGrid(QtWidgets.QWidget):

    class DeviceButton(QtWidgets.QPushButton):
        COLOR_DARK = "#000000"
        COLOR_LIGHT = "#FFFFFF"
        COLOR_BG = "#E9ECEF"
        def __init__(self, main_window, host):
            super().__init__()
            self.info = ""
            self.main_window = main_window
            self.host = host
            self.info_show = False
            self.menu = QtWidgets.QMenu(self)
            self.clicked.connect(lambda: self.main_window.connect_host(self.host))
            self.clicked.connect(lambda: self.setEnabled(False))
            self.text_color = self.COLOR_DARK
            self.bg_color = self.COLOR_BG
            self.border_color =  ("#A3A3A3", "#A3A3A3")

            self.init_actions()
            self.get_info()
            self.get_text()
            self.set_image()
            self.set_style()

        def init_actions(self):
            self.action_info = QtGui.QAction(self)
            self.action_info.triggered.connect(self.toggle_info)
            self.menu.addAction(self.action_info)
            self.action_power = QtGui.QAction(self)
            self.menu.addAction(self.action_power)
            self.action_power.triggered.connect(self.toggle_power)

        def set_style(self):
            if self.host["status_code"] == 200:
                self.border_color = ("#6EA8FE", "#0D6EFD")
            else:
                self.border_color =  ("#FEB272", "#FFC107")
            self.setStyleSheet("".join(
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
            ))

        def update_state(self, state):
            cur_id = self.host.get("running-app-titleid")
            new_id = state.get("running-app-titleid")
            self.host = state
            self.get_info()
            self.get_text()
            if cur_id != new_id:
                self.set_image()
            self.set_style()

        def set_image(self):
            async def get_image(title_id):
                image = None
                item = await async_search_ps_store(title_id, "United States")
                if item is None:
                    return None
                if item.cover_art is None:
                    return None
                async with aiohttp.ClientSession() as session:
                    async with session.get(item.cover_art) as response:
                        image = await response.read()
                return image

            self.bg_color = self.COLOR_BG
            title_id = self.host.get("running-app-titleid")
            if title_id:
                image = asyncio.run(get_image(title_id))
                asyncio.set_event_loop(asyncio.new_event_loop())
                if image is not None:
                    pix = QtGui.QPixmap()
                    pix.loadFromData(image)
                    icon = QtGui.QIcon(pix)
                    self.setIcon(pix)
                    self.setIconSize(QtCore.QSize(100, 100))
                    img = pix.toImage()
                    self.bg_color = img.pixelColor(25, 25).name()
                    contrast = self.calc_contrast(self.bg_color)
                    if contrast >= 1 / 4.5:
                        self.text_color = self.COLOR_LIGHT
                    else:
                        self.text_color = self.COLOR_DARK

        def calc_contrast(self, hex_color):
            colors = (self.text_color, hex_color)
            lum = []
            for color in colors:
                lum.append(self.calc_luminance(color))
            lum = sorted(lum)
            contrast = (lum[0] + 0.05) / lum[1] + 0.05
            return contrast

        def calc_luminance(self, hex_color):
            assert len(hex_color) == 7
            hex_color = hex_color.replace("#", "")
            assert len(hex_color) == 6
            color = []
            for index in range(0, 3):
                start = 2 * index
                rgb = int.from_bytes(
                    bytes.fromhex(hex_color[start: start + 2]),
                    'little',
                )
                rgb /= 255
                rgb = rgb / 12.92 if rgb <= 0.04045 else ((rgb + 0.055) / 1.055) ** 2.4
                color.append(rgb)
            luminance = (
                (0.2126 * color[0]) + (0.7152 * color[1]) + (0.0722 * color[2])
            )
            return luminance

        def get_text(self):
            device_type = self.host['host-type']
            if self.host['host-type'] == "PS4":
                device_type = "PlayStation 4"
            elif self.host['host-type'] == "PS5":
                device_type = "PlayStation 5"
            app = self.host.get('running-app-name')
            if not app:
                app = "Idle" if self.host["status_code"] == 200 else "Standby" 
            self.main_text = (
                f"{self.host['host-name']}\n"
                f"{device_type}\n\n"
                f"{app}"
            )
            if not self.info_show:
                self.setText(self.main_text)

        def get_info(self):
            self.info = (
                f"Type: {self.host['host-type']}\n"
                f"Name: {self.host['host-name']}\n"
                f"IP Address: {self.host['host-ip']}\n"
                f"Mac Address: {self.host['host-id']}\n\n"
                f"Status: {self.host['status']}\n"
                f"Playing: {self.host.get('running-app-name')}"
            )

        def contextMenuEvent(self, event):
            text = "View Info" if not self.info_show else "Hide Info"
            self.action_info.setText(text)
            if self.host['status_code'] == 200:
                self.action_power.setText("Standby")
            else:
                self.action_power.setText("Wakeup")
            self.menu.popup(QtGui.QCursor.pos())

        def toggle_info(self):
            text = self.info if not self.info_show else self.main_text
            self.setText(text)
            self.info_show = not self.info_show

        def toggle_power(self):
            if self.host['status_code'] == 200:
                self.main_window.standby_host(self.host)
            else:
                self.main_window.wakeup_host(self.host)

    class DeviceSearch(QtCore.QObject):
        finished = QtCore.Signal()

        def __init__(self, hosts=[]):
            super().__init__()
            self.hosts = []
            self.manual_hosts = hosts

        def get_hosts(self):
            self.hosts = search()
            if self.manual_hosts:
                found = []
                if self.hosts:
                    for item in self.hosts:
                        found.append(item.get("host-ip"))
                for _host in self.manual_hosts:
                    if _host in found:
                        continue
                    host = get_status(_host)
                    if host:
                        self.hosts.append(host)
            self.finished.emit()

    def __init__(self, main_window):
        super().__init__(main_window)
        self.main_window = main_window
        self.layout = QtWidgets.QGridLayout(self)
        self.layout.setColumnMinimumWidth(0, 100)
        self.widgets = []
        self.setStyleSheet("QPushButton {padding: 50px 25px;}")
        self.timer = QtCore.QTimer()
        self.timer.timeout.connect(self.discover)

    def add(self, button, row, col):
        self.layout.setRowStretch(row, 6)
        self.layout.addWidget(button, row, col, QtCore.Qt.AlignCenter)
        self.widgets.append(button)

    def create_grid(self, hosts):
        if self.widgets:
            ip_addresses = {}
            if hosts:
                for item in hosts:
                    ip_address = item["host-ip"]
                    ip_addresses[ip_address] = item
                    hosts.remove(item)
            for widget in self.widgets:
                self.layout.removeWidget(widget)
                new_state = ip_addresses.get(widget.host["host-ip"])
                if not new_state:
                    widget.setParent(None)
                    widget.deleteLater()
                else:
                    widget.update_state(new_state)
                    hosts.append(widget)
            self.widgets = []
        max_cols = 3
        if hosts:
            for index, host in enumerate(hosts):
                col = index % max_cols
                row = index // max_cols
                if isinstance(host, dict):
                    button = DeviceGrid.DeviceButton(self.main_window, host)
                else:
                    button = host
                self.add(button, row, col)
            if not self.main_window.toolbar.options.isChecked() \
                    and not self.main_window.toolbar.controls.isChecked():
                self.show()
            self.main_window.center_text.hide()

    def discover(self):
        thread = QtCore.QThread(self)
        worker = DeviceGrid.DeviceSearch(
            self.main_window.options.options.get("devices")
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.get_hosts)
        worker.finished.connect(lambda: self.create_grid(worker.hosts))
        worker.finished.connect(thread.quit)
        thread.start()

    def start_timer(self):
        self.timer.start(5000)

    def session_stop(self):
        self.start_timer()
        QtCore.QTimer.singleShot(5000, self.enable_buttons)

    def enable_buttons(self):
        for button in self.widgets:
            button.setDisabled(False)


class MainWidget(QtWidgets.QWidget):
    def __init__(self, app):
        super().__init__()
        self._app = app
        self.idle = True
        self.thread = None
        self.ctrl_window = None
        self.toolbar = None
        self.device_grid = None
        self._init_window()

    def _init_window(self):
        self.setWindowTitle("PyRemotePlay")
        self.device_grid = DeviceGrid(self)
        self.toolbar = ToolbarWidget(self)
        self.options = OptionsWidget(self)
        self.controls = ControlsWidget(self)
        self.options.hide()
        self.controls.hide()
        self.center_text = QtWidgets.QLabel("", alignment=QtCore.Qt.AlignCenter)
        self.center_text.setWordWrap(True)
        self.center_text.setObjectName("center-text")
        self.layout = QtWidgets.QVBoxLayout(self)
        self.layout.addWidget(self.toolbar)
        self.layout.addWidget(self.options)
        self.layout.addWidget(self.center_text)
        self.layout.addWidget(self.device_grid)
        self.layout.addWidget(self.controls)
        self.layout.setAlignment(self.toolbar, QtCore.Qt.AlignTop)
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
            message(self, "Needs Registration", text, "info", cb=lambda:self.options.register(host, name), escape=True)
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
        host_type = host["host-type"]
        mac_address = host["host-id"]
        regist_key = profile["hosts"][mac_address]["data"][f"{host_type}-RegistKey"]
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
        self.ctrl_window = CTRLWindow(
            self,
            ip_address,
            name,
            profile,
            fps=fps,
            resolution=resolution,
            show_fps=show_fps,
            fullscreen=fullscreen,
            input_map=self.controls.map,
        )
        self.ctrl_window.show()
        self._app.setActiveWindow(self.ctrl_window)
        self.ctrl_window.start()

    def session_stop(self):
        print("Detected Session Stop")
        self.ctrl_window.close()
        self.ctrl_window.deleteLater()
        self.ctrl_window = None
        self._app.setActiveWindow(self)
        self.device_grid.session_stop()

    def set_center_text(self, text):
        self.center_text.setText(text)
        self.center_text.show()


class Popup(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()

    def set_text(self, text):
        self.center_text = QtWidgets.QLabel(text, alignment=QtCore.Qt.AlignCenter)


def spacer():
    return QtWidgets.QSpacerItem(20, 40)


def message(widget, title, text, level="critical", cb=None, escape=False, should_exec=True):
    def clicked(message, cb):
        button = message.clickedButton()
        text = button.text().lower()
        if "ok" in text:
            cb()
    icon = QtWidgets.QMessageBox.Critical
    if level == "critical":
        icon = QtWidgets.QMessageBox.Critical
    elif level == "info":
        icon = QtWidgets.QMessageBox.Information
    elif level == "warning":
        icon = QtWidgets.QMessageBox.Warning
    message = QtWidgets.QMessageBox(widget)
    message.setIcon(icon)
    message.setWindowTitle(title)
    message.setText(text)
    if escape:
        message.setStandardButtons(QtWidgets.QMessageBox.Ok | QtWidgets.QMessageBox.Cancel)
    else:
        message.setStandardButtons(QtWidgets.QMessageBox.Ok)
    if cb is not None:
        message.buttonClicked.connect(lambda: clicked(message, cb))
    if should_exec:
        message.exec()
    return message


def get_label(text: str, widget):
    label = QtWidgets.QLabel(widget)
    label.setText(text)
    return label


def gui():
    app = QtWidgets.QApplication([])
    widget = MainWidget(app)
    widget.resize(800, 600)
    widget.show()
    sys.exit(app.exec())
