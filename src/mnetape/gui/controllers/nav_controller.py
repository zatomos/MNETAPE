"""Step navigation and MNE browser shortcut for the main window.

NavController manages the step-selector combo box and the MNE browser shortcut.
"""

from __future__ import annotations

import logging

from PyQt6.QtWidgets import QMessageBox
from typing import TYPE_CHECKING

from mnetape.gui.widgets.common import (
    disable_mne_browser_channel_clicks,
    sanitize_mne_browser_toolbar,
)

if TYPE_CHECKING:
    from mnetape.gui.controllers.main_window import MainWindow

logger = logging.getLogger(__name__)


class NavController:
    """Handles step-selector navigation and MNE browser launch."""

    def __init__(self, window: MainWindow) -> None:
        self.w = window
        self.state = window.state

    def on_step_changed(self):
        """Respond to the step combo box selection changing."""
        self.w.update_visualization()

        idx = self.w.viz_panel.step_combo.currentIndex()
        if 0 < idx <= len(self.state.actions):
            self.w.set_selected_action_row(idx - 1)
        else:
            self.w.action_list.clearSelection()
        self.w.update_button_states()

    def prev_step(self):
        """Move the step combo to the previous entry."""
        idx = self.w.viz_panel.step_combo.currentIndex()
        if idx > 0:
            self.w.viz_panel.step_combo.setCurrentIndex(idx - 1)

    def next_step(self):
        """Move the step combo to the next entry."""
        idx = self.w.viz_panel.step_combo.currentIndex()
        if idx < self.w.viz_panel.step_combo.count() - 1:
            self.w.viz_panel.step_combo.setCurrentIndex(idx + 1)

    def open_browser(self):
        """Open MNE's interactive browser for the currently selected step."""
        import mne

        step = self.w.viz_panel.step_combo.currentIndex()
        if step == 0 and self.state.raw_original:
            browser = self.state.raw_original.plot(block=False, title="Original")
            sanitize_mne_browser_toolbar(browser, allow_annotation_mode=False)
            disable_mne_browser_channel_clicks(browser)
        elif 0 < step <= len(self.state.data_states):
            data = self.state.data_states[step - 1]
            if data is None:
                QMessageBox.warning(self.w, "No Data", "This step has not been computed yet.")
            else:
                if isinstance(data, mne.Evoked):
                    data.plot(show=True)
                else:
                    browser = data.plot(block=False, title=f"After step {step}")
                    sanitize_mne_browser_toolbar(browser, allow_annotation_mode=False)
                    disable_mne_browser_channel_clicks(browser)
        else:
            QMessageBox.warning(self.w, "No Data", "No data to display.")
