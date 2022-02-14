# pylint: disable=c-extension-no-member
"""Toolbar Widget."""
from PySide6 import QtWidgets


class ToolbarWidget(QtWidgets.QToolBar):
    """Toolbar Widget."""

    def __init__(self, main_window):
        super().__init__(main_window)
        self.main_window = main_window
        self.refresh = QtWidgets.QPushButton("Auto Refresh")
        self.controls = QtWidgets.QPushButton("Controls")
        self.options = QtWidgets.QPushButton("Options")
        self.home = QtWidgets.QPushButton("Home")

        spacer = QtWidgets.QWidget()
        spacer.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred
        )
        self.widgets = [self.refresh, spacer, self.home, self.controls, self.options]
        self.options.clicked.connect(self._options_click)
        self.refresh.clicked.connect(self._refresh_click)
        self.controls.clicked.connect(self._controls_click)
        self.home.clicked.connect(self._home_click)

        for widget in self.widgets:
            self.addWidget(widget)
            if not isinstance(widget, QtWidgets.QPushButton):
                continue
            widget.setMaximumWidth(200)
            widget.setCheckable(True)
        self.home.setCheckable(False)
        self.set_button_visible(self.home, False)

    def set_button_visible(self, widget, visible: bool):
        """Set Button Visibility."""
        index = self.widgets.index(widget)
        self.actions()[index].setVisible(visible)

    def _main_hide(self):
        self.set_button_visible(self.refresh, False)
        self.set_button_visible(self.home, True)

    def _main_show(self):
        self.main_window.centralWidget().setCurrentWidget(self.main_window.main_frame)
        self.set_button_visible(self.refresh, True)

    def _home_click(self):
        self._main_show()
        self._options_hide()
        self._controls_hide()
        self.set_button_visible(self.home, False)

    def _options_click(self):
        if self.options.isChecked():
            self._main_hide()
            self._options_show()
            self._controls_hide()
        else:
            self._home_click()

    def _options_show(self):
        self.main_window.centralWidget().setCurrentWidget(self.main_window.options)

    def _options_hide(self):
        self.options.setChecked(False)

    def _controls_click(self):
        if self.controls.isChecked():
            self._main_hide()
            self._controls_show()
            self._options_hide()
        else:
            self._home_click()

    def _controls_show(self):
        self.main_window.centralWidget().setCurrentWidget(self.main_window.controls)

    def _controls_hide(self):
        self.controls.setChecked(False)

    def _refresh_click(self):
        if self.refresh.isChecked():
            self.main_window.device_grid.start_update()
        else:
            self._refresh_reset()

    def _refresh_reset(self):
        self.refresh.setChecked(False)
        self.main_window.device_grid.stop_update()
