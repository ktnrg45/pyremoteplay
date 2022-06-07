# pylint: disable=c-extension-no-member,invalid-name
"""Controls Widget."""
from __future__ import annotations
import logging
from enum import IntEnum, auto
from typing import Union
from PySide6 import QtWidgets, QtCore, QtGui
from PySide6.QtCore import Qt  # pylint: disable=no-name-in-module
from pyremoteplay.util import get_mapping, write_mapping
from pyremoteplay.gamepad import Gamepad, DEFAULT_DEADZONE
from pyremoteplay.gamepad.mapping import HatType, rp_map_keys
import pygame

from .util import message, format_qt_key
from .widgets import AnimatedToggle, LabeledWidget

_LOGGER = logging.getLogger(__name__)

INPUT_KEYBOARD = "keyboard"
INPUT_GAMEPAD = "gamepad"
DEFAULT = "default"


class RPKeys(IntEnum):
    """RP Keys Enum."""

    STANDBY = 0
    QUIT = auto()
    STICK_RIGHT_UP = auto()
    STICK_RIGHT_DOWN = auto()
    STICK_RIGHT_LEFT = auto()
    STICK_RIGHT_RIGHT = auto()
    STICK_LEFT_UP = auto()
    STICK_LEFT_DOWN = auto()
    STICK_LEFT_LEFT = auto()
    STICK_LEFT_RIGHT = auto()
    UP = auto()
    DOWN = auto()
    LEFT = auto()
    RIGHT = auto()
    L1 = auto()
    L2 = auto()
    L3 = auto()
    R1 = auto()
    R2 = auto()
    R3 = auto()
    CROSS = auto()
    CIRCLE = auto()
    SQUARE = auto()
    TRIANGLE = auto()
    OPTIONS = auto()
    SHARE = auto()
    PS = auto()
    TOUCHPAD = auto()


class AbstractControlsTable(QtWidgets.QTableWidget):
    """Abstract Controls Table."""

    keyChanged = QtCore.Signal(dict)

    def __init__(self, controls_widget: ControlsWidget, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._controls_widget = controls_widget

        self.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)

    def controlsWidget(self) -> ControlsWidget:
        """Return Controls Widget."""
        return self._controls_widget


