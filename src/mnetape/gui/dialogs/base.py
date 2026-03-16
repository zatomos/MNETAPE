"""Shared base class for all Adw.Dialog-based modal dialogs."""

from __future__ import annotations

from typing import Any

from gi.repository import GLib, Gtk


class ModalDialog:
    """Mixin providing standardized accept/reject/exec for Adw.Dialog-based modals.

    Subclasses must:
    1. Create ``self.dialog = Adw.Dialog(...)`` before calling ``setup_modal``.
    2. Call ``self.setup_modal(parent_window)`` after building the dialog content.
    3. Connect accept buttons to ``self.accept`` and cancel buttons to ``self.reject``.
    """

    dialog: Any  # Adw.Dialog — assigned by subclass before setup_modal is called

    def setup_modal(self, parent_window=None) -> None:
        self.parent_window = parent_window
        self.result = "rejected"
        self.loop = GLib.MainLoop()
        self.dialog.connect("closed", self.on_closed)
        key_ctrl = Gtk.EventControllerKey()
        key_ctrl.connect("key-pressed", lambda _, kv, *__: (self.reject() or True) if kv == 0xFF1B else False)
        self.dialog.add_controller(key_ctrl)

    def accept(self, *_) -> None:
        self.result = "accepted"
        self.dialog.close()

    def reject(self, *_) -> None:
        self.dialog.close()

    def on_closed(self, *_) -> None:
        self.loop.quit()

    def exec(self) -> bool:
        self.dialog.present(self.parent_window)
        self.loop.run()
        return self.result == "accepted"
