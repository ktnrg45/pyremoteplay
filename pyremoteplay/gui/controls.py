# pylint: disable=c-extension-no-member,invalid-name
"""Controls Widget."""

from PySide6 import QtWidgets, QtCore
from PySide6.QtCore import Qt  # pylint: disable=no-name-in-module
from pyremoteplay.util import get_mapping, write_mapping

from .util import message
from .widgets import AnimatedToggle


class ControlsTable(QtWidgets.QTableWidget):
    """Table for controls."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setRowCount(len(ControlsWidget.KEYS))
        self.setColumnCount(2)
        self.setHorizontalHeaderLabels(["Control", "Remote Play Control"])
        header = self.horizontalHeader()
        header.setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        header.setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeToContents)

    def mousePressEvent(self, event):
        """Mouse Press Event."""
        super().mousePressEvent(event)
        button = event.button().name.decode()
        self.parent().set_control(button)

    def keyPressEvent(self, event):
        """Key Press Event."""
        self.parent().keyPressEvent(event)


class ControlsWidget(QtWidgets.QWidget):
    """Widget for controls options."""

    KEYS = (
        "STANDBY",
        "QUIT",
        "STICK_RIGHT_UP",
        "STICK_RIGHT_DOWN",
        "STICK_RIGHT_LEFT",
        "STICK_RIGHT_RIGHT",
        "STICK_LEFT_UP",
        "STICK_LEFT_DOWN",
        "STICK_LEFT_LEFT",
        "STICK_LEFT_RIGHT",
        "UP",
        "DOWN",
        "LEFT",
        "RIGHT",
        "L1",
        "L2",
        "L3",
        "R1",
        "R2",
        "R3",
        "CROSS",
        "CIRCLE",
        "SQUARE",
        "TRIANGLE",
        "OPTIONS",
        "SHARE",
        "PS",
        "TOUCHPAD",
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

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._mapping = {}
        self._options = {}
        self._selected_map = ""
        self._input = None

        self._table = ControlsTable(self)

        self._left_joystick = AnimatedToggle("Show Left Joystick", self)
        self._right_joystick = AnimatedToggle("Show Right Joystick", self)
        self._reset = QtWidgets.QPushButton("Reset to Default")
        self._clear = QtWidgets.QPushButton("Clear")
        self._cancel = QtWidgets.QPushButton("Cancel")
        self._cancel.hide()
        self._clear.hide()
        self._init_controls()

        self.setLayout(QtWidgets.QGridLayout(alignment=Qt.AlignTop))
        self.layout().setColumnMinimumWidth(0, 30)
        self.layout().setRowStretch(4, 1)
        self.layout().addWidget(self._left_joystick, 0, 0, 1, 2)
        self.layout().addWidget(self._right_joystick, 1, 0, 1, 2)
        self.layout().addWidget(self._reset, 2, 0)
        self.layout().addWidget(self._clear, 2, 1)
        self.layout().addWidget(self._cancel, 2, 2)
        self.layout().addWidget(self._table, 3, 0, 2, 3)
        self.layout().addWidget(self._instructions(), 3, 3, 2, 1)

        self._left_joystick.clicked.connect(self._click_joystick)
        self._right_joystick.clicked.connect(self._click_joystick)
        self._cancel.clicked.connect(self._click_cancel)
        self._reset.clicked.connect(self._click_reset)
        self._clear.clicked.connect(self._click_clear)
        self._table.clicked.connect(self._click_table)

    def keyPressEvent(self, event):
        """Key Press Event."""
        if self._input is not None:
            key = Qt.Key(event.key()).name.decode()
            self.set_control(key)

    def hide(self):
        """Hide widget."""
        self._click_cancel()
        super().hide()

    def set_control(self, key):
        """Set RP Control to Qt Key."""
        if self._input is not None:
            item = self._table.item(self._input, 0)
            rp_key = self._table.item(self._input, 1).text()
            current = self._get_current_map_key(rp_key)
            _map = self.get_map()

            # Delete the current key
            if current is not None:
                assert _map.get(current) == rp_key
                _map.pop(current)

            _map[key] = rp_key
            item.setText(key.replace("Key_", ""))
            self._set_map(_map)
            self._set_table()

    def get_map(self):
        """Return Controller Map."""
        return self._mapping["maps"][self._selected_map]["map"]

    def get_options(self):
        """Return options."""
        return self._mapping["maps"][self._selected_map]["options"]

    def _default_mapping(self):
        """Return Default map."""
        if not self._options:
            options = {"joysticks": {"left": False, "right": False}}
        self._mapping = {}
        self._mapping.update(
            {
                "selected": "keyboard",
                "maps": {
                    "keyboard": {
                        "map": ControlsWidget.DEFAULT_MAPPING.copy(),
                        "options": options,
                    }
                },
            }
        )
        write_mapping(self._mapping)
        self._set_map(self.get_map())
        self._set_table()

    def _init_controls(self):
        self._mapping = get_mapping()
        if not self._mapping:
            self._default_mapping()
        self._selected_map = self._mapping["selected"]
        self._set_map(self.get_map())
        self._set_options(self.get_options())
        self._set_table()
        self._set_joysticks()

    def _set_table(self):
        self._table.clearContents()
        self._click_cancel()
        if self._selected_map == "keyboard":
            self._set_keyboard()

    def _set_joysticks(self):
        options = self.get_options()
        joysticks = options["joysticks"]
        if joysticks["left"]:
            self._left_joystick.setChecked(True)
        if joysticks["right"]:
            self._right_joystick.setChecked(True)

    def _set_options(self, options):
        self._mapping["maps"][self._selected_map]["options"] = options
        write_mapping(self._mapping)

    def _set_map(self, _map):
        self._input = None
        self._mapping["maps"][self._selected_map]["map"] = _map
        write_mapping(self._mapping)

    def _set_keyboard(self):
        remove_keys = []
        _map = self.get_map()

        for index, rp_key in enumerate(ControlsWidget.KEYS):
            item = QtWidgets.QTableWidgetItem(rp_key)
            item.setFlags(Qt.ItemIsEnabled)
            blank = QtWidgets.QTableWidgetItem()
            blank.setFlags(Qt.ItemIsEnabled)
            self._table.setItem(index, 1, item)
            self._table.setItem(index, 0, blank)
        for key, rp_key in _map.items():
            if rp_key not in ControlsWidget.KEYS:
                remove_keys.append(key)
                continue
            item = QtWidgets.QTableWidgetItem(
                key.replace("Key_", "").replace("Button", " Click")
            )
            item.setFlags(Qt.ItemIsEnabled)
            self._table.setItem(ControlsWidget.KEYS.index(rp_key), 0, item)
        if remove_keys:
            for key in remove_keys:
                _map.pop(key)
        self._set_map(_map)

    def _instructions(self) -> QtWidgets.QLabel:
        text = (
            "To set a Control, click on the corresponding row "
            "and then press the key that you would like to map "
            "to the Remote Play Control."
        )
        label = QtWidgets.QLabel(text)
        label.setWordWrap(True)
        return label

    @QtCore.Slot()
    def _click_joystick(self):
        button = self.sender()
        stick = ""
        if button == self._left_joystick:
            stick = "left"
        elif button == self._right_joystick:
            stick = "right"

        if not stick or stick not in ("left", "right"):
            raise ValueError("Invalid stick")
        options = self.get_options()
        value = not options["joysticks"][stick]
        options["joysticks"][stick] = value
        self._set_options(options)

    def _click_table(self, item):
        self._input = item.row()
        self._cancel.show()
        self._clear.show()

    def _click_cancel(self):
        self._input = None
        self._cancel.hide()
        self._clear.hide()

    def _click_clear(self):
        if self._input is None:
            return
        item = self._table.item(self._input, 0).text()
        if "Click" in item:
            key = item.replace(" Click", "Button")
        else:
            key = f"Key_{item}"
        _map = self.get_map()
        if key in _map:
            _map.pop(key)
        self._set_map(_map)
        self._set_table()

    def _click_reset(self):
        text = "Reset input mapping to default?"
        message(
            self, "Reset Mapping", text, "warning", self._default_mapping, escape=True
        )

    def _get_current_map_key(self, rp_key):
        _map = self.get_map()
        rp_keys = list(_map.values())
        if rp_key not in rp_keys:
            return None
        index = rp_keys.index(rp_key)
        key = list(_map.keys())[index]
        return key
