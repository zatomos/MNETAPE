"""ICA action widgets and interactive component inspection dialog.

Provides Full-screen dialog that shows component topographies, an embedded MNE sources plot,
ICLabel + EOG/ECG/muscle detection scores per component, and overlay comparison.
"""

from __future__ import annotations

import logging

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QApplication,
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
import matplotlib.pyplot as plt
import numpy as np

from mnetape.core.models import ActionStatus
from mnetape.gui.widgets import PlotCanvas

logger = logging.getLogger(__name__)


def format_component_labels(
    ic_labels: dict | None,
    detection_details: dict | None,
    n_components: int,
) -> list[str]:
    """Build multi-line labels for each ICA component for display in topography plot titles.

    Each label contains up to two lines:
        - Line 1: ICLabel prediction abbreviated to 3 chars + probability
        - Line 2: Detection flags with scores for methods that flagged this component

    Args:
        ic_labels: Dict returned by ICLabel containing "labels" and "y_pred_proba" keys, or None if ICLabel was skipped.
        detection_details: Dict produced by the classify step containing eog_indices, ecg_indices, muscle_indices,
            and corresponding score arrays.
        n_components: Total number of ICA components.

    Returns:
        List of label strings, one per component (empty string when no info is available).
    """

    result = [""] * n_components
    details = detection_details or {}

    # ICLabel prediction
    iclabel_lines = [""] * n_components
    if ic_labels and ic_labels.get("labels"):
        probs = ic_labels.get("y_pred_proba")
        if probs is None:
            probs = ic_labels.get("probabilities")
        if probs is not None:
            iclabel_lines = [
                f"{label[:3].upper()} {np.max(prob):.0%}"
                for label, prob in zip(ic_labels["labels"], probs)
            ]

    # Collect per-component detection flags
    eog_indices = set(details.get("eog_indices", []))
    ecg_indices = set(details.get("ecg_indices", []))
    muscle_indices = set(details.get("muscle_indices", []))
    eog_scores = details.get("eog_scores")
    ecg_scores = details.get("ecg_scores")
    muscle_scores = details.get("muscle_scores")

    # Build label lines
    for i in range(n_components):
        parts = []
        if iclabel_lines[i]:
            parts.append(f"ICL: {iclabel_lines[i]}")

        flags = []
        if i in eog_indices and eog_scores is not None:
            score = np.max(np.abs(eog_scores[i])) if np.ndim(eog_scores) > 1 else abs(eog_scores[i])
            flags.append(f"EOG:{score:.2f}")
        if i in ecg_indices and ecg_scores is not None:
            flags.append(f"ECG:{abs(ecg_scores[i]):.2f}")
        if i in muscle_indices and muscle_scores is not None:
            flags.append(f"MUS:{abs(muscle_scores[i]):.2f}")

        if flags:
            parts.append(" ".join(flags))
        result[i] = "\n".join(parts)

    return result


def safe_patch_toolbar(fig):
    """Wrap toolbar.set_message to swallow RuntimeError when the Qt widget is deleted during teardown.
    Without this, closing the dialog can trigger a RuntimeError if matplotlib tries to update the toolbar message
    after the widget is gone.
    """
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
    """Close a matplotlib figure, silently ignoring RuntimeError and KeyError.

    Args:
        fig: The matplotlib Figure to close.
    """
    try:
        plt.close(fig)
    except (RuntimeError, KeyError):
        logger.debug("Ignored error while closing matplotlib figure", exc_info=True)
    except Exception as e:
        logger.debug("Failed to close matplotlib figure cleanly: %s", e, exc_info=True)


