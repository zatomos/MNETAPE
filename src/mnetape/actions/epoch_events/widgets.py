"""Event-based epoching action widgets."""

from __future__ import annotations

import logging

import mne
import pandas as _pd


from gi.repository import Adw, Gtk

from mnetape.actions.base import ParamWidgetBinding
from mnetape.gui.dialogs.base import ModalDialog

logger = logging.getLogger(__name__)

# -------- EventPickerDialog --------

class EventPickerDialog(ModalDialog):
    """Dialog for selecting which event IDs to include.

    Discovers available events from the recording using the specified source
    and source parameters, then presents a checklist for the user to choose from.
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
        parent_window=None,
    ):
        self.id_map: dict[str, int] = {}
        self.checkboxes: dict[str, Gtk.CheckButton] = {}

        self.dialog = Adw.Dialog()
        self.dialog.set_title("Pick Events")
        self.dialog.set_content_width(340)

        toolbar_view = Adw.ToolbarView()
        toolbar_view.add_top_bar(Adw.HeaderBar())
        self.dialog.set_child(toolbar_view)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        content.set_margin_start(16)
        content.set_margin_end(16)
        content.set_margin_top(12)
        content.set_margin_bottom(12)
        toolbar_view.set_content(content)

        id_map, error = self.discover(raw, source, stim_channel, min_duration, shortest_event, events_file)
        self.id_map = id_map

        if error:
            content.append(Gtk.Label(label=f"Could not discover events:\n{error}"))
        elif not id_map:
            content.append(Gtk.Label(label="No events found."))
        else:
            content.append(Gtk.Label(label=f"{len(id_map)} event type(s) found:"))

            scrolled = Gtk.ScrolledWindow()
            scrolled.set_max_content_height(200)
            scrolled.set_propagate_natural_height(True)
            inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            inner.set_margin_start(4)
            inner.set_margin_end(4)
            inner.set_margin_top(4)
            inner.set_margin_bottom(4)

            selected = set(current_value.keys()) if isinstance(current_value, dict) else set()
            check_all = not selected

            for name, code in sorted(id_map.items()):
                cb = Gtk.CheckButton(label=f"{name}  (code {code})")
                cb.set_active(check_all or name in selected)
                inner.append(cb)
                self.checkboxes[name] = cb

            scrolled.set_child(inner)
            content.append(scrolled)

        btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        btn_row.set_halign(Gtk.Align.END)
        cancel_btn = Gtk.Button(label="Cancel")
        cancel_btn.connect("clicked", self.reject)
        btn_row.append(cancel_btn)
        ok_btn = Gtk.Button(label="OK")
        ok_btn.add_css_class("suggested-action")
        ok_btn.connect("clicked", self.accept)
        btn_row.append(ok_btn)
        content.append(btn_row)

        self.setup_modal(parent_window)

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
                events = mne.find_events(
                    raw, stim_channel=ch,
                    min_duration=min_duration,
                    shortest_event=shortest_event,
                    verbose=False,
                )
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
        checked = {name: self.id_map[name] for name, cb in self.checkboxes.items() if cb.get_active()}
        if not checked or len(checked) == len(self.id_map):
            return None
        return checked

# -------- EventIdsValueWidget --------

class EventIdsValueWidget(Gtk.Box):
    """Hidden value widget that stores the event_ids selection (dict or None)."""

    def __init__(self, value: dict | None):
        super().__init__()
        self.set_visible(False)
        self.value = value
        self.changed_cbs: list = []

    def set_value(self, v: dict | None):
        self.value = v
        for cb in self.changed_cbs:
            cb()

    def get_value(self) -> dict | None:
        return self.value

    def connect_value_changed(self, cb):
        self.changed_cbs.append(cb)

# -------- Factories --------

def read_widget_value(widget) -> object:
    """Read the current value from a param widget using duck typing."""
    if widget is None:
        return None
    if hasattr(widget, "get_value") and callable(widget.get_value):
        return widget.get_value()
    if hasattr(widget, "get_text") and callable(widget.get_text):
        return widget.get_text()
    if hasattr(widget, "get_active") and callable(widget.get_active):
        return widget.get_active()
    return None

def event_ids_factory(current_value, raw, param_widgets=None):
    """Param widget factory for the 'event_ids' param type."""
    value_widget = EventIdsValueWidget(current_value)

    def make_summary() -> str:
        v = value_widget.get_value()
        if not v:
            return "All events"
        return f"{len(v)} event(s) selected"

    summary_label = Gtk.Label(label=make_summary())
    summary_label.set_xalign(0.0)
    summary_label.set_hexpand(True)
    btn = Gtk.Button(label="Pick\u2026")

    def open_picker(_btn):
        source = "annotations"
        stim_channel = None
        min_duration = 0.0
        shortest_event = 1
        events_file = ""

        if param_widgets is not None:
            pw = param_widgets
            source = str(read_widget_value(pw.get("event_source")) or "annotations")
            stim_channel = str(read_widget_value(pw.get("stim_channel")) or "") or None
            min_duration = float(read_widget_value(pw.get("min_duration")) or 0.0)
            shortest_event = int(read_widget_value(pw.get("shortest_event")) or 1)
            events_file = str(read_widget_value(pw.get("events_file")) or "")

        parent_window = btn.get_root()
        dlg = EventPickerDialog(
            raw=raw,
            source=source,
            stim_channel=stim_channel,
            min_duration=min_duration,
            shortest_event=shortest_event,
            events_file=events_file,
            current_value=value_widget.get_value(),
            parent_window=parent_window,
        )
        if dlg.exec():
            value_widget.set_value(dlg.get_value())
            summary_label.set_text(make_summary())

    btn.connect("clicked", open_picker)

    container = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
    container.set_hexpand(True)
    container.append(summary_label)
    container.append(btn)

    return container, value_widget

def stim_channel_factory(current_value, raw):
    """Param widget factory for the 'stim_channel' param type.

    Returns an editable Gtk.Entry pre-populated with detected STI channels.
    """
    # Build items: STI channels first, then others
    items: list[str] = []
    if raw is not None:
        sti = [c for c in raw.ch_names if c.upper().startswith("STI")]
        other = [c for c in raw.ch_names if not c.upper().startswith("STI")]
        items = sti + other

    if not items:
        items = ["STI 014"]

    # Use an editable Entry
    entry = Gtk.Entry()
    entry.set_hexpand(True)

    saved = str(current_value) if current_value else ""
    if saved:
        entry.set_text(saved)
    elif items:
        entry.set_text(items[0])

    # Attach an EntryCompletion for convenience
    completion = Gtk.EntryCompletion()
    model = Gtk.StringList(strings=items)
    completion.set_model(model)
    completion.set_text_column(0)
    entry.set_completion(completion)

    return entry, entry

def events_file_factory(current_value, raw):
    """Param widget factory for the 'events_file' param type."""
    entry = Gtk.Entry()
    entry.set_text(str(current_value) if current_value else "")
    entry.set_placeholder_text("Path to events file...")
    entry.set_hexpand(True)

    btn = Gtk.Button(label="Browse\u2026")

    def on_open_done(dialog, result):
        try:
            gfile = dialog.open_finish(result)
            if gfile is not None:
                entry.set_text(gfile.get_path())
        except Exception:
            pass

    def browse(_btn):
        parent_window = btn.get_root()
        file_dialog = Gtk.FileDialog()
        file_dialog.set_title("Select events file")
        file_dialog.open(parent_window, None, on_open_done)

    btn.connect("clicked", browse)

    container = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
    container.set_hexpand(True)
    container.append(entry)
    container.append(btn)

    return container, entry

# -------- Widget bindings --------

WIDGET_BINDINGS = [
    ParamWidgetBinding("event_ids", event_ids_factory),
    ParamWidgetBinding("stim_channel", stim_channel_factory),
    ParamWidgetBinding("events_file", events_file_factory),
]
