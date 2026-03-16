"""Crop action widgets.

Viewer to interactively select a time range to keep, with a scrollable overview of all channels and red highlights
on the dropped parts of the recording.
"""

from __future__ import annotations

import matplotlib.pyplot as plt
import numpy as np


from gi.repository import Adw, Gtk
from matplotlib.backends.backend_gtk4agg import FigureCanvasGTK4Agg
from matplotlib.widgets import SpanSelector

from mnetape.actions.base import ParamWidgetBinding
from mnetape.gui.dialogs.base import ModalDialog

MAX_SAMPLES = 10_000        # Max samples to plot
N_VISIBLE = 20              # Visible channels at once
CH_SPACING = 2.5            # Vertical spacing between channels
SNAP_SECS = 1.0             # Snap crop edges to 0 or end if within this many seconds

# -------- Crop dialog --------

class CropDialog(ModalDialog):
    """Interactive crop range selector using GTK4 + matplotlib."""

    def __init__(self, raw, tmin: float = 0.0, tmax: float | None = None, parent_window=None):
        self.raw = raw
        self.rec_end: float = float(raw.times[-1])

        self.tmin: float = float(tmin)
        self.tmax: float = float(tmax) if tmax is not None else self.rec_end
        has_prior = not (self.tmin == 0.0 and abs(self.tmax - self.rec_end) < 0.001)
        self.updating = False

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

        self.lines = [
            self.ax.plot(times, data_norm[i] - i * CH_SPACING, color="#000000", linewidth=0.6)[0]
            for i in range(n_ch)
        ]

        self.ax.set_xlabel("Time (s)")
        self.ax.set_xlim(0.0, self.rec_end)
        self.update_yaxis()
        self.ax.set_autoscale_on(False)

        self.left_red = self.ax.axvspan(0, self.tmin, facecolor="#EF5350", alpha=0.15, zorder=1)
        self.right_red = self.ax.axvspan(self.tmax, self.rec_end, facecolor="#EF5350", alpha=0.15, zorder=1)

        # ---- Build dialog ----
        dialog = Adw.Dialog()
        dialog.set_title("Crop Recording")
        dialog.set_content_width(950)
        self.dialog = dialog

        toolbar_view = Adw.ToolbarView()
        toolbar_view.add_top_bar(Adw.HeaderBar())
        dialog.set_child(toolbar_view)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        outer.set_margin_start(12)
        outer.set_margin_end(12)
        outer.set_margin_top(8)
        outer.set_margin_bottom(8)
        toolbar_view.set_content(outer)

        # Canvas in a scrolled window
        canvas_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        canvas_box.set_vexpand(True)

        self.canvas = FigureCanvasGTK4Agg(self.fig)
        self.canvas.set_hexpand(True)
        self.canvas.set_vexpand(True)
        self.canvas.set_size_request(700, 380)
        canvas_box.append(self.canvas)

        # Vertical scrollbar
        self.vadj = Gtk.Adjustment(
            value=0,
            lower=0,
            upper=max(0, n_ch - self.n_visible),
            step_increment=1,
            page_increment=self.n_visible,
        )
        vscroll = Gtk.Scrollbar(orientation=Gtk.Orientation.VERTICAL, adjustment=self.vadj)
        canvas_box.append(vscroll)
        self.vadj.connect("value-changed", self.on_vscroll)

        outer.append(canvas_box)

        # Time spinboxes
        form_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        form_box.set_margin_top(4)

        tmin_adj = Gtk.Adjustment(value=self.tmin, lower=0.0, upper=self.rec_end,
                                  step_increment=0.001, page_increment=0.1)
        self.spin_tmin = Gtk.SpinButton(adjustment=tmin_adj, climb_rate=1.0, digits=3)
        self.spin_tmin.connect("value-changed", self.on_spinbox_changed)
        form_box.append(Gtk.Label(label="Start (s):"))
        form_box.append(self.spin_tmin)

        tmax_adj = Gtk.Adjustment(value=self.tmax, lower=0.0, upper=self.rec_end,
                                  step_increment=0.001, page_increment=0.1)
        self.spin_tmax = Gtk.SpinButton(adjustment=tmax_adj, climb_rate=1.0, digits=3)
        self.spin_tmax.connect("value-changed", self.on_spinbox_changed)
        form_box.append(Gtk.Label(label="End (s):"))
        form_box.append(self.spin_tmax)

        self.dur_label = Gtk.Label()
        self.update_dur_label()
        form_box.append(self.dur_label)

        outer.append(form_box)

        # SpanSelector
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

        # Buttons
        btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        btn_row.set_halign(Gtk.Align.END)
        cancel_btn = Gtk.Button(label="Cancel")
        cancel_btn.connect("clicked", self.reject)
        ok_btn = Gtk.Button(label="OK")
        ok_btn.add_css_class("suggested-action")
        ok_btn.connect("clicked", self.accept)
        btn_row.append(cancel_btn)
        btn_row.append(ok_btn)
        outer.append(btn_row)

        self.setup_modal(parent_window)

    def update_yaxis(self):
        start = self.ch_offset
        end = min(len(self.lines), start + self.n_visible)
        for i, line in enumerate(self.lines):
            line.set_visible(start <= i < end)
        self.vis_start, self.vis_end = start, end
        pad = CH_SPACING * 0.5
        self.ax.set_ylim(-(end - 1) * CH_SPACING - pad, -start * CH_SPACING + pad)
        self.ax.set_yticks([-i * CH_SPACING for i in range(start, end)])
        self.ax.set_yticklabels([self.raw.ch_names[i] for i in range(start, end)], fontsize=7)

    def update_red_zones(self):
        self.left_red.set_width(self.tmin)
        self.right_red.set_x(self.tmax)
        self.right_red.set_width(self.rec_end - self.tmax)

    def update_dur_label(self):
        self.dur_label.set_text(
            f"Duration: {self.tmax - self.tmin:.3f} s  /  {self.rec_end:.3f} s total"
        )

    def on_vscroll(self, adj):
        self.ch_offset = int(adj.get_value())
        self.update_yaxis()
        self.canvas.draw_idle()

    def on_span_move(self, xmin: float, xmax: float):
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
            self.spin_tmin.set_value(tmin)
            self.spin_tmax.set_value(tmax)
            self.update_red_zones()
            self.update_dur_label()
        finally:
            self.updating = False

    def on_spinbox_changed(self, _spin):
        if self.updating:
            return
        self.updating = True
        try:
            tmin = self.spin_tmin.get_value()
            tmax = self.spin_tmax.get_value()
            if tmin >= tmax:
                if _spin is self.spin_tmin:
                    tmax = min(self.rec_end, tmin + 0.001)
                    self.spin_tmax.set_value(tmax)
                else:
                    tmin = max(0.0, tmax - 0.001)
                    self.spin_tmin.set_value(tmin)
            self.tmin, self.tmax = tmin, tmax
            self.span.extents = (tmin, tmax)
            self.update_red_zones()
            self.canvas.draw_idle()
            self.update_dur_label()
        finally:
            self.updating = False

    def on_closed(self, *_) -> None:
        plt.close(self.fig)
        self.loop.quit()

    def get_range(self) -> tuple[float, float]:
        return self.tmin, self.tmax

    def run(self) -> tuple[float, float] | None:
        if ModalDialog.exec(self):
            return self.get_range()
        return None

