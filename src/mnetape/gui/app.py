"""EEG Preprocessing Pipeline application entry point.

Configures matplotlib to use the GTK4Agg backend, disables the mne-qt-browser,
loads the CSS stylesheet from the assets directory, and launches the main window.

Entry point: main(), called by the mnetape console script.
"""

from __future__ import annotations

import logging
import os
import platform
import sys
from pathlib import Path

# ---- GTK version requirements must be set BEFORE any gi.repository imports ----
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("GtkSource", "5")

# matplotlib backend must be set before any matplotlib import
import matplotlib

matplotlib.use("GTK4Agg")

import matplotlib.pyplot as plt

import mne
from gi.repository import Adw, Gdk, Gio, Gtk

from mnetape.core.logging_config import setup_logging
from mnetape.gui.controllers import MainWindow

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


def load_css() -> None:
    """Load the application CSS stylesheet from the assets directory."""
    assets_dir = Path(__file__).with_name("assets")
    css_path = assets_dir / "style.css"
    if not css_path.exists():
        return

    css_data = css_path.read_bytes()
    provider = Gtk.CssProvider()
    provider.load_from_data(css_data)

    display = gdk_get_default_display()
    if display is not None:
        Gtk.StyleContext.add_provider_for_display(
            display,
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )


def gdk_get_default_display():
    """Return the default Gdk.Display, or None if not available."""
    try:
        return Gdk.Display.get_default()
    except Exception:
        return None


class MnetapeApplication(Adw.Application):
    """Main GTK application class."""

    def __init__(self):
        super().__init__(
            application_id="org.crnl.mnetape",
            flags=Gio.ApplicationFlags.DEFAULT_FLAGS,
        )
        self.main_window: MainWindow | None = None
        self.connect("activate", self.on_activate)

    def on_activate(self, _app):
        if self.main_window is not None:
            self.main_window.show()
            return

        # Load CSS
        load_css()

        # Disable MNE qt browser. Use matplotlib backend instead
        try:
            mne.viz.set_browser_backend("matplotlib")
        except Exception as e:
            logger.warning("Could not set MNE browser backend: %s", e)

        self.main_window = MainWindow(self)
        self.main_window.show()


def main():
    """Launch the EEG Preprocessing Pipeline GUI application."""
    # Clear console
    if platform.system() == "Windows":
        os.system("cls")
    else:
        os.system("clear")

    setup_logging()
    logger.info("Starting MNETAPE.")

    app = MnetapeApplication()
    sys.exit(app.run(sys.argv))


if __name__ == "__main__":
    main()
