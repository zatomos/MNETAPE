"""Step navigation and MNE browser shortcut for the main window.

NavController manages the step-selector combo box and the MNE browser shortcut.
"""

from __future__ import annotations

import logging

from PyQt6.QtWidgets import QMessageBox
from typing import TYPE_CHECKING

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
            self.w.action_list.setCurrentRow(idx - 1)
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
        """Open MNE's interactive raw browser for the currently selected step."""

        step = self.w.viz_panel.step_combo.currentIndex()
        if step == 0 and self.state.raw_original:
            self.state.raw_original.plot(block=True, title="Original")
        elif 0 < step <= len(self.state.raw_states):
            self.state.raw_states[step - 1].plot(block=True, title=f"After step {step}")
        else:
            QMessageBox.warning(self.w, "No Data", "No data to display.")
