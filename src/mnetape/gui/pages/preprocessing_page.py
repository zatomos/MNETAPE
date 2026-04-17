"""Preprocessing page widget for the EEG preprocessing pipeline.

QWidget that hosts an active preprocessing session. It owns the shared AppState and instantiates the controller objects
that implement all user-facing operations.
Builds the header bar, action list, code/visualization panels, and provides update helpers that keep the action list,
code panel, and visualization panel in sync.
"""

import ast
import logging
import re
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from mnetape.gui.pages.project_page import ProjectPage

import mne
from PyQt6.QtCore import QEvent, QSettings, Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QBrush, QColor, QDesktopServices, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from mnetape.actions.registry import get_action_by_id, get_action_title
from mnetape.core.codegen import extract_custom_preamble, generate_full_script, parse_script_to_actions
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

FUNCS_HEADER_RE = re.compile(r"^#\s*---\s*Functions\s*---[ \t]*$", re.MULTILINE)
PIPE_HEADER_RE = re.compile(r"^#\s*---\s*Pipeline\s*---[ \t]*$", re.MULTILINE)
BLOCK_HEADER_RE = re.compile(r"^#\s*\[\d+\]\s*(.+)$", re.MULTILINE)


def remove_func_def_from_text(text: str, func_name: str) -> str:
    """Remove a named function definition from a block of Python source.
    Uses the AST to find accurate line bounds; returns text unchanged on parse error."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return text
    lines = text.splitlines(keepends=True)
    for node in tree.body:
        if (
            isinstance(node, ast.FunctionDef)
            and node.name == func_name
            and node.lineno is not None
            and node.end_lineno is not None
        ):
            start: int = node.lineno - 1
            end: int = node.end_lineno
            while end < len(lines) and lines[end].strip() == "":
                end += 1
            return "".join(lines[:start]) + "".join(lines[end:])
    return text


def remove_pipeline_action_block(pipe_text: str, title: str) -> str:
    """Remove all '# [N] <title>' blocks (header + body until next header) from pipeline text."""
    block_re = re.compile(
        r"^#\s*\[\d+\]\s*" + re.escape(title) + r"\s*$",
        re.MULTILINE | re.IGNORECASE,
    )
    next_re = re.compile(r"^#\s*\[\d+\]", re.MULTILINE)
    result = pipe_text
    m = block_re.search(result)
    while m:
        next_m = next_re.search(result, m.end())
        end = next_m.start() if next_m else len(result)
        result = result[: m.start()] + result[end:]
        m = block_re.search(result)
    return result


def renumber_action_blocks(pipe_text: str) -> str:
    """Renumber # [N] Title markers sequentially from 1."""
    n = 0

    def repl(m: re.Match) -> str:
        nonlocal n
        n += 1
        return f"# [{n}] {m.group(1)}"

    return BLOCK_HEADER_RE.sub(repl, pipe_text)


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


