"""Reusable non-dialog widgets for the EEG pipeline GUI.

Exports:
    PlotCanvas: Gtk.Box that hosts a matplotlib figure and its navigation toolbar.
    ActionListItem: Custom Gtk.Box row widget for the pipeline action list.
    disable_psd_span_popups: Helper to deactivate MNE PSD span selectors.
    sanitize_mne_browser_toolbar: Hide unsupported toolbar buttons in embedded MNE browsers.
    disable_mne_browser_channel_clicks: Disable channel-click interactions.
    embed_mne_browser: Detach an MNE browser canvas from its window and add it to a container.
"""

from __future__ import annotations

import logging
from typing import Callable

from gi.repository import Gtk
from matplotlib.backends.backend_gtk4agg import FigureCanvasGTK4Agg
from matplotlib.backends.backend_gtk4 import NavigationToolbar2GTK4
from matplotlib.figure import Figure

from mnetape.actions.registry import get_action_title
from mnetape.core.models import (
    CUSTOM_ACTION_ID,
    ActionConfig,
    ActionStatus,
    STATUS_ICONS,
    STATUS_COLORS,
)

logger = logging.getLogger(__name__)

# -------- MNE browser helpers --------

def disable_psd_span_popups(fig: Figure) -> None:
    """Disable span selectors in MNE PSD figures that open popup windows."""
    from matplotlib.widgets import SpanSelector

    for ax in fig.axes:
        for attr in vars(ax).values():
            if isinstance(attr, SpanSelector):
                attr.set_active(False)

def sanitize_mne_browser_toolbar(browser, *, allow_annotation_mode: bool) -> None:
    """Hide selected controls from MNE browser toolbars.

    Works with the matplotlib GTK4 backend; tries to iterate toolbar children by looking for GTK Button widgets
    whose tooltip/label matches keywords.
    """
    if browser is None:
        return

    hide_keywords = {"settings", "setting", "config", "options", "projector", "proj", "ssp"}
    if not allow_annotation_mode:
        hide_keywords.add("annotation")

    # MNE matplotlib browser: the canvas manager's window contains a Gtk.Box with the canvas and toolbar.
    canvas = getattr(browser, "canvas", None)
    if canvas is None:
        return
    mgr = getattr(canvas, "manager", None)
    if mgr is None:
        return

    # Walk toolbar buttons if accessible
    toolbar = getattr(mgr, "toolbar", None)
    if toolbar is None:
        return

    def walk_and_hide(widget):
        if isinstance(widget, Gtk.Button):
            tooltip = widget.get_tooltip_text() or ""
            label_text = ""
            child = widget.get_child()
            if isinstance(child, Gtk.Label):
                label_text = child.get_text() or ""
            combined = (tooltip + " " + label_text).lower()
            if any(k in combined for k in hide_keywords):
                widget.set_visible(False)
        child = widget.get_first_child() if hasattr(widget, "get_first_child") else None
        while child is not None:
            walk_and_hide(child)
            child = child.get_next_sibling() if hasattr(child, "get_next_sibling") else None

    try:
        if isinstance(toolbar, Gtk.Widget):
            walk_and_hide(toolbar)
    except Exception as e:
        logger.warning("sanitize_mne_browser_toolbar failed: %s", e)

def disable_mne_browser_channel_clicks(browser) -> None:
    """Disable channel click interactions in an MNE browser widget.

    Sets picker=False on channel trace lines and y-axis tick labels so they
    never fire pick_event.  The pick_event callback registry is left intact so
    MNE's scrollbar and overview bar widgets (which also use pick_event) keep
    working.
    """
    if browser is None:
        return

    mne_state = getattr(browser, "mne", None)
    canvas = getattr(browser, "canvas", None)

    if mne_state is not None:
        for trace in getattr(mne_state, "traces", []):
            if hasattr(trace, "set_picker"):
                trace.set_picker(False)

    if canvas is not None:
        fig = getattr(canvas, "figure", None)
        if fig is not None:
            for ax in fig.axes:
                for text in ax.get_yticklabels():
                    text.set_picker(False)

def embed_mne_browser(browser, container: Gtk.Box) -> None:
    """Detach an MNE browser widget from its window and embed it in container.

    Args:
        browser: The MNE browser object (matplotlib backend).
        container: The Gtk.Box to embed the browser into.
    """
    canvas = getattr(browser, "canvas", None)
    if canvas is None:
        logger.warning("embed_mne_browser: browser has no canvas attribute")
        return

    mgr = getattr(canvas, "manager", None)
    if mgr is None:
        logger.warning("embed_mne_browser: canvas has no manager")
        return

    mgr_window = getattr(mgr, "window", None)
    if mgr_window is None:
        logger.warning("embed_mne_browser: manager has no window")
        return

    # Realize the manager window before extracting its child.  matplotlib creates
    # the window with show=False so it is never realized; event controllers on the
    # canvas are only fully activated during realization.  Calling realize() here
    # ensures controllers are initialized before we reparent the widget.
    try:
        mgr_window.realize()
    except Exception as e:
        logger.debug("embed_mne_browser: realize() failed: %s", e)

    # Detach the Gtk.Box from the manager's window
    box = mgr_window.get_child()
    if box is not None:
        mgr_window.set_child(None)

    # Clear container
    child = container.get_first_child()
    while child is not None:
        nxt = child.get_next_sibling()
        container.remove(child)
        child = nxt

    if box is not None:
        box.set_hexpand(True)
        box.set_vexpand(True)
        container.append(box)

