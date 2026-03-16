"""Visualization panel with PSD, time-series, sensor map, and topomap tabs.

VisualizationPanel displays the current data object at a user-selected pipeline step.
It contains tabs that are rendered on demand when the active tab changes.
The active tab set switches depending on the data type.
The time-series tab embeds an MNE interactive browser widget.
"""

from __future__ import annotations

import logging
import threading
from typing import Callable

from gi.repository import Gtk, GLib
from matplotlib.figure import Figure
import mne

from mnetape.gui.widgets import PlotCanvas
from mnetape.gui.widgets.common import (
    disable_mne_browser_channel_clicks,
    disable_psd_span_popups,
    embed_mne_browser,
    sanitize_mne_browser_toolbar,
)

logger = logging.getLogger(__name__)

# Tab indices for each mode
RAW_TAB_NAMES = ["Time Series", "PSD", "Sensors", "Topomap"]
EPOCHS_TAB_NAMES = ["PSD", "Epochs Browser", "Sensors", "Topomap", "Epochs Image"]
EVOKED_TAB_NAMES = ["Evoked", "Topomap", "Sensors"]

def make_loading_fig(message: str, color: str = "#666666", fontstyle: str = "italic") -> Figure:
    """Return a matplotlib Figure showing a loading/placeholder message."""
    fig = Figure(figsize=(8, 4))
    ax = fig.add_subplot(111)
    ax.text(0.5, 0.5, message, ha="center", va="center",
            fontsize=14, color=color, fontstyle=fontstyle)
    ax.set_xticks([])
    ax.set_yticks([])
    ax.spines[:].set_visible(False)
    return fig


def setup_browser_scroll_focus(browser) -> None:
    """Configure the embedded browser for sizing and native input handling.
    """
    canvas = getattr(browser, "canvas", None)
    if canvas is None:
        return
    try:
        canvas.set_focusable(True)
        canvas.set_hexpand(True)
        canvas.set_vexpand(True)
        canvas.set_size_request(-1, -1)

        motion = Gtk.EventControllerMotion()
        motion.connect("enter", lambda *_: canvas.grab_focus())
        canvas.add_controller(motion)

        def on_scroll(_ctrl, _dx, dy):
            mne_state = getattr(browser, "mne", None)
            if dy == 0 or mne_state is None:
                return False

            max_start = max(0, len(mne_state.ch_order) - mne_state.n_channels)
            direction = 1 if dy > 0 else -1
            new_start = min(max(mne_state.ch_start + direction, 0), max_start)
            if new_start == mne_state.ch_start:
                return True

            mne_state.ch_start = new_start
            browser._update_picks()
            browser._update_vscroll()
            browser._redraw()
            return True

        scroll = Gtk.EventControllerScroll.new(Gtk.EventControllerScrollFlags.VERTICAL)
        scroll.connect("scroll", on_scroll)
        canvas.add_controller(scroll)
    except Exception as e:
        logger.debug("Failed to set up browser scroll focus: %s", e)


