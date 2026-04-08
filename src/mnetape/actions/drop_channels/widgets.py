"""Widgets for the drop-channels action.

Provides:
    BadChannelDetectorDialog: Runs pyprep's NoisyChannels in a background thread to automatically detect bad channels.
    ChannelPickerDialog: Interactive dialog with a sensor map and embedded raw preview for manually selecting channels
        to drop.
    channels_widget_factory: Param widget factory for the "channels" param type, combining a text field
        with "Pick..." and "Detect..." buttons.
"""

import logging

from PyQt6.QtCore import Qt, QThread, pyqtSignal, QObject
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QStyle,
    QVBoxLayout,
    QWidget,
)
import mne

from mnetape.actions.base import ParamWidgetBinding
from mnetape.gui.dialogs.action_editor import ListLineEdit
from mnetape.gui.dialogs.channel_picker_dialog import ChannelPickerDialog

logger = logging.getLogger(__name__)


class DetectionWorker(QObject):
    """QObject worker that runs pyprep's NoisyChannels in a background QThread.

    Signals:
        finished (list[str]): Emitted with the list of detected bad channel names when detection completes successfully.
        error (str): Emitted with the error message string when detection fails.
    """

    finished = pyqtSignal(list)
    error = pyqtSignal(str)

    def __init__(self, raw: mne.io.Raw, params: dict):
        super().__init__()
        self.raw = raw
        self.params = params

    def run(self):
        """Execute detection and emit finished or error on the worker thread."""
        try:
            self.run_pyprep()
        except Exception as exc:
            self.error.emit(str(exc))

    def run_pyprep(self):
        """Run NoisyChannels detection and emit finished with the detected bad channels list."""
        from pyprep import NoisyChannels

        nd = NoisyChannels(
            self.raw,
            do_detrend=self.params.get("do_detrend", True),
            random_state=self.params.get("random_state", 42),
        )
        nd.find_all_bads(ransac=self.params.get("ransac", True))
        bads = nd.get_bads()
        self.finished.emit(bads)


class BadChannelDetectorDialog(QDialog):
    """Dialog to auto-detect bad channels using pyprep."""
    def __init__(self, raw: mne.io.Raw, parent=None):
        super().__init__(parent)
        self.raw = raw
        self.detected: list[str] = []
        self.thread: QThread | None = None
        self.worker: DetectionWorker | None = None

        self.setWindowTitle("Auto-Detect Bad Channels")
        self.setMinimumWidth(420)

        layout = QVBoxLayout(self)

        # Method selector
        method_layout = QHBoxLayout()
        method_layout.addWidget(QLabel("Automated bad channels detection using pyprep's NoisyChannel."))
        layout.addLayout(method_layout)

        # pyprep params
        pyprep_form = QFormLayout()
        self.pyprep_ransac = QComboBox()
        self.pyprep_ransac.addItems(["Yes", "No"])
        pyprep_form.addRow("Include RANSAC:", self.pyprep_ransac)
        self.pyprep_detrend = QComboBox()
        self.pyprep_detrend.addItems(["Yes", "No"])
        pyprep_form.addRow("Detrend (<1 Hz):", self.pyprep_detrend)
        layout.addLayout(pyprep_form)

        # Run button
        self.btn_run = QPushButton("Run Detection")
        self.btn_run.clicked.connect(self.run_detection)
        layout.addWidget(self.btn_run)

        # Results
        self.result_label = QLabel("")
        self.result_label.setWordWrap(True)
        layout.addWidget(self.result_label)

        # OK / Cancel
        self.button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.button_box.button(QDialogButtonBox.StandardButton.Ok).setEnabled(False)
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)
        layout.addWidget(self.button_box)

    def gather_params(self) -> dict:
        """Collect pyprep detection parameters from the dialog widgets.

        Returns:
            Dict with pyprep params
        """
        return {
            "ransac": self.pyprep_ransac.currentText() == "Yes",
            "do_detrend": self.pyprep_detrend.currentText() == "Yes",
            "random_state": 42,
        }

    def run_detection(self):
        """Start the detection background thread and update the UI to a running state."""
        params = self.gather_params()

        # Disable UI while running
        self.btn_run.setEnabled(False)
        self.btn_run.setText("Running...")
        self.result_label.setStyleSheet("")
        self.result_label.setText("Detecting bad channels...")
        self.setCursor(Qt.CursorShape.WaitCursor)

        # Detection thread
        self.thread = QThread()
        self.worker = DetectionWorker(self.raw, params)
        self.worker.moveToThread(self.thread)

        # Connect signals
        self.thread.started.connect(self.worker.run)
        self.worker.finished.connect(self.on_detection_done)
        self.worker.error.connect(self.on_detection_error)
        self.worker.finished.connect(self.cleanup_thread)
        self.worker.error.connect(self.cleanup_thread)

        self.thread.start()

    def on_detection_done(self, bads: list[str]):
        """Handle successful detection and update the result label.

        Args:
            bads: List of detected bad channel names.
        """
        self.detected = bads
        self.unsetCursor()
        self.btn_run.setEnabled(True)
        self.btn_run.setText("Run Detection")
        if bads:
            self.result_label.setStyleSheet("color: #4EC9B0;")
            self.result_label.setText(
                f"Detected {len(bads)} bad channel(s):\n{', '.join(bads)}"
            )
            self.button_box.button(QDialogButtonBox.StandardButton.Ok).setEnabled(True)
        else:
            self.result_label.setStyleSheet("")
            self.result_label.setText("No bad channels detected.")
            self.button_box.button(QDialogButtonBox.StandardButton.Ok).setEnabled(False)

    def on_detection_error(self, msg: str):
        """Handle a detection failure and show the error in the result label.

        Args:
            msg: Human-readable error description.
        """
        self.detected = []
        self.unsetCursor()
        self.btn_run.setEnabled(True)
        self.btn_run.setText("Run Detection")
        self.result_label.setStyleSheet("color: #F44747;")
        self.result_label.setText(f"Error: {msg}")
        self.button_box.button(QDialogButtonBox.StandardButton.Ok).setEnabled(False)

    def cleanup_thread(self):
        """Quit and wait for the detection thread, then clear thread/worker references."""
        if self.thread is not None:
            self.thread.quit()
            self.thread.wait()
            self.thread = None
            self.worker = None

    def get_detected(self) -> list[str]:
        """Return a copy of the detected bad channels list after the dialog is accepted."""
        return list(self.detected)

    def closeEvent(self, event):
        self.cleanup_thread()
        super().closeEvent(event)


