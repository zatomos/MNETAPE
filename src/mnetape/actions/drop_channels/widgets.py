"""Widgets for the drop-channels action.

Provides:
    BadChannelDetectorDialog: Runs pyprep's NoisyChannels in a background thread to automatically detect bad channels.
    ChannelPickerDialog: Interactive dialog with a sensor map and embedded raw preview for manually selecting channels
        to drop.
    channels_widget_factory: Param widget factory for the "channels" param type, combining a text field
        with "Pick..." and "Detect..." buttons.
"""

import logging
import numpy as np

from PyQt6.QtCore import Qt, QTimer, QThread, pyqtSignal, QObject
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QVBoxLayout,
    QWidget,
)
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.path import Path as MplPath
import mne

logger = logging.getLogger(__name__)

YELLOW_COLOR = np.array([1.0, 0.85, 0.0, 1.0])


class DetectionWorker(QObject):
    """QObject worker that runs pyprep's NoisyChannels in a background QThread.

    Signals:
        finished (list[str]): Emitted with the list of detected bad channel names when detection completes successfully.
        error (str): Emitted with the error message string when detection fails.
    """

    finished = pyqtSignal(list)
    error = pyqtSignal(str)

    def __init__(self, raw: mne.io.Raw, params: dict):
        super().__init__()
        self.raw = raw
        self.params = params

    def run(self):
        """Execute detection and emit finished or error on the worker thread."""
        try:
            self.run_pyprep()
        except Exception as exc:
            self.error.emit(str(exc))

    def run_pyprep(self):
        """Run NoisyChannels detection and emit finished with the detected bad channels list."""
        from pyprep import NoisyChannels

        nd = NoisyChannels(
            self.raw,
            do_detrend=self.params.get("do_detrend", True),
            random_state=self.params.get("random_state", 42),
        )
        nd.find_all_bads(ransac=self.params.get("ransac", True))
        bads = nd.get_bads()
        self.finished.emit(bads)


