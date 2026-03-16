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

from gi.repository import Adw, Gio, Gtk

from mnetape.core.codegen import generate_full_script, parse_script_to_actions
from mnetape.core.data_io import load_raw_data, open_file_dialog_filter
from mnetape.gui.controllers.pipeline_runner import OperationCancelled
from mnetape.gui.dialogs.montage_dialog import MontageDialog

if TYPE_CHECKING:
    from mnetape.gui.controllers.main_window import MainWindow

logger = logging.getLogger(__name__)

def build_file_filter(name: str, patterns: list[str]) -> Gtk.FileFilter:
    f = Gtk.FileFilter()
    f.set_name(name)
    for pat in patterns:
        f.add_pattern(pat)
    return f

def build_filter_list(filters: list[Gtk.FileFilter]):
    store = Gio.ListStore.new(Gtk.FileFilter)
    for f in filters:
        store.append(f)
    return store

class FileHandler:
    """Handles all file I/O operations for the main window."""

    def __init__(self, window: MainWindow) -> None:
        self.export_data = None
        self.w = window
        self.state = window.state

    def close_file(self, _action=None, _param=None):
        """Close the loaded EEG file and reset state and UI."""
        self.state.raw_original = None
        self.state.data_states.clear()
        self.state.data_filepath = None

        for action in self.state.actions:
            action.reset()

        self.w.update_action_list()
        self.w.viz_panel.current_raw = None
        self.w.viz_panel.show_placeholder()
        self.w.set_status("File closed")

    def add_recent_file(self, path: str):
        """Add a file path to the head of the recent-files list and persist it."""
        if not path:
            return
        path = str(Path(path))
        if path in self.state.recent_fif:
            self.state.recent_fif.remove(path)
        self.state.recent_fif.insert(0, path)
        self.state.recent_fif = self.state.recent_fif[:10]
        self.state.settings.set_value("recent_fif", self.state.recent_fif)

    def load_data_path(self, path: str):
        """Load an EEG file from a known path without opening a dialog."""
        if not path:
            return

        filename = Path(path).name
        try:
            raw = self.w.runner.run_in_thread(
                lambda: load_raw_data(path, preload=True, verbose=False),
                f"Loading {filename}...",
            )
        except OperationCancelled:
            self.w.set_status("Load cancelled")
            return
        except Exception as e:
            logger.exception("Failed to load data file: %s", path)
            dlg = Adw.AlertDialog(heading="Error", body=f"Failed to load:\n{e}")
            dlg.add_response("ok", "OK")
            dlg.set_default_response("ok")
            dlg.present(self.w.window)
            self.w.set_status("Load failed")
            return

        self.state.raw_original = raw
        self.state.data_filepath = Path(path)
        self.state.data_states.clear()

        for action in self.state.actions:
            action.reset()

        self.w.update_action_list()
        self.w.update_visualization()

        self.w.set_status(f"Loaded {self.state.data_filepath.name}")
        self.check_montage(self.state.raw_original)
        self.add_recent_file(path)
        self.w.rebuild_recent_actions()
        logger.info("Loaded data file: %s", path)

    def check_montage(self, raw):
        """Prompt the user to set a montage if none is found in the raw object."""
        try:
            montage = raw.get_montage()
        except Exception as e:
            logger.warning("Could not get montage from raw: %s", e)
            montage = None

        dig = raw.info.get("dig")
        has_dig = bool(dig)
        has_montage = montage is not None and getattr(montage, "ch_names", None)

        if not has_montage and not has_dig:
            dialog = MontageDialog(raw, parent_window=self.w.window)
            dialog.exec()
            logger.warning("Loaded file without montage/digitization: %s", raw.filenames)

    def open_file(self, _action=None, _param=None):
        """Open a file-picker dialog and load the selected EEG file."""
        file_dialog = Gtk.FileDialog()
        file_dialog.set_title("Open EEG File")

        # Build filters from the filter string
        filter_str = open_file_dialog_filter()
        # Parse "Description (*.ext1 *.ext2);;..." format
        filters = []
        for part in filter_str.split(";;"):
            part = part.strip()
            if not part:
                continue
            if "(" in part:
                name_part, ext_part = part.split("(", 1)
                name_part = name_part.strip()
                ext_part = ext_part.rstrip(")").strip()
                patterns = [p.strip() for p in ext_part.split() if p.strip()]
            else:
                name_part = part
                patterns = ["*"]
            f = Gtk.FileFilter()
            f.set_name(name_part)
            for pat in patterns:
                f.add_pattern(pat)
            filters.append(f)

        if filters:
            file_dialog.set_filters(build_filter_list(filters))

        file_dialog.open(self.w.window, None, self.on_open_file_done)

    def on_open_file_done(self, file_dialog, result):
        try:
            gfile = file_dialog.open_finish(result)
            if gfile is not None:
                path = gfile.get_path()
                if path:
                    self.load_data_path(path)
        except Exception as e:
            if "dismissed" not in str(e).lower():
                logger.warning("File open dialog failed: %s", e)

    def export_file(self, row: int | None = None, _action=None, _param=None):
        """Export the last computed raw object to a FIF file chosen via dialog."""
        if row is None:
            if not self.state.data_states:
                dlg = Adw.AlertDialog(heading="No Data", body="Run the pipeline first.")
                dlg.add_response("ok", "OK")
                dlg.set_default_response("ok")
                dlg.present(self.w.window)
                return
            raw_to_export = self.state.data_states[-1]
        else:
            if row >= len(self.state.data_states):
                dlg = Adw.AlertDialog(
                    heading="No Data",
                    body="Selected action has not been computed yet.",
                )
                dlg.add_response("ok", "OK")
                dlg.set_default_response("ok")
                dlg.present(self.w.window)
                return
            raw_to_export = self.state.data_states[row]

        # Keep a reference for the callback
        self.export_data = raw_to_export

        file_dialog = Gtk.FileDialog()
        file_dialog.set_title("Export Processed")
        file_dialog.set_initial_name("processed.fif")

        fif_filter = build_file_filter("FIF Files", ["*.fif"])
        file_dialog.set_filters(build_filter_list([fif_filter]))

        file_dialog.save(self.w.window, None, self.on_export_done)

    def on_export_done(self, file_dialog, result):
        try:
            gfile = file_dialog.save_finish(result)
            if gfile is not None:
                path = gfile.get_path()
                if path:
                    if not path.endswith(".fif"):
                        path += ".fif"
                    try:
                        self.export_data.save(path, overwrite=True)
                        self.w.set_status(f"Exported: {Path(path).name}")
                        logger.info("Exported processed FIF: %s", path)
                    except Exception as e:
                        logger.exception("Export failed: %s", path)
                        dlg = Adw.AlertDialog(heading="Error", body=f"Export failed:\n{e}")
                        dlg.add_response("ok", "OK")
                        dlg.set_default_response("ok")
                        dlg.present(self.w.window)
        except Exception as e:
            if "dismissed" not in str(e).lower():
                logger.warning("Export dialog failed: %s", e)
        finally:
            self.export_data = None

    def new_pipeline(self, _action=None, _param=None):
        """Clear all actions and computed states to start a fresh pipeline."""
        self.state.actions = []
        self.state.data_states.clear()
        self.w.update_action_list()
        self.w.update_visualization()
        self.w.set_status("New pipeline")

    def save_pipeline(self, _action=None, _param=None):
        """Serialize the current action list to a Python script and save to disk."""
        file_dialog = Gtk.FileDialog()
        file_dialog.set_title("Save Pipeline")
        file_dialog.set_initial_name("pipeline.py")

        py_filter = build_file_filter("Python Files", ["*.py"])
        file_dialog.set_filters(build_filter_list([py_filter]))

        file_dialog.save(self.w.window, None, self.on_save_pipeline_done)

    def on_save_pipeline_done(self, file_dialog, result):
        try:
            gfile = file_dialog.save_finish(result)
            if gfile is not None:
                path = gfile.get_path()
                if path:
                    if not path.endswith(".py"):
                        path += ".py"
                    try:
                        code = generate_full_script(self.state.data_filepath, self.state.actions)
                        Path(path).write_text(code)
                    except Exception as exc:
                        logger.exception("Failed to save pipeline")
                        dlg = Adw.AlertDialog(
                            heading="Save Failed",
                            body=f"Could not save pipeline:\n{exc}",
                        )
                        dlg.add_response("ok", "OK")
                        dlg.set_default_response("ok")
                        dlg.present(self.w.window)
                        return

                    self.state.pipeline_filepath = Path(path)
                    self.w.code_panel.set_file(self.state.pipeline_filepath)
                    self.w.set_status(f"Saved: {self.state.pipeline_filepath.name}")
        except Exception as e:
            if "dismissed" not in str(e).lower():
                logger.warning("Save pipeline dialog failed: %s", e)

    def load_pipeline(self, _action=None, _param=None):
        """Open a Python pipeline script and parse it back into actions."""
        file_dialog = Gtk.FileDialog()
        file_dialog.set_title("Load Pipeline")

        py_filter = build_file_filter("Python Files", ["*.py"])
        file_dialog.set_filters(build_filter_list([py_filter]))

        file_dialog.open(self.w.window, None, self.on_load_pipeline_done)

    def on_load_pipeline_done(self, file_dialog, result):
        try:
            gfile = file_dialog.open_finish(result)
            if gfile is not None:
                path = gfile.get_path()
                if path:
                    try:
                        code = Path(path).read_text()
                        self.state.actions = parse_script_to_actions(code)
                        self.state.pipeline_filepath = Path(path)
                        self.w.code_panel.set_file(self.state.pipeline_filepath)
                        self.state.data_states.clear()
                        self.w.update_action_list()
                        self.w.update_visualization()
                        self.w.set_status(f"Loaded pipeline: {self.state.pipeline_filepath.name}")
                        logger.info("Loaded pipeline script: %s", path)
                    except Exception as e:
                        logger.exception("Failed to load pipeline: %s", path)
                        dlg = Adw.AlertDialog(heading="Error", body=f"Failed to load pipeline:\n{e}")
                        dlg.add_response("ok", "OK")
                        dlg.set_default_response("ok")
                        dlg.present(self.w.window)
        except Exception as e:
            if "dismissed" not in str(e).lower():
                logger.warning("Load pipeline dialog failed: %s", e)

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
            dlg = Adw.AlertDialog(
                heading="Reload Failed",
                body=f"Could not reload pipeline:\n{exc}\n\nThe current session state is unchanged.",
            )
            dlg.add_response("ok", "OK")
            dlg.set_default_response("ok")
            dlg.present(self.w.window)
            return
        self.state.actions = actions
        self.state.data_states.clear()
        self.w.update_action_list(sync_code=False)
        self.w.code_panel.set_code(code)
        self.w.set_status("Reloaded from file")

    def on_external_code_change(self):
        """Handle the pipeline file being modified externally."""
        self.reload_pipeline()
        self.w.set_status("Reloaded from disk")
        self.w.code_panel.pending_external_change = False
        logger.info("Auto-reloaded pipeline after external file change")
