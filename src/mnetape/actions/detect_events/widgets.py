"""Detect events action widgets."""

from __future__ import annotations

from PyQt6.QtWidgets import QComboBox

from mnetape.actions.base import ParamWidgetBinding

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


ecg_channel_factory = channel_factory("ecg")
eog_channel_factory = channel_factory("eog")


# -------- Widget bindings --------

WIDGET_BINDINGS = [
    ParamWidgetBinding("ecg_channel", ecg_channel_factory),
    ParamWidgetBinding("eog_channel", eog_channel_factory),
]