# -------- Param widget factory --------

def channels_widget_factory(current_value, raw, parent):
    """Build a widget for the 'channels' param type.

    Returns a (container, value_widget) pair:
        - container is a QWidget that the user can interact with to pick or detect channels
        - value_widget is a QLineEdit that holds the comma-separated channel names string.
    Both buttons are disabled when raw is None.

    Args:
        param_def: Parameter metadata dict (unused here, kept for factory protocol).
        current_value: Current channel list or string to pre-populate the text field.
        raw: The MNE Raw object to pass to the picker/detector dialogs. May be None.
        parent: Parent widget for the dialogs.

    Returns:
        Tuple of (container QWidget, QLineEdit value widget).
    """
    container = QWidget()
    layout = QHBoxLayout(container)
    layout.setContentsMargins(0, 0, 0, 0)

    text = ", ".join(current_value) if isinstance(current_value, list) else (str(current_value) if current_value is not None else "")
    line_edit = ListLineEdit(text)
    line_edit.setPlaceholderText("channel names, comma-separated")
    layout.addWidget(line_edit, 1)

    btn_pick = QPushButton("Pick...")
    btn_pick.setEnabled(raw is not None)

    def pick():
        if raw is None:
            return
        selected = [c.strip() for c in line_edit.text().split(",") if c.strip()]
        dlg = ChannelPickerDialog(raw, selected, parent)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            line_edit.setText(", ".join(dlg.get_selected()))

    drop_icon = line_edit.style().standardIcon(QStyle.StandardPixmap.SP_ArrowDown)
    drop_action = QAction(drop_icon, "Pick channels…", line_edit)
    drop_action.setEnabled(raw is not None)
    drop_action.triggered.connect(pick)
    line_edit.addAction(drop_action, QLineEdit.ActionPosition.TrailingPosition)

    btn_pick.clicked.connect(pick)
    layout.addWidget(btn_pick)

    btn_detect = QPushButton("Detect...")
    btn_detect.setEnabled(raw is not None)

    def detect():
        if raw is None:
            return
        dlg = BadChannelDetectorDialog(raw, parent)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            detected = dlg.get_detected()
            if detected:
                existing = {c.strip() for c in line_edit.text().split(",") if c.strip()}
                merged = existing | set(detected)
                ordered = [ch for ch in raw.ch_names if ch in merged]
                line_edit.setText(", ".join(ordered))

    btn_detect.clicked.connect(detect)
    layout.addWidget(btn_detect)

    return container, line_edit


# -------- Widget bindings --------

WIDGET_BINDINGS = [
    ParamWidgetBinding("channels", channels_widget_factory),
]