class BadChannelDetectorDialog(QDialog):
    """Dialog to auto-detect bad channels using pyprep."""
    def __init__(self, raw: mne.io.Raw, parent=None):
        super().__init__(parent)
        self.raw = raw
        self.detected: list[str] = []
        self.thread: QThread | None = None
        self.worker: DetectionWorker | None = None

        self.setWindowTitle("Auto-Detect Bad Channels")
        self.setMinimumWidth(420)

        layout = QVBoxLayout(self)

        # Method selector
        method_layout = QHBoxLayout()
        method_layout.addWidget(QLabel("Automated bad channels detection using pyprep's NoisyChannel."))
        layout.addLayout(method_layout)

        # pyprep params
        pyprep_form = QFormLayout()
        self.pyprep_ransac = QComboBox()
        self.pyprep_ransac.addItems(["Yes", "No"])
        pyprep_form.addRow("Include RANSAC:", self.pyprep_ransac)
        self.pyprep_detrend = QComboBox()
        self.pyprep_detrend.addItems(["Yes", "No"])
        pyprep_form.addRow("Detrend (<1 Hz):", self.pyprep_detrend)
        layout.addLayout(pyprep_form)

        # Run button
        self.btn_run = QPushButton("Run Detection")
        self.btn_run.clicked.connect(self.run_detection)
        layout.addWidget(self.btn_run)

        # Results
        self.result_label = QLabel("")
        self.result_label.setWordWrap(True)
        layout.addWidget(self.result_label)

        # OK / Cancel
        self.button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.button_box.button(QDialogButtonBox.StandardButton.Ok).setEnabled(False)
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)
        layout.addWidget(self.button_box)

    def gather_params(self) -> dict:
        """Collect pyprep detection parameters from the dialog widgets.

        Returns:
            Dict with pyprep params
        """
        return {
            "ransac": self.pyprep_ransac.currentText() == "Yes",
            "do_detrend": self.pyprep_detrend.currentText() == "Yes",
            "random_state": 42,
        }

    def run_detection(self):
        """Start the detection background thread and update the UI to a running state."""
        params = self.gather_params()

        # Disable UI while running
        self.btn_run.setEnabled(False)
        self.btn_run.setText("Running...")
        self.result_label.setStyleSheet("")
        self.result_label.setText("Detecting bad channels...")
        self.setCursor(Qt.CursorShape.WaitCursor)

        # Detection thread
        self.thread = QThread()
        self.worker = DetectionWorker(self.raw, params)
        self.worker.moveToThread(self.thread)

        # Connect signals
        self.thread.started.connect(self.worker.run)
        self.worker.finished.connect(self.on_detection_done)
        self.worker.error.connect(self.on_detection_error)
        self.worker.finished.connect(self.cleanup_thread)
        self.worker.error.connect(self.cleanup_thread)

        self.thread.start()

    def on_detection_done(self, bads: list[str]):
        """Handle successful detection and update the result label.

        Args:
            bads: List of detected bad channel names.
        """
        self.detected = bads
        self.unsetCursor()
        self.btn_run.setEnabled(True)
        self.btn_run.setText("Run Detection")
        if bads:
            self.result_label.setStyleSheet("color: #4EC9B0;")
            self.result_label.setText(
                f"Detected {len(bads)} bad channel(s):\n{', '.join(bads)}"
            )
            self.button_box.button(QDialogButtonBox.StandardButton.Ok).setEnabled(True)
        else:
            self.result_label.setStyleSheet("")
            self.result_label.setText("No bad channels detected.")
            self.button_box.button(QDialogButtonBox.StandardButton.Ok).setEnabled(False)

    def on_detection_error(self, msg: str):
        """Handle a detection failure and show the error in the result label.

        Args:
            msg: Human-readable error description.
        """
        self.detected = []
        self.unsetCursor()
        self.btn_run.setEnabled(True)
        self.btn_run.setText("Run Detection")
        self.result_label.setStyleSheet("color: #F44747;")
        self.result_label.setText(f"Error: {msg}")
        self.button_box.button(QDialogButtonBox.StandardButton.Ok).setEnabled(False)

    def cleanup_thread(self):
        """Quit and wait for the detection thread, then clear thread/worker references."""
        if self.thread is not None:
            self.thread.quit()
            self.thread.wait()
            self.thread = None
            self.worker = None

    def get_detected(self) -> list[str]:
        """Return a copy of the detected bad channels list after the dialog is accepted."""
        return list(self.detected)

    def closeEvent(self, event):
        self.cleanup_thread()
        super().closeEvent(event)


