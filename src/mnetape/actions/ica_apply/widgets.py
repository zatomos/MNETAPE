"""ICA apply action widgets.

Contains the ICAInspectionDialog for interactive component review and the exclude_components param widget factory
used by the action editor.
"""

from __future__ import annotations

import logging

import matplotlib.pyplot as plt
from mnetape.gui.utils import refresh_mne_browser_bads
import numpy as np
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from PyQt6.QtGui import QFont

from mnetape.actions.base import InteractiveRunner
from mnetape.actions.ica_apply.classify import get_auto_exclude, run_background_classification
from mnetape.gui.widgets import PlotCanvas

logger = logging.getLogger(__name__)


# -------- Utility helpers --------

def format_component_labels(
    ic_labels: dict | None,
    detected_artifacts: list[int] | None,
    n_components: int,
) -> list[str]:
    """Build per-component label strings for topography plot titles.

    Each label contains up to two lines:
        - Line 1: ICLabel prediction abbreviated to 3 chars + probability
        - Line 2: "ARTIFACT" when the component is in detected_artifacts

    Args:
        ic_labels: Dict returned by ICLabel containing "labels" and "y_pred_proba", or None.
        detected_artifacts: Sorted list of artifact component indices from background detection, or None.
        n_components: Total number of ICA components.

    Returns:
        List of label strings, one per component.
    """
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
    """Wrap toolbar.set_message to swallow RuntimeError when the Qt widget is deleted during teardown."""
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

