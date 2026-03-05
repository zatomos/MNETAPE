"""Visualization panel with PSD, time-series, sensor map, and topomap tabs.

VisualizationPanel displays the current data object at a user-selected pipeline step.
It contains tabs that are rendered on demand when the active tab changes.
The active tab set switches depending on the data type.
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
from mnetape.gui.widgets.common import (
    disable_mne_browser_channel_clicks,
    disable_psd_span_popups,
    sanitize_mne_browser_toolbar,
)

logger = logging.getLogger(__name__)

# Tab indices for each mode
RAW_TAB_NAMES = ["PSD", "Time Series", "Sensors", "Topomap"]
EPOCHS_TAB_NAMES = ["PSD", "Epochs Browser", "Sensors", "Topomap", "Epochs Image"]
EVOKED_TAB_NAMES = ["Evoked", "Topomap", "Sensors"]


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
        try:
            self.close_browser()
            self.time_placeholder.setVisible(False)
            self.browser = self.current_data.plot(show=False)
            self.sanitize_browser_toolbar()
            self.time_layout.addWidget(self.browser)
        except Exception as e:
            logger.warning("Time-series plot update failed: %s", e)

    def update_epochs_browser(self):
        """Render the epochs browser tab by embedding an MNE Epochs browser widget."""
        if self.current_data is None or not isinstance(self.current_data, mne.Epochs):
            return
        try:
            self.close_browser()
            self.epochs_placeholder.setVisible(False)
            self.browser = self.current_data.plot(show=False)
            self.sanitize_browser_toolbar()
            self.epochs_layout.addWidget(self.browser)
        except Exception as e:
            logger.warning("Epochs browser update failed: %s", e)

    def show_placeholder(self):
        """Show placeholder text when no data is loaded."""
        self.psd_data_id = None
        self.topomap_data_id = None
        for plot in [self.plot_psd, self.plot_evoked, self.plot_sensors, self.plot_topomap, self.plot_image]:
            fig = Figure(figsize=(8, 4))
            ax = fig.add_subplot(111)
            ax.text(0.5, 0.5, "Load data to view", ha="center", va="center", fontsize=14, color="#999999")
            ax.set_xticks([])
            ax.set_yticks([])
            ax.spines[:].set_visible(False)
            plot.update_figure(fig)
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
        """Compute and display the PSD plot, skipping if the data object is unchanged."""
        if self.current_data is None:
            return
        data_id = id(self.current_data)
        if data_id == self.psd_data_id:
            return
        try:
            fig_psd = self.current_data.compute_psd(fmax=60).plot(show=False)
            disable_psd_span_popups(fig_psd)
            self.plot_psd.update_figure(fig_psd)
            self.psd_data_id = data_id
        except Exception as e:
            logger.warning("PSD plot update failed: %s", e)

    def update_evoked_plot(self):
        """Render an evoked waveform plot."""
        if self.current_data is None or not isinstance(self.current_data, mne.Evoked):
            return
        try:
            fig_evoked = self.current_data.plot(show=False)
            self.plot_evoked.update_figure(fig_evoked)
        except Exception as e:
            logger.warning("Evoked plot update failed: %s", e)

    def update_sensors_plot(self):
        """Render a sensor map plot for the current data object."""
        if self.current_data is None:
            return
        try:
            if isinstance(self.current_data, mne.Evoked):
                fig_sensors = mne.viz.plot_sensors(
                    self.current_data.info,
                    show=False,
                    show_names=True,
                    kind="3d",
                )
            else:
                fig_sensors = self.current_data.plot_sensors(show=False, show_names=True, kind="3d")
            self.plot_sensors.update_figure(fig_sensors)
        except Exception as e:
            logger.warning("Sensor plot update failed: %s", e)

    def update_topomap_plot(self):
        """Compute and display the PSD topomap, skipping if the data object is unchanged."""
        if self.current_data is None:
            return
        data_id = id(self.current_data)
        if data_id == self.topomap_data_id:
            return
        try:
            if isinstance(self.current_data, mne.Evoked):
                fig_topo = self.current_data.plot_topomap(times="auto", show=False)
            else:
                fig_topo = self.current_data.compute_psd(fmax=60).plot_topomap(show=False)
            self.plot_topomap.update_figure(fig_topo)
            self.topomap_data_id = data_id
        except Exception as e:
            logger.warning("Topomap plot update failed: %s", e)

    def update_image_plot(self):
        """Render an epochs image plot (Epochs mode only)."""
        if self.current_data is None or not isinstance(self.current_data, mne.Epochs):
            return
        try:
            figs = self.current_data.plot_image(show=False)
            if figs:
                self.plot_image.update_figure(figs[0])
        except Exception as e:
            logger.warning("Epochs image plot update failed: %s", e)
