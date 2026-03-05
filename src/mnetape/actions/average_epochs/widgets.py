"""Average epochs action widgets."""

from __future__ import annotations

import mne
from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QWidget


class EventKeyWidget(QWidget):
    """Widget for selecting an epoch event key, or averaging all events."""

    value_changed = pyqtSignal()

    def __init__(self, event_keys: list[str], current_value: str | None, parent=None):
        super().__init__(parent)
        self.combo = QComboBox(self)
        self.combo.addItem("All events", None)

        for key in event_keys:
            self.combo.addItem(key, key)

        idx = 0
        if current_value:
            found = self.combo.findData(current_value)
            if found >= 0:
                idx = found
        self.combo.setCurrentIndex(idx)
        self.combo.currentIndexChanged.connect(lambda _: self.value_changed.emit())

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.combo)

    def get_value(self) -> str | None:
        value = self.combo.currentData()
        return str(value) if value else None


def event_key_factory(param_def, current_value, raw, parent):
    """Param widget factory for selecting a single event key."""

    event_keys: list[str] = []
    hint_text = ""

    if isinstance(raw, mne.Epochs):
        event_keys = sorted(raw.event_id.keys())
        if not event_keys:
            hint_text = "No event IDs found in the current epochs."
    else:
        hint_text = "Run epoching first to select an event condition."

    value_widget = EventKeyWidget(event_keys, current_value, parent)

    container = QWidget(parent)
    layout = QHBoxLayout(container)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.addWidget(value_widget, 1)

    if hint_text:
        hint = QLabel(hint_text, container)
        hint.setStyleSheet("color: #777777;")
        layout.addWidget(hint)

    return container, value_widget
