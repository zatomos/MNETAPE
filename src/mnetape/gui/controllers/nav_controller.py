"""Step navigation and MNE browser shortcut for the main window.

NavController manages the MNE browser shortcut and step-based visualization updates.
"""

from __future__ import annotations

import logging

import mne
from PyQt6.QtWidgets import QMessageBox
from typing import TYPE_CHECKING

from mnetape.core.models import ICASolution
from mnetape.gui.widgets.common import (
    disable_mne_browser_channel_clicks,
    sanitize_mne_browser_toolbar,
)

if TYPE_CHECKING:
    from mnetape.gui.pages.preprocessing_page import PreprocessingPage

logger = logging.getLogger(__name__)


class NavController:
    """Handles step-selector navigation and MNE browser launch."""

    def __init__(self, window: "PreprocessingPage") -> None:
        self.w = window
        self.state = window.state

    def open_browser(self):
        """Open MNE's interactive browser for the currently selected step."""
        step = self.w.viz_panel.current_step
        if step == 0 and self.state.raw_original:
            browser = self.state.raw_original.plot(block=False, title="Original")
            sanitize_mne_browser_toolbar(browser, allow_annotation_mode=False)
            disable_mne_browser_channel_clicks(browser)
        elif 0 < step <= len(self.state.data_states):
            data = self.state.data_states[step - 1]
            if data is None:
                return
            elif isinstance(data, ICASolution):
                # ICA slots: show the raw the ICA was fitted on
                browser = data.raw.plot(block=False, title=f"Raw at ICA step {step}")
                sanitize_mne_browser_toolbar(browser, allow_annotation_mode=False)
                disable_mne_browser_channel_clicks(browser)
            elif isinstance(data, mne.Evoked):
                data.plot(show=True)
            else:
                browser = data.plot(block=False, title=f"After step {step}")
                sanitize_mne_browser_toolbar(browser, allow_annotation_mode=False)
                disable_mne_browser_channel_clicks(browser)
        else:
            QMessageBox.warning(self.w, "No Data", "No data to display.")
