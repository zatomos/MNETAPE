"""File and pipeline persistence handlers for the main window.

FileHandler groups all I/O operations: opening and closing EEG data files, managing the recent files list,
exporting processed data, saving/loading pipeline scripts, auto-saving edits to disk, and reloading when
the open file is modified externally.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from typing import TYPE_CHECKING

from PyQt6.QtWidgets import QFileDialog, QMessageBox

from mnetape.core.codegen import generate_full_script, parse_script_to_actions
from mnetape.core.data_io import load_raw_data, open_file_dialog_filter
from mnetape.gui.controllers.pipeline_runner import OperationCancelled

if TYPE_CHECKING:
    from mnetape.gui.controllers.main_window import MainWindow

logger = logging.getLogger(__name__)


class FileHandler:
    """Handles all file I/O operations for the main window.

    Operates on MainWindow.state and calls MainWindow update methods to keep the UI in sync after each operation.
    """

    def __init__(self, window: MainWindow) -> None:
        self.w = window
        self.state = window.state

    def close_file(self):
        """Close the loaded EEG file and reset state and UI to their initial conditions."""

        self.state.raw_original = None
        self.state.raw_states = []
        self.state.data_filepath = None

        for action in self.state.actions:
            action.reset()

        self.w.update_action_list()
        self.w.viz_panel.current_raw = None
        self.w.viz_panel.show_placeholder()
        self.w.viz_panel.status_label.setText("")
        self.w.status.showMessage("File closed")

    def add_recent_file(self, path: str):
        """Add a file path to the head of the recent-files list and persist it.

        Deduplicates and trims the list to 10 entries.

        Args:
            path: Absolute file path to record.
        """
        if not path:
            return
        path = str(Path(path))
        if path in self.state.recent_fif:
            self.state.recent_fif.remove(path)
        self.state.recent_fif.insert(0, path)
        self.state.recent_fif = self.state.recent_fif[:10]
        self.state.settings.setValue("recent_fif", self.state.recent_fif)

    def refresh_recent_menu(self):
        """Rebuild the Open Recent menu from the current recent files list."""
        self.w.recent_menu.clear()
        if not self.state.recent_fif:
            empty = self.w.recent_menu.addAction("No recent files")
            empty.setEnabled(False)
            return

        for path in self.state.recent_fif:
            act = self.w.recent_menu.addAction(path)
            act.triggered.connect(lambda _, p=path: self.load_data_path(p))

    def load_data_path(self, path: str):
        """Load an EEG file from a known path without opening a dialog.

        Resets action states and visualization, updates the status bar, and prompts for a montage if none is found
        in the file.

        Args:
            path: Absolute path to the EEG data file.
        """
        if not path:
            return

        filename = Path(path).name
        try:
            raw = self.w.runner.run_in_thread(
                lambda: load_raw_data(path, preload=True, verbose=False),
                f"Loading {filename}...",
            )
        except OperationCancelled:
            self.w.status.showMessage("Load cancelled")
            return
        except Exception as e:
            logger.exception("Failed to load data file: %s", path)
            QMessageBox.critical(self.w, "Error", f"Failed to load:\n{e}")
            self.w.status.showMessage("Load failed")
            return

        self.state.raw_original = raw
        self.state.data_filepath = Path(path)
        self.state.raw_states = []

        for action in self.state.actions:
            action.reset()

        self.w.update_action_list()
        self.w.update_visualization()

        info = self.state.raw_original.info
        self.w.status.showMessage(
            f"Loaded: {self.state.data_filepath.name} | "
            f"{len(info['ch_names'])} ch | "
            f"{info['sfreq']:.0f} Hz | "
            f"{self.state.raw_original.times[-1]:.1f}s"
        )
        self.check_montage(self.state.raw_original)
        self.add_recent_file(path)
        logger.info("Loaded data file: %s", path)

    def check_montage(self, raw):
        """Prompt the user to set a montage if none is found in the raw object.

        Opens MontageDialog when the raw object contains neither digitization points nor a channel montage
        with positions.

        Args:
            raw: The loaded MNE Raw object to inspect.
        """
        try:
            montage = raw.get_montage()
        except Exception as e:
            logger.warning("Could not get montage from raw: %s", e)
            montage = None

        dig = raw.info.get("dig")
        has_dig = bool(dig)
        has_montage = montage is not None and getattr(montage, "ch_names", None)

        if not has_montage and not has_dig:
            from mnetape.gui.dialogs.montage_dialog import MontageDialog

            dialog = MontageDialog(raw, parent=self.w)
            dialog.exec()
            logger.warning("Loaded file without montage/digitization: %s", raw.filenames)

    def open_file(self):
        """Open a file-picker dialog and load the selected EEG file."""
        path, _ = QFileDialog.getOpenFileName(
            self.w, "Open EEG File", "", open_file_dialog_filter()
        )
        if not path:
            return

        self.load_data_path(path)

    def export_file(self, row: int = None):
        """Export the last computed raw object to a FIF file chosen via dialog."""

        # Check for pipeline state
        if row is None:
            if not self.state.raw_states:
                QMessageBox.warning(self.w, "No Data", "Run the pipeline first.")
                return
            raw_to_export = self.state.raw_states[-1]
        else:
            print(row, len(self.state.raw_states))
            if row >= len(self.state.raw_states):
                QMessageBox.warning(self.w, "No Data", "Selected action has not been computed yet.")
                return
            raw_to_export = self.state.raw_states[row]

        path, _ = QFileDialog.getSaveFileName(self.w, "Export Processed", "", "FIF Files (*.fif)")
        if not path:
            return

        if not path.endswith(".fif"):
            path += ".fif"

        try:
            raw_to_export.save(path, overwrite=True)
            self.w.status.showMessage(f"Exported: {Path(path).name}")
            logger.info("Exported processed FIF: %s", path)
        except Exception as e:
            logger.exception("Export failed: %s", path)
            QMessageBox.critical(self.w, "Error", f"Export failed:\n{e}")

    def new_pipeline(self):
        """Clear all actions and computed states to start a fresh pipeline."""
        self.state.actions = []
        self.state.raw_states = []
        self.w.update_action_list()
        self.w.update_visualization()
        self.w.status.showMessage("New pipeline")

    def save_pipeline(self):
        """Serialize the current action list to a Python script and save to disk."""
        path, _ = QFileDialog.getSaveFileName(self.w, "Save Pipeline", "", "Python Files (*.py)")
        if not path:
            return

        if not path.endswith(".py"):
            path += ".py"

        try:
            code = generate_full_script(self.state.data_filepath, self.state.actions)
            Path(path).write_text(code)
        except Exception as exc:
            logger.exception("Failed to save pipeline")
            QMessageBox.critical(self.w, "Save Failed", f"Could not save pipeline:\n{exc}")
            return

        self.state.pipeline_filepath = Path(path)
        self.w.code_panel.set_file(self.state.pipeline_filepath)
        self.w.status.showMessage(f"Saved: {self.state.pipeline_filepath.name}")

    def load_pipeline(self):
        """Open a Python pipeline script and parse it back into actions."""
        path, _ = QFileDialog.getOpenFileName(self.w, "Load Pipeline", "", "Python Files (*.py)")
        if not path:
            return

        try:
            code = Path(path).read_text()
            self.state.actions = parse_script_to_actions(code)
            self.state.pipeline_filepath = Path(path)
            self.w.code_panel.set_file(self.state.pipeline_filepath)
            self.state.raw_states = []

            self.w.update_action_list()
            self.w.update_visualization()
            self.w.status.showMessage(f"Loaded pipeline: {self.state.pipeline_filepath.name}")
            logger.info("Loaded pipeline script: %s", path)
        except Exception as e:
            logger.exception("Failed to load pipeline: %s", path)
            QMessageBox.critical(self.w, "Error", f"Failed to load pipeline:\n{e}")

    def auto_save(self):
        """Write current editor code to disk if a pipeline file is open."""
        fp = self.state.pipeline_filepath
        if not fp or not fp.exists():
            return
        code = self.w.code_panel.get_code()
        fp.write_text(code)
        self.w.code_panel.file_hash = hashlib.md5(code.encode()).hexdigest()

    def reload_pipeline(self):
        """Reload the pipeline from the currently open file, discarding computed states."""
        if not self.state.pipeline_filepath or not self.state.pipeline_filepath.exists():
            return
        try:
            code = self.state.pipeline_filepath.read_text()
            actions = parse_script_to_actions(code)
        except Exception as exc:
            logger.exception("Failed to reload pipeline from %s", self.state.pipeline_filepath)
            QMessageBox.warning(
                self.w, "Reload Failed",
                f"Could not reload pipeline:\n{exc}\n\nThe current session state is unchanged.",
            )
            return
        self.state.actions = actions
        self.state.raw_states = []
        self.w.update_action_list(sync_code=False)
        self.w.code_panel.set_code(code)
        self.w.status.showMessage("Reloaded from file")

    def on_external_code_change(self):
        """Handle the pipeline file being modified externally."""
        self.reload_pipeline()
        self.w.status.showMessage("Reloaded from disk", 3000)
        self.w.code_panel.pending_external_change = False
        logger.info("Auto-reloaded pipeline after external file change")