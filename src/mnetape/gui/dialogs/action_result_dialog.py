"""Dialog for displaying action execution results."""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from mnetape.core.models import ActionResult


class ActionResultDialog(QDialog):
    """Dialog showing an action's post-execution feedback.

    Displays an optional matplotlib figure, a summary line, and an optional details table.

    Args:
        result: The ActionResult to display.
        title: Action title shown in the window caption.
        parent: Optional parent widget.
    """

    def __init__(self, result: ActionResult, title: str, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle(f"Results - {title}")
        self.setMinimumWidth(560)

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        # Figure
        if result.fig is not None:
            from mnetape.gui.widgets.common import PlotCanvas
            plot_canvas = PlotCanvas(result.fig, self)
            plot_canvas.setMinimumHeight(300)
            layout.addWidget(plot_canvas)

        # Summary
        summary_label = QLabel(result.summary)
        summary_label.setWordWrap(True)
        summary_label.setStyleSheet("font-size: 13px; padding: 4px 2px;")
        layout.addWidget(summary_label)

        # Details
        if result.details:
            details_widget = QWidget()
            form = QFormLayout(details_widget)
            form.setContentsMargins(0, 0, 0, 0)
            form.setSpacing(2)
            for key, value in result.details.items():
                val_label = QLabel(str(value))
                val_label.setStyleSheet("color: gray;")
                form.addRow(f"{key}:", val_label)
            layout.addWidget(details_widget)

        layout.addStretch()

        # Close button
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        close_btn = QPushButton("Close")
        close_btn.setFixedWidth(80)
        close_btn.clicked.connect(self.close)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)
