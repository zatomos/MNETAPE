"""Main application window for the EEG preprocessing pipeline.

MainWindow is the top-level QMainWindow. It owns the shared AppState and instantiates the controller objects
that implement all user-facing operations.
The window itself only builds the menu, sets up the layout widgets, and provides update helpers that keep
the action list, code panel, and visualization panel in sync.
"""

import logging

import mne
from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtGui import QAction, QBrush, QColor, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from mnetape.actions.registry import get_action_by_id, get_action_title
from mnetape.core.codegen import generate_full_script, parse_script_to_actions
from mnetape.core.models import CUSTOM_ACTION_ID, DataType, ICASolution
from mnetape.core.project import ParticipantStatus, ProjectContext
from mnetape.gui.controllers.action_controller import ActionController, PROTECTED_ACTION_IDS
from mnetape.gui.controllers.file_handler import FileHandler
from mnetape.gui.controllers.nav_controller import NavController
from mnetape.gui.controllers.pipeline_runner import OperationCancelled, PipelineRunner
from mnetape.gui.controllers.state import AppState
from mnetape.gui.dialogs.action_result_dialog import ActionResultDialog
from mnetape.gui.dialogs.preferences_dialog import PreferencesDialog
from mnetape.gui.panels import CodePanel, VisualizationPanel
from mnetape.gui.widgets import ActionListItem, ActionListWidget

logger = logging.getLogger(__name__)


def make_type_header(data_type: DataType) -> QListWidgetItem:
    """Create a section header item for the given data type."""
    header = QListWidgetItem(f"── {data_type.label} ──")
    header.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
    header.setForeground(QBrush(QColor("#888888")))
    font = header.font()
    font.setItalic(True)
    header.setFont(font)
    header.setFlags(Qt.ItemFlag.NoItemFlags)
    header.setData(Qt.ItemDataRole.UserRole, -1)
    return header


