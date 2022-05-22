"""GUI Main Methods for pyremoteplay."""
import sys
import logging
from PySide6 import QtCore, QtWidgets

from .main_window import MainWindow

logging.basicConfig(level=logging.INFO)


def main():
    """Run GUI."""
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
