"""Dialogs for resolving a missing EEG channel montage on file load.

Exports:
    MontageDialog: Offers three options:
        - Import a montage file
        - Choose a standard MNE montage by name
        - Auto-detect the best match by comparing EEG channel names against all built-in montages
    AutoDetectDialog: Shown after auto-detection completes; lets the user confirm and, when multiple montages tie
        for best match, choose among them.
"""

from __future__ import annotations

import logging
import threading

import mne
from gi.repository import Adw, Gio, GLib, Gtk

from mnetape.gui.dialogs.base import ModalDialog

logger = logging.getLogger(__name__)

MONTAGE_FILE_FILTER_PATTERNS = [
    "*.loc", "*.locs", "*.elc", "*.sfp", "*.csd", "*.elp", "*.htps", "*.bvef", "*.bvct",
]

class AutoDetectDialog(ModalDialog):
    """Confirmation dialog shown after montage auto-detection completes.

    Args:
        tied: List of (montage_name, ratio, matched_count, total_count) tuples.
        raw: The loaded MNE Raw object.
        parent_window: Optional parent window.
    """

    def __init__(
        self,
        tied: list[tuple[str, float, int, int]],
        raw,
        parent_window=None,
    ):
        self.tied = tied
        self.raw = raw
        self.selected_name = tied[0][0] if tied else ""

        _, ratio, matched, total = tied[0]

        self.dialog = Adw.Dialog()
        self.dialog.set_title("Auto-Detect Result")
        self.dialog.set_content_width(380)

        toolbar_view = Adw.ToolbarView()
        toolbar_view.add_top_bar(Adw.HeaderBar())
        self.dialog.set_child(toolbar_view)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        content.set_margin_start(16)
        content.set_margin_end(16)
        content.set_margin_top(12)
        content.set_margin_bottom(12)
        toolbar_view.set_content(content)

        if len(tied) == 1:
            lbl = Gtk.Label(label=f"Best match: <b>{tied[0][0]}</b>")
            lbl.set_use_markup(True)
            lbl.set_xalign(0.0)
            content.append(lbl)
        else:
            lbl = Gtk.Label(label=f"{len(tied)} montages matched {matched}/{total} channels ({ratio:.1%}):")
            lbl.set_xalign(0.0)
            content.append(lbl)

            model = Gtk.StringList(strings=[name for name, *_ in tied])
            self.combo = Gtk.DropDown(model=model)
            self.combo.connect("notify::selected", self.on_combo_changed)
            content.append(self.combo)

        info_lbl = Gtk.Label(label=f"Matched: {matched}/{total} channels ({ratio:.1%})")
        info_lbl.set_xalign(0.0)
        content.append(info_lbl)

        self.unmatched_label = Gtk.Label(label="")
        self.unmatched_label.set_wrap(True)
        self.unmatched_label.add_css_class("dim-label")
        self.unmatched_label.set_xalign(0.0)
        content.append(self.unmatched_label)
        self.update_unmatched(self.selected_name)

        btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        btn_row.set_halign(Gtk.Align.END)
        btn_row.set_margin_top(8)

        no_btn = Gtk.Button(label="No")
        no_btn.connect("clicked", self.reject)
        btn_row.append(no_btn)

        yes_btn = Gtk.Button(label="Yes")
        yes_btn.add_css_class("suggested-action")
        yes_btn.connect("clicked", self.accept)
        btn_row.append(yes_btn)

        content.append(btn_row)

        self.setup_modal(parent_window)

    def on_combo_changed(self, dropdown, _pspec):
        idx = dropdown.get_selected()
        if 0 <= idx < len(self.tied):
            self.selected_name = self.tied[idx][0]
            self.update_unmatched(self.selected_name)

    def update_unmatched(self, name: str):
        eeg_picks = mne.pick_types(self.raw.info, eeg=True, exclude=[])
        eeg_names = {self.raw.ch_names[i] for i in eeg_picks}
        try:
            montage = mne.channels.make_standard_montage(name)
            unmatched = eeg_names - set(montage.ch_names)
            if unmatched:
                self.unmatched_label.set_text(f"Unmatched channels: {', '.join(sorted(unmatched))}")
            else:
                self.unmatched_label.set_text("")
        except Exception:
            self.unmatched_label.set_text("")

    def selected_name(self) -> str:
        return self.selected_name