class ICAInspectionDialog(QDialog):
    """Interactive dialog for reviewing and selecting ICA components for removal.

    Shows component topographies in paginated chunks alongside the ICA sources plot.
    Components can be toggled for exclusion by clicking on them.
    A collapsible overlay button compares the signal before and after applying ICA.

    Attributes:
        CHUNK_SIZE: Number of component topographies shown per page.
        ica: The fitted MNE ICA object (ica.exclude is modified in-place).
        raw: The MNE Raw object used for source plotting.
        labels: Per-component label strings (ICLabel + detection flags).
        page_idx: Current page of component topographies.
        poll_timer: Timer used to poll the sources widget for exclusion changes.
        closed_unexpectedly: True if the dialog was closed via window close rather than one of the action buttons.
    """

    CHUNK_SIZE = 15

    def __init__(self, ica, raw, auto_exclude, ic_labels=None, detection_details=None, parent=None):
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
        self.details = detection_details or {}
        self.labels = format_component_labels(ic_labels, self.details, ica.n_components_)
        self.ica.exclude = list(auto_exclude)
        self.ica_names = list(
            getattr(ica, "ica_names", None)
            or getattr(ica, "_ica_names", None)
            or [f"ICA{i:03d}" for i in range(ica.n_components_)]
        )

        # Pagination
        pick_list = list(range(ica.n_components_))
        self.pick_chunks = [
            pick_list[i:i + self.CHUNK_SIZE]
            for i in range(0, len(pick_list), self.CHUNK_SIZE)
        ]
        self.max_page = len(self.pick_chunks)

        # State
        self.page_idx: int = 0                      # Current page index for component topographies
        self.current_fig = None                     # Currently displayed component topographies figure
        self.source_fig = None                      # Figure for ICA sources plot
        self.source_view = None                     # The actual widget or figure used to display ICA sources
        self.prop_dialog: QDialog | None = None     # Dialog for component properties
        self.current_prop_figs: list = []           # Figures for currently open component properties
        self.extra_figs: list = []                  # Any additional figures that need cleanup
        self.prev_exclude: list[int] = []           # Track previous exclude state for syncing with sources widget
        self.closed_unexpectedly = True             # True if dialog is closed without applying
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

        # Left: component topographies with pagination
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

        # Exclusion summary
        self.exclude_display = QTextEdit()
        self.exclude_display.setReadOnly(True)
        self.exclude_display.setMaximumHeight(100)
        self.exclude_display.setFont(QFont("Consolas", 10))
        layout.addWidget(self.exclude_display)

        layout.addLayout(self.build_button_row())

    def build_info_row(self) -> QHBoxLayout:
        """Build the info row showing which detection methods were active/disabled/failed."""
        d = self.details
        has_eog = d.get("has_eog_channel", False)
        has_ecg = d.get("has_ecg_channel", False)
        eog_ch = d.get("eog_channel_names", [])
        ecg_ch = d.get("ecg_channel_names", [])

        methods, failed, disabled = [], [], []
        if d.get("enable_iclabel", True):
            methods.append("ICLabel")
        else:
            disabled.append("ICLabel")
        if d.get("enable_eog", True):
            methods.append(f"EOG ({', '.join(eog_ch)})") if has_eog else failed.append("EOG (no channels)")
        else:
            disabled.append("EOG")
        if d.get("enable_ecg", True):
            methods.append(f"ECG ({', '.join(ecg_ch)})") if has_ecg else failed.append("ECG (no channels)")
        else:
            disabled.append("ECG")
        if d.get("enable_muscle", True):
            methods.append("Muscle")
        else:
            disabled.append("Muscle")

        info_parts = []
        if methods:
            info_parts.append(f"Active: {', '.join(methods)}")
        if disabled:
            info_parts.append(f"Disabled: {', '.join(disabled)}")
        if failed:
            info_parts.append(f"Failed: {', '.join(failed)}")

        info_row = QHBoxLayout()
        info_row.setContentsMargins(0, 6, 0, 6)
        info_row.setSpacing(64)

        status_label = QLabel("\n".join(info_parts))
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
        """Build the bottom button row: Show Overlay, Apply Exclusion, Keep auto."""
        btn_layout = QHBoxLayout()

        self.btn_overlay = QPushButton("Show Overlay")
        self.btn_overlay.setToolTip("Show before/after comparison")
        self.btn_overlay.setEnabled(len(self.ica.exclude) > 0)
        self.btn_overlay.clicked.connect(self.show_overlay)
        btn_layout.addWidget(self.btn_overlay)
        btn_layout.addStretch()

        btn_done = QPushButton("Apply Exclusion")
        btn_done.setStyleSheet(
            "QPushButton { background-color: #2E7D32; color: white; "
            "font-weight: bold; padding: 8px 16px; border: none; border-radius: 5px; }"
            "QPushButton:hover { background-color: #388E3C; }"
        )
        btn_done.clicked.connect(self.accept)
        btn_layout.addWidget(btn_done)

        btn_cancel = QPushButton("Keep auto (NOT RECOMMENDED)")
        btn_cancel.clicked.connect(self.on_cancel)
        btn_layout.addWidget(btn_cancel)

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
        """Switch the component topography display to the given page index.

        Closes the previous figure, renders the new component topographies,
        installs the click handler, and updates pagination controls.

        Args:
            idx:  page index.
        """
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
        # Remove MNE's click handlers (they parse titles assuming "ICAxxxx" format)
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
        """Add or remove a component index from ica.exclude and refresh the UI.

        Args:
            idx:  component index to toggle.
        """
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
        """Return True if a mouse event's screen position falls within ax's title text.

        Args:
            event: A matplotlib MouseEvent.
            ax: The axes whose title bounding box is tested.

        Returns:
            True if the event position is inside the title bounding box.
        """
        if event.x is None or event.y is None:
            return False
        renderer = self.plot_components.canvas.figure.canvas.get_renderer()
        bbox = ax.title.get_window_extent(renderer)
        return bbox.contains(event.x, event.y)

    def on_click(self, event):
        """Handle left-click (toggle exclusion) and right-click (show properties) on topographies.

        Args:
            event: A matplotlib MouseEvent on the component figure canvas.
        """
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
            # Preserve non-ICA bads (e.g. EOG reference channels)
            existing_bads = self.source_view.mne.info.get("bads", [])
            non_ica_bads = [b for b in existing_bads if b not in ica_names_set]
            self.source_view.mne.info["bads"] = new_bads + non_ica_bads
            bads_set = set(new_bads)

            traces = getattr(self.source_view.mne, "traces", [])
            if traces:
                for trace in traces:
                    if trace.ch_name not in ica_names_set:
                        continue
                    trace.isbad = trace.ch_name in bads_set
                    if hasattr(trace, "update_color"):
                        trace.update_color()
                if hasattr(self.source_view, "update_yaxis_labels"):
                    self.source_view.update_yaxis_labels()
            elif hasattr(self.source_view, "_redraw"):
                self.source_view._redraw(update_data=False)

            if hasattr(self.source_view, "update"):
                self.source_view.update()
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


    # -------- Button handlers --------

    def on_cancel(self):
        """Handle the 'Keep auto' button: mark as not unexpectedly closed and reject."""
        self.closed_unexpectedly = False
        self.reject()


    # -------- Result --------

    def run(self) -> tuple:
        """Execute the dialog and return (raw, closed_unexpectedly)."""
        result = self.exec()
        if self.poll_timer is not None:
            self.poll_timer.stop()
        self.cleanup_figures()

        if result == QDialog.DialogCode.Accepted:
            self.ica.exclude = sorted(self.ica.exclude)
            return self.ica.apply(self.raw.copy(), verbose=False), False
        return None, self.closed_unexpectedly


