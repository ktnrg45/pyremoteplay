# pylint: disable=c-extension-no-member,invalid-name
"""Controls Widget."""
from __future__ import annotations
import logging
from enum import IntEnum, auto
from typing import Union
from PySide6 import QtWidgets, QtCore, QtGui
from PySide6.QtCore import Qt  # pylint: disable=no-name-in-module
from pyremoteplay.util import get_mapping, write_mapping

from .util import message, format_qt_key
from .widgets import AnimatedToggle

_LOGGER = logging.getLogger(__name__)


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


class ControlsTable(QtWidgets.QTableWidget):
    """Table for controls."""

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

    keyChanged = QtCore.Signal(dict)

    @staticmethod
    def get_default_mapping():
        """Return Default mapping."""
        skip = [RPKeys.UP, RPKeys.DOWN, RPKeys.LEFT, RPKeys.RIGHT]
        rp_keys = [key for key in RPKeys if key not in skip]
        return {
            key: rp_key.name for key, rp_key in zip(ControlsTable.DEFAULT_KEYS, rp_keys)
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setRowCount(len(list(RPKeys)))
        self.setColumnCount(2)
        self.setHorizontalHeaderLabels(["Control", "Remote Play Control"])
        header = self.horizontalHeader()
        header.setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        header.setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeToContents)

        self.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)

    # pylint: disable=useless-super-delegation
    def parent(self) -> ControlsWidget:
        """Return Parent."""
        return super().parent()

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
        return self.parent().get_map()

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
        brush = ControlsTable.BG_WARNING if warning else QtGui.QBrush()
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
        self._table.keyChanged.connect(self._set_map)
        self._table.itemSelectionChanged.connect(self._item_selection_changed)

    def hide(self):
        """Hide widget."""
        self._click_cancel()
        super().hide()

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
        self._selected_map = "keyboard"
        self._mapping = {
            "selected": self._selected_map,
            "maps": {
                self._selected_map: {
                    "map": ControlsTable.get_default_mapping(),
                    "options": options,
                }
            },
        }
        write_mapping(self._mapping)
        self._set_map(self.get_map())
        self._set_table()
        self._set_joysticks()

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
        self._click_cancel()
        if self._selected_map == "keyboard":
            self._table.set_mapping()

    def _set_joysticks(self):
        options = self.get_options()
        joysticks = options["joysticks"]

        self._left_joystick.setChecked(joysticks["left"])
        self._right_joystick.setChecked(joysticks["right"])

    def _set_options(self, options):
        self._mapping["maps"][self._selected_map]["options"] = options
        write_mapping(self._mapping)

    def _set_map(self, _map):
        self._mapping["maps"][self._selected_map]["map"] = _map
        write_mapping(self._mapping)

    def _item_selection_changed(self):
        if self._table.selectedItems():
            self._cancel.show()
            if not self._table.quit_selected():
                self._clear.show()
        else:
            self._cancel.hide()
            self._clear.hide()

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

    def _click_cancel(self):
        self._table.clearSelection()
        self._cancel.hide()
        self._clear.hide()

    def _click_clear(self):
        self._table.clear_control()

    def _click_reset(self):
        text = "Reset input mapping to default?"
        message(
            self, "Reset Mapping", text, "warning", self._default_mapping, escape=True
        )