# -------- PlotCanvas --------

class PlotCanvas(Gtk.Box):
    """Gtk.Box that embeds a matplotlib figure together with a navigation toolbar.

    Provides update_figure() to swap in a new figure without rebuilding the widget.
    """

    def __init__(self, fig: Figure | None = None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.set_hexpand(True)
        self.set_vexpand(True)

        if fig is None:
            fig = Figure(figsize=(8, 4))

        self._canvas = FigureCanvasGTK4Agg(fig)
        self._canvas.set_hexpand(True)
        self._canvas.set_vexpand(True)
        self._canvas.set_size_request(-1, -1)

        self.toolbar = NavigationToolbar2GTK4(self._canvas)

        self.append(self.toolbar)
        self.append(self._canvas)

    @property
    def canvas(self) -> FigureCanvasGTK4Agg:
        return self._canvas

    def update_figure(self, fig: Figure) -> None:
        """Replace the current matplotlib figure with a new one.

        Args:
            fig: The new matplotlib Figure to display.
        """
        import matplotlib.pyplot as plt

        # Remove old widgets
        self.remove(self.toolbar)
        self.remove(self._canvas)

        old_fig = self._canvas.figure
        plt.close(old_fig)

        self._canvas = FigureCanvasGTK4Agg(fig)
        self._canvas.set_hexpand(True)
        self._canvas.set_vexpand(True)
        self._canvas.set_size_request(-1, -1)

        self.toolbar = NavigationToolbar2GTK4(self._canvas)

        self.append(self.toolbar)
        self.append(self._canvas)

# -------- ActionListItem --------

class ActionListItem(Gtk.Box):
    """Row widget representing a single pipeline action in the action list.

    Displays the action number, status icon, title, and a run button.
    A type_mismatch flag shows a warning icon and disables the run button
    when the action's expected input type doesn't match the pipeline's current type.

    The run_clicked callback is a plain Python callable (row: int) -> None.
    """

    def __init__(
        self,
        index: int,
        action: ActionConfig,
        type_mismatch: bool = False,
        run_clicked_cb: Callable[[int], None] | None = None,
    ):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self.set_margin_start(8)
        self.set_margin_end(8)
        self.set_margin_top(6)
        self.set_margin_bottom(6)

        self.index = index
        self.row = index - 1
        self.action = action
        self.type_mismatch = type_mismatch
        self.run_clicked_cb = run_clicked_cb

        # Status icon
        self.status_label = Gtk.Label()
        self.status_label.set_size_request(22, -1)
        self.status_label.set_xalign(0.5)
        self.update_status_icon()
        self.append(self.status_label)

        # Action name
        name = get_action_title(action)
        if action.is_custom:
            name += " [CUSTOM]" if action.action_id == CUSTOM_ACTION_ID else " [EDITED]"
        self.name_label = Gtk.Label(label=f"{index}. {name}")
        self.name_label.set_xalign(0.0)
        self.name_label.set_hexpand(True)
        self.name_label.add_css_class("action-item-name")
        if type_mismatch:
            self.name_label.add_css_class("action-item-mismatch")
        self.append(self.name_label)

        # Run button
        self.run_btn = Gtk.Button(label="▶")
        self.run_btn.set_size_request(35, 25)
        self.run_btn.set_sensitive(not type_mismatch)
        self.run_btn.connect("clicked", self.on_run_clicked)
        self.append(self.run_btn)

    def on_run_clicked(self, _btn):
        if self.run_clicked_cb is not None:
            self.run_clicked_cb(self.row)

    def set_run_clicked_cb(self, cb: Callable[[int], None]) -> None:
        """Set or replace the clicked callback."""
        self.run_clicked_cb = cb

    def update_status_icon(self) -> None:
        """Update the status icon label to match the action's current status."""
        if self.type_mismatch:
            self.status_label.set_text("⚠")
            self.status_label.remove_css_class("status-complete")
            self.status_label.remove_css_class("status-pending")
            self.status_label.add_css_class("status-error")
        else:
            self.status_label.set_text(STATUS_ICONS.get(self.action.status, "○"))
            for cls in ("status-complete", "status-error", "status-pending"):
                self.status_label.remove_css_class(cls)
            color = STATUS_COLORS.get(self.action.status, "")
            if "2E7D32" in color or "green" in color.lower():
                self.status_label.add_css_class("status-complete")
            elif "D32F2F" in color or "red" in color.lower():
                self.status_label.add_css_class("status-error")
            else:
                self.status_label.add_css_class("status-pending")

    def update_status(self, status: ActionStatus) -> None:
        """Set a new action status and refresh the status icon.

        Args:
            status: The new ActionStatus to apply.
        """
        self.action.status = status
        self.update_status_icon()
