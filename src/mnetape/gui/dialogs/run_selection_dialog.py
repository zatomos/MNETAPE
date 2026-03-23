"""Dialog for selecting which runs to load when opening a multi-run session."""

from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
)
from PyQt6.QtCore import Qt


class RunSelectionDialog(QDialog):
    """Let the user pick a single run or concatenate all runs for a preprocessing session.

    Call get_selected_files() after exec() to retrieve the chosen list.
    A list with one item means a single run was selected.
    A list with multiple items means the user chose to concatenate.
    """

    def __init__(self, run_files: list[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Select Run")
        self.setFixedSize(480, 300)
        self._run_files = run_files
        self._result: list[str] = []

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        layout.addWidget(QLabel("This session has multiple runs. Select one to preprocess:"))

        self._list = QListWidget()
        self._list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        for f in run_files:
            self._list.addItem(QListWidgetItem(f))
        if self._list.count():
            self._list.setCurrentRow(0)
        self._list.itemDoubleClicked.connect(self.accept_single)
        layout.addWidget(self._list)

        buttons = QDialogButtonBox()
        btn_ok = buttons.addButton("Open Run", QDialogButtonBox.ButtonRole.AcceptRole)
        btn_ok.clicked.connect(self.accept_single)

        btn_concat = QPushButton("Concatenate All Runs")
        btn_concat.setToolTip("Load all runs as one continuous recording (requires matching channel layouts)")
        btn_concat.clicked.connect(self.accept_all)
        buttons.addButton(btn_concat, QDialogButtonBox.ButtonRole.ActionRole)

        buttons.rejected.connect(self.reject)
        btn_cancel = buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        layout.addWidget(buttons)

    def accept_single(self):
        row = self._list.currentRow()
        if row < 0:
            return
        self._result = [self._run_files[row]]
        self.accept()

    def accept_all(self):
        self._result = list(self._run_files)
        self.accept()

    def get_selected_files(self) -> list[str]:
        return self._result
