# pylint: disable=c-extension-no-member,invalid-name
"""Controls Widget."""
from __future__ import annotations
import logging
from typing import Union
from PySide6 import QtWidgets, QtCore, QtGui
from PySide6.QtCore import Qt  # pylint: disable=no-name-in-module
from pyremoteplay.util import get_mapping, write_mapping

from .util import message, format_qt_key
from .widgets import AnimatedToggle

_LOGGER = logging.getLogger(__name__)


class ControlsTable(QtWidgets.QTableWidget):
    """Table for controls."""

    RP_KEYS = (
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

    BG_WARNING = QtGui.QBrush("#EA868F")

    keyChanged = QtCore.Signal(dict)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setRowCount(len(ControlsTable.RP_KEYS))
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

        for index, rp_key in enumerate(ControlsTable.RP_KEYS):
            flags = Qt.ItemIsEnabled | Qt.ItemIsSelectable
            blank = QtWidgets.QTableWidgetItem()
            item = QtWidgets.QTableWidgetItem(rp_key)
            blank.setFlags(flags)
            item.setFlags(flags)
            self.setItem(index, 0, blank)
            self.setItem(index, 1, item)

        for key, rp_key in mapping.items():
            item = self.item(ControlsTable.RP_KEYS.index(rp_key), 0)
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
        quit_row = ControlsTable.RP_KEYS.index("QUIT")
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
        key = key.name.decode()
        items = self.selectedItems()
        if not items:
            return
        if len(items) < 2:
            _LOGGER.warning("Incomplete row selected")
            return

        old_key = items[0].data(Qt.UserRole)
        rp_key = items[1].text()

        # Swap keys if set
        old_rp_key = self._get_current_rp_key(key)
        if old_rp_key:
            if rp_key == old_rp_key:
                self.clearSelection()
                return
            # Don't allow QUIT to be None
            if old_rp_key == "QUIT":
                self._set_item_warning("QUIT", True)
                self._set_item_warning(rp_key, True)
                self.clearSelection()
                return
            index = ControlsTable.RP_KEYS.index(old_rp_key)
            self._set_key_item(self.item(index, 0), old_key)
        self._set_key_item(items[0], key)

        for _rp_key in ControlsTable.RP_KEYS:
            self._set_item_warning(_rp_key, False)

        self.clearSelection()
        self.keyChanged.emit(self._get_map())

    def _set_item_warning(self, rp_key: str, warning: bool):
        row = ControlsTable.RP_KEYS.index(rp_key)
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

    def _get_current_key(self, rp_key: str) -> str:
        """Return Qt Key name from rp_key."""
        _map = self._get_map()
        rp_keys = list(_map.values())
        if rp_key not in rp_keys:
            return None
        index = rp_keys.index(rp_key)
        key = list(_map.keys())[index]
        return key

    def _get_current_rp_key(self, key: str) -> str:
        """Return RP key from Qt key."""
        _map = self._get_map()
        rp_key = _map.get(key)
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

        self._mapping = {
            "selected": "keyboard",
            "maps": {
                "keyboard": {
                    "map": ControlsTable.DEFAULT_MAPPING.copy(),
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
