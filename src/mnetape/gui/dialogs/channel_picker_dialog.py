"""Shared channel picker dialog. Meant to be used by actions.

Interactive dialog with a sensor map and embedded raw time-series preview for manually selecting channels.
"""

import logging

import numpy as np
from matplotlib.path import Path as MplPath
import mne

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from mnetape.gui.utils import refresh_mne_browser_bads
from mnetape.gui.widgets.common import sanitize_mne_browser_toolbar

logger = logging.getLogger(__name__)

YELLOW_COLOR = np.array([1.0, 0.85, 0.0, 1.0])


class ChannelPickerDialog(QDialog):
    """Interactive dialog for selecting channels from a sensor map and raw time-series preview.

    Shows a side-by-side view: left panel contains an MNE sensor-map figure with lasso and
    click selection; right panel embeds a raw time-series browser.
    Selections are kept in sync between both panels via a polling timer.

    Args:
        raw: The MNE Raw object whose channels can be selected.
        selected: Pre-selected channel names to show as selected on open.
        parent: Optional parent widget.
        title: Dialog window title.
    """

    def __init__(self, raw: mne.io.Raw, selected: list[str] = None, parent=None, title: str = "Select Channels"):
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

        self.off_map_selected: list[str] = []

        self.setWindowTitle(title)
        self.setMinimumSize(1200, 600)

        layout = QVBoxLayout(self)
        info = QLabel("Click or lasso channels on the sensor map to select channels.")
        info.setWordWrap(True)
        layout.addWidget(info)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        layout.addWidget(splitter, 1)

        sensor_panel = QWidget(self)
        left_layout = QVBoxLayout(sensor_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(QLabel("Sensor Map"))
        self.build_sensor_panel(left_layout)
        # Channels in initial_selected that have no sensor position are invisible to the lasso picker.
        # Track them separately so they survive sync.
        lasso_names = set(getattr(self.lasso, "names", [])) if self.lasso is not None else set()
        self.off_map_selected = [ch for ch in self.initial_selected if ch not in lasso_names]
        splitter.addWidget(sensor_panel)

        raw_panel = QWidget(self)
        right_layout = QVBoxLayout(raw_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.addWidget(QLabel("Raw Time Series Preview"))
        self.build_raw_preview(right_layout)
        splitter.addWidget(raw_panel)
        splitter.setSizes([650, 550])

        footer_layout = QHBoxLayout()
        self.selected_label = QLabel()
        self.selected_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        footer_layout.addWidget(self.selected_label, 1)

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

        self.selection_timer = QTimer(self)
        self.selection_timer.timeout.connect(self.pull_selection_from_raw_preview)
        self.selection_timer.start(150)
        self.apply_initial_selection()
        self.sync_selection()

    def build_sensor_panel(self, parent_layout: QVBoxLayout) -> None:
        try:
            self.sensor_figure, _ = self.raw.plot_sensors(kind="select", show=False, show_names=True)
        except (RuntimeError, ValueError) as e:
            logger.warning("Sensor map unavailable: %s", e)
            lbl = QLabel("No channel positions available.\nSelect channels from the list below.")
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setStyleSheet("color: gray; font-size: 11px;")
            parent_layout.addWidget(lbl, 1)
            self.sensor_figure = None
            self.sensor_canvas = None
            self.lasso = None
            return

        self.sensor_canvas = self.sensor_figure.canvas
        parent_layout.addWidget(self.sensor_canvas, 1)

        self.lasso = getattr(self.sensor_figure, "lasso", None)
        if self.lasso is None:
            logger.warning("No lasso object found on sensor picker figure")
            return

        self.sensor_canvas.mpl_connect("pick_event", self.on_pick_event)
        callbacks = getattr(self.lasso, "callbacks", None)
        if isinstance(callbacks, list):
            callbacks.append(self.sync_selection)
        self.patch_lasso_colors()
        self.patch_lasso_selection_mode()

    def build_raw_preview(self, parent_layout: QVBoxLayout) -> None:
        n_channels = min(len(self.raw.ch_names), 20)
        self.raw_preview = self.raw.plot(show=False, duration=10.0, n_channels=n_channels, block=False)
        sanitize_mne_browser_toolbar(self.raw_preview, allow_annotation_mode=False)

        if hasattr(self.raw_preview, "setParent"):
            self.raw_preview.setParent(parent_layout.parentWidget())
            self.raw_preview.setWindowFlags(Qt.WindowType.Widget)
            parent_layout.addWidget(self.raw_preview, 1)
            self.base_preview_bads = set(self.raw_preview.mne.info.get("bads", []))
        else:
            parent_layout.addWidget(self.raw_preview.canvas, 1)

    def patch_lasso_colors(self) -> None:
        if self.lasso is None:
            return
        lasso = self.lasso
        original_fc = lasso.fc.copy()
        orig_style = lasso.style_objects

        def _styled():
            orig_style()
            lasso.fc[:] = original_fc
            if len(lasso.selection_inds) > 0:
                lasso.fc[lasso.selection_inds] = YELLOW_COLOR
            lasso.collection.set_facecolors(lasso.fc)
            lasso.canvas.draw_idle()

        lasso.style_objects = _styled

    def patch_lasso_selection_mode(self) -> None:
        if self.lasso is None:
            return

        def _on_select_append(verts):
            mods = QApplication.keyboardModifiers()
            ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
            shift = bool(mods & Qt.KeyboardModifier.ShiftModifier)
            if len(verts) <= 3 and not ctrl:
                self.sensor_canvas.draw_idle()
                return
            path = MplPath(verts)
            inds = np.nonzero([path.intersects_path(p) for p in self.lasso.paths])[0]
            current = np.asarray(getattr(self.lasso, "selection_inds", []), dtype=int)
            if ctrl and shift:
                new_inds = np.setdiff1d(current, inds).astype(int)
            else:
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
        if not self.initial_selected or self.lasso is None:
            return
        names = np.asarray(getattr(self.lasso, "names", []), dtype=object)
        if names.size == 0:
            return
        indices = np.flatnonzero(np.isin(names, self.initial_selected))
        self.lasso.select_many(indices.tolist())

    def on_pick_event(self, event) -> None:
        if self.lasso is None:
            return
        inds = getattr(event, "ind", None)
        if inds is None or len(inds) == 0:
            return
        self.toggle_index(int(inds[0]))

    def toggle_index(self, ind: int) -> None:
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
        all_selected = self.get_selected()
        if all_selected:
            self.selected_label.setText(f"Selected: {', '.join(all_selected)}")
        else:
            self.selected_label.setText("No channels selected")

    def sync_selection(self, *_args) -> None:
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
        if self.raw_preview is None or not hasattr(self.raw_preview, "mne"):
            return
        try:
            self.sync_guard = True
            managed = set(self.selected) | set(self.off_map_selected)
            bads = sorted(self.base_preview_bads | managed)
            self.raw_preview.mne.info["bads"] = bads
            refresh_mne_browser_bads(self.raw_preview, set(bads))
        except Exception as e:
            logger.debug("Failed to push channel selection to raw preview: %s", e, exc_info=True)
        finally:
            self.sync_guard = False

    def pull_selection_from_raw_preview(self) -> None:
        if self.raw_preview is None or not hasattr(self.raw_preview, "mne"):
            return
        if self.sync_guard:
            return
        try:
            self.sync_guard = True
            bads = set(self.raw_preview.mne.info.get("bads", []))
            all_selected = [ch for ch in self.raw.ch_names if ch in bads and ch not in self.base_preview_bads]
            lasso_names = set(getattr(self.lasso, "names", [])) if self.lasso is not None else set()
            new_on_map = [ch for ch in all_selected if ch in lasso_names]
            new_off_map = [ch for ch in all_selected if ch not in lasso_names]
            if new_on_map == self.selected and new_off_map == self.off_map_selected:
                return
            self.selected = new_on_map
            self.off_map_selected = new_off_map
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
        if self.lasso is not None:
            self.lasso.select_many([])
            self.lasso.notify()
        self.sync_selection()

    def get_selected(self) -> list[str]:
        keep = set(self.selected) | set(self.off_map_selected)
        return [ch for ch in self.raw.ch_names if ch in keep]

    def closeEvent(self, event):
        if self.selection_timer.isActive():
            self.selection_timer.stop()
        if self.raw_preview is not None and hasattr(self.raw_preview, "close"):
            try:
                self.raw_preview.close()
            except Exception as e:
                logger.debug("Failed to close raw preview cleanly %s", e, exc_info=True)
        super().closeEvent(event)