# -------- Param widget factory --------

def crop_factory(current_value, raw, param_widgets=None):
    max_t = float(raw.times[-1]) if raw is not None else 999999.0
    init_val = float(current_value) if current_value is not None else max_t

    adj = Gtk.Adjustment(value=init_val, lower=0.0, upper=max_t,
                         step_increment=0.001, page_increment=0.1)
    spinbox = Gtk.SpinButton(adjustment=adj, climb_rate=1.0, digits=3)
    spinbox.set_hexpand(True)

    btn = Gtk.Button(label="Crop...")
    btn.set_sensitive(raw is not None)

    container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    container.set_hexpand(True)
    container.append(spinbox)

    btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
    btn_row.set_halign(Gtk.Align.END)
    btn_row.append(btn)
    container.append(btn_row)

    def crop(_btn):
        parent_window = spinbox.get_root()
        tmin_spin = param_widgets.get("tmin") if param_widgets else None
        tmin_val = tmin_spin.get_value() if tmin_spin is not None else 0.0
        dlg = CropDialog(raw, tmin=tmin_val, tmax=spinbox.get_value(),
                         parent_window=parent_window)
        result = dlg.run()
        if result is not None:
            new_tmin, new_tmax = result
            if tmin_spin is not None:
                tmin_spin.set_value(new_tmin)
            spinbox.set_value(new_tmax)

    btn.connect("clicked", crop)

    return container, spinbox

# -------- Widget bindings --------

WIDGET_BINDINGS = [
    ParamWidgetBinding("tmax", crop_factory),
]
