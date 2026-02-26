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
from typing import TYPE_CHECKING

import threading
import time

import mne
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
)

if TYPE_CHECKING:
    from mne.io import BaseRaw

logger = logging.getLogger(__name__)

MONTAGE_FILE_FILTER = (
    "Montage files (*.loc *.locs *.elc *.sfp *.csd *.elp *.htps *.bvef);;"
    "All files (*)"
)


class AutoDetectDialog(QDialog):
    """Confirmation dialog shown after montage auto-detection completes.

    Displays the best match ratio and, when multiple montages tie, a combo box that lets the user choose among them.
    Also shows any EEG channels that could not be matched.

    Args:
        tied: List of (montage_name, ratio, matched_count, total_count) tuples
            with equal best ratio, sorted best-first.
        raw: The loaded MNE Raw object.
        parent: Optional parent widget.
    """

    def __init__(
        self,
        tied: list[tuple[str, float, int, int]],
        raw: BaseRaw,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Auto-Detect Result")
        self.tied = tied
        self.raw = raw

        _, ratio, matched, total = tied[0]
        layout = QVBoxLayout(self)

        # Montage selection + summary
        if len(tied) == 1:
            layout.addWidget(QLabel(f"Best match: <b>{tied[0][0]}</b>"))
        else:
            layout.addWidget(
                QLabel(f"{len(tied)} montages matched {matched}/{total} channels ({ratio:.1%}):")
            )
            self._combo = QComboBox()
            for name, *_ in tied:
                self._combo.addItem(name)
            self._combo.currentTextChanged.connect(self.update_unmatched)
            layout.addWidget(self._combo)

        info_text = f"Matched: {matched}/{total} channels ({ratio:.1%})"
        layout.addWidget(QLabel(info_text))

        # Show unmatched channels (if any)
        self.unmatched_label = QLabel()
        self.unmatched_label.setWordWrap(True)
        self.unmatched_label.setStyleSheet("color: gray;")
        layout.addWidget(self.unmatched_label)
        self.update_unmatched(self.selected_name())

        layout.addSpacing(8)
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        no_btn = QPushButton("No")
        no_btn.clicked.connect(self.reject)
        btn_row.addWidget(no_btn)
        yes_btn = QPushButton("Yes")
        yes_btn.setDefault(True)
        yes_btn.clicked.connect(self.accept)
        btn_row.addWidget(yes_btn)
        layout.addLayout(btn_row)

    def selected_name(self) -> str:
        """Return the montage name currently selected in the dialog.

        Returns:
            The single tied name when there is no combo box, or the combo's current text otherwise.
        """
        if len(self.tied) == 1:
            return self.tied[0][0]
        return self._combo.currentText()

    def update_unmatched(self, name: str):
        """Refresh the unmatched-channels label for a given montage name.

        Args:
            name: The standard montage name to compare against the raw's EEG channels.
        """
        eeg_picks = mne.pick_types(self.raw.info, eeg=True, exclude=[])
        eeg_names = {self.raw.ch_names[i] for i in eeg_picks}
        montage = mne.channels.make_standard_montage(name)
        unmatched = eeg_names - set(montage.ch_names)
        if unmatched:
            self.unmatched_label.setText(
                f"Unmatched channels: {', '.join(sorted(unmatched))}"
            )
        else:
            self.unmatched_label.setText("")


class MontageDialog(QDialog):
    """Dialog for resolving a missing EEG montage on file load.

    Offers three options:
        - Import: load a montage from a file (loc, sfp, elc, etc.).
        - Standard: select from MNE's built-in montage library.
        - Auto-detect: scan all built-in montages and select the best channel-name match.

    Applies the chosen montage directly to the raw object.

    Args:
        raw: The loaded MNE Raw object to apply the montage to.
        parent: Optional parent widget.
    """

    def __init__(self, raw: BaseRaw, parent=None):
        super().__init__(parent)
        self.raw = raw
        self._montage_path: str | None = None
        self.setWindowTitle("Missing Montage")
        self.setFixedSize(420, 250)
        layout = QVBoxLayout(self)

        layout.addWidget(QLabel("No montage/digitization found in the loaded file."))
        layout.addSpacing(8)

        # Option 1. Import file
        self.radio_import = QRadioButton("Import montage file")
        row_import = QHBoxLayout()
        row_import.addWidget(self.radio_import, 1)
        self.browse_btn = QPushButton("Browse...")
        self.browse_btn.setFixedWidth(90)
        self.browse_btn.clicked.connect(self.browse_file)
        row_import.addWidget(self.browse_btn)
        layout.addLayout(row_import)

        self.file_label = QLabel("")
        self.file_label.setStyleSheet("color: gray; margin-left: 24px;")
        layout.addWidget(self.file_label)

        # Option 2. Standard montage
        self.radio_standard = QRadioButton("Choose standard montage")
        row_standard = QHBoxLayout()
        row_standard.addWidget(self.radio_standard, 1)
        self.montage_combo = QComboBox()
        self.montage_combo.addItems(mne.channels.get_builtin_montages())
        self.montage_combo.setCurrentText("standard_1020")
        self.montage_combo.setFixedWidth(180)
        row_standard.addWidget(self.montage_combo)
        layout.addLayout(row_standard)

        # Option 3: Auto-detect
        self.radio_auto = QRadioButton("Auto-detect best match")
        layout.addWidget(self.radio_auto)

        # Group radio buttons
        self.group = QButtonGroup(self)
        self.group.addButton(self.radio_import, 0)
        self.group.addButton(self.radio_standard, 1)
        self.group.addButton(self.radio_auto, 2)
        self.radio_standard.setChecked(True)

        # Apply/Skip buttons
        layout.addSpacing(12)
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        skip_btn = QPushButton("Skip")
        skip_btn.clicked.connect(self.reject)
        btn_row.addWidget(skip_btn)
        apply_btn = QPushButton("Apply")
        apply_btn.setDefault(True)
        apply_btn.clicked.connect(self.apply)
        btn_row.addWidget(apply_btn)
        layout.addLayout(btn_row)

    def browse_file(self):
        """Open a file picker dialog and record the chosen montage file path."""
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Montage File", "", MONTAGE_FILE_FILTER
        )
        if path:
            self._montage_path = path
            self.file_label.setText(path.rsplit("/", 1)[-1])
            self.radio_import.setChecked(True)

    def apply(self):
        """Apply the selected montage option and close the dialog on success."""
        checked = self.group.checkedId()

        try:
            if checked == 0:  # Import file
                if not self._montage_path:
                    QMessageBox.warning(self, "No File", "Please select a montage file first.")
                    return
                montage = mne.channels.read_custom_montage(self._montage_path)
                self.raw.set_montage(montage, on_missing="warn")
                logger.info("Applied custom montage from %s", self._montage_path)

            elif checked == 1:  # Standard montage
                name = self.montage_combo.currentText()
                montage = mne.channels.make_standard_montage(name)
                self.raw.set_montage(montage, on_missing="warn")
                logger.info("Applied standard montage: %s", name)

            elif checked == 2:  # Auto-detect
                results = self.auto_detect_in_thread()
                if not results:
                    QMessageBox.information(
                        self, "Auto-Detect", "No matching montages found."
                    )
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
            QMessageBox.critical(self, "Error", f"Failed to apply montage:\n{exc}")
            return

        self.accept()

    def confirm_auto_detect(self, results: list[tuple[str, float, int, int]]) -> str | None:
        """Show confirmation dialog. Returns chosen montage name, or None if canceled."""
        best_ratio = results[0][1]
        tied = [r for r in results if r[1] == best_ratio]

        dlg = AutoDetectDialog(tied, self.raw, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            return dlg.selected_name()
        return None

    def auto_detect_in_thread(self) -> list[tuple[str, float, int, int]]:
        """Run auto_detect in a background thread."""
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

        progress = QProgressDialog("Scanning montages...", None, 0, 0, self)
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.show()

        while not finished.is_set():
            QApplication.processEvents()
            time.sleep(0.05)

        progress.close()

        if error[0]:
            raise error[0]

        return result

    def auto_detect(self) -> list[tuple[str, float, int, int]]:
        """Score all built-in MNE montages against the raw's EEG channel names.

        Returns:
            List of (name, ratio, matched_count, total_eeg_count) tuples,
            sorted by descending ratio then descending matched count.
            Empty list when there are no EEG channels or no matches.
        """
        eeg_picks = mne.pick_types(self.raw.info, eeg=True, exclude=[])
        eeg_names = {self.raw.ch_names[i] for i in eeg_picks}
        if not eeg_names:
            return []

        results: list[tuple[str, float, int, int]] = []     # (montage name, match ratio, matched count, total count)
        for name in mne.channels.get_builtin_montages():
            montage = mne.channels.make_standard_montage(name)
            matched = len(eeg_names & set(montage.ch_names))
            if matched > 0:
                results.append((name, matched / len(eeg_names), matched, len(eeg_names)))
        results.sort(key=lambda x: (-x[1], -x[2]))
        logger.info("Auto-detect montage results: %s", results)
        return results