def run_ica_inspection(ica, raw, auto_exclude, ic_labels=None, detection_details=None, parent=None):
    """Create and run an ICAInspectionDialog, returning the processed raw object.

    Args:
        ica: The fitted MNE ICA object.
        raw: The MNE Raw object to inspect and apply ICA to.
        auto_exclude: Initial list of component indices to mark as excluded.
        ic_labels: Optional ICLabel output dict for label display.
        detection_details: Optional detection details dict (scores, channel info, flags).
        parent: Optional parent QWidget.

    Returns:
        A tuple (new_raw | None, closed_unexpectedly: bool). new_raw is None when the user canceled or closed
        without applying.
    """
    if parent is None:
        parent = QApplication.activeWindow()
    dialog = ICAInspectionDialog(ica, raw, auto_exclude, ic_labels, detection_details, parent)
    return dialog.run()


def run_interactive_step(action, raw, parent=None):
    """Run the ICA manual-selection step as an interactive_runner callable.

    Extracts the fitted ICA object and classification results from action.step_state.

    Args:
        action: The ActionConfig for the ICA action; step_state must contain "scope" with an "ica" key populated
            by the classify step.
        raw: The MNE Raw object to pass to the inspection dialog.
        parent: Optional parent QWidget for dialogs.

    Returns:
        The processed MNE Raw object (ICA applied), or None if canceled.
    """
    scope = action.step_state.get("scope", {})
    ica = scope.get("ica")
    auto_exclude = scope.get("auto_exclude", [])
    ic_labels = scope.get("ic_labels")
    detection_details = scope.get("detection_details")
    result, closed = run_ica_inspection(ica, raw, auto_exclude, ic_labels, detection_details, parent=parent)
    if closed:
        action.status = ActionStatus.ERROR
        action.error_msg = "Manual selection closed."
        logger.warning("Manual ICA selection window closed before apply")
    return result
