"""Main application window for the EEG preprocessing pipeline.

MainWindow is the top-level QMainWindow. It owns the shared AppState and instantiates the four controller objects
(FileHandler, PipelineRunner, ActionController, NavController) that implement all user-facing operations.
The window itself only builds the menu, sets up the layout widgets, and provides update helpers that keep
the action list, code panel, and visualization panel in sync.
"""

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QAction, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QPushButton,
    QStackedWidget,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from mnetape.core.codegen import (
    extract_action_blocks,
    generate_action_code,
    generate_full_script,
)
from mnetape.gui.controllers.action_controller import ActionController
from mnetape.gui.controllers.file_handler import FileHandler
from mnetape.gui.controllers.nav_controller import NavController
from mnetape.gui.controllers.pipeline_runner import PipelineRunner
from mnetape.gui.controllers.state import AppState
from mnetape.gui.panels import CodePanel, VisualizationPanel
from mnetape.gui.widgets import ActionListItem


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
    """

    def __init__(self):
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

        # Basic window setup
        self.setWindowTitle("MNETAPE")
        self.resize(1400, 900)

        # State
        self.state = AppState.create()

        # Helpers
        self.files = FileHandler(self)
        self.runner = PipelineRunner(self)
        self.action_ctrl = ActionController(self)
        self.nav = NavController(self)

        # UI
        self.setup_menu()
        self.setup_ui()
        self.setup_shortcuts()

        self.status = QStatusBar()
        self.setStatusBar(self.status)
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
        export_action.triggered.connect(self.files.export_file)
        file_menu.addAction(export_action)

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

        view_menu = menubar.addMenu("View")

        browser_action = QAction("Open MNE Browser", self)
        browser_action.setShortcut(QKeySequence("Ctrl+B"))
        browser_action.triggered.connect(self.nav.open_browser)
        view_menu.addAction(browser_action)


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

        self.action_list = QListWidget()
        self.action_list.itemClicked.connect(self.action_ctrl.on_action_clicked)
        self.action_list.itemDoubleClicked.connect(self.action_ctrl.on_action_double_clicked)
        self.action_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.action_list.customContextMenuRequested.connect(self.action_ctrl.show_action_context_menu)
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

        left_layout.addLayout(move_btns)

        self.btn_run = QPushButton("\u25b6  Run All")
        self.btn_run.setStyleSheet(
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
            QPushButton:hover {
                background-color: #388E3C;
            }
            QPushButton:pressed {
                background-color: #1B5E20;
            }
        """
        )
        self.btn_run.clicked.connect(self.runner.run_all)
        left_layout.addWidget(self.btn_run)

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
        self.viz_panel.step_combo.currentIndexChanged.connect(self.nav.on_step_changed)
        self.viz_panel.btn_prev.clicked.connect(self.nav.prev_step)
        self.viz_panel.btn_next.clicked.connect(self.nav.next_step)
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
        for i, action in enumerate(self.state.actions, 1):
            item = QListWidgetItem()
            widget = ActionListItem(i, action)
            item.setSizeHint(widget.sizeHint())
            widget.size_changed.connect(lambda it=item, w=widget: it.setSizeHint(w.sizeHint()))
            widget.step_clicked.connect(self.action_ctrl.on_step_clicked)
            widget.run_clicked.connect(self.runner.run_action_at)
            self.action_list.addItem(item)
            self.action_list.setItemWidget(item, widget)

        self.viz_panel.update_step_list(self.state.actions)
        if sync_code:
            self.update_code()
        self.update_button_states()

    def update_button_states(self):
        """Enable or disable the move-up and move-down buttons based on selection."""
        row = self.action_list.currentRow()
        has_selection = row >= 0
        self.btn_move_up.setEnabled(has_selection and row > 0)
        self.btn_move_down.setEnabled(has_selection and row < len(self.state.actions) - 1)

    def update_code(self):
        """Regenerate the full pipeline script and push it to the code panel."""
        code = generate_full_script(self.state.data_filepath, self.state.actions)
        self.code_panel.set_code(code)
        self.files.auto_save()

    def update_visualization(self):
        """Refresh the visualization panel for the currently selected pipeline step."""
        step = self.viz_panel.step_combo.currentIndex()

        if step == 0:
            raw_to_show = self.state.raw_original
        elif 0 < step <= len(self.state.raw_states):
            raw_to_show = self.state.raw_states[step - 1]
        else:
            raw_to_show = self.state.raw_original

        self.viz_panel.update_plots(raw_to_show, step, len(self.state.raw_states))


    # --------- Code generation and execution ---------

    def get_action_blocks(self) -> list[dict]:
        """Extract per-action code blocks from the current editor content.

        Falls back to generating the script from the action list when the editor is empty.

        Returns:
            List of block dicts as returned by extract_action_blocks.
        """
        script = self.code_panel.get_code()
        if not script.strip():
            script = generate_full_script(self.state.data_filepath, self.state.actions)
        return extract_action_blocks(script)

    def get_action_code(self, index: int, action) -> str:
        """Return the source code for a single action, from the editor or generated.

        Args:
            index: Position of the action in the pipeline.
            action: The ActionConfig whose code should be returned.

        Returns:
            Python source code string for the action.
        """
        blocks = self.get_action_blocks()
        if index < len(blocks):
            return blocks[index]["code"]
        return generate_action_code(action)
