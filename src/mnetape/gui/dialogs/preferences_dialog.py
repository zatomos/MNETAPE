"""Preferences dialog for persistent user settings."""

from __future__ import annotations

from typing import TYPE_CHECKING

from gi.repository import Adw, GLib, Gtk

if TYPE_CHECKING:
    from mnetape.gui.controllers.state import AppState

class PreferencesDialog:
    """Preferences window using Adw.PreferencesWindow.

    Args:
        state: Shared application state.
        parent_window: Optional parent Adw.ApplicationWindow.
    """

    def __init__(self, state: AppState, parent_window=None):
        self.state = state
        self.parent_window = parent_window
        self.loop = GLib.MainLoop()

        win = Adw.PreferencesWindow()
        win.set_title("Preferences")
        win.set_default_size(480, -1)
        self.win = win

        page = Adw.PreferencesPage()
        page.set_title("General")
        page.set_icon_name("preferences-other-symbolic")
        win.add(page)

        group = Adw.PreferencesGroup()
        group.set_title("Data Store")
        group.set_description(
            "Control how many pipeline checkpoints are kept in memory and on disk."
        )
        page.add(group)

        # Cache size row
        self.cache_row = Adw.SpinRow()
        self.cache_row.set_title("Max checkpoints in RAM")
        self.cache_row.set_subtitle(
            "Higher values speed up step navigation at the cost of memory. "
            "Reduce if you run out of RAM with large files."
        )
        adj_cache = Gtk.Adjustment(
            value=state.data_states.cache_size,
            lower=1,
            upper=20,
            step_increment=1,
            page_increment=5,
        )
        self.cache_row.set_adjustment(adj_cache)
        group.add(self.cache_row)

        # Max disk states row
        self.disk_row = Adw.SpinRow()
        self.disk_row.set_title("Max checkpoints on disk")
        self.disk_row.set_subtitle(
            "Older checkpoints are removed first when the limit is exceeded. "
            "0 = unlimited. Reduce to save disk space."
        )
        adj_disk = Gtk.Adjustment(
            value=state.data_states.max_disk_states,
            lower=0,
            upper=99,
            step_increment=1,
            page_increment=10,
        )
        self.disk_row.set_adjustment(adj_disk)
        group.add(self.disk_row)

        win.connect("close-request", self.on_close_request)

        # Add a "Save" button to the window header bar
        save_btn = Gtk.Button(label="Save")
        save_btn.add_css_class("suggested-action")
        save_btn.connect("clicked", self.on_save)
        win.add_toast_action_button = None  # placeholder attribute

        # Access the header bar
        shortcut_ctrl = Gtk.ShortcutController()
        shortcut_ctrl.set_scope(Gtk.ShortcutScope.GLOBAL)
        shortcut_ctrl.add_shortcut(
            Gtk.Shortcut(
                trigger=Gtk.KeyvalTrigger.new(
                    ord("s") if False else 0,  # placeholder; actual binding below
                    0,
                ),
                action=Gtk.CallbackAction.new(lambda *_: self.on_save(None) or True),
            )
        )

    def on_save(self, _btn):
        cache_size = int(self.cache_row.get_value())
        self.state.data_states.cache_size = cache_size
        self.state.settings.set_value("data_store/cache_size", cache_size)

        max_states = int(self.disk_row.get_value())
        self.state.data_states.max_disk_states = max_states
        self.state.settings.set_value("data_store/max_disk_states", max_states)
        self.win.close()

    def on_close_request(self, _win):
        self.loop.quit()
        return False  # Allow close

    def exec(self):
        """Present the dialog modally and block until closed."""
        if self.parent_window is not None:
            self.win.set_transient_for(self.parent_window)
            self.win.set_modal(True)
        self.win.present()
        self.loop.run()
