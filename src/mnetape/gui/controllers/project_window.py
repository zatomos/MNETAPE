"""Project window: participant roster and project management.

ProjectWindow is the application entry point. It shows the list of participants and their sessions in a tree on the left
and provides a stacked detail panel on the right.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PyQt6.QtCore import QEvent, Qt, QSettings, QUrl
from PyQt6.QtGui import QAction, QBrush, QColor, QDesktopServices, QKeySequence
from PyQt6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
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

import dataclasses

from mnetape.actions.registry import get_action_by_id
from mnetape.core.codegen import generate_full_script
from mnetape.core.project import (
    Participant,
    ParticipantStatus,
    Project,
    ProjectContext,
    Session,
    STATUS_COLORS,
    STATUS_ICONS,
    STATUS_LABELS,
)

logger = logging.getLogger(__name__)

SETTINGS_LAST_PROJECT = "project/last_dir"

# UserRole data keys stored in tree items
ROLE_TYPE = Qt.ItemDataRole.UserRole          # "participant" | "session"
ROLE_PID = Qt.ItemDataRole.UserRole + 1       # participant id str
ROLE_SID = Qt.ItemDataRole.UserRole + 2       # session id str

# Right panel page indices
PAGE_WELCOME = 0
PAGE_NO_SELECTION = 1
PAGE_PARTICIPANT_DETAIL = 2
PAGE_SESSION_DETAIL = 3
PAGE_PREPROCESSING = 4
PAGE_ANALYSIS = 5


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


def make_participant_item(p: Participant) -> QTreeWidgetItem:
    if p.excluded:
        text = f"─  {p.id}"
        color = QColor("#888888")
    else:
        status = p.participant_status
        icon = STATUS_ICONS.get(status, "◌")
        text = f"{icon}  {p.id}"
        color = QColor(STATUS_COLORS.get(status, "#888888"))

    item = QTreeWidgetItem([text])
    font = item.font(0)
    font.setBold(True)
    item.setFont(0, font)
    item.setForeground(0, QBrush(color))
    item.setData(0, ROLE_TYPE, "participant")
    item.setData(0, ROLE_PID, p.id)
    return item


def make_session_item(p: Participant, s: Session) -> QTreeWidgetItem:
    status = s.session_status
    icon = STATUS_ICONS.get(status, "◌")
    label = STATUS_LABELS.get(status, s.status)
    text = f"   ses-{s.id}  {icon} {label}"
    color = QColor(STATUS_COLORS.get(status, "#888888"))

    item = QTreeWidgetItem([text])
    item.setForeground(0, QBrush(color))
    item.setData(0, ROLE_TYPE, "session")
    item.setData(0, ROLE_PID, p.id)
    item.setData(0, ROLE_SID, s.id)
    return item


def open_standalone():
    from mnetape.gui.controllers.main_window import MainWindow
    w = MainWindow()
    w.show()


def strip_managed_params(actions) -> str:
    """Generate pipeline code with run-specific params reset to their schema defaults.

    Used when saving a default pipeline template so that per-run values (e.g. ICA exclusions)
    don't bleed across participants.
    """
    clean = []
    for action in actions:
        action_def = get_action_by_id(action.action_id)
        ir = action_def.interactive_runner if action_def else None
        if ir and ir.managed_params:
            clean_params = dict(action.params)
            for param in ir.managed_params:
                clean_params[param] = action_def.params_schema.get(param, {}).get("default")
            clean.append(dataclasses.replace(action, params=clean_params))
        else:
            clean.append(action)
    return generate_full_script(clean)


def open_folder(folder: Path):
    folder.mkdir(parents=True, exist_ok=True)
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))


class ProjectWindow(QMainWindow):
    """Top-level window for project-based EEG study management.

    Shows a participant/session tree on the left and a stacked detail panel on the right.
    """

    def __init__(self):
        super().__init__()
        # State
        self.project: Project | None = None
        self.project_dir: Path | None = None

        # Menu actions
        self.recent_menu = None
        self.open_folder_action = None
        self.add_p_action = None
        self.import_folder_action = None
        self.import_bids_action = None

        # Left panel
        self.participant_tree = None
        self.btn_add = None
        self.btn_remove = None
        self.btn_analysis = None
        self.left_panel = None
        self.left_sep = None

        # Right stack + detail pages
        self.right_stack = None
        self.welcome_widget = None
        self.no_selection_widget = None
        self.participant_detail_widget = None
        self.participant_detail_refs = None
        self.session_detail_widget = None
        self.session_detail_refs = None

        # Preprocessing page
        self.prep_page = None
        self.prep_refs = None
        self.prep_content = None
        self.prep_content_layout = None
        self.prep_window = None

        # Analysis page
        self.analysis_page = None
        self.analysis_refs = None
        self.analysis_content = None
        self.analysis_content_layout = None
        self.analysis_window = None

        self.setWindowTitle("MNETAPE")
        self.resize(1200, 760)

        self.setup_menu()
        self.setup_ui()

        # Restore last project
        settings = QSettings()
        last = settings.value(SETTINGS_LAST_PROJECT)
        if last and Path(last).is_dir():
            self.load_project(Path(last))

    # Menu

    def setup_menu(self):
        menubar = self.menuBar()

        file_menu = menubar.addMenu("File")

        new_action = QAction("New Project...", self)
        new_action.setShortcut(QKeySequence.StandardKey.New)
        new_action.triggered.connect(self.new_project)
        file_menu.addAction(new_action)

        open_action = QAction("Open Project...", self)
        open_action.setShortcut(QKeySequence.StandardKey.Open)
        open_action.triggered.connect(self.open_project)
        file_menu.addAction(open_action)

        self.open_folder_action = QAction("Open Project Folder", self)
        self.open_folder_action.triggered.connect(self.open_project_folder)
        self.open_folder_action.setEnabled(False)
        file_menu.addAction(self.open_folder_action)

        self.recent_menu = QMenu("Open Recent Project", self)
        self.recent_menu.aboutToShow.connect(self.refresh_recent_menu)
        file_menu.addMenu(self.recent_menu)

        file_menu.addSeparator()

        standalone_action = QAction("Open Without Project...", self)
        standalone_action.triggered.connect(open_standalone)
        file_menu.addAction(standalone_action)

        file_menu.addSeparator()

        quit_action = QAction("Quit", self)
        quit_action.setShortcut(QKeySequence.StandardKey.Quit)
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

        project_menu = menubar.addMenu("Project")

        self.add_p_action = QAction("Add Participant...", self)
        self.add_p_action.triggered.connect(self.add_participant)
        self.add_p_action.setEnabled(False)
        project_menu.addAction(self.add_p_action)

        self.import_folder_action = QAction("Import Participants from Folder...", self)
        self.import_folder_action.triggered.connect(self.import_from_folder)
        self.import_folder_action.setEnabled(False)
        project_menu.addAction(self.import_folder_action)

        self.import_bids_action = QAction("Import BIDS Dataset...", self)
        self.import_bids_action.triggered.connect(self.import_bids)
        self.import_bids_action.setEnabled(True)
        project_menu.addAction(self.import_bids_action)


    # UI

    def setup_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QHBoxLayout(central)
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

        self.participant_tree = QTreeWidget()
        self.participant_tree.setObjectName("participant_tree")
        self.participant_tree.setHeaderHidden(True)
        self.participant_tree.setColumnCount(1)
        self.participant_tree.setUniformRowHeights(True)
        self.participant_tree.currentItemChanged.connect(self.on_item_selected)
        self.participant_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.participant_tree.customContextMenuRequested.connect(self.show_tree_context_menu)
        left_layout.addWidget(self.participant_tree)

        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(8, 4, 8, 0)
        self.btn_add = QPushButton("+ Add")
        self.btn_add.setObjectName("btn_add_action")
        self.btn_add.clicked.connect(self.add_participant)
        self.btn_add.setEnabled(False)
        self.btn_remove = QPushButton("Remove")
        self.btn_remove.setEnabled(False)
        self.btn_remove.clicked.connect(self.remove_selected)
        btn_row.addWidget(self.btn_add)
        btn_row.addWidget(self.btn_remove)
        btn_row.addStretch()
        left_layout.addLayout(btn_row)

        analysis_sep = QFrame()
        analysis_sep.setFrameShape(QFrame.Shape.HLine)
        analysis_sep.setStyleSheet("color: #D5D5D8; margin: 4px 8px;")
        left_layout.addWidget(analysis_sep)

        self.btn_analysis = QPushButton("Open Analysis")
        self.btn_analysis.setObjectName("btn_analysis")
        self.btn_analysis.setContentsMargins(8, 0, 8, 0)
        self.btn_analysis.clicked.connect(self.open_analysis)
        left_layout.addWidget(self.btn_analysis)

        self.left_panel = left_panel
        main_layout.addWidget(left_panel)

        # Vertical separator
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setStyleSheet("color: #D5D5D8;")
        self.left_sep = sep
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

        # Page 4: preprocessing
        self.prep_page, self.prep_refs = self.make_preprocessing_page()
        self.right_stack.addWidget(self.prep_page)

        # Page 5: analysis
        self.analysis_page, self.analysis_refs = self.make_analysis_page()
        self.right_stack.addWidget(self.analysis_page)

        self.right_stack.setCurrentWidget(self.welcome_widget)
        main_layout.addWidget(self.right_stack, 1)

        self.setStatusBar(None)

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
        btn_new.setFixedWidth(170)
        btn_new.clicked.connect(self.new_project)
        btn_open = QPushButton("Open Project...")
        btn_open.setFixedWidth(170)
        btn_open.clicked.connect(self.open_project)
        btn_layout.addStretch()
        btn_layout.addWidget(btn_new)
        btn_layout.addSpacing(8)
        btn_layout.addWidget(btn_open)
        btn_layout.addStretch()
        layout.addLayout(btn_layout)

        layout.addSpacing(8)
        btn_standalone = QPushButton("Open Without Project...")
        btn_standalone.setFixedWidth(200)
        btn_standalone.clicked.connect(open_standalone)
        btn_standalone.setStyleSheet("color: gray;")
        btn_row2 = QHBoxLayout()
        btn_row2.addStretch()
        btn_row2.addWidget(btn_standalone)
        btn_row2.addStretch()
        layout.addLayout(btn_row2)

        return w

    # Participant detail page

    def make_participant_detail_widget(self) -> tuple[QWidget, dict]:
        """Build the participant detail panel."""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setObjectName("participant_detail_scroll")

        inner = QWidget()
        inner.setObjectName("participant_detail_content")
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

        status_label = QLabel()
        layout.addWidget(status_label)

        layout.addSpacing(4)

        notes_header = QLabel("Notes:")
        layout.addWidget(notes_header)
        notes_edit = QPlainTextEdit()
        notes_edit.setMaximumHeight(90)
        notes_edit.textChanged.connect(self.on_notes_changed)
        layout.addWidget(notes_edit)

        layout.addSpacing(4)

        excluded_check = QCheckBox("Exclude this participant from analysis")
        excluded_check.toggled.connect(self.on_excluded_toggled)
        layout.addWidget(excluded_check)

        exclusion_edit = QLineEdit()
        exclusion_edit.setPlaceholderText("Reason for exclusion...")
        exclusion_edit.setVisible(False)
        exclusion_edit.textChanged.connect(self.on_exclusion_reason_changed)
        layout.addWidget(exclusion_edit)

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
        btn_add_session.clicked.connect(self.add_session_to_selected_participant)
        btn_row.addWidget(btn_add_session)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        scroll.setWidget(inner)

        refs = {
            "id_label": id_label,
            "status_label": status_label,
            "notes_edit": notes_edit,
            "excluded_check": excluded_check,
            "exclusion_edit": exclusion_edit,
            "sessions_list_label": sessions_list_label,
        }
        return scroll, refs

    # Session detail page

    def make_session_detail_widget(self) -> tuple[QWidget, dict]:
        """Build the session detail panel."""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setObjectName("participant_detail_scroll")

        inner = QWidget()
        inner.setObjectName("session_detail")
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
        btn_add_run.setFixedWidth(90)
        btn_add_run.clicked.connect(self.add_session_run)
        runs_header.addWidget(btn_add_run)
        btn_remove_run = QPushButton("Remove")
        btn_remove_run.setFixedWidth(90)
        btn_remove_run.clicked.connect(self.remove_session_run)
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
        merge_runs_check.toggled.connect(self.on_merge_runs_toggled)
        layout.addWidget(merge_runs_check)

        layout.addStretch()

        btn_row = QHBoxLayout()
        btn_open = QPushButton("Open Preprocessing")
        btn_open.setObjectName("btn_add_action")
        btn_open.clicked.connect(self.open_preprocessing)
        btn_row.addWidget(btn_open)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        scroll.setWidget(inner)

        refs = {
            "id_label": id_label,
            "runs_container": runs_container,
            "runs_layout": runs_layout,
            "runs_button_group": runs_button_group,
            "merge_runs_check": merge_runs_check,
            "status_label": status_label,
        }
        return scroll, refs

    # Preprocessing page

    def make_preprocessing_page(self) -> tuple[QWidget, dict]:
        """Build the embedded preprocessing container."""
        page = QWidget()
        page.setObjectName("prep_page")
        outer = QVBoxLayout(page)
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
        btn_back.clicked.connect(self.close_preprocessing)
        header_layout.addWidget(btn_back)

        v_sep = QFrame()
        v_sep.setFrameShape(QFrame.Shape.VLine)
        v_sep.setStyleSheet("color: #D5D5D8;")
        header_layout.addWidget(v_sep)

        participant_label = QLabel()
        participant_label.setObjectName("prep_participant_label")
        header_layout.addWidget(participant_label)

        header_layout.addStretch()

        btn_set_default = QPushButton("Set as Default Pipeline")
        btn_set_default.setObjectName("btn_set_default_pipeline")
        btn_set_default.setToolTip("Save current pipeline as the project default")
        btn_set_default.clicked.connect(self.set_default_pipeline)
        header_layout.addWidget(btn_set_default)

        btn_use_default = QPushButton("Use Default Pipeline")
        btn_use_default.setObjectName("btn_use_default_pipeline")
        btn_use_default.setToolTip("Reset this participant's pipeline to the project default")
        btn_use_default.clicked.connect(self.use_default_pipeline)
        header_layout.addWidget(btn_use_default)

        v_sep2 = QFrame()
        v_sep2.setFrameShape(QFrame.Shape.VLine)
        v_sep2.setStyleSheet("color: #D5D5D8;")
        header_layout.addWidget(v_sep2)

        status_label = QLabel("Status: Pending")
        status_label.setStyleSheet("color: #888888; font-size: 11px; font-weight: bold;")
        status_label.setMinimumWidth(140)
        header_layout.addWidget(status_label)

        outer.addWidget(header)

        h_sep = QFrame()
        h_sep.setFrameShape(QFrame.Shape.HLine)
        h_sep.setStyleSheet("color: #D5D5D8;")
        outer.addWidget(h_sep)

        # ---- Content area ----
        self.prep_content = QWidget()
        self.prep_content.setObjectName("prep_content")
        self.prep_content_layout = QVBoxLayout(self.prep_content)
        self.prep_content_layout.setContentsMargins(0, 0, 0, 0)
        self.prep_content_layout.setSpacing(0)
        outer.addWidget(self.prep_content, 1)

        refs = {
            "participant_label": participant_label,
            "status_label": status_label,
        }
        return page, refs

    # Analysis page

    def make_analysis_page(self) -> tuple[QWidget, dict]:
        """Build the embedded analysis container."""
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        header = QWidget()
        header.setObjectName("prep_header")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(8, 6, 8, 6)
        header_layout.setSpacing(8)

        btn_back = QPushButton("← Back")
        btn_back.setObjectName("btn_back_to_project")
        btn_back.setFixedWidth(90)
        btn_back.clicked.connect(self.close_analysis)
        header_layout.addWidget(btn_back)

        v_sep = QFrame()
        v_sep.setFrameShape(QFrame.Shape.VLine)
        v_sep.setStyleSheet("color: #D5D5D8;")
        header_layout.addWidget(v_sep)

        title_label = QLabel("Analysis")
        title_label.setObjectName("prep_participant_label")
        header_layout.addWidget(title_label)
        header_layout.addStretch()
        outer.addWidget(header)

        h_sep = QFrame()
        h_sep.setFrameShape(QFrame.Shape.HLine)
        h_sep.setStyleSheet("color: #D5D5D8;")
        outer.addWidget(h_sep)

        self.analysis_content = QWidget()
        self.analysis_content_layout = QVBoxLayout(self.analysis_content)
        self.analysis_content_layout.setContentsMargins(0, 0, 0, 0)
        self.analysis_content_layout.setSpacing(0)
        outer.addWidget(self.analysis_content, 1)

        refs = {"title_label": title_label}
        return page, refs

    # Project load/save

    def load_project(self, project_dir: Path):
        """Load a project from disk and update the UI."""
        try:
            project = Project.load(project_dir)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Could not open project:\n{e}")
            logger.exception("Failed to load project from %s", project_dir)
            return

        self.project = project
        self.project_dir = project_dir
        self.setWindowTitle(f"MNETAPE - {project.name}")

        self.add_p_action.setEnabled(True)
        self.import_folder_action.setEnabled(True)
        self.open_folder_action.setEnabled(True)
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

    def refresh_recent_menu(self):
        self.recent_menu.clear()
        settings = QSettings()
        recent = settings.value("project/recent", [], list) or []
        recent = [r for r in recent if Path(r).is_dir()]
        if not recent:
            a = self.recent_menu.addAction("No recent projects")
            a.setEnabled(False)
            return
        for path in recent:
            act = self.recent_menu.addAction(path)
            act.triggered.connect(lambda _, p=path: self.load_project(Path(p)))

    # Tree building

    def rebuild_tree(self):
        """Repopulate the participant/session tree from project.participants."""
        self.participant_tree.blockSignals(True)
        self.participant_tree.clear()
        if self.project:
            for p in self.project.participants:
                p_item = make_participant_item(p)
                self.participant_tree.addTopLevelItem(p_item)
                for s in p.sessions:
                    s_item = make_session_item(p, s)
                    p_item.addChild(s_item)
                p_item.setExpanded(True)
        self.participant_tree.blockSignals(False)

    def refresh_participant_item(self, participant_id: str):
        """Refresh the display text of a participant and all its session items."""
        p = self.project.get_participant(participant_id)
        if not p:
            return
        for i in range(self.participant_tree.topLevelItemCount()):
            p_item = self.participant_tree.topLevelItem(i)
            if p_item.data(0, ROLE_PID) == participant_id:
                new_p = make_participant_item(p)
                p_item.setText(0, new_p.text(0))
                p_item.setForeground(0, new_p.foreground(0))
                p_item.setFont(0, new_p.font(0))
                # Refresh children
                for j in range(p_item.childCount()):
                    s_item = p_item.child(j)
                    sid = s_item.data(0, ROLE_SID)
                    s = p.get_session(sid)
                    if s:
                        new_s = make_session_item(p, s)
                        s_item.setText(0, new_s.text(0))
                        s_item.setForeground(0, new_s.foreground(0))
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

    def on_item_selected(self, current: QTreeWidgetItem | None, previous):
        if not self.project or current is None:
            if self.right_stack.currentWidget() != self.prep_page:
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
                if self.right_stack.currentWidget() != self.prep_page:
                    self.right_stack.setCurrentWidget(self.participant_detail_widget)
        elif item_type == "session":
            pid = current.data(0, ROLE_PID)
            sid = current.data(0, ROLE_SID)
            p = self.project.get_participant(pid)
            if p:
                s = p.get_session(sid)
                if s:
                    self.populate_session_detail(p, s)
                    if self.right_stack.currentWidget() != self.prep_page:
                        self.right_stack.setCurrentWidget(self.session_detail_widget)

    # Detail population

    def populate_participant_detail(self, p: Participant):
        refs = self.participant_detail_refs
        refs["id_label"].setText(f"<b>{p.id}</b>")

        if p.excluded:
            refs["status_label"].setText("<span style='color:#888888;'>─ Excluded</span>")
        else:
            status = p.participant_status
            color = STATUS_COLORS.get(status, "#888888")
            label = STATUS_LABELS.get(status, str(status))
            refs["status_label"].setText(
                f"<span style='color:{color};'>{STATUS_ICONS.get(status, '')} {label}</span>"
            )

        refs["notes_edit"].blockSignals(True)
        refs["notes_edit"].setPlainText(p.notes)
        refs["notes_edit"].blockSignals(False)

        refs["excluded_check"].blockSignals(True)
        refs["excluded_check"].setChecked(p.excluded)
        refs["excluded_check"].blockSignals(False)
        refs["exclusion_edit"].setVisible(p.excluded)
        refs["exclusion_edit"].blockSignals(True)
        refs["exclusion_edit"].setText(p.exclusion_reason)
        refs["exclusion_edit"].blockSignals(False)

        # Sessions summary
        session_lines = []
        for s in p.sessions:
            icon = STATUS_ICONS.get(s.session_status, "◌")
            n_runs = len(s.data_files)
            runs_str = f"  [{n_runs} run{'s' if n_runs != 1 else ''}]" if n_runs else ""
            session_lines.append(
                f"ses-{s.id}  {icon}  {STATUS_LABELS.get(s.session_status, s.status)}{runs_str}"
            )
        refs["sessions_list_label"].setText("\n".join(session_lines) if session_lines else "No sessions")

    def populate_session_detail(self, p: Participant, s: Session):
        refs = self.session_detail_refs
        refs["id_label"].setText(f"<b>{p.id}</b>  /  ses-{s.id}")

        # Rebuild button-like run items
        runs_layout: QVBoxLayout = refs["runs_layout"]
        button_group: QButtonGroup = refs["runs_button_group"]

        for btn in list(button_group.buttons()):
            button_group.removeButton(btn)
        while runs_layout.count():
            item = runs_layout.takeAt(0)
            if item and item.widget():
                item.widget().deleteLater()

        if self.project_dir:
            resolved = self.project.resolve_data_files(self.project_dir, s)
            _btn_style = """
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
            if s.merge_runs:
                n = len(s.data_files)
                is_processed = bool(s.processed_files)
                run_icon = "●" if is_processed else "○"
                run_label = f"run{'s' if n != 1 else ''}"
                btn = QPushButton(f"{run_icon}  Merged  ({n} {run_label})")
                btn.setCheckable(True)
                btn.setChecked(True)
                any_missing = any(not p.exists() for p in resolved)
                btn.setStyleSheet(_btn_style.format(text_color="#C62828" if any_missing else "inherit"))
                button_group.addButton(btn, 0)
                runs_layout.addWidget(btn)
            else:
                for i, (raw_str, path) in enumerate(zip(s.data_files, resolved)):
                    filename = Path(raw_str).name
                    is_processed = (
                        i < len(s.processed_files) and bool(s.processed_files[i])
                    )
                    run_icon = "●" if is_processed else "○"
                    btn = QPushButton(f"{run_icon}  {filename}")
                    btn.setCheckable(True)
                    btn.setStyleSheet(_btn_style.format(
                        text_color="#C62828" if not path.exists() else "inherit"
                    ))
                    button_group.addButton(btn, i)
                    runs_layout.addWidget(btn)

        status = s.session_status
        color = STATUS_COLORS.get(status, "#888888")
        label = STATUS_LABELS.get(status, s.status)
        msg = f"<span style='color:{color};'>{STATUS_ICONS.get(status, '')} {label}</span>"
        if s.error_msg:
            msg += f"<br><small style='color:#C62828;'>{s.error_msg}</small>"
        refs["status_label"].setText(msg)

        refs["merge_runs_check"].blockSignals(True)
        refs["merge_runs_check"].setChecked(s.merge_runs)
        refs["merge_runs_check"].blockSignals(False)

    # Detail editing

    def add_session_run(self):
        """Append a run file to the current session's data_files list."""
        from mnetape.core.data_io import open_file_dialog_filter
        p, s = self.get_selected_session()
        if not p or not s:
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Select EEG Run File", "", open_file_dialog_filter()
        )
        if not path:
            return
        if self.project_dir:
            try:
                file_str = str(Path(path).relative_to(self.project_dir))
            except ValueError:
                file_str = path
        else:
            file_str = path
        if file_str not in s.data_files:
            s.data_files.append(file_str)
            self.save_project()
            self.populate_session_detail(p, s)

    def remove_session_run(self):
        """Remove the selected run file from the current session's data_files list."""
        p, s = self.get_selected_session()
        if not p or not s:
            return
        button_group: QButtonGroup = self.session_detail_refs["runs_button_group"]
        row = button_group.checkedId()
        if row < 0 or row >= len(s.data_files):
            return
        s.data_files.pop(row)
        if row < len(s.processed_files):
            s.processed_files.pop(row)
        self.save_project()
        self.populate_session_detail(p, s)

    def on_merge_runs_toggled(self, checked: bool):
        """Handle the merge-runs checkbox toggle; optionally apply to all sessions/participants."""
        p, s = self.get_selected_session()
        if not p or not s:
            return

        all_sessions = [(p, s) for p in self.project.participants for s in p.sessions] if self.project else [(p, s)]
        p_sessions = [(p, ps) for ps in p.sessions]

        if len(all_sessions) > 1:
            action = "Enable" if checked else "Disable"
            reply = QMessageBox.question(
                self,
                "Apply to all?",
                f"{action} merge runs for all participants in the project?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.Cancel:
                # Revert checkbox without re-triggering signal
                self.session_detail_refs["merge_runs_check"].blockSignals(True)
                self.session_detail_refs["merge_runs_check"].setChecked(s.merge_runs)
                self.session_detail_refs["merge_runs_check"].blockSignals(False)
                return
            pairs_to_update = all_sessions if reply == QMessageBox.StandardButton.Yes else p_sessions
        else:
            pairs_to_update = p_sessions

        affected_pids = set()
        for participant, session in pairs_to_update:
            session.merge_runs = checked
            session.status = ParticipantStatus.PENDING
            session.error_msg = ""
            session.processed_files = []
            affected_pids.add(participant.id)

        self.save_project()
        for pid in affected_pids:
            self.refresh_participant_item(pid)
        self.populate_session_detail(p, s)

    def on_notes_changed(self):
        p = self.get_selected_participant()
        if p:
            p.notes = self.participant_detail_refs["notes_edit"].toPlainText()
            self.save_project()

    def on_excluded_toggled(self, checked: bool):
        p = self.get_selected_participant()
        if p:
            p.excluded = checked
            self.save_project()
            self.populate_participant_detail(p)
            self.refresh_participant_item(p.id)

    def on_exclusion_reason_changed(self, text: str):
        p = self.get_selected_participant()
        if p:
            p.exclusion_reason = text
            self.save_project()

    # Project actions

    def new_project(self):
        from mnetape.gui.dialogs.new_project_dialog import NewProjectDialog
        dlg = NewProjectDialog(self)
        if dlg.exec() != NewProjectDialog.DialogCode.Accepted:
            return

        name = dlg.get_name()
        project_dir = dlg.get_project_dir()

        project = Project(name=name)
        project.save(project_dir)

        pipeline_path = project.pipeline_path(project_dir)
        if not pipeline_path.exists():
            pipeline_path.write_text(
                "# MNETAPE Pipeline\n"
                "# This script is shared across all participants.\n"
                "# Use --file to specify the EEG data file path.\n\n"
                "import argparse\nimport mne\n\nparser = argparse.ArgumentParser()\n"
                "parser.add_argument('--file', required=True)\nargs = parser.parse_args()\n\n"
                "raw = mne.io.read_raw(args.file, preload=True)\n"
            )

        self.load_project(project_dir)

    def open_project(self):
        project_dir = QFileDialog.getExistingDirectory(self, "Open Project Folder")
        if not project_dir:
            return
        path = Path(project_dir)
        if not (path / "project.json").exists():
            QMessageBox.warning(
                self, "Not a project",
                "The selected folder does not contain a project.json file.\n\n"
                "Choose the root folder of a MNETAPE project."
            )
            return
        self.load_project(path)

    def open_analysis(self):
        from mnetape.gui.controllers.analysis_window import AnalysisWindow
        if self.analysis_window is None:
            self.analysis_window = AnalysisWindow(
                project=self.project, project_dir=self.project_dir
            )
            # Hide the analysis window's own menu bar
            self.analysis_window.menuBar().setVisible(False)
            self.analysis_content_layout.addWidget(self.analysis_window)
        else:
            self.analysis_window.project = self.project
            self.analysis_window.project_dir = self.project_dir
            self.analysis_window.rebuild_tree()

        if self.project:
            self.analysis_refs["title_label"].setText(f"Analysis - {self.project.name}")

        self.left_panel.setVisible(False)
        self.left_sep.setVisible(False)
        self.right_stack.setCurrentWidget(self.analysis_page)

    def close_analysis(self):
        self.left_panel.setVisible(True)
        self.left_sep.setVisible(True)
        item_type, pid, sid = self.get_selected_item_data()
        if self.project and item_type == "session" and pid and sid:
            p = self.project.get_participant(pid)
            if p:
                s = p.get_session(sid)
                if s:
                    self.populate_session_detail(p, s)
                    self.right_stack.setCurrentWidget(self.session_detail_widget)
                    return
        elif self.project and item_type == "participant" and pid:
            p = self.project.get_participant(pid)
            if p:
                self.populate_participant_detail(p)
                self.right_stack.setCurrentWidget(self.participant_detail_widget)
                return
        self.right_stack.setCurrentWidget(self.no_selection_widget)

    # Participant/session management

    def add_participant(self):
        if not self.project:
            return
        from mnetape.gui.dialogs.add_participant_dialog import AddParticipantDialog
        dlg = AddParticipantDialog(
            existing_ids=[p.id for p in self.project.participants],
            project_dir=self.project_dir,
            parent=self,
        )
        if dlg.exec() != AddParticipantDialog.DialogCode.Accepted:
            return
        initial_file = dlg.get_file()
        session = Session(id=dlg.get_session_id(), data_files=[initial_file] if initial_file else [])
        participant = Participant(id=dlg.get_id(), sessions=[session])
        self.project.participants.append(participant)
        self.save_project()
        self.rebuild_tree()
        # Select the newly added participant
        last = self.participant_tree.topLevelItem(self.participant_tree.topLevelItemCount() - 1)
        if last:
            self.participant_tree.setCurrentItem(last)

    def add_session_to_selected_participant(self):
        """Add a new session to the currently selected participant."""
        p = self.get_selected_participant()
        if not p:
            return

        # Reuse the dialog but we only care about session_id and file
        # We build a minimal dialog inline using the existing class
        from PyQt6.QtWidgets import QInputDialog
        sid, ok = QInputDialog.getText(self, "Add Session", "Session ID:", text="01")
        if not ok or not sid.strip():
            return
        sid = sid.strip()
        if p.get_session(sid):
            QMessageBox.warning(self, "Duplicate", f'Session "{sid}" already exists.')
            return

        from mnetape.core.data_io import open_file_dialog_filter
        path, _ = QFileDialog.getOpenFileName(
            self, "Select EEG File (optional)", "", open_file_dialog_filter()
        )
        data_files: list[str] = []
        if path:
            try:
                data_files = [str(Path(path).relative_to(self.project_dir))]
            except (ValueError, TypeError):
                data_files = [path]

        session = Session(id=sid, data_files=data_files)
        p.sessions.append(session)
        self.save_project()
        self.rebuild_tree()
        self.populate_participant_detail(p)

    def remove_selected(self):
        item_type, pid, sid = self.get_selected_item_data()
        if item_type == "participant":
            self.remove_participant()
        elif item_type == "session":
            self.remove_session(pid, sid)

    def remove_participant(self):
        p = self.get_selected_participant()
        if not p:
            return
        if self.prep_window and self.prep_window.project_context:
            if self.prep_window.project_context.participant.id == p.id:
                QMessageBox.information(
                    self, "Participant in use",
                    "Close the preprocessing session before removing this participant."
                )
                return
        reply = QMessageBox.question(
            self, "Remove Participant",
            f'Remove participant "{p.id}" from the project?\n\n'
            "This does not delete any files from disk.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self.project.participants.remove(p)
        self.save_project()
        self.rebuild_tree()
        if self.participant_tree.topLevelItemCount() == 0:
            self.right_stack.setCurrentWidget(self.no_selection_widget)

    def remove_session(self, participant_id: str, session_id: str):
        if not self.project:
            return
        p = self.project.get_participant(participant_id)
        if not p:
            return
        s = p.get_session(session_id)
        if not s:
            return
        reply = QMessageBox.question(
            self, "Remove Session",
            f'Remove session "ses-{s.id}" from participant "{p.id}"?\n\n'
            "This does not delete any files from disk.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        p.sessions.remove(s)
        self.save_project()
        self.rebuild_tree()
        self.right_stack.setCurrentWidget(self.no_selection_widget)

    def import_from_folder(self):
        if not self.project:
            return
        folder = QFileDialog.getExistingDirectory(self, "Select Folder with EEG Files")
        if not folder:
            return

        folder_path = Path(folder)
        existing_ids = {p.id for p in self.project.participants}

        extensions = (".fif", ".edf", ".bdf", ".set", ".vhdr", ".brainvision")
        files = sorted(f for f in folder_path.iterdir() if f.suffix.lower() in extensions)
        if not files:
            QMessageBox.information(
                self, "No Files Found",
                "No recognized EEG files found in the selected folder."
            )
            return

        added = 0
        for f in files:
            pid = f.stem
            if pid in existing_ids:
                continue
            try:
                file_str = str(f.relative_to(self.project_dir))
            except ValueError:
                file_str = str(f)
            session = Session(id="01", data_files=[file_str])
            self.project.participants.append(Participant(id=pid, sessions=[session]))
            existing_ids.add(pid)
            added += 1

        if added:
            self.save_project()
            self.rebuild_tree()
            self.status_bar.showMessage(f"Imported {added} participant(s).")
        else:
            QMessageBox.information(self, "No New Participants", "All files already have entries.")

    def import_bids(self):
        """Import participants and sessions from a BIDS dataset directory."""
        bids_dir = QFileDialog.getExistingDirectory(self, "Select BIDS Dataset Root")
        if not bids_dir:
            return
        bids_path = Path(bids_dir)

        # If no project is open, create one first
        if not self.project:
            from mnetape.gui.dialogs.new_project_dialog import NewProjectDialog
            dlg = NewProjectDialog(self)
            if dlg.exec() != NewProjectDialog.DialogCode.Accepted:
                return
            name = dlg.get_name()
            project_dir = dlg.get_project_dir()
            project = Project(name=name)
            project.save(project_dir)
            self.load_project(project_dir)

        try:
            bids_project = Project.from_bids(bids_path, self.project_dir)
        except Exception as e:
            QMessageBox.critical(self, "BIDS Import Error", f"Failed to parse BIDS dataset:\n{e}")
            logger.exception("BIDS import failed for %s", bids_path)
            return

        existing_ids = {p.id for p in self.project.participants}
        added = 0
        for p in bids_project.participants:
            if p.id not in existing_ids:
                self.project.participants.append(p)
                existing_ids.add(p.id)
                added += 1

        if added:
            self.save_project()
            self.rebuild_tree()
            self.status_bar.showMessage(f"Imported {added} participant(s) from BIDS dataset.")
        else:
            QMessageBox.information(self, "No New Participants", "All BIDS subjects already exist.")

    def show_tree_context_menu(self, pos):
        item = self.participant_tree.itemAt(pos)
        if not item:
            return
        item_type = item.data(0, ROLE_TYPE)
        menu = QMenu(self)

        if item_type == "participant":
            add_session_action = menu.addAction("Add Session...")
            add_session_action.triggered.connect(self.add_session_to_selected_participant)
            open_p_folder_action = menu.addAction("Open Participant Folder")
            open_p_folder_action.triggered.connect(self.open_participant_folder)
            menu.addSeparator()
            remove_action = menu.addAction("Remove Participant")
            remove_action.triggered.connect(self.remove_participant)
        elif item_type == "session":
            open_session_action = menu.addAction("Open Session Folder")
            open_session_action.triggered.connect(self.open_session_folder)
            open_data_action = menu.addAction("Open Data Folder")
            open_data_action.triggered.connect(self.open_participant_data_folder)
            menu.addSeparator()
            remove_action = menu.addAction("Remove Session")
            pid = item.data(0, ROLE_PID)
            sid = item.data(0, ROLE_SID)
            remove_action.triggered.connect(lambda: self.remove_session(pid, sid))

        menu.exec(self.participant_tree.mapToGlobal(pos))

    def open_project_folder(self):
        if self.project_dir:
            open_folder(self.project_dir)

    def open_participant_folder(self):
        p = self.get_selected_participant()
        if not p or not self.project_dir:
            return
        open_folder(self.project.participant_dir(self.project_dir, p))

    def open_session_folder(self):
        p, s = self.get_selected_session()
        if not p or not s or not self.project_dir:
            return
        open_folder(self.project.session_dir(self.project_dir, p, s))

    def open_participant_data_folder(self):
        """Open the source data folder for the selected session in the system file manager."""
        p, s = self.get_selected_session()
        if not p or not s or not self.project_dir:
            return
        folder: Path | None = None
        if s.data_files:
            resolved = self.project.resolve_data_files(self.project_dir, s)
            if resolved:
                folder = resolved[0].parent
        if folder is None or not folder.exists():
            folder = self.project.session_dir(self.project_dir, p, s)
        open_folder(folder)

    # Embedded preprocessing

    def open_preprocessing(self):
        """Embed the preprocessing UI for the selected session in the right panel."""
        p, s = self.get_selected_session()
        if not p or not s:
            # If a participant node is selected but no session, try first session
            p2 = self.get_selected_participant()
            if p2 and p2.sessions:
                p = p2
                s = p2.sessions[0]
            else:
                QMessageBox.information(
                    self, "No Session Selected",
                    "Please select a session from the tree to open preprocessing."
                )
                return

        # If same session is already open, just switch back to preprocessing view
        if self.prep_window and self.prep_window.project_context:
            ctx = self.prep_window.project_context
            if ctx.participant.id == p.id and ctx.session.id == s.id:
                self.right_stack.setCurrentWidget(self.prep_page)
                return

        # Close any existing session (different participant/session)
        if self.prep_window is not None:
            self.close_preprocessing(report_status=True)

        # Resolve run files; merge_runs=True → all files, False → selected run button
        resolved = self.project.resolve_data_files(self.project_dir, s)
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

        from mnetape.gui.controllers.main_window import MainWindow

        ctx = ProjectContext(
            project=self.project,
            project_dir=self.project_dir,
            participant=p,
            session=s,
            on_status_update=lambda status, pid=p.id, sid=s.id: self.on_session_status_update(
                pid, sid, status
            ),
            data_files=data_files,
            run_index=run_index,
        )
        self.prep_window = MainWindow(project_context=ctx)

        self.prep_window.host_window = self

        # Take the central widget from MainWindow and embed it in our content area
        central = self.prep_window.takeCentralWidget()
        self.prep_content_layout.addWidget(central)

        # Embed the status bar below the content so messages and raw-info stay visible
        self.prep_content_layout.addWidget(self.prep_window.status)

        # Show current status in the header label (per-run when not merging)
        self.update_prep_status_label(s, run_index)

        # Update header participant label
        run_text = run_index + 1 if not s.merge_runs else "merged"
        self.prep_refs["participant_label"].setText(
            f"<b>{p.id}</b>  /  ses-{s.id}  /  run-{run_text}  ·  {self.project.name}"
        )

        # Hide sidebar
        self.left_panel.setVisible(False)
        self.left_sep.setVisible(False)

        # Switch to preprocessing view
        self.right_stack.setCurrentWidget(self.prep_page)

        self.prep_window.auto_load()

    def close_preprocessing(self, report_status: bool = True):
        """Close the embedded preprocessing session and return to the detail view."""
        if self.prep_window is None:
            return

        if report_status:
            ctx = self.prep_window.project_context
            if ctx:
                actions = self.prep_window.state.actions
                if any(a.status.name == "ERROR" for a in actions):
                    final_status = ParticipantStatus.ERROR
                elif actions and all(a.status.name == "COMPLETE" for a in actions):
                    final_status = ParticipantStatus.DONE
                else:
                    final_status = ParticipantStatus.PENDING
                try:
                    ctx.on_status_update(final_status)
                except Exception as e:
                    logger.warning("Failed to report preprocessing status: %s", e)

        # Remove embedded central widget
        while self.prep_content_layout.count():
            item = self.prep_content_layout.takeAt(0)
            if item.widget():
                item.widget().setParent(None)

        # Clean up resources
        self.prep_window.cleanup()
        self.prep_window.deleteLater()
        self.prep_window = None

        # Restore sidebar
        self.left_panel.setVisible(True)
        self.left_sep.setVisible(True)

        # Clear status label
        self.prep_refs["status_label"].setText("")

        # Return to appropriate detail view
        item_type, pid, sid = self.get_selected_item_data()
        if item_type == "session" and pid and sid:
            p = self.project.get_participant(pid)
            if p:
                s = p.get_session(sid)
                if s:
                    self.populate_session_detail(p, s)
                    self.right_stack.setCurrentWidget(self.session_detail_widget)
                    return
        elif item_type == "participant" and pid:
            p = self.project.get_participant(pid)
            if p:
                self.populate_participant_detail(p)
                self.right_stack.setCurrentWidget(self.participant_detail_widget)
                return
        self.right_stack.setCurrentWidget(self.no_selection_widget)

    def set_default_pipeline(self):
        """Save current pipeline as the project default; optionally reset participant overrides."""
        if not self.prep_window or not self.project or not self.project_dir:
            return
        code = strip_managed_params(self.prep_window.state.actions)
        if not code:
            return

        reply = QMessageBox.question(
            self,
            "Set as Default Pipeline?",
            "Overwrite the project default pipeline with the current participant's pipeline?\n"
            "Participants using the default will get this pipeline next time they are opened.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        # Check which participants have a custom pipeline override
        custom_paths = []
        for p in self.project.participants:
            for s in p.sessions:
                path = self.project.participant_pipeline_path(self.project_dir, p, s)
                if path.exists():
                    custom_paths.append(path)

        if custom_paths:
            reply = QMessageBox.question(
                self,
                "Reset Participant Pipelines?",
                f"{len(custom_paths)} participant session(s) have custom pipelines.\n"
                "Reset them to the new default?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel,
            )
            if reply == QMessageBox.StandardButton.Cancel:
                return
            if reply == QMessageBox.StandardButton.Yes:
                for path in custom_paths:
                    path.unlink(missing_ok=True)

        default_path = self.project.pipeline_path(self.project_dir)
        default_path.parent.mkdir(parents=True, exist_ok=True)
        default_path.write_text(code)
        self.status_bar.showMessage(f"Saved as default pipeline: {default_path.name}")
        logger.info("Set default pipeline: %s", default_path)

    def use_default_pipeline(self):
        """Reset this participant's pipeline to the project default."""
        if not self.prep_window or not self.project or not self.project_dir:
            return
        default_path = self.project.pipeline_path(self.project_dir)
        if not default_path.exists():
            QMessageBox.information(self, "No Default Pipeline", "No default pipeline found for this project.")
            return
        try:
            from mnetape.core.codegen import parse_script_to_actions
            code = default_path.read_text()
            actions = parse_script_to_actions(code)
            # Update load_file with actual participant file path
            data_fp = self.prep_window.state.data_filepath
            if data_fp and actions and actions[0].action_id == "load_file":
                actions[0].params["file_path"] = str(data_fp)
            self.prep_window.state.actions = actions
            self.prep_window.state.data_states.clear()
            self.prep_window.code_panel.set_code(code)
            self.prep_window.update_action_list()
            self.status_bar.showMessage("Loaded default pipeline")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to load default pipeline:\n{e}")

    def save_participant_pipeline(self):
        """Save the current pipeline as an override for this participant/session only."""
        if not self.prep_window or not self.prep_window.project_context:
            return
        ctx = self.prep_window.project_context
        path = self.project.participant_pipeline_path(self.project_dir, ctx.participant, ctx.session)
        path.parent.mkdir(parents=True, exist_ok=True)
        code = self.prep_window.code_panel.get_code()
        if not code:
            return
        try:
            path.write_text(code)
            self.prep_window.state.pipeline_filepath = path
            self.prep_window.code_panel.set_file(path)
            self.status_bar.showMessage(f"Saved participant pipeline: {path.name}")
            logger.info("Saved participant pipeline: %s", path)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to save participant pipeline:\n{e}")

    def update_prep_status_label(self, s: Session, run_index: int | None = None):
        """Update the preprocessing header status label.

        When run_index is set (merge_runs=False), shows per-run processed state.
        Otherwise, shows the overall session status.
        """
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
        label: QLabel = self.prep_refs["status_label"]
        label.setText(f"Status: {text}")
        label.setStyleSheet(f"color: {color}; font-size: 11px; font-weight: bold;")

    def on_session_status_update(self, participant_id: str, session_id: str, new_status: ParticipantStatus):
        """Called when a preprocessing session reports its final status."""
        p = self.project.get_participant(participant_id)
        if not p:
            return
        s = p.get_session(session_id)
        if not s:
            return

        # session_status derives DONE/INCOMPLETE/PENDING from processed_files automatically.
        # Only persist ERROR and RUNNING so they survive across open/close cycles.
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
        # Refresh the prep header status label if this session is currently open
        if self.prep_window and self.prep_window.project_context:
            ctx = self.prep_window.project_context
            if ctx.participant.id == participant_id and ctx.session.id == session_id:
                self.update_prep_status_label(s, ctx.run_index)
        logger.info("Participant %s / ses-%s status → %s", participant_id, session_id, new_status)

    # Window events

    def event(self, event):
        if event.type() == QEvent.Type.WindowActivate:
            modal = QApplication.activeModalWidget()
            if modal:
                modal.raise_()
                modal.activateWindow()
        return super().event(event)

    def closeEvent(self, event):
        if self.prep_window is not None:
            self.close_preprocessing(report_status=True)
        super().closeEvent(event)
