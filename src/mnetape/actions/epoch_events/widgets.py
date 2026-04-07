"""Event-based epoching action widgets."""

from __future__ import annotations

import logging

import mne
import pandas as _pd

from mnetape.actions.base import ParamWidgetBinding
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)
from PyQt6.QtCore import pyqtSignal

logger = logging.getLogger(__name__)


# -------- EventPickerDialog --------

class EventPickerDialog(QDialog):
    """Dialog for selecting which event IDs to include.

    Discovers available events from the recording using the specified source
    and source parameters, then presents a checklist for the user to choose from.

    Args:
        raw: MNE Raw object used for event discovery.
        source: One of "annotations", "stim", or "file".
        stim_channel: Channel name for stim source (None = auto-detect).
        min_duration: Minimum stimulus duration for stim source.
        shortest_event: Minimum event length in samples for stim source.
        events_file: Path to events file for file source.
        current_value: Previously selected event_id dict (or None = all events).
        parent: Optional parent widget.
    """

    def __init__(
        self,
        raw: mne.io.Raw | None,
        source: str,
        stim_channel: str | None,
        min_duration: float,
        shortest_event: int,
        events_file: str,
        current_value: dict | None,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Pick Events")
        self.setMinimumWidth(300)

        self.id_map: dict[str, int] = {}
        self.checkboxes: dict[str, QCheckBox] = {}

        layout = QVBoxLayout(self)

        # Discover events
        id_map, error = self.discover(raw, source, stim_channel, min_duration, shortest_event, events_file)
        self.id_map = id_map

        if error:
            layout.addWidget(QLabel(f"Could not discover events:\n{error}"))
        elif not id_map:
            layout.addWidget(QLabel("No events found."))
        else:
            layout.addWidget(QLabel(f"{len(id_map)} event type(s) found:"))

            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setMaximumHeight(200)
            inner = QWidget()
            inner_layout = QVBoxLayout(inner)
            inner_layout.setContentsMargins(4, 4, 4, 4)
            inner_layout.setSpacing(2)

            selected = set(current_value.keys()) if isinstance(current_value, dict) else set()
            check_all = not selected

            for name, code in sorted(id_map.items()):
                cb = QCheckBox(f"{name}  (code {code})")
                cb.setChecked(check_all or name in selected)
                inner_layout.addWidget(cb)
                self.checkboxes[name] = cb

            scroll.setWidget(inner)
            layout.addWidget(scroll)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @staticmethod
    def discover(
        raw, source, stim_channel, min_duration, shortest_event, events_file
    ) -> tuple[dict[str, int], str]:
        """Return (id_map, error_message). id_map is empty on failure."""
        try:
            if source == "stim":
                if raw is None:
                    return {}, "No data loaded."
                ch = stim_channel or None
                events = mne.find_events(raw, stim_channel=ch, min_duration=min_duration, shortest_event=shortest_event, verbose=False)
                return {str(int(c)): int(c) for c in sorted(set(events[:, 2]))}, ""
            if source == "file":
                if not events_file:
                    return {}, "No events file specified."
                if events_file.lower().endswith(".tsv"):
                    df = _pd.read_csv(events_file, sep="\t")
                    if "event_type" in df.columns:
                        col = "event_type"
                    elif "trial_type" in df.columns:
                        col = "trial_type"
                    else:
                        return {}, (
                            f"BIDS events file has no 'event_type' or 'trial_type' column. "
                            f"Available columns: {list(df.columns)}"
                        )
                    descs = df[col].fillna("n/a").astype(str)
                    id_map = {d: i + 1 for i, d in enumerate(sorted(set(descs)))}
                    return id_map, ""
                events = mne.read_events(events_file)
                return {str(int(c)): int(c) for c in sorted(set(events[:, 2]))}, ""
            # annotations
            if raw is None:
                return {}, "No data loaded."
            _, raw_id_map = mne.events_from_annotations(raw, verbose=False)
            return {str(k): int(v) for k, v in raw_id_map.items()}, ""
        except Exception as e:
            logger.exception("Event discovery failed for source '%s'", source)
            return {}, str(e)

    def get_value(self) -> dict | None:
        """Return selected event_id dict, or None if all events are selected."""
        if not self.id_map:
            return None
        checked = {name: self.id_map[name] for name, cb in self.checkboxes.items() if cb.isChecked()}
        if not checked or len(checked) == len(self.id_map):
            return None
        return checked


# -------- EventIdsValueWidget --------

class EventIdsValueWidget(QWidget):
    """Hidden value widget that stores the event_ids selection (dict or None)."""

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


# -------- Factories --------

def read_param_widget(widget) -> object:
    """Read the current value from a param widget using duck typing."""
    if widget is None:
        return None
    if hasattr(widget, "get_value") and callable(widget.get_value):
        return widget.get_value()
    if hasattr(widget, "currentText"):
        return widget.currentText()
    if hasattr(widget, "value"):
        return widget.value()
    if hasattr(widget, "isChecked"):
        return widget.isChecked()
    if hasattr(widget, "text"):
        return widget.text()
    return None


def event_ids_factory(current_value, raw, parent):
    """Param widget factory for the 'event_ids' param type.

    Returns a (container, EventIdsValueWidget) pair where:
    - container has a summary label + Pick button that opens EventPickerDialog.
    - value_widget is a hidden EventIdsValueWidget storing the dict.

    The dialog reads the current event_source, stim_channel, etc. from the parent
    ActionEditor's param_widgets at the time the user clicks Pick.
    """
    value_widget = EventIdsValueWidget(current_value)

    def make_summary() -> str:
        v = value_widget.get_value()
        if not v:
            return "All events"
        return f"{len(v)} event(s) selected"

    summary_label = QLabel(make_summary())
    btn = QPushButton("Pick…")
    btn.setEnabled(raw is not None)

    def open_picker():
        source = "annotations"
        stim_channel = None
        min_duration = 0.0
        shortest_event = 1
        events_file = ""

        if parent is not None:
            pw = getattr(parent, "param_widgets", {})
            source = str(read_param_widget(pw.get("event_source")) or "annotations")
            stim_channel = str(read_param_widget(pw.get("stim_channel")) or "") or None
            min_duration = float(read_param_widget(pw.get("min_duration")) or 0.0)
            shortest_event = int(read_param_widget(pw.get("shortest_event")) or 1)
            events_file = str(read_param_widget(pw.get("events_file")) or "")

        dlg = EventPickerDialog(
            raw=raw,
            source=source,
            stim_channel=stim_channel,
            min_duration=min_duration,
            shortest_event=shortest_event,
            events_file=events_file,
            current_value=value_widget.get_value(),
            parent=parent,
        )
        if dlg.exec() == QDialog.DialogCode.Accepted:
            value_widget.set_value(dlg.get_value())
            summary_label.setText(make_summary())

    btn.clicked.connect(open_picker)

    container = QWidget()
    layout = QHBoxLayout(container)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.addWidget(summary_label, 1)
    layout.addWidget(btn)

    return container, value_widget


def stim_channel_factory(current_value, raw, parent):
    """Param widget factory for the 'stim_channel' param type.

    Returns a QComboBox populated with channels from raw, STI-prefixed channels
    listed first. The combo is editable to allow typing custom channel names.
    """
    combo = QComboBox()
    combo.setEditable(True)

    if raw is not None:
        sti = [c for c in raw.ch_names if c.upper().startswith("STI")]
        other = [c for c in raw.ch_names if not c.upper().startswith("STI")]
        combo.addItems(sti + other)

    if combo.count() == 0:
        combo.addItem("STI 014")

    saved = str(current_value) if current_value else ""
    if saved:
        idx = combo.findText(saved)
        if idx >= 0:
            combo.setCurrentIndex(idx)
        else:
            combo.setEditText(saved)

    return combo, combo


def events_file_factory(current_value, raw, parent):
    """Param widget factory for the 'events_file' param type.

    Returns a (container, QLineEdit) pair where the container has a text field
    and a "Browse…" button for selecting an events file.
    """
    line = QLineEdit(str(current_value) if current_value else "")
    line.setPlaceholderText("Path to events file...")
    btn = QPushButton("Browse…")

    def browse():
        path, _ = QFileDialog.getOpenFileName(
            parent, "Select events file", "", "Events files (*.tsv *.fif *.eve *.txt);;All files (*)"
        )
        if path:
            line.setText(path)

    btn.clicked.connect(browse)

    container = QWidget()
    layout = QHBoxLayout(container)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.addWidget(line, 1)
    layout.addWidget(btn)

    return container, line


# -------- Widget bindings --------

WIDGET_BINDINGS = [
    ParamWidgetBinding("event_ids", event_ids_factory),
    ParamWidgetBinding("stim_channel", stim_channel_factory),
    ParamWidgetBinding("events_file", events_file_factory),
]