class ICAInspectionDialog(QDialog):
    """Interactive dialog for reviewing and selecting ICA components for removal.

    Shows component topographies in paginated chunks alongside the ICA sources plot.
    Components can be toggled for exclusion by clicking on them.
    A collapsible overlay button compares the signal before and after applying ICA.

    Attributes:
        CHUNK_SIZE: Number of component topographies shown per page.
        ica: The fitted MNE ICA object (ica.exclude is modified in-place).
        raw: The MNE Raw object used for source plotting.
        labels: Per-component label strings (ICLabel + artifact flag).
        page_idx: Current page of component topographies.
        poll_timer: Timer used to poll the sources widget for exclusion changes.
    """

    CHUNK_SIZE = 15

    def __init__(self, ica, raw, auto_exclude, ic_labels=None, parent=None):
        super().__init__(parent)
        self.btn_overlay = None
        self.exclude_display = None
        self.page_label = None
        self.btn_next = None
        self.btn_prev = None
        self.plot_components = None
        self.plots_layout = None
        self.ica = ica
        self.raw = raw
        self.ic_labels = ic_labels
        # Derive detected_artifacts from ic_labels["detected_artifacts"] if available
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
        self.prop_dialog: QDialog | None = None
        self.current_prop_figs: list = []
        self.extra_figs: list = []
        self.prev_exclude: list[int] = []
        self.poll_timer: QTimer | None = None

        self.setup_ui()
        self.setup_sources()
        self.set_page(0)
        self.push_to_sources()


    # -------- UI setup --------

    def setup_ui(self):
        self.setWindowTitle("ICA Component Inspection")
        self.setMinimumSize(1200, 800)
        self.setSizeGripEnabled(True)
        layout = QVBoxLayout(self)

        layout.addLayout(self.build_info_row())

        self.plots_layout = QHBoxLayout()

        components_container = QWidget(self)
        cc_layout = QVBoxLayout(components_container)
        cc_layout.setContentsMargins(0, 0, 0, 0)

        self.plot_components = PlotCanvas(None, self)
        self.plot_components.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        cc_layout.addWidget(self.plot_components, 1)

        page_controls = QHBoxLayout()
        self.btn_prev = QPushButton("◀ Prev")
        self.btn_next = QPushButton("Next ▶")
        self.page_label = QLabel("")
        page_controls.addWidget(self.btn_prev)
        page_controls.addWidget(self.btn_next)
        page_controls.addStretch()
        page_controls.addWidget(self.page_label)
        cc_layout.addLayout(page_controls)

        self.btn_prev.clicked.connect(lambda: self.set_page(self.page_idx - 1))
        self.btn_next.clicked.connect(lambda: self.set_page(self.page_idx + 1))

        self.plots_layout.addWidget(components_container, 1)
        layout.addLayout(self.plots_layout)

        self.exclude_display = QTextEdit()
        self.exclude_display.setReadOnly(True)
        self.exclude_display.setMaximumHeight(100)
        self.exclude_display.setFont(QFont("Consolas", 10))
        layout.addWidget(self.exclude_display)

        layout.addLayout(self.build_button_row())

    def build_info_row(self) -> QHBoxLayout:
        """Build the info row showing which classification methods ran."""
        methods = []
        if self.ic_labels is not None:
            methods.append("ICLabel")
        if self.detected_artifacts:
            methods.append("Artifact detection")

        info_row = QHBoxLayout()
        info_row.setContentsMargins(0, 6, 0, 6)
        info_row.setSpacing(64)

        status_label = QLabel(f"Active: {', '.join(methods)}" if methods else "")
        status_label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        info_row.addWidget(status_label)

        explain_label = QLabel(
            "Components are auto-excluded based on enabled detection methods. "
            "Left-click to toggle exclusion. 'Apply Exclusion' removes selected components from the data. "
            "Excluded components are permanently removed. If unsure, keep a component.\n"
            "Right-click a topography to view properties."
        )
        explain_label.setWordWrap(True)
        explain_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        info_row.addWidget(explain_label, 1)
        return info_row

    def build_button_row(self) -> QHBoxLayout:
        """Build the bottom button row: Show Overlay, Cancel, Apply Exclusion."""
        btn_layout = QHBoxLayout()

        self.btn_overlay = QPushButton("Show Overlay")
        self.btn_overlay.setToolTip("Show before/after comparison")
        self.btn_overlay.setEnabled(len(self.ica.exclude) > 0)
        self.btn_overlay.clicked.connect(self.show_overlay)
        btn_layout.addWidget(self.btn_overlay)
        btn_layout.addStretch()

        btn_cancel = QPushButton("Cancel")
        btn_cancel.clicked.connect(self.reject)
        btn_layout.addWidget(btn_cancel)

        btn_done = QPushButton("Apply Exclusion")
        btn_done.setStyleSheet(
            "QPushButton { background-color: #2E7D32; color: white; "
            "font-weight: bold; padding: 8px 16px; border: none; border-radius: 5px; }"
            "QPushButton:hover { background-color: #388E3C; }"
        )
        btn_done.clicked.connect(self.accept)
        btn_layout.addWidget(btn_done)

        return btn_layout

    def setup_sources(self):
        """Create the ICA sources plot and embed it in plots_layout."""
        sources_widget = None
        fig_sources = None

        try:
            candidate = self.ica.plot_sources(self.raw, show=False)
            if isinstance(candidate, QWidget):
                candidate.setParent(self)
                candidate.setWindowFlags(Qt.WindowType.Widget)
                sources_widget = candidate
            elif hasattr(candidate, "canvas"):
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

        if sources_widget is not None:
            self.source_view = sources_widget
            self.plots_layout.addWidget(sources_widget, 1)
        else:
            self.source_view = fig_sources
            self.source_fig = fig_sources
            plot_sources = PlotCanvas(fig_sources, self)
            plot_sources.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
            self.plots_layout.addWidget(plot_sources, 1)

        if self.source_view is not None and hasattr(self.source_view, "mne"):
            self.poll_timer = QTimer(self)
            self.poll_timer.timeout.connect(self.sync_from_sources)
            self.poll_timer.start(150)


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
        self.page_label.setText(f"Page {idx + 1} / {self.max_page}")
        self.btn_prev.setEnabled(idx > 0)
        self.btn_next.setEnabled(idx < self.max_page - 1)


    # -------- Exclusion state --------

    def update_info(self):
        """Refresh the exclusion summary text box."""
        try:
            if self.ica.exclude:
                parts = [f"IC{idx:03d}" for idx in sorted(self.ica.exclude)]
                self.exclude_display.setPlainText(
                    f"Excluding {len(self.ica.exclude)} components:\n" + ", ".join(parts)
                )
            else:
                self.exclude_display.setPlainText("No components excluded")
        except RuntimeError:
            logger.debug("Ignored RuntimeError updating exclusion info text", exc_info=True)

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
        self.btn_overlay.setEnabled(len(self.ica.exclude) > 0)

    def update_colors(self):
        """Update axis title colors and text for the current page to reflect exclusion state."""
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
        """Return True if a mouse event's screen position falls within ax's title text."""
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
                dlg = QDialog(self)
                dlg.setWindowTitle(f"IC{idx:03d} Properties")
                dlg_layout = QVBoxLayout(dlg)
                dlg_layout.addWidget(PlotCanvas(pf, dlg))
                self.prop_dialog = dlg
                dlg.exec()
                for extra in prop_figs[1:]:
                    close_figure_safely(extra)
        except Exception as e:
            logger.exception("Failed to open ICA component properties for IC%s due to error: %s", idx, e)


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

    def sync_from_sources(self):
        """Poll the sources widget's bad-channel list and update ica.exclude if it has changed."""
        if self.source_view is None:
            return
        try:
            if not hasattr(self.source_view, "mne"):
                return
            bads = set(self.source_view.mne.info.get("bads", []))
            new_exclude = sorted(
                self.ica_names.index(n) for n in bads if n in self.ica_names
            )
            if new_exclude == self.prev_exclude:
                return
            self.ica.exclude = new_exclude
            self.prev_exclude[:] = new_exclude
            self.update_colors()
            self.update_info()
            self.btn_overlay.setEnabled(len(self.ica.exclude) > 0)
        except Exception as e:
            logger.debug("Failed to sync exclusions from sources widget: %s", e, exc_info=True)


    # -------- Overlay --------

    def show_overlay(self):
        try:
            exclude = sorted(self.ica.exclude)
            if not exclude:
                return
            fig_overlay = self.ica.plot_overlay(self.raw, exclude=exclude, show=False)
            safe_patch_toolbar(fig_overlay)
            self.extra_figs.append(fig_overlay)
            overlay_dlg = QDialog(self)
            overlay_dlg.setWindowTitle("ICA Overlay - Before/After")
            overlay_dlg.setMinimumSize(600, 400)
            ol = QVBoxLayout(overlay_dlg)
            ol.addWidget(PlotCanvas(fig_overlay, overlay_dlg))
            overlay_dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
            overlay_dlg.show()
        except Exception as e:
            logger.debug("Failed to show ICA overlay figure: %s", e, exc_info=True)


    # -------- Cleanup --------

    def close_prop_figs(self):
        """Close any open component-properties figures and the properties dialog."""
        while self.current_prop_figs:
            close_figure_safely(self.current_prop_figs.pop())
        if self.prop_dialog is not None:
            try:
                self.prop_dialog.close()
            except Exception as e:
                logger.debug("Failed to close properties dialog cleanly: %s", e, exc_info=True)
            self.prop_dialog = None

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

    def teardown(self):
        """Stop the poll timer and close all matplotlib figures."""
        if self.poll_timer is not None:
            self.poll_timer.stop()
        self.cleanup_figures()

    def closeEvent(self, event):
        self.teardown()
        super().closeEvent(event)


