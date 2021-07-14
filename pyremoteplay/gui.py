import asyncio
import sys
import threading
import time

from pyps4_2ndscreen.ddp import search

from .av import QueueReceiver, GUIReceiver
from .const import RESOLUTION_PRESETS
from .ctrl import CTRLAsync
from .oauth import LOGIN_URL, get_user_account
from .register import register
from .util import (add_profile, get_options, get_profiles, write_options,
                   write_profiles)

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
        self.loop = asyncio.new_event_loop()
        self.ctrl.loop = self.loop
        task = self.loop.create_task(self.start())
        print("CTRL Start")
        self.loop.run_until_complete(task)
        self.loop.run_forever()
        self.loop.close()

    async def start(self):
        status = await self.ctrl.start()
        if not status:
            print("CTRL Failed to Start")
            message(self.window, "Error", self.ctrl.error, cb=self.window.close)


class AVProcessor(QtCore.QObject):
    frame = QtCore.Signal()

    def __init__(self, window):
        super().__init__()
        self.window = window
        self.video_output = self.window.video_output
        self.v_queue = self.window.v_queue
        #self.codec = av.codec.Codec("h264", "r").create()
        #self.codec.flags = av.codec.context.Flags.LOW_DELAY
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

    MAP = {
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

    def __init__(self, main_window, host, name, profile, resolution='720p', fps=60, show_fps=False, fullscreen=False, input_map=None):
        super().__init__()
        self._main_window = main_window
        self.map = CTRLWindow.MAP if input_map is None else input_map
        self.host = host
        self.profile = profile
        self.fps = fps
        self.ctrl = CTRLAsync(self.host, self.profile, resolution=resolution, fps=fps, av_receiver=GUIReceiver)
        self.width = self.ctrl.resolution["width"]
        self.height = self.ctrl.resolution["height"]
        self.v_queue = self.ctrl.av_receiver.v_queue
        # self.ctrl.av_receiver.add_audio_cb(self.handle_audio)
        self.controller = self.ctrl.controller
        self.controller.enable_sticks()

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
        button = self.map.get(key)
        if button is None:
            print(f"Button Invalid: {key}")
            return
        if button == "QUIT":
            self.stop()
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
        button = self.map.get(key)
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
        event.accept()
        self.stop()

    def stop(self):
        self.ctrl.stop()
        print(f"Stopping Session @ {self.host}")
        self.thread.quit()
        self.av_thread.quit()
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
        self.setStyleSheet("QPushButton {padding: 5px}")
        self.layout = QtWidgets.QHBoxLayout(self)
        self.refresh = QtWidgets.QPushButton("Refresh")
        self.options = QtWidgets.QPushButton("Options")
        self.buttons = [self.refresh, self.options]
        self.refresh.setCheckable(True)
        self.options.setCheckable(True)
        self.options.clicked.connect(self.options_click)
        self.refresh.clicked.connect(self.refresh_click)

        for button in self.buttons:
            button.setMaximumWidth(200)
            self.layout.addWidget(button)
            self.layout.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignTop)

    def options_click(self):
        if self.options.isChecked():
            self.options.setStyleSheet("background-color:#0D6EFD;color:white;")
            self.main_window.device_grid.hide()
            self.refresh.hide()
            self.main_window.options.show()
            self.main_window.center_text.hide()
        else:
            self.options.setStyleSheet("")
            self.main_window.device_grid.show()
            self.refresh.show()
            self.main_window.options.hide()

    def refresh_click(self):
        if self.refresh.isChecked():
            self.refresh.setStyleSheet("background-color:#0D6EFD;color:white;")
            self.main_window.device_grid.discover()

    def refresh_reset(self):
        self.refresh.setChecked(False)
        self.refresh.setStyleSheet("")


class OptionsWidget(QtWidgets.QWidget):
    def __init__(self, main_window):
        super().__init__(main_window)
        self.main_window = main_window
        self.options = {}
        self.profiles = {}
        self.setStyleSheet("QPushButton {padding: 5px}")
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

    def init_options(self):
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
        set_account = QtWidgets.QPushButton("Select Account")
        add_account = QtWidgets.QPushButton("Add Account")
        del_account = QtWidgets.QPushButton("Delete Account")
        set_account.clicked.connect(self.change_profile)
        add_account.clicked.connect(self.new_profile)
        del_account.clicked.connect(self.delete_profile)

        self.add(self.fps, 0, 1, label=get_label("FPS:", self))
        self.add(self.fps_show, 0, 2)
        self.add(self.resolution, 1, 1, label=get_label("Resolution:", self))
        self.add(self.fullscreen, 1, 2)
        self.layout.addItem(spacer(), 2, 0)
        self.add(set_account, 3, 0)
        self.add(add_account, 3, 1)
        self.add(del_account, 3, 2)
        self.layout.addWidget(self.accounts, 4, 0, 3, 3)

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
        }
        write_options(options)
        return options

    def load_options(self):
        options = get_options()
        if not options:
            options = self.default_options()
        self.options = options

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

    def delete_profile(self):
        item = self.accounts.selectedItems()[0]
        if not item:
            return
        name = item.text(0)
        text = f"Are you sure you want to delete account: {name}"
        msg = message(self, "Delete Account" , text, "warning", escape=True, should_exec=False)
        if not msg.exec():
            return
        self.profiles.pop(name)
        write_profiles(self.profiles)
        self.options['profile'] = list(self.profiles.keys())[0] if self.profiles else ""
        write_options(self.options)
        self.set_options()
        self.set_profiles()

    def new_profile(self):
        title = "Add PSN Account"
        text = "To Add a New PSN Account you will have to sign in to your PSN Account. Continue?"
        self.options_message = message(self.main_window, title, text, "info", escape=True, should_exec=False)
        self.options_message.exec()
        self.options_message.hide()

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
        account = get_user_account(dialog.textValue())
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


