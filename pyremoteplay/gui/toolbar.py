# pylint: disable=c-extension-no-member
"""Toolbar Widget."""
from PySide6 import QtCore, QtWidgets


class ToolbarWidget(QtWidgets.QWidget):
    """Toolbar Widget."""

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
        self.buttons = [self.home, self.controls, self.options]
        self.options.clicked.connect(self._options_click)
        self.refresh.clicked.connect(self._refresh_click)
        self.controls.clicked.connect(self._controls_click)
        self.home.clicked.connect(self._home_click)

        self.refresh.setMaximumWidth(200)
        self.refresh.setCheckable(True)
        self.layout.addWidget(self.refresh)
        self.layout.addStretch()
        for button in self.buttons:
            button.setMaximumWidth(200)
            button.setCheckable(True)
            self.layout.addWidget(button)
        self.home.setCheckable(False)
        self.buttons.append(self.refresh)

    def _main_hide(self):
        self.main_window.main_frame.hide()
        self.refresh.hide()
        self.home.show()

    def _main_show(self):
        self.main_window.main_frame.show()
        self.refresh.show()

    def _home_click(self):
        self._main_show()
        self._options_hide()
        self._controls_hide()
        self.home.hide()

    def _options_click(self):
        if self.options.isChecked():
            self._main_hide()
            self._options_show()
            self._controls_hide()
        else:
            self._home_click()

    def _options_show(self):
        self.main_window.options.show()

    def _options_hide(self):
        self.options.setChecked(False)
        self.main_window.options.hide()

    def _controls_click(self):
        if self.controls.isChecked():
            self._main_hide()
            self._controls_show()
            self._options_hide()
        else:
            self._home_click()

    def _controls_show(self):
        self.main_window.controls.show()

    def _controls_hide(self):
        self.controls.setChecked(False)
        self.main_window.controls.hide()

    def _refresh_click(self):
        if self.refresh.isChecked():
            self.main_window.device_grid.start_update()
        else:
            self._refresh_reset()

    def _refresh_reset(self):
        self.refresh.setChecked(False)
        self.main_window.device_grid.stop_update()
