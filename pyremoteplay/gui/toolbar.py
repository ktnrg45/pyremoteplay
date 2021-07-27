from PySide6 import QtCore, QtWidgets


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
        self.main_window.device_grid.stop_update()
