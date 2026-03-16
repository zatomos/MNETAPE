"""Set channel types action widgets.

Provides a table-based dialog that lets users change the channel type for any channel.
"""

from __future__ import annotations

import json
import logging

import mne


from gi.repository import Adw, Gtk

from mnetape.actions.base import ParamWidgetBinding
from mnetape.gui.dialogs.base import ModalDialog

logger = logging.getLogger(__name__)

# Accepted channel types, fetched from MNE at import.
try:
    from mne._fiff.pick import get_channel_type_constants

    VALID_CHANNEL_TYPES: tuple[str, ...] = tuple(sorted(get_channel_type_constants()))
except Exception as e:
    logger.warning(f"Could not fetch valid channel types from MNE: {e}")

    VALID_CHANNEL_TYPES = (
        "bio", "chpi", "csd", "dbs", "dipole", "ecg", "ecog", "eeg", "emg",
        "eog", "exci", "eyegaze", "fnirs_cw_amplitude", "fnirs_fd_ac_amplitude",
        "fnirs_fd_phase", "fnirs_od", "gof", "grad", "gsr", "hbo", "hbr",
        "ias", "mag", "misc", "pupil", "ref_meg", "resp", "seeg", "stim",
        "syst", "temperature",
    )

class ChannelTypeDialog(ModalDialog):
    """Dialog for setting channel types via a filterable table with per-row dropdowns.

    Displays all channels with their current type and a dropdown to select a new type.
    Only channels whose type was changed are included in the returned mapping.
    The table can be filtered by channel name or current type.
    """

    def __init__(
        self,
        raw: mne.io.Raw,
        current_mapping: dict[str, str] | None = None,
        parent_window=None,
    ):
        self.raw = raw
        self.current_mapping = dict(current_mapping or {})

        # Per-channel row data: {ch_name: (orig_type, dropdown)}
        self.rows: dict[str, tuple[str, Gtk.DropDown]] = {}
        self.all_row_widgets: list[tuple[str, str, Gtk.ListBoxRow]] = []  # (ch_name, orig_type, row)

        self.dialog = Adw.Dialog()
        self.dialog.set_title("Set Channel Types")
        self.dialog.set_content_width(620)

        toolbar_view = Adw.ToolbarView()
        toolbar_view.add_top_bar(Adw.HeaderBar())
        self.dialog.set_child(toolbar_view)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        outer.set_margin_start(12)
        outer.set_margin_end(12)
        outer.set_margin_top(8)
        outer.set_margin_bottom(8)
        toolbar_view.set_content(outer)

        info = Gtk.Label(
            label=(
                "Change the type for any channel using the dropdown. "
                "Only channels with a changed type will be included in the mapping."
            )
        )
        info.set_wrap(True)
        info.set_xalign(0.0)
        outer.append(info)

        # Filter row
        filter_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        filter_box.append(Gtk.Label(label="Filter:"))
        self.filter_entry = Gtk.SearchEntry()
        self.filter_entry.set_placeholder_text("Type to filter channels...")
        self.filter_entry.set_hexpand(True)
        self.filter_entry.connect("search-changed", self.apply_filter)
        filter_box.append(self.filter_entry)

        # Type filter dropdown
        present_types = sorted(set(
            mne.channel_type(raw.info, i) for i in range(len(raw.ch_names))
        ))
        type_items = ["All types"] + present_types
        type_model = Gtk.StringList(strings=type_items)
        self.type_dropdown = Gtk.DropDown(model=type_model)
        self.type_dropdown.connect("notify::selected", self.apply_filter)
        filter_box.append(self.type_dropdown)
        outer.append(filter_box)

        # Column headers
        header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        header_box.set_margin_start(6)
        header_box.set_margin_end(6)
        ch_hdr = Gtk.Label(label="Channel")
        ch_hdr.set_xalign(0.0)
        ch_hdr.set_hexpand(True)
        cur_hdr = Gtk.Label(label="Current Type")
        cur_hdr.set_size_request(110, -1)
        cur_hdr.set_xalign(0.0)
        new_hdr = Gtk.Label(label="New Type")
        new_hdr.set_size_request(160, -1)
        new_hdr.set_xalign(0.0)
        header_box.append(ch_hdr)
        header_box.append(cur_hdr)
        header_box.append(new_hdr)
        outer.append(header_box)

        # Scrollable list
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        scrolled.set_min_content_height(300)
        self.list_box = Gtk.ListBox()
        self.list_box.set_selection_mode(Gtk.SelectionMode.NONE)
        scrolled.set_child(self.list_box)
        outer.append(scrolled)

        self.populate_rows()

        # Summary + footer buttons
        self.summary_label = Gtk.Label(label="No changes.")
        self.summary_label.set_xalign(0.0)
        self.summary_label.add_css_class("dim-label")
        self.summary_label.set_hexpand(True)

        footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        footer.set_halign(Gtk.Align.FILL)
        footer.append(self.summary_label)

        btn_reset = Gtk.Button(label="Reset All")
        btn_reset.connect("clicked", self.reset_all)
        footer.append(btn_reset)

        cancel_btn = Gtk.Button(label="Cancel")
        cancel_btn.connect("clicked", self.reject)
        footer.append(cancel_btn)

        ok_btn = Gtk.Button(label="OK")
        ok_btn.add_css_class("suggested-action")
        ok_btn.connect("clicked", self.accept)
        footer.append(ok_btn)

        outer.append(footer)

        self.setup_modal(parent_window)
        self.update_summary()

    def populate_rows(self) -> None:
        """Populate the list with one row per channel."""
        ch_names = self.raw.ch_names
        original_types = [mne.channel_type(self.raw.info, i) for i in range(len(ch_names))]
        type_list = list(VALID_CHANNEL_TYPES)

        self.all_row_widgets.clear()
        self.rows.clear()

        for ch_name, orig_type in zip(ch_names, original_types):
            row = Gtk.ListBoxRow()

            row_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
            row_box.set_margin_start(6)
            row_box.set_margin_end(6)
            row_box.set_margin_top(3)
            row_box.set_margin_bottom(3)

            ch_label = Gtk.Label(label=ch_name)
            ch_label.set_xalign(0.0)
            ch_label.set_hexpand(True)
            row_box.append(ch_label)

            cur_label = Gtk.Label(label=orig_type)
            cur_label.set_size_request(110, -1)
            cur_label.set_xalign(0.0)
            row_box.append(cur_label)

            # Dropdown for new type
            target = self.current_mapping.get(ch_name, orig_type)
            items_for_row = list(type_list)
            if target not in items_for_row:
                items_for_row.insert(0, target)

            model = Gtk.StringList(strings=items_for_row)
            dropdown = Gtk.DropDown(model=model)
            dropdown.set_size_request(160, -1)
            try:
                idx = items_for_row.index(target)
                dropdown.set_selected(idx)
            except ValueError:
                dropdown.set_selected(0)
            dropdown.connect("notify::selected", lambda *_: self.update_summary())
            row_box.append(dropdown)

            row.set_child(row_box)
            # Store metadata on row for filter
            row._ch_name = ch_name
            row._orig_type = orig_type
            self.list_box.append(row)
            self.rows[ch_name] = (orig_type, dropdown, items_for_row)
            self.all_row_widgets.append((ch_name, orig_type, row))

    def apply_filter(self, *_args) -> None:
        """Show/hide rows based on text filter and type filter."""
        text = self.filter_entry.get_text().lower()
        sel_idx = self.type_dropdown.get_selected()
        type_items = ["All types"] + sorted(set(
            mne.channel_type(self.raw.info, i) for i in range(len(self.raw.ch_names))
        ))
        type_filter = type_items[sel_idx] if sel_idx < len(type_items) else "All types"

        for ch_name, orig_type, row in self.all_row_widgets:
            name_match = not text or text in ch_name.lower()
            type_match = type_filter == "All types" or orig_type == type_filter
            row.set_visible(name_match and type_match)

    def update_summary(self) -> None:
        mapping = self.get_mapping()
        n = len(mapping)
        if n == 0:
            self.summary_label.set_text("No changes.")
        else:
            self.summary_label.set_text(f"{n} channel{'s' if n != 1 else ''} will be re-typed.")

    def reset_all(self, _btn) -> None:
        """Reset all dropdowns to the channel's original type."""
        for ch_name, orig_type, items_for_row, dropdown in self.iter_rows():
            try:
                idx = items_for_row.index(orig_type)
                dropdown.set_selected(idx)
            except ValueError:
                pass
        self.update_summary()

    def iter_rows(self):
        for ch_name, (orig_type, dropdown, items_for_row) in self.rows.items():
            yield ch_name, orig_type, items_for_row, dropdown

    def get_mapping(self) -> dict[str, str]:
        """Return a dict of channels whose type was changed from their original value."""
        mapping: dict[str, str] = {}
        for ch_name, (orig_type, dropdown, items_for_row) in self.rows.items():
            sel = dropdown.get_selected()
            new_type = items_for_row[sel] if sel < len(items_for_row) else orig_type
            if new_type != orig_type:
                mapping[ch_name] = new_type
        return mapping

    def get_mapping_string(self) -> str:
        """Return the mapping as a JSON string for the param field."""
        mapping = self.get_mapping()
        if not mapping:
            return ""
        return json.dumps(mapping)

