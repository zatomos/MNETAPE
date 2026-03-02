"""Crop action widgets.

Viewer to interactively select a time range to keep, with a scrollable overview of all channels and red highlights
on the dropped parts of the recording.
"""

from __future__ import annotations

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.widgets import SpanSelector
from PyQt6.QtCore import QEvent, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import (
    QAbstractSpinBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollBar,
    QVBoxLayout,
    QWidget,
)

MAX_SAMPLES = 10_000        # Max samples to plot
N_VISIBLE = 20              # Visible channels at once
CH_SPACING = 2.5            # Vertical spacing between channels
SNAP_SECS = 1.0             # Snap crop edges to 0 or end if within this many seconds


# -------- Range slider --------

class RangeSlider(QWidget):
    """Horizontal slider with two draggable handles for selecting a sub-range of the recording.

    Signals:
        rangeChanged (lo, hi): emitted while dragging.
    """

    rangeChanged = pyqtSignal(float, float)

    HANDLE_WIDTH = 16
    TRACK_HEIGHT = 10

    def __init__(self, parent=None):
        super().__init__(parent)
        self.lo = 0.0
        self.hi = 1.0
        self.drag: str | None = None
        self.drag_x0 = 0.0
        self.drag_lo0 = 0.0
        self.drag_hi0 = 0.0
        self.setMinimumHeight(24)
        self.setMouseTracking(True)

    def set_range(self, lo: float, hi: float):
        self.lo = max(0.0, min(1.0, lo))
        self.hi = max(self.lo + 0.001, min(1.0, hi))
        self.update()

    def to_x(self, frac: float) -> float:
        return self.HANDLE_WIDTH / 2 + frac * (self.width() - self.HANDLE_WIDTH)

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        hw, th = self.HANDLE_WIDTH, self.TRACK_HEIGHT
        mid = h // 2

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#F2F2F4"))
        p.drawRoundedRect(hw // 2, mid - th // 2, w - hw, th, 3, 3)

        x_lo = int(self.to_x(self.lo))
        x_hi = int(self.to_x(self.hi))
        p.setBrush(QColor("#B8B8BF"))
        p.drawRect(x_lo, mid - th // 2, x_hi - x_lo, th)

        p.setPen(QPen(QColor("#8C8C97"), 1))
        p.setBrush(QColor("#B8B8BF"))
        for x in (x_lo, x_hi):
            p.drawRoundedRect(x - hw // 2, mid - hw // 2, hw, hw, 3, 3)

        p.end()

    def mousePressEvent(self, event):
        x = event.position().x()
        x_lo = self.to_x(self.lo)
        x_hi = self.to_x(self.hi)
        if abs(x - x_lo) <= self.HANDLE_WIDTH:
            self.drag = "lo"
        elif abs(x - x_hi) <= self.HANDLE_WIDTH:
            self.drag = "hi"
        elif x_lo < x < x_hi:
            self.drag = "mid"
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
        self.drag_x0 = x
        self.drag_lo0 = self.lo
        self.drag_hi0 = self.hi

    def mouseMoveEvent(self, event):
        x = event.position().x()
        if self.drag is None:
            x_lo = self.to_x(self.lo)
            x_hi = self.to_x(self.hi)
            if abs(x - x_lo) <= self.HANDLE_WIDTH or abs(x - x_hi) <= self.HANDLE_WIDTH:
                self.setCursor(Qt.CursorShape.SizeHorCursor)
            elif x_lo < x < x_hi:
                self.setCursor(Qt.CursorShape.OpenHandCursor)
            else:
                self.setCursor(Qt.CursorShape.ArrowCursor)
            return

        dfrac = (x - self.drag_x0) / max(1, self.width() - self.HANDLE_WIDTH)
        if self.drag == "lo":
            self.lo = max(0.0, min(self.drag_hi0 - 0.001, self.drag_lo0 + dfrac))
        elif self.drag == "hi":
            self.hi = max(self.drag_lo0 + 0.001, min(1.0, self.drag_hi0 + dfrac))
        elif self.drag == "mid":
            w = self.drag_hi0 - self.drag_lo0
            lo = max(0.0, min(1.0 - w, self.drag_lo0 + dfrac))
            self.lo, self.hi = lo, lo + w
        self.update()
        self.rangeChanged.emit(self.lo, self.hi)

    def mouseReleaseEvent(self, _event):
        self.drag = None
        self.setCursor(Qt.CursorShape.ArrowCursor)


# -------- Crop dialog --------

class CropDialog(QDialog):
    """Interactive crop range selector.

    - Main plot displays EEG channels.
    - Red zones highlight the parts of the recording that will be dropped.
    - SpanSelector to define the kept range.
    - RangeSlider to zoom the view.

    Args:
        raw: The MNE Raw object.
        tmin: Initial crop start in seconds.
        tmax: Initial crop end in seconds (None = end of recording).
        parent: Optional parent QWidget.
    """

    def __init__(self, raw, tmin: float = 0.0, tmax: float | None = None, parent=None):
        super().__init__(parent)
        self.raw = raw
        self.rec_end: float = float(raw.times[-1])
        self.setWindowTitle("Crop Recording")
        self.setMinimumSize(900, 580)
        self.setSizeGripEnabled(True)

        self.tmin: float = float(tmin)
        self.tmax: float = float(tmax) if tmax is not None else self.rec_end
        has_prior = not (self.tmin == 0.0 and abs(self.tmax - self.rec_end) < 0.001)
        self.updating = False
        self.wheel_accum = 0

        n_ch = len(raw.ch_names)
        self.n_visible = min(N_VISIBLE, n_ch)
        self.ch_offset = 0
        self.vis_start = 0
        self.vis_end = n_ch

        # ---- Data ----
        step = max(1, raw.n_times // MAX_SAMPLES)
        times = raw.times[::step]
        data = raw.get_data()[:, ::step]
        scale = np.percentile(np.abs(data), 95, axis=1, keepdims=True)
        scale[scale == 0] = 1.0
        data_norm = data / scale

        # ---- Figure ----
        self.fig, self.ax = plt.subplots(figsize=(12, 5))
        self.fig.subplots_adjust(left=0.08, right=0.98, top=0.97, bottom=0.1)

        # Plot channels
        self.lines = [
            self.ax.plot(times, data_norm[i] - i * CH_SPACING, color="#000000", linewidth=0.6)[0]
            for i in range(n_ch)
        ]

        self.ax.set_xlabel("Time (s)")
        self.ax.set_xlim(0.0, self.rec_end)
        self.update_yaxis()
        self.ax.set_autoscale_on(False)

        # Red zones
        self.left_red = self.ax.axvspan(0, self.tmin, facecolor="#EF5350", alpha=0.15, zorder=1)
        self.right_red = self.ax.axvspan(self.tmax, self.rec_end, facecolor="#EF5350", alpha=0.15, zorder=1)

        self.canvas = FigureCanvasQTAgg(self.fig)
        self.canvas.installEventFilter(self)

        # ---- SpanSelector ----
        self.span = SpanSelector(
            self.ax,
            self.on_span_move,
            "horizontal",
            useblit=False,
            props={"facecolor": "#8ac211", "alpha": 0.2},
            interactive=True,
            ignore_event_outside=True,
            onmove_callback=self.on_span_move,
        )
        if has_prior:
            self.span.extents = (self.tmin, self.tmax)
        self.canvas.draw_idle()

        # ---- Layout ----
        outer = QVBoxLayout(self)

        plot_row = QHBoxLayout()
        plot_row.addWidget(self.canvas, 1)

        # Vertical scrollbar
        self.ch_scroll = QScrollBar(Qt.Orientation.Vertical)
        self.ch_scroll.setRange(0, max(0, n_ch - self.n_visible))
        self.ch_scroll.setSingleStep(1)
        self.ch_scroll.setPageStep(self.n_visible)
        self.ch_scroll.valueChanged.connect(self.on_ch_scroll)
        self.ch_scroll.installEventFilter(self)
        plot_row.addWidget(self.ch_scroll)
        outer.addLayout(plot_row, 1)

        # Horizontal range slider
        self.range_slider = RangeSlider()
        self.range_slider.setFixedHeight(24)
        self.range_slider.rangeChanged.connect(self.on_range_changed)
        outer.addWidget(self.range_slider)

        # Time range spinboxes
        form = QFormLayout()
        self.spin_tmin = make_spinbox(0.0, self.rec_end, self.tmin)
        self.spin_tmax = make_spinbox(0.0, self.rec_end, self.tmax)
        form.addRow("Start (s):", self.spin_tmin)
        form.addRow("End (s):", self.spin_tmax)

        # Duration label
        dur_row = QHBoxLayout()
        self.dur_label = QLabel()
        dur_row.addStretch()
        dur_row.addWidget(self.dur_label)

        outer.addLayout(form)
        outer.addLayout(dur_row)
        self.update_dur_label()

        self.spin_tmin.valueChanged.connect(self.on_spinbox_changed)
        self.spin_tmax.valueChanged.connect(self.on_spinbox_changed)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)


    # -------- Channel scrolling handling --------

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.Wheel and obj in (self.canvas, self.ch_scroll):
            if obj is self.canvas:
                self.wheel_accum += event.angleDelta().y()
                steps = self.wheel_accum // 120          # 120 units per step (one notch)
                if steps:
                    self.wheel_accum -= steps * 120
                    self.ch_scroll.setValue(self.ch_scroll.value() - steps)
            return True
        return super().eventFilter(obj, event)


    # -------- View helpers --------

    def update_yaxis(self):
        start = self.ch_offset
        end = min(len(self.lines), start + self.n_visible)

        # Only update visibility of lines that changed
        for i in range(self.vis_start, min(start, self.vis_end)):
            self.lines[i].set_visible(False)
        for i in range(max(end, self.vis_start), self.vis_end):
            self.lines[i].set_visible(False)
        for i in range(start, min(self.vis_start, end)):
            self.lines[i].set_visible(True)
        for i in range(max(self.vis_end, start), end):
            self.lines[i].set_visible(True)

        self.vis_start, self.vis_end = start, end
        pad = CH_SPACING * 0.5
        self.ax.set_ylim(-(end - 1) * CH_SPACING - pad, - start * CH_SPACING + pad)
        self.ax.set_yticks([-i * CH_SPACING for i in range(start, end)])
        self.ax.set_yticklabels([self.raw.ch_names[i] for i in range(start, end)], fontsize=7)

    def update_red_zones(self):
        self.left_red.set_width(self.tmin)
        self.right_red.set_x(self.tmax)
        self.right_red.set_width(self.rec_end - self.tmax)

    def update_dur_label(self):
        self.dur_label.setText(
            f"Duration: {self.tmax - self.tmin:.3f} s  /  {self.rec_end:.3f} s total"
        )


    # -------- Signals --------

    def on_ch_scroll(self, value: int):
        """Called when the channel scrollbar is moved, updates the vertical position of the channels."""
        self.ch_offset = value
        self.update_yaxis()
        self.canvas.draw_idle()

    def on_range_changed(self, lo: float, hi: float):
        """Called when the range slider is changed, updates the horizontal zoom level."""
        self.ax.set_xlim(lo * self.rec_end, hi * self.rec_end)
        self.canvas.draw_idle()

    def on_span_move(self, xmin: float, xmax: float):
        """Called while the span is dragged and on release. Updates the crop range and the red zones."""

        # Guard against recursive updates
        if self.updating:
            return
        self.updating = True

        try:
            tmin = max(0.0, xmin)
            tmax = min(self.rec_end, xmax)
            if tmin < SNAP_SECS:
                tmin = 0.0
            if tmax > self.rec_end - SNAP_SECS:
                tmax = self.rec_end
            if tmin != xmin or tmax != xmax:
                self.span.extents = (tmin, tmax)
            self.tmin, self.tmax = tmin, tmax
            self.spin_tmin.setValue(tmin)
            self.spin_tmax.setValue(tmax)
            self.update_red_zones()
            self.update_dur_label()
        finally:
            self.updating = False

    def on_spinbox_changed(self):
        """Called when either spinbox is changed, updates the crop range and the red zones."""

        # Guard against recursive updates
        if self.updating:
            return
        self.updating = True

        try:
            tmin = self.spin_tmin.value()
            tmax = self.spin_tmax.value()
            if tmin >= tmax:
                if self.sender() is self.spin_tmin:
                    tmax = min(self.rec_end, tmin + 0.001)
                    self.spin_tmax.setValue(tmax)
                else:
                    tmin = max(0.0, tmax - 0.001)
                    self.spin_tmin.setValue(tmin)
            self.tmin, self.tmax = tmin, tmax
            self.span.extents = (tmin, tmax)
            self.update_red_zones()
            self.canvas.draw_idle()
            self.update_dur_label()
        finally:
            self.updating = False


    # -------- Result --------

    def get_range(self) -> tuple[float, float]:
        return self.tmin, self.tmax

    def run(self) -> tuple[float, float] | None:
        result = self.exec()
        plt.close(self.fig)
        if result == QDialog.DialogCode.Accepted:
            return self.get_range()
        return None


# -------- Shared helpers --------

def make_spinbox(min_val: float, max_val: float, value: float) -> QDoubleSpinBox:
    sb = QDoubleSpinBox()
    sb.setRange(min_val, max_val)
    sb.setDecimals(3)
    sb.setSingleStep(0.1)
    sb.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.UpDownArrows)
    sb.setValue(value)
    return sb


# -------- Param widget factory --------

def crop_factory(_param_def, current_value, raw, parent):
    max_t = float(raw.times[-1]) if raw is not None else 999999.0
    spinbox = make_spinbox(0.0, max_t, float(current_value) if current_value else max_t)

    btn = QPushButton("Crop...")
    btn.setEnabled(raw is not None)

    # Layout: spinbox on the left, button on the right
    btn_row = QHBoxLayout()
    btn_row.addStretch()
    btn_row.addWidget(btn)

    # Spinbox on top, button row below
    container = QWidget()
    layout = QVBoxLayout(container)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(4)
    layout.addWidget(spinbox)
    layout.addLayout(btn_row)

    def crop():
        tmin_widget = parent.param_widgets.get("tmin")
        tmax_widget = parent.param_widgets.get("tmax")
        dlg = CropDialog(raw, tmin=tmin_widget.value(), tmax=tmax_widget.value(), parent=parent)
        result = dlg.run()
        if result is None:
            return
        new_tmin, new_tmax = result
        tmin_widget.setValue(new_tmin)
        tmax_widget.setValue(new_tmax)

    btn.clicked.connect(crop)

    return container, spinbox
