"""Dialog for creating a new MNETAPE project."""

import re
from pathlib import Path

from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


def sanitize_name(name: str) -> str:
    """Convert a human-readable name to a filesystem-safe directory name."""
    s = name.lower().strip()
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"[\s_]+", "_", s)
    return s or "project"


class NewProjectDialog(QDialog):
    """Collect a project name and parent folder to create a new project.

    The project will be created as a subdirectory of the chosen parent folder,
    named after the sanitized project name.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("New Project")
        self.setFixedSize(500, 160)

        layout = QVBoxLayout(self)

        form = QFormLayout()
        form.setSpacing(10)

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("My EEG Study...")
        self.name_edit.textChanged.connect(self.update_path_preview)
        form.addRow("Project name:", self.name_edit)

        dir_row = QHBoxLayout()
        dir_row.setContentsMargins(0, 0, 0, 0)
        self.dir_edit = QLineEdit()
        self.dir_edit.setReadOnly(True)
        self.dir_edit.setPlaceholderText("Choose parent folder...")
        btn_browse = QPushButton("Browse...")
        btn_browse.setFixedWidth(90)
        btn_browse.clicked.connect(self.browse)
        dir_row.addWidget(self.dir_edit)
        dir_row.addWidget(btn_browse)

        dir_widget = QWidget()
        dir_widget.setLayout(dir_row)
        form.addRow("Parent folder:", dir_widget)

        layout.addLayout(form)

        # Path preview label
        self.path_preview = QLabel()
        self.path_preview.setWordWrap(True)
        self.path_preview.setStyleSheet("color: #3C7EDB; font-size: 11px;")
        layout.addWidget(self.path_preview)

        layout.addStretch()

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.parent_dir: Path | None = None

    def browse(self):
        path = QFileDialog.getExistingDirectory(self, "Select Parent Folder")
        if path:
            self.parent_dir = Path(path)
            self.dir_edit.setText(str(path))
            self.update_path_preview()

    def update_path_preview(self):
        name = self.name_edit.text().strip()
        if self.parent_dir and name:
            sanitized = sanitize_name(name)
            full_path = self.parent_dir / sanitized
            self.path_preview.setText(f"Project directory: {full_path}")
        else:
            self.path_preview.setText("")

    def _accept(self):
        if not self.name_edit.text().strip():
            QMessageBox.warning(self, "Missing name", "Please enter a project name.")
            return
        if not self.parent_dir:
            QMessageBox.warning(self, "Missing folder", "Please select a parent folder.")
            return
        self.accept()

    def get_name(self) -> str:
        return self.name_edit.text().strip()

    def get_project_dir(self) -> Path | None:
        """Return the full project directory (parent / sanitized_name)."""
        if not self.parent_dir:
            return None
        return self.parent_dir / sanitize_name(self.name_edit.text().strip())
