"""Set annotations action widgets.

Provides a lightweight annotation dialog with:
- a read-only list of current annotations
- an embedded MNE browser used to add/edit/remove annotations

The list is refreshed from browser changes via a low-frequency poll.
"""

from __future__ import annotations

import logging

import mne
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from mnetape.actions.base import ParamWidgetBinding
from mnetape.gui.widgets.common import disable_mne_browser_channel_clicks, sanitize_mne_browser_toolbar

logger = logging.getLogger(__name__)


class AnnotationsValueWidget(QWidget):
    """Hidden value widget that stores the annotations list."""

    value_changed = pyqtSignal()

    def __init__(self, annotations: list[dict], parent=None):
        super().__init__(parent)
        self.hide()
        self.annotations: list[dict] = list(annotations) if annotations else []

    def set_value(self, annotations: list[dict]):
        self.annotations = list(annotations)
        self.value_changed.emit()

    def get_value(self) -> list[dict]:
        return self.annotations


class AnnotationEditorDialog(QDialog):
    """Dialog for managing annotations through the MNE browser.

    Left panel shows a read-only list of annotations.
    Right panel hosts the MNE browser where annotations are edited.
    """

    def __init__(self, raw, annotations: list[dict], parent=None):
        super().__init__(parent)
        self.raw = raw
        self.seed_annotations = list(annotations) if annotations else []
        self.raw_copy: mne.io.Raw | None = raw.copy() if raw is not None else None

        self.browser = None
        self.right_layout = None
        self.last_ann_hash: int | None = None

        self.setWindowTitle("Edit Annotations")
        self.setMinimumSize(1000, 560)
        self.setSizeGripEnabled(True)

        outer = QVBoxLayout(self)
        splitter = QSplitter(Qt.Orientation.Horizontal)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)

        hint = QLabel("Use the browser to add/edit annotations.\nThis list is read-only.")
        hint.setStyleSheet("color: #777777;")
        hint.setWordWrap(True)
        left_layout.addWidget(hint)

        self.list_widget = QListWidget()
        self.list_widget.setAlternatingRowColors(True)
        self.list_widget.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        left_layout.addWidget(self.list_widget)

        splitter.addWidget(left)

        right = QWidget()
        self.right_layout = QVBoxLayout(right)
        self.right_layout.setContentsMargins(0, 0, 0, 0)
        splitter.addWidget(right)
        splitter.setSizes([350, 650])
        outer.addWidget(splitter, 1)

        self.initialize_annotations()
        self.refresh_list_from_annotations()
        self.last_ann_hash = self.ann_hash()

        if self.raw_copy is not None:
            try:
                self.browser = self.raw_copy.plot(show=False)
                sanitize_mne_browser_toolbar(self.browser, allow_annotation_mode=True)
                disable_mne_browser_channel_clicks(self.browser)
                self.right_layout.addWidget(self.browser)
            except Exception as e:
                logger.warning("Could not embed MNE browser in annotation editor: %s", e)
                lbl = QLabel(f"Browser unavailable:\n{e}")
                lbl.setWordWrap(True)
                lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
                self.right_layout.addWidget(lbl)
        else:
            lbl = QLabel("Load data to view and edit annotations")
            lbl.setStyleSheet("color: #999999; font-size: 14pt;")
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.right_layout.addWidget(lbl)

        self.poll_timer = QTimer()
        self.poll_timer.setInterval(250)
        self.poll_timer.timeout.connect(self.poll_browser)
        if self.browser is not None:
            self.poll_timer.start()

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

    def initialize_annotations(self):
        """Seed raw_copy annotations from param value or existing raw annotations."""
        if self.raw_copy is None:
            return

        if self.seed_annotations:
            anns = self.seed_annotations
        elif len(self.raw.annotations) > 0:
            anns = [
                {
                    "onset": float(onset),
                    "duration": float(duration),
                    "description": str(description),
                }
                for onset, duration, description in zip(
                    self.raw.annotations.onset,
                    self.raw.annotations.duration,
                    self.raw.annotations.description,
                )
            ]
        else:
            anns = []

        self.raw_copy.set_annotations(
            mne.Annotations(
                onset=[a["onset"] for a in anns],
                duration=[a["duration"] for a in anns],
                description=[a["description"] for a in anns],
            )
        )

    def ann_hash(self) -> int | None:
        if self.raw_copy is None:
            return None
        ann = self.raw_copy.annotations
        return hash((tuple(ann.onset), tuple(ann.duration), tuple(ann.description)))

    def refresh_list_from_annotations(self):
        """Render current annotations into the read-only list."""
        self.list_widget.clear()

        if self.raw_copy is None:
            anns = self.seed_annotations
        else:
            ann = self.raw_copy.annotations
            anns = [
                {
                    "onset": float(onset),
                    "duration": float(duration),
                    "description": str(description),
                }
                for onset, duration, description in zip(
                    ann.onset,
                    ann.duration,
                    ann.description,
                )
            ]

        if not anns:
            self.list_widget.addItem("No annotations")
            return

        for idx, a in enumerate(anns, start=1):
            self.list_widget.addItem(
                f"{idx}. {a['onset']:.3f}s | {a['duration']:.3f}s | {a['description']}"
            )

    def poll_browser(self):
        """Refresh the list only when browser annotations changed."""
        if self.raw_copy is None or self.browser is None:
            return
        h = self.ann_hash()
        if h == self.last_ann_hash:
            return
        self.last_ann_hash = h
        self.refresh_list_from_annotations()

    def get_annotations(self) -> list[dict]:
        """Return current annotations from raw_copy (or seed list if no raw)."""
        if self.raw_copy is None:
            return list(self.seed_annotations)

        ann = self.raw_copy.annotations
        return [
            {
                "onset": float(onset),
                "duration": float(duration),
                "description": str(description),
            }
            for onset, duration, description in zip(
                ann.onset,
                ann.duration,
                ann.description,
            )
        ]

    def done(self, result):
        self.poll_timer.stop()
        if self.browser is not None:
            try:
                if self.right_layout is not None:
                    self.right_layout.removeWidget(self.browser)
                self.browser.close()
                self.browser.deleteLater()
            except Exception as e:
                logger.debug("Browser cleanup error: %s", e)
            self.browser = None
        super().done(result)


# -------- Param widget factory --------

def annotations_factory(current_value, raw, parent):
    """Param widget factory for the annotations param type."""
    annotations = list(current_value) if current_value else []
    value_widget = AnnotationsValueWidget(annotations)

    def make_summary() -> str:
        n = len(value_widget.get_value())
        return f"{n} annotation{'s' if n != 1 else ''}" if n else "No annotations"

    summary_label = QLabel(make_summary())
    btn = QPushButton("Open Browser…")

    def open_editor():
        dlg = AnnotationEditorDialog(
            raw=raw,
            annotations=value_widget.get_value(),
            parent=parent,
        )
        if dlg.exec() == QDialog.DialogCode.Accepted:
            value_widget.set_value(dlg.get_annotations())
            summary_label.setText(make_summary())

    btn.clicked.connect(open_editor)

    container = QWidget()
    layout = QHBoxLayout(container)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.addWidget(summary_label, 1)
    layout.addWidget(btn)

    return container, value_widget


# -------- Widget bindings --------

WIDGET_BINDINGS = [
    ParamWidgetBinding("annotations", annotations_factory),
]
