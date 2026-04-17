"""Dialog for adding a participant to a project."""

import re

from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
)


class AddParticipantDialog(QDialog):
    """Collect a participant ID. Sessions and EEG files can be added afterwards."""

    def __init__(self, existing_ids: list[str], parent=None, **_kwargs):
        super().__init__(parent)
        self.setWindowTitle("Add Participant")
        self.setFixedSize(360, 140)
        self._existing_ids = [i.lower() for i in existing_ids]

        layout = QVBoxLayout(self)

        form = QFormLayout()
        form.setSpacing(10)

        self.id_edit = QLineEdit()
        self.id_edit.setPlaceholderText("e.g. sub-01")
        form.addRow("Participant ID:", self.id_edit)

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

    def _accept(self):
        pid = self.id_edit.text().strip()
        if not pid:
            self.error_label.setText("Please enter a participant ID.")
            return
        if re.search(r"[/\\]", pid):
            self.error_label.setText("Participant ID must not contain path separators.")
            return
        if pid.lower() in self._existing_ids:
            self.error_label.setText(f'A participant with ID "{pid}" already exists.')
            return
        self.error_label.setText("")
        self.accept()

    def get_id(self) -> str:
        return self.id_edit.text().strip()

    def get_session_id(self) -> str:
        return "01"

    def get_file(self) -> str | None:
        return None