class GamepadControlsTable(AbstractControlsTable):
    """Table for gamepad controls."""

    gamepad_removed = QtCore.Signal(int)

    IndexRole = Qt.UserRole + 1
    TypeRole = Qt.UserRole + 2
    HatRole = Qt.UserRole + 3
    AxisIdleRole = Qt.UserRole + 4

    FLAGS = Qt.ItemIsEnabled | Qt.ItemIsSelectable

    BG_NORMAL = QtGui.QBrush(Qt.white)
    BG_ACTIVE = QtGui.QBrush("#4DD4AC")

    class ControlType(IntEnum):
        """Control Types."""

        button = 0
        axis = auto()
        hat = auto()

    def __init__(self, controls_widget: ControlsWidget, *args, **kwargs):
        super().__init__(controls_widget, *args, **kwargs)
        self._gamepad = None
        self._timer_id = None
        self.setColumnCount(3)
        self.setHorizontalHeaderLabels(
            ["Gamepad Value", "Gamepad Control", "Remote Play Control"]
        )
        self.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.Stretch)
        self.horizontalHeader().setSectionResizeMode(
            0, QtWidgets.QHeaderView.ResizeToContents
        )

    def set_mapping(self, gamepad: Gamepad):
        """Set Mapping."""
        self.clearContents()
        self._gamepad = gamepad
        config = gamepad.get_config()
        _LOGGER.debug(config)
        items = []

        for _type in self.ControlType:
            key = _type.name
            value = config[key]
            for index in range(0, value):
                text = f"{key}-{index}"
                if key == "hat":
                    for hat_type in HatType:
                        item = self._get_control_item(index, _type)
                        item.setText(f"{text}-{hat_type.name}")
                        item.setData(self.HatRole, hat_type)
                        items.append(item)
                else:
                    item = self._get_control_item(index, _type)
                    item.setText(text)
                    items.append(item)

        self.setRowCount(max(len(items), len(rp_map_keys())))
        self._set_rows(items)
        self._update_values()

    def _get_control_item(
        self, index: int, control_type: ControlType
    ) -> QtWidgets.QTableWidgetItem:
        item = QtWidgets.QTableWidgetItem()
        item.setData(self.IndexRole, index)
        item.setData(self.TypeRole, control_type)
        return item

    def _set_rows(self, items: list[QtWidgets.QTableWidgetItem]):
        for row, item in enumerate(items):
            _type = item.data(self.TypeRole)
            index = item.data(self.IndexRole)
            text = ""
            if _type == self.ControlType.hat:
                hat = item.data(self.HatRole).name
                hats = self._gamepad.mapping[_type.name.lower()].get(index)
                if hats:
                    text = hats.get(hat)
            else:
                text = self._gamepad.mapping[_type.name.lower()].get(index)
            if not text:
                text = ""

            val_item = QtWidgets.QTableWidgetItem()
            rp_item = QtWidgets.QTableWidgetItem()
            rp_item.setText(text)
            val_item.setFlags(self.FLAGS)
            item.setFlags(self.FLAGS)
            rp_item.setFlags(self.FLAGS)
            self.setItem(row, 0, val_item)
            self.setItem(row, 1, item)
            self.setItem(row, 2, rp_item)

    def _update_values(self):
        found_activated = activated = False
        if self._gamepad:
            if not self._gamepad.available:
                self.gamepad_removed.emit(self._gamepad.instance_id)
                self._gamepad = None
                return
            config = self._gamepad.get_config()
            row = 0
            funcs = {
                self.ControlType.button: self._gamepad.get_button,
                self.ControlType.axis: self._gamepad.get_axis,
                self.ControlType.hat: self._gamepad.get_hat,
            }
            for _type, func in funcs.items():
                key = _type.name
                count = config[key]
                for num in range(0, count):
                    if _type == self.ControlType.hat:
                        val = func(num)
                        for hat in HatType:
                            color = self.BG_NORMAL
                            value = 0
                            if hat == val:
                                value = 1
                            if value == 1 and not activated:
                                color = self.BG_ACTIVE
                                activated = True

                            item = self.item(row, 0)
                            item.setText(str(value))
                            item.setData(Qt.UserRole, value)
                            self._set_bg_color(row, color)
                            if activated and not found_activated:
                                self._key_activated(item)
                                found_activated = True
                            row += 1

                    else:
                        color = self.BG_NORMAL
                        val = func(num)
                        item = self.item(row, 0)

                        if isinstance(val, float):
                            text = f"{val:.4f}"
                        else:
                            text = str(val)

                        if _type == self.ControlType.button:
                            if val == 1 and not activated:
                                color = self.BG_ACTIVE
                                activated = True
                        else:
                            idle = item.data(self.AxisIdleRole)
                            if idle is None:
                                item.setData(self.AxisIdleRole, val)
                            else:
                                if abs(idle - val) > 0.8 and not activated:
                                    color = self.BG_ACTIVE
                                    activated = True

                        item.setText(text)
                        item.setData(Qt.UserRole, val)
                        self._set_bg_color(row, color)
                        if activated and not found_activated:
                            self._key_activated(item)
                            found_activated = True
                        row += 1

    def _key_activated(self, item: QtWidgets.QTableWidgetItem):
        """Set item key with Selected RP Key."""
        selected = None
        self.scrollToItem(item)
        items = self.selectedItems()
        mapping = self._gamepad.mapping
        if items:
            selected = self.item(items[0].row(), 2)
            item = self.item(item.row(), 2)
            old_rp_key = item.text()
            new_rp_key = selected.text()
            item.setText(new_rp_key)
            selected.setText(old_rp_key)
            for _item in (selected, item):
                _type = self.item(_item.row(), 1).data(self.TypeRole)
                index = self.item(_item.row(), 1).data(self.IndexRole)
                if _type == self.ControlType.hat:
                    hat = self.item(_item.row(), 1).data(self.HatRole).name
                    mapping[_type.name.lower()][index][hat] = _item.text()
                else:
                    mapping[_type.name.lower()][index] = _item.text()
            self._gamepad.mapping = mapping
            self.keyChanged.emit(self._gamepad.mapping)
            self.clearSelection()

    def _set_bg_color(self, row: int, brush: QtGui.QBrush):
        for col in range(0, self.columnCount()):
            self.item(row, col).setBackground(brush)

    def hideEvent(self, event):
        """Hide Event."""
        super().hideEvent(event)
        self._stop_timer()

    def showEvent(self, event):
        """Show Event."""
        super().showEvent(event)
        self.handle_gamepad()

    def timerEvent(self, event):
        """Timer Event."""
        super().timerEvent(event)
        if event.timerId() == self._timer_id:
            self._update_values()

    def handle_gamepad(self):
        """Start Handling gamepad events."""
        if self._timer_id is None:
            self._timer_id = self.startTimer(125)

    def _stop_timer(self):
        if self._timer_id is not None:
            self.killTimer(self._timer_id)
        self._timer_id = None


