"""Preferences dialog for persistent user settings."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QSpinBox,
    QVBoxLayout,
)

if TYPE_CHECKING:
    from mnetape.gui.controllers.pipeline_state import PipelineState


class PreferencesDialog(QDialog):
    """Modal dialog for editing persistent application preferences."""

    def __init__(self, state: PipelineState | None = None, parent=None, *, settings: QSettings | None = None):
        super().__init__(parent)

        self.cache_size_spin: QSpinBox
        self.max_states_spin: QSpinBox
        self.qc_auto_check: QCheckBox
        self.qc_events_check: QCheckBox
        self.state = state
        self.settings: QSettings = state.settings if state is not None else (settings or QSettings())
        self.setWindowTitle("Preferences")
        self.setMinimumWidth(380)
        self.build_ui()

    def build_ui(self):
        layout = QVBoxLayout(self)

        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)

        cache_default = self.state.data_states.cache_size if self.state is not None else int(self.settings.value("data_store/cache_size", 2))
        self.cache_size_spin = QSpinBox()
        self.cache_size_spin.setRange(1, 20)
        self.cache_size_spin.setValue(cache_default)
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

        disk_default = self.state.data_states.max_disk_states if self.state is not None else int(self.settings.value("data_store/max_disk_states", 0))
        self.max_states_spin = QSpinBox()
        self.max_states_spin.setRange(0, 99)
        self.max_states_spin.setValue(disk_default)
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

        # ---- QC Report ----
        qc_group = QGroupBox("QC Report")
        qc_form = QFormLayout(qc_group)
        qc_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)

        self.qc_auto_check = QCheckBox("Auto-generate when pipeline completes")
        self.qc_auto_check.setChecked(
            self.settings.value("qc/auto_generate", True, type=bool)
        )
        qc_form.addRow(self.qc_auto_check)

        self.qc_events_check = QCheckBox("Include Events Viewer (ERP / TFR evolution)")
        self.qc_events_check.setChecked(
            self.settings.value("qc/events_viewer_enabled", True, type=bool)
        )
        qc_events_hint = QLabel(
            "Computes ERP and TFR for every step. Can be slow on long recordings "
            "or pipelines with many steps."
        )
        qc_events_hint.setWordWrap(True)
        qc_events_hint.setStyleSheet("color: gray; font-size: 11px;")
        qc_form.addRow(self.qc_events_check)
        qc_form.addRow("", qc_events_hint)

        layout.addWidget(qc_group)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def save(self):
        cache_size = self.cache_size_spin.value()
        self.settings.setValue("data_store/cache_size", cache_size)
        if self.state is not None:
            self.state.data_states.cache_size = cache_size

        max_states = self.max_states_spin.value()
        self.settings.setValue("data_store/max_disk_states", max_states)
        if self.state is not None:
            self.state.data_states.max_disk_states = max_states

        self.settings.setValue("qc/auto_generate", self.qc_auto_check.isChecked())
        self.settings.setValue("qc/events_viewer_enabled", self.qc_events_check.isChecked())

        self.accept()
