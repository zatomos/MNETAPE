"""Visualization panel with PSD, time-series, sensor map, and topomap tabs.

VisualizationPanel displays the current data object at a user-selected pipeline step.
It contains tabs that are rendered on demand when the active tab changes.
The active tab set switches depending on the data type.
The time-series tab embeds an MNE interactive browser widget.
"""

import logging
from typing import Callable

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)
from matplotlib.figure import Figure
import mne

from mnetape.actions.registry import get_action_title
from mnetape.core.models import ActionConfig, STATUS_ICONS
from mnetape.gui.widgets import PlotCanvas
from mnetape.gui.widgets.common import (
    disable_mne_browser_channel_clicks,
    disable_psd_span_popups,
    sanitize_mne_browser_toolbar,
)

logger = logging.getLogger(__name__)


class PlotWorker(QThread):
    """Background thread for the compute phase of a plot update."""

    finished = pyqtSignal(object)

    def __init__(self, fn: Callable):
        super().__init__()
        self.fn = fn

    def run(self):
        try:
            self.finished.emit(self.fn())
        except Exception as e:
            logger.warning("Background plot computation failed: %s", e)
            self.finished.emit(None)

# Tab indices for each mode
RAW_TAB_NAMES = ["PSD", "Time Series", "Sensors", "Topomap"]
EPOCHS_TAB_NAMES = ["PSD", "Epochs Browser", "Sensors", "Topomap", "Epochs Image"]
EVOKED_TAB_NAMES = ["Evoked", "Topomap", "Sensors"]


def make_loading_fig(message: str, color: str = "#666666", fontstyle: str = "italic") -> Figure:
    """Return a matplotlib Figure showing a loading message."""
    fig = Figure(figsize=(8, 4))
    ax = fig.add_subplot(111)
    ax.text(0.5, 0.5, message, ha="center", va="center",
            fontsize=14, color=color, fontstyle=fontstyle)
    ax.set_xticks([])
    ax.set_yticks([])
    ax.spines[:].set_visible(False)
    return fig


