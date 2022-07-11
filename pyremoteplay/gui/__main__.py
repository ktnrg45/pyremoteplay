"""GUI Main Methods for pyremoteplay."""
import sys
import logging

from PySide6 import QtCore, QtWidgets

from .main_window import MainWindow


def main():
    """Run GUI."""
    if "-v" in sys.argv:
        level = logging.DEBUG
    else:
        level = logging.INFO
    logging.basicConfig(level=level)

    QtWidgets.QApplication.setHighDpiScaleFactorRoundingPolicy(
        QtCore.Qt.HighDpiScaleFactorRoundingPolicy.Floor
    )
    app = QtWidgets.QApplication([])
    app.setApplicationName("PyRemotePlay")
    widget = MainWindow()
    widget.resize(800, 600)
    widget.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
