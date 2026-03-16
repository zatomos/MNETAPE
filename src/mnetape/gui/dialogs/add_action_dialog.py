"""Dialog for selecting and adding a new pipeline action.

Lists all registered actions sorted alphabetically.
The action description is shown below the list as the selection changes.
The chosen action_id is returned after the dialog is accepted.
"""

from __future__ import annotations

from gi.repository import Adw, Gtk

from mnetape.actions.registry import get_action_by_id, list_actions
from mnetape.core.models import CUSTOM_ACTION_ID
from mnetape.gui.dialogs.base import ModalDialog

class AddActionDialog(ModalDialog):
    """Modal dialog for picking an action type to add to the pipeline.

    Double-clicking an item or pressing OK accepts the dialog.
    """

    def __init__(self, parent_window=None):
        self.selected_action_id: str | None = None

        # Build sorted action list (id, title) excluding CUSTOM_ACTION_ID
        self.actions = [
            (a.action_id, a.title)
            for a in sorted(list_actions(), key=lambda a: a.title.lower())
            if a.action_id != CUSTOM_ACTION_ID
        ]

        self.dialog = Adw.Dialog()
        self.dialog.set_title("Add Action")
        self.dialog.set_content_width(360)

        toolbar_view = Adw.ToolbarView()
        header = Adw.HeaderBar()
        toolbar_view.add_top_bar(header)
        self.dialog.set_child(toolbar_view)

        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        content_box.set_margin_start(16)
        content_box.set_margin_end(16)
        content_box.set_margin_top(12)
        content_box.set_margin_bottom(12)
        toolbar_view.set_content(content_box)

        content_box.append(Gtk.Label(label="Select action type:"))

        # Scrolled list
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_min_content_height(200)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        self.list_box = Gtk.ListBox()
        self.list_box.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.list_box.set_activate_on_single_click(False)
        self.list_box.connect("row-activated", self.on_row_activated)
        self.list_box.connect("row-selected", self.on_row_selected)

        for action_id, title in self.actions:
            row = Gtk.ListBoxRow()
            lbl = Gtk.Label(label=title)
            lbl.set_xalign(0.0)
            lbl.set_margin_start(8)
            lbl.set_margin_end(8)
            lbl.set_margin_top(4)
            lbl.set_margin_bottom(4)
            row.set_child(lbl)
            setattr(row, "action_id", action_id)
            self.list_box.append(row)

        scrolled.set_child(self.list_box)
        content_box.append(scrolled)

        # Description label
        self.desc_label = Gtk.Label(label="")
        self.desc_label.set_wrap(True)
        self.desc_label.add_css_class("dim-label")
        self.desc_label.set_xalign(0.0)
        self.desc_label.set_size_request(-1, 48)
        content_box.append(self.desc_label)

        # Select first row (fires row-selected → _update_description immediately)
        first_row = self.list_box.get_row_at_index(0)
        if first_row is not None:
            self.selected_action_id = self.actions[0][0] if self.actions else None
            self.list_box.select_row(first_row)

        # Buttons
        btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        btn_row.set_halign(Gtk.Align.END)

        cancel_btn = Gtk.Button(label="Cancel")
        cancel_btn.connect("clicked", self.reject)
        btn_row.append(cancel_btn)

        ok_btn = Gtk.Button(label="OK")
        ok_btn.add_css_class("suggested-action")
        ok_btn.connect("clicked", self.accept)
        btn_row.append(ok_btn)

        content_box.append(btn_row)

        self.setup_modal(parent_window)

    def on_row_selected(self, _list_box, row):
        if row is not None:
            self.selected_action_id = getattr(row, "action_id", None)
            self.update_description()

    def on_row_activated(self, _list_box, _row):
        self.accept()

    def update_description(self):
        if self.selected_action_id:
            action_def = get_action_by_id(self.selected_action_id)
            self.desc_label.set_text(action_def.doc if action_def else "")
        else:
            self.desc_label.set_text("")

    def get_action_id(self) -> str | None:
        """Return the action_id of the selected action, or None."""
        return self.selected_action_id
