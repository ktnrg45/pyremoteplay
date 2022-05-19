# pylint: disable=c-extension-no-member
"""Toolbar Widget."""
from PySide6 import QtWidgets, QtCore


class ToolbarWidget(QtWidgets.QToolBar):
    """Toolbar Widget."""

    buttonClicked = QtCore.Signal(QtWidgets.QPushButton)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.refresh = QtWidgets.QPushButton("Auto Refresh")
        self.controls = QtWidgets.QPushButton("Controls")
        self.options = QtWidgets.QPushButton("Options")
        self.home = QtWidgets.QPushButton("Home")

        spacer = QtWidgets.QWidget()
        spacer.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred
        )
        self.widgets = [self.refresh, spacer, self.home, self.controls, self.options]

        for widget in self.widgets:
            self.addWidget(widget)
            if not isinstance(widget, QtWidgets.QPushButton):
                continue
            widget.setMaximumWidth(200)
            widget.setCheckable(True)
            widget.clicked.connect(self._button_click)
        self._button_group = QtWidgets.QButtonGroup()
        self._button_group.addButton(self.controls)
        self._button_group.addButton(self.options)
        self._button_group.addButton(self.home)
        self._button_group.setExclusive(True)
        self.home.setChecked(True)
        self.refresh.setChecked(True)

    def _button_click(self):
        button = self.sender()
        self.buttonClicked.emit(button)
