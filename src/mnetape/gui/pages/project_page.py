"""Project page widget: participant roster and project management.

QWidget that shows the list of participants and their sessions in a tree on the left and provides
a stacked detail panel on the right.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from typing import TYPE_CHECKING

from PyQt6.QtCore import QEvent, QObject, QSettings, Qt, QUrl, pyqtSignal

if TYPE_CHECKING:
    from PyQt6.QtCore import QPoint
from PyQt6.QtGui import (QBrush, QColor, QDesktopServices, QMouseEvent)
from PyQt6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from mnetape.core.codegen import extract_custom_preamble, parse_script_to_actions, pipeline_canonical_code
from mnetape.gui.controllers.project_participant_controller import ProjectParticipantController
from mnetape.gui.controllers.project_pipeline_controller import ProjectPipelineController
from mnetape.core.project import (
    Participant,
    ParticipantStatus,
    Project,
    ProjectContext,
    Session,
    STATUS_COLORS,
    STATUS_DESCRIPTIONS,
    STATUS_ICONS,
    STATUS_LABELS,
    aggregate_participant_status,
)

logger = logging.getLogger(__name__)

SETTINGS_LAST_PROJECT = "project/last_dir"

# UserRole data keys stored in tree items
ROLE_TYPE = Qt.ItemDataRole.UserRole          # "participant" | "session"
ROLE_PID = Qt.ItemDataRole.UserRole + 1       # participant id str
ROLE_SID = Qt.ItemDataRole.UserRole + 2       # session id str

CUSTOM_PIPELINE_ICON = "✎"
CUSTOM_PIPELINE_TOOLTIP = "Custom pipeline (participant-specific override)"

# Right panel page indices
PAGE_WELCOME = 0
PAGE_NO_SELECTION = 1
PAGE_PARTICIPANT_DETAIL = 2
PAGE_SESSION_DETAIL = 3


def make_no_selection_widget() -> QWidget:
    w = QWidget()
    layout = QVBoxLayout(w)
    layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
    lbl = QLabel("Select a participant or session from the list.")
    lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
    lbl.setStyleSheet("color: gray; font-size: 14px;")
    layout.addWidget(lbl)
    return w


def add_recent_project(path: str):
    settings = QSettings()
    recent = settings.value("project/recent", [], list) or []
    if path in recent:
        recent.remove(path)
    recent.insert(0, path)
    settings.setValue("project/recent", recent[:10])


def compute_session_pipeline_hash(project: Project, project_dir: Path, participant: Participant, session: Session) -> str:
    """Return an MD5 hex digest of the normalized generated script for the pipeline in effect.

    Normalizes via parse_script_to_actions -> generate_full_script so the hash matches the one stored at run time.
    Returns empty string when no pipeline file is recorded or the file is missing.
    """
    if session.has_custom_pipeline:
        path = project.participant_pipeline_path(project_dir, participant, session)
    elif project.has_default_pipeline:
        path = project.pipeline_path(project_dir)
    else:
        return ""
    try:
        code = path.read_text(encoding="utf-8")
        actions = parse_script_to_actions(code)
        normalized = pipeline_canonical_code(actions, extra_preamble=extract_custom_preamble(code, actions) or None)
        return hashlib.md5(normalized.encode()).hexdigest()
    except Exception:
        return ""


def make_participant_item(p: Participant, status: ParticipantStatus | None = None, expanded: bool = True) -> QTreeWidgetItem:
    arrow = "▾" if expanded else "▸"
    if status is None:
        status = p.participant_status
    icon = STATUS_ICONS.get(status, "◌")
    text = f"{arrow}  {icon}  {p.id}"
    color = QColor(STATUS_COLORS.get(status, "#888888"))

    item = QTreeWidgetItem([text])
    font = item.font(0)
    font.setBold(True)
    item.setFont(0, font)
    item.setForeground(0, QBrush(color))
    item.setData(0, ROLE_TYPE, "participant")
    item.setData(0, ROLE_PID, p.id)
    return item



def session_pipeline_state(s: Session) -> str:
    """Return 'custom' if the session has a participant-specific pipeline, else 'none'."""
    return "custom" if s.has_custom_pipeline else "none"


def make_session_item(p: Participant, s: Session, pipeline_state: str = "none", current_pipeline_hash: str = "") -> QTreeWidgetItem:
    status = s.effective_status(current_pipeline_hash)
    icon = STATUS_ICONS.get(status, "◌")
    label = STATUS_LABELS.get(status, str(status))
    is_custom = pipeline_state == "custom"
    text = f"   ses-{s.id}  {icon} {label}  {CUSTOM_PIPELINE_ICON}" if is_custom else f"   ses-{s.id}  {icon} {label}"
    color = QColor(STATUS_COLORS.get(status, "#888888"))

    item = QTreeWidgetItem([text])
    item.setForeground(0, QBrush(color))
    if is_custom:
        item.setToolTip(0, CUSTOM_PIPELINE_TOOLTIP)
    item.setData(0, ROLE_TYPE, "session")
    item.setData(0, ROLE_PID, p.id)
    item.setData(0, ROLE_SID, s.id)
    return item



def open_folder(folder: Path):
    folder.mkdir(parents=True, exist_ok=True)
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))


def on_participant_expanded(item: QTreeWidgetItem):
    if item.data(0, ROLE_TYPE) == "participant":
        item.setText(0, item.text(0).replace("▸", "▾", 1))


def on_participant_collapsed(item: QTreeWidgetItem):
    if item.data(0, ROLE_TYPE) == "participant":
        item.setText(0, item.text(0).replace("▾", "▸", 1))


class RunFileButton(QPushButton):
    """Checkable push button that also emits doubleClicked."""

    doubleClicked = pyqtSignal()

    def mouseDoubleClickEvent(self, event: QMouseEvent | None) -> None:
        self.doubleClicked.emit()
        super().mouseDoubleClickEvent(event)


class ProjectPage(QWidget):
    """Top-level page for project-based EEG study management.

    Shows a participant/session tree on the left and a stacked detail panel on the right.

    Signals:
        open_preprocessing_requested(object, list): (ProjectContext, nav_list) open a prep session.
        status_message(str, int): Emit a status bar message.
        title_change(str): Request a window title change.
        preprocessing_closed(object, object): (ParticipantStatus, ProjectContext) prep session closed.
    """

    open_preprocessing_requested = pyqtSignal(object, list)
    close_project_requested = pyqtSignal()
    status_message = pyqtSignal(str, int)
    title_change = pyqtSignal(str)
    preprocessing_closed = pyqtSignal(object, object)

    def __init__(self, settings: QSettings, parent=None):
        super().__init__(parent)

        self.settings = settings

        # State
        self.project: Project | None = None
        self.project_dir: Path | None = None

        # Active prep page reference (set by MainWindow)
        self.active_prep_page = None

        # Menu action refs (injected by MainWindow)
        self.open_folder_action = None
        self.add_p_action = None
        self.import_folder_action = None
        self.import_bids_action = None
        self.rename_project_action = None
        self.close_project_action = None

        # Widget attributes
        self.participant_tree = QTreeWidget()
        self.btn_add = QPushButton()
        self.btn_remove = QPushButton()
        self.left_panel = QWidget()
        self.left_sep = QFrame()
        self.right_stack = QStackedWidget()
        self.welcome_widget = QWidget()
        self.no_selection_widget = QWidget()
        self.participant_detail_widget = QWidget()
        self.participant_detail_refs = {}
        self.session_detail_widget = QWidget()
        self.session_detail_refs = {}
        self.pipeline_status_label = QLabel()

        self.pipeline_ctrl = ProjectPipelineController(self)
        self.participant_ctrl = ProjectParticipantController(self)

        self.setup_ui()

    # --- Active prep page management ---

    def set_active_prep_page(self, page):
        """Called by MainWindow when a prep page is opened."""
        self.active_prep_page = page

    def clear_active_prep_page(self):
        """Called by MainWindow when a prep page is closed."""
        self.active_prep_page = None

    # UI

    def setup_ui(self):
        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # ---- Left: participant/session tree ----
        left_panel = QWidget()
        left_panel.setObjectName("project_left_panel")
        left_panel.setMinimumWidth(220)
        left_panel.setMaximumWidth(300)
        left_panel.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 8, 0, 8)
        left_layout.setSpacing(4)

        title = QLabel("Participants")
        title.setObjectName("sidebar_title")
        title.setContentsMargins(12, 0, 0, 4)
        left_layout.addWidget(title)

        self.pipeline_status_label = QPushButton()
        self.pipeline_status_label.setFlat(True)
        self.pipeline_status_label.setStyleSheet(
            "QPushButton { font-size: 11px; color: #888; text-align: left;"
            " padding: 0 0 0 13px; border: none; }"
            "QPushButton:hover:enabled { text-decoration: underline; }"
        )
        self.pipeline_status_label.setCursor(Qt.CursorShape.PointingHandCursor)
        self.pipeline_status_label.setVisible(False)
        self.pipeline_status_label.clicked.connect(self.pipeline_ctrl.open_default_pipeline)
        left_layout.addWidget(self.pipeline_status_label)

        self.participant_tree = QTreeWidget()
        self.participant_tree.setObjectName("participant_tree")
        self.participant_tree.setHeaderHidden(True)
        self.participant_tree.setColumnCount(1)
        self.participant_tree.setStyleSheet("QTreeWidget::item { height: 28px; }")
        self.participant_tree.currentItemChanged.connect(self.on_item_selected)
        if vp := self.participant_tree.viewport():
            vp.installEventFilter(self)
        self.participant_tree.itemExpanded.connect(on_participant_expanded)
        self.participant_tree.itemCollapsed.connect(on_participant_collapsed)
        self.participant_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.participant_tree.customContextMenuRequested.connect(self.show_tree_context_menu)
        left_layout.addWidget(self.participant_tree)

        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(8, 4, 8, 0)
        self.btn_add = QPushButton("+ Add Participant")
        self.btn_add.setObjectName("btn_add_action")
        self.btn_add.clicked.connect(self.participant_ctrl.add_participant)
        self.btn_add.setEnabled(False)
        self.btn_remove = QPushButton("Remove Participant")
        self.btn_remove.setEnabled(False)
        self.btn_remove.clicked.connect(self.participant_ctrl.remove_selected)
        btn_row.addWidget(self.btn_add, 1)
        btn_row.addWidget(self.btn_remove, 1)
        left_layout.addLayout(btn_row)

        self.left_panel = left_panel
        left_panel.setVisible(False)
        main_layout.addWidget(left_panel)

        # Vertical separator
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setStyleSheet("color: #D5D5D8;")
        self.left_sep = sep
        sep.setVisible(False)
        main_layout.addWidget(sep)

        # ---- Right: stacked panel ----
        self.right_stack = QStackedWidget()
        self.right_stack.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        # Page 0: welcome
        self.welcome_widget = self.make_welcome_widget()
        self.right_stack.addWidget(self.welcome_widget)

        # Page 1: no selection
        self.no_selection_widget = make_no_selection_widget()
        self.right_stack.addWidget(self.no_selection_widget)

        # Page 2: participant detail
        self.participant_detail_widget, self.participant_detail_refs = self.make_participant_detail_widget()
        self.right_stack.addWidget(self.participant_detail_widget)

        # Page 3: session detail
        self.session_detail_widget, self.session_detail_refs = self.make_session_detail_widget()
        self.right_stack.addWidget(self.session_detail_widget)

        self.right_stack.setCurrentWidget(self.welcome_widget)
        main_layout.addWidget(self.right_stack, 1)

    #  welcome / no-selection

    def make_welcome_widget(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        title = QLabel("<h2>Welcome to MNETAPE</h2>")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        subtitle = QLabel("Create a new project or open an existing one to get started.")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle.setStyleSheet("color: gray;")
        layout.addWidget(subtitle)

        layout.addSpacing(24)

        btn_layout = QHBoxLayout()
        btn_new = QPushButton("New Project...")
        btn_new.setFixedWidth(180)
        btn_new.clicked.connect(self.new_project)
        btn_open = QPushButton("Open MNETAPE Project...")
        btn_open.setFixedWidth(180)
        btn_open.clicked.connect(self.open_project)
        btn_layout.addStretch()
        btn_layout.addWidget(btn_new)
        btn_layout.addSpacing(8)
        btn_layout.addWidget(btn_open)
        btn_layout.addStretch()
        layout.addLayout(btn_layout)

        layout.addSpacing(8)
        btn_standalone = QPushButton("Open Single EEG File...")
        btn_standalone.setFixedWidth(200)
        btn_standalone.clicked.connect(self.open_standalone)
        btn_row2 = QHBoxLayout()
        btn_row2.addStretch()
        btn_row2.addWidget(btn_standalone)
        btn_row2.addStretch()
        layout.addLayout(btn_row2)

        return w

    def open_standalone(self):
        """Open standalone preprocessing after requiring an EEG file selection."""
        from mnetape.core.data_io import open_file_dialog_filter
        from mnetape.gui.pages.preprocessing_page import PreprocessingPage
        from PyQt6.QtWidgets import QMainWindow

        selected_path = ""
        while not selected_path:
            selected_path, _ = QFileDialog.getOpenFileName(
                self.window(),
                "Select EEG File",
                "",
                open_file_dialog_filter(),
            )
            if selected_path:
                break
            retry = QMessageBox.question(
                self.window(),
                "EEG File Required",
                "You must select an EEG file before opening preprocessing.\n"
                "Do you want to select a file now?",
                QMessageBox.StandardButton.Retry | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Retry,
            )
            if retry == QMessageBox.StandardButton.Cancel:
                return

        w = QMainWindow()
        w.setWindowTitle("MNETAPE")
        w.resize(1400, 900)
        page = PreprocessingPage(ctx=None, settings=self.settings, nav_list=[], parent=w)
        w.setCentralWidget(page)
        raw_info = QLabel()
        raw_info.setStyleSheet("color: gray;")
        status_bar = w.statusBar()
        if status_bar:
            status_bar.addPermanentWidget(raw_info)

        def _on_status(msg: str, t: int) -> None:
            if status_bar:
                status_bar.showMessage(msg, t)

        page.status_message.connect(_on_status)
        page.raw_info_changed.connect(raw_info.setText)
        page.close_requested.connect(w.close)
        # Keep a reference so the window isn't garbage collected
        self._standalone_window = w
        w.show()
        page.files.load_data_path(selected_path)

    @staticmethod
    def make_detail_scroll(inner_name: str) -> tuple[QScrollArea, QVBoxLayout, QLabel]:
        """Create a titled scroll area used by both detail panels.

        Returns (scroll, layout, id_label) where layout is the inner VBoxLayout
        and id_label is the pre-added bold title label.
        """
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setObjectName("participant_detail_scroll")

        inner = QWidget()
        inner.setObjectName(inner_name)
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(28, 20, 28, 20)
        layout.setSpacing(12)

        id_label = QLabel()
        id_label.setObjectName("sidebar_title")
        layout.addWidget(id_label)

        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet("color: #D5D5D8;")
        layout.addWidget(line)

        scroll.setWidget(inner)
        return scroll, layout, id_label

    # Participant detail page

    def make_participant_detail_widget(self) -> tuple[QWidget, dict]:
        """Build the participant detail panel."""
        scroll, layout, id_label = self.make_detail_scroll("participant_detail_content")

        status_label = QLabel()
        layout.addWidget(status_label)

        layout.addSpacing(4)

        notes_header = QLabel("Notes:")
        layout.addWidget(notes_header)
        notes_edit = QPlainTextEdit()
        notes_edit.setMaximumHeight(90)
        notes_edit.textChanged.connect(self.participant_ctrl.on_notes_changed)
        layout.addWidget(notes_edit)

        layout.addSpacing(8)

        sessions_header = QLabel("<b>Sessions</b>")
        layout.addWidget(sessions_header)

        sessions_list_label = QLabel()
        sessions_list_label.setWordWrap(True)
        sessions_list_label.setStyleSheet("color: gray; font-size: 11px;")
        layout.addWidget(sessions_list_label)

        layout.addStretch()

        btn_row = QHBoxLayout()
        btn_add_session = QPushButton("+ Add Session")
        btn_add_session.clicked.connect(self.participant_ctrl.add_session_to_selected_participant)
        btn_row.addWidget(btn_add_session)
        btn_row.addStretch()
        btn_open_participant_folder = QPushButton("Open Participant Folder")
        btn_open_participant_folder.setToolTip("Open this participant's output folder in the file manager")
        btn_open_participant_folder.clicked.connect(self.open_participant_folder)
        btn_row.addWidget(btn_open_participant_folder)
        layout.addLayout(btn_row)

        refs = {
            "id_label": id_label,
            "status_label": status_label,
            "notes_edit": notes_edit,
            "sessions_list_label": sessions_list_label,
        }
        return scroll, refs

    # Session detail page

    def make_session_detail_widget(self) -> tuple[QWidget, dict]:
        """Build the session detail panel."""
        scroll, layout, id_label = self.make_detail_scroll("session_detail")

        form = QFormLayout()
        form.setSpacing(9)
        form.setContentsMargins(0, 0, 0, 0)

        status_label = QLabel()
        form.addRow("Status:", status_label)

        layout.addLayout(form)

        # Run files section
        runs_header = QHBoxLayout()
        runs_header.addWidget(QLabel("<b>Run Files</b>"))
        runs_header.addStretch()
        btn_add_run = QPushButton("+ Add Run")
        btn_add_run.setFixedWidth(100)
        btn_add_run.clicked.connect(self.participant_ctrl.add_session_run)
        runs_header.addWidget(btn_add_run)
        btn_remove_run = QPushButton("Remove Run")
        btn_remove_run.setFixedWidth(100)
        btn_remove_run.clicked.connect(self.participant_ctrl.remove_session_run)
        runs_header.addWidget(btn_remove_run)
        layout.addLayout(runs_header)

        # Run items buttons
        runs_container = QWidget()
        runs_container.setObjectName("runs_container")
        runs_layout = QVBoxLayout(runs_container)
        runs_layout.setContentsMargins(0, 0, 0, 0)
        runs_layout.setSpacing(2)
        layout.addWidget(runs_container)

        runs_button_group = QButtonGroup()
        runs_button_group.setExclusive(True)

        merge_runs_check = QCheckBox("Merge runs")
        merge_runs_check.setToolTip(
            "When checked, all run files are concatenated before preprocessing.\n"
            "When unchecked, runs are preprocessed individually."
        )
        merge_runs_check.toggled.connect(self.participant_ctrl.on_merge_runs_toggled)
        layout.addWidget(merge_runs_check)

        layout.addStretch()

        btn_row = QHBoxLayout()
        btn_open = QPushButton("Open Preprocessing")
        btn_open.setObjectName("btn_add_action")
        btn_open.clicked.connect(self.open_preprocessing)
        btn_row.addWidget(btn_open)
        btn_row.addStretch()
        btn_open_folder = QPushButton("Open Output Folder")
        btn_open_folder.setToolTip("Open the session output folder in the file manager")
        btn_open_folder.clicked.connect(self.open_output_folder)
        btn_row.addWidget(btn_open_folder)
        layout.addLayout(btn_row)

        refs = {
            "id_label": id_label,
            "runs_container": runs_container,
            "runs_layout": runs_layout,
            "runs_button_group": runs_button_group,
            "merge_runs_check": merge_runs_check,
            "status_label": status_label,
        }
        return scroll, refs

    # Project load/save

    def load_project(self, project_dir: Path):
        """Load a project from disk and update the UI."""
        try:
            project = Project.load(project_dir)
        except Exception as e:
            QMessageBox.critical(self.window(), "Error", f"Could not open project:\n{e}")
            logger.exception("Failed to load project from %s", project_dir)
            return

        self.project = project
        self.project_dir = project_dir
        self.title_change.emit(f"MNETAPE - {project.name}")

        if self.add_p_action:
            self.add_p_action.setEnabled(True)
        if self.import_folder_action:
            self.import_folder_action.setEnabled(True)
        if self.open_folder_action:
            self.open_folder_action.setEnabled(True)
        if self.rename_project_action:
            self.rename_project_action.setEnabled(True)
        if self.close_project_action:
            self.close_project_action.setEnabled(True)
        self.left_panel.setVisible(True)
        self.left_sep.setVisible(True)
        self.btn_add.setEnabled(True)

        self.rebuild_tree()
        self.right_stack.setCurrentWidget(self.no_selection_widget)

        QSettings().setValue(SETTINGS_LAST_PROJECT, str(project_dir))
        add_recent_project(str(project_dir))
        logger.debug("Opened: %s (%s)", project.name, project_dir)
        logger.info("Loaded project: %s from %s", project.name, project_dir)

    def save_project(self):
        if self.project and self.project_dir:
            try:
                self.project.save(self.project_dir)
            except Exception as e:
                logger.error("Failed to save project: %s", e)

    # Recent projects

    def populate_recent_menu(self, menu: QMenu):
        """Populate the given menu with recent project entries."""
        menu.clear()
        settings = QSettings()
        recent = settings.value("project/recent", [], list) or []
        recent = [r for r in recent if Path(r).is_dir()]
        if not recent:
            if a := menu.addAction("No recent projects"):
                a.setEnabled(False)
            return
        for path in recent:
            if act := menu.addAction(path):
                act.triggered.connect(lambda _, p=path: self.load_project(Path(p)))

    def update_pipeline_status_label(self):
        """Refresh the pipeline status label shown below the Participants title."""
        if not self.project or not self.project_dir:
            self.pipeline_status_label.setVisible(False)
            return
        if self.project.has_default_pipeline:
            self.pipeline_status_label.setText("≡ Default pipeline set")
            self.pipeline_status_label.setStyleSheet(
                "QPushButton { font-size: 11px; color: #2E7D32; text-align: left;"
                " padding: 0 0 0 13px; border: none; background-color: #FAFAFA; }"
                "QPushButton:hover:enabled { text-decoration: underline; }"
            )
            self.pipeline_status_label.setEnabled(True)
        else:
            self.pipeline_status_label.setText("No default pipeline")
            self.pipeline_status_label.setStyleSheet(
                "QPushButton { font-size: 11px; color: #888; text-align: left;"
                " padding: 0 0 0 13px; border: none; background-color: #FAFAFA; }"
            )
            self.pipeline_status_label.setEnabled(False)
        self.pipeline_status_label.setVisible(True)

    # Tree building

    def rebuild_tree(self):
        """Repopulate the participant/session tree from project.participants."""
        self.participant_tree.blockSignals(True)
        self.participant_tree.clear()
        if self.project and self.project_dir:
            for p in self.project.participants:
                session_items = []
                session_statuses = []
                for s in p.sessions:
                    h = compute_session_pipeline_hash(self.project, self.project_dir, p, s)
                    session_statuses.append(s.effective_status(h))
                    s_item = make_session_item(p, s, session_pipeline_state(s), h)
                    session_items.append(s_item)
                p_item = make_participant_item(p, aggregate_participant_status(session_statuses))
                self.participant_tree.addTopLevelItem(p_item)
                for s_item in session_items:
                    p_item.addChild(s_item)
                p_item.setExpanded(True)
        self.participant_tree.blockSignals(False)
        self.update_pipeline_status_label()

    def refresh_participant_item(self, participant_id: str):
        """Refresh the display text of a participant and all its session items."""
        if not self.project:
            return
        p = self.project.get_participant(participant_id)
        if not p:
            return
        for i in range(self.participant_tree.topLevelItemCount()):
            p_item = self.participant_tree.topLevelItem(i)
            if not p_item:
                continue
            if p_item.data(0, ROLE_PID) == participant_id:
                session_statuses = []
                for j in range(p_item.childCount()):
                    s_item = p_item.child(j)
                    if not s_item:
                        continue
                    sid = s_item.data(0, ROLE_SID)
                    s = p.get_session(sid)
                    if s:
                        h = compute_session_pipeline_hash(self.project, self.project_dir or Path(), p, s)
                        s_status = s.effective_status(h)
                        session_statuses.append(s_status)
                        new_s = make_session_item(p, s, session_pipeline_state(s), h)
                        s_item.setText(0, new_s.text(0))
                        s_item.setForeground(0, new_s.foreground(0))
                        s_item.setToolTip(0, new_s.toolTip(0))
                p_status = aggregate_participant_status(session_statuses) if session_statuses else p.participant_status
                new_p = make_participant_item(p, p_status, expanded=p_item.isExpanded())
                p_item.setText(0, new_p.text(0))
                p_item.setForeground(0, new_p.foreground(0))
                p_item.setFont(0, new_p.font(0))
                break

    def get_selected_item_data(self) -> tuple[str | None, str | None, str | None]:
        """Return (item_type, participant_id, session_id) for the currently selected item."""
        item = self.participant_tree.currentItem()
        if not item:
            return None, None, None
        item_type = item.data(0, ROLE_TYPE)
        pid = item.data(0, ROLE_PID)
        sid = item.data(0, ROLE_SID) if item_type == "session" else None
        return item_type, pid, sid

    def get_selected_participant(self) -> Participant | None:
        if not self.project:
            return None
        _, pid, _ = self.get_selected_item_data()
        if not pid:
            return None
        return self.project.get_participant(pid)

    def get_selected_session(self) -> tuple[Participant | None, Session | None]:
        """Return (participant, session) for the selected session item, or (None, None)."""
        if not self.project:
            return None, None
        item_type, pid, sid = self.get_selected_item_data()
        if item_type != "session" or not pid or not sid:
            return None, None
        p = self.project.get_participant(pid)
        if not p:
            return None, None
        return p, p.get_session(sid)

    # Selection handling

    def eventFilter(self, obj: QObject | None, event: QEvent | None) -> bool:
        vp = self.participant_tree.viewport()
        if (
            vp and obj is vp
            and isinstance(event, QMouseEvent)
            and event.type() == QEvent.Type.MouseButtonPress
        ):
            item = self.participant_tree.itemAt(event.pos())
            if item and item.data(0, ROLE_TYPE) == "participant":
                if event.pos().x() <= vp.width() * 0.2:
                    item.setExpanded(not item.isExpanded())
        return super().eventFilter(obj, event)

    def on_item_selected(self, current: QTreeWidgetItem | None, _previous: QTreeWidgetItem | None):
        if not self.project or current is None:
            self.right_stack.setCurrentWidget(self.no_selection_widget)
            self.btn_remove.setEnabled(False)
            return

        item_type = current.data(0, ROLE_TYPE)
        self.btn_remove.setEnabled(True)

        if item_type == "participant":
            pid = current.data(0, ROLE_PID)
            p = self.project.get_participant(pid)
            if p:
                self.populate_participant_detail(p)
                self.right_stack.setCurrentWidget(self.participant_detail_widget)
        elif item_type == "session":
            pid = current.data(0, ROLE_PID)
            sid = current.data(0, ROLE_SID)
            p = self.project.get_participant(pid)
            if p:
                s = p.get_session(sid)
                if s:
                    self.populate_session_detail(p, s)
                    self.right_stack.setCurrentWidget(self.session_detail_widget)

    # Detail population

    def populate_participant_detail(self, p: Participant):
        refs = self.participant_detail_refs
        refs["id_label"].setText(f"<b>{p.id}</b>")
        project_dir = self.project_dir or Path()
        session_statuses = [
            s.effective_status(compute_session_pipeline_hash(self.project, project_dir, p, s))
            for s in p.sessions
        ] if self.project else []
        status = aggregate_participant_status(session_statuses) if session_statuses else p.participant_status
        color = STATUS_COLORS.get(status, "#888888")
        label = STATUS_LABELS.get(status, str(status))
        refs["status_label"].setText(
            f"<span style='color:{color};'>{STATUS_ICONS.get(status, '')} {label}</span>"
        )

        refs["notes_edit"].blockSignals(True)
        refs["notes_edit"].setPlainText(p.notes)
        refs["notes_edit"].blockSignals(False)

        session_lines = []
        for s, s_status in (zip(p.sessions, session_statuses) if session_statuses else []):
            icon = STATUS_ICONS.get(s_status, "◌")
            n_runs = len(s.data_files)
            runs_str = f"  [{n_runs} run{'s' if n_runs != 1 else ''}]" if n_runs else ""
            session_lines.append(
                f"ses-{s.id}  {icon}  {STATUS_LABELS.get(s_status, str(s_status))}{runs_str}"
            )
        refs["sessions_list_label"].setText("\n".join(session_lines) if session_lines else "No sessions")

    def populate_session_detail(self, p: Participant, s: Session):
        refs = self.session_detail_refs
        refs["id_label"].setText(f"<b>{p.id}</b>  /  ses-{s.id}")

        runs_layout: QVBoxLayout = refs["runs_layout"]
        button_group: QButtonGroup = refs["runs_button_group"]

        for btn in list(button_group.buttons()):
            button_group.removeButton(btn)
        while runs_layout.count():
            item = runs_layout.takeAt(0)
            if item and (w := item.widget()):
                w.deleteLater()

        pipeline_hash = compute_session_pipeline_hash(self.project, self.project_dir or Path(), p, s) if self.project else ""

        if self.project and (project_dir := self.project_dir):
            resolved = self.project.resolve_data_files(project_dir, s)
            btn_style = """
                QPushButton {{
                    background: white;
                    border: none;
                    text-align: left;
                    padding: 5px 10px;
                    border-radius: 3px;
                    color: {text_color};
                }}
                QPushButton:checked {{
                    background: #EBF3FF;
                    border: 1px solid #4A90D9;
                }}
                QPushButton:hover:!checked {{
                    background: #F5F5F5;
                }}
            """
            def make_run_row(btn: QPushButton, report_path: Path) -> QWidget:
                row = QWidget()
                row_layout = QHBoxLayout(row)
                row_layout.setContentsMargins(0, 0, 0, 0)
                row_layout.setSpacing(4)
                row_layout.addWidget(btn, 1)
                qc_btn = QPushButton("Report")
                qc_btn.setFixedWidth(50)
                qc_btn.setToolTip(f"Open QC Report: {report_path.name}")
                qc_btn.setVisible(report_path.exists())
                qc_btn.setStyleSheet(
                    "QPushButton { background: #2E7D32; color: white; border: 1px solid #2E7D32;"
                    " border-radius: 3px; font-size: 11px; padding: 0; }"
                    "QPushButton:hover { background: #256A2A; color: white; }"
                )
                qc_btn.clicked.connect(
                    lambda _checked, open_path=report_path: QDesktopServices.openUrl(
                        QUrl.fromLocalFile(str(open_path))
                    )
                )
                row_layout.addWidget(qc_btn)
                return row

            if s.merge_runs:
                n = len(s.data_files)
                run_st = s.run_status(0, pipeline_hash) if any(s.processed_files) else ParticipantStatus.PENDING
                run_icon = STATUS_ICONS.get(run_st, "○")
                run_label = f"run{'s' if n != 1 else ''}"
                run_btn = RunFileButton(f"{run_icon}  Merged  ({n} {run_label})")
                run_btn.setCheckable(True)
                run_btn.setChecked(True)
                any_missing = any(not rpath.exists() for rpath in resolved)
                if any_missing:
                    text_color = "#C62828"
                else:
                    text_color = STATUS_COLORS.get(run_st, "inherit")
                run_btn.setStyleSheet(btn_style.format(text_color=text_color))
                run_btn.doubleClicked.connect(self.open_preprocessing)
                button_group.addButton(run_btn, 0)
                qc_path = self.project.qc_report_path(project_dir, p, s, None)
                runs_layout.addWidget(make_run_row(run_btn, qc_path))
            else:
                for i, (raw_str, resolved_path) in enumerate(zip(s.data_files, resolved)):
                    filename = Path(raw_str).name
                    run_st = s.run_status(i, pipeline_hash)
                    run_icon = STATUS_ICONS.get(run_st, "○")
                    run_btn = RunFileButton(f"{run_icon}  {filename}")
                    run_btn.setCheckable(True)
                    if not resolved_path.exists():
                        text_color = "#C62828"
                    else:
                        text_color = STATUS_COLORS.get(run_st, "inherit")
                    run_btn.setStyleSheet(btn_style.format(text_color=text_color))
                    run_btn.doubleClicked.connect(self.open_preprocessing)
                    button_group.addButton(run_btn, i)
                    qc_path = self.project.qc_report_path(project_dir, p, s, i)
                    runs_layout.addWidget(make_run_row(run_btn, qc_path))

        status = s.effective_status(pipeline_hash)
        color = STATUS_COLORS.get(status, "#888888")
        label = STATUS_LABELS.get(status, str(status))
        msg = f"<span style='color:{color};'>{STATUS_ICONS.get(status, '')} {label}</span>"
        if s.error_msg:
            msg += f"<br><small style='color:#C62828;'>{s.error_msg}</small>"
        refs["status_label"].setText(msg)
        refs["status_label"].setToolTip(STATUS_DESCRIPTIONS.get(status, ""))

        refs["merge_runs_check"].blockSignals(True)
        refs["merge_runs_check"].setChecked(s.merge_runs)
        refs["merge_runs_check"].blockSignals(False)
        refs["merge_runs_check"].setVisible(len(s.data_files) > 1)

    # Project actions

    def _create_and_load_project(self) -> Path | None:
        """Open the new-project dialog, save project.json, and load the project. Returns project_dir or None."""
        from mnetape.gui.dialogs.new_project_dialog import NewProjectDialog
        dlg = NewProjectDialog(self.window())
        if dlg.exec() != NewProjectDialog.DialogCode.Accepted:
            return None
        project_dir = dlg.get_project_dir()
        if not project_dir:
            return None
        Project(name=dlg.get_name()).save(project_dir)
        self.load_project(project_dir)
        return project_dir

    def new_project(self):
        self._create_and_load_project()

    def open_project(self):
        project_dir = QFileDialog.getExistingDirectory(self.window(), "Open Project Folder")
        if not project_dir:
            return
        path = Path(project_dir)
        if not (path / "project.json").exists():
            QMessageBox.warning(
                self.window(), "Not a project",
                "The selected folder does not contain a project.json file.\n\n"
                "Choose the root folder of a MNETAPE project."
            )
            return
        self.load_project(path)

    def rename_project(self):
        """Rename the current project."""
        if not self.project:
            return
        new_name, ok = QInputDialog.getText(
            self.window(), "Rename Project", "Project name:", text=self.project.name
        )
        new_name = new_name.strip()
        if not ok or not new_name or new_name == self.project.name:
            return
        self.project.name = new_name
        self.save_project()
        self.title_change.emit(f"MNETAPE - {new_name}")

    def show_tree_context_menu(self, pos: QPoint):
        item = self.participant_tree.itemAt(pos)
        if not item:
            return
        item_type = item.data(0, ROLE_TYPE)
        menu = QMenu(self)

        if item_type == "participant":
            pid = item.data(0, ROLE_PID)
            if a := menu.addAction("Add Session..."):
                a.triggered.connect(self.participant_ctrl.add_session_to_selected_participant)
            if a := menu.addAction("Rename..."):
                a.triggered.connect(lambda: self.participant_ctrl.rename_participant(pid))
            if a := menu.addAction("Open Participant Folder"):
                a.triggered.connect(self.open_participant_folder)
            menu.addSeparator()
            if a := menu.addAction("Remove Participant"):
                a.triggered.connect(self.participant_ctrl.remove_participant)
        elif item_type == "session":
            if a := menu.addAction("Open Session Folder"):
                a.triggered.connect(self.open_session_folder)
            if a := menu.addAction("Open Data Folder"):
                a.triggered.connect(self.open_participant_data_folder)
            menu.addSeparator()
            pid = item.data(0, ROLE_PID)
            sid = item.data(0, ROLE_SID)
            if a := menu.addAction("Rename..."):
                a.triggered.connect(lambda: self.participant_ctrl.rename_session_id(pid, sid))
            if a := menu.addAction("Remove Session"):
                a.triggered.connect(lambda: self.participant_ctrl.remove_session(pid, sid))

        menu.exec(self.participant_tree.mapToGlobal(pos))

    def open_preferences(self):
        from mnetape.gui.dialogs.preferences_dialog import PreferencesDialog
        PreferencesDialog(settings=self.settings, parent=self.window()).exec()

    def open_project_folder(self):
        if self.project_dir:
            open_folder(self.project_dir)

    def open_session_folder(self):
        p, s = self.get_selected_session()
        if not p or not s or not self.project or not self.project_dir:
            return
        open_folder(self.project.session_dir(self.project_dir, p, s))

    def open_participant_data_folder(self):
        """Open the source data folder for the selected session in the system file manager."""
        p, s = self.get_selected_session()
        if not p or not s or not self.project or not self.project_dir:
            return
        project_dir = self.project_dir
        folder: Path | None = None
        if s.data_files:
            resolved = self.project.resolve_data_files(project_dir, s)
            if resolved:
                folder = resolved[0].parent
        target = folder if (folder is not None and folder.exists()) else self.project.session_dir(project_dir, p, s)
        open_folder(target)

    def open_participant_folder(self):
        """Open the participant output folder in the system file manager."""
        p = self.get_selected_participant()
        if not p or not self.project or not self.project_dir:
            return
        folder = self.project.participant_dir(self.project_dir, p)
        folder.mkdir(parents=True, exist_ok=True)
        open_folder(folder)

    def open_output_folder(self):
        """Open the session output folder in the system file manager."""
        p, s = self.get_selected_session()
        if not p or not s or not self.project or not self.project_dir:
            return
        folder = self.project.session_dir(self.project_dir, p, s) / "outputs"
        folder.mkdir(parents=True, exist_ok=True)
        open_folder(folder)

    # Close project

    def close_project(self):
        """Ask the user to confirm, then emit close_project_requested for MainWindow to handle."""
        if not self.project:
            return
        if self.active_prep_page and not self.active_prep_page.confirm_discard_if_dirty():
            return
        self.close_project_requested.emit()

    def do_close_project(self):
        """Reset project state and return to the welcome screen. Called by MainWindow."""
        self.project = None
        self.project_dir = None
        self.participant_tree.clear()
        self.pipeline_status_label.setVisible(False)
        self.left_panel.setVisible(False)
        self.left_sep.setVisible(False)
        self.right_stack.setCurrentWidget(self.welcome_widget)
        self.btn_add.setEnabled(False)
        self.btn_remove.setEnabled(False)
        for action in (
            self.add_p_action, self.import_folder_action, self.open_folder_action,
            self.rename_project_action, self.close_project_action,
        ):
            if action:
                action.setEnabled(False)
        self.title_change.emit("MNETAPE")
        QSettings().setValue(SETTINGS_LAST_PROJECT, "")

    # Preprocessing navigation

    def build_nav_list(self) -> list:
        """Return the flat ordered list of (Participant, Session, run_index|None)."""
        if not self.project or not self.project_dir:
            return []
        items = []
        for p in self.project.participants:
            for s in p.sessions:
                resolved = self.project.resolve_data_files(self.project_dir, s)
                if s.merge_runs or not resolved:
                    items.append((p, s, None))
                else:
                    for i in range(len(resolved)):
                        items.append((p, s, i))
        return items

    def navigate_preprocessing(self, delta: int):
        """Open the previous (delta=-1) or next (delta=+1) run/session."""
        if not self.active_prep_page or not self.active_prep_page.project_context:
            return
        ctx = self.active_prep_page.project_context
        nav = self.build_nav_list()
        pos = next(
            (i for i, (p, s, r) in enumerate(nav)
             if p.id == ctx.participant.id and s.id == ctx.session.id and r == ctx.run_index),
            None,
        )
        if pos is None:
            return
        new_pos = pos + delta
        if new_pos < 0 or new_pos >= len(nav):
            return
        new_p, new_s, new_run_index = nav[new_pos]
        self.open_preprocessing_for(new_p, new_s, new_run_index)

    def _emit_open_preprocessing(
        self, project: Project, project_dir: Path, p: Participant, s: Session,
        data_files: list, run_index
    ):
        """Build ProjectContext and emit open_preprocessing_requested."""
        ctx = ProjectContext(
            project=project,
            project_dir=project_dir,
            participant=p,
            session=s,
            on_status_update=lambda status, pid=p.id, sid=s.id: self.on_ctx_status_update(pid, sid, status),
            data_files=data_files,
            run_index=run_index,
        )
        self.open_preprocessing_requested.emit(ctx, self.build_nav_list())

    def open_preprocessing_for(self, p: Participant, s: Session, run_index):
        """Emit open_preprocessing_requested for an explicit participant/session/run."""
        project = self.project
        project_dir = self.project_dir
        if not project or not project_dir:
            return
        resolved = project.resolve_data_files(project_dir, s)
        if run_index is not None and not s.merge_runs:
            data_files = [resolved[run_index]] if run_index < len(resolved) else []
        else:
            data_files = resolved
        self._emit_open_preprocessing(project, project_dir, p, s, data_files, run_index)

    def on_ctx_status_update(self, participant_id: str, session_id: str, new_status):
        """Internal handler: update project state when a preprocessing session reports status."""
        if not self.project:
            return
        p = self.project.get_participant(participant_id)
        if not p:
            return
        s = p.get_session(session_id)
        if not s:
            return

        if new_status in (ParticipantStatus.ERROR, ParticipantStatus.RUNNING):
            s.status = new_status
        else:
            s.status = ParticipantStatus.PENDING

        self.save_project()
        self.refresh_participant_item(participant_id)
        item_type, cur_pid, cur_sid = self.get_selected_item_data()
        if item_type == "session" and cur_pid == participant_id and cur_sid == session_id:
            self.populate_session_detail(p, s)
        elif item_type == "participant" and cur_pid == participant_id:
            self.populate_participant_detail(p)

        # Update prep header status label if this session is currently open
        if self.active_prep_page and self.active_prep_page.project_context:
            ctx = self.active_prep_page.project_context
            if ctx.participant.id == participant_id and ctx.session.id == session_id:
                self.active_prep_page.update_status_label(s, ctx.run_index)

        logger.info("Participant %s / ses-%s status -> %s", participant_id, session_id, new_status)

    def on_preprocessing_closed(self, _final_status, ctx: ProjectContext):
        """Called by MainWindow after tearing down the preprocessing page."""
        if ctx is None:
            return
        p = self.project.get_participant(ctx.participant.id) if self.project else None
        if not p:
            return
        s = p.get_session(ctx.session.id)
        if not s:
            return

        # Refresh tree and detail
        self.refresh_participant_item(ctx.participant.id)
        item_type, cur_pid, cur_sid = self.get_selected_item_data()
        if item_type == "session" and cur_pid == ctx.participant.id and cur_sid == ctx.session.id:
            self.populate_session_detail(p, s)
            self.right_stack.setCurrentWidget(self.session_detail_widget)
        elif item_type == "participant" and cur_pid == ctx.participant.id:
            self.populate_participant_detail(p)
            self.right_stack.setCurrentWidget(self.participant_detail_widget)
        else:
            self.right_stack.setCurrentWidget(self.no_selection_widget)

        self.save_project()

    # Embedded preprocessing (from session detail page)

    def open_preprocessing(self):
        """Request the MainWindow to open preprocessing for the selected session."""
        project = self.project
        project_dir = self.project_dir
        if not project or not project_dir:
            return
        p, s = self.get_selected_session()
        if not p or not s:
            p2 = self.get_selected_participant()
            if p2 and p2.sessions:
                p = p2
                s = p2.sessions[0]
            else:
                QMessageBox.information(
                    self.window(), "No Session Selected",
                    "Please select a session from the tree to open preprocessing."
                )
                return

        resolved = project.resolve_data_files(project_dir, s)
        if s.merge_runs:
            data_files = resolved
            run_index = None
        else:
            bg: QButtonGroup = self.session_detail_refs["runs_button_group"]
            selected_idx = bg.checkedId()
            if selected_idx < 0 or selected_idx >= len(resolved):
                selected_idx = 0
            data_files = [resolved[selected_idx]] if resolved else []
            run_index = selected_idx if resolved else None

        self._emit_open_preprocessing(project, project_dir, p, s, data_files, run_index)

