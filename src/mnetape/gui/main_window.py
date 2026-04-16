"""Thin QMainWindow shell that hosts ProjectPage and PreprocessingPage on a QStackedWidget.

MainWindow owns the menu bars, status bar, geometry persistence, and the page-switching logic.
All application content lives in the two page widgets.
"""

import logging
from pathlib import Path

from PyQt6.QtCore import QSettings
from PyQt6.QtGui import QAction, QKeySequence
from PyQt6.QtWidgets import QLabel, QMainWindow, QMenu, QStackedWidget, QStatusBar

from mnetape.gui.pages.project_page import ProjectPage
from mnetape.gui.pages.preprocessing_page import PreprocessingPage

logger = logging.getLogger(__name__)

STACK_PROJECT = 0
STACK_PREP = 1


class MainWindow(QMainWindow):
    """Thin shell window that switches between ProjectPage and PreprocessingPage."""

    def __init__(self):
        super().__init__()
        self.settings = QSettings()

        # Permanent raw-info label in the status bar
        self.raw_info_label = QLabel()
        self.raw_info_label.setStyleSheet("color: gray;")
        self._status_bar = QStatusBar(self)
        self.setStatusBar(self._status_bar)
        self._status_bar.addPermanentWidget(self.raw_info_label)

        # Stacked widget: slot 0 = project, slot 1 = prep
        self.stack = QStackedWidget()
        self.setCentralWidget(self.stack)

        # Project page
        self.project_page = ProjectPage(self.settings)
        self.stack.addWidget(self.project_page)

        # Prep page placeholder
        self.prep_page: PreprocessingPage | None = None

        # Build single menu bar with per-page menu groups
        self.project_menus: list[QMenu] = []
        self.prep_menus: list[QMenu] = []
        self.prep_recent_menu = QMenu(self)  # placeholder, replaced in build_menus
        self.build_menus()

        # Wire project page signals
        self.project_page.open_preprocessing_requested.connect(self.on_open_preprocessing)
        self.project_page.close_project_requested.connect(self.on_close_project)
        self.project_page.status_message.connect(lambda msg, t: self._status_bar.showMessage(msg, t))
        self.project_page.title_change.connect(self.setWindowTitle)

        # Show project page first
        self.show_project_page()
        self.setMinimumSize(1100, 700)
        self.setWindowTitle("MNETAPE")

        # Restore geometry
        geom = self.settings.value("main_window/geometry")
        if geom:
            self.restoreGeometry(geom)

        # Restore last project
        last = self.settings.value("project/last_dir")
        if last and Path(last).is_dir():
            self.project_page.load_project(Path(last))

    # -------- Menu bar --------

    def build_menus(self):
        """Build one menu bar with project and prep groups; visibility is toggled per page."""
        bar = self.menuBar()
        assert bar is not None

        # ---- Project menus ----
        proj_file = QMenu("File", self)
        bar.addMenu(proj_file)
        new_action = QAction("New Project...", self)
        new_action.setShortcut(QKeySequence.StandardKey.New)
        new_action.triggered.connect(self.project_page.new_project)
        proj_file.addAction(new_action)

        open_action = QAction("Open Project...", self)
        open_action.setShortcut(QKeySequence.StandardKey.Open)
        open_action.triggered.connect(self.project_page.open_project)
        proj_file.addAction(open_action)

        open_folder_action = QAction("Open Project Folder", self)
        open_folder_action.triggered.connect(self.project_page.open_project_folder)
        open_folder_action.setEnabled(False)
        proj_file.addAction(open_folder_action)
        self.project_page.open_folder_action = open_folder_action

        recent_proj_menu = QMenu("Open Recent Project", self)
        recent_proj_menu.aboutToShow.connect(lambda: self.project_page.populate_recent_menu(recent_proj_menu))
        proj_file.addMenu(recent_proj_menu)

        proj_file.addSeparator()
        close_project_action = QAction("Close Project", self)
        close_project_action.triggered.connect(self.project_page.close_project)
        close_project_action.setEnabled(False)
        proj_file.addAction(close_project_action)
        self.project_page.close_project_action = close_project_action

        proj_file.addSeparator()
        standalone_action = QAction("Open Without Project...", self)
        standalone_action.triggered.connect(self.project_page.open_standalone)
        proj_file.addAction(standalone_action)

        proj_file.addSeparator()
        proj_prefs = QAction("Preferences...", self)
        proj_prefs.triggered.connect(self.project_page.open_preferences)
        proj_file.addAction(proj_prefs)

        proj_file.addSeparator()
        proj_quit = QAction("Quit", self)
        proj_quit.setShortcut(QKeySequence.StandardKey.Quit)
        proj_quit.triggered.connect(self.close)
        proj_file.addAction(proj_quit)

        project_menu = QMenu("Project", self)
        bar.addMenu(project_menu)
        add_p_action = QAction("Add Participant...", self)
        add_p_action.triggered.connect(self.project_page.add_participant)
        add_p_action.setEnabled(False)
        project_menu.addAction(add_p_action)
        self.project_page.add_p_action = add_p_action

        import_folder_action = QAction("Import Participants from Folder...", self)
        import_folder_action.triggered.connect(self.project_page.import_from_folder)
        import_folder_action.setEnabled(False)
        project_menu.addAction(import_folder_action)
        self.project_page.import_folder_action = import_folder_action

        import_bids_action = QAction("Import BIDS Dataset...", self)
        import_bids_action.triggered.connect(self.project_page.import_bids)
        project_menu.addAction(import_bids_action)
        self.project_page.import_bids_action = import_bids_action

        project_menu.addSeparator()
        rename_action = QAction("Rename Project...", self)
        rename_action.triggered.connect(self.project_page.rename_project)
        rename_action.setEnabled(False)
        project_menu.addAction(rename_action)
        self.project_page.rename_project_action = rename_action

        self.project_menus = [proj_file, project_menu]

        # ---- Prep menus ----
        prep_file = QMenu("File", self)
        bar.addMenu(prep_file)
        open_eeg = QAction("Open EEG File...", self)
        open_eeg.setShortcut(QKeySequence.StandardKey.Open)
        open_eeg.triggered.connect(lambda: self.prep_page and self.prep_page.files.open_file())
        prep_file.addAction(open_eeg)

        self.prep_recent_menu = QMenu("Open Recent", self)
        self.prep_recent_menu.aboutToShow.connect(self.refresh_prep_recent_menu)
        prep_file.addMenu(self.prep_recent_menu)

        close_file = QAction("Close File", self)
        close_file.setShortcut(QKeySequence.StandardKey.Close)
        close_file.triggered.connect(lambda: self.prep_page and self.prep_page.files.close_file())
        prep_file.addAction(close_file)

        prep_file.addSeparator()
        export_action = QAction("Export Processed...", self)
        export_action.triggered.connect(lambda checked: self.prep_page and self.prep_page.files.export_file())
        prep_file.addAction(export_action)

        prep_file.addSeparator()
        prep_prefs = QAction("Preferences...", self)
        prep_prefs.triggered.connect(lambda: self.prep_page and self.prep_page.open_preferences())
        prep_file.addAction(prep_prefs)

        prep_file.addSeparator()
        prep_quit = QAction("Quit", self)
        prep_quit.setShortcut(QKeySequence.StandardKey.Quit)
        prep_quit.triggered.connect(self.close)
        prep_file.addAction(prep_quit)

        pipeline_menu = QMenu("Pipeline", self)
        bar.addMenu(pipeline_menu)
        new_pl = QAction("New Pipeline", self)
        new_pl.setShortcut(QKeySequence.StandardKey.New)
        new_pl.triggered.connect(lambda: self.prep_page and self.prep_page.files.new_pipeline())
        pipeline_menu.addAction(new_pl)

        save_pl = QAction("Save Pipeline", self)
        save_pl.setShortcut(QKeySequence.StandardKey.Save)
        save_pl.triggered.connect(lambda: self.prep_page and self.prep_page.files.save_pipeline_default())
        pipeline_menu.addAction(save_pl)

        save_as_pl = QAction("Save Pipeline As...", self)
        save_as_pl.setShortcut(QKeySequence("Ctrl+Shift+S"))
        save_as_pl.triggered.connect(lambda: self.prep_page and self.prep_page.files.save_pipeline())
        pipeline_menu.addAction(save_as_pl)

        load_pl = QAction("Load Pipeline...", self)
        load_pl.triggered.connect(lambda: self.prep_page and self.prep_page.files.load_pipeline())
        pipeline_menu.addAction(load_pl)

        pipeline_menu.addSeparator()
        run_all = QAction("Run All", self)
        run_all.setShortcut(QKeySequence("Ctrl+Shift+Return"))
        run_all.triggered.connect(lambda: self.prep_page and self.prep_page.runner.run_all())
        pipeline_menu.addAction(run_all)

        self.prep_menus = [prep_file, pipeline_menu]

    def set_page_menus(self, page: str):
        """Show menus for the given page ('project' or 'prep'), hide the other group."""
        for m in self.project_menus:
            if (action := m.menuAction()) is not None:
                action.setVisible(page == "project")
        for m in self.prep_menus:
            if (action := m.menuAction()) is not None:
                action.setVisible(page == "prep")

    def refresh_prep_recent_menu(self):
        """Rebuild the prep recent-files menu from the current prep page state."""
        self.prep_recent_menu.clear()
        recent = self.prep_page.state.recent_fif if self.prep_page is not None else []
        if not recent:
            a = QAction("No recent files", self)
            a.setEnabled(False)
            self.prep_recent_menu.addAction(a)
            return
        for path in recent:
            act = QAction(path, self)
            act.triggered.connect(lambda _, p=path: self.prep_page and self.prep_page.files.load_data_path(p))
            self.prep_recent_menu.addAction(act)

    # -------- Page switching --------

    def show_project_page(self):
        """Switch to the project page."""
        self.stack.setCurrentIndex(STACK_PROJECT)
        self.set_page_menus("project")

    def show_preprocessing_page(self, ctx, nav_list: list):
        """Switch to (or replace) the preprocessing page for the given context."""
        # Tear down any existing prep page first
        if self.prep_page is not None:
            self.teardown_prep_page(report_status=True)

        page = PreprocessingPage(ctx, self.settings, nav_list, parent=self.stack)
        self.prep_page = page

        # Ensure slot 1 holds the new page
        if self.stack.count() > STACK_PREP:
            old = self.stack.widget(STACK_PREP)
            self.stack.removeWidget(old)
        self.stack.insertWidget(STACK_PREP, page)

        # Wire signals
        page.status_message.connect(lambda msg, t: self._status_bar.showMessage(msg, t))
        page.raw_info_changed.connect(self.raw_info_label.setText)
        page.title_change.connect(self.setWindowTitle)
        page.close_requested.connect(self.on_close_preprocessing)
        page.navigate_requested.connect(self.project_page.navigate_preprocessing)

        self.project_page.set_active_prep_page(page)

        self.stack.setCurrentIndex(STACK_PREP)
        self.set_page_menus("prep")

        page.auto_load()

    def teardown_prep_page(self, report_status: bool = True):
        """Clean up the current prep page; update project state if report_status."""
        if self.prep_page is None:
            return

        ctx = self.prep_page.project_context
        if report_status and ctx:
            try:
                self.project_page.on_preprocessing_closed(None, ctx)
            except Exception as e:
                logger.warning("on_preprocessing_closed failed: %s", e)

        self.project_page.clear_active_prep_page()
        self.prep_page.cleanup()
        self.prep_page.deleteLater()
        self.prep_page = None

    # -------- Signal handlers --------

    def on_open_preprocessing(self, ctx, nav_list: list):
        """Open or replace preprocessing page."""
        self.show_preprocessing_page(ctx, nav_list)

    def on_close_preprocessing(self):
        """Back button -> tear down prep page and return to project."""
        self.teardown_prep_page(report_status=True)
        self.raw_info_label.setText("")
        self.show_project_page()
        self.setWindowTitle(
            f"MNETAPE - {self.project_page.project.name}"
            if self.project_page.project
            else "MNETAPE"
        )

    def on_close_project(self):
        """Close Project -> tear down any open prep page, then reset project state."""
        if self.prep_page is not None:
            self.teardown_prep_page(report_status=False)
            self.raw_info_label.setText("")
        self.project_page.do_close_project()
        self.show_project_page()

    # -------- Window events --------

    def closeEvent(self, event):
        if self.prep_page is not None:
            if not self.prep_page.confirm_discard_if_dirty():
                event.ignore()
                return
            self.teardown_prep_page(report_status=True)
        self.settings.setValue("main_window/geometry", self.saveGeometry())
        super().closeEvent(event)

    def event(self, event) -> bool:
        from PyQt6.QtCore import QEvent
        from PyQt6.QtWidgets import QApplication
        if event is not None and event.type() == QEvent.Type.WindowActivate:
            modal = QApplication.activeModalWidget()
            if modal:
                modal.raise_()
                modal.activateWindow()
        return super().event(event)