class MainWindow(QMainWindow):
    """Top-level application window.

    Composes the left action-list panel, the right view stack (visualization or code editor), and a status bar.
    All operations are delegated to the controller objects stored as instance attributes.

    Attributes:
        state: Shared mutable application state.
        files: File I/O controller.
        runner: Pipeline execution controller.
        viz_panel: Visualisation panel.
        code_panel:Code editor panel.
        action_list: Pipeline action list.
        status: QStatusBar for transient messages.
        recent_menu: The File > Open Recent sub-menu.
        project_context: Optional project context when opened from ProjectWindow.
    """

    def __init__(self, project_context: ProjectContext | None = None):
        super().__init__()
        self.code_panel = None
        self.viz_panel = None
        self.view_stack = None
        self.btn_code = None
        self.btn_run = None
        self.btn_move_down = None
        self.btn_move_up = None
        self.action_list = None
        self.btn_add_action = None
        self.recent_menu = None
        self.btn_viz = None

        self.project_context: ProjectContext | None = project_context

        # Basic window setup
        self.setWindowTitle("MNETAPE")
        self.resize(1400, 900)

        # State
        self.state = AppState.create()
        self.state.data_states.close()
        self.open_dialogs: list = []

        # Helpers
        self.files = FileHandler(self)
        self.runner = PipelineRunner(self)
        self.action_ctrl = ActionController(self)
        self.nav = NavController(self)

        # DataStore shows a progress dialog when reading a file
        self.state.data_states.thread_runner = self.runner.run_in_thread

        # UI
        self.setup_menu()
        self.setup_ui()
        self.setup_shortcuts()

        self.raw_info_label = QLabel()
        self.raw_info_label.setStyleSheet("color: gray;")

        self.status = QStatusBar()
        self.setStatusBar(self.status)
        self.status.addPermanentWidget(self.raw_info_label)
        self.status.showMessage("Ready - Open a FIF file to begin")


    # -------- Menu setup --------

    def setup_menu(self):
        """Build the application menu bar."""
        menubar = self.menuBar()

        file_menu = menubar.addMenu("File")

        open_action = QAction("Open EEG File...", self)
        open_action.setShortcut(QKeySequence.StandardKey.Open)
        open_action.triggered.connect(self.files.open_file)
        file_menu.addAction(open_action)

        self.recent_menu = QMenu("Open Recent", self)
        self.recent_menu.aboutToShow.connect(self.files.refresh_recent_menu)
        file_menu.addMenu(self.recent_menu)

        close_action = QAction("Close File", self)
        close_action.setShortcut(QKeySequence.StandardKey.Close)
        close_action.triggered.connect(self.files.close_file)
        file_menu.addAction(close_action)

        file_menu.addSeparator()

        export_action = QAction("Export Processed...", self)
        export_action.triggered.connect(lambda checked: self.files.export_file())
        file_menu.addAction(export_action)

        file_menu.addSeparator()

        prefs_action = QAction("Preferences...", self)
        prefs_action.triggered.connect(self.open_preferences)
        file_menu.addAction(prefs_action)

        file_menu.addSeparator()

        quit_action = QAction("Quit", self)
        quit_action.setShortcut(QKeySequence.StandardKey.Quit)
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

        pipeline_menu = menubar.addMenu("Pipeline")

        new_action = QAction("New Pipeline", self)
        new_action.setShortcut(QKeySequence.StandardKey.New)
        new_action.triggered.connect(self.files.new_pipeline)
        pipeline_menu.addAction(new_action)

        save_action = QAction("Save Pipeline...", self)
        save_action.setShortcut(QKeySequence.StandardKey.Save)
        save_action.triggered.connect(self.files.save_pipeline)
        pipeline_menu.addAction(save_action)

        load_action = QAction("Load Pipeline...", self)
        load_action.triggered.connect(self.files.load_pipeline)
        pipeline_menu.addAction(load_action)

        pipeline_menu.addSeparator()

        run_all_action = QAction("Run All", self)
        run_all_action.setShortcut(QKeySequence("Ctrl+Shift+Return"))
        run_all_action.triggered.connect(self.runner.run_all)
        pipeline_menu.addAction(run_all_action)

    # -------- UI setup --------

    def setup_ui(self):
        """Build the central widget: action list on the left, view stack on the right."""
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QHBoxLayout(central)

        left_panel = QWidget()
        left_panel.setMaximumWidth(300)
        left_panel.setMinimumWidth(240)
        left_layout = QVBoxLayout(left_panel)

        left_layout.addWidget(QLabel("<b>Actions</b>"))

        self.btn_add_action = QPushButton("+ Add Action")
        self.btn_add_action.clicked.connect(self.action_ctrl.add_action)
        left_layout.addWidget(self.btn_add_action)

        self.action_list = ActionListWidget()
        self.action_list.itemClicked.connect(self.action_ctrl.on_action_clicked)
        self.action_list.itemDoubleClicked.connect(self.action_ctrl.on_action_double_clicked)
        self.action_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.action_list.customContextMenuRequested.connect(self.action_ctrl.show_action_context_menu)
        self.action_list.items_reordered.connect(self.action_ctrl.move_action_to)
        delete_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Delete), self.action_list)
        delete_shortcut.setContext(Qt.ShortcutContext.WidgetShortcut)
        delete_shortcut.activated.connect(self.action_ctrl.remove_action)
        left_layout.addWidget(self.action_list)

        move_btns = QHBoxLayout()

        self.btn_move_up = QPushButton("\u25b2")
        self.btn_move_up.setFixedWidth(40)
        self.btn_move_up.clicked.connect(lambda: self.action_ctrl.move_action(-1))
        self.btn_move_up.setEnabled(False)
        move_btns.addWidget(self.btn_move_up)

        self.btn_move_down = QPushButton("\u25bc")
        self.btn_move_down.setFixedWidth(40)
        self.btn_move_down.clicked.connect(lambda: self.action_ctrl.move_action(1))
        self.btn_move_down.setEnabled(False)
        move_btns.addWidget(self.btn_move_down)

        move_btns.addStretch()

        self.btn_run = QPushButton("\u25b6 Run All")
        self.btn_run.clicked.connect(self.runner.run_all)
        move_btns.addWidget(self.btn_run)

        left_layout.addLayout(move_btns)

        self.btn_finish = QPushButton("\u25b6\u25b6  Run Preprocessing")
        self.btn_finish.setStyleSheet(
            """
            QPushButton {
                background-color: #2E7D32;
                color: white;
                font-weight: bold;
                padding: 8px;
                border: none;
                border-radius: 5px;
                font-size: 13px;
            }
            QPushButton:hover { background-color: #388E3C; }
            QPushButton:pressed { background-color: #1B5E20; }
        """
        )
        self.btn_finish.clicked.connect(self.run_and_save)
        self.btn_finish.setVisible(self.project_context is not None)
        left_layout.addWidget(self.btn_finish)

        main_layout.addWidget(left_panel)

        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)

        toggle_layout = QHBoxLayout()
        self.btn_viz = QPushButton("Visualization")
        self.btn_viz.setCheckable(True)
        self.btn_viz.setChecked(True)
        self.btn_viz.clicked.connect(lambda: self.set_view_mode("viz"))
        toggle_layout.addWidget(self.btn_viz)

        self.btn_code = QPushButton("Code")
        self.btn_code.setCheckable(True)
        self.btn_code.clicked.connect(lambda: self.set_view_mode("code"))
        toggle_layout.addWidget(self.btn_code)

        toggle_layout.addStretch()
        right_layout.addLayout(toggle_layout)

        self.view_stack = QStackedWidget()

        self.viz_panel = VisualizationPanel()
        self.view_stack.addWidget(self.viz_panel)

        self.code_panel = CodePanel()
        self.code_panel.on_external_change = self.files.on_external_code_change
        self.code_panel.on_manual_edit = self.action_ctrl.on_manual_code_edit
        self.view_stack.addWidget(self.code_panel)

        right_layout.addWidget(self.view_stack)
        main_layout.addWidget(right_panel, 1)

    def setup_shortcuts(self):
        """Register global keyboard shortcuts not covered by menu accelerators."""
        shortcut = QShortcut(QKeySequence("Ctrl+Return"), self)
        shortcut.activated.connect(self.runner.run_all)


    # -------- Toggle between code/viz --------

    def set_view_mode(self, mode: str):
        """Switch the right panel between visualization and code editor.

        Args:
            mode: "viz" to show the visualization panel, or "code" to show the code editor.
        """
        if mode == "viz":
            self.view_stack.setCurrentWidget(self.viz_panel)
            self.btn_viz.setChecked(True)
            self.btn_code.setChecked(False)
        else:
            self.view_stack.setCurrentWidget(self.code_panel)
            self.btn_viz.setChecked(False)
            self.btn_code.setChecked(True)
            self.update_code()


    # -------- UI update --------

    def update_action_list(self, sync_code: bool = True):
        """Rebuild the action list widget and synchronize dependent UI elements.

        Repopulates action_list from state.actions, refreshes the step combo in the visualization panel,
        and optionally regenerates the code panel.

        Args:
            sync_code: When True, also call update_code(). Pass False when the code panel already reflects the
            current state to avoid a feedback loop (e.g. after a manual code edit).
        """
        self.action_list.clear()

        pipeline_type = DataType.RAW
        self.action_list.addItem(make_type_header(pipeline_type))

        for i, action in enumerate(self.state.actions):
            action_def = get_action_by_id(action.action_id)
            input_type = action_def.input_type if action_def else DataType.RAW
            output_type = action_def.output_type if action_def else DataType.RAW
            is_mismatch = (
                action.action_id not in PROTECTED_ACTION_IDS
                and input_type != DataType.ANY
                and input_type != pipeline_type
            )

            needs_inspection = (
                action_def is not None
                and action_def.interactive_runner is not None
                and action_def.interactive_runner.needs_inspection is not None
                and action_def.interactive_runner.needs_inspection(action)
            )

            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, i)
            widget = ActionListItem(i + 1, action, type_mismatch=is_mismatch,
                                    needs_inspection=needs_inspection)
            if action.action_id == "load_file":
                widget.run_btn.setVisible(False)
            item.setSizeHint(widget.sizeHint())
            widget.size_changed.connect(lambda it=item, w=widget: it.setSizeHint(w.sizeHint()))
            widget.run_clicked.connect(lambda _row, actual_row=i: self.runner.run_action_at(actual_row))
            self.action_list.addItem(item)
            self.action_list.setItemWidget(item, widget)

            if not is_mismatch:
                new_type = output_type
                if new_type != DataType.ANY and new_type != pipeline_type:
                    pipeline_type = new_type
                    self.action_list.addItem(make_type_header(pipeline_type))

        self.viz_panel.update_step_list(self.state.actions)
        if sync_code:
            self.update_code()
        self.update_button_states()

    def get_selected_action_row(self) -> int:
        """Return the action index of the currently selected list item, or -1."""
        item = self.action_list.currentItem()
        if item is None:
            return -1
        idx = item.data(Qt.ItemDataRole.UserRole)
        return idx if isinstance(idx, int) and idx >= 0 else -1

    def set_selected_action_row(self, action_row: int):
        """Select the given action list item."""
        for i in range(self.action_list.count()):
            item = self.action_list.item(i)
            if item and item.data(Qt.ItemDataRole.UserRole) == action_row:
                self.action_list.setCurrentRow(i)
                return

    def update_button_states(self):
        """Enable or disable the move-up and move-down buttons based on selection."""
        row = self.get_selected_action_row()
        has_selection = row >= 0
        is_protected = has_selection and self.state.actions[row].action_id in PROTECTED_ACTION_IDS
        above_protected = (
            has_selection and row > 0
            and self.state.actions[row - 1].action_id in PROTECTED_ACTION_IDS
        )
        self.btn_move_up.setEnabled(has_selection and not is_protected and not above_protected and row > 0)
        self.btn_move_down.setEnabled(has_selection and not is_protected and row < len(self.state.actions) - 1)

    def update_code(self):
        """Regenerate the full pipeline script and push it to the code panel."""
        code = generate_full_script(self.state.actions)
        self.code_panel.set_code(code)
        self.files.auto_save()

    def fallback_data(self, step: int):
        """Walk backward from step-1 to find the last computed data state.

        Returns:
            Tuple of (data, label).
        """
        for i in range(step - 1, -1, -1):
            if i == 0:
                if self.state.raw_original is not None:
                    return self.state.raw_original, "original"
            elif 0 < i <= len(self.state.data_states):
                stored = self.state.data_states[i - 1]
                if stored is not None:
                    data = stored.raw if isinstance(stored, ICASolution) else stored
                    action = self.state.actions[i - 1]
                    title = get_action_title(action)
                    return data, f"step {i}. {title}"
        return self.state.raw_original, "original"

    def update_visualization(self):
        """Refresh the visualization panel for the currently selected pipeline step."""
        step = self.viz_panel.current_step

        if step == 0:
            data_to_show = self.state.raw_original
            fallback_label = None
        elif 0 < step <= len(self.state.data_states):
            stored = self.state.data_states[step - 1]
            if stored is None:
                data_to_show, fallback_label = self.fallback_data(step)
            elif isinstance(stored, ICASolution):
                data_to_show = stored.raw
                fallback_label = None
            else:
                data_to_show = stored
                fallback_label = None
        else:
            data_to_show, fallback_label = self.fallback_data(step)

        self.viz_panel.update_plots(data_to_show, step, fallback_label)
        self.update_raw_info(data_to_show)

    def update_raw_info(self, data):
        if data is None:
            self.raw_info_label.setText("")
            return
        name = self.state.data_filepath.name if self.state.data_filepath else ""
        n_ch = len(data.ch_names)
        sfreq = data.info["sfreq"]
        if isinstance(data, mne.Epochs):
            n_epochs = len(data)
            self.raw_info_label.setText(f"{name}  ·  {n_ch} ch  ·  {sfreq:.0f} Hz  ·  {n_epochs} epochs")
        elif isinstance(data, mne.Evoked):
            n_ave = getattr(data, "nave", 0)
            dur = data.times[-1] - data.times[0] if len(data.times) else 0.0
            self.raw_info_label.setText(
                f"{name}  ·  {n_ch} ch  ·  {sfreq:.0f} Hz  ·  {dur:.3f} s  ·  nave={n_ave}"
            )
        else:
            dur = data.times[-1]
            self.raw_info_label.setText(f"{name}  ·  {n_ch} ch  ·  {sfreq:.0f} Hz  ·  {dur:.1f} s")

    # --------- Code generation and execution ---------

    def get_execution_code(self, index: int, action) -> tuple[str, str]:
        """Return (call_site, func_defs) for executing a single action.

        For custom/inline actions, func_defs is empty and call_site contains the raw code.
        For standard actions, generates the call-site and the action's function definition fresh.

        Args:
            index: Position of the action in the pipeline.
            action: The ActionConfig whose code to generate.

        Returns:
            Tuple of (call_site_str, func_defs_str).
        """
        if action.action_id == CUSTOM_ACTION_ID:
            return action.custom_code or "", ""

        action_def = get_action_by_id(action.action_id)
        if not action_def:
            return action.custom_code or "", ""

        context_type = self.runner.get_data_type_at(index)

        if action.is_custom and action.custom_code:
            # Custom-edited body: wrap in canonical signature so call site still works
            func_defs = action_def.build_function_def_with_body(action.action_id, action.custom_code, context_type)
            params = {**action_def.default_params(), **action.params}
            adv = action.advanced_params or None
            call_site = action_def.build_call_site(action.action_id, params, adv, context_type)
            return call_site, func_defs

        # Standard action: generate function def + call site using action_id as func name
        params = {**action_def.default_params(), **action.params}
        adv = action.advanced_params or None
        func_defs = action_def.build_function_def(action.action_id, context_type)
        call_site = action_def.build_call_site(action.action_id, params, adv, context_type)
        return call_site, func_defs

    def open_action_results(self, row: int):
        """Open the ActionResultDialog for the action at row."""
        if row < 0 or row >= len(self.state.actions):
            return
        action = self.state.actions[row]
        if action.result is None:
            return
        self.show_action_result(action.result, get_action_title(action))

    def show_action_result(self, result, title: str):
        """Open the ActionResultDialog for a given result and title."""

        dlg = ActionResultDialog(result, title, parent=self)
        self.open_dialogs.append(dlg)
        dlg.destroyed.connect(lambda: self.open_dialogs.remove(dlg) if dlg in self.open_dialogs else None)
        dlg.show()
        dlg.raise_()

    def open_preferences(self):
        dlg = PreferencesDialog(self.state, parent=self)
        dlg.exec()

    def showEvent(self, event):
        """Autoload project data on first show (standalone mode)."""
        super().showEvent(event)
        self.auto_load()

    def auto_load(self):
        """Load participant data files and pipeline from project context, if not already loaded."""
        if not self.project_context or self.state.raw_original:
            return
        ctx = self.project_context

        # Parse pipeline first so that set_montage is in state.actions before load_data_path triggers check_montage.
        participant_pipeline = ctx.project.participant_pipeline_path(
            ctx.project_dir, ctx.participant, ctx.session
        )
        project_default = ctx.project.pipeline_path(ctx.project_dir)
        pipeline_source = participant_pipeline if participant_pipeline.exists() else project_default
        if pipeline_source.exists():
            try:
                code = pipeline_source.read_text()
                self.state.actions = parse_script_to_actions(code)
                self.state.pipeline_filepath = participant_pipeline
                if participant_pipeline.exists():
                    self.code_panel.set_file(participant_pipeline)
                else:
                    self.code_panel.set_code(code)
            except Exception as e:
                logger.warning("Failed to load project pipeline: %s", e)

        # Load data after pipeline is parsed so check_montage sees set_montage in state.actions
        existing = [p for p in ctx.data_files if p.exists()]
        if len(existing) == 1:
            self.files.load_data_path(str(existing[0]))
        elif len(existing) > 1:
            self.load_and_concatenate(existing)

        # Update load_file params with actual file path
        if self.state.data_filepath and self.state.actions and self.state.actions[0].action_id == "load_file":
            self.state.actions[0].params["file_path"] = str(self.state.data_filepath)

        # Apply stored montage to raw_original so visualization reflects it without needing
        # to run the pipeline first (set_montage is already parsed into state.actions above).
        self.files.apply_stored_montage_if_present()

        if pipeline_source.exists():
            self.update_action_list()
            self.status.showMessage(f"Loaded pipeline: {pipeline_source.name}")

    def load_and_concatenate(self, paths: list):
        """Load multiple run files and concatenate them into a single Raw object.

        Uses mne.concatenate_raws which inserts BAD_boundary / EDGE annotations at run boundaries
        so downstream filters and epoch rejection handle them correctly.
        """
        n = len(paths)
        try:
            raw = self.runner.run_in_thread(
                lambda: self.concatenate_raws(paths),
                f"Loading {n} runs...",
            )
        except OperationCancelled:
            self.status.showMessage("Load cancelled")
            return
        except Exception as e:
            logger.exception("Failed to load/concatenate run files")
            QMessageBox.critical(self, "Error", f"Failed to load runs:\n{e}")
            return

        self.state.raw_original = raw
        self.state.data_filepath = paths[0]
        self.state.data_states.clear()
        for action in self.state.actions:
            action.reset()
        self.files.mark_load_file_complete(raw)
        self.update_action_list()
        self.update_visualization()
        self.status.showMessage(f"Loaded {n} runs - concatenated ({raw.times[-1]:.1f} s total)")

    @staticmethod
    def concatenate_raws(paths: list):
        raws = [mne.io.read_raw(str(p), preload=True, verbose=False) for p in paths]
        return mne.concatenate_raws(raws)

    def run_and_save(self):
        """Run all pipeline actions, export the final output, then mark the session preprocessed."""
        self.runner.run_all()

        ctx = self.project_context
        if not ctx:
            return
        if not (self.state.actions and all(a.status.name == "COMPLETE" for a in self.state.actions)):
            return

        last_data = self.state.data_states[-1] if self.state.data_states else None
        if last_data is None:
            ctx.on_status_update("done")
            return

        if isinstance(last_data, mne.BaseEpochs):
            file_type = "epochs"
            data_to_save = last_data
        elif isinstance(last_data, mne.Evoked):
            file_type = "evoked"
            data_to_save = last_data
        else:
            file_type = "preprocessed"
            data_to_save = last_data.raw if isinstance(last_data, ICASolution) else last_data

        # When the pipeline ends at epochs or evoked, also save the last raw intermediate.
        last_raw: mne.io.BaseRaw | None = None
        if file_type in ("epochs", "evoked"):
            for state in reversed(self.state.data_states):
                if isinstance(state, mne.io.BaseRaw):
                    last_raw = state
                    break

        # Ensure raw data is fully loaded into memory before saving.
        if isinstance(data_to_save, mne.io.BaseRaw) and not data_to_save.preload:
            logger.warning("data_to_save is not preloaded - forcing load_data() before save")
            try:
                data_to_save.load_data()
            except Exception as e:
                logger.error("Failed to preload data before save: %s", e)

        if isinstance(data_to_save, mne.BaseEpochs):
            n_samples = len(data_to_save)
        elif isinstance(data_to_save, mne.Evoked):
            n_samples = data_to_save.data.shape[1] if data_to_save.data is not None else 0
        else:
            n_samples = getattr(data_to_save, "n_times", 0)
        logger.info(
            "Auto-export: type=%s file_type=%s n_samples=%s preload=%s",
            type(data_to_save).__name__, file_type, n_samples,
            getattr(data_to_save, "preload", "N/A"),
        )
        if n_samples == 0:
            logger.error("data_to_save has 0 samples - aborting save to avoid creating an empty file")
            QMessageBox.warning(self, "Export Failed", "Pipeline output contains no data (0 samples).\nCheck your pipeline for actions that may have produced empty output.")
            ctx.on_status_update("error")
            return

        out_path = ctx.project.session_output_file(
            ctx.project_dir, ctx.participant, ctx.session, file_type, ctx.run_index
        )

        try:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            self.runner.run_in_thread(
                lambda: data_to_save.save(str(out_path), overwrite=True),
                f"Saving {file_type}...",
            )
            logger.info(
                "Saved %s: %s bytes",
                out_path.name,
                out_path.stat().st_size if out_path.exists() else "file missing!",
            )
        except Exception as e:
            logger.exception("Auto-export failed: %s", out_path)
            QMessageBox.warning(self, "Export Failed", f"Could not save output:\n{e}")
            ctx.on_status_update("error")
            return

        # Also save the last raw intermediate when output is epochs/evoked.
        if last_raw is not None:
            raw_path = ctx.project.session_output_file(
                ctx.project_dir, ctx.participant, ctx.session, "preprocessed", ctx.run_index
            )
            try:
                if not last_raw.preload:
                    last_raw.load_data()
                raw_path.parent.mkdir(parents=True, exist_ok=True)
                self.runner.run_in_thread(
                    lambda: last_raw.save(str(raw_path), overwrite=True),
                    "Saving preprocessed raw...",
                )
                logger.info("Saved raw intermediate: %s", raw_path.name)
            except Exception as e:
                logger.warning("Could not save raw intermediate: %s", e)

        # Track exported path(s) in processed_files before notifying project window.
        s = ctx.session
        paths_to_track = [str(out_path)]
        if last_raw is not None:
            raw_path_str = str(ctx.project.session_output_file(
                ctx.project_dir, ctx.participant, ctx.session, "preprocessed", ctx.run_index
            ))
            if raw_path_str not in paths_to_track:
                paths_to_track.append(raw_path_str)

        for out_str in paths_to_track:
            if ctx.run_index is not None and not s.merge_runs:
                while len(s.processed_files) <= ctx.run_index:
                    s.processed_files.append("")
                s.processed_files[ctx.run_index] = out_str
            elif out_str not in s.processed_files:
                s.processed_files.append(out_str)

        ctx.on_status_update(ParticipantStatus.DONE)

    def on_pipeline_complete(self):
        """Called by PipelineRunner when all pipeline actions complete successfully."""

    def cleanup(self):
        """Release resources. Called by ProjectWindow when the embedded session ends,
        or automatically from closeEvent in standalone mode."""
        self.state.data_states.close()

    def event(self, event):
        if event.type() == QEvent.Type.WindowActivate:
            modal = QApplication.activeModalWidget()
            if modal:
                modal.raise_()
                modal.activateWindow()
        return super().event(event)

    def closeEvent(self, event):
        """Handle close for standalone mode. In embedded mode, ProjectWindow calls cleanup()."""
        self.cleanup()
        # Notify project context when used as a standalone window (not embedded)
        if self.project_context:
            actions = self.state.actions
            if any(a.status.name == "ERROR" for a in actions):
                final_status = ParticipantStatus.ERROR
            elif actions and all(a.status.name == "COMPLETE" for a in actions):
                final_status = ParticipantStatus.DONE
            else:
                final_status = ParticipantStatus.PENDING
            try:
                self.project_context.on_status_update(final_status)
            except Exception as e:
                logger.warning("Failed to update participant status on close: %s", e)
        super().closeEvent(event)
