"""Widgets for the drop-channels action.

Provides:
    BadChannelDetectorDialog: Runs pyprep's NoisyChannels in a background thread to automatically detect bad channels.
    ChannelPickerDialog: Interactive dialog with a sensor map and embedded raw preview for manually selecting channels
        to drop.
    channels_widget_factory: Param widget factory for the "channels" param type, combining a text field
        with "Pick..." and "Detect..." buttons.
"""

import logging
import threading

import numpy as np
from matplotlib.path import Path as MplPath
import mne


from gi.repository import Adw, GLib, Gtk
from matplotlib.backends.backend_gtk4agg import FigureCanvasGTK4Agg

from mnetape.actions.base import ParamWidgetBinding
from mnetape.gui.dialogs.base import ModalDialog
from mnetape.gui.utils import refresh_mne_browser_bads
from mnetape.gui.widgets.common import embed_mne_browser, sanitize_mne_browser_toolbar

logger = logging.getLogger(__name__)

YELLOW_COLOR = np.array([1.0, 0.85, 0.0, 1.0])

class BadChannelDetectorDialog(ModalDialog):
    """Dialog to auto-detect bad channels using pyprep."""

    def __init__(self, raw: mne.io.Raw, parent_window=None):
        self.raw = raw
        self.detected: list[str] = []

        self.dialog = Adw.Dialog()
        self.dialog.set_title("Auto-Detect Bad Channels")
        self.dialog.set_content_width(440)

        toolbar_view = Adw.ToolbarView()
        toolbar_view.add_top_bar(Adw.HeaderBar())
        self.dialog.set_child(toolbar_view)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        content.set_margin_start(16)
        content.set_margin_end(16)
        content.set_margin_top(12)
        content.set_margin_bottom(12)
        toolbar_view.set_content(content)

        info_lbl = Gtk.Label(label="Automated bad channels detection using pyprep's NoisyChannel.")
        info_lbl.set_wrap(True)
        info_lbl.set_xalign(0.0)
        content.append(info_lbl)

        # RANSAC option
        ransac_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        ransac_row.append(Gtk.Label(label="Include RANSAC:"))
        ransac_model = Gtk.StringList(strings=["Yes", "No"])
        self.ransac_dropdown = Gtk.DropDown(model=ransac_model)
        self.ransac_dropdown.set_selected(0)
        ransac_row.append(self.ransac_dropdown)
        content.append(ransac_row)

        # Detrend option
        detrend_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        detrend_row.append(Gtk.Label(label="Detrend (<1 Hz):"))
        detrend_model = Gtk.StringList(strings=["Yes", "No"])
        self.detrend_dropdown = Gtk.DropDown(model=detrend_model)
        self.detrend_dropdown.set_selected(0)
        detrend_row.append(self.detrend_dropdown)
        content.append(detrend_row)

        self.btn_run = Gtk.Button(label="Run Detection")
        self.btn_run.connect("clicked", self.on_run)
        content.append(self.btn_run)

        self.result_label = Gtk.Label(label="")
        self.result_label.set_wrap(True)
        self.result_label.set_xalign(0.0)
        content.append(self.result_label)

        # Spinner shown during detection
        self.spinner = Gtk.Spinner()
        self.spinner.set_visible(False)
        content.append(self.spinner)

        btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        btn_row.set_halign(Gtk.Align.END)
        cancel_btn = Gtk.Button(label="Cancel")
        cancel_btn.connect("clicked", self.reject)
        btn_row.append(cancel_btn)

        self.ok_btn = Gtk.Button(label="OK")
        self.ok_btn.add_css_class("suggested-action")
        self.ok_btn.set_sensitive(False)
        self.ok_btn.connect("clicked", self.accept)
        btn_row.append(self.ok_btn)
        content.append(btn_row)

        self.setup_modal(parent_window)

    def gather_params(self) -> dict:
        items = ["Yes", "No"]
        ransac_sel = self.ransac_dropdown.get_selected()
        detrend_sel = self.detrend_dropdown.get_selected()
        return {
            "ransac": (items[ransac_sel] if ransac_sel < 2 else "Yes") == "Yes",
            "do_detrend": (items[detrend_sel] if detrend_sel < 2 else "Yes") == "Yes",
            "random_state": 42,
        }

    def on_run(self, _btn):
        params = self.gather_params()
        self.btn_run.set_sensitive(False)
        self.btn_run.set_label("Running...")
        self.result_label.set_text("Detecting bad channels...")
        self.spinner.set_visible(True)
        self.spinner.start()
        self.ok_btn.set_sensitive(False)

        def _worker():
            try:
                from pyprep import NoisyChannels
                nd = NoisyChannels(
                    self.raw,
                    do_detrend=params.get("do_detrend", True),
                    random_state=params.get("random_state", 42),
                )
                nd.find_all_bads(ransac=params.get("ransac", True))
                bads = nd.get_bads()
                GLib.idle_add(self.on_done, bads)
            except Exception as exc:
                GLib.idle_add(self.on_error, str(exc))

        threading.Thread(target=_worker, daemon=True).start()

    def on_done(self, bads: list[str]):
        self.detected = bads
        self.spinner.stop()
        self.spinner.set_visible(False)
        self.btn_run.set_sensitive(True)
        self.btn_run.set_label("Run Detection")
        if bads:
            self.result_label.set_text(f"Detected {len(bads)} bad channel(s):\n{', '.join(bads)}")
            self.ok_btn.set_sensitive(True)
        else:
            self.result_label.set_text("No bad channels detected.")
            self.ok_btn.set_sensitive(False)

    def on_error(self, msg: str):
        self.detected = []
        self.spinner.stop()
        self.spinner.set_visible(False)
        self.btn_run.set_sensitive(True)
        self.btn_run.set_label("Run Detection")
        self.result_label.set_text(f"Error: {msg}")
        self.ok_btn.set_sensitive(False)

    def get_detected(self) -> list[str]:
        return list(self.detected)

