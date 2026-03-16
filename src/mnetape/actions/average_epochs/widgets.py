"""Average epochs action widgets."""

from __future__ import annotations


import mne
from gi.repository import Gtk

from mnetape.actions.base import ParamWidgetBinding

class EventKeyWidget(Gtk.Box):
    """Widget for selecting an epoch event key, or averaging all events."""

    def __init__(self, event_keys: list[str], current_value: str | None):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self.set_hexpand(True)

        # Items
        self.items = ["All events"] + list(event_keys)
        self.values: list[str | None] = [None] + list(event_keys)

        model = Gtk.StringList(strings=self.items)
        self.combo = Gtk.DropDown(model=model)
        self.combo.set_hexpand(True)
        self.append(self.combo)

        # Select current value
        idx = 0
        if current_value:
            try:
                idx = self.values.index(current_value)
            except ValueError:
                idx = 0
        self.combo.set_selected(idx)

    def get_value(self) -> str | None:
        idx = self.combo.get_selected()
        if idx == Gtk.INVALID_LIST_POSITION or idx < 0:
            return None
        return self.values[idx]

    def connect_value_changed(self, cb):
        self.combo.connect("notify::selected", lambda *_: cb())

def event_key_factory(current_value, raw):
    """Param widget factory for selecting a single event key."""
    event_keys: list[str] = []
    hint_text = ""

    if isinstance(raw, mne.Epochs):
        event_keys = sorted(raw.event_id.keys())
        if not event_keys:
            hint_text = "No event IDs found in the current epochs."
    else:
        hint_text = "Run epoching first to select an event condition."

    value_widget = EventKeyWidget(event_keys, current_value)

    container = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
    container.set_hexpand(True)
    container.append(value_widget)

    if hint_text:
        hint = Gtk.Label(label=hint_text)
        hint.add_css_class("dim-label")
        container.append(hint)

    return container, value_widget

# -------- Widget bindings --------

WIDGET_BINDINGS = [
    ParamWidgetBinding("event_key", event_key_factory),
]