class PreprocessingPage(QWidget):
    """Preprocessing session page.

    Composes the header bar (back button, participant label, nav buttons, status label), the left action-list panel,
    and the right view stack (visualization or code editor).
    All operations are delegated to the controller objects stored as instance attributes.

    Signals:
        status_message(str, int): Emit a status bar message with timeout.
        raw_info_changed(str): Emit updated raw info text for the status bar permanent widget.
        title_change(str): Request a window title change.
        close_requested(): User clicked the back button.
        navigate_requested(int): User clicked prev (-1) or next (+1).
        session_status_updated(str, str, object): (participant_id, session_id, status)

    Attributes:
        state: Shared mutable application state.
        files: File I/O controller.
        runner: Pipeline execution controller.
        viz_panel: Visualisation panel.
        code_panel: Code editor panel.
        action_list: Pipeline action list.
        project_context: Optional project context when opened from ProjectPage.
    """

    status_message = pyqtSignal(str, int)
    raw_info_changed = pyqtSignal(str)
    title_change = pyqtSignal(str)
    close_requested = pyqtSignal()
    navigate_requested = pyqtSignal(int)
    session_status_updated = pyqtSignal(str, str, object)

    def __init__(
        self,
        ctx: ProjectContext | None,
        settings: QSettings,
        nav_list: list,
        parent=None,
    ):
        super().__init__(parent)
        self.project_context: ProjectContext | None = ctx
        self._nav_list: list = nav_list

        # State
        self.state = AppState.create_with_settings(settings)
        self.state.data_states.close()
        self.open_dialogs: list = []
        self.generate_qc_after_pipeline = False

        # Helpers
        self.files = FileHandler(self)
        self.runner = PipelineRunner(self)
        self.action_ctrl = ActionController(self)
        self.nav = NavController(self)

        # DataStore shows a progress dialog when reading a file
        self.state.data_states.thread_runner = self.runner.run_in_thread

        # Widget declarations — setup_ui() assigns the real instances
        self.btn_prev = QPushButton()
        self._participant_label = QLabel()
        self._dirty_indicator = QLabel()
        self.btn_next = QPushButton()
        self.status_label = QLabel()
        self.btn_add_action = QPushButton()
        self.btn_undo = QPushButton()
        self.btn_redo = QPushButton()
        self.load_file_warning = QWidget()
        self.load_file_warning_label = QLabel()
        self.btn_restore_load_file = QPushButton()
        self.action_list = ActionListWidget()
        self.btn_move_up = QPushButton()
        self.btn_move_down = QPushButton()
        self.btn_run = QPushButton()
        self.btn_finish = QPushButton()
        self.btn_viz = QPushButton()
        self.btn_code = QPushButton()
        self.btn_qc_report = QPushButton()
        self.view_stack = QStackedWidget()
        self.viz_panel = VisualizationPanel()
        self.code_panel = CodePanel()

        # UI
        self.setup_ui()
        self.setup_shortcuts()

        # Connect status updated signal to the context callback
        if ctx:
            _ctx = ctx  # capture narrowed (non-None) reference for lambda
            self.session_status_updated.connect(
                lambda pid, sid, status: _ctx.on_status_update(status)
                if _ctx.participant.id == pid and _ctx.session.id == sid
                else None
            )

        self.emit_status("Ready - Open an EEG file to begin")

    # -------- Status bar helpers --------

    def emit_status(self, msg: str, timeout: int = 0):
        """Emit a status bar message via signal."""
        self.status_message.emit(msg, timeout)

    # -------- UI setup --------

    def setup_ui(self):
        """Build the page: header bar on top, action list on the left, view stack on the right."""
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ---- Header bar ----
        header = QWidget()
        header.setObjectName("prep_header")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(8, 6, 8, 6)
        header_layout.setSpacing(8)

        btn_back = QPushButton("← Back")
        btn_back.setObjectName("btn_back_to_project")
        btn_back.setFixedWidth(90)
        btn_back.clicked.connect(self.on_back_clicked)
        header_layout.addWidget(btn_back)

        v_sep = QFrame()
        v_sep.setFrameShape(QFrame.Shape.VLine)
        v_sep.setStyleSheet("color: #D5D5D8;")
        header_layout.addWidget(v_sep)

        self.btn_prev = QPushButton("‹")
        self.btn_prev.setObjectName("btn_prev_step")
        self.btn_prev.setFixedSize(32, 28)
        self.btn_prev.setEnabled(False)
        self.btn_prev.setToolTip("Previous run")
        self.btn_prev.clicked.connect(lambda: self.on_navigate(-1))
        header_layout.addWidget(self.btn_prev)

        self._participant_label = QLabel()
        self._participant_label.setObjectName("prep_participant_label")
        header_layout.addWidget(self._participant_label)

        self._dirty_indicator = QLabel("●")
        self._dirty_indicator.setStyleSheet("color: #CC7700; font-size: 10px;")
        self._dirty_indicator.setToolTip("Pipeline has unsaved changes (Ctrl+S to save)")
        self._dirty_indicator.setVisible(False)
        header_layout.addWidget(self._dirty_indicator)

        self.btn_next = QPushButton("›")
        self.btn_next.setObjectName("btn_next_step")
        self.btn_next.setFixedSize(32, 28)
        self.btn_next.setEnabled(False)
        self.btn_next.setToolTip("Next run")
        self.btn_next.clicked.connect(lambda: self.on_navigate(+1))
        header_layout.addWidget(self.btn_next)

        header_layout.addStretch()

        btn_set_default = QPushButton("Set as Default Pipeline")
        btn_set_default.setObjectName("btn_set_default_pipeline")
        btn_set_default.setToolTip("Save current pipeline as the project default")
        btn_set_default.clicked.connect(self.set_default_pipeline_stub)
        header_layout.addWidget(btn_set_default)

        btn_use_default = QPushButton("Use Default Pipeline")
        btn_use_default.setObjectName("btn_use_default_pipeline")
        btn_use_default.setToolTip("Reset this participant's pipeline to the project default")
        btn_use_default.clicked.connect(self.use_default_pipeline_stub)
        header_layout.addWidget(btn_use_default)

        v_sep2 = QFrame()
        v_sep2.setFrameShape(QFrame.Shape.VLine)
        v_sep2.setStyleSheet("color: #D5D5D8;")
        header_layout.addWidget(v_sep2)

        self.status_label = QLabel("Status: Pending")
        self.status_label.setStyleSheet("color: #888888; font-size: 11px; font-weight: bold;")
        self.status_label.setMinimumWidth(140)
        header_layout.addWidget(self.status_label)

        outer.addWidget(header)

        h_sep = QFrame()
        h_sep.setFrameShape(QFrame.Shape.HLine)
        h_sep.setStyleSheet("color: #D5D5D8;")
        outer.addWidget(h_sep)

        # ---- Main content area ----
        content = QWidget()
        content_layout = QHBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)

        # Left panel
        left_panel = QWidget()
        left_panel.setMaximumWidth(300)
        left_panel.setMinimumWidth(240)
        left_layout = QVBoxLayout(left_panel)

        left_layout.addWidget(QLabel("<b>Actions</b>"))

        action_bar = QHBoxLayout()

        self.btn_add_action = QPushButton("+ Add Action")
        self.btn_add_action.clicked.connect(self.action_ctrl.add_action)
        action_bar.addWidget(self.btn_add_action, 1)

        self.btn_undo = QPushButton("\u21ba")
        self.btn_undo.setFixedSize(34, 28)
        self.btn_undo.setStyleSheet("padding: 0;")
        self.btn_undo.setToolTip("Undo (Ctrl+Z)")
        self.btn_undo.setEnabled(False)
        self.btn_undo.clicked.connect(self.undo_pipeline)
        action_bar.addWidget(self.btn_undo)

        self.btn_redo = QPushButton("\u21bb")
        self.btn_redo.setFixedSize(34, 28)
        self.btn_redo.setStyleSheet("padding: 0;")
        self.btn_redo.setToolTip("Redo (Ctrl+Y)")
        self.btn_redo.setEnabled(False)
        self.btn_redo.clicked.connect(self.redo_pipeline)
        action_bar.addWidget(self.btn_redo)

        left_layout.addLayout(action_bar)

        self.load_file_warning = QFrame()
        self.load_file_warning.setStyleSheet(
            "QFrame { background: #FFF3CD; border: 1px solid #FFDA6A; border-radius: 4px; }"
            "QLabel { color: #664D03; }"
        )
        warning_layout = QHBoxLayout(self.load_file_warning)
        warning_layout.setContentsMargins(8, 6, 8, 6)
        warning_layout.setSpacing(8)
        self.load_file_warning_label = QLabel("Missing Load File!")
        self.load_file_warning_label.setWordWrap(True)
        warning_layout.addWidget(self.load_file_warning_label, 1)
        self.btn_restore_load_file = QPushButton("Restore Load File")
        self.btn_restore_load_file.clicked.connect(self.restore_load_file_action)
        warning_layout.addWidget(self.btn_restore_load_file)
        self.load_file_warning.setVisible(False)
        left_layout.addWidget(self.load_file_warning)

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

        content_layout.addWidget(left_panel)

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

        self.btn_qc_report = QPushButton("Open QC Report")
        self.btn_qc_report.setVisible(False)
        self.btn_qc_report.clicked.connect(self.open_qc_report)
        toggle_layout.addWidget(self.btn_qc_report)

        right_layout.addLayout(toggle_layout)

        self.view_stack = QStackedWidget()

        self.view_stack.addWidget(self.viz_panel)

        self.code_panel.on_external_change = self.files.on_external_code_change
        self.code_panel.on_manual_edit = self.action_ctrl.on_manual_code_edit
        self.view_stack.addWidget(self.code_panel)

        right_layout.addWidget(self.view_stack)
        content_layout.addWidget(right_panel, 1)

        outer.addWidget(content, 1)

        # Update header from context
        self.refresh_header_from_context()

    def refresh_header_from_context(self):
        """Update header participant label and nav buttons from the current context and nav list."""
        ctx = self.project_context
        if ctx is None:
            return
        p = ctx.participant
        s = ctx.session
        run_index = ctx.run_index
        run_text = (run_index + 1) if run_index is not None else "merged"
        if self._participant_label is not None:
            self._participant_label.setText(
                f"<b>{p.id}</b>  /  ses-{s.id}  /  run-{run_text}  ·  {ctx.project.name}"
            )
        self.update_nav_buttons()
        self.update_status_label_from_session()

    def update_nav_buttons(self):
        """Enable/disable and re-label prev/next buttons based on current position."""
        ctx = self.project_context
        nav = self._nav_list
        if not ctx or not nav:
            return
        pos = next(
            (i for i, (p, s, r) in enumerate(nav)
             if p.id == ctx.participant.id and s.id == ctx.session.id and r == ctx.run_index),
            None,
        )
        btn_prev = self.btn_prev
        btn_next = self.btn_next
        if btn_prev is None or btn_next is None:
            return

        if pos is None or pos == 0:
            btn_prev.setEnabled(False)
            btn_prev.setText("‹")
            btn_prev.setToolTip("Previous run")
        else:
            btn_prev.setEnabled(True)
            prev_p, prev_s, _ = nav[pos - 1]
            same_ses = prev_s.id == ctx.session.id and prev_p.id == ctx.participant.id
            btn_prev.setText("‹" if same_ses else "«")
            btn_prev.setToolTip("Previous run" if same_ses else "Previous session")

        if pos is None or pos >= len(nav) - 1:
            btn_next.setEnabled(False)
            btn_next.setText("›")
            btn_next.setToolTip("Next run")
        else:
            btn_next.setEnabled(True)
            next_p, next_s, _ = nav[pos + 1]
            same_ses = next_s.id == ctx.session.id and next_p.id == ctx.participant.id
            btn_next.setText("›" if same_ses else "»")
            btn_next.setToolTip("Next run" if same_ses else "Next session")

    def update_status_label_from_session(self):
        """Update the preprocessing header status label from the current context."""
        ctx = self.project_context
        if ctx is None:
            return
        self.update_status_label(ctx.session, ctx.run_index)

    def update_status_label(self, s, run_index: int | None = None):
        """Update the preprocessing header status label. Called externally by ProjectPage."""
        if run_index is not None and not s.merge_runs:
            is_processed = (
                run_index < len(s.processed_files) and bool(s.processed_files[run_index])
            )
            text = "Preprocessed" if is_processed else "Pending"
            color = "#2E7D32" if is_processed else "#888888"
        else:
            _display = {
                "done": ("Preprocessed", "#2E7D32"),
                "error": ("Error", "#C62828"),
                "pending": ("Pending", "#888888"),
                "incomplete": ("Incomplete", "#E65100"),
            }
            text, color = _display.get(s.status, (s.status.title(), "#888888"))
        self.status_label.setText(f"Status: {text}")
        self.status_label.setStyleSheet(f"color: {color}; font-size: 11px; font-weight: bold;")

    def set_default_pipeline_stub(self, *, confirm: bool = True):
        """Forward set-default-pipeline to the project page via the window."""
        win = self.window()
        project_page: "ProjectPage | None" = getattr(win, "project_page", None) if win is not None else None
        if project_page is not None:
            project_page.set_default_pipeline(confirm=confirm)

    def use_default_pipeline_stub(self):
        """Forward use-default-pipeline to the project page via the window."""
        win = self.window()
        project_page: "ProjectPage | None" = getattr(win, "project_page", None) if win is not None else None
        if project_page is not None:
            project_page.use_default_pipeline()

    def setup_shortcuts(self):
        """Register global keyboard shortcuts not covered by menu accelerators."""
        shortcut = QShortcut(QKeySequence("Ctrl+Return"), self)
        shortcut.activated.connect(self.runner.run_all)

        undo_shortcut = QShortcut(QKeySequence.StandardKey.Undo, self)
        undo_shortcut.activated.connect(self.undo_pipeline)

        redo_shortcut = QShortcut(QKeySequence.StandardKey.Redo, self)
        redo_shortcut.activated.connect(self.redo_pipeline)


    # -------- Toggle between code/viz --------

    def set_view_mode(self, mode: str):
        """Switch the right panel between visualization and code editor."""
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
        """Rebuild the action list widget and synchronize dependent UI elements."""
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
        if not self.has_load_file_function() or not self.has_load_file_call_site():
            self.load_file_warning.setVisible(True)
        else:
            self.load_file_warning.setVisible(False)
        self.update_button_states()

    def has_load_file_function(self) -> bool:
        """Return True when the code panel contains a load_file function definition."""
        code = self.code_panel.get_code()
        return bool(re.search(r"^\s*def\s+load_file(?:_\d+)?\s*\(", code, re.MULTILINE))

    def has_load_file_call_site(self) -> bool:
        """Return True when the code panel contains a load_file call site."""
        code = self.code_panel.get_code()
        return bool(re.search(r"\braw\s*=\s*load_file(?:_\d+)?\s*\(", code))

    def restore_load_file_action(self) -> None:
        """Surgically insert load_file back without regenerating the whole script.

        Locates the # --- Functions --- and # --- Pipeline --- markers, removes any
        existing load_file entries, then inserts a fresh function def (after the
        Functions header) and a fresh call-site block (as the first block after the
        Pipeline header). Everything else — custom code, imports, comments, helper
        functions — is left exactly as-is. Falls back to a manual instructions
        dialog if either section marker is missing.
        """
        action_def = get_action_by_id("load_file")
        if not action_def:
            return

        # Resolve the file path from current state
        existing_load = next(
            (a for a in self.state.actions if a.action_id == "load_file"), None
        )
        file_path = (
            str(existing_load.params.get("file_path") or "")
            if existing_load
            else str(self.state.data_filepath or "")
        )
        load_params = {**action_def.default_params(), "file_path": file_path, "preload": True}
        func_def_text = action_def.build_function_def("load_file")
        call_site_text = action_def.build_call_site("load_file", load_params)

        code = self.code_panel.get_code()
        funcs_m = FUNCS_HEADER_RE.search(code)
        pipe_m = PIPE_HEADER_RE.search(code)

        if not funcs_m or not pipe_m:
            missing = [h for h, m in [
                ("# --- Functions ---", funcs_m), ("# --- Pipeline ---", pipe_m)
            ] if not m]
            QMessageBox.warning(
                self.window(),
                "Cannot Restore Load File",
                "Missing section header(s): " + ", ".join(missing) + ".\n\n"
                "Add the following manually:\n\n"
                "In the # --- Functions --- section:\n" + func_def_text + "\n\n"
                "In the # --- Pipeline --- section (first block):\n"
                "# [1] Load File\n" + call_site_text,
            )
            return

        self.state.push_undo()
        self.mark_pipeline_dirty()

        preamble = code[: funcs_m.start()]
        funcs_section = code[funcs_m.start() : pipe_m.start()]
        pipe_section = code[pipe_m.start() :]

        # Functions section: remove old load_file def; insert new one right after header.
        # Strip leading blank lines from the remainder so we control spacing exactly.
        funcs_section = remove_func_def_from_text(funcs_section, "load_file")
        header_end_m = re.search(r"#\s*---\s*Functions\s*---[ \t]*\n", funcs_section)
        if header_end_m:
            pos = header_end_m.end()
            rest = funcs_section[pos:].lstrip("\n")
            funcs_section = funcs_section[:pos] + "\n" + func_def_text + "\n\n" + rest

        # Pipeline section: remove old load_file block; insert new one as first block; renumber.
        # Same blank-line control: strip leading newlines from the remainder.
        pipe_section = remove_pipeline_action_block(pipe_section, "Load File")
        header_end_m = re.search(r"#\s*---\s*Pipeline\s*---[ \t]*\n", pipe_section)
        if header_end_m:
            pos = header_end_m.end()
            rest = pipe_section[pos:].lstrip("\n")
            new_block = "\n# [1] Load File\n" + call_site_text + "\n\n"
            pipe_section = pipe_section[:pos] + new_block + rest
        pipe_section = renumber_action_blocks(pipe_section)

        new_code = preamble + funcs_section + pipe_section

        # Re-sync state from the modified code
        new_actions = parse_script_to_actions(new_code)
        self.state.actions = new_actions
        self.state.custom_preamble = extract_custom_preamble(new_code, new_actions)
        self.state.data_states.clear()
        if self.state.raw_original is not None and new_actions:
            self.files.mark_load_file_complete(self.state.raw_original)

        self.code_panel.set_code(new_code)
        self.update_action_list(sync_code=False)
        self.update_visualization()
        self.emit_status("Restored load_file action")

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
        """Enable or disable the move-up, move-down, undo, and redo buttons based on selection."""
        row = self.get_selected_action_row()
        has_selection = row >= 0
        is_protected = has_selection and self.state.actions[row].action_id in PROTECTED_ACTION_IDS
        above_protected = (
            has_selection and row > 0
            and self.state.actions[row - 1].action_id in PROTECTED_ACTION_IDS
        )
        self.btn_move_up.setEnabled(has_selection and not is_protected and not above_protected and row > 0)
        self.btn_move_down.setEnabled(has_selection and not is_protected and row < len(self.state.actions) - 1)
        self.btn_undo.setEnabled(bool(self.state.undo_stack))
        self.btn_redo.setEnabled(bool(self.state.redo_stack))

    def mark_pipeline_dirty(self):
        """Mark the pipeline as having unsaved changes and show the dirty indicator."""
        self.state.pipeline_dirty = True
        self.state.pipeline_modified_this_session = True
        if self.project_context is not None:
            self._dirty_indicator.setVisible(True)

    def clear_pipeline_dirty(self):
        """Mark the pipeline as saved and hide the dirty indicator."""
        self.state.pipeline_dirty = False
        self._dirty_indicator.setVisible(False)

    def undo_pipeline(self):
        """Restore the previous pipeline snapshot from the undo stack."""
        snapshot = self.state.pop_undo()
        if snapshot is None:
            return
        self.state.actions = snapshot
        self.mark_pipeline_dirty()
        self.state.data_states.truncate(0)
        for action in self.state.actions:
            action.reset()
        self.update_action_list()

    def redo_pipeline(self):
        """Re-apply the next pipeline snapshot from the redo stack."""
        snapshot = self.state.pop_redo()
        if snapshot is None:
            return
        self.state.actions = snapshot
        self.mark_pipeline_dirty()
        self.state.data_states.truncate(0)
        for action in self.state.actions:
            action.reset()
        self.update_action_list()

    # -------- Unsaved-changes guard --------

    def confirm_discard_if_dirty(self) -> bool:
        """Return True if it is safe to leave (saved or user chose to discard).

        Shows a Save / Discard / Cancel dialog when the pipeline has unsaved changes.
        Only active in project mode — standalone windows never prompt for save.
        """
        if self.project_context is None or not self.state.pipeline_dirty:
            return True
        reply = QMessageBox.question(
            self.window(),
            "Unsaved Changes",
            "The pipeline has unsaved changes.\nDo you want to save before leaving?",
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )
        if reply == QMessageBox.StandardButton.Save:
            return self.files.save_pipeline_default()
        return reply == QMessageBox.StandardButton.Discard

    def offer_set_as_default(self):
        """Ask whether the just-saved pipeline should become the project default."""
        if self.project_context is None:
            return
        reply = QMessageBox.question(
            self.window(),
            "Apply to All Participants?",
            "Do you also want to apply this pipeline as the project default for all participants?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.set_default_pipeline_stub(confirm=False)

    def on_back_clicked(self):
        if not self.confirm_discard_if_dirty():
            return
        if self.state.pipeline_modified_this_session and not self.state.pipeline_dirty:
            self.offer_set_as_default()
        self.close_requested.emit()

    def on_navigate(self, direction: int):
        if not self.confirm_discard_if_dirty():
            return
        if self.state.pipeline_modified_this_session and not self.state.pipeline_dirty:
            self.offer_set_as_default()
        self.navigate_requested.emit(direction)

    def update_code(self):
        """Regenerate the full pipeline script and push it to the code panel."""
        code = generate_full_script(self.state.actions, extra_preamble=self.state.custom_preamble or None)
        self.code_panel.set_code(code)

    def fallback_data(self, step: int):
        """Walk backward from step-1 to find the last computed data state."""
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
            self.raw_info_changed.emit("")
            return
        name = self.state.data_filepath.name if self.state.data_filepath else ""
        n_ch = len(data.ch_names)
        sfreq = data.info["sfreq"]
        if isinstance(data, mne.Epochs):
            n_epochs = len(data)
            self.raw_info_changed.emit(f"{name}  ·  {n_ch} ch  ·  {sfreq:.0f} Hz  ·  {n_epochs} epochs")
        elif isinstance(data, mne.Evoked):
            n_ave = getattr(data, "nave", 0)
            dur = data.times[-1] - data.times[0] if len(data.times) else 0.0
            self.raw_info_changed.emit(
                f"{name}  ·  {n_ch} ch  ·  {sfreq:.0f} Hz  ·  {dur:.3f} s  ·  nave={n_ave}"
            )
        else:
            dur = data.times[-1]
            self.raw_info_changed.emit(f"{name}  ·  {n_ch} ch  ·  {sfreq:.0f} Hz  ·  {dur:.1f} s")

    # --------- Code generation and execution ---------

    def get_execution_code(self, index: int, action) -> tuple[str, str]:
        """Return (call_site, func_defs) for executing a single action."""
        if action.action_id == CUSTOM_ACTION_ID:
            return action.custom_code or "", ""

        action_def = get_action_by_id(action.action_id)
        if not action_def:
            return action.custom_code or "", ""

        context_type = self.runner.get_data_type_at(index)

        if action.is_custom and action.custom_code:
            func_defs = action_def.build_function_def_with_body(action.action_id, action.custom_code, context_type, params=action.params)
            params = {**action_def.default_params(), **action.params}
            adv = action.advanced_params or None
            call_site = action_def.build_call_site(action.action_id, params, adv, context_type)
            return call_site, func_defs

        params = {**action_def.default_params(), **action.params}
        adv = action.advanced_params or None
        func_defs = action_def.build_function_def(action.action_id, context_type, params=params)
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
        dlg = ActionResultDialog(result, title, parent=self.window())
        self.open_dialogs.append(dlg)
        def _on_dlg_destroyed():
            if dlg in self.open_dialogs:
                self.open_dialogs.remove(dlg)
        dlg.destroyed.connect(_on_dlg_destroyed)
        dlg.show()
        dlg.raise_()

    def open_preferences(self):
        dlg = PreferencesDialog(self.state, parent=self.window())
        dlg.exec()

    def auto_load(self):
        """Load participant data files and pipeline from project context, if not already loaded."""
        if not self.project_context or self.state.raw_original:
            return
        ctx = self.project_context

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

        existing = [p for p in ctx.data_files if p.exists()]
        if len(existing) == 1:
            self.files.load_data_path(str(existing[0]))
        elif len(existing) > 1:
            self.load_and_concatenate(existing)

        if self.state.data_filepath and self.state.actions and self.state.actions[0].action_id == "load_file":
            self.state.actions[0].params["file_path"] = str(self.state.data_filepath)

        self.files.apply_stored_montage_if_present()

        if pipeline_source.exists():
            self.update_action_list()
            self.emit_status(f"Loaded pipeline: {pipeline_source.name}")

    def load_and_concatenate(self, paths: list):
        """Load multiple run files and concatenate them into a single Raw object."""
        n = len(paths)
        try:
            raw = self.runner.run_in_thread(
                lambda: self.concatenate_raws(paths),
                f"Loading {n} runs...",
            )
        except OperationCancelled:
            self.emit_status("Load cancelled")
            return
        except Exception as e:
            logger.exception("Failed to load/concatenate run files")
            QMessageBox.critical(self.window(), "Error", f"Failed to load runs:\n{e}")
            return

        self.state.raw_original = raw
        self.state.data_filepath = paths[0]
        self.state.data_states.clear()
        for action in self.state.actions:
            action.reset()
        self.files.mark_load_file_complete(raw)
        self.update_action_list()
        self.update_visualization()
        self.emit_status(f"Loaded {n} runs - concatenated ({raw.times[-1]:.1f} s total)")

    @staticmethod
    def concatenate_raws(paths: list) -> mne.io.Raw:
        raws = [mne.io.read_raw(str(p), preload=True, verbose=False) for p in paths]
        return cast(mne.io.Raw, mne.concatenate_raws(raws))

    def run_and_save(self):
        """Run all pipeline actions, export the final output, then mark the session preprocessed."""
        self.generate_qc_after_pipeline = True
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

        last_raw: mne.io.BaseRaw | None = None
        if file_type in ("epochs", "evoked"):
            for state in reversed(self.state.data_states):
                if isinstance(state, mne.io.BaseRaw):
                    last_raw = state
                    break

        if data_to_save is None:
            ctx.on_status_update("error")
            return

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
            QMessageBox.warning(self.window(), "Export Failed", "Pipeline output contains no data (0 samples).\nCheck your pipeline for actions that may have produced empty output.")
            ctx.on_status_update("error")
            return

        out_path = ctx.project.session_output_file(
            ctx.project_dir, ctx.participant, ctx.session, file_type, ctx.run_index
        )

        try:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            _data_to_save = data_to_save
            self.runner.run_in_thread(
                lambda: _data_to_save.save(str(out_path), overwrite=True),
                f"Saving {file_type}...",
            )
            logger.info(
                "Saved %s: %s bytes",
                out_path.name,
                out_path.stat().st_size if out_path.exists() else "file missing!",
            )
        except Exception as e:
            logger.exception("Auto-export failed: %s", out_path)
            QMessageBox.warning(self.window(), "Export Failed", f"Could not save output:\n{e}")
            ctx.on_status_update("error")
            return

        if last_raw is not None:
            raw_path = ctx.project.session_output_file(
                ctx.project_dir, ctx.participant, ctx.session, "preprocessed", ctx.run_index
            )
            try:
                _last_raw = last_raw
                if not _last_raw.preload:
                    _last_raw.load_data()
                raw_path.parent.mkdir(parents=True, exist_ok=True)
                self.runner.run_in_thread(
                    lambda: _last_raw.save(str(raw_path), overwrite=True),
                    "Saving preprocessed raw...",
                )
                logger.info("Saved raw intermediate: %s", raw_path.name)
            except Exception as e:
                logger.warning("Could not save raw intermediate: %s", e)

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
        if not self.generate_qc_after_pipeline:
            return
        self.generate_qc_after_pipeline = False
        if self.state.settings.value("qc/auto_generate", True, type=bool):
            self.generate_qc_report()

    def get_qc_report_path(self):
        """Determine where to save the QC report."""
        if self.project_context:
            ctx = self.project_context
            return ctx.project.qc_report_path(
                ctx.project_dir, ctx.participant, ctx.session, ctx.run_index
            )
        if self.state.data_filepath:
            return self.state.data_filepath.parent / f"qc_report_{self.state.data_filepath.stem}.html"
        return Path(tempfile.gettempdir()) / "mnetape_qc_report.html"

    def generate_qc_report(self):
        """Generate the QC report in a background thread and show the Open button."""
        from mnetape.core.qc_report import generate_report
        from mnetape.gui.controllers.pipeline_runner import OperationCancelled

        out_path = self.get_qc_report_path()
        title = "EEG QC Report"
        if self.state.data_filepath:
            title = f"QC - {self.state.data_filepath.name}"

        settings = self.state.settings
        include_events_viewer = settings.value("qc/events_viewer_enabled", True, type=bool)

        try:
            self.runner.run_in_thread(
                lambda: generate_report(
                    self.state, out_path, title=title,
                    include_events_viewer=include_events_viewer,
                ),
                "Generating QC report...",
            )
        except OperationCancelled:
            return
        except Exception as e:
            logger.warning("QC report generation failed: %s", e)
            self.emit_status("QC report failed.")
            return

        self.btn_qc_report.setVisible(True)
        self.emit_status(f"QC report saved → {out_path.name}")

    def open_qc_report(self):
        """Open the QC report for the current run in the system browser."""
        path = self.get_qc_report_path()
        if path.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def cleanup(self):
        """Release resources. Called by MainWindow when the embedded session ends."""
        self.state.data_states.close()

    def event(self, event: QEvent | None) -> bool:
        if event is not None and event.type() == QEvent.Type.WindowActivate:
            modal = QApplication.activeModalWidget()
            if modal:
                modal.raise_()
                modal.activateWindow()
        return super().event(event)
