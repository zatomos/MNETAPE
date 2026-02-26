"""Code panel for generated pipeline script viewing and editing.

CodePanel wraps an editor with action-block background highlighting, a watcher that reacts to external edits,
and callbacks that allow MainWindow to handle manual edits and external file changes.
"""

import hashlib
import re
from colorsys import hls_to_rgb
from pathlib import Path
from typing import Callable

from PyQt6.QtCore import QFileSystemWatcher, QTimer
from PyQt6.QtGui import QColor
from PyQt6.Qsci import QsciScintilla
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from mnetape.gui.widgets import create_code_editor

# QDcintilla has a limit of 32 markers
MAX_ACTION_MARKERS = 32


def action_name_color(name: str) -> QColor:
    """Generate a deterministic, subtle dark background tint for an action name.

    Uses an MD5 hash of the name to pick a hue, then maps it to a dark, slightly saturated color.

    Args:
        name: The action title string used as the color seed.

    Returns:
        A QColor in dark-theme range.
    """

    h = int(hashlib.md5(name.encode()).hexdigest()[:8], 16)
    hue = (h % 360) / 360.0
    r, g, b = hls_to_rgb(hue, 0.14, 0.4)
    return QColor(int(r * 255), int(g * 255), int(b * 255))


class CodePanel(QWidget):
    """Panel containing a QScintilla code editor for the pipeline script.

    Provides action-block background highlighting so each pipeline action's code is tinted with a unique color.
    Monitors the backing file for external edits via QFileSystemWatcher and invokes on_external_change when a change is
    detected.

    Attributes:
        editor: The QsciScintilla editor widget.
        file_label: Label showing the name of the open pipeline file.
        current_file: Path of the file currently watched for external changes.
        file_hash: MD5 hex digest of the last written content, used to detect changes without false positives
            from filesystem events.
        pending_external_change: Set to True when the watcher detects a new hash; cleared by the caller after handling.
        internal_update: Set to True while CodePanel itself is updating the editor content, suppressing
            on_manual_edit callbacks.
        on_external_change: Optional callback invoked when the watched file changes on disk.
        on_manual_edit: Optional callback invoked with the new code string whenever the user edits the editor content.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        toolbar = QHBoxLayout()

        self.file_label = QLabel("No file")
        self.file_label.setStyleSheet("color: gray;")
        toolbar.addWidget(self.file_label)

        toolbar.addStretch()

        layout.addLayout(toolbar)

        self.editor = create_code_editor(self)
        self.editor.textChanged.connect(self.on_text_changed)
        layout.addWidget(self.editor)

        self.editor.setMarginWidth(1, 0)

        self.marker_colors: dict[str, int] = {}
        self.next_marker = 0

        # Watch the file for external changes
        self.watcher = QFileSystemWatcher()
        self.watcher.fileChanged.connect(self.on_file_changed)

        self.current_file: Path | None = None
        self.file_hash: str = ""
        self.pending_external_change = False
        self.internal_update = False

        self.on_external_change: Callable[[], None] | None = None
        self.on_manual_edit: Callable[[str], None] | None = None

    def set_file(self, filepath: Path):
        """Start watching filepath and load its contents into the editor.

        Removes the previously watched path (if any) before adding the new one.

        Args:
            filepath: Absolute path to the pipeline file to open and watch.
        """
        if self.current_file and str(self.current_file) in self.watcher.files():
            self.watcher.removePath(str(self.current_file))

        self.current_file = filepath
        self.file_label.setText(filepath.name)

        if filepath.exists():
            self.watcher.addPath(str(filepath))
            self.load_file()

    def set_code(self, code: str):
        """Replace editor content programmatically without triggering on_manual_edit.

        Args:
            code: Full Python source to display in the editor.
        """
        self.internal_update = True
        self.editor.setText(code)
        self.file_hash = hashlib.md5(code.encode()).hexdigest()
        self.internal_update = False
        self.highlight_action_blocks()

    def get_code(self) -> str:
        """Return the current editor content as a plain string."""
        return self.editor.text()

    def load_file(self):
        """Read current_file from disk and populate the editor, suppressing callbacks."""
        if self.current_file and self.current_file.exists():
            content = self.current_file.read_text()
            self.internal_update = True
            self.editor.setText(content)
            self.file_hash = hashlib.md5(content.encode()).hexdigest()
            self.internal_update = False

    def on_text_changed(self):
        """Handle editor text changes, ignoring changes made programmatically."""
        if self.internal_update:
            return
        self.highlight_action_blocks()
        if self.on_manual_edit:
            self.on_manual_edit(self.get_code())

    def get_marker_for_action(self, action_name: str) -> int:
        """Return the QScintilla marker ID for an action, allocating one if needed.

        QScintilla supports at most MAX_ACTION_MARKERS distinct markers.
        When that limit is reached, -1 is returned and no highlighting is applied.

        Args:
            action_name: The action title string used to identify the marker.

        Returns:
            The marker ID, or -1 if the limit has been exceeded.
        """
        if action_name in self.marker_colors:
            return self.marker_colors[action_name]

        # If we exceed the max markers, skip highlighting
        if self.next_marker >= MAX_ACTION_MARKERS:
            return -1

        # Assign a new marker ID for this action
        marker_id = self.next_marker
        self.next_marker += 1
        self.marker_colors[action_name] = marker_id

        self.editor.markerDefine(QsciScintilla.MarkerSymbol.Background, marker_id)
        self.editor.setMarkerBackgroundColor(action_name_color(action_name), marker_id)
        return marker_id

    def highlight_action_blocks(self):
        """Scan the editor text and apply per-action background markers.

        Clears all existing markers, then walks the editor lines looking for "# In[N] Title" / "# End[N]" pairs.
        Lines inside each pair are highlighted with the color assigned to that action's title.
        """
        for marker_id in range(MAX_ACTION_MARKERS):
            self.editor.markerDeleteAll(marker_id)

        text = self.editor.text()
        lines = text.split("\n")

        header_re = re.compile(r"^#\s*In\[\d+]\s*(.*?)\s*$")
        footer_re = re.compile(r"^#\s*End\[\d+]\s*$")

        i = 0
        while i < len(lines):
            match = header_re.match(lines[i].strip())
            if match:
                action_name = match.group(1).strip()
                marker_id = self.get_marker_for_action(action_name)
                start_line = i

                end_line = i
                i += 1
                while i < len(lines):
                    if footer_re.match(lines[i].strip()):
                        end_line = i
                        i += 1
                        break
                    if header_re.match(lines[i].strip()):
                        end_line = i - 1
                        break
                    end_line = i
                    i += 1

                for line_num in range(start_line, end_line + 1):
                    self.editor.markerAdd(line_num, marker_id)
            else:
                i += 1

    def on_file_changed(self, path: str):
        """Respond to a QFileSystemWatcher notification for the watched file.

        Re-adds the path to the watcher after a short debounce delay.
        When the content hash differs from the last known hash, pending_external_change is set and
        on_external_change is called.

        Args:
            path: The file-system path that triggered the change event.
        """
        if not self.current_file:
            return

        # Debounce rapid file change events by waiting a short time before re-adding the path to the watcher
        QTimer.singleShot(100, lambda: self.watcher.addPath(path))

        # Check for file changes and update the editor if needed
        if self.current_file.exists():
            new_content = self.current_file.read_text()
            new_hash = hashlib.md5(new_content.encode()).hexdigest()

            if new_hash != self.file_hash:
                self.pending_external_change = True
                if self.on_external_change:
                    self.on_external_change()