class ChannelPickerDialog(ModalDialog):
    """Dialog for interactively selecting channels to drop.

    Shows a side-by-side view: left panel contains an MNE sensor-map figure with lasso
    and click selection; right panel embeds a raw time-series browser.
    Selections are kept in sync between both panels via a polling timer.
    """

    def __init__(self, raw: mne.io.Raw, selected: list[str] | None = None, parent_window=None):
        self.raw = raw
        self.initial_selected = [name for name in (selected or []) if name in raw.ch_names]
        self.selected: list[str] = []
        self.lasso = None
        self.sensor_canvas = None
        self.sensor_figure = None
        self.raw_preview = None
        self.base_preview_bads: set[str] = set()
        self.sync_guard = False
        self.poll_source: int | None = None

        self.dialog = Adw.Dialog()
        self.dialog.set_title("Select Channels to Drop")
        self.dialog.set_content_width(1200)

        toolbar_view = Adw.ToolbarView()
        toolbar_view.add_top_bar(Adw.HeaderBar())
        self.dialog.set_child(toolbar_view)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        outer.set_margin_start(8)
        outer.set_margin_end(8)
        outer.set_margin_top(6)
        outer.set_margin_bottom(6)
        toolbar_view.set_content(outer)

        info = Gtk.Label(label="Click or lasso channels on the sensor map to select channels to drop.")
        info.set_wrap(True)
        info.set_xalign(0.0)
        outer.append(info)

        # Paned: sensor map | raw preview
        paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        paned.set_vexpand(True)
        paned.set_position(650)
        outer.append(paned)

        # Left: sensor map
        left_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        left_box.set_margin_start(2)
        left_box.set_margin_end(2)
        lbl_sensor = Gtk.Label(label="Sensor Map")
        lbl_sensor.set_xalign(0.0)
        left_box.append(lbl_sensor)
        self.sensor_container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.sensor_container.set_hexpand(True)
        self.sensor_container.set_vexpand(True)
        left_box.append(self.sensor_container)
        paned.set_start_child(left_box)

        # Right: raw preview
        right_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        right_box.set_margin_start(2)
        right_box.set_margin_end(2)
        lbl_raw = Gtk.Label(label="Raw Time Series Preview")
        lbl_raw.set_xalign(0.0)
        right_box.append(lbl_raw)
        self.preview_container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.preview_container.set_hexpand(True)
        self.preview_container.set_vexpand(True)
        right_box.append(self.preview_container)
        paned.set_end_child(right_box)

        # Footer
        footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.selected_label = Gtk.Label(label="No channels selected")
        self.selected_label.set_xalign(0.0)
        self.selected_label.set_hexpand(True)
        footer.append(self.selected_label)

        btn_clear = Gtk.Button(label="Clear")
        btn_clear.connect("clicked", self.clear_selection)
        footer.append(btn_clear)

        cancel_btn = Gtk.Button(label="Cancel")
        cancel_btn.connect("clicked", self.reject)
        footer.append(cancel_btn)

        ok_btn = Gtk.Button(label="OK")
        ok_btn.add_css_class("suggested-action")
        ok_btn.connect("clicked", self.accept)
        footer.append(ok_btn)
        outer.append(footer)

        self.setup_modal(parent_window)

        # Build panels after dialog is constructed
        self.build_sensor_panel()
        self.build_raw_preview()
        self.apply_initial_selection()
        self.sync_selection()

        self.poll_source = GLib.timeout_add(150, self.pull_selection_from_raw_preview)

    def build_sensor_panel(self) -> None:
        try:
            self.sensor_figure, _ = self.raw.plot_sensors(kind="select", show=False, show_names=True)
            self.sensor_canvas = self.sensor_figure.canvas
            if isinstance(self.sensor_canvas, FigureCanvasGTK4Agg):
                self.sensor_canvas.set_hexpand(True)
                self.sensor_canvas.set_vexpand(True)
                self.sensor_container.append(self.sensor_canvas)
            else:
                # Wrap in a GTK4Agg canvas if needed
                from matplotlib.backends.backend_gtk4agg import FigureCanvasGTK4Agg as _Canvas
                canvas_widget = _Canvas(self.sensor_figure)
                canvas_widget.set_hexpand(True)
                canvas_widget.set_vexpand(True)
                self.sensor_canvas = canvas_widget
                self.sensor_container.append(canvas_widget)
        except Exception as e:
            logger.warning("Could not build sensor panel: %s", e)
            self.sensor_container.append(Gtk.Label(label=f"Sensor map unavailable:\n{e}"))
            return

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

    def build_raw_preview(self) -> None:
        n_channels = min(len(self.raw.ch_names), 20)
        try:
            self.raw_preview = self.raw.plot(show=False, duration=10.0, n_channels=n_channels, block=False)
            sanitize_mne_browser_toolbar(self.raw_preview, allow_annotation_mode=False)
            embed_mne_browser(self.raw_preview, self.preview_container)
            if hasattr(self.raw_preview, "mne"):
                self.base_preview_bads = set(self.raw_preview.mne.info.get("bads", []))
        except Exception as e:
            logger.warning("Could not embed raw preview: %s", e)
            self.preview_container.append(Gtk.Label(label=f"Preview unavailable:\n{e}"))

    def patch_lasso_colors(self) -> None:
        """Override style_objects to color selected channels yellow."""
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
        """Make lasso append by default."""
        if self.lasso is None:
            return

        def on_select_append(verts):
            if len(verts) <= 3:
                self.sensor_canvas.draw_idle()
                return

            path = MplPath(verts)
            inds = np.nonzero([path.intersects_path(p) for p in self.lasso.paths])[0]
            current = np.asarray(getattr(self.lasso, "selection_inds", []), dtype=int)
            new_inds = np.union1d(current, inds).astype(int)
            self.lasso.selection_inds = new_inds
            self.lasso.selection = [self.lasso.names[i] for i in new_inds]
            self.lasso.style_objects()
            self.lasso.notify()
            self.sensor_canvas.draw_idle()

        self.lasso.on_select = on_select_append
        if hasattr(self.lasso, "lasso"):
            self.lasso.lasso.onselect = on_select_append

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
        if self.selected:
            self.selected_label.set_text(f"Selected: {', '.join(self.selected)}")
        else:
            self.selected_label.set_text("No channels selected")

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
            managed = set(self.selected)
            bads = sorted(self.base_preview_bads | managed)
            self.raw_preview.mne.info["bads"] = bads
            refresh_mne_browser_bads(self.raw_preview, set(bads))
        except Exception as e:
            logger.debug("Failed to push channel selection to raw preview: %s", e, exc_info=True)
        finally:
            self.sync_guard = False

    def pull_selection_from_raw_preview(self) -> bool:
        """Poll raw preview bads. Returns True to keep the timeout alive."""
        if self.raw_preview is None or not hasattr(self.raw_preview, "mne"):
            return True
        if self.sync_guard:
            return True
        try:
            self.sync_guard = True
            bads = set(self.raw_preview.mne.info.get("bads", []))
            selected_from_preview = [
                ch for ch in self.raw.ch_names
                if ch in bads and ch not in self.base_preview_bads
            ]
            if selected_from_preview == self.selected:
                return True
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
        return True

    def clear_selection(self, _btn=None):
        if self.lasso is not None:
            self.lasso.select_many([])
            self.lasso.notify()
        self.sync_selection()

    def get_selected(self) -> list[str]:
        return [ch for ch in self.raw.ch_names if ch in set(self.selected)]

    def on_closed(self, *_) -> None:
        if self.poll_source is not None:
            GLib.source_remove(self.poll_source)
            self.poll_source = None
        self.loop.quit()

