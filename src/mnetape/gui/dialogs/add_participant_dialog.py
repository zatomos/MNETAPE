"""Dialog for adding a participant to a project."""

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

from mnetape.core.data_io import open_file_dialog_filter


class AddParticipantDialog(QDialog):
    """Collect a participant ID, optional session ID, and optional EEG file path."""

    def __init__(self, existing_ids: list[str], project_dir: Path | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add Participant")
        self.setFixedSize(460, 220)
        self._existing_ids = [i.lower() for i in existing_ids]
        self._project_dir = project_dir

        layout = QVBoxLayout(self)

        form = QFormLayout()
        form.setSpacing(10)

        self.id_edit = QLineEdit()
        self.id_edit.setPlaceholderText("P01")
        form.addRow("Participant ID:", self.id_edit)

        self.session_edit = QLineEdit()
        self.session_edit.setPlaceholderText("e.g. 01")
        self.session_edit.setText("01")
        form.addRow("Session ID:", self.session_edit)

        file_row = QHBoxLayout()
        file_row.setContentsMargins(0, 0, 0, 0)
        self.file_edit = QLineEdit()
        self.file_edit.setReadOnly(True)
        self.file_edit.setPlaceholderText("Optional, can be set later")
        btn_browse = QPushButton("Browse...")
        btn_browse.setFixedWidth(80)
        btn_browse.clicked.connect(self.browse)
        file_row.addWidget(self.file_edit)
        file_row.addWidget(btn_browse)

        file_widget = QWidget()
        file_widget.setLayout(file_row)
        form.addRow("EEG File:", file_widget)

        layout.addLayout(form)

        self.error_label = QLabel()
        self.error_label.setStyleSheet("color: #C62828;")
        layout.addWidget(self.error_label)

        layout.addStretch()

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._file_path: str | None = None

    def browse(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select EEG File", "", open_file_dialog_filter()
        )
        if not path:
            return
        self._file_path = path
        # Store relative path if inside project dir
        if self._project_dir:
            try:
                rel = Path(path).relative_to(self._project_dir)
                self._file_path = str(rel)
            except ValueError:
                pass
        self.file_edit.setText(self._file_path)

    def _accept(self):
        pid = self.id_edit.text().strip()
        if not pid:
            self.error_label.setText("Please enter a participant ID.")
            return
        # Disallow slashes or path separators
        if re.search(r"[/\\]", pid):
            self.error_label.setText("Participant ID must not contain path separators.")
            return
        if pid.lower() in self._existing_ids:
            self.error_label.setText(f'A participant with ID "{pid}" already exists.')
            return
        sid = self.session_edit.text().strip()
        if not sid:
            self.error_label.setText("Please enter a session ID.")
            return
        if re.search(r"[/\\]", sid):
            self.error_label.setText("Session ID must not contain path separators.")
            return
        self.error_label.setText("")
        self.accept()

    def get_id(self) -> str:
        return self.id_edit.text().strip()

    def get_session_id(self) -> str:
        return self.session_edit.text().strip() or "01"

    def get_file(self) -> str | None:
        return self._file_path
