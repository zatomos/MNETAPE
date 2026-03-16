"""ICA apply action widgets.

Contains the ICAInspectionDialog for interactive component review and the exclude_components param widget factory
used by the action editor.
"""

from __future__ import annotations

import logging

import matplotlib.pyplot as plt
import numpy as np


from gi.repository import Adw, GLib, Gtk
from matplotlib.backends.backend_gtk4agg import FigureCanvasGTK4Agg

from mnetape.actions.base import ParamWidgetBinding
from mnetape.gui.dialogs.base import ModalDialog
from mnetape.core.models import ICASolution
from mnetape.gui.utils import refresh_mne_browser_bads
from mnetape.gui.widgets.common import PlotCanvas, embed_mne_browser

logger = logging.getLogger(__name__)

# -------- Utility helpers --------

def format_component_labels(
    ic_labels: dict | None,
    detected_artifacts: list[int] | None,
    n_components: int,
) -> list[str]:
    """Build per-component label strings for topography plot titles."""
    artifact_set = set(detected_artifacts or [])

    iclabel_lines = [""] * n_components
    if ic_labels and ic_labels.get("labels"):
        probs = ic_labels.get("y_pred_proba")
        if probs is not None:
            iclabel_lines = [
                f"{label[:3].upper()} {np.max(prob):.0%}"
                for label, prob in zip(ic_labels["labels"], probs)
            ]

    result = [""] * n_components
    for i in range(n_components):
        parts = []
        if iclabel_lines[i]:
            parts.append(f"ICL: {iclabel_lines[i]}")
        if i in artifact_set:
            parts.append("ARTIFACT")
        result[i] = "\n".join(parts)
    return result

def safe_patch_toolbar(fig):
    """Wrap toolbar.set_message to swallow RuntimeError during teardown."""
    try:
        manager = fig.canvas.manager
        if manager and hasattr(manager, "toolbar") and manager.toolbar:
            toolbar = manager.toolbar
            orig = toolbar.set_message

            def safe_set_message(s):
                try:
                    orig(s)
                except RuntimeError:
                    logger.debug("Ignored RuntimeError while updating toolbar message", exc_info=True)

            toolbar.set_message = safe_set_message
    except Exception as e:
        logger.debug("Failed to patch matplotlib toolbar safely: %s", e, exc_info=True)

def close_figure_safely(fig):
    """Close a matplotlib figure, silently ignoring RuntimeError and KeyError."""
    try:
        plt.close(fig)
    except (RuntimeError, KeyError):
        logger.debug("Ignored error while closing matplotlib figure", exc_info=True)
    except Exception as e:
        logger.debug("Failed to close matplotlib figure cleanly: %s", e, exc_info=True)

# -------- ICA inspection dialog --------