# -------- Interactive runner --------

def ica_apply_run(action, data, parent):
    """Run background classification then open the ICA inspection dialog."""
    from mnetape.core.models import ICASolution

    ica_solution: ICASolution = data

    ic_labels = run_background_classification(ica_solution.ica, ica_solution.raw)
    initial_exclude = action.params.get("exclude") or get_auto_exclude(ic_labels)

    dialog = ICAInspectionDialog(
        ica=ica_solution.ica,
        raw=ica_solution.raw,
        auto_exclude=initial_exclude,
        ic_labels=ic_labels,
        parent=parent,
    )
    accepted = dialog.exec() == QDialog.DialogCode.Accepted
    dialog.teardown()

    if accepted:
        ica_solution.ica.exclude = sorted(dialog.ica.exclude)
        action.params["exclude"] = ica_solution.ica.exclude
    else:
        # Cancel: apply no exclusions but keep params as-is so badge stays
        ica_solution.ica.exclude = []

    return ica_solution


def ica_apply_needs_inspection(action):
    return action.params.get("exclude") is None


def ica_apply_build_editor_widget(data, action, parent, param_widgets=None):
    """Widget shown in ActionEditor.

    Shows saved exclusion status from action.params["exclude"]:
      None  = not yet reviewed
      []    = reviewed, nothing excluded
      [...]  = reviewed, components excluded

    Browse is enabled only when ICASolution is in memory (ICA has been run this session).
    param_widgets: the ActionEditor's param_widgets dict used to sync the exclude field.
    """
    from mnetape.core.models import ICASolution

    ica_solution = data if isinstance(data, ICASolution) else None

    container = QWidget(parent)
    vbox = QVBoxLayout(container)
    vbox.setContentsMargins(0, 0, 0, 8)
    vbox.setSpacing(4)

    status_label = QLabel()

    def refresh_label():
        current = action.params.get("exclude")
        if current is None:
            status_label.setText("Not yet reviewed. Click Browse to inspect components.")
            status_label.setStyleSheet("color: #E65100;")
        elif current:
            names = ", ".join(f"IC{i:03d}" for i in sorted(current))
            status_label.setText(f"Reviewed: excluding {names}")
            status_label.setStyleSheet("color: #2E7D32;")
        else:
            status_label.setText("Reviewed: no components excluded.")
            status_label.setStyleSheet("color: #2E7D32;")

    refresh_label()
    vbox.addWidget(status_label)

    btn = QPushButton("Browse Components...")
    btn.setEnabled(ica_solution is not None)
    if ica_solution is None:
        btn.setToolTip("Run ICA first to browse components")

    def open_dialog():
        if ica_solution is None:
            return
        ic_labels = run_background_classification(ica_solution.ica, ica_solution.raw)
        auto_exclude = get_auto_exclude(ic_labels)
        current = action.params.get("exclude")
        initial = list(current) if current else auto_exclude
        dialog = ICAInspectionDialog(
            ica=ica_solution.ica,
            raw=ica_solution.raw,
            auto_exclude=initial,
            ic_labels=ic_labels,
            parent=parent,
        )
        accepted = dialog.exec() == QDialog.DialogCode.Accepted
        dialog.teardown()
        if accepted:
            action.params["exclude"] = sorted(dialog.ica.exclude)
            if param_widgets is not None:
                w = param_widgets.get("exclude")
                if w is not None and hasattr(w, "setText"):
                    w.setText(", ".join(str(i) for i in action.params["exclude"]))
            refresh_label()

    btn.clicked.connect(open_dialog)
    vbox.addWidget(btn)
    return container


INTERACTIVE_RUNNER = InteractiveRunner(
    run=ica_apply_run,
    needs_inspection=ica_apply_needs_inspection,
    build_editor_widget=ica_apply_build_editor_widget,
    managed_params=("exclude",),
)
