"""Set channel types action widgets.

Provides a table-based dialog that lets users change the channel type for any channel.
"""

from __future__ import annotations

import json
import logging

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
import mne

logger = logging.getLogger(__name__)

# Accepted channel types, fetched from MNE at import.
try:
    from mne._fiff.pick import get_channel_type_constants

    VALID_CHANNEL_TYPES: tuple[str, ...] = tuple(sorted(get_channel_type_constants()))
except Exception as e:
    logger.warning(f"Could not fetch valid channel types from MNE: {e}")

    VALID_CHANNEL_TYPES = (
        "bio", "chpi", "csd", "dbs", "dipole", "ecg", "ecog", "eeg", "emg",
        "eog", "exci", "eyegaze", "fnirs_cw_amplitude", "fnirs_fd_ac_amplitude",
        "fnirs_fd_phase", "fnirs_od", "gof", "grad", "gsr", "hbo", "hbr",
        "ias", "mag", "misc", "pupil", "ref_meg", "resp", "seeg", "stim",
        "syst", "temperature",
    )


class ChannelTypeDialog(QDialog):
    """Dialog for setting channel types via a filterable table with per-row dropdowns.

    Displays all channels with their current type and a combo box to select a new type.
    Only channels whose type was changed are included in the returned mapping.
    The table can be filtered by channel name or current type.

    Args:
        raw: The MNE Raw object providing channel names and current types.
        current_mapping: Pre-existing channel->type mapping to pre-populate the combos.
        parent: Optional parent widget.
    """

    def __init__(
        self,
        raw: mne.io.Raw,
        current_mapping: dict[str, str] | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self.raw = raw
        self._current_mapping = dict(current_mapping or {})
        self._combo_widgets: dict[str, QComboBox] = {}

        self.setWindowTitle("Set Channel Types")
        self.setMinimumSize(600, 500)

        layout = QVBoxLayout(self)

        info = QLabel(
            "Change the type for any channel using the dropdown. "
            "Only channels with a changed type will be included in the mapping."
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        # Filter row
        filter_layout = QHBoxLayout()
        filter_layout.addWidget(QLabel("Filter:"))
        self._filter_edit = QLineEdit()
        self._filter_edit.setPlaceholderText("Type to filter channels...")
        self._filter_edit.textChanged.connect(self.apply_filter)
        filter_layout.addWidget(self._filter_edit, 1)

        self._type_filter = QComboBox()
        self._type_filter.addItem("All types")
        # Populate with types actually present in the data
        present_types = sorted(set(mne.channel_type(raw.info, i) for i in range(len(raw.ch_names))))
        for t in present_types:
            self._type_filter.addItem(t)
        self._type_filter.currentTextChanged.connect(self.apply_filter)
        filter_layout.addWidget(self._type_filter)
        layout.addLayout(filter_layout)

        # Channel table
        self._table = QTableWidget()
        self._table.setColumnCount(3)
        self._table.setHorizontalHeaderLabels(["Channel", "Current Type", "New Type"])
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)
        layout.addWidget(self._table, 1)

        self.populate_table()

        # Summary + buttons
        footer = QHBoxLayout()
        self._summary_label = QLabel()
        self._summary_label.setStyleSheet("color: gray;")
        footer.addWidget(self._summary_label, 1)

        btn_reset = QPushButton("Reset All")
        btn_reset.clicked.connect(self._reset_all)
        footer.addWidget(btn_reset)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        footer.addWidget(buttons)
        layout.addLayout(footer)

        self.update_summary()

    def populate_table(self) -> None:
        """Populate the table with one row per channel, pre-selecting current or mapped types."""
        ch_names = self.raw.ch_names
        original_types = [mne.channel_type(self.raw.info, i) for i in range(len(ch_names))]

        self._table.setRowCount(len(ch_names))
        self._combo_widgets.clear()

        type_list = list(VALID_CHANNEL_TYPES)

        for row, (ch_name, orig_type) in enumerate(zip(ch_names, original_types)):
            # Channel name
            name_item = QTableWidgetItem(ch_name)
            name_item.setFlags(name_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self._table.setItem(row, 0, name_item)

            # Current type
            type_item = QTableWidgetItem(orig_type)
            type_item.setFlags(type_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self._table.setItem(row, 1, type_item)

            # New type dropdown
            combo = QComboBox()
            combo.addItems(type_list)

            # Set to mapped type if exists, otherwise current type
            target = self._current_mapping.get(ch_name, orig_type)
            idx = combo.findText(target)
            if idx >= 0:
                combo.setCurrentIndex(idx)
            else:
                # Type not in list, add it
                combo.insertItem(0, target)
                combo.setCurrentIndex(0)

            combo.currentTextChanged.connect(self.update_summary)
            self._table.setCellWidget(row, 2, combo)
            self._combo_widgets[ch_name] = combo

    def apply_filter(self) -> None:
        """Hide rows that do not match the current text filter and type-filter selection."""
        text = self._filter_edit.text().lower()
        type_filter = self._type_filter.currentText()

        for row in range(self._table.rowCount()):
            name_item = self._table.item(row, 0)
            type_item = self._table.item(row, 1)
            if name_item is None:
                continue

            name_match = not text or text in name_item.text().lower()
            type_match = type_filter == "All types" or type_item.text() == type_filter

            self._table.setRowHidden(row, not (name_match and type_match))

    def update_summary(self) -> None:
        """Refresh the summary label to show how many channels will be re-typed."""
        mapping = self.get_mapping()
        n = len(mapping)
        if n == 0:
            self._summary_label.setText("No changes.")
        else:
            self._summary_label.setText(f"{n} channel{'s' if n != 1 else ''} will be re-typed.")

    def _reset_all(self) -> None:
        """Reset all combo boxes to the channel's original type."""
        for row in range(self._table.rowCount()):
            type_item = self._table.item(row, 1)
            name_item = self._table.item(row, 0)
            if type_item is None or name_item is None:
                continue
            combo = self._combo_widgets.get(name_item.text())
            if combo is not None:
                idx = combo.findText(type_item.text())
                if idx >= 0:
                    combo.setCurrentIndex(idx)
        self.update_summary()

    def get_mapping(self) -> dict[str, str]:
        """Return a dict of channels whose type was changed from their original value.

        Returns:
            Dict mapping channel name to the new type string; empty if no changes.
        """
        mapping: dict[str, str] = {}
        for row in range(self._table.rowCount()):
            name_item = self._table.item(row, 0)
            type_item = self._table.item(row, 1)
            if name_item is None or type_item is None:
                continue
            ch_name = name_item.text()
            orig_type = type_item.text()
            combo = self._combo_widgets.get(ch_name)
            if combo is None:
                continue
            new_type = combo.currentText()
            if new_type != orig_type:
                mapping[ch_name] = new_type
        return mapping

    def get_mapping_string(self) -> str:
        """Return the mapping as a JSON string for the param field."""
        mapping = self.get_mapping()
        if not mapping:
            return ""
        return json.dumps(mapping)


# -------- Param widget factory --------

def channel_types_widget_factory(param_def, current_value, raw, parent):
    """Build a compound widget for the 'channel_types' param type.

    Returns a (container, value_widget) pair:
        - container is a QWidget with a read-only QLineEdit and an "Edit..." button that opens the ChannelTypeDialog.
        - value_widget is the QLineEdit where the JSON string representation of the mapping is stored.

    Args:
        param_def: Parameter metadata dict (unused here, kept for factory protocol).
        current_value: Current mapping as a JSON string or dict to pre-populate.
        raw: The MNE Raw object passed to ChannelTypeDialog. May be None.
        parent: Parent widget for the dialog.

    Returns:
        Tuple of (container QWidget, QLineEdit value widget).
    """
    container = QWidget()
    layout = QHBoxLayout(container)
    layout.setContentsMargins(0, 0, 0, 0)

    line_edit = QLineEdit(str(current_value or ""))
    line_edit.setReadOnly(True)
    line_edit.setPlaceholderText("Click 'Edit...' to set channel types")
    layout.addWidget(line_edit, 1)

    btn_edit = QPushButton("Edit...")
    btn_edit.setEnabled(raw is not None)

    def _edit():
        if raw is None:
            return
        current_text = line_edit.text().strip()
        current_mapping: dict[str, str] = {}
        if current_text:
            try:
                parsed = json.loads(current_text)
                if isinstance(parsed, dict):
                    current_mapping = parsed
            except (json.JSONDecodeError, ValueError):
                for pair in current_text.split(","):
                    pair = pair.strip()
                    if ":" in pair:
                        ch, typ = pair.split(":", 1)
                        current_mapping[ch.strip()] = typ.strip()
        dlg = ChannelTypeDialog(raw, current_mapping, parent)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            line_edit.setText(dlg.get_mapping_string())

    btn_edit.clicked.connect(_edit)
    layout.addWidget(btn_edit)

    return container, line_edit
