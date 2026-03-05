"""Drop bad epochs action widgets."""

from __future__ import annotations

import logging

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

logger = logging.getLogger(__name__)


# -------- Unit / default tables --------

UNITS: dict[str, tuple[str, float]] = {
    "eeg":  ("µV",    1e-6),
    "eog":  ("µV",    1e-6),
    "emg":  ("µV",    1e-6),
    "ecg":  ("µV",    1e-6),
    "mag":  ("fT",    1e-15),
    "grad": ("fT/cm", 1e-13),
}

REJECT_DEFAULTS: dict[str, float] = {
    "eeg":  150.0,
    "eog":  250.0,
    "emg":  500.0,
    "ecg":  500.0,
    "mag":  3000.0,
    "grad": 3500.0,
}

FLAT_DEFAULTS: dict[str, float] = {
    "eeg":  0.1,
    "eog":  0.1,
    "emg":  0.1,
    "ecg":  0.1,
    "mag":  0.1,
    "grad": 0.1,
}


# -------- ThresholdsValueWidget --------

class ThresholdsValueWidget(QWidget):
    """Hidden value widget storing a thresholds dict (SI units) or None."""

    value_changed = pyqtSignal()

    def __init__(self, value: dict | None, parent=None):
        super().__init__(parent)
        self.hide()
        self.value = value

    def set_value(self, v: dict | None):
        self.value = v
        self.value_changed.emit()

    def get_value(self) -> dict | None:
        return self.value


# -------- ThresholdsDialog --------

class ThresholdsDialog(QDialog):
    """Dialog for configuring per-channel-type amplitude thresholds.

    Values are stored in SI units on save.

    Args:
        raw: MNE Raw object used to detect channel types present in the data.
        current_value: Existing threshold dict in SI units, or None.
        defaults: Default display-unit values per channel type.
        title: Dialog window title.
        parent: Optional parent widget.
    """

    def __init__(
        self,
        raw,
        current_value: dict | None,
        defaults: dict[str, float],
        title: str,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(340)

        layout = QVBoxLayout(self)

        present_types = sorted(set(raw.get_channel_types())) if raw is not None else list(UNITS.keys())
        supported_types = [t for t in present_types if t in UNITS]

        self.rows: dict[str, tuple[QCheckBox, QDoubleSpinBox]] = {}

        if not supported_types:
            layout.addWidget(QLabel("No supported channel types found in data."))
        else:
            layout.addWidget(QLabel("Set a threshold for each channel type to reject:"))

            # Checkboxes + spinboxes for each channel type
            form = QFormLayout()
            for ch_type in supported_types:
                unit, factor = UNITS[ch_type]

                cb = QCheckBox()
                spinbox = QDoubleSpinBox()
                spinbox.setRange(0.001, 999999.0)
                spinbox.setDecimals(3)

                if current_value is not None and ch_type in current_value:
                    cb.setChecked(True)
                    spinbox.setValue(current_value[ch_type] / factor)
                else:
                    # If no current value, default to checked with the provided defaults
                    cb.setChecked(current_value is None)
                    spinbox.setValue(defaults.get(ch_type, 100.0))

                spinbox.setEnabled(cb.isChecked())
                cb.toggled.connect(spinbox.setEnabled)

                row_widget = QWidget()
                row_layout = QHBoxLayout(row_widget)
                row_layout.setContentsMargins(0, 0, 0, 0)
                row_layout.addWidget(cb)
                row_layout.addWidget(spinbox, 1)
                form.addRow(f"{ch_type} ({unit}):", row_widget)
                self.rows[ch_type] = (cb, spinbox)

            layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def get_value(self) -> dict | None:
        """Return threshold dict in SI units, or None if nothing is checked."""
        result = {}
        for ch_type, (cb, spinbox) in self.rows.items():
            if cb.isChecked():
                _, factor = UNITS[ch_type]
                result[ch_type] = float(f"{spinbox.value() * factor:.10g}")
        return result if result else None


# -------- Factories --------

def make_summary(value: dict | None) -> str:
    if not value:
        return ""
    parts = []
    for ch_type, si_val in sorted(value.items()):
        if ch_type in UNITS:
            unit, factor = UNITS[ch_type]
            parts.append(f"{ch_type}: {si_val / factor:.1f} {unit}")
        else:
            parts.append(f"{ch_type}: {si_val:.2e}")
    return ",  ".join(parts)


def build_defaults_dict(raw, defaults: dict[str, float]) -> dict:
    """Return a defaults dict in SI units for channel types present in raw."""
    present = set(raw.get_channel_types()) if raw is not None else set(UNITS.keys())
    return {
        t: float(f"{defaults[t] * UNITS[t][1]:.10g}")
        for t in present
        if t in defaults and t in UNITS
    }


def thresholds_factory(defaults: dict[str, float]):
    """Return a param widget factory for a threshold dict param."""

    def factory(param_def, current_value, raw, parent):
        value_widget = ThresholdsValueWidget(current_value)

        toggle = QCheckBox()
        toggle.setChecked(current_value is not None)

        summary_label = QLabel(make_summary(current_value))
        btn = QPushButton("Configure…")

        def set_active(enabled: bool):
            summary_label.setEnabled(enabled)
            btn.setEnabled(enabled)

        set_active(current_value is not None)

        def on_toggle(checked: bool):
            if checked and value_widget.get_value() is None:
                value_widget.set_value(build_defaults_dict(raw, defaults))
                summary_label.setText(make_summary(value_widget.get_value()))
            elif not checked:
                value_widget.set_value(None)
                summary_label.setText("")
            set_active(checked)

        toggle.toggled.connect(on_toggle)

        def open_dialog():
            dlg = ThresholdsDialog(
                raw=raw,
                current_value=value_widget.get_value(),
                defaults=defaults,
                title=param_def.get("label", "Thresholds"),
                parent=parent,
            )
            if dlg.exec() == QDialog.DialogCode.Accepted:
                new_val = dlg.get_value()
                value_widget.set_value(new_val)
                summary_label.setText(make_summary(new_val))
                if new_val is None:
                    toggle.setChecked(False)

        btn.clicked.connect(open_dialog)

        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(toggle)
        layout.addWidget(summary_label, 1)
        layout.addWidget(btn)

        return container, value_widget

    return factory


reject_thresholds_factory = thresholds_factory(REJECT_DEFAULTS)
flat_thresholds_factory = thresholds_factory(FLAT_DEFAULTS)