# -------- Param widget factory --------

def channels_widget_factory(current_value, raw):
    """Build a widget for the 'channels' param type."""
    container = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
    container.set_hexpand(True)

    entry = Gtk.Entry()
    entry.set_text(str(current_value) if current_value is not None else "")
    entry.set_hexpand(True)
    container.append(entry)

    btn_pick = Gtk.Button(label="Pick...")
    btn_pick.set_sensitive(raw is not None)

    def _pick(_btn):
        if raw is None:
            return
        selected = [c.strip() for c in entry.get_text().split(",") if c.strip()]
        parent_window = btn_pick.get_root()
        dlg = ChannelPickerDialog(raw, selected, parent_window=parent_window)
        if dlg.exec():
            entry.set_text(", ".join(dlg.get_selected()))

    btn_pick.connect("clicked", _pick)
    container.append(btn_pick)

    btn_detect = Gtk.Button(label="Detect...")
    btn_detect.set_sensitive(raw is not None)

    def _detect(_btn):
        if raw is None:
            return
        parent_window = btn_detect.get_root()
        dlg = BadChannelDetectorDialog(raw, parent_window=parent_window)
        if dlg.exec():
            detected = dlg.get_detected()
            if detected:
                existing = {c.strip() for c in entry.get_text().split(",") if c.strip()}
                merged = existing | set(detected)
                ordered = [ch for ch in raw.ch_names if ch in merged]
                entry.set_text(", ".join(ordered))

    btn_detect.connect("clicked", _detect)
    container.append(btn_detect)

    return container, entry

# -------- Widget bindings --------

WIDGET_BINDINGS = [
    ParamWidgetBinding("channels", channels_widget_factory),
]
