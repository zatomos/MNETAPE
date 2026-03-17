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
    """

    def __init__(self, state: AppState, parent=None):
        super().__init__(parent)

        self.cache_size_spin = None
        self.max_states_spin = None

        self.state = state
        self.setWindowTitle("Preferences")
        self.setMinimumWidth(380)
        self.build_ui()

    def build_ui(self):
        layout = QVBoxLayout(self)

        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)

        self.cache_size_spin = QSpinBox()
        self.cache_size_spin.setRange(1, 20)
        self.cache_size_spin.setValue(self.state.data_states.cache_size)
        self.cache_size_spin.setToolTip(
            "Number of pipeline checkpoints kept in RAM simultaneously."
        )
        form.addRow("Max checkpoints in RAM:", self.cache_size_spin)

        cache_hint = QLabel(
            "Number of pipeline checkpoints kept in memory at once. "
            "Higher values speed up step navigation at the cost of memory. "
            "Reduce this if you run out of RAM with large files."
        )
        cache_hint.setWordWrap(True)
        cache_hint.setStyleSheet("color: gray; font-size: 11px;")
        form.addRow("", cache_hint)

        self.max_states_spin = QSpinBox()
        self.max_states_spin.setRange(0, 99)
        self.max_states_spin.setValue(self.state.data_states.max_disk_states)
        self.max_states_spin.setSpecialValueText("Unlimited")
        self.max_states_spin.setToolTip(
            "Maximum number of pipeline checkpoints stored on disk."
        )
        form.addRow("Max checkpoints on disk:", self.max_states_spin)

        disk_hint = QLabel(
            "Maximum number of pipeline checkpoints stored on disk. "
            "Older checkpoints are removed first when the limit is exceeded. "
            "Reducing this saves disk space but requires re-running to view older steps."
        )
        disk_hint.setWordWrap(True)
        disk_hint.setStyleSheet("color: gray; font-size: 11px;")
        form.addRow("", disk_hint)

        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def save(self):
        cache_size = self.cache_size_spin.value()
        self.state.data_states.cache_size = cache_size
        self.state.settings.setValue("data_store/cache_size", cache_size)

        max_states = self.max_states_spin.value()
        self.state.data_states.max_disk_states = max_states
        self.state.settings.setValue("data_store/max_disk_states", max_states)
        self.accept()