class KeyboardControlsTable(AbstractControlsTable):
    """Table for keyboard controls."""

    DEFAULT_KEYS = (
        "Key_Escape",
        "Key_Q",
        "Key_Up",
        "Key_Down",
        "Key_Left",
        "Key_Right",
        "Key_W",
        "Key_S",
        "Key_A",
        "Key_D",
        "Key_1",
        "Key_2",
        "Key_3",
        "Key_4",
        "Key_5",
        "Key_6",
        "Key_Return",
        "Key_C",
        "Key_R",
        "Key_T",
        "Key_Backspace",
        "Key_Equal",
        "Key_P",
        "Key_0",
    )

    BG_WARNING = QtGui.QBrush("#EA868F")

    @staticmethod
    def get_default_mapping():
        """Return Default mapping."""
        skip = [RPKeys.UP, RPKeys.DOWN, RPKeys.LEFT, RPKeys.RIGHT]
        rp_keys = [key for key in RPKeys if key not in skip]
        return {
            key: rp_key.name
            for key, rp_key in zip(KeyboardControlsTable.DEFAULT_KEYS, rp_keys)
        }

    def __init__(self, controls_widget: ControlsWidget, *args, **kwargs):
        super().__init__(controls_widget, *args, **kwargs)
        self.setColumnCount(2)
        self.setHorizontalHeaderLabels(["Control", "Remote Play Control"])
        header = self.horizontalHeader()
        header.setSectionResizeMode(QtWidgets.QHeaderView.Stretch)
        self.setRowCount(len(list(RPKeys)))

    def mousePressEvent(self, event):
        """Mouse Press Event."""
        if self.selectedItems():
            self._set_control(event.button())
            event.accept()
        else:
            super().mousePressEvent(event)

    def keyPressEvent(self, event):
        """Key Press Event."""
        event.accept()
        self._set_control(Qt.Key(event.key()))

    def set_mapping(self):
        """Set mapping in table."""
        self.clearContents()
        mapping = self._get_saved_map()

        for rp_key in RPKeys:
            flags = Qt.ItemIsEnabled | Qt.ItemIsSelectable
            blank = QtWidgets.QTableWidgetItem()
            blank.setData(Qt.UserRole, "")
            item = QtWidgets.QTableWidgetItem(rp_key.name)
            blank.setFlags(flags)
            item.setFlags(flags)
            self.setItem(rp_key, 0, blank)
            self.setItem(rp_key, 1, item)

        for key, rp_key in mapping.items():
            item = self.item(RPKeys[rp_key], 0)
            self._set_key_item(item, key)
        assert self._get_saved_map() == self._get_map()

    def clear_control(self):
        """Clear Control."""
        items = self.selectedItems()
        if not items:
            return
        self._set_key_item(items[0], "")
        self.clearSelection()
        self.keyChanged.emit(self._get_map())

    def quit_selected(self) -> bool:
        """Return True if quit selected."""
        items = self.selectedItems()
        if len(items) < 2:
            return False
        quit_row = RPKeys.QUIT
        if items[1].row() == quit_row:
            return True
        return False

    def _get_saved_map(self) -> dict:
        """Return saved map."""
        return self.controlsWidget().get_keyboard_map()

    def _get_map(self) -> dict:
        """Return map from table."""
        mapping = {}
        for row in range(0, self.rowCount()):
            key = self.item(row, 0).data(Qt.UserRole)
            rp_key = self.item(row, 1).text()
            if key:
                mapping[key] = rp_key
        return mapping

    def _set_control(self, key: Union[Qt.Key, Qt.MouseButton]):
        """Set RP Control to Qt Key."""
        for _rp_key in RPKeys:
            self._set_item_warning(_rp_key, False)

        key = key.name.decode()
        items = self.selectedItems()
        if not items:
            return
        if len(items) < 2:
            _LOGGER.warning("Incomplete row selected")
            return

        current_key = items[0].data(Qt.UserRole)
        rp_key = RPKeys(items[1].row())

        # Swap keys if set
        current_rp_key = self._get_current_rp_key(key)
        if current_rp_key:
            if rp_key == current_rp_key:
                self.clearSelection()
                return
            # Don't allow QUIT to be None
            if current_rp_key == RPKeys.QUIT and current_key == "":
                self._set_item_warning(RPKeys.QUIT, True)
                self._set_item_warning(rp_key, True)
                self.clearSelection()
                return
            self._set_key_item(self.item(current_rp_key, 0), current_key)
        self._set_key_item(items[0], key)

        self.clearSelection()
        self.keyChanged.emit(self._get_map())

    def _set_item_warning(self, row: RPKeys, warning: bool):
        items = [self.item(row, 0), self.item(row, 1)]
        brush = KeyboardControlsTable.BG_WARNING if warning else QtGui.QBrush()
        tooltip = "'QUIT' Remote Play Control must be set" if warning else ""
        for item in items:
            item.setBackground(brush)
            item.setToolTip(tooltip)

    def _set_key_item(self, item: QtWidgets.QTableWidgetItem, key: str):
        if key is None:
            key = ""
        item.setText(format_qt_key(key))
        item.setData(Qt.UserRole, key)

    def _get_current_rp_key(self, key: str) -> RPKeys:
        """Return RP key from Qt key."""
        _map = self._get_map()
        rp_key = _map.get(key)
        if rp_key is not None:
            rp_key = RPKeys[rp_key]
        return rp_key


