"""Transient toast notification widget."""

from __future__ import annotations

from typing import Callable

from PyQt6.QtCore import QEvent, Qt, QTimer
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class ToastNotification(QWidget):
    """Overlay widget shown in the bottom-right corner of the parent window.

    Closes automatically after a few seconds. An optional callable can be wired to a "View Results" button.
    If warnings are provided they are shown as orange text below the main message.

    Args:
        message: Main notification text.
        parent: Parent window used for positioning.
        on_view_results: If provided, a "View Results" button is added that calls this callable
            and then closes the toast.
        warnings: Optional list of warning strings displayed in orange below the message.
    """

    DURATION_MS = 10_000

    def __init__(
        self,
        message: str,
        parent=None,
        on_view_results: Callable | None = None,
        warnings: list[str] | None = None,
    ):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)

        self.build_ui(message, on_view_results, warnings)
        self.setFixedWidth(320)

        if parent is not None:
            parent.installEventFilter(self)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.close)
        self._timer.start(self.DURATION_MS)

    def build_ui(self, message: str, on_view_results: Callable | None, runtime_warnings: list[str] | None) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        frame = QFrame()
        frame.setStyleSheet("""
            QFrame {
                background-color: #F6F6F6;
                border-radius: 8px;
                border: 1px solid #C7C7C7;
            }
        """)
        frame_layout = QVBoxLayout(frame)
        frame_layout.setContentsMargins(14, 10, 14, 10)
        frame_layout.setSpacing(8)

        top_row = QHBoxLayout()
        top_row.setSpacing(8)

        msg_label = QLabel(message)
        msg_label.setStyleSheet("color: #000000; font-size: 13px; border: none;")
        msg_label.setWordWrap(True)
        top_row.addWidget(msg_label, 1)

        close_btn = QPushButton("✕")
        close_btn.setFixedSize(22, 22)
        close_btn.setStyleSheet(
            "QPushButton { background: transparent; color: #888; border: none; font-size: 13px; }"
            "QPushButton:hover { color: #EEE; }"
        )
        close_btn.clicked.connect(self.close)
        top_row.addWidget(close_btn, 0, Qt.AlignmentFlag.AlignTop)
        frame_layout.addLayout(top_row)

        if runtime_warnings:
            warn_label = QLabel("\n".join(runtime_warnings))
            warn_label.setStyleSheet("color: #E65100; font-size: 11px; border: none;")
            warn_label.setWordWrap(True)
            frame_layout.addWidget(warn_label)

        if on_view_results is not None:
            results_btn = QPushButton("View Results")
            results_btn.setStyleSheet("""
                QPushButton {
                    background-color: #1565C0;
                    color: white;
                    border: none;
                    border-radius: 4px;
                    padding: 5px 12px;
                    font-size: 12px;
                }
                QPushButton:hover { background-color: #1976D2; }
            """)
            results_btn.clicked.connect(lambda _: on_view_results())
            results_btn.clicked.connect(self.close)
            frame_layout.addWidget(results_btn)

        outer.addWidget(frame)

    def reposition(self) -> None:
        parent = self.parent()
        if parent is None:
            return
        self.adjustSize()
        x = parent.width() - self.width() - 20
        y = parent.height() - self.height() - 20
        self.move(x, y)
        self.raise_()

    def show(self) -> None:
        super().show()
        self.reposition()

    def eventFilter(self, obj, event: QEvent) -> bool:
        if event.type() == QEvent.Type.Resize:
            self.reposition()
        return False

    def closeEvent(self, event) -> None:
        parent = self.parent()
        if parent is not None:
            parent.removeEventFilter(self)
        super().closeEvent(event)
