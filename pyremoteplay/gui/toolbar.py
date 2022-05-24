# pylint: disable=c-extension-no-member
"""Toolbar Widget."""
from PySide6 import QtWidgets, QtCore
from PySide6.QtCore import Qt


class ToolbarWidget(QtWidgets.QToolBar):
    """Toolbar Widget."""

    buttonClicked = QtCore.Signal(QtWidgets.QPushButton)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setMovable(False)
        self.toggleViewAction().setEnabled(False)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self._refresh = QtWidgets.QPushButton("Auto Refresh")
        self._controls = QtWidgets.QPushButton("Controls")
        self._options = QtWidgets.QPushButton("Options")
        self._home = QtWidgets.QPushButton("Home")

        spacer = QtWidgets.QWidget()
        spacer.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred
        )
        widgets = [self._refresh, spacer, self._home, self._controls, self._options]

        for widget in widgets:
            self.addWidget(widget)
            if not isinstance(widget, QtWidgets.QPushButton):
                continue
            widget.setMaximumWidth(200)
            widget.setCheckable(True)
            widget.clicked.connect(self._button_click)
        self._button_group = QtWidgets.QButtonGroup()
        self._button_group.addButton(self._controls)
        self._button_group.addButton(self._options)
        self._button_group.addButton(self._home)
        self._button_group.setExclusive(True)
        self._home.setChecked(True)

    @QtCore.Slot()
    def _button_click(self):
        button = self.sender()
        self.buttonClicked.emit(button)

    def home(self) -> QtWidgets.QPushButton:
        """Return Home Button."""
        return self._home

    def refresh(self) -> QtWidgets.QPushButton:
        """Return Refresh Button."""
        return self._refresh

    def controls(self) -> QtWidgets.QPushButton:
        """Return Controls Button."""
        return self._controls

    def options(self) -> QtWidgets.QPushButton:
        """Return Options Button."""
        return self._options