class VisualizationPanel(QWidget):
    """Panel showing EEG visualizations across tabs for a selected pipeline step.

    Switches based on the data type being visualized.

    Attributes:
        step_combo: Combo box for choosing which pipeline step to visualize.
        btn_prev: Button to step backward through the pipeline.
        btn_next: Button to step forward through the pipeline.
        status_label: Displays a warning when the selected step has no computed data.
        tabs: QTabWidget containing the visualization tabs.
        plot_psd: PlotCanvas for the PSD plot.
        plot_sensors: PlotCanvas for the sensor map.
        plot_topomap: PlotCanvas for the topomap plot.
        plot_image: PlotCanvas for the epochs image plot (Epochs mode only).
        current_data: The MNE object currently being visualized.
        psd_data_id: id of the data used for the cached PSD, to skip redraws.
        topomap_data_id: id of the data used for the cached topomap.
        browser: The embedded MNE browser widget, or None.
        mode: Current tab mode: "raw", "epochs", or "evoked".
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.current_data = None
        self.psd_data_id = None
        self.topomap_data_id = None
        self.browser = None
        self.mode = "raw"
        # Worker management
        self.slot_workers: dict[str, PlotWorker] = {}
        self.orphaned_workers: set[PlotWorker] = set()
        self.loading_count = 0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        step_layout = QHBoxLayout()
        step_layout.addWidget(QLabel("Viewing after:"))

        self.step_combo = QComboBox()
        self.step_combo.addItem("Original (no processing)")
        step_layout.addWidget(self.step_combo, 1)

        self.btn_prev = QPushButton("◀")
        self.btn_prev.setFixedWidth(35)
        step_layout.addWidget(self.btn_prev)

        self.btn_next = QPushButton("▶")
        self.btn_next.setFixedWidth(35)
        step_layout.addWidget(self.btn_next)

        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: orange; font-weight: bold;")
        step_layout.addWidget(self.status_label)

        layout.addLayout(step_layout)

        self.tabs = QTabWidget()

        self.plot_psd = PlotCanvas()
        self.plot_evoked = PlotCanvas()
        self.plot_sensors = PlotCanvas()
        self.plot_topomap = PlotCanvas()
        self.plot_image = PlotCanvas()

        # Time-series tab (Raw mode)
        self.time_container = QWidget()
        self.time_layout = QVBoxLayout(self.time_container)
        self.time_layout.setContentsMargins(0, 0, 0, 0)
        self.time_placeholder = QLabel("Load data to view")
        self.time_placeholder.setStyleSheet(
            "color: #999999; font-size: 14pt; qproperty-alignment: AlignCenter;"
        )
        self.time_layout.addWidget(self.time_placeholder)

        # Epochs browser tab (Epochs mode)
        self.epochs_container = QWidget()
        self.epochs_layout = QVBoxLayout(self.epochs_container)
        self.epochs_layout.setContentsMargins(0, 0, 0, 0)
        self.epochs_placeholder = QLabel("Run epoching to view")
        self.epochs_placeholder.setStyleSheet(
            "color: #999999; font-size: 14pt; qproperty-alignment: AlignCenter;"
        )
        self.epochs_layout.addWidget(self.epochs_placeholder)

        self.build_raw_tabs()
        layout.addWidget(self.tabs)
        self.show_placeholder()


    # -------- Tab set switching --------

    def build_raw_tabs(self):
        """Populate the tab widget with Raw mode tabs."""
        self.tabs.clear()
        self.tabs.addTab(self.plot_psd, "PSD")
        self.tabs.addTab(self.time_container, "Time Series")
        self.tabs.addTab(self.plot_sensors, "Sensors")
        self.tabs.addTab(self.plot_topomap, "Topomap")
        self.mode = "raw"

    def build_epochs_tabs(self):
        """Populate the tab widget with Epochs mode tabs."""
        self.tabs.clear()
        self.tabs.addTab(self.plot_psd, "PSD")
        self.tabs.addTab(self.epochs_container, "Epochs Browser")
        self.tabs.addTab(self.plot_sensors, "Sensors")
        self.tabs.addTab(self.plot_topomap, "Topomap")
        self.tabs.addTab(self.plot_image, "Image")
        self.mode = "epochs"

    def build_evoked_tabs(self):
        """Populate the tab widget with Evoked mode tabs."""
        self.tabs.clear()
        self.tabs.addTab(self.plot_evoked, "Evoked")
        self.tabs.addTab(self.plot_topomap, "Topomap")
        self.tabs.addTab(self.plot_sensors, "Sensors")
        self.mode = "evoked"


    # -------- Helper methods --------

    def close_browser(self):
        """Close and remove the current MNE browser widget."""
        if self.browser is not None:
            container_layout = self.epochs_layout if self.mode == "epochs" else self.time_layout
            container_layout.removeWidget(self.browser)
            self.browser.close()
            self.browser.deleteLater()
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
        self.show_browser_loading(self.time_placeholder)
        try:
            self.close_browser()
            self.time_placeholder.setVisible(False)
            self.browser = self.current_data.plot(show=False)
            self.sanitize_browser_toolbar()
            self.time_layout.addWidget(self.browser)
        except Exception as e:
            logger.warning("Time-series plot update failed: %s", e)
            self.time_placeholder.setText("Load data to view")
        finally:
            self.clear_browser_loading()

    def update_epochs_browser(self):
        """Render the epochs browser tab by embedding an MNE Epochs browser widget."""
        if self.current_data is None or not isinstance(self.current_data, mne.Epochs):
            return
        self.show_browser_loading(self.epochs_placeholder)
        try:
            self.close_browser()
            self.epochs_placeholder.setVisible(False)
            self.browser = self.current_data.plot(show=False)
            self.sanitize_browser_toolbar()
            self.epochs_layout.addWidget(self.browser)
        except Exception as e:
            logger.warning("Epochs browser update failed: %s", e)
            self.epochs_placeholder.setText("Run epoching to view")
        finally:
            self.clear_browser_loading()

    def show_placeholder(self):
        """Show placeholder text when no data is loaded."""
        self.psd_data_id = None
        self.topomap_data_id = None
        for plot in [self.plot_psd, self.plot_evoked, self.plot_sensors, self.plot_topomap, self.plot_image]:
            plot.update_figure(make_loading_fig("Load data to view", color="#999999", fontstyle="normal"))
        self.close_browser()
        self.time_placeholder.setVisible(True)
        self.epochs_placeholder.setVisible(True)

    def update_step_list(self, actions: list[ActionConfig]):
        """Repopulate the step combo box to reflect the current action list.

        Preserves the current combo index when possible.

        Args:
            actions: The ordered list of pipeline actions.
        """
        old_index = self.step_combo.currentIndex()
        self.step_combo.blockSignals(True)
        self.step_combo.clear()
        self.step_combo.addItem("Original (no processing)")
        for i, action in enumerate(actions, 1):
            status = STATUS_ICONS.get(action.status, "○")
            name = get_action_title(action)
            if action.is_custom:
                name += " [CUSTOM]"
            self.step_combo.addItem(f"{status} {i}. {name}")
        new_index = old_index if 0 <= old_index < self.step_combo.count() else 0
        self.step_combo.setCurrentIndex(new_index)
        self.step_combo.blockSignals(False)

    def update_plots(
        self,
        data: mne.io.Raw | mne.Epochs | mne.Evoked | None,
        current_step: int = 0,
        computed_steps: int = 0,
    ):
        """Update the active tab's plot for a new data object or step selection.

        Switches between tab sets when the data type changes.
        Sets status_label when the requested step has not been computed yet.
        Reconnects the tab-changed signal so that switching tabs triggers a fresh render.

        Args:
            data: The MNE object to visualize, or None to show a placeholder.
            current_step: Index of the step combo selection (0 = original).
            computed_steps: Number of actions whose results are available.
        """
        self.current_data = data

        if current_step > computed_steps and current_step > 0:
            self.status_label.setText("(not computed - showing original)")
        else:
            self.status_label.setText("")

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
            current_tab = self.tabs.currentIndex()

        if new_mode == "epochs":
            self.render_epochs_tab(current_tab)
        elif new_mode == "evoked":
            self.render_evoked_tab(current_tab)
        else:
            self.render_raw_tab(current_tab)

        try:
            self.tabs.currentChanged.disconnect()
        except TypeError:
            pass
        self.tabs.currentChanged.connect(self.on_tab_changed)


    # ------- Loading / worker helpers --------

    def start_loading(self, canvas: PlotCanvas, message: str) -> None:
        """Show a loading placeholder on canvas and set the wait cursor."""
        canvas.update_figure(make_loading_fig(message))
        self.loading_count += 1
        if self.loading_count == 1:
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)

    def finish_loading(self) -> None:
        """Decrement loading counter and restore cursor when all workers are done."""
        self.loading_count = max(0, self.loading_count - 1)
        if self.loading_count == 0:
            QApplication.restoreOverrideCursor()

    def run_plot_worker(
        self,
        slot_key: str,
        canvas: PlotCanvas,
        compute_fn: Callable,
        render_fn: Callable,
        message: str = "Computing...",
    ) -> None:
        """Start a background thread to compute data, then render a figure on the main thread.

        Shows a loading placeholder immediately. The background thread runs compute_fn and emits the result
        via a queued signal.
        If a worker for the same slot is already running, its result is discarded.

        Args:
            slot_key: Unique name for this plot slot (e.g. "psd", "topomap").
            canvas: The PlotCanvas to update when done.
            compute_fn: Runs in background; returns intermediate data (e.g. a Spectrum object).
                        Return None to skip rendering.
            render_fn: Runs on the main thread; receives the compute_fn result; returns a Figure.
            message: Text shown in the loading placeholder.
        """
        # Discard result from any previous worker for this slot.
        # Keep a live reference in orphaned_workers until the thread exits.
        old = self.slot_workers.pop(slot_key, None)
        if old is not None:
            try:
                old.finished.disconnect()
            except TypeError:
                pass
            self.orphaned_workers.add(old)
            old.finished.connect(lambda _: self.orphaned_workers.discard(old),
                                 Qt.ConnectionType.QueuedConnection)

        self.start_loading(canvas, message)

        worker = PlotWorker(compute_fn)
        self.slot_workers[slot_key] = worker

        def on_worker_done(result):
            self.slot_workers.pop(slot_key, None)
            self.finish_loading()
            if result is not None:
                try:
                    fig = render_fn(result)
                    if fig is not None:
                        canvas.update_figure(fig)
                except Exception as e:
                    logger.warning("Plot render failed for %s: %s", slot_key, e)

        worker.finished.connect(on_worker_done, Qt.ConnectionType.QueuedConnection)
        worker.start()

    def show_browser_loading(self, label: QLabel) -> None:
        """Show a loading label in a browser container and push the wait cursor."""
        label.setText("Loading...")
        label.setVisible(True)
        self.loading_count += 1
        if self.loading_count == 1:
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        QApplication.processEvents()  # browser loads are synchronous — flush now so label is visible

    def clear_browser_loading(self) -> None:
        """Pop the wait cursor after a synchronous browser-load operation."""
        self.finish_loading()

    # ------- Plot update methods --------

    def on_tab_changed(self, index: int):
        """Render the newly selected tab when the user switches tabs.

        Args:
            index: The tab index that was just selected.
        """
        if self.current_data is None:
            return
        if isinstance(self.current_data, mne.Epochs):
            self.render_epochs_tab(index)
        elif isinstance(self.current_data, mne.Evoked):
            self.render_evoked_tab(index)
        else:
            self.render_raw_tab(index)

    def render_raw_tab(self, index: int):
        """Render tab by index (0=PSD, 1=Time, 2=Sensors, 3=Topomap) for Raw mode."""
        if index == 0:
            self.update_psd_plot()
        elif index == 1:
            self.update_time_plot()
        elif index == 2:
            self.update_sensors_plot()
        elif index == 3:
            self.update_topomap_plot()

    def render_epochs_tab(self, index: int):
        """Render a tab by index (0=PSD, 1=Browser, 2=Sensors, 3=Topomap, 4=Image) for Epochs mode."""
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
        """Render a tab by index (0=Evoked, 1=Topomap, 2=Sensors) for Evoked mode."""
        if index == 0:
            self.update_evoked_plot()
        elif index == 1:
            self.update_topomap_plot()
        elif index == 2:
            self.update_sensors_plot()

    def update_psd_plot(self):
        """Compute and display the PSD plot in a background thread, skipping if data is unchanged."""
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
        """Render a sensor map plot on the main thread."""
        if self.current_data is None:
            return
        data = self.current_data

        def render(d):
            if isinstance(d, mne.Evoked):
                return mne.viz.plot_sensors(d.info, show=False, show_names=True, kind="3d")
            return d.plot_sensors(show=False, show_names=True, kind="3d")

        self.run_plot_worker("sensors", self.plot_sensors, lambda: data, render, "Rendering sensor map...")

    def update_topomap_plot(self):
        """Compute PSD in a background thread, then render the topomap on the main thread."""
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
        """Render an epochs image plot on the main thread (Epochs mode only)."""
        if self.current_data is None or not isinstance(self.current_data, mne.Epochs):
            return
        data = self.current_data

        def render(d):
            figs = d.plot_image(show=False)
            return figs[0] if figs else None

        self.run_plot_worker("image", self.plot_image, lambda: data, render, "Rendering epochs image...")
