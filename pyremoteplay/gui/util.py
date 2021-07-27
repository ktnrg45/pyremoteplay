"""GUI utilities."""

from PySide6 import QtWidgets
from PySide6.QtCore import Qt


class Popup(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()

    def set_text(self, text):
        self.center_text = QtWidgets.QLabel(text, alignment=Qt.AlignCenter)


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


def label(parent, text: str, wrap: bool = False):
    label = QtWidgets.QLabel(parent)
    label.setText(text)
    label.setWordWrap(wrap)
    return label