class ICAInspectionDialog(ModalDialog):
    """Interactive dialog for reviewing and selecting ICA components for removal.

    Shows component topographies in paginated chunks alongside the ICA sources plot.
    Components can be toggled for exclusion by clicking on them.
    """

    CHUNK_SIZE = 15

    def __init__(self, ica, raw, auto_exclude, ic_labels=None, parent_window=None):
        self.btn_overlay = None
        self.ica = ica
        self.raw = raw
        self.ic_labels = ic_labels
        self.detected_artifacts = list(ic_labels.get("detected_artifacts", [])) if ic_labels else []
        self.labels = format_component_labels(ic_labels, self.detected_artifacts, ica.n_components_)
        self.ica.exclude = list(auto_exclude)
        self.ica_names = list(
            getattr(ica, "ica_names", None)
            or getattr(ica, "_ica_names", None)
            or [f"ICA{i:03d}" for i in range(ica.n_components_)]
        )

        pick_list = list(range(ica.n_components_))
        self.pick_chunks = [
            pick_list[i:i + self.CHUNK_SIZE]
            for i in range(0, len(pick_list), self.CHUNK_SIZE)
        ]
        self.max_page = len(self.pick_chunks)
        self.page_idx: int = 0

        self.current_fig = None
        self.source_fig = None
        self.source_view = None
        self.current_prop_figs: list = []
        self.extra_figs: list = []
        self.prev_exclude: list[int] = []
        self.poll_source: int | None = None

        # ---- Build dialog ----
        self.dialog = Adw.Dialog()
        self.dialog.set_title("ICA Component Inspection")
        self.dialog.set_content_width(1300)
        self.dialog.set_content_height(780)

        toolbar_view = Adw.ToolbarView()
        toolbar_view.add_top_bar(Adw.HeaderBar())
        self.dialog.set_child(toolbar_view)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        outer.set_margin_start(8)
        outer.set_margin_end(8)
        outer.set_margin_top(6)
        outer.set_margin_bottom(6)
        toolbar_view.set_content(outer)

        # Info row
        outer.append(self.build_info_row())

        # Main content: components (left) | sources (right) in a paned
        paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        paned.set_vexpand(True)
        paned.set_position(650)
        outer.append(paned)

        # Left: component topographies + page controls
        left_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.plot_components = PlotCanvas(None)
        self.plot_components.set_hexpand(True)
        self.plot_components.set_vexpand(True)
        left_box.append(self.plot_components)

        page_controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.btn_prev = Gtk.Button(label="\u25c4 Prev")
        self.btn_next = Gtk.Button(label="Next \u25ba")
        self.page_label = Gtk.Label(label="")
        self.page_label.set_hexpand(True)
        self.page_label.set_xalign(1.0)
        self.btn_prev.connect("clicked", lambda _: self.set_page(self.page_idx - 1))
        self.btn_next.connect("clicked", lambda _: self.set_page(self.page_idx + 1))
        page_controls.append(self.btn_prev)
        page_controls.append(self.btn_next)
        page_controls.append(self.page_label)
        left_box.append(page_controls)
        paned.set_start_child(left_box)

        # Right: sources plot
        self.sources_container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.sources_container.set_hexpand(True)
        self.sources_container.set_vexpand(True)
        paned.set_end_child(self.sources_container)

        # Exclusion summary
        self.exclude_text = Gtk.TextView()
        self.exclude_text.set_editable(False)
        self.exclude_text.set_monospace(True)
        self.exclude_text.set_size_request(-1, 70)
        outer.append(self.exclude_text)

        # Bottom buttons
        outer.append(self.build_button_row())

        self.setup_modal(parent_window)

        # Setup sources and first page
        self.setup_sources()
        self.set_page(0)
        self.update_info()
        self.push_to_sources()

    def build_info_row(self) -> Gtk.Box:
        methods = []
        if self.ic_labels is not None:
            methods.append("ICLabel")
        if self.detected_artifacts:
            methods.append("Artifact detection")

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=24)
        row.set_margin_top(4)
        row.set_margin_bottom(4)

        status_label = Gtk.Label(label=f"Active: {', '.join(methods)}" if methods else "")
        row.append(status_label)

        explain_label = Gtk.Label(
            label=(
                "Components are auto-excluded based on enabled detection methods. "
                "Left-click to toggle exclusion. 'Apply Exclusion' removes selected components. "
                "Excluded components are permanently removed. Right-click for properties."
            )
        )
        explain_label.set_wrap(True)
        explain_label.set_hexpand(True)
        explain_label.set_xalign(0.0)
        row.append(explain_label)
        return row

    def build_button_row(self) -> Gtk.Box:
        btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        btn_row.set_margin_top(4)

        self.btn_overlay = Gtk.Button(label="Show Overlay")
        self.btn_overlay.set_tooltip_text("Show before/after comparison")
        self.btn_overlay.set_sensitive(len(self.ica.exclude) > 0)
        self.btn_overlay.connect("clicked", self.show_overlay)
        btn_row.append(self.btn_overlay)

        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        btn_row.append(spacer)

        cancel_btn = Gtk.Button(label="Cancel")
        cancel_btn.connect("clicked", self.reject)
        btn_row.append(cancel_btn)

        done_btn = Gtk.Button(label="Apply Exclusion")
        done_btn.add_css_class("suggested-action")
        done_btn.connect("clicked", self.accept)
        btn_row.append(done_btn)

        return btn_row

    def setup_sources(self):
        """Create the ICA sources plot and embed it in sources_container."""
        sources_widget = None
        fig_sources = None

        try:
            candidate = self.ica.plot_sources(self.raw, show=False)
            if hasattr(candidate, "canvas"):
                fig_sources = candidate
        except Exception as e:
            logger.debug("Falling back from widget-based ICA sources plot: %s", e, exc_info=True)

        if fig_sources is None and sources_widget is None:
            fig_sources, ax = plt.subplots(figsize=(8, 4))
            sources = self.ica.get_sources(self.raw).get_data()
            times = self.raw.times
            n_show = min(10, sources.shape[0])
            for i in range(n_show):
                data = sources[i]
                data = (data - data.mean()) / (data.std() + 1e-10)
                ax.plot(times, data - i * 4, linewidth=0.5)
            ax.set_yticks([-i * 4 for i in range(n_show)])
            ax.set_yticklabels([f"IC{i:03d}" for i in range(n_show)])
            ax.set_xlabel("Time (s)")
            ax.set_title("ICA Sources")
            fig_sources.tight_layout()

        if fig_sources is not None:
            self.source_fig = fig_sources
            # Keep the MNE browser object so sync helpers can access .mne
            self.source_view = fig_sources if hasattr(fig_sources, "mne") else None
            # Try to embed via manager
            if hasattr(fig_sources, "canvas") and hasattr(fig_sources.canvas, "manager"):
                try:
                    embed_mne_browser(fig_sources, self.sources_container)
                except Exception:
                    # Fallback: add the canvas directly
                    canvas = FigureCanvasGTK4Agg(fig_sources)
                    canvas.set_hexpand(True)
                    canvas.set_vexpand(True)
                    self.sources_container.append(canvas)
            else:
                plot_sources = PlotCanvas(fig_sources)
                plot_sources.set_hexpand(True)
                plot_sources.set_vexpand(True)
                self.sources_container.append(plot_sources)

        # If the source has .mne attribute, set up polling
        if self.source_view is not None and hasattr(self.source_view, "mne"):
            self.poll_source = GLib.timeout_add(150, self.sync_from_sources)

    # -------- Page management --------

    def set_page(self, idx: int):
        """Switch the component topography display to the given page index."""
        if idx < 0 or idx >= self.max_page:
            return
        self.page_idx = idx
        if self.current_fig is not None:
            close_figure_safely(self.current_fig)

        fig = self.ica.plot_components(picks=self.pick_chunks[idx], show=False)
        if isinstance(fig, list):
            fig = fig[0]
        safe_patch_toolbar(fig)
        self.plot_components.update_figure(fig)
        self.current_fig = fig
        fig.canvas.callbacks.callbacks.pop("button_press_event", None)
        fig.canvas.mpl_connect("button_press_event", self.on_click)
        self.update_colors()
        self.page_label.set_text(f"Page {idx + 1} / {self.max_page}")
        self.btn_prev.set_sensitive(idx > 0)
        self.btn_next.set_sensitive(idx < self.max_page - 1)

    # -------- Exclusion state --------

    def update_info(self):
        """Refresh the exclusion summary text box."""
        buf = self.exclude_text.get_buffer()
        if self.ica.exclude:
            parts = [f"IC{i:03d}" for i in sorted(self.ica.exclude)]
            buf.set_text(f"Excluding {len(self.ica.exclude)} components:\n" + ", ".join(parts), -1)
        else:
            buf.set_text("No components excluded", -1)

    def toggle_exclude(self, idx: int):
        """Add or remove a component index from ica.exclude and refresh the UI."""
        if idx in self.ica.exclude:
            self.ica.exclude.remove(idx)
        else:
            self.ica.exclude.append(idx)
        self.ica.exclude = sorted(set(self.ica.exclude))
        self.prev_exclude[:] = self.ica.exclude
        self.update_colors()
        self.update_info()
        self.push_to_sources()
        self.btn_overlay.set_sensitive(len(self.ica.exclude) > 0)

    def update_colors(self):
        """Update axis title colors for the current page to reflect exclusion state."""
        fig = self.plot_components.canvas.figure
        axes = [ax for ax in fig.get_axes() if hasattr(ax, "get_title") and ax.get_title()]
        chunk = self.pick_chunks[self.page_idx] if self.pick_chunks else []
        dirty = False
        for ax, idx in zip(axes, chunk):
            new_color = "gray" if idx in self.ica.exclude else "k"
            label_txt = self.labels[idx] if self.labels and self.labels[idx] else ""
            new_title = f"IC{idx:03d}\n{label_txt}" if label_txt else f"IC{idx:03d}"
            if ax.get_title() != new_title or ax.title.get_color() != new_color:
                ax.set_title(new_title, color=new_color, fontsize=8)
                dirty = True
        if dirty:
            try:
                fig.canvas.draw_idle()
            except RuntimeError:
                logger.debug("Ignored RuntimeError while redrawing ICA component figure", exc_info=True)

    # -------- Click handling --------

    def title_clicked(self, event, ax) -> bool:
        if event.x is None or event.y is None:
            return False
        renderer = self.plot_components.canvas.figure.canvas.get_renderer()
        bbox = ax.title.get_window_extent(renderer)
        return bbox.contains(event.x, event.y)

    def on_click(self, event):
        """Handle left-click (toggle exclusion) and right-click (show properties) on topographies."""
        if event.x is None or event.y is None:
            return
        fig = self.plot_components.canvas.figure
        axes = [ax for ax in fig.get_axes() if hasattr(ax, "get_title") and ax.get_title()]
        chunk = self.pick_chunks[self.page_idx] if self.pick_chunks else []
        axis_map = {ax: idx for ax, idx in zip(axes, chunk)}

        if event.button == 1:
            for ax, idx in axis_map.items():
                if self.title_clicked(event, ax):
                    self.toggle_exclude(idx)
                    return

        if event.inaxes is None or event.inaxes not in axis_map:
            return
        idx = axis_map[event.inaxes]

        if event.button == 1:
            self.toggle_exclude(idx)
            return

        # Right-click: show properties
        try:
            self.close_prop_figs()
            prop_figs = self.ica.plot_properties(
                self.raw, picks=[idx], reject=None,
                reject_by_annotation=False, show=False,
            )
            if isinstance(prop_figs, list) and prop_figs:
                pf = prop_figs[0]
                safe_patch_toolbar(pf)
                self.current_prop_figs.append(pf)
                self.show_prop_dialog(pf, idx)
                for extra in prop_figs[1:]:
                    close_figure_safely(extra)
        except Exception as e:
            logger.exception("Failed to open ICA component properties for IC%s due to error: %s", idx, e)

    def show_figure_dialog(self, fig, title: str, dialog_w: int, dialog_h: int, canvas_w: int, canvas_h: int) -> None:
        """Show a blocking figure-viewer dialog stacked on top of the ICA inspection dialog."""
        dlg = Adw.Dialog()
        dlg.set_title(title)
        dlg.set_content_width(dialog_w)
        dlg.set_content_height(dialog_h)

        toolbar_view = Adw.ToolbarView()
        toolbar_view.add_top_bar(Adw.HeaderBar())
        dlg.set_child(toolbar_view)

        canvas = FigureCanvasGTK4Agg(fig)
        canvas.set_hexpand(True)
        canvas.set_vexpand(True)
        canvas.set_size_request(canvas_w, canvas_h)
        toolbar_view.set_content(canvas)

        key_ctrl = Gtk.EventControllerKey()
        key_ctrl.connect("key-pressed", lambda _, kv, *__: (dlg.close() or True) if kv == 0xFF1B else False)
        dlg.add_controller(key_ctrl)

        loop = GLib.MainLoop()
        dlg.connect("closed", lambda _: loop.quit())
        dlg.present(self.dialog)
        loop.run()

    def show_prop_dialog(self, fig, idx: int):
        """Show a blocking dialog with the component properties figure."""
        self.show_figure_dialog(fig, f"IC{idx:03d} Properties", 820, 600, 780, 520)

    # -------- Source sync --------

    def push_to_sources(self):
        if self.source_view is None or not hasattr(self.source_view, "mne"):
            return
        try:
            ica_names_set = set(self.ica_names)
            new_bads = [self.ica_names[i] for i in self.ica.exclude if 0 <= i < len(self.ica_names)]
            existing_bads = self.source_view.mne.info.get("bads", [])
            non_ica_bads = [b for b in existing_bads if b not in ica_names_set]
            self.source_view.mne.info["bads"] = new_bads + non_ica_bads
            refresh_mne_browser_bads(self.source_view, set(new_bads), filter_names=ica_names_set)
        except Exception as e:
            logger.debug("Failed to push exclusions to sources widget: %s", e, exc_info=True)

    def sync_from_sources(self) -> bool:
        """Poll the sources widget's bad-channel list. Returns True to keep polling."""
        if self.source_view is None:
            return False
        try:
            if not hasattr(self.source_view, "mne"):
                return False
            bads = set(self.source_view.mne.info.get("bads", []))
            new_exclude = sorted(
                self.ica_names.index(n) for n in bads if n in self.ica_names
            )
            if new_exclude == self.prev_exclude:
                return True
            self.ica.exclude = new_exclude
            self.prev_exclude[:] = new_exclude
            self.update_colors()
            self.update_info()
            self.btn_overlay.set_sensitive(len(self.ica.exclude) > 0)
        except Exception as e:
            logger.debug("Failed to sync exclusions from sources widget: %s", e, exc_info=True)
        return True

    # -------- Overlay --------

    def show_overlay(self, _btn=None):
        try:
            exclude = sorted(self.ica.exclude)
            if not exclude:
                return
            fig_overlay = self.ica.plot_overlay(self.raw, exclude=exclude, show=False)
            safe_patch_toolbar(fig_overlay)
            self.extra_figs.append(fig_overlay)

            self.show_figure_dialog(fig_overlay, "ICA Overlay - Before/After", 720, 480, 680, 400)
        except Exception as e:
            logger.debug("Failed to show ICA overlay figure: %s", e, exc_info=True)

    # -------- Cleanup --------

    def close_prop_figs(self):
        while self.current_prop_figs:
            close_figure_safely(self.current_prop_figs.pop())

    def cleanup_figures(self):
        """Close all matplotlib figures held by the dialog."""
        if self.current_fig is not None:
            close_figure_safely(self.current_fig)
            self.current_fig = None
        if self.source_fig is not None:
            close_figure_safely(self.source_fig)
            self.source_fig = None
        self.close_prop_figs()
        for fig in self.extra_figs:
            close_figure_safely(fig)
        self.extra_figs.clear()

    def on_closed(self, *_) -> None:
        if self.poll_source is not None:
            GLib.source_remove(self.poll_source)
            self.poll_source = None
        self.cleanup_figures()
        self.loop.quit()

