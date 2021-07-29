"""For pyremoteplay/gui."""
import sys

from PySide6 import QtWidgets

from .main_window import MainWindow


def run():
    """Run GUI."""
    app = QtWidgets.QApplication([])
    widget = MainWindow(app)
    widget.resize(800, 600)
    widget.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    run()
