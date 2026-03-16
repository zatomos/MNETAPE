"""Code panel for generated pipeline script viewing and editing.

CodePanel wraps a GtkSource.View editor with action-block background highlighting, a file watcher that reacts
to external edits, and callbacks that allow MainWindow to handle manual edits and external file changes.
"""

from __future__ import annotations

import hashlib
import logging
import re
from colorsys import hls_to_rgb
from pathlib import Path
from typing import Callable

from gi.repository import Gdk, Gio, GLib, Gtk

from mnetape.gui.widgets import create_code_editor

logger = logging.getLogger(__name__)

# GtkSource tag table limit
MAX_ACTION_MARKERS = 64

def action_name_color_rgba(name: str) -> tuple[float, float, float, float]:
    """Generate a deterministic, subtle dark background tint for an action name.

    Args:
        name: The action title string used as the color seed.

    Returns:
        A (r, g, b, a) tuple with values in [0, 1].
    """
    h = int(hashlib.md5(name.encode()).hexdigest()[:8], 16)
    hue = (h % 360) / 360.0
    r, g, b = hls_to_rgb(hue, 0.88, 0.35)
    return r, g, b, 0.1

class CodePanel(Gtk.Box):
    """Panel containing a GtkSource.View code editor for the pipeline script.

    Provides action-block background highlighting so each pipeline action's code
    is tinted with a unique color. Monitors the backing file for external edits via
    Gio.FileMonitor and invokes on_external_change when a change is detected.

    Attributes:
        editor: The editor widget.
        file_label: Label showing the name of the open pipeline file.
        current_file: Path of the file currently watched for external changes.
        file_hash: MD5 hex digest of the last written content.
        pending_external_change: Set to True when the watcher detects a new hash.
        internal_update: Set to True while CodePanel itself is updating the editor content.
        on_external_change: Optional callback invoked when the watched file changes on disk.
        on_manual_edit: Optional callback invoked with the new code string when user edits.
    """

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.set_hexpand(True)
        self.set_vexpand(True)

        # Toolbar row
        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        toolbar.set_margin_start(8)
        toolbar.set_margin_end(8)
        toolbar.set_margin_top(4)
        toolbar.set_margin_bottom(4)

        self.file_label = Gtk.Label(label="No file")
        self.file_label.add_css_class("dim-label")
        self.file_label.set_xalign(0.0)
        toolbar.append(self.file_label)

        self.append(toolbar)

        # Scrolled editor
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_hexpand(True)
        scrolled.set_vexpand(True)
        scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)

        self.editor = create_code_editor()
        scrolled.set_child(self.editor)
        self.append(scrolled)

        # Tag table for background highlighting
        self.tag_colors: dict[str, object] = {}  # action_name -> Gtk.TextTag
        self.next_tag_idx = 0

        # File watcher
        self.file_monitor: Gio.FileMonitor | None = None
        self.debounce_timer: int | None = None

        self.current_file: Path | None = None
        self.file_hash: str = ""
        self.pending_external_change = False
        self.internal_update = False

        self.on_external_change: Callable[[], None] | None = None
        self.on_manual_edit: Callable[[str], None] | None = None

        # Connect text changed signal
        self.buf = self.editor.get_buffer()
        self.buf.connect("changed", self.on_text_changed)

    # -------- Public API --------

    def set_file(self, filepath: Path):
        """Start watching filepath and load its contents into the editor.

        Args:
            filepath: Absolute path to the pipeline file to open and watch.
        """
        self.stop_watching()
        self.current_file = filepath
        self.file_label.set_text(filepath.name)

        if filepath.exists():
            self.start_watching(filepath)
            self.load_file()

    def set_code(self, code: str):
        """Replace editor content programmatically without triggering on_manual_edit.

        Args:
            code: Full Python source to display in the editor.
        """
        self.internal_update = True
        self.buf.set_text(code, -1)
        self.file_hash = hashlib.md5(code.encode()).hexdigest()
        self.internal_update = False
        self.highlight_action_blocks()

    def get_code(self) -> str:
        """Return the current editor content as a plain string."""
        start = self.buf.get_start_iter()
        end = self.buf.get_end_iter()
        return self.buf.get_text(start, end, True)

    def load_file(self):
        """Read current_file from disk and populate the editor, suppressing callbacks."""
        if self.current_file and self.current_file.exists():
            content = self.current_file.read_text()
            self.internal_update = True
            self.buf.set_text(content, -1)
            self.file_hash = hashlib.md5(content.encode()).hexdigest()
            self.internal_update = False

    # -------- Internal handlers --------

    def on_text_changed(self, _buf):
        """Handle editor text changes."""
        if self.internal_update:
            return
        self.highlight_action_blocks()
        if self.on_manual_edit:
            self.on_manual_edit(self.get_code())

    def start_watching(self, filepath: Path):
        """Set up a Gio.FileMonitor for the given filepath."""
        gfile = Gio.File.new_for_path(str(filepath))
        try:
            self.file_monitor = gfile.monitor_file(Gio.FileMonitorFlags.NONE, None)
            self.file_monitor.connect("changed", self.on_file_monitor_changed)
        except Exception as e:
            logger.warning("Could not set up file monitor: %s", e)

    def stop_watching(self):
        """Cancel the active file monitor if any."""
        if self.file_monitor is not None:
            self.file_monitor.cancel()
            self.file_monitor = None

    def on_file_monitor_changed(self, _monitor, _gfile, _other, event_type):
        """Respond to a file monitor change event."""
        if event_type not in (
            Gio.FileMonitorEvent.CHANGED,
            Gio.FileMonitorEvent.CHANGES_DONE_HINT,
        ):
            return

        if self.debounce_timer is not None:
            GLib.source_remove(self.debounce_timer)
        self.debounce_timer = GLib.timeout_add(100, self.check_external_change)

    def check_external_change(self) -> bool:
        """Check for a real hash change and invoke on_external_change if so."""
        self.debounce_timer = None
        if not self.current_file or not self.current_file.exists():
            return False  # Don't repeat
        try:
            new_content = self.current_file.read_text()
            new_hash = hashlib.md5(new_content.encode()).hexdigest()
            if new_hash != self.file_hash:
                self.pending_external_change = True
                if self.on_external_change:
                    self.on_external_change()
        except Exception as e:
            logger.warning("Failed to check external change: %s", e)
        return False  # Don't repeat

    # -------- Highlighting --------

    def get_tag_for_action(self, action_name: str):
        """Return the Gtk.TextTag for an action, allocating one if needed."""
        if action_name in self.tag_colors:
            return self.tag_colors[action_name]

        if self.next_tag_idx >= MAX_ACTION_MARKERS:
            return None

        r, g, b, a = action_name_color_rgba(action_name)

        tag_name = f"action_block_{self.next_tag_idx}"
        tag = self.buf.create_tag(
            tag_name,
            paragraph_background_rgba=create_rgba(r, g, b, a),
        )
        self.next_tag_idx += 1
        self.tag_colors[action_name] = tag
        return tag

    def highlight_action_blocks(self):
        """Scan the editor text and apply per-action background tags.

        Clears all existing action tags, then walks the pipeline section looking for "# [N] Title" comments
        and applies color tags to the corresponding lines.
        """
        # Remove all existing action tags
        start_buf = self.buf.get_start_iter()
        end_buf = self.buf.get_end_iter()
        for tag in self.tag_colors.values():
            self.buf.remove_tag(tag, start_buf, end_buf)

        text = self.get_code()
        lines = text.split("\n")

        header_re = re.compile(r"^#\s*\[(\d+)]\s*(.*?)\s*$")
        inline_end_re = re.compile(r"^#\s*--end--\s*$")

        i = 0
        in_pipeline = False
        while i < len(lines):
            stripped = lines[i].strip()
            if stripped == "# --- Pipeline ---":
                in_pipeline = True
                i += 1
                continue
            if not in_pipeline:
                i += 1
                continue

            match = header_re.match(stripped)
            if match:
                action_name = match.group(2).strip()
                tag = self.get_tag_for_action(action_name)
                start_line = i
                i += 1

                if i < len(lines) and lines[i].strip() == "# --inline--":
                    while i < len(lines) and not inline_end_re.match(lines[i].strip()):
                        i += 1
                    if i < len(lines):
                        i += 1  # include # --end--
                else:
                    while i < len(lines) and not lines[i].strip():
                        i += 1
                    if i < len(lines) and not header_re.match(lines[i].strip()):
                        i += 1  # include call-site line

                if tag is not None:
                    for line_num in range(start_line, i):
                        ok, line_start = self.buf.get_iter_at_line(line_num)
                        if line_num + 1 < self.buf.get_line_count():
                            ok, line_end = self.buf.get_iter_at_line(line_num + 1)
                        else:
                            line_end = self.buf.get_end_iter()
                        self.buf.apply_tag(tag, line_start, line_end)
            else:
                i += 1

def create_rgba(r: float, g: float, b: float, a: float):
    """Create a Gdk.RGBA from float components."""
    rgba = Gdk.RGBA()
    rgba.red = r
    rgba.green = g
    rgba.blue = b
    rgba.alpha = a
    return rgba