# -------- Param widget factory --------

def channel_types_widget_factory(current_value, raw):
    """Build a compound widget for the 'channel_types' param type.

    Returns a (container, value_widget) pair:
        - container has a read-only Entry and an "Edit..." button.
        - value_widget is the Gtk.Entry holding the JSON string.
    """
    container = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
    container.set_hexpand(True)

    entry = Gtk.Entry()
    entry.set_text(str(current_value or ""))
    entry.set_editable(False)
    entry.set_placeholder_text("Click 'Edit...' to set channel types")
    entry.set_hexpand(True)
    container.append(entry)

    btn_edit = Gtk.Button(label="Edit...")
    btn_edit.set_sensitive(raw is not None)

    def _edit(_btn):
        if raw is None:
            return
        current_text = entry.get_text().strip()
        current_mapping: dict[str, str] = {}
        if current_text:
            try:
                parsed = json.loads(current_text)
                if isinstance(parsed, dict):
                    current_mapping = parsed
            except (json.JSONDecodeError, ValueError):
                for pair in current_text.split(","):
                    pair = pair.strip()
                    if ":" in pair:
                        ch, typ = pair.split(":", 1)
                        current_mapping[ch.strip()] = typ.strip()
        parent_window = btn_edit.get_root()
        dlg = ChannelTypeDialog(raw, current_mapping, parent_window=parent_window)
        if dlg.exec():
            entry.set_text(dlg.get_mapping_string())

    btn_edit.connect("clicked", _edit)
    container.append(btn_edit)

    return container, entry

# -------- Widget bindings --------

WIDGET_BINDINGS = [
    ParamWidgetBinding("channel_mapping", channel_types_widget_factory),
]
