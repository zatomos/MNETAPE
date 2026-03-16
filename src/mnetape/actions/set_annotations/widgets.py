"""Set annotations action widgets.

Provides a lightweight annotation dialog with:
- a read-only list of current annotations
- an embedded MNE browser used to add/edit/remove annotations

The list is refreshed from browser changes via a low-frequency poll.
"""

from __future__ import annotations

import logging

import mne


from gi.repository import Adw, GLib, Gtk

from mnetape.actions.base import ParamWidgetBinding
from mnetape.gui.dialogs.base import ModalDialog
from mnetape.gui.widgets.common import (
    disable_mne_browser_channel_clicks,
    embed_mne_browser,
    sanitize_mne_browser_toolbar,
)

logger = logging.getLogger(__name__)

class AnnotationsValueWidget(Gtk.Box):
    """Hidden value widget that stores the annotations list."""

    def __init__(self, annotations: list[dict]):
        super().__init__()
        self.set_visible(False)
        self.annotations: list[dict] = list(annotations) if annotations else []
        self.changed_cbs: list = []

    def set_value(self, annotations: list[dict]):
        self.annotations = list(annotations)
        for cb in self.changed_cbs:
            cb()

    def get_value(self) -> list[dict]:
        return self.annotations

    def connect_value_changed(self, cb):
        self.changed_cbs.append(cb)

