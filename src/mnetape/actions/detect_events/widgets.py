"""Detect events action widgets."""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QWidget,
)

from mnetape.actions.base import ParamWidgetBinding
from mnetape.gui.dialogs.channel_picker_dialog import ChannelPickerDialog

AUTO_DETECT = "Auto-detect"


class ChannelCombo(QComboBox):
    """Non-editable combo for channel selection.

    The first item is always "Auto-detect": maps to None.
    """

    def get_value(self) -> str:
        t = self.currentText()
        return "" if t == AUTO_DETECT else t


def channel_factory(preferred_type: str):
    """Return a factory that populates a ChannelCombo with channels of preferred_type first.

    If a channel of the preferred type exists and no value is saved, the first matching channel
    is pre-selected automatically.
    """

    def factory(current_value, raw, parent):
        combo = ChannelCombo()

        combo.addItem(AUTO_DETECT)

        if raw is not None:
            types = raw.get_channel_types()
            preferred = [ch for ch, t in zip(raw.ch_names, types) if t == preferred_type]
            others = [ch for ch, t in zip(raw.ch_names, types) if t != preferred_type]
            combo.addItems(preferred + others)

        saved = str(current_value) if current_value else ""
        if saved:
            idx = combo.findText(saved)
            if idx >= 0:
                combo.setCurrentIndex(idx)
            else:
                # Saved value not found in raw, add it to the combo and select it
                combo.addItem(saved)
                combo.setCurrentText(saved)
        elif raw is not None:
            types = raw.get_channel_types()
            preferred = [ch for ch, t in zip(raw.ch_names, types) if t == preferred_type]
            if preferred:
                combo.setCurrentText(preferred[0])

        return combo, combo

    return factory


def threshold_channel_factory(current_value, raw, parent):
    """Factory for the threshold_channel param button."""
    saved = ", ".join(current_value) if isinstance(current_value, list) else (str(current_value) if current_value else "")

    line_edit = QLineEdit(saved)
    line_edit.setPlaceholderText("all channels if empty")

    btn_pick = QPushButton("Pick...")
    btn_pick.setEnabled(raw is not None)

    def pick():
        if raw is None:
            return
        selected = [c.strip() for c in line_edit.text().split(",") if c.strip()]
        dlg = ChannelPickerDialog(raw, selected, parent, title="Select Channels to Scan (Drop Channels)")
        if dlg.exec() == QDialog.DialogCode.Accepted:
            line_edit.setText(", ".join(dlg.get_selected()))

    btn_pick.clicked.connect(pick)

    container = QWidget()
    layout = QHBoxLayout(container)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.addWidget(line_edit, 1)
    layout.addWidget(btn_pick)

    return container, line_edit


ecg_channel_factory = channel_factory("ecg")
eog_channel_factory = channel_factory("eog")


# -------- Widget bindings --------

WIDGET_BINDINGS = [
    ParamWidgetBinding("ecg_channel", ecg_channel_factory),
    ParamWidgetBinding("eog_channel", eog_channel_factory),
    ParamWidgetBinding("threshold_channel", threshold_channel_factory),
]