# -------- Param widget factory --------

def format_exclude_label(exclude: list) -> str:
    if not exclude:
        return "No components excluded"
    return f"{len(exclude)} excluded: {exclude}"

class ExcludeWidget(Gtk.Box):
    """Container widget for the ICA component exclusion param.

    Exposes get_value() and connect_value_changed() so the action editor can read
    and react to changes.
    """

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.set_hexpand(True)
        self.state: dict = {"exclude": []}
        self.changed_cbs: list = []

    def get_value(self) -> list | None:
        return self.state["exclude"] or None

    def connect_value_changed(self, cb):
        self.changed_cbs.append(cb)

    def _emit_changed(self):
        for cb in self.changed_cbs:
            cb()

def exclude_components_factory(current_value, raw):
    """Widget factory for the exclude components param."""
    ica_solution = raw if isinstance(raw, ICASolution) else None
    exclude = list(current_value) if current_value is not None else []

    container = ExcludeWidget()
    container.state = {"exclude": exclude}

    has_classify = ica_solution is not None and ica_solution.ic_labels is not None
    ic_labels_dict = ica_solution.ic_labels if has_classify else None
    auto_exclude = list(ic_labels_dict.get("detected_artifacts", [])) if ic_labels_dict else []

    label = Gtk.Label(label=format_exclude_label(container.state["exclude"]))
    label.set_xalign(0.0)
    label.set_hexpand(True)
    container.append(label)

    def update_label():
        label.set_text(format_exclude_label(container.state["exclude"]))

    if ica_solution is not None:
        btn = Gtk.Button(label="Browse Components...")

        if has_classify:
            use_auto_cb = Gtk.CheckButton(label="Use auto")
            use_auto_cb.set_tooltip_text(
                "Use the exclusion list computed by the Classify ICA Components step."
            )
            container.append(use_auto_cb)

            def on_use_auto_toggled(_cb):
                checked = use_auto_cb.get_active()
                container.state["exclude"] = auto_exclude if checked else []
                update_label()
                btn.set_sensitive(not checked)
                container._emit_changed()

            use_auto_cb.connect("toggled", on_use_auto_toggled)

        def on_browse(_btn):
            parent_window = btn.get_root()
            dialog = ICAInspectionDialog(
                ica=ica_solution.ica,
                raw=ica_solution.raw,
                auto_exclude=list(container.state["exclude"]),
                ic_labels=ica_solution.ic_labels,
                parent_window=parent_window,
            )
            if dialog.exec():
                container.state["exclude"] = sorted(dialog.ica.exclude)
                update_label()
                container._emit_changed()
            dialog.cleanup_figures()

        btn.connect("clicked", on_browse)
        container.append(btn)
    else:
        note = Gtk.Label(label="(run Fit ICA first to browse)")
        note.add_css_class("dim-label")
        container.append(note)

    return container, container

# -------- Widget bindings --------

WIDGET_BINDINGS = [
    ParamWidgetBinding("exclude", exclude_components_factory),
]
