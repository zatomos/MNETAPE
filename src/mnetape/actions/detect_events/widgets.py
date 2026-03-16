"""Detect events action widgets."""

from __future__ import annotations


from gi.repository import Gtk

from mnetape.actions.base import ParamWidgetBinding

AUTO_DETECT = "Auto-detect"

class ChannelCombo(Gtk.DropDown):
    """DropDown for channel selection.

    The first item is always "Auto-detect" which maps to an empty string (None).
    """

    def __init__(self, items: list[str]):
        self.items = items
        model = Gtk.StringList(strings=items)
        super().__init__(model=model)
        self.set_hexpand(True)

    def get_value(self) -> str:
        idx = self.get_selected()
        if idx == Gtk.INVALID_LIST_POSITION or idx < 0 or idx >= len(self.items):
            return ""
        t = self.items[idx]
        return "" if t == AUTO_DETECT else t

    def set_value_text(self, text: str) -> None:
        """Select the item matching text, or AUTO_DETECT if empty."""
        if not text:
            self.set_selected(0)
            return
        try:
            idx = self.items.index(text)
            self.set_selected(idx)
        except ValueError:
            # Add it
            self.items.append(text)
            self.get_model().append(text)
            self.set_selected(len(self.items) - 1)

def channel_factory(preferred_type: str):
    """Return a factory that populates a ChannelCombo with channels of preferred_type first."""

    def factory(current_value, raw):
        items = [AUTO_DETECT]

        if raw is not None:
            types = raw.get_channel_types()
            preferred = [ch for ch, t in zip(raw.ch_names, types) if t == preferred_type]
            others = [ch for ch, t in zip(raw.ch_names, types) if t != preferred_type]
            items.extend(preferred + others)

        combo = ChannelCombo(items)

        saved = str(current_value) if current_value else ""
        if saved:
            combo.set_value_text(saved)
        elif raw is not None:
            types = raw.get_channel_types()
            preferred = [ch for ch, t in zip(raw.ch_names, types) if t == preferred_type]
            if preferred:
                combo.set_value_text(preferred[0])

        return combo, combo

    return factory

ecg_channel_factory = channel_factory("ecg")
eog_channel_factory = channel_factory("eog")

# -------- Widget bindings --------

WIDGET_BINDINGS = [
    ParamWidgetBinding("ecg_channel", ecg_channel_factory),
    ParamWidgetBinding("eog_channel", eog_channel_factory),
]
