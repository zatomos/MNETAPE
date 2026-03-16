"""MNE browser shortcut for the main window."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import mne
from gi.repository import Adw

from mnetape.core.models import ICASolution
from mnetape.gui.widgets.common import (
    disable_mne_browser_channel_clicks,
    sanitize_mne_browser_toolbar,
)

if TYPE_CHECKING:
    from mnetape.gui.controllers.main_window import MainWindow

logger = logging.getLogger(__name__)

class NavController:
    """Handles the MNE browser launch shortcut."""

    def __init__(self, window: MainWindow) -> None:
        self.w = window
        self.state = window.state

    def open_browser(self, _action=None, _param=None):
        """Open MNE's interactive browser for the latest computed pipeline step."""
        # Find the latest non-None data state
        data = None
        step_label = "Original"
        for i in range(len(self.state.data_states) - 1, -1, -1):
            candidate = self.state.data_states[i]
            if candidate is not None:
                data = candidate
                step_label = f"After step {i + 1}"
                break

        if data is None:
            if self.state.raw_original is None:
                dlg = Adw.AlertDialog(heading="No Data", body="No data to display.")
                dlg.add_response("ok", "OK")
                dlg.set_default_response("ok")
                dlg.present(self.w.window)
                return
            data = self.state.raw_original
            step_label = "Original"

        if isinstance(data, ICASolution):
            browser = data.raw.plot(block=False, title=step_label)
            sanitize_mne_browser_toolbar(browser, allow_annotation_mode=False)
            disable_mne_browser_channel_clicks(browser)
        elif isinstance(data, mne.Evoked):
            data.plot(show=True)
        else:
            browser = data.plot(block=False, title=step_label)
            sanitize_mne_browser_toolbar(browser, allow_annotation_mode=False)
            disable_mne_browser_channel_clicks(browser)
