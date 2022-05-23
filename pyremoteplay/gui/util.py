# pylint: disable=c-extension-no-member
"""GUI utilities."""

from PySide6 import QtWidgets


def format_qt_key(key: str) -> str:
    """Return formatted Qt Key name."""
    return key.replace("Key_", "").replace("Button", " Click")


def spacer():
    """Return Spacer."""
    return QtWidgets.QSpacerItem(20, 40)


def message(
    widget, title, text, level="critical", callback=None, escape=False, should_exec=True
):
    """Return Message box."""

    def clicked(msg, callback):
        button = msg.clickedButton()
        text = button.text().lower()
        if "ok" in text:
            callback()

    icon = QtWidgets.QMessageBox.Critical
    if level == "critical":
        icon = QtWidgets.QMessageBox.Critical
    elif level == "info":
        icon = QtWidgets.QMessageBox.Information
    elif level == "warning":
        icon = QtWidgets.QMessageBox.Warning
    msg = QtWidgets.QMessageBox(widget)
    msg.setIcon(icon)
    msg.setWindowTitle(title)
    msg.setText(text)
    if escape:
        msg.setStandardButtons(QtWidgets.QMessageBox.Ok | QtWidgets.QMessageBox.Cancel)
    else:
        msg.setStandardButtons(QtWidgets.QMessageBox.Ok)
    if callback is not None:
        msg.buttonClicked.connect(lambda: clicked(msg, callback))
    if should_exec:
        msg.exec()
    return msg


def label(parent, text: str, wrap: bool = False):
    """Return Label."""
    _label = QtWidgets.QLabel(parent)
    _label.setText(text)
    _label.setWordWrap(wrap)
    return _label
