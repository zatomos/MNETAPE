"""Analysis window for reviewing preprocessed EEG data across participants."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QKeySequence
from PyQt6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QStatusBar,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from mnetape.core.project import Project, Participant, Session, ParticipantStatus
from mnetape.gui.widgets import PlotCanvas

logger = logging.getLogger(__name__)

ROLE_PID = Qt.ItemDataRole.UserRole
ROLE_SID = Qt.ItemDataRole.UserRole + 1


class LoadWorker(QThread):
    """Background thread for loading processed session files."""

    finished = pyqtSignal(object)

    def __init__(self, paths: dict[str, list[Path]]):
        super().__init__()
        self.paths = paths

    def run(self):
        try:
            import mne
            result = {}
            for key, file_list in self.paths.items():
                try:
                    epochs_files = [f for f in file_list if "_epo.fif" in f.name or "epochs" in f.name]
                    evoked_files = [f for f in file_list if "_ave.fif" in f.name
                                    or ("evoked" in f.name and f not in epochs_files)]
                    raw_files = [f for f in file_list if f not in epochs_files and f not in evoked_files]
                    if epochs_files:
                        loaded = [
                            mne.read_epochs(str(f), preload=True, verbose=False)
                            for f in epochs_files
                        ]
                        result[key] = mne.concatenate_epochs(loaded) if len(loaded) > 1 else loaded[0]
                    elif evoked_files:
                        loaded = []
                        for f in evoked_files:
                            evokeds = mne.read_evokeds(str(f), verbose=False)
                            loaded.extend(evokeds)
                        result[key] = loaded[0] if len(loaded) == 1 else loaded
                    elif raw_files:
                        raw_loaded = []
                        evoked_loaded = []
                        for f in raw_files:
                            try:
                                raw_loaded.append(mne.io.read_raw_fif(str(f), preload=True, verbose=False))
                            except Exception:
                                try:
                                    evoked_loaded.extend(mne.read_evokeds(str(f), verbose=False))
                                except Exception as e2:
                                    logger.warning("Could not load %s as raw or evoked: %s", f.name, e2)
                        if raw_loaded:
                            result[key] = mne.concatenate_raws(raw_loaded) if len(raw_loaded) > 1 else raw_loaded[0]
                        elif evoked_loaded:
                            result[key] = evoked_loaded[0] if len(evoked_loaded) == 1 else evoked_loaded
                except Exception as e:
                    logger.warning("Could not load %s: %s", key, e)
            self.finished.emit(result)
        except Exception as e:
            logger.error("LoadWorker failed: %s", e)
            self.finished.emit(None)


class ComputeWorker(QThread):
    """Background thread for grand-average / plot computation."""

    finished = pyqtSignal(object)

    def __init__(self, fn: Callable):
        super().__init__()
        self._fn = fn

    def run(self):
        try:
            self.finished.emit(self._fn())
        except Exception as e:
            logger.warning("Compute worker failed: %s", e)
            self.finished.emit(None)


class AnalysisWindow(QMainWindow):
    """Window for multi-participant EEG analysis.

    Left panel: QTreeWidget listing participants and their preprocessed sessions.
    Right panel: QTabWidget with Grand Average ERP, Butterfly, and Topomap tabs.
    """

    def __init__(
        self,
        project: Project | None = None,
        project_dir: Path | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self.project = project
        self.project_dir = project_dir
        self._loaded_epochs: dict[str, object] = {}  # key: mne.Epochs
        self.worker: QThread | None = None

        self.setWindowTitle("Analysis")
        self.resize(1200, 760)

        self.setup_menu()
        self.setup_ui()
        self._rebuild_tree()

    # Menu

    def setup_menu(self):
        menubar = self.menuBar()
        file_menu = menubar.addMenu("File")

        open_proj = file_menu.addAction("Open Project...")
        open_proj.triggered.connect(self._open_project)

        file_menu.addSeparator()

        export_action = file_menu.addAction("Export Plots...")
        export_action.triggered.connect(self.export_plots)

        file_menu.addSeparator()

        close_action = file_menu.addAction("Close")
        close_action.setShortcut(QKeySequence.StandardKey.Close)
        close_action.triggered.connect(self.close)

    # UI

    def setup_ui(self):
        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.setCentralWidget(splitter)

        # ---- Left panel ----
        left_widget = QWidget()
        left_widget.setMaximumWidth(320)
        left_widget.setMinimumWidth(200)
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(8, 8, 8, 8)
        left_layout.setSpacing(6)

        left_layout.addWidget(QLabel("<b>Sessions</b>"))

        self.session_tree = QTreeWidget()
        self.session_tree.setHeaderHidden(True)
        self.session_tree.setColumnCount(1)
        left_layout.addWidget(self.session_tree)

        self.btn_load = QPushButton("Load Selected")
        self.btn_load.clicked.connect(self._load_selected)
        left_layout.addWidget(self.btn_load)

        splitter.addWidget(left_widget)

        # ---- Right panel ----
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)

        self.tabs = QTabWidget()
        right_layout.addWidget(self.tabs)

        # Grand Average ERP tab
        erp_widget = QWidget()
        erp_layout = QVBoxLayout(erp_widget)
        erp_layout.setContentsMargins(8, 8, 8, 8)
        erp_layout.setSpacing(6)

        erp_ctrl = QHBoxLayout()
        erp_ctrl.addWidget(QLabel("Condition:"))
        self.erp_condition_combo = QComboBox()
        self.erp_condition_combo.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        erp_ctrl.addWidget(self.erp_condition_combo)
        erp_ctrl.addWidget(QLabel("Channel type:"))
        self.erp_chtype_combo = QComboBox()
        self.erp_chtype_combo.addItems(["EEG", "MEG", "All"])
        erp_ctrl.addWidget(self.erp_chtype_combo)
        self.btn_erp_compute = QPushButton("Compute")
        self.btn_erp_compute.clicked.connect(self.compute_erp)
        erp_ctrl.addWidget(self.btn_erp_compute)
        erp_layout.addLayout(erp_ctrl)

        self.erp_canvas = PlotCanvas(parent=erp_widget)
        erp_layout.addWidget(self.erp_canvas)

        self.tabs.addTab(erp_widget, "Grand Average ERP")

        # Butterfly tab
        butterfly_widget = QWidget()
        butterfly_layout = QVBoxLayout(butterfly_widget)
        butterfly_layout.setContentsMargins(8, 8, 8, 8)
        butterfly_layout.setSpacing(6)

        butterfly_ctrl = QHBoxLayout()
        butterfly_ctrl.addWidget(QLabel("Condition:"))
        self.butterfly_condition_combo = QComboBox()
        self.butterfly_condition_combo.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        butterfly_ctrl.addWidget(self.butterfly_condition_combo)
        self.btn_butterfly_compute = QPushButton("Compute")
        self.btn_butterfly_compute.clicked.connect(self.compute_butterfly)
        butterfly_ctrl.addWidget(self.btn_butterfly_compute)
        butterfly_layout.addLayout(butterfly_ctrl)

        self.butterfly_canvas = PlotCanvas(parent=butterfly_widget)
        butterfly_layout.addWidget(self.butterfly_canvas)

        self.tabs.addTab(butterfly_widget, "Butterfly")

        # Topomap tab (placeholder)
        topo_placeholder = QLabel("Topomap view coming soon")
        topo_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        topo_placeholder.setStyleSheet("color: gray; font-size: 14px;")
        self.tabs.addTab(topo_placeholder, "Topomap")

        splitter.addWidget(right_widget)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)

        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)

    # Tree building

    def _rebuild_tree(self):
        """Populate the session tree with processed_files in each session."""
        self.session_tree.clear()
        if not self.project or not self.project_dir:
            return
        for p in self.project.participants:
            p_item = QTreeWidgetItem([p.id])
            font = p_item.font(0)
            font.setBold(True)
            p_item.setFont(0, font)
            p_item.setData(0, ROLE_PID, p.id)
            has_child = False
            for s in p.sessions:
                existing = [Path(f) for f in s.processed_files if f and Path(f).exists()]
                if not existing:
                    continue
                has_evoked = any("_ave.fif" in f.name or "evoked" in f.name for f in existing)
                has_epochs = any("_epo.fif" in f.name or "epochs" in f.name for f in existing)
                type_tag = "epochs" if has_epochs else ("evoked" if has_evoked else "raw")
                s_item = QTreeWidgetItem([f"ses-{s.id}  [{type_tag}]"])
                s_item.setCheckState(0, Qt.CheckState.Unchecked)
                s_item.setData(0, ROLE_PID, p.id)
                s_item.setData(0, ROLE_SID, s.id)
                p_item.addChild(s_item)
                has_child = True
            if has_child:
                self.session_tree.addTopLevelItem(p_item)
                p_item.setExpanded(True)

    # Loading

    def _load_selected(self):
        """Load processed files for all checked sessions in a background thread."""
        paths: dict[str, list[Path]] = {}
        for i in range(self.session_tree.topLevelItemCount()):
            p_item = self.session_tree.topLevelItem(i)
            pid = p_item.data(0, ROLE_PID)
            p = self.project.get_participant(pid)
            if not p:
                continue
            for j in range(p_item.childCount()):
                s_item = p_item.child(j)
                if s_item.checkState(0) == Qt.CheckState.Checked:
                    sid = s_item.data(0, ROLE_SID)
                    s = p.get_session(sid)
                    if s:
                        existing = [Path(f) for f in s.processed_files if f and Path(f).exists()]
                        if existing:
                            key = f"{pid}/ses-{sid}"
                            paths[key] = existing

        if not paths:
            QMessageBox.information(self, "Nothing selected", "Check at least one session to load.")
            return

        progress = QProgressDialog("Loading epochs...", None, 0, 0, self)
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.show()

        self.worker = LoadWorker(paths)
        self.worker.finished.connect(lambda result: self.on_loaded(result, progress))
        self.worker.start()

    def on_loaded(self, result: dict | None, progress: QProgressDialog):
        progress.close()
        self.worker = None
        if result is None:
            QMessageBox.warning(self, "Load Error", "Failed to load epoch files.")
            return
        self._loaded_epochs = result

        import mne as _mne
        n_epochs = sum(1 for v in result.values() if isinstance(v, _mne.BaseEpochs))
        n_raw = sum(1 for v in result.values() if isinstance(v, _mne.io.BaseRaw))
        msg = f"Loaded {len(result)} session(s)."
        if n_raw > 0 and n_epochs == 0:
            msg += " (raw data loaded - ERP analysis requires epochs files)"
        elif n_raw > 0:
            msg += f" ({n_raw} raw, {n_epochs} epochs)"
        self.status_bar.showMessage(msg)
        self.update_condition_combos()

    def update_condition_combos(self):
        """Populate condition combos from the union of event_id keys across loaded epochs/evoked."""
        import mne
        conditions: set[str] = set()
        for data in self._loaded_epochs.values():
            if isinstance(data, mne.BaseEpochs):
                conditions.update(data.event_id.keys())
            elif isinstance(data, mne.Evoked):
                if data.comment:
                    conditions.add(data.comment)
            elif isinstance(data, list):
                for item in data:
                    if isinstance(item, mne.Evoked) and item.comment:
                        conditions.add(item.comment)
        conditions_sorted = sorted(conditions)

        for combo in (self.erp_condition_combo, self.butterfly_condition_combo):
            current = combo.currentText()
            combo.clear()
            combo.addItems(conditions_sorted)
            if current in conditions_sorted:
                combo.setCurrentText(current)

    # Computation

    def compute_erp(self):
        """Compute grand-average ERP and display it."""
        condition = self.erp_condition_combo.currentText()
        ch_type = self.erp_chtype_combo.currentText().lower()
        if ch_type == "all":
            ch_type = None

        if not self._loaded_epochs:
            QMessageBox.information(self, "No data", "Load sessions first.")
            return

        def _compute():
            import mne

            evokeds = []
            for key, data in self._loaded_epochs.items():
                try:
                    if isinstance(data, mne.BaseEpochs):
                        epochs_sel = data[condition] if condition and condition in data.event_id else data
                        evokeds.append(epochs_sel.average())
                    elif isinstance(data, mne.Evoked):
                        evokeds.append(data)
                    elif isinstance(data, list):
                        for item in data:
                            if isinstance(item, mne.Evoked):
                                if not condition or not item.comment or item.comment == condition:
                                    evokeds.append(item)
                except Exception as e:
                    logger.warning("Could not average %s for condition %s: %s", key, condition, e)

            if not evokeds:
                return None

            grand_avg = mne.grand_average(evokeds)
            picks = ch_type if ch_type else "data"
            fig = grand_avg.plot(picks=picks, show=False, spatial_colors=True)
            return fig

        self.run_compute(_compute, self.erp_canvas)

    def compute_butterfly(self):
        """Compute butterfly plot and display it."""
        condition = self.butterfly_condition_combo.currentText()

        if not self._loaded_epochs:
            QMessageBox.information(self, "No data", "Load sessions first.")
            return

        def _compute():
            import mne

            evokeds = []
            for key, data in self._loaded_epochs.items():
                try:
                    if isinstance(data, mne.BaseEpochs):
                        epochs_sel = data[condition] if condition and condition in data.event_id else data
                        evokeds.append(epochs_sel.average())
                    elif isinstance(data, mne.Evoked):
                        evokeds.append(data)
                    elif isinstance(data, list):
                        for item in data:
                            if isinstance(item, mne.Evoked):
                                if not condition or not item.comment or item.comment == condition:
                                    evokeds.append(item)
                except Exception as e:
                    logger.warning("Could not average %s for condition %s: %s", key, condition, e)

            if not evokeds:
                return None

            grand_avg = mne.grand_average(evokeds)
            fig = grand_avg.plot(picks="data", show=False, spatial_colors=False)
            return fig

        self.run_compute(_compute, self.butterfly_canvas)

    def run_compute(self, fn: Callable, canvas: PlotCanvas):
        progress = QProgressDialog("Computing...", None, 0, 0, self)
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.show()

        worker = ComputeWorker(fn)
        worker.finished.connect(lambda fig: self.on_compute_done(fig, canvas, progress))
        worker.finished.connect(worker.deleteLater)
        worker.start()
        self.worker = worker

    def on_compute_done(self, fig, canvas: PlotCanvas, progress: QProgressDialog):
        progress.close()
        self.worker = None
        if fig is None:
            QMessageBox.warning(self, "Compute Error", "Could not compute the plot.")
            return
        canvas.update_figure(fig)
        self.status_bar.showMessage("Plot updated.")

    # File menu actions

    def openproject(self):
        project_dir = QFileDialog.getExistingDirectory(self, "Open Project Folder")
        if not project_dir:
            return
        path = Path(project_dir)
        if not (path / "project.json").exists():
            QMessageBox.warning(self, "Not a project", "No project.json found.")
            return
        try:
            self.project = Project.load(path)
            self.project_dir = path
            self.setWindowTitle(f"Analysis - {self.project.name}")
            self._rebuild_tree()
            self._loaded_epochs.clear()
            self.status_bar.showMessage(f"Opened: {self.project.name}")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Could not open project:\n{e}")

    def export_plots(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Export Folder")
        if not folder:
            return
        export_dir = Path(folder)
        exported = 0
        for tab_idx, (canvas, name) in enumerate(
            [(self.erp_canvas, "grand_average_erp"), (self.butterfly_canvas, "butterfly")]
        ):
            fig = canvas.canvas.figure if hasattr(canvas, "canvas") else None
            if fig is not None:
                try:
                    fig.savefig(str(export_dir / f"{name}.png"), dpi=150, bbox_inches="tight")
                    exported += 1
                except Exception as e:
                    logger.warning("Failed to export %s: %s", name, e)
        self.status_bar.showMessage(f"Exported {exported} plot(s) to {export_dir}.")
