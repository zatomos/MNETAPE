"""Dialog for displaying action execution results."""

from __future__ import annotations

from gi.repository import Adw, Gtk

from mnetape.core.models import ActionResult
from mnetape.gui.widgets.common import PlotCanvas

class ActionResultDialog:
    """Dialog showing an action's post-execution feedback.

    Displays an optional matplotlib figure, a summary line, and an optional details table.

    Args:
        result: The ActionResult to display.
        title: Action title shown in the dialog header.
        parent_window: The Adw.ApplicationWindow to present on.
    """

    def __init__(self, result: ActionResult, title: str, parent_window=None):
        self.parent_window = parent_window
        self.open = True

        self.dialog = Adw.Dialog()
        self.dialog.set_title(f"Results - {title}")
        self.dialog.set_content_width(640)
        self.dialog.set_content_height(560)

        toolbar_view = Adw.ToolbarView()
        header = Adw.HeaderBar()
        toolbar_view.add_top_bar(header)
        self.dialog.set_child(toolbar_view)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_propagate_natural_height(True)
        scrolled.set_max_content_height(700)

        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        content_box.set_margin_start(16)
        content_box.set_margin_end(16)
        content_box.set_margin_top(12)
        content_box.set_margin_bottom(12)
        scrolled.set_child(content_box)
        toolbar_view.set_content(scrolled)

        # Figure
        if result.fig is not None:
            plot_canvas = PlotCanvas(result.fig)
            plot_canvas.set_size_request(-1, 320)
            content_box.append(plot_canvas)

        # Summary
        summary_label = Gtk.Label(label=result.summary)
        summary_label.set_wrap(True)
        summary_label.set_xalign(0.0)
        content_box.append(summary_label)

        # Details
        if result.details:
            details_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            for key, value in result.details.items():
                row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
                key_lbl = Gtk.Label(label=f"{key}:")
                key_lbl.set_size_request(180, -1)
                key_lbl.set_xalign(1.0)
                val_lbl = Gtk.Label(label=str(value))
                val_lbl.add_css_class("dim-label")
                val_lbl.set_xalign(0.0)
                val_lbl.set_hexpand(True)
                row.append(key_lbl)
                row.append(val_lbl)
                details_box.append(row)
            content_box.append(details_box)

        # Spacer
        spacer = Gtk.Box()
        spacer.set_vexpand(True)
        content_box.append(spacer)

        # Close button
        btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        btn_row.set_halign(Gtk.Align.END)
        close_btn = Gtk.Button(label="Close")
        close_btn.set_size_request(80, -1)
        close_btn.connect("clicked", self.on_close)
        btn_row.append(close_btn)
        content_box.append(btn_row)

        self.dialog.connect("closed", self.on_dialog_closed)

        # Close on Escape
        key_ctrl = Gtk.EventControllerKey()
        key_ctrl.connect(
            "key-pressed",
            lambda ctrl, keyval, code, state: (self.on_close(None) or True)
            if keyval == 0xFF1B else False,
        )
        self.dialog.add_controller(key_ctrl)

    def on_close(self, _btn):
        self.dialog.close()

    def on_dialog_closed(self, _dialog):
        self.open = False

    def show(self):
        """Present the dialog non-modally."""
        if self.parent_window is not None:
            self.dialog.present(self.parent_window)
        else:
            self.dialog.present(None)

    def raise_(self):
        pass

    def close(self):
        """Close the dialog programmatically."""
        if self.open:
            self.dialog.close()