class MontageDialog(ModalDialog):
    """Dialog for resolving a missing EEG montage on file load.

    Args:
        raw: The loaded MNE Raw object to apply the montage to.
        parent_window: Optional parent window.
    """

    def __init__(self, raw, parent_window=None):
        self.raw = raw
        self.montage_path: str | None = None

        self.dialog = Adw.Dialog()
        self.dialog.set_title("Missing Montage")
        self.dialog.set_content_width(440)

        toolbar_view = Adw.ToolbarView()
        toolbar_view.add_top_bar(Adw.HeaderBar())
        self.dialog.set_child(toolbar_view)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        content.set_margin_start(16)
        content.set_margin_end(16)
        content.set_margin_top(12)
        content.set_margin_bottom(12)
        toolbar_view.set_content(content)

        content.append(Gtk.Label(label="No montage/digitization found in the loaded file."))

        # Radio group
        self.radio_import = Gtk.CheckButton(label="Import montage file")
        self.radio_standard = Gtk.CheckButton(label="Choose standard montage")
        self.radio_standard.set_group(self.radio_import)
        self.radio_auto = Gtk.CheckButton(label="Auto-detect best match")
        self.radio_auto.set_group(self.radio_import)
        self.radio_standard.set_active(True)

        # Option 1: import file
        import_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        import_row.append(self.radio_import)
        self.browse_btn = Gtk.Button(label="Browse...")
        self.browse_btn.set_size_request(90, -1)
        self.browse_btn.connect("clicked", self.on_browse)
        import_row.append(self.browse_btn)
        content.append(import_row)

        self.file_label = Gtk.Label(label="")
        self.file_label.add_css_class("dim-label")
        self.file_label.set_xalign(0.0)
        self.file_label.set_margin_start(24)
        content.append(self.file_label)

        # Option 2: standard montage
        standard_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        standard_row.append(self.radio_standard)
        montage_names = mne.channels.get_builtin_montages()
        model = Gtk.StringList(strings=montage_names)
        self.montage_combo = Gtk.DropDown(model=model)
        self.montage_combo.set_size_request(200, -1)
        try:
            idx = montage_names.index("standard_1020")
            self.montage_combo.set_selected(idx)
        except ValueError:
            pass
        standard_row.append(self.montage_combo)
        content.append(standard_row)

        # Option 3: auto-detect
        content.append(self.radio_auto)

        # Buttons
        btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        btn_row.set_halign(Gtk.Align.END)
        btn_row.set_margin_top(12)

        skip_btn = Gtk.Button(label="Skip")
        skip_btn.connect("clicked", self.reject)
        btn_row.append(skip_btn)

        apply_btn = Gtk.Button(label="Apply")
        apply_btn.add_css_class("suggested-action")
        apply_btn.connect("clicked", self.on_apply)
        btn_row.append(apply_btn)

        content.append(btn_row)

        self.setup_modal(parent_window)

    def on_browse(self, _btn):
        file_dialog = Gtk.FileDialog()
        file_dialog.set_title("Select Montage File")

        filter_montage = Gtk.FileFilter()
        filter_montage.set_name("Montage files")
        for pat in MONTAGE_FILE_FILTER_PATTERNS:
            filter_montage.add_pattern(pat)

        filter_all = Gtk.FileFilter()
        filter_all.set_name("All files")
        filter_all.add_pattern("*")

        filters = gio_liststore_for_filters([filter_montage, filter_all])
        file_dialog.set_filters(filters)

        file_dialog.open(self.get_parent_gtk_window(), None, self.on_browse_done)

    def get_parent_gtk_window(self):
        """Return the parent Gtk.Window if available."""
        if self.parent_window is not None and isinstance(self.parent_window, Gtk.Window):
            return self.parent_window
        return None

    def on_browse_done(self, file_dialog, result):
        try:
            gfile = file_dialog.open_finish(result)
            if gfile is not None:
                path = gfile.get_path()
                if path:
                    self.montage_path = path
                    self.file_label.set_text(path.rsplit("/", 1)[-1])
                    self.radio_import.set_active(True)
        except Exception as e:
            logger.warning("Browse montage file failed: %s", e)

    def on_apply(self, _btn):
        try:
            if self.radio_import.get_active():
                if not self.montage_path:
                    self.show_error("No File", "Please select a montage file first.")
                    return
                if self.montage_path.lower().endswith(".bvct"):
                    montage = mne.channels.read_dig_captrak(self.montage_path)
                else:
                    montage = mne.channels.read_custom_montage(self.montage_path)
                self.raw.set_montage(montage, on_missing="warn")
                logger.info("Applied custom montage from %s", self.montage_path)

            elif self.radio_standard.get_active():
                idx = self.montage_combo.get_selected()
                names = mne.channels.get_builtin_montages()
                name = names[idx] if 0 <= idx < len(names) else "standard_1020"
                montage = mne.channels.make_standard_montage(name)
                self.raw.set_montage(montage, on_missing="warn")
                logger.info("Applied standard montage: %s", name)

            elif self.radio_auto.get_active():
                results = self.auto_detect_in_thread()
                if not results:
                    self.show_info("Auto-Detect", "No matching montages found.")
                    return
                best_name = self.confirm_auto_detect(results)
                if best_name is None:
                    return
                _, _, matched, total = next(r for r in results if r[0] == best_name)
                montage = mne.channels.make_standard_montage(best_name)
                self.raw.set_montage(montage, on_missing="warn")
                logger.info("Applied auto-detected montage: %s (%d/%d)", best_name, matched, total)

        except Exception as exc:
            logger.exception("Failed to apply montage")
            self.show_error("Error", f"Failed to apply montage:\n{exc}")
            return

        self.accept()

    def show_error(self, title: str, message: str):
        dlg = Adw.AlertDialog(heading=title, body=message)
        dlg.add_response("ok", "OK")
        dlg.set_default_response("ok")
        parent_win = self.get_parent_gtk_window()
        dlg.present(parent_win)

    def show_info(self, title: str, message: str):
        dlg = Adw.AlertDialog(heading=title, body=message)
        dlg.add_response("ok", "OK")
        dlg.set_default_response("ok")
        parent_win = (self.
                      get_parent_gtk_window())
        dlg.present(parent_win)

    def confirm_auto_detect(self, results: list[tuple[str, float, int, int]]) -> str | None:
        best_ratio = results[0][1]
        tied = [r for r in results if r[1] == best_ratio]

        dlg = AutoDetectDialog(tied, self.raw, parent_window=self.get_parent_gtk_window())
        if dlg.exec():
            return dlg.selected_name
        return None

    def auto_detect_in_thread(self) -> list[tuple[str, float, int, int]]:
        """Run auto_detect in a background thread with a progress dialog."""
        result: list = []
        error: list[BaseException | None] = [None]
        finished = threading.Event()

        def _worker():
            try:
                result.extend(self.auto_detect())
            except Exception as exc:
                error[0] = exc
            finally:
                finished.set()

        t = threading.Thread(target=_worker, daemon=True)
        t.start()

        # Show a simple progress indicator
        progress_dlg = Adw.AlertDialog(heading="Scanning montages...", body="Please wait.")
        spinner = Gtk.Spinner()
        spinner.start()
        progress_dlg.set_extra_child(spinner)
        parent_win = (self.
                      get_parent_gtk_window())
        progress_dlg.present(parent_win)

        # Poll until done
        loop = GLib.MainLoop()

        def _check():
            if finished.is_set():
                progress_dlg.close()
                loop.quit()
                return False
            return True

        GLib.timeout_add(50, _check)
        loop.run()

        if error[0]:
            raise error[0]

        return result

    def auto_detect(self) -> list[tuple[str, float, int, int]]:
        """Score all built-in MNE montages against the raw's EEG channel names."""
        eeg_picks = mne.pick_types(self.raw.info, eeg=True, exclude=[])
        eeg_names = {self.raw.ch_names[i] for i in eeg_picks}
        if not eeg_names:
            return []

        results: list[tuple[str, float, int, int]] = []
        for name in mne.channels.get_builtin_montages():
            montage = mne.channels.make_standard_montage(name)
            matched = len(eeg_names & set(montage.ch_names))
            if matched > 0:
                results.append((name, matched / len(eeg_names), matched, len(eeg_names)))
        results.sort(key=lambda x: (-x[1], -x[2]))
        logger.info("Auto-detect montage results: %s", results)
        return results

def gio_liststore_for_filters(filters: list) -> object:
    """Create a Gio.ListStore containing Gtk.FileFilter objects."""
    store = Gio.ListStore.new(Gtk.FileFilter)
    for f in filters:
        store.append(f)
    return store