class ControlsWidget(QtWidgets.QWidget):
    """Widget for controls options."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._mapping = {}
        self._gamepad_mapping = {}
        self._selected_keyboard_map = DEFAULT
        self._selected_gamepad_guid = None
        self._gamepad_timer = None

        self._keyboard_table = KeyboardControlsTable(self)
        self._gamepad_table = GamepadControlsTable(self)

        self._stacked_widget = QtWidgets.QStackedWidget(self)
        self._stacked_widget.addWidget(self._keyboard_table)
        self._stacked_widget.addWidget(self._gamepad_table)

        self._input_selector = QtWidgets.QComboBox(self)
        self._input_selector.addItems([INPUT_KEYBOARD, INPUT_GAMEPAD])
        self._gamepad_selector = QtWidgets.QComboBox(self)
        self._deadzone = QtWidgets.QDoubleSpinBox()
        self._deadzone.setRange(0.0, 0.99)
        self._deadzone.setSingleStep(0.01)
        self._deadzone.setDecimals(2)
        self._deadzone.setValue(DEFAULT_DEADZONE)
        input_select_widget = LabeledWidget("Input", self._input_selector)
        gamepad_select_widget = LabeledWidget("Gamepad", self._gamepad_selector)
        deadzone_widget = LabeledWidget("Deadzone", self._deadzone, self)

        self._gamepad_selector.parent().hide()
        self._deadzone.parent().hide()

        self._instructions = QtWidgets.QLabel(self)
        self._instructions.setWordWrap(True)
        self._instructions.setTextFormat(Qt.RichText)

        self._left_joystick = AnimatedToggle("Show Left Joystick", self)
        self._right_joystick = AnimatedToggle("Show Right Joystick", self)
        self._use_gamepad = AnimatedToggle("Use Gamepad", self)
        self._add = QtWidgets.QPushButton("Add Map")
        self._remove = QtWidgets.QPushButton("Remove Map")
        self._reset = QtWidgets.QPushButton("Reset to Default")
        self._clear = QtWidgets.QPushButton("Clear")
        self._cancel = QtWidgets.QPushButton("Cancel")
        self._save_gamepad = QtWidgets.QPushButton("Save Map")
        self._add.hide()
        self._remove.hide()
        self._cancel.hide()
        self._clear.hide()
        self._save_gamepad.hide()

        self._init_controls()
        self._set_instructions()

        self.setLayout(QtWidgets.QGridLayout(alignment=Qt.AlignTop))
        self.layout().setColumnMinimumWidth(0, 30)
        self.layout().setRowStretch(5, 1)
        self.layout().setColumnStretch(0, 1)
        self.layout().setColumnStretch(1, 1)
        self.layout().setColumnStretch(2, 1)

        self.layout().addWidget(self._left_joystick, 0, 0, 1, 1)
        self.layout().addWidget(self._right_joystick, 0, 1, 1, 1)
        self.layout().addWidget(self._use_gamepad, 0, 2, 1, 1)
        self.layout().addWidget(input_select_widget, 2, 0, 1, 1)
        self.layout().addWidget(gamepad_select_widget, 3, 0, 1, 1)
        self.layout().addWidget(deadzone_widget, 3, 1, 1, 1)
        self.layout().addWidget(self._save_gamepad, 3, 2, 1, 1)
        # self.layout().addWidget(self._add, 3, 2, 1, 1)
        self.layout().addWidget(self._clear, 4, 0, 1, 1)
        self.layout().addWidget(self._cancel, 4, 1, 1, 1)
        self.layout().addWidget(self._reset, 4, 2, 1, 1)
        self.layout().addWidget(self._stacked_widget, 5, 0, 2, 3)
        self.layout().addWidget(self._instructions, 5, 3, 2, 1)

        self._left_joystick.clicked.connect(self._click_joystick)
        self._right_joystick.clicked.connect(self._click_joystick)
        self._cancel.clicked.connect(self._click_cancel)
        self._reset.clicked.connect(self._click_reset)
        self._clear.clicked.connect(self._click_clear)
        self._use_gamepad.clicked.connect(self._click_gamepad)
        self._save_gamepad.clicked.connect(self._click_gamepad_save)
        self._keyboard_table.keyChanged.connect(self._set_keyboard_map)
        self._keyboard_table.itemSelectionChanged.connect(self._item_selection_changed)
        self._gamepad_table.keyChanged.connect(self._set_gamepad_map)
        self._gamepad_table.itemSelectionChanged.connect(self._item_selection_changed)
        self._gamepad_table.gamepad_removed.connect(self._remove_gamepad)
        self._input_selector.currentTextChanged.connect(self._map_type_changed)
        self._gamepad_selector.currentTextChanged.connect(self._gamepad_changed)
        self._deadzone.valueChanged.connect(self._deadzone_changed)

    def showEvent(self, event):
        """Show Event,"""
        super().showEvent(event)
        if self._gamepad_table.isVisible():
            self._gamepad_table.handle_gamepad()

    def hide(self):
        """Hide widget."""
        self._click_cancel()
        if self._gamepad_timer:
            self.killTimer(self._gamepad_timer)
        self._gamepad_timer = None
        super().hide()

    def get_keyboard_map_names(self) -> list[str]:
        """Return keyboard map names."""
        maps = self._mapping[INPUT_KEYBOARD]["maps"]
        return list(maps.keys())

    def get_keyboard_map(self) -> dict:
        """Return Keyboard Controller Map."""
        mapping = self._mapping[INPUT_KEYBOARD]["maps"].get(self._selected_keyboard_map)
        if not mapping:
            return {}
        return mapping["map"]

    def get_keyboard_options(self):
        """Return Keyboard options."""
        mapping = self._mapping[INPUT_KEYBOARD]["maps"].get(self._selected_keyboard_map)
        if not mapping:
            return {}
        return mapping["options"]

    def get_gamepad_map(self) -> dict:
        """Return Gamepad map."""
        if not self._selected_gamepad_guid:
            return {}
        mapping = self._mapping[INPUT_GAMEPAD]["maps"].get(self._selected_gamepad_guid)
        if not mapping:
            return {}
        return mapping["map"]

    def get_gamepad_options(self):
        """Return Gamepad options."""
        if not self._selected_gamepad_guid:
            return {}
        mapping = self._mapping[INPUT_GAMEPAD]["maps"].get(self._selected_gamepad_guid)
        if not mapping:
            return {}
        return mapping["options"]

    def use_gamepad(self) -> bool:
        """Return True if Use Gamepad is set."""
        return self._use_gamepad.isChecked()

    def get_gamepad(self) -> Gamepad:
        """Get Selected Gamepad."""
        if self._gamepad_selector.currentText():
            index = self._gamepad_selector.currentIndex()
            gamepad = self._gamepad_selector.itemData(index, Qt.UserRole)
            if isinstance(gamepad, Gamepad):
                return gamepad
        return None

    def _default_mapping(self):
        """Set Default mapping."""
        self._selected_keyboard_map = DEFAULT
        self._mapping = {
            INPUT_KEYBOARD: {
                "selected": self._selected_keyboard_map,
                "maps": {
                    self._selected_keyboard_map: {
                        "map": KeyboardControlsTable.get_default_mapping(),
                        "options": {"joysticks": {"left": True, "right": True}},
                    },
                },
            },
            INPUT_GAMEPAD: {
                "use_gamepad": False,
                "maps": {},
            },
        }
        self._write_mapping()
        self._set_toggles()
        self._set_keyboard_table()

    def _default_gamepad_map(self) -> dict:
        mapping = {
            "map": {},
            "options": {"deadzone": DEFAULT_DEADZONE},
        }
        gamepad = self.get_gamepad()
        if gamepad:
            mapping["map"] = gamepad.default_map()
        return mapping

    def _init_controls(self):
        self._mapping = get_mapping()
        if not self._mapping:
            self._default_mapping()
        try:
            self._selected_keyboard_map = self._mapping[INPUT_KEYBOARD].get("selected")
            if not self._selected_keyboard_map:
                self._selected_keyboard_map = DEFAULT
            self.get_keyboard_map()
            self.get_keyboard_options()
            self.get_gamepad_map()
            self.get_gamepad_options()
            self._set_toggles()
            self._set_keyboard_table()
        except (KeyError, TypeError):
            self._default_mapping()

        Gamepad.start()
        for gamepad in Gamepad.get_all():
            self._add_gamepad(gamepad)
        self._set_selected_gamepad()
        Gamepad.register(self._gamepad_event)

    def _add_gamepad(self, gamepad: Gamepad):
        text = gamepad.name
        text = f"{text} ({gamepad.guid[-5:]})"
        self._gamepad_selector.addItem(text, gamepad)

    def _set_keyboard_table(self):
        self._click_cancel()
        self._keyboard_table.set_mapping()

    def _set_toggles(self):
        options = self.get_keyboard_options()
        joysticks = options["joysticks"]

        self._left_joystick.setChecked(joysticks["left"])
        self._right_joystick.setChecked(joysticks["right"])

        self._use_gamepad.setChecked(self._mapping[INPUT_GAMEPAD]["use_gamepad"])

    def _set_keyboard_options(self, options: dict):
        self._mapping[INPUT_KEYBOARD]["maps"][self._selected_keyboard_map][
            "options"
        ] = options
        self._write_mapping()

    def _set_keyboard_map(self, mapping: dict):
        self._mapping[INPUT_KEYBOARD]["maps"][self._selected_keyboard_map][
            "map"
        ] = mapping
        self._write_mapping()

    def _set_gamepad_options(self, options: dict):
        if not self._selected_gamepad_guid:
            return
        if not self.get_gamepad_map():
            self._mapping[INPUT_GAMEPAD]["maps"][
                self._selected_gamepad_guid
            ] = self._default_gamepad_map()
        self._mapping[INPUT_GAMEPAD]["maps"][self._selected_gamepad_guid][
            "options"
        ] = options
        self._write_mapping()

    def _set_gamepad_map(self, mapping: dict):
        if not self._selected_gamepad_guid:
            return
        if not self.get_gamepad_map():
            self._mapping[INPUT_GAMEPAD]["maps"][
                self._selected_gamepad_guid
            ] = self._default_gamepad_map()
        self._mapping[INPUT_GAMEPAD]["maps"][self._selected_gamepad_guid][
            "map"
        ] = mapping
        self._write_mapping()

    def _set_selected_gamepad(self):
        gamepad = self.get_gamepad()
        guid = None
        if gamepad:
            guid = gamepad.guid
        self._selected_gamepad_guid = guid

    def _write_mapping(self):
        """Save Entire Map."""
        write_mapping(self._mapping)

    def _item_selection_changed(self):
        table = self._stacked_widget.currentWidget()
        if table.selectedItems():
            self._cancel.show()
            if table == self._keyboard_table:
                if not self._keyboard_table.quit_selected():
                    self._clear.show()
        else:
            self._cancel.hide()
            self._clear.hide()

    def _set_instructions(self):
        if self._input_selector.currentText() == INPUT_KEYBOARD:
            text = (
                "To set a Control, click on the corresponding row "
                "and then press the key that you would like to map "
                "to the Remote Play Control."
            )
        else:
            text = (
                "To change a Remote Play Control, click on the corresponding row "
                "and then press the button or move the sticks on the gamepad. "
                "The activated button or stick will be mapped to "
                "the Remote Play Control that was selected.<br><br>"
                "<b>Note:</b> Analog Triggers are shown as an axis with an idle value of '-1'."
            )
        self._instructions.setText(text)

    def _map_type_changed(self, *args):
        self._click_cancel()
        if self._input_selector.currentText() == INPUT_KEYBOARD:
            self._gamepad_selector.parent().hide()
            self._deadzone.parent().hide()
            self._save_gamepad.hide()
            self._stacked_widget.setCurrentWidget(self._keyboard_table)
        else:
            self._stacked_widget.setCurrentWidget(self._gamepad_table)
            self._gamepad_selector.parent().show()
            self._gamepad_changed()
        self._set_instructions()

    def _gamepad_changed(self, *args):
        _LOGGER.debug("Current Gamepad: %s", self._gamepad_selector.currentText())
        if self._input_selector.currentText() == INPUT_GAMEPAD:
            if self._gamepad_selector.currentText():
                self._deadzone.parent().show()
                self._save_gamepad.show()
                self._set_selected_gamepad()
                gamepad = self.get_gamepad()
                if self._selected_gamepad_guid and gamepad:
                    mapping = self.get_gamepad_map()
                    if mapping:
                        gamepad.mapping = mapping
                        options = self.get_gamepad_options()
                        self._deadzone.setValue(options["deadzone"])
                    self._gamepad_table.set_mapping(gamepad)
            else:
                self._gamepad_table.clearContents()
                self._set_selected_gamepad()

    def _deadzone_changed(self, value: int):
        options = self.get_gamepad_options()
        options["deadzone"] = value
        gamepad = self.get_gamepad()
        if gamepad:
            gamepad.deadzone = value
        self._set_gamepad_options(options)

    def _check_gamepads(self):
        count = self._gamepad_selector.count()
        existing = [
            self._gamepad_selector.itemData(index, Qt.UserRole)
            for index in range(0, count)
        ]
        for gamepad in Gamepad.get_all():
            if gamepad is not None and gamepad not in existing:
                self._add_gamepad(gamepad)

    def _remove_gamepad(self, instance_id: int):
        for index in range(0, self._gamepad_selector.count()):
            gamepad = self._gamepad_selector.itemData(index, Qt.UserRole)
            if gamepad and gamepad.instance_id == instance_id:
                self._gamepad_selector.removeItem(index)
                self._gamepad_selector.update()
                self._gamepad_changed()

    def _gamepad_event(self, event: pygame.event.Event):
        if event.type not in (pygame.JOYDEVICEADDED, pygame.JOYDEVICEREMOVED):
            return

        if event.type == pygame.JOYDEVICEREMOVED:
            self._remove_gamepad(event.instance_id)
        else:
            gamepads = Gamepad.get_all()
            gamepad = None
            for _gamepad in gamepads:
                if _gamepad.guid == event.guid:
                    gamepad = _gamepad
                    break
            count = self._gamepad_selector.count()
            existing = [
                self._gamepad_selector.itemData(index, Qt.UserRole)
                for index in range(0, count)
            ]
            if gamepad is not None and gamepad not in existing:
                self._add_gamepad(gamepad)

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
        options = self.get_keyboard_options()
        value = not options["joysticks"][stick]
        options["joysticks"][stick] = value
        self._set_keyboard_options(options)

    def _click_cancel(self):
        self._keyboard_table.clearSelection()
        self._gamepad_table.clearSelection()
        self._cancel.hide()
        self._clear.hide()

    def _click_clear(self):
        self._keyboard_table.clear_control()

    def _click_reset(self):
        if self._input_selector.currentText() == INPUT_GAMEPAD:
            if not self.get_gamepad():
                return
        text = "Reset input mapping to default?"
        message(self, "Reset Mapping", text, "warning", self._reset_map, escape=True)

    def _click_gamepad(self):
        self._mapping[INPUT_GAMEPAD]["use_gamepad"] = self._use_gamepad.isChecked()
        self._write_mapping()

    def _click_gamepad_save(self):
        filename, _ = QtWidgets.QFileDialog.getSaveFileName(
            self.window(), "Save Gamepad Mapping", filter="YAML (*.yaml *.yml)"
        )
        if filename:
            gamepad = self.get_gamepad()
            if not gamepad:
                QtWidgets.QMessageBox.warning(
                    self.window(), "Error", "No Gamepad Available"
                )
                return
            gamepad.save_map(filename)

    def _reset_map(self):
        """Reset Current map."""
        if self._input_selector.currentText() == INPUT_KEYBOARD:
            self._set_keyboard_map(KeyboardControlsTable.get_default_mapping())
            self._set_keyboard_table()
        elif self._input_selector.currentText() == INPUT_GAMEPAD:
            gamepad = self.get_gamepad()
            gamepad.mapping = gamepad.default_map()
            self._set_gamepad_map(gamepad.mapping)
            self._deadzone.setValue(DEFAULT_DEADZONE)
            self._gamepad_table.set_mapping(gamepad)