class AnnotationEditorDialog(ModalDialog):
    """Dialog for managing annotations through the MNE browser.

    Left panel shows a read-only list of annotations.
    Right panel hosts the MNE browser where annotations are edited.
    """

    def __init__(self, raw, annotations: list[dict], parent_window=None):
        self.raw = raw
        self.seed_annotations = list(annotations) if annotations else []
        self.raw_copy: mne.io.Raw | None = raw.copy() if raw is not None else None

        self.browser = None
        self.poll_source: int | None = None
        self.last_ann_hash: int | None = None

        self.dialog = Adw.Dialog()
        self.dialog.set_title("Edit Annotations")
        self.dialog.set_content_width(1000)

        toolbar_view = Adw.ToolbarView()
        toolbar_view.add_top_bar(Adw.HeaderBar())
        self.dialog.set_child(toolbar_view)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        outer.set_margin_start(12)
        outer.set_margin_end(12)
        outer.set_margin_top(8)
        outer.set_margin_bottom(8)
        toolbar_view.set_content(outer)

        # Horizontal paned (left: list, right: browser)
        paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        paned.set_vexpand(True)
        paned.set_position(350)
        outer.append(paned)

        # Left panel
        left_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        left_box.set_margin_start(4)
        left_box.set_margin_end(4)
        left_box.set_margin_top(4)
        left_box.set_margin_bottom(4)

        hint = Gtk.Label(label="Use the browser to add/edit annotations.\nThis list is read-only.")
        hint.set_wrap(True)
        hint.add_css_class("dim-label")
        left_box.append(hint)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        scrolled.set_hexpand(True)
        self.list_box = Gtk.ListBox()
        self.list_box.set_selection_mode(Gtk.SelectionMode.NONE)
        scrolled.set_child(self.list_box)
        left_box.append(scrolled)
        paned.set_start_child(left_box)

        # Right panel: browser container
        self.browser_container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.browser_container.set_hexpand(True)
        self.browser_container.set_vexpand(True)
        paned.set_end_child(self.browser_container)

        self.initialize_annotations()
        self.refresh_list_from_annotations()
        self.last_ann_hash = self.ann_hash()

        if self.raw_copy is not None:
            try:
                self.browser = self.raw_copy.plot(show=False)
                sanitize_mne_browser_toolbar(self.browser, allow_annotation_mode=True)
                disable_mne_browser_channel_clicks(self.browser)
                embed_mne_browser(self.browser, self.browser_container)
            except Exception as e:
                logger.warning("Could not embed MNE browser in annotation editor: %s", e)
                lbl = Gtk.Label(label=f"Browser unavailable:\n{e}")
                lbl.set_wrap(True)
                self.browser_container.append(lbl)
        else:
            lbl = Gtk.Label(label="Load data to view and edit annotations")
            lbl.add_css_class("dim-label")
            self.browser_container.append(lbl)

        # Button row
        btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        btn_row.set_halign(Gtk.Align.END)
        cancel_btn = Gtk.Button(label="Cancel")
        cancel_btn.connect("clicked", self.reject)
        ok_btn = Gtk.Button(label="OK")
        ok_btn.add_css_class("suggested-action")
        ok_btn.connect("clicked", self.accept)
        btn_row.append(cancel_btn)
        btn_row.append(ok_btn)
        outer.append(btn_row)

        self.setup_modal(parent_window)

        if self.browser is not None:
            self.poll_source = GLib.timeout_add(250, self.poll_browser)

    def initialize_annotations(self):
        """Seed raw_copy annotations from param value or existing raw annotations."""
        if self.raw_copy is None:
            return

        if self.seed_annotations:
            anns = self.seed_annotations
        elif len(self.raw.annotations) > 0:
            anns = [
                {
                    "onset": float(onset),
                    "duration": float(duration),
                    "description": str(description),
                }
                for onset, duration, description in zip(
                    self.raw.annotations.onset,
                    self.raw.annotations.duration,
                    self.raw.annotations.description,
                )
            ]
        else:
            anns = []

        self.raw_copy.set_annotations(
            mne.Annotations(
                onset=[a["onset"] for a in anns],
                duration=[a["duration"] for a in anns],
                description=[a["description"] for a in anns],
            )
        )

    def ann_hash(self) -> int | None:
        if self.raw_copy is None:
            return None
        ann = self.raw_copy.annotations
        return hash((tuple(ann.onset), tuple(ann.duration), tuple(ann.description)))

    def refresh_list_from_annotations(self):
        """Render current annotations into the read-only list."""
        # Remove all existing rows
        while True:
            row = self.list_box.get_first_child()
            if row is None:
                break
            self.list_box.remove(row)

        if self.raw_copy is None:
            anns = self.seed_annotations
        else:
            ann = self.raw_copy.annotations
            anns = [
                {
                    "onset": float(onset),
                    "duration": float(duration),
                    "description": str(description),
                }
                for onset, duration, description in zip(
                    ann.onset,
                    ann.duration,
                    ann.description,
                )
            ]

        if not anns:
            row = Gtk.ListBoxRow()
            row.set_child(Gtk.Label(label="No annotations"))
            self.list_box.append(row)
            return

        for idx, a in enumerate(anns, start=1):
            row = Gtk.ListBoxRow()
            lbl = Gtk.Label(
                label=f"{idx}. {a['onset']:.3f}s | {a['duration']:.3f}s | {a['description']}"
            )
            lbl.set_xalign(0.0)
            lbl.set_margin_start(6)
            lbl.set_margin_end(6)
            row.set_child(lbl)
            self.list_box.append(row)

    def poll_browser(self) -> bool:
        """Refresh the list only when browser annotations changed. Returns True to keep polling."""
        if self.raw_copy is None or self.browser is None:
            return False
        h = self.ann_hash()
        if h != self.last_ann_hash:
            self.last_ann_hash = h
            self.refresh_list_from_annotations()
        return True  # keep the timeout alive

    def get_annotations(self) -> list[dict]:
        """Return current annotations from raw_copy (or seed list if no raw)."""
        if self.raw_copy is None:
            return list(self.seed_annotations)

        ann = self.raw_copy.annotations
        return [
            {
                "onset": float(onset),
                "duration": float(duration),
                "description": str(description),
            }
            for onset, duration, description in zip(
                ann.onset,
                ann.duration,
                ann.description,
            )
        ]

    def on_closed(self, *_) -> None:
        if self.poll_source is not None:
            GLib.source_remove(self.poll_source)
            self.poll_source = None
        self.loop.quit()

# -------- Param widget factory --------

def annotations_factory(current_value, raw):
    """Param widget factory for the annotations param type."""
    annotations = list(current_value) if current_value else []
    value_widget = AnnotationsValueWidget(annotations)

    def make_summary() -> str:
        n = len(value_widget.get_value())
        return f"{n} annotation{'s' if n != 1 else ''}" if n else "No annotations"

    summary_label = Gtk.Label(label=make_summary())
    summary_label.set_xalign(0.0)
    summary_label.set_hexpand(True)
    btn = Gtk.Button(label="Open Browser\u2026")

    def open_editor(_btn):
        parent_window = btn.get_root()
        dlg = AnnotationEditorDialog(
            raw=raw,
            annotations=value_widget.get_value(),
            parent_window=parent_window,
        )
        if dlg.exec():
            value_widget.set_value(dlg.get_annotations())
            summary_label.set_text(make_summary())

    btn.connect("clicked", open_editor)

    container = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
    container.set_hexpand(True)
    container.append(summary_label)
    container.append(btn)

    return container, value_widget

# -------- Widget bindings --------

WIDGET_BINDINGS = [
    ParamWidgetBinding("annotations", annotations_factory),
]
