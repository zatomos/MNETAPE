"""Preferences dialog for persistent user settings."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QSpinBox,
    QVBoxLayout,
)

if TYPE_CHECKING:
    from mnetape.gui.controllers.state import AppState


class PreferencesDialog(QDialog):
    """Modal dialog for editing persistent application preferences.

    Currently exposes:
        - Maximum on-disk checkpoints (DataStore.max_disk_states)
    """

    def __init__(self, state: AppState, parent=None):
        super().__init__(parent)

        self.max_states_spin = None

        self.state = state
        self.setWindowTitle("Preferences")
        self.setMinimumWidth(380)
        self.build_ui()

    def build_ui(self):
        layout = QVBoxLayout(self)

        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)

        self.max_states_spin = QSpinBox()
        self.max_states_spin.setRange(0, 999)
        self.max_states_spin.setValue(self.state.data_states.max_disk_states)
        self.max_states_spin.setSpecialValueText("Unlimited")
        self.max_states_spin.setToolTip(
            "Maximum number of pipeline checkpoints stored on disk.\n"
        )
        form.addRow("Max checkpoints on disk:", self.max_states_spin)

        hint = QLabel(
            "Maximum number of pipeline checkpoints stored on disk. "
            "Older checkpoints are removed first when the limit is exceeded. \n"
            "Reducing this saves disk space but requires re-running to view older steps."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: gray; font-size: 11px;")
        form.addRow("", hint)

        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def save(self):
        max_states = self.max_states_spin.value()
        self.state.data_states.max_disk_states = max_states
        self.state.settings.setValue("data_store/max_disk_states", max_states)
        self.accept()
