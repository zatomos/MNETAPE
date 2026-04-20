"""Widgets and dialogs for the set_montage action.

Dialogs:
    ChannelRemapDialog: Lets the user remap EEG channels not found in a montage.
    AutoDetectDialog: Confirms the best auto-detected montage match.
    MontageDialog: Three-option dialog (import file / standard / auto-detect).

Param widgets (via WIDGET_BINDINGS):
    MontageConfigWidget: Inline radio-button compound widget for ActionEditor.
"""

from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING

import mne
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

if TYPE_CHECKING:
    from mne.io import BaseRaw

from mnetape.actions.base import ParamWidgetBinding

MONTAGE_FILE_FILTER = (
    "Montage files (*.loc *.locs *.elc *.sfp *.csd *.elp *.htps *.bvef *.bvct);;"
    "All files (*)"
)


# ── Dialogs ─────────────────────────────────────────────────────────────────


class ChannelRemapDialog(QDialog):
    """Dialog for remapping EEG channels not found in the selected montage."""

    def __init__(self, unmatched: list[str], montage_ch_names: list[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Remap Unmatched Channels")
        self.setMinimumWidth(420)
        self._combos: dict[str, QComboBox] = {}

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            f"{len(unmatched)} EEG channel(s) were not found in the montage.\n"
            "Optionally remap them to a known channel name, or leave them unpositioned."
        ))

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll_content = QWidget()
        scroll_layout = QVBoxLayout(scroll_content)
        scroll_layout.setSpacing(4)

        sorted_montage = sorted(montage_ch_names)
        for ch in sorted(unmatched):
            row = QHBoxLayout()
            row.addWidget(QLabel(ch), 1)
            combo = QComboBox()
            combo.addItem("keep as-is", userData=None)
            for m in sorted_montage:
                combo.addItem(m, userData=m)
            combo.setFixedWidth(200)
            self._combos[ch] = combo
            row.addWidget(combo)
            scroll_layout.addLayout(row)

        scroll_layout.addStretch()
        scroll.setWidget(scroll_content)
        scroll.setMaximumHeight(300)
        layout.addWidget(scroll)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        ok_btn = QPushButton("OK")
        ok_btn.setDefault(True)
        ok_btn.clicked.connect(self.accept)
        btn_row.addWidget(ok_btn)
        layout.addLayout(btn_row)

    def get_renames(self) -> dict[str, str]:
        return {ch: combo.currentData() for ch, combo in self._combos.items()
                if combo.currentData() is not None}