class DeviceWidget(QtWidgets.QWidget):

    class DeviceSearch(QtCore.QObject):
        finished = QtCore.Signal()

        def __init__(self):
            super().__init__()
            self.hosts = []

        def get_hosts(self):
            self.hosts = search()
            self.finished.emit()

    def __init__(self, main_window):
        super().__init__(main_window)
        self.main_window = main_window
        self.layout = QtWidgets.QGridLayout(self)
        self.layout.setColumnMinimumWidth(0, 100)
        self.widgets = []
        self.setStyleSheet("QPushButton {padding: 50px 25px;}")

    def add(self, button, row, col):
        self.layout.setRowStretch(0, 6)
        self.layout.addWidget(button, row, col, QtCore.Qt.AlignCenter)
        self.widgets.append(button)

    def create_grid(self, hosts):
        if self.widgets:
            for widget in self.widgets:
                self.layout.removeWidget(widget)
                widget.setParent(None)
                widget.deleteLater()
            self.widgets = []
        max_cols = 3
        if hosts:
            for index, host in enumerate(hosts):
                col = index % max_cols
                row = index // max_cols
                button = QtWidgets.QPushButton(
                    f"Type: {host['host-type']}\n"
                    f"Name: {host['host-name']}\n"
                    f"IP Address: {host['host-ip']}\n"
                    f"Mac Address: {host['host-id']}\n\n"
                    f"Status: {host['status']}\n"
                    f"Playing: {host['running-app-name']}"
                )
                # button.setIcon(QtGui.QIcon.fromTheme("computer"))
                button.clicked.connect(lambda: self.main_window.connect(host))
                self.add(button, row, col)
            if not self.main_window.toolbar.options.isChecked():
                self.show()
            self.main_window.center_text.hide()

        else:
            self.main_window.set_center_text("No Devices Found.")

    def discover(self):
        self.hide()
        self.main_window.set_center_text("Refreshing...")
        thread = QtCore.QThread(self)
        worker = DeviceWidget.DeviceSearch()
        worker.moveToThread(thread)
        thread.started.connect(worker.get_hosts)
        worker.finished.connect(lambda: self.create_grid(worker.hosts))
        worker.finished.connect(thread.quit)
        worker.finished.connect(self.main_window.toolbar.refresh_reset)
        thread.start()


class MainWidget(QtWidgets.QWidget):
    def __init__(self, app):
        super().__init__()
        self._app = app
        self.idle = True
        self.hosts = []
        self.thread = None
        self.ctrl_window = None
        self.toolbar = None
        self.device_grid = None
        self._init_window()

    def _init_window(self):
        self.setWindowTitle("PyRemotePlay")
        self.device_grid = DeviceWidget(self)
        self.toolbar = ToolbarWidget(self)
        self.options = OptionsWidget(self)
        self.options.hide()
        self.center_text = QtWidgets.QLabel("", alignment=QtCore.Qt.AlignCenter)
        self.center_text.setObjectName("center-text")
        self.layout = QtWidgets.QVBoxLayout(self)
        self.layout.addWidget(self.toolbar)
        self.layout.addWidget(self.options)
        self.layout.addWidget(self.center_text)
        self.layout.addWidget(self.device_grid)
        self.layout.setAlignment(self.toolbar, QtCore.Qt.AlignTop)
        self.set_style()
        self.toolbar.refresh.setChecked(True)
        self.toolbar.refresh_click()

    def set_style(self):
        style = (
            "QPushButton {padding: 10px 0px;}"
            "QPushButton:hover {background-color:#0D6EFD;color:white;}"
            "#center-text {font-size: 24px;}"
        )
        self.setStyleSheet(style)

    def show_popup(self):
        self.popup = Popup()
        self.popup.setGeometry(QtCore.QRect(100, 100, 400, 200))
        self.popup.show()

    def connect(self, host):
        ip_address = host["host-ip"]
        options = self.options.options
        name = options.get("profile")
        profile = self.options.profiles.get(name)
        if not profile:
            message(self, "Error: No PSN Accounts found", "Click 'Options' -> 'Add Account' to add PSN Account.")
            return
        if host["host-id"] not in profile["hosts"]:
            text = f"PSN account: {name} has not been registered with this device. Click 'Ok' to register."
            message(self, "Needs Registration", text, "info", cb=lambda:self.options.register(host, name), escape=True)
            return

        resolution = options['resolution']
        fps = options['fps']
        show_fps = options['show_fps']
        fullscreen = options['fullscreen']
        self.ctrl_window = CTRLWindow(self, ip_address, name, profile, fps=fps, resolution=resolution, show_fps=show_fps, fullscreen=fullscreen)
        self.ctrl_window.show()
        self._app.setActiveWindow(self.ctrl_window)
        self.ctrl_window.start()

    def session_stop(self):
        print("Detected Session Stop")
        self.ctrl_window.deleteLater()
        self.ctrl_window = None
        self._app.setActiveWindow(self)

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
