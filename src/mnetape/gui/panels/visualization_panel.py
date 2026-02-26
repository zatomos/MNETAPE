"""Visualization panel with PSD, time-series, sensor map, and topomap tabs.

VisualizationPanel displays the current raw object at a user-selected pipeline step.
It contains four tabs (PSD, Time Series, Sensors, Topomap) that are rendered on demand when the active tab changes.
The time-series tab embeds an MNE interactive browser widget.
"""

import logging

from PyQt6.QtWidgets import (
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

logger = logging.getLogger(__name__)


class VisualizationPanel(QWidget):
    """Panel showing EEG visualizations across four tabs for a selected pipeline step.

    Attributes:
        step_combo: Combo box for choosing which pipeline step to visualize.
        btn_prev: Button to step backward through the pipeline.
        btn_next: Button to step forward through the pipeline.
        status_label: Displays a warning when the selected step has no computed raw.
        tabs: QTabWidget containing the four visualization tabs.
        plot_psd: PlotCanvas for the PSD plot.
        plot_sensors: PlotCanvas for the sensor map.
        plot_topomap: PlotCanvas for the topomap plot.
        current_raw: The MNE Raw object currently being visualized.
        psd_raw_id: id() of the raw used for the cached PSD, to skip redraws.
        topomap_raw_id: id() of the raw used for the cached topomap.
        browser: The embedded MNE time-series browser widget, or None.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.current_raw = None
        self.psd_raw_id = None
        self.topomap_raw_id = None
        self.browser = None

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
        self.plot_sensors = PlotCanvas()
        self.plot_topomap = PlotCanvas()

        # Time-series tab
        self.time_container = QWidget()
        self.time_layout = QVBoxLayout(self.time_container)
        self.time_layout.setContentsMargins(0, 0, 0, 0)
        self.time_placeholder = QLabel("Load data to view")
        self.time_placeholder.setStyleSheet(
            "color: #999999; font-size: 14pt; qproperty-alignment: AlignCenter;"
        )
        self.time_layout.addWidget(self.time_placeholder)

        self.tabs.addTab(self.plot_psd, "PSD")
        self.tabs.addTab(self.time_container, "Time Series")
        self.tabs.addTab(self.plot_sensors, "Sensors")
        self.tabs.addTab(self.plot_topomap, "Topomap")

        layout.addWidget(self.tabs)

        self.show_placeholder()


    # -------- Helper methods --------

    def close_browser(self):
        """Close and remove the current MNE browser widget."""

        if self.browser is not None:
            self.time_layout.removeWidget(self.browser)
            self.browser.close()
            self.browser.deleteLater()
            self.browser = None

    def disable_browser_clicks(self):
        """Disable click-to-mark-bad on traces and channel axis."""

        if self.browser is None:
            return
        try:
            mne_params = self.browser.mne
            # Disable clicks on each channel trace
            for trace in mne_params.traces:
                trace.setClickable(False)
            # Disable clicks on the channel name axis
            ch_axis = mne_params.channel_axis
            ch_axis.mouseClickEvent = lambda ev: ev.ignore()
        except Exception as e:
            logger.warning("Failed to disable browser clicks: %s", e)

    def update_time_plot(self):
        """Render the time-series tab by embedding an MNE browser widget."""
        if self.current_raw is None:
            return
        try:
            self.close_browser()
            self.time_placeholder.setVisible(False)
            self.browser = self.current_raw.plot(show=False)
            self.disable_browser_clicks()
            self.time_layout.addWidget(self.browser)
        except Exception as e:
            logger.warning("Time-series plot update failed: %s", e)

    def show_placeholder(self):
        """Show placeholder text when no data is loaded."""

        self.psd_raw_id = None
        self.topomap_raw_id = None
        for plot in [self.plot_psd, self.plot_sensors, self.plot_topomap]:
            fig = Figure(figsize=(8, 4))
            ax = fig.add_subplot(111)
            ax.text(0.5, 0.5, "Load data to view", ha="center", va="center", fontsize=14, color="#999999")
            ax.set_xticks([])
            ax.set_yticks([])
            ax.spines[:].set_visible(False)
            plot.update_figure(fig)
        self.close_browser()
        self.time_placeholder.setVisible(True)

    def update_step_list(self, actions: list[ActionConfig]):
        """Repopulate the step combo box to reflect the current action list.

        Preserves the current combo index when possible.

        Args:
            actions: The ordered list of pipeline actions.
        """
        current = self.step_combo.currentIndex()
        self.step_combo.clear()
        self.step_combo.addItem("Original (no processing)")
        for i, action in enumerate(actions, 1):
            status = STATUS_ICONS.get(action.status, "○")
            name = get_action_title(action)
            if action.is_custom:
                name += " [CUSTOM]"
            self.step_combo.addItem(f"{status} {i}. {name}")
        if current < self.step_combo.count():
            self.step_combo.setCurrentIndex(current)

    def update_plots(self, raw: mne.io.Raw | None, current_step: int = 0, computed_steps: int = 0):
        """Update the active tab's plot for a new raw object or step selection.

        Sets status_label when the requested step has not been computed yet.
        Reconnects the tab-changed signal so that switching tabs triggers a fresh render.

        Args:
            raw: The MNE Raw object to visualize, or None to show a placeholder.
            current_step: Index of the step combo selection (0 = original).
            computed_steps: Number of actions whose results are available.
        """
        self.current_raw = raw

        if current_step > computed_steps and current_step > 0:
            self.status_label.setText("(not computed - showing original)")
        else:
            self.status_label.setText("")

        if raw is None:
            self.show_placeholder()
            return

        current_tab = self.tabs.currentIndex()

        if current_tab == 0:
            self.update_psd_plot()
        elif current_tab == 1:
            self.update_time_plot()
        elif current_tab == 2:
            self.update_sensors_plot()
        elif current_tab == 3:
            self.update_topomap_plot()

        try:
            self.tabs.currentChanged.disconnect()
        except TypeError:
            pass
        self.tabs.currentChanged.connect(self.on_tab_changed)


    # ------- Plot update methods --------

    def on_tab_changed(self, index: int):
        """Render the newly selected tab when the user switches tabs.

        Args:
            index: The tab index that was just selected (0=PSD, 1=Time, 2=Sensors, 3=Topomap).
        """
        if self.current_raw is None:
            return
        if index == 0:
            self.update_psd_plot()
        elif index == 1:
            self.update_time_plot()
        elif index == 2:
            self.update_sensors_plot()
        elif index == 3:
            self.update_topomap_plot()

    def update_psd_plot(self):
        """Compute and display the PSD plot, skipping if the raw object is unchanged."""
        if self.current_raw is None:
            return
        raw_id = id(self.current_raw)
        if raw_id == self.psd_raw_id:
            return
        try:
            fig_psd = self.current_raw.compute_psd(fmax=60).plot(
                show=False,
            )
            self.plot_psd.update_figure(fig_psd)
            self.psd_raw_id = raw_id
        except Exception as e:
            logger.warning("PSD plot update failed: %s", e)

    def update_sensors_plot(self):
        """Render a 3-D sensor map plot for the current raw object."""
        if self.current_raw is None:
            return
        try:
            fig_sensors = self.current_raw.plot_sensors(show=False, show_names=True, kind="3d")
            self.plot_sensors.update_figure(fig_sensors)
        except Exception as e:
            logger.warning("Sensor plot update failed: %s", e)

    def update_topomap_plot(self):
        """Compute and display the PSD topomap, skipping if the raw object is unchanged."""
        if self.current_raw is None:
            return
        raw_id = id(self.current_raw)
        if raw_id == self.topomap_raw_id:
            return
        try:
            fig_topo = self.current_raw.compute_psd(fmax=60).plot_topomap(
                show=False,
            )
            self.plot_topomap.update_figure(fig_topo)
            self.topomap_raw_id = raw_id
        except Exception as e:
            logger.warning("Topomap plot update failed: %s", e)