class AutoDetectDialog(QDialog):
    """Confirmation dialog shown after montage auto-detection completes."""

    def __init__(self, tied: list[tuple[str, float, int, int]], raw: BaseRaw, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Auto-Detect Result")
        self.tied = tied
        self.raw = raw

        _, ratio, matched, total = tied[0]
        layout = QVBoxLayout(self)

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

        layout.addWidget(QLabel(f"Matched: {matched}/{total} channels ({ratio:.1%})"))

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
        if len(self.tied) == 1:
            return self.tied[0][0]
        return self._combo.currentText()

    def update_unmatched(self, name: str):
        eeg_picks = mne.pick_types(self.raw.info, eeg=True, exclude=[])
        eeg_upper = {self.raw.ch_names[i].upper() for i in eeg_picks}
        montage = mne.channels.make_standard_montage(name)
        montage_upper = {n.upper() for n in montage.ch_names}
        unmatched = eeg_upper - montage_upper
        if unmatched:
            self.unmatched_label.setText(f"Unmatched channels: {', '.join(sorted(unmatched))}")
        else:
            self.unmatched_label.setText("")


class MontageDialog(QDialog):
    """Dialog for resolving a missing EEG montage on file load.

    Offers three options: import file, choose standard montage, auto-detect.
    Applies the chosen montage directly to the raw object.
    """

    def __init__(self, raw: BaseRaw, parent=None):
        super().__init__(parent)
        self.raw = raw
        self._montage_path: str | None = None
        self._applied_info: dict | None = None
        self.setWindowTitle("Set Montage")
        self.setFixedSize(420, 250)
        layout = QVBoxLayout(self)

        layout.addWidget(QLabel("No montage/digitization found in the loaded file."))
        layout.addSpacing(8)

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

        self.radio_standard = QRadioButton("Choose standard montage")
        row_standard = QHBoxLayout()
        row_standard.addWidget(self.radio_standard, 1)
        self.montage_combo = QComboBox()
        self.montage_combo.addItems(mne.channels.get_builtin_montages())
        self.montage_combo.setCurrentText("standard_1020")
        self.montage_combo.setFixedWidth(180)
        row_standard.addWidget(self.montage_combo)
        layout.addLayout(row_standard)

        self.radio_auto = QRadioButton("Auto-detect best match")
        layout.addWidget(self.radio_auto)

        self.group = QButtonGroup(self)
        self.group.addButton(self.radio_import, 0)
        self.group.addButton(self.radio_standard, 1)
        self.group.addButton(self.radio_auto, 2)
        self.radio_standard.setChecked(True)

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
        path, _ = QFileDialog.getOpenFileName(self, "Select Montage File", "", MONTAGE_FILE_FILTER)
        if path:
            self._montage_path = path
            self.file_label.setText(path.rsplit("/", 1)[-1])
            self.radio_import.setChecked(True)

    def apply(self):
        checked = self.group.checkedId()
        try:
            if checked == 0:
                if not self._montage_path:
                    QMessageBox.warning(self, "No File", "Please select a montage file first.")
                    return
                if self._montage_path.lower().endswith(".bvct"):
                    montage = mne.channels.read_dig_captrak(self._montage_path)
                else:
                    montage = mne.channels.read_custom_montage(self._montage_path)
                montage_info: dict = {"type": "file", "path": self._montage_path}

            elif checked == 1:
                name = self.montage_combo.currentText()
                montage = mne.channels.make_standard_montage(name)
                montage_info = {"type": "standard", "name": name}

            elif checked == 2:
                results = self._auto_detect_in_thread()
                if not results:
                    QMessageBox.information(self, "Auto-Detect", "No matching montages found.")
                    return
                best_name = self._confirm_auto_detect(results)
                if best_name is None:
                    return
                montage = mne.channels.make_standard_montage(best_name)
                montage_info = {"type": "standard", "name": best_name}
            else:
                return

            renames = self._get_renames_for_montage(montage)
            if renames is None:
                return
            montage_info["renames"] = renames
            if renames:
                self.raw.rename_channels(renames)
            self.raw.set_montage(montage, on_missing="warn")
            self._applied_info = montage_info

        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Failed to apply montage:\n{exc}")
            return

        self.accept()

    def _get_renames_for_montage(self, montage) -> dict | None:
        eeg_picks = mne.pick_types(self.raw.info, eeg=True, exclude=[])
        eeg_names = [self.raw.ch_names[i] for i in eeg_picks]
        montage_upper = {n.upper() for n in montage.ch_names}
        unmatched = [ch for ch in eeg_names if ch.upper() not in montage_upper]
        if not unmatched:
            return {}
        dlg = ChannelRemapDialog(unmatched, montage.ch_names, parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return None
        return dlg.get_renames()

    def get_applied_info(self) -> dict | None:
        return self._applied_info

    def _confirm_auto_detect(self, results) -> str | None:
        best_ratio = results[0][1]
        tied = [r for r in results if r[1] == best_ratio]
        dlg = AutoDetectDialog(tied, self.raw, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            return dlg.selected_name()
        return None

    def _auto_detect_in_thread(self) -> list:
        result: list = []
        error: list = [None]
        finished = threading.Event()

        def _worker():
            try:
                result.extend(self._auto_detect())
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

    def _auto_detect(self) -> list:
        eeg_picks = mne.pick_types(self.raw.info, eeg=True, exclude=[])
        eeg_upper = {self.raw.ch_names[i].upper() for i in eeg_picks}
        if not eeg_upper:
            return []
        results = []
        for name in mne.channels.get_builtin_montages():
            montage = mne.channels.make_standard_montage(name)
            montage_upper = {n.upper() for n in montage.ch_names}
            matched = len(eeg_upper & montage_upper)
            if matched > 0:
                results.append((name, matched / len(eeg_upper), matched, len(eeg_upper)))
        results.sort(key=lambda x: (-x[1], -x[2]))
        return results


# ── Inline param widget ──────────────────────────────────────────────────────

# Shared cache: maps id(ActionEditor) -> MontageConfigWidget
_compound_cache: dict = {}


class _ValueProxy(QWidget):
    """Zero-height invisible QWidget whose get_value() delegates to a getter function."""

    def __init__(self, getter_fn, parent=None):
        super().__init__(parent)
        self.setVisible(False)
        self.setMaximumHeight(0)
        self._getter = getter_fn

    def get_value(self):
        return self._getter()


class MontageConfigWidget(QWidget):
    """Inline compound widget for montage selection with 3 radio-button options."""

    def __init__(self, current_name: str, raw, parent=None):
        super().__init__(parent)
        self._raw = raw
        self._renames: dict | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.setSpacing(4)

        self.radio_file = QRadioButton("Import montage file")
        layout.addWidget(self.radio_file)

        file_row = QHBoxLayout()
        file_row.setContentsMargins(20, 0, 0, 0)
        self.file_edit = QLineEdit()
        self.file_edit.setPlaceholderText("path to montage file…")
        self.btn_browse = QPushButton("Browse…")
        self.btn_browse.setFixedWidth(80)
        self.btn_browse.clicked.connect(self._browse)
        file_row.addWidget(self.file_edit, 1)
        file_row.addWidget(self.btn_browse)
        layout.addLayout(file_row)

        self.radio_standard = QRadioButton("Choose standard montage")
        layout.addWidget(self.radio_standard)

        std_row = QHBoxLayout()
        std_row.setContentsMargins(20, 0, 0, 0)
        self.montage_combo = QComboBox()
        self.montage_combo.setEditable(True)
        self.montage_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.montage_combo.addItems(mne.channels.get_builtin_montages())
        self.montage_combo.setFixedWidth(200)
        std_row.addWidget(self.montage_combo)
        std_row.addStretch()
        layout.addLayout(std_row)

        self.radio_auto = QRadioButton("Auto-detect best match")
        layout.addWidget(self.radio_auto)

        auto_row = QHBoxLayout()
        auto_row.setContentsMargins(20, 0, 0, 0)
        self.detect_label = QLabel("")
        self.detect_label.setStyleSheet("color: gray;")
        self.btn_detect = QPushButton("Run Auto-Detect")
        self.btn_detect.setEnabled(raw is not None)
        self.btn_detect.clicked.connect(self._run_auto_detect)
        auto_row.addWidget(self.btn_detect)
        auto_row.addWidget(self.detect_label, 1)
        layout.addLayout(auto_row)

        self.group = QButtonGroup(self)
        self.group.addButton(self.radio_file, 0)
        self.group.addButton(self.radio_standard, 1)
        self.group.addButton(self.radio_auto, 2)

        self.radio_standard.setChecked(True)
        name = current_name or "standard_1020"
        idx = self.montage_combo.findText(name)
        if idx >= 0:
            self.montage_combo.setCurrentIndex(idx)
        else:
            self.montage_combo.setCurrentText(name)

    def set_montage_file(self, path: str):
        if path:
            self.file_edit.setText(path)
            self.radio_file.setChecked(True)

    def set_renames(self, renames):
        self._renames = renames or None

    def get_value(self) -> str:
        if self.radio_file.isChecked():
            return ""
        return self.montage_combo.currentText()

    def get_montage_file(self) -> str:
        if self.radio_file.isChecked():
            return self.file_edit.text()
        return ""

    def get_renames(self) -> dict | None:
        return self._renames or None

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Montage File", self.file_edit.text() or "", MONTAGE_FILE_FILTER
        )
        if path:
            self.file_edit.setText(path)
            self.radio_file.setChecked(True)

    def _run_auto_detect(self):
        if self._raw is None:
            return

        results: list = []
        error: list = [None]
        finished = threading.Event()

        def _worker():
            try:
                eeg_picks = mne.pick_types(self._raw.info, eeg=True, exclude=[])
                eeg_upper = {self._raw.ch_names[i].upper() for i in eeg_picks}
                if not eeg_upper:
                    finished.set()
                    return
                for name in mne.channels.get_builtin_montages():
                    montage = mne.channels.make_standard_montage(name)
                    montage_upper = {n.upper() for n in montage.ch_names}
                    matched = len(eeg_upper & montage_upper)
                    if matched > 0:
                        results.append((name, matched / len(eeg_upper), matched, len(eeg_upper)))
                results.sort(key=lambda x: (-x[1], -x[2]))
            except Exception as exc:
                error[0] = exc
            finally:
                finished.set()

        t = threading.Thread(target=_worker, daemon=True)
        t.start()

        progress = QProgressDialog("Scanning montages…", None, 0, 0, self)
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.show()
        while not finished.is_set():
            QApplication.processEvents()
            time.sleep(0.05)
        progress.close()

        if error[0]:
            QMessageBox.critical(self, "Error", f"Auto-detect failed:\n{error[0]}")
            return
        if not results:
            QMessageBox.information(self, "Auto-Detect", "No matching montages found.")
            return

        best_ratio = results[0][1]
        tied = [r for r in results if r[1] == best_ratio]
        dlg = AutoDetectDialog(tied, self._raw, parent=self)
        if not dlg.exec():
            return

        name = dlg.selected_name()
        montage = mne.channels.make_standard_montage(name)
        eeg_picks = mne.pick_types(self._raw.info, eeg=True, exclude=[])
        eeg_names = [self._raw.ch_names[i] for i in eeg_picks]
        montage_upper = {n.upper() for n in montage.ch_names}
        unmatched = [ch for ch in eeg_names if ch.upper() not in montage_upper]

        if unmatched:
            remap_dlg = ChannelRemapDialog(unmatched, montage.ch_names, parent=self)
            if remap_dlg.exec():
                self._renames = remap_dlg.get_renames() or None

        idx = self.montage_combo.findText(name)
        if idx >= 0:
            self.montage_combo.setCurrentIndex(idx)
        else:
            self.montage_combo.setCurrentText(name)
        self.radio_standard.setChecked(True)
        self.detect_label.setText(f"✓ {name}")


# ── Factory functions ────────────────────────────────────────────────────────


def montage_name_factory(current_value, raw, parent):
    widget = MontageConfigWidget(current_name=str(current_value or ""), raw=raw, parent=parent)
    _compound_cache[id(parent)] = widget
    return widget, widget


def _make_proxy_factory(setter_name: str, getter_name: str):
    def factory(current_value, raw, parent):
        compound = _compound_cache.get(id(parent))
        if compound is None:
            le = QLineEdit(str(current_value or ""))
            return le, le
        getattr(compound, setter_name)(current_value)
        proxy = _ValueProxy(getattr(compound, getter_name))
        QTimer.singleShot(0, lambda p=proxy, par=parent: _hide_row(p, par))
        return proxy, proxy
    return factory


def _hide_row(proxy: QWidget, parent):
    form = getattr(parent, "form", None)
    if not isinstance(form, QFormLayout):
        return
    for row in range(form.rowCount()):
        item = form.itemAt(row, QFormLayout.ItemRole.FieldRole)
        if item and item.widget() is proxy:
            form.setRowVisible(row, False)
            break


montage_file_factory = _make_proxy_factory("set_montage_file", "get_montage_file")
montage_renames_factory = _make_proxy_factory("set_renames", "get_renames")

WIDGET_BINDINGS = [
    ParamWidgetBinding("montage_name", montage_name_factory),
    ParamWidgetBinding("montage_file", montage_file_factory),
    ParamWidgetBinding("renames", montage_renames_factory),
]
