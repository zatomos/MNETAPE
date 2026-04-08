"""Dialog for selecting and adding a new pipeline action.

Actions are grouped into categories. The action description is shown below the tree as the selection changes.
The chosen action_id is returned after the dialog is accepted.
"""

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QBrush, QColor, QFont
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QSizePolicy,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from mnetape.actions.registry import get_action_by_id, list_actions
from mnetape.core.models import CUSTOM_ACTION_ID

# Action IDs not listed here are collected into "Other".
CATEGORIES: dict[str, list[str]] = {
    "Preprocessing": ["filter", "notch", "resample", "crop", "reference", "normalize"],
    "Channels": ["set_channel_types", "drop_channels", "interpolate"],
    "Annotations & Events": ["set_annotations", "detect_events"],
    "Epochs": ["epoch_fixed", "epoch_events", "drop_bad_epochs", "average_epochs"],
    "ICA": ["ica_fit", "ica_apply"],
}


class AddActionDialog(QDialog):
    """Modal dialog for picking an action type to add to the pipeline.

    Actions are grouped into collapsible categories. Double-clicking an action or pressing OK accepts the dialog.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add Action")
        self.setFixedSize(360, 420)

        layout = QVBoxLayout(self)

        layout.addWidget(QLabel("Select action type:"))

        self.action_tree = QTreeWidget()
        self.action_tree.setObjectName("addActionList")
        self.action_tree.setHeaderHidden(True)
        self.action_tree.setRootIsDecorated(True)
        self.action_tree.setIndentation(16)
        layout.addWidget(self.action_tree, 1)

        # Build a lookup: action_id → action_def for all non-custom registered actions
        all_actions = {
            a.action_id: a
            for a in list_actions()
            if a.action_id != CUSTOM_ACTION_ID
        }

        # Place actions into categories; track which ones have been assigned
        assigned: set[str] = set()
        first_action_item: QTreeWidgetItem | None = None

        for category, action_ids in CATEGORIES.items():
            cat_item = self.make_category_item(category)
            valid_ids = [aid for aid in action_ids if aid in all_actions]
            if not valid_ids:
                continue
            self.action_tree.addTopLevelItem(cat_item)
            for action_id in valid_ids:
                action_def = all_actions[action_id]
                child = QTreeWidgetItem(cat_item, [action_def.title])
                child.setData(0, Qt.ItemDataRole.UserRole, action_id)
                if first_action_item is None:
                    first_action_item = child
                assigned.add(action_id)
            cat_item.setExpanded(True)

        # Uncategorized actions go in "Other"
        remaining = [a for aid, a in sorted(all_actions.items()) if aid not in assigned]
        if remaining:
            cat_item = self.make_category_item("Other")
            self.action_tree.addTopLevelItem(cat_item)
            for action_def in remaining:
                child = QTreeWidgetItem(cat_item, [action_def.title])
                child.setData(0, Qt.ItemDataRole.UserRole, action_def.action_id)
                if first_action_item is None:
                    first_action_item = child
            cat_item.setExpanded(True)

        if first_action_item is not None:
            self.action_tree.setCurrentItem(first_action_item)

        self.action_tree.itemDoubleClicked.connect(self.on_double_click)
        self.action_tree.currentItemChanged.connect(self.update_description)

        # Description label
        self.desc_label = QLabel()
        self.desc_label.setWordWrap(True)
        self.desc_label.setStyleSheet("color: gray;")
        self.desc_label.setFixedHeight(40)
        self.desc_label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.update_description()
        layout.addWidget(self.desc_label)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @staticmethod
    def make_category_item(label: str) -> QTreeWidgetItem:
        item = QTreeWidgetItem([label])
        item.setFlags(Qt.ItemFlag.ItemIsEnabled)  # not selectable
        font = QFont()
        font.setBold(True)
        item.setFont(0, font)
        item.setForeground(0, QBrush(QColor("#555555")))
        return item

    def on_double_click(self, item: QTreeWidgetItem, _column: int):
        if item.data(0, Qt.ItemDataRole.UserRole) is not None:
            self.accept()

    def update_description(self):
        """Refresh the description label for the currently highlighted action."""
        item = self.action_tree.currentItem()
        if item:
            action_id = item.data(0, Qt.ItemDataRole.UserRole)
            if action_id:
                action_def = get_action_by_id(action_id)
                self.desc_label.setText(action_def.doc if action_def else "")
                return
        self.desc_label.setText("")

    def get_action_id(self) -> str | None:
        """Return the action_id of the currently selected item, or None."""
        item = self.action_tree.currentItem()
        if item:
            return item.data(0, Qt.ItemDataRole.UserRole)
        return None
