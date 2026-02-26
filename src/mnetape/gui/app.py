"""EEG Preprocessing Pipeline application entry point.

Configures matplotlib to use the QtAgg backend and applies a light color scheme.
Loads the QSS stylesheet from the assets directory and launches the main window.

Entry point: main(), called by the eeg-ui console script.
"""

import logging
import os
import platform
import sys
from pathlib import Path

import matplotlib
import matplotlib.pyplot as plt
from PyQt6.QtWidgets import QApplication

from mnetape.core.logging_config import setup_logging
from mnetape.gui.controllers import MainWindow

matplotlib.use("QtAgg")
logger = logging.getLogger(__name__)

# Light theme for matplotlib plots
plt.rcParams.update(
    {
        "figure.facecolor": "#FFFFFF",
        "axes.facecolor": "#FFFFFF",
        "axes.edgecolor": "#CCCCCC",
        "axes.labelcolor": "#222222",
        "text.color": "#222222",
        "xtick.color": "#444444",
        "ytick.color": "#444444",
        "grid.color": "#E6E6E6",
        "figure.edgecolor": "#FFFFFF",
    }
)


def load_stylesheet() -> str:
    """Load the application QSS stylesheet from the assets directory.

    Replaces the {assets} placeholder in the stylesheet with the absolute
    path to the assets directory so that url() references resolve correctly.

    Returns:
        The stylesheet string, or an empty string if the file is not found.
    """
    assets_dir = Path(__file__).with_name("assets")
    stylesheet_path = assets_dir / "style.qss"
    if not stylesheet_path.exists():
        return ""
    css = stylesheet_path.read_text()
    # Replace {assets} placeholder with the absolute assets directory path
    return css.replace("{assets}", assets_dir.as_posix())


def main():
    """Launch the EEG Preprocessing Pipeline GUI application.

    Clears the terminal, initializes logging, creates the QApplication with the Fusion style and the bundled stylesheet,
    and enters the Qt event loop.
    """
    # Clear console
    if platform.system() == "Windows":
        os.system("cls")
    else:
        os.system("clear")

    setup_logging()
    logger.info("Starting EEG GUI application.")

    # Create the Qt application
    app = QApplication(sys.argv)
    app.setOrganizationName("CRNL")
    app.setApplicationName("MNETAPE")
    app.setStyle("Fusion")
    app.setStyleSheet(load_stylesheet())

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