class ChannelPickerDialog(QDialog):
    """Dialog for interactively selecting channels to drop.

    Shows a side-by-side view: left panel contains an MNE sensor-map figure with lasso and click selection;
    right panel embeds a raw time-series browser.
    Selections are kept in sync between both panels via a polling timer.

    Args:
        raw: The MNE Raw object whose channels can be selected.
        selected: Pre-selected channel names to show as selected on open.
        parent: Optional parent widget.
    """

    def __init__(self, raw: mne.io.Raw, selected: list[str] = None, parent=None):
        super().__init__(parent)
        self.raw = raw
        self.initial_selected = [name for name in (selected or []) if name in raw.ch_names]
        self.selected: list[str] = []
        self.lasso = None
        self.sensor_canvas = None
        self.sensor_figure = None
        self.raw_preview = None
        self.base_preview_bads: set[str] = set()
        self.sync_guard = False

        self.setWindowTitle("Select Channels to Drop")
        self.setMinimumSize(1200, 600)

        # Info label
        layout = QVBoxLayout(self)
        info = QLabel("Click or lasso channels on the sensor map to select channels to drop.")
        info.setWordWrap(True)
        layout.addWidget(info)

        # Main splitter
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        layout.addWidget(splitter, 1)

        # Left: Sensor map
        sensor_panel = QWidget(self)
        left_layout = QVBoxLayout(sensor_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(QLabel("Sensor Map"))
        self.build_sensor_panel(left_layout)
        splitter.addWidget(sensor_panel)

        # Right: Raw preview
        raw_panel = QWidget(self)
        right_layout = QVBoxLayout(raw_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.addWidget(QLabel("Raw Time Series Preview"))
        self.build_raw_preview(right_layout)
        splitter.addWidget(raw_panel)
        splitter.setSizes([650, 550])

        # Footer with selection summary
        footer_layout = QHBoxLayout()
        self.selected_label = QLabel()
        self.selected_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        footer_layout.addWidget(self.selected_label, 1)

        # Bottom buttons
        self.btn_clear = QPushButton("Clear")
        self.btn_clear.clicked.connect(self.clear_selection)
        footer_layout.addWidget(self.btn_clear)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        footer_layout.addWidget(buttons)
        layout.addLayout(footer_layout)

        # Timer to sync selection from raw preview
        self.selection_timer = QTimer(self)
        self.selection_timer.timeout.connect(self.pull_selection_from_raw_preview)
        self.selection_timer.start(150)
        self.apply_initial_selection()
        self.sync_selection()

    def build_sensor_panel(self, parent_layout: QVBoxLayout) -> None:
        # Plot sensors
        self.sensor_figure, _ = self.raw.plot_sensors(kind="select", show=False, show_names=True)
        self.sensor_canvas = self.sensor_figure.canvas
        parent_layout.addWidget(self.sensor_canvas, 1)

        # Get the lasso selector
        self.lasso = getattr(self.sensor_figure, "lasso", None)
        if self.lasso is None:
            logger.warning("No lasso object found on sensor picker figure")
            return

        # Connect pick events for clicking channels
        self.sensor_canvas.mpl_connect("pick_event", self.on_pick_event)
        callbacks = getattr(self.lasso, "callbacks", None)
        if isinstance(callbacks, list):
            callbacks.append(self.sync_selection)
        self.patch_lasso_colors()
        self.patch_lasso_selection_mode()

    def build_raw_preview(self, parent_layout: QVBoxLayout) -> None:
        n_channels = min(len(self.raw.ch_names), 20)
        self.raw_preview = self.raw.plot(show=False, duration=10.0, n_channels=n_channels, block=False)

        if hasattr(self.raw_preview, "setParent"):
            # MNEQtBrowser embed directly as a widget
            self.raw_preview.setParent(parent_layout.parentWidget())
            self.raw_preview.setWindowFlags(Qt.WindowType.Widget)
            parent_layout.addWidget(self.raw_preview, 1)
            self.base_preview_bads = set(self.raw_preview.mne.info.get("bads", []))
        else:
            # Matplotlib figure fallback
            parent_layout.addWidget(self.raw_preview.canvas, 1)

    def patch_lasso_colors(self) -> None:
        """Override style_objects to color selected channels yellow."""
        if self.lasso is None:
            return

        lasso = self.lasso
        # Save the original base facecolor (first channel's color)
        original_fc = lasso.fc.copy()
        orig_style = lasso.style_objects  # bound method

        def _styled():
            orig_style()
            # Reset to original base colors first
            lasso.fc[:] = original_fc
            # Set selected to yellow
            if len(lasso.selection_inds) > 0:
                lasso.fc[lasso.selection_inds] = YELLOW_COLOR
            lasso.collection.set_facecolors(lasso.fc)
            lasso.canvas.draw_idle()

        lasso.style_objects = _styled

    def patch_lasso_selection_mode(self) -> None:
        """Make lasso append by default and redraw so overlay clears."""
        if self.lasso is None:
            return

        def _on_select_append(verts):
            mods = QApplication.keyboardModifiers()
            ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
            shift = bool(mods & Qt.KeyboardModifier.ShiftModifier)

            # If the lasso selection is very small and ctrl is not held, treat it as a toggle click.
            if len(verts) <= 3 and not ctrl:
                self.sensor_canvas.draw_idle()
                return

            path = MplPath(verts)
            inds = np.nonzero([path.intersects_path(p) for p in self.lasso.paths])[0]
            current = np.asarray(getattr(self.lasso, "selection_inds", []), dtype=int)

            if ctrl and shift:
                # Remove lassoed channels from current selection.
                new_inds = np.setdiff1d(current, inds).astype(int)
            else:
                # Add lassoed channels to current selection.
                new_inds = np.union1d(current, inds).astype(int)

            self.lasso.selection_inds = new_inds
            self.lasso.selection = [self.lasso.names[i] for i in new_inds]
            self.lasso.style_objects()
            self.lasso.notify()
            self.sensor_canvas.draw_idle()

        self.lasso.on_select = _on_select_append
        if hasattr(self.lasso, "lasso"):
            self.lasso.lasso.onselect = _on_select_append

    def apply_initial_selection(self) -> None:
        """Pre-select channels in the lasso that were passed as selected on construction."""
        if not self.initial_selected or self.lasso is None:
            return

        names = np.asarray(getattr(self.lasso, "names", []), dtype=object)

        if names.size == 0:
            return

        indices = np.flatnonzero(np.isin(names, self.initial_selected))
        self.lasso.select_many(indices.tolist())

    def on_pick_event(self, event) -> None:
        """Handle a matplotlib pick event on the sensor map.

        Args:
            event: The matplotlib PickEvent containing the picked artist indices.
        """
        if self.lasso is None:
            return

        inds = getattr(event, "ind", None)

        if inds is None or len(inds) == 0:
            return

        self.toggle_index(int(inds[0]))

    def toggle_index(self, ind: int) -> None:
        """Toggle the selection state of the channel at the given lasso index.

        Args:
            ind:  index into the lasso's names array.
        """
        if self.lasso is None:
            return

        current = np.asarray(getattr(self.lasso, "selection_inds", []), dtype=int)

        if ind in current:
            next_inds = current[current != ind]
        else:
            next_inds = np.sort(np.append(current, ind)).astype(int)

        self.lasso.selection_inds = next_inds
        self.lasso.selection = [self.lasso.names[i] for i in next_inds]
        self.lasso.style_objects()
        self.lasso.notify()
        self.sensor_canvas.draw_idle()

    def update_selected_label(self) -> None:
        """Refresh the footer label showing which channels are currently selected."""
        if self.selected:
            self.selected_label.setText(f"Selected: {', '.join(self.selected)}")
        else:
            self.selected_label.setText("No channels selected")

    def sync_selection(self, *_args) -> None:
        """Read the current lasso selection and push it to the raw preview."""
        if self.sync_guard:
            return

        if self.lasso is None:
            self.selected = []
        else:
            selection = [str(ch) for ch in getattr(self.lasso, "selection", [])]
            self.selected = [ch for ch in selection if ch in self.raw.ch_names]
        self.push_selection_to_raw_preview()
        self.update_selected_label()

    def push_selection_to_raw_preview(self) -> None:
        """Update the raw preview's bad channels list to match the current selection,
        while preserving any existing bads that were not selected.
        """

        if self.raw_preview is None or not hasattr(self.raw_preview, "mne"):
            return

        try:
            self.sync_guard = True
            managed = set(self.selected)
            self.raw_preview.mne.info["bads"] = sorted(self.base_preview_bads | managed)
            traces = getattr(self.raw_preview.mne, "traces", [])
            if traces:
                bads = set(self.raw_preview.mne.info["bads"])
                for trace in traces:
                    trace.isbad = trace.ch_name in bads
                    if hasattr(trace, "update_color"):
                        trace.update_color()
                if hasattr(self.raw_preview, "update_yaxis_labels"):
                    self.raw_preview.update_yaxis_labels()
            elif hasattr(self.raw_preview, "_redraw"):
                self.raw_preview._redraw(update_data=False)
            self.raw_preview.update()
        except Exception as e:
            logger.debug("Failed to push channel selection to raw preview: %s", e, exc_info=True)
        finally:
            self.sync_guard = False

    def pull_selection_from_raw_preview(self) -> None:
        """Check the raw preview's bad channel list and update the selection if it has changed."""
        if self.raw_preview is None or not hasattr(self.raw_preview, "mne"):
            return
        if self.sync_guard:
            return

        try:
            self.sync_guard = True
            bads = set(self.raw_preview.mne.info.get("bads", []))
            selected_from_preview = [
                ch for ch in self.raw.ch_names
                if ch in bads and ch not in self.base_preview_bads
            ]
            if selected_from_preview == self.selected:
                return
            self.selected = selected_from_preview
            if self.lasso is not None:
                names = np.asarray(getattr(self.lasso, "names", []), dtype=object)
                if names.size:
                    indices = np.flatnonzero(np.isin(names, self.selected))
                    self.lasso.selection_inds = indices.astype(int)
                    self.lasso.selection = [self.lasso.names[i] for i in indices]
                    self.lasso.style_objects()
                    if self.sensor_canvas is not None:
                        self.sensor_canvas.draw_idle()
            self.update_selected_label()
        except Exception as e:
            logger.debug("Failed to pull channel selection from raw preview: %s", e, exc_info=True)
        finally:
            self.sync_guard = False

    def clear_selection(self):
        """Deselect all channels in both the sensor map and the raw preview."""
        if self.lasso is not None:
            self.lasso.select_many([])
            self.lasso.notify()
        self.sync_selection()

    def get_selected(self) -> list[str]:
        """Return the selected channel names ordered by their position in raw.ch_names."""
        return [ch for ch in self.raw.ch_names if ch in set(self.selected)]

    def closeEvent(self, event):
        if self.selection_timer.isActive():
            self.selection_timer.stop()
        if self.raw_preview is not None and hasattr(self.raw_preview, "close"):
            try:
                self.raw_preview.close()
            except Exception as e:
                logger.debug("Failed to close raw preview cleanly %s", e, exc_info=True)
        super().closeEvent(event)


# -------- Param widget factory --------

def channels_widget_factory(param_def, current_value, raw, parent):
    """Build a widget for the 'channels' param type.

    Returns a (container, value_widget) pair:
        - container is a QWidget that the user can interact with to pick or detect channels
        - value_widget is a QLineEdit that holds the comma-separated channel names string.
    Both buttons are disabled when raw is None.

    Args:
        param_def: Parameter metadata dict (unused here, kept for factory protocol).
        current_value: Current channel list or string to pre-populate the text field.
        raw: The MNE Raw object to pass to the picker/detector dialogs. May be None.
        parent: Parent widget for the dialogs.

    Returns:
        Tuple of (container QWidget, QLineEdit value widget).
    """
    container = QWidget()
    layout = QHBoxLayout(container)
    layout.setContentsMargins(0, 0, 0, 0)

    line_edit = QLineEdit(str(current_value) if current_value is not None else "")
    layout.addWidget(line_edit, 1)

    btn_pick = QPushButton("Pick...")
    btn_pick.setEnabled(raw is not None)

    def pick():
        if raw is None:
            return
        selected = [c.strip() for c in line_edit.text().split(",") if c.strip()]
        dlg = ChannelPickerDialog(raw, selected, parent)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            line_edit.setText(", ".join(dlg.get_selected()))

    btn_pick.clicked.connect(pick)
    layout.addWidget(btn_pick)

    btn_detect = QPushButton("Detect...")
    btn_detect.setEnabled(raw is not None)

    def detect():
        if raw is None:
            return
        dlg = BadChannelDetectorDialog(raw, parent)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            detected = dlg.get_detected()
            if detected:
                existing = {c.strip() for c in line_edit.text().split(",") if c.strip()}
                merged = existing | set(detected)
                ordered = [ch for ch in raw.ch_names if ch in merged]
                line_edit.setText(", ".join(ordered))

    btn_detect.clicked.connect(detect)
    layout.addWidget(btn_detect)

    return container, line_edit
