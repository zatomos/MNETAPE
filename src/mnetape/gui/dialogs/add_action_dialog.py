"""Dialog for selecting and adding a new pipeline action.

Lists all registered actions (except "custom") sorted alphabetically. The action description is shown below the list as
the selection changes. The chosen action_id is returned after the dialog is accepted.
"""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QSizePolicy,
    QVBoxLayout,
)

from mnetape.actions.registry import get_action_by_id, list_actions


class AddActionDialog(QDialog):
    """Modal dialog for picking an action type to add to the pipeline.

    Double-clicking an item or pressing OK accepts the dialog. The selected action's description is shown in a
    label below the list.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add Action")
        self.setFixedSize(350, 350)

        layout = QVBoxLayout(self)

        layout.addWidget(QLabel("Select action type:"))

        # List of available actions
        self.action_list = QListWidget()
        self.action_list.setObjectName("addActionList")
        for action_def in sorted(list_actions(), key=lambda a: a.title.lower()):
            if action_def.action_id == "custom":
                continue
            item = QListWidgetItem(action_def.title)
            item.setData(Qt.ItemDataRole.UserRole, action_def.action_id)
            self.action_list.addItem(item)
        self.action_list.setCurrentRow(0)
        self.action_list.itemDoubleClicked.connect(self.accept)
        layout.addWidget(self.action_list, 1)

        # Description label
        self.desc_label = QLabel()
        self.desc_label.setWordWrap(True)
        self.desc_label.setStyleSheet("color: gray;")
        self.desc_label.setFixedHeight(40)
        self.desc_label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.update_description()
        self.action_list.currentRowChanged.connect(self.update_description)
        layout.addWidget(self.desc_label)

        # OK/Cancel buttons
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def update_description(self):
        """Refresh the description label for the currently highlighted action."""
        item = self.action_list.currentItem()
        if item:
            action_id = item.data(Qt.ItemDataRole.UserRole)
            action_def = get_action_by_id(action_id)
            self.desc_label.setText(action_def.doc if action_def else "")

    def get_action_id(self) -> str | None:
        """Return the action_id of the currently selected list item.

        Returns:
            The action_id string, or None if nothing is selected.
        """
        item = self.action_list.currentItem()
        if item:
            return item.data(Qt.ItemDataRole.UserRole)
        return None