class VisualizationPanel(Gtk.Box):
    """Panel showing EEG visualizations across tabs for the latest pipeline step.

    Attributes:
        notebook: Gtk.Notebook containing the visualization tabs.
        current_data: The MNE object currently being visualized.
        psd_data_id: id of the data used for the cached PSD, to skip redraws.
        topomap_data_id: id of the data used for the cached topomap.
        browser: The embedded MNE browser widget, or None.
        mode: Current tab mode: "raw", "epochs", or "evoked".
    """

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.set_hexpand(True)
        self.set_vexpand(True)

        self.current_data = None
        self.psd_data_id = None
        self.topomap_data_id = None
        self.browser = None
        self.mode = "raw"
        # Worker management: slot_key -> slot identity list
        self.slot_workers: dict[str, list] = {}
        self.loading_count = 0

        # ---- Tab notebook ----
        self.notebook = Gtk.Notebook()
        self.notebook.set_hexpand(True)
        self.notebook.set_vexpand(True)

        # Create plot canvases
        self.plot_psd = PlotCanvas()
        self.plot_evoked = PlotCanvas()
        self.plot_sensors = PlotCanvas()
        self.plot_topomap = PlotCanvas()
        self.plot_image = PlotCanvas()

        # Time-series container (Raw mode)
        self.time_container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.time_container.set_hexpand(True)
        self.time_container.set_vexpand(True)
        self.time_placeholder = Gtk.Label(label="Load data to view")
        self.time_placeholder.add_css_class("dim-label")
        self.time_placeholder.set_hexpand(True)
        self.time_placeholder.set_vexpand(True)
        self.time_container.append(self.time_placeholder)

        # Epochs browser container (Epochs mode)
        self.epochs_container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.epochs_container.set_hexpand(True)
        self.epochs_container.set_vexpand(True)
        self.epochs_placeholder = Gtk.Label(label="Run epoching to view")
        self.epochs_placeholder.add_css_class("dim-label")
        self.epochs_placeholder.set_hexpand(True)
        self.epochs_placeholder.set_vexpand(True)
        self.epochs_container.append(self.epochs_placeholder)

        # Tab bar
        self.tab_bar_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        self.tab_bar_box.add_css_class("linked")
        self.tab_bar_box.set_margin_start(8)
        self.tab_bar_box.set_margin_top(4)
        self.tab_bar_box.set_margin_bottom(4)
        self.append(self.tab_bar_box)

        # Stack
        self.tab_stack = Gtk.Stack()
        self.tab_stack.set_hexpand(True)
        self.tab_stack.set_vexpand(True)
        self.tab_stack.set_transition_type(Gtk.StackTransitionType.NONE)
        self.tab_stack.add_named(self.plot_psd, "psd")
        self.tab_stack.add_named(self.time_container, "time")
        self.tab_stack.add_named(self.epochs_container, "epochs")
        self.tab_stack.add_named(self.plot_sensors, "sensors")
        self.tab_stack.add_named(self.plot_topomap, "topomap")
        self.tab_stack.add_named(self.plot_image, "image")
        self.tab_stack.add_named(self.plot_evoked, "evoked")
        self.append(self.tab_stack)

        self.current_tab_names: list[str] = []
        self.mode = "raw"
        self.build_raw_tabs()
        self.show_placeholder()

    # -------- Tab set switching --------

    def rebuild_tab_bar(self, tabs: list[tuple[str, str]]) -> None:
        """Rebuild tab toggle buttons for the given (stack-name, label) pairs."""
        child = self.tab_bar_box.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.tab_bar_box.remove(child)
            child = nxt

        self.current_tab_names = [name for name, _ in tabs]
        first_btn: Gtk.ToggleButton | None = None
        for name, label in tabs:
            btn = Gtk.ToggleButton(label=label)
            if first_btn is None:
                first_btn = btn
                btn.set_active(True)
            else:
                btn.set_group(first_btn)
            btn.connect("toggled", self.on_tab_toggled, name)
            self.tab_bar_box.append(btn)

        if self.current_tab_names:
            self.tab_stack.set_visible_child_name(self.current_tab_names[0])

    def on_tab_toggled(self, btn: Gtk.ToggleButton, tab_name: str) -> None:
        if not btn.get_active():
            return
        self.tab_stack.set_visible_child_name(tab_name)
        idx = self.current_tab_names.index(tab_name)
        if self.current_data is None:
            return
        if isinstance(self.current_data, mne.Epochs):
            self.render_epochs_tab(idx)
        elif isinstance(self.current_data, mne.Evoked):
            self.render_evoked_tab(idx)
        else:
            self.render_raw_tab(idx)

    def build_raw_tabs(self) -> None:
        self.rebuild_tab_bar([
            ("time", "Time Series"), ("psd", "PSD"),
            ("sensors", "Sensors"), ("topomap", "Topomap"),
        ])
        self.mode = "raw"

    def build_epochs_tabs(self) -> None:
        self.rebuild_tab_bar([
            ("psd", "PSD"), ("epochs", "Epochs Browser"),
            ("sensors", "Sensors"), ("topomap", "Topomap"), ("image", "Image"),
        ])
        self.mode = "epochs"

    def build_evoked_tabs(self) -> None:
        self.rebuild_tab_bar([
            ("evoked", "Evoked"), ("topomap", "Topomap"), ("sensors", "Sensors"),
        ])
        self.mode = "evoked"

    def get_current_tab_index(self) -> int:
        visible = self.tab_stack.get_visible_child_name() or ""
        try:
            return self.current_tab_names.index(visible)
        except ValueError:
            return 0

    # -------- Helper methods --------

    def close_browser(self):
        """Close and remove the current MNE browser widget, freeing the matplotlib figure."""
        if self.browser is not None:
            import matplotlib.pyplot as plt

            container = self.epochs_container if self.mode == "epochs" else self.time_container
            child = container.get_first_child()
            while child is not None:
                nxt = child.get_next_sibling()
                if child is not self.time_placeholder and child is not self.epochs_placeholder:
                    container.remove(child)
                child = nxt
            try:
                plt.close(self.browser)
            except Exception as e:
                logger.warning("close_browser: plt.close failed: %s", e)
            self.browser = None

    def sanitize_browser_toolbar(self):
        """Hide unsupported toolbar controls in embedded MNE browsers."""
        if self.browser is None:
            return
        try:
            sanitize_mne_browser_toolbar(self.browser, allow_annotation_mode=False)
            disable_mne_browser_channel_clicks(self.browser)
        except Exception as e:
            logger.warning("Failed to sanitize browser toolbar: %s", e)

    def update_time_plot(self):
        """Render the time-series tab by embedding an MNE browser widget."""
        if self.current_data is None or isinstance(self.current_data, (mne.Epochs, mne.Evoked)):
            return
        self.time_placeholder.set_text("Loading...")
        self.time_placeholder.set_visible(True)
        try:
            self.close_browser()
            self.time_placeholder.set_visible(False)
            self.browser = self.current_data.plot(show=False)
            self.sanitize_browser_toolbar()
            embed_mne_browser(self.browser, self.time_container)
            setup_browser_scroll_focus(self.browser)
        except Exception as e:
            logger.warning("Time-series plot update failed: %s", e)
            self.close_browser()
            self.time_placeholder.set_text("Load data to view")
            self.time_placeholder.set_visible(True)

    def update_epochs_browser(self):
        """Render the epochs browser tab by embedding an MNE Epochs browser widget."""
        if self.current_data is None or not isinstance(self.current_data, mne.Epochs):
            return
        self.epochs_placeholder.set_text("Loading...")
        self.epochs_placeholder.set_visible(True)
        try:
            self.close_browser()
            self.epochs_placeholder.set_visible(False)
            self.browser = self.current_data.plot(show=False)
            self.sanitize_browser_toolbar()
            embed_mne_browser(self.browser, self.epochs_container)
            setup_browser_scroll_focus(self.browser)
        except Exception as e:
            logger.warning("Epochs browser update failed: %s", e)
            self.close_browser()
            self.epochs_placeholder.set_text("Run epoching to view")
            self.epochs_placeholder.set_visible(True)

    def show_placeholder(self):
        """Show placeholder text when no data is loaded."""
        self.psd_data_id = None
        self.topomap_data_id = None
        for plot in [self.plot_psd, self.plot_evoked, self.plot_sensors, self.plot_topomap, self.plot_image]:
            plot.update_figure(make_loading_fig("Load data to view", color="#999999", fontstyle="normal"))
        self.close_browser()
        self.time_placeholder.set_visible(True)
        self.epochs_placeholder.set_visible(True)

    def update_plots(self, data: mne.io.Raw | mne.Epochs | mne.Evoked | None):
        """Update the active tab's plot for a new data object."""
        self.current_data = data

        if data is None:
            self.show_placeholder()
            return

        if isinstance(data, mne.Epochs):
            new_mode = "epochs"
        elif isinstance(data, mne.Evoked):
            new_mode = "evoked"
        else:
            new_mode = "raw"

        if new_mode != self.mode:
            current_tab = 0
            if new_mode == "epochs":
                self.build_epochs_tabs()
            elif new_mode == "evoked":
                self.build_evoked_tabs()
            else:
                self.build_raw_tabs()
        else:
            current_tab = self.get_current_tab_index()

        if new_mode == "epochs":
            self.render_epochs_tab(current_tab)
        elif new_mode == "evoked":
            self.render_evoked_tab(current_tab)
        else:
            self.render_raw_tab(current_tab)

    # ------- Loading / worker helpers --------

    def start_loading(self, canvas: PlotCanvas, message: str) -> None:
        """Show a loading placeholder on canvas."""
        canvas.update_figure(make_loading_fig(message))
        self.loading_count += 1

    def finish_loading(self) -> None:
        """Decrement loading counter."""
        self.loading_count = max(0, self.loading_count - 1)

    def run_plot_worker(
        self,
        slot_key: str,
        canvas: PlotCanvas,
        compute_fn: Callable,
        render_fn: Callable,
        message: str = "Computing...",
    ) -> None:
        """Start a background thread to compute data, then render on the main thread.

        Args:
            slot_key: Unique name for this plot slot (e.g. "psd", "topomap").
            canvas: The PlotCanvas to update when done.
            compute_fn: Runs in background; returns intermediate data. Return None to skip.
            render_fn: Runs on the main thread; receives the compute_fn result; returns a Figure.
            message: Text shown in the loading placeholder.
        """
        # Cancel any existing worker for this slot by marking it superseded
        slot_id = [id(self)]  # mutable reference to detect supersession

        self.slot_workers[slot_key] = slot_id

        self.start_loading(canvas, message)

        my_slot_id = slot_id  # capture reference

        def _worker():
            try:
                result = compute_fn()
            except Exception as e:
                logger.warning("Background plot computation failed (%s): %s", slot_key, e)
                result = None

            def _on_done():
                # Check if this worker has been superseded
                if self.slot_workers.get(slot_key) is not my_slot_id:
                    self.finish_loading()
                    return False
                self.slot_workers.pop(slot_key, None)
                self.finish_loading()
                if result is not None:
                    try:
                        fig = render_fn(result)
                        if fig is not None:
                            canvas.update_figure(fig)
                    except Exception as e:
                        logger.warning("Plot render failed for %s: %s", slot_key, e)
                return False  # Don't repeat

            GLib.idle_add(_on_done)

        t = threading.Thread(target=_worker, daemon=True)
        t.start()

    # ------- Tab signal --------

    def on_switch_page(self, _notebook, _page, page_num: int):
        """Render the newly selected tab."""
        if self.current_data is None:
            return
        if isinstance(self.current_data, mne.Epochs):
            self.render_epochs_tab(page_num)
        elif isinstance(self.current_data, mne.Evoked):
            self.render_evoked_tab(page_num)
        else:
            self.render_raw_tab(page_num)

    # ------- Plot update methods --------

    def render_raw_tab(self, index: int):
        """Render tab by index for Raw mode."""
        if index == 0:
            self.update_time_plot()
        elif index == 1:
            self.update_psd_plot()
        elif index == 2:
            self.update_sensors_plot()
        elif index == 3:
            self.update_topomap_plot()

    def render_epochs_tab(self, index: int):
        """Render tab by index for Epochs mode."""
        if index == 0:
            self.update_psd_plot()
        elif index == 1:
            self.update_epochs_browser()
        elif index == 2:
            self.update_sensors_plot()
        elif index == 3:
            self.update_topomap_plot()
        elif index == 4:
            self.update_image_plot()

    def render_evoked_tab(self, index: int):
        """Render tab by index for Evoked mode."""
        if index == 0:
            self.update_evoked_plot()
        elif index == 1:
            self.update_topomap_plot()
        elif index == 2:
            self.update_sensors_plot()

    def update_psd_plot(self):
        """Compute and display the PSD plot in a background thread."""
        if self.current_data is None:
            return
        data_id = id(self.current_data)
        if data_id == self.psd_data_id:
            return
        data = self.current_data

        def render(spectrum):
            fig = spectrum.plot(show=False)
            disable_psd_span_popups(fig)
            self.psd_data_id = data_id
            return fig

        self.run_plot_worker("psd", self.plot_psd,
                             lambda: data.compute_psd(fmax=60), render, "Computing PSD...")

    def update_evoked_plot(self):
        """Render an evoked waveform plot on the main thread."""
        if self.current_data is None or not isinstance(self.current_data, mne.Evoked):
            return
        data = self.current_data
        self.run_plot_worker(
            "evoked", self.plot_evoked,
            lambda: data,
            lambda d: d.plot(show=False),
            "Rendering evoked...",
        )

    def update_sensors_plot(self):
        """Render a sensor map plot."""
        if self.current_data is None:
            return
        data = self.current_data

        def render(d):
            if isinstance(d, mne.Evoked):
                return mne.viz.plot_sensors(d.info, show=False, show_names=True, kind="3d")
            return d.plot_sensors(show=False, show_names=True, kind="3d")

        self.run_plot_worker("sensors", self.plot_sensors, lambda: data, render, "Rendering sensor map...")

    def update_topomap_plot(self):
        """Compute PSD in a background thread, then render the topomap."""
        if self.current_data is None:
            return
        data_id = id(self.current_data)
        if data_id == self.topomap_data_id:
            return
        data = self.current_data

        if isinstance(data, mne.Evoked):
            def render_evoked_topo(d):
                fig = d.plot_topomap(times="auto", show=False)
                self.topomap_data_id = data_id
                return fig
            self.run_plot_worker("topomap", self.plot_topomap,
                                 lambda: data, render_evoked_topo, "Computing topomap...")
        else:
            def render_psd_topo(spectrum):
                fig = spectrum.plot_topomap(show=False)
                self.topomap_data_id = data_id
                return fig
            self.run_plot_worker("topomap", self.plot_topomap,
                                 lambda: data.compute_psd(fmax=60), render_psd_topo, "Computing topomap...")

    def update_image_plot(self):
        """Render an epochs image plot (Epochs mode only)."""
        if self.current_data is None or not isinstance(self.current_data, mne.Epochs):
            return
        data = self.current_data

        def render(d):
            figs = d.plot_image(show=False)
            return figs[0] if figs else None

        self.run_plot_worker("image", self.plot_image, lambda: data, render, "Rendering epochs image...")
