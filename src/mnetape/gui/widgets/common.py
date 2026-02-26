"""Reusable non-dialog widgets for the EEG pipeline GUI.

Exports:
    Toolbar: Matplotlib navigation toolbar with a simplified tool set.
    PlotCanvas: QWidget that hosts a matplotlib figure and its toolbar.
    ActionListItem: Row widget for the pipeline action list, with optional
        step expansion and an inline run button.
"""

from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel
from PyQt6.QtCore import pyqtSignal, Qt
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT
from matplotlib.figure import Figure

from mnetape.actions.registry import get_action_by_id, get_action_title
from mnetape.core.models import ActionConfig, ActionStatus, STATUS_ICONS, STATUS_COLORS


class Toolbar(NavigationToolbar2QT):
    """Matplotlib navigation toolbar exposing only the most-used tools.

    Removes sub-plot config, figure options, and other tools that are rarely
    needed in an embedded context, keeping the toolbar compact.
    """

    tool_items = [
        ('Home', 'Reset original view', 'home', 'home'),            # Home
        ('Back', 'Back to previous view', 'back', 'back'),          # Back
        ('Forward', 'Forward to next view', 'forward', 'forward'),  # Forward
        (None, None, None, None),                                   # Separator
        ('Pan', 'Pan axes', 'move', 'pan'),                         # Pan
        ('Zoom', 'Zoom to rectangle', 'zoom_to_rect', 'zoom'),      # Zoom
        (None, None, None, None),                                   # Separator
        ('Save', 'Save the figure', 'filesave', 'save_figure'),     # Save
    ]


class PlotCanvas(QWidget):
    """QWidget that embeds a matplotlib figure together with a navigation toolbar.

    Provides update_figure() to swap in a new figure without rebuilding the
    widget; the old canvas and toolbar are scheduled for deletion via
    deleteLater().
    """

    def __init__(self, fig=None, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        if fig is None:
            fig = Figure(figsize=(8, 4))

        self.canvas = FigureCanvasQTAgg(fig)
        self.toolbar = Toolbar(self.canvas, self)

        layout.addWidget(self.toolbar)
        layout.addWidget(self.canvas, 1)

    def update_figure(self, fig):
        """Replace the current matplotlib figure with a new one.

        Removes and schedules deletion of the old canvas and toolbar before
        creating new ones. The toolbar's set_message is neutered first to
        prevent a RuntimeError if matplotlib tries to update it during teardown.

        Args:
            fig: The new matplotlib Figure to display.
        """
        layout = self.layout()
        layout.removeWidget(self.toolbar)
        layout.removeWidget(self.canvas)

        # Detach callbacks and schedule destruction
        self.toolbar.set_message = lambda s: None
        self.toolbar.deleteLater()
        self.canvas.deleteLater()

        # Create new canvas and toolbar
        self.canvas = FigureCanvasQTAgg(fig)
        self.toolbar = Toolbar(self.canvas, self)
        layout.addWidget(self.toolbar)
        layout.addWidget(self.canvas)


class ActionListItem(QWidget):
    """Row widget representing a single pipeline action in the action list.

    Displays the action number, status icon, title, and a run button. For
    multi-step actions, an expand/collapse button reveals per-step status
    labels. Each step label is clickable and emits step_clicked.

    Signals:
        size_changed: Emitted when the widget's size changes (e.g. on expand/collapse)
            so the parent QListWidget can update the item size hint.
        step_clicked (row, step_idx): Emitted when a step label is clicked.
        run_clicked (row): Emitted when the inline run button is clicked.
    """

    size_changed = pyqtSignal()
    step_clicked = pyqtSignal(int, int)
    run_clicked = pyqtSignal(int)

    def __init__(self, index: int, action: ActionConfig, parent=None):
        super().__init__(parent)
        self.index = index
        self.row = index - 1
        self.action = action
        self.expanded = False

        # Layout setup
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(8, 8, 8, 8)
        main_layout.setSpacing(4)
        top_row = QHBoxLayout()
        top_row.setSpacing(4)
        top_row.setContentsMargins(0, 0, 0, 0)

        # Determine if action has steps and get them
        action_def = get_action_by_id(action.action_id)
        self.has_steps = action_def is not None and action_def.has_steps()
        self.steps = action_def.steps if self.has_steps else ()

        # Expand/collapse button
        if self.has_steps:
            self.expand_btn = QPushButton("▶")
            self.expand_btn.setFlat(True)
            self.expand_btn.setFixedSize(20, 20)
            self.expand_btn.setStyleSheet(
                "QPushButton { border:none; background:transparent; font-size:10px; "
                "font-family:'Segoe UI Symbol', 'Ubuntu', sans-serif; "
                "color:#888; padding:0px 0px 0px 0px; text-align:center; }"
                "QPushButton:hover { color:#444; }"
            )
            self.expand_btn.clicked.connect(self.toggle_expand)
            top_row.addWidget(self.expand_btn, 0, Qt.AlignmentFlag.AlignVCenter)

        # Status icon
        self.status_label = QLabel()
        self.status_label.setFixedWidth(20)
        self.update_status_icon()
        top_row.addWidget(self.status_label, 0, Qt.AlignmentFlag.AlignVCenter)

        # Action name with custom/edited badge
        name = get_action_title(action)
        if action.is_custom:
            name += " [CUSTOM]" if action.action_id == "custom" else " [EDITED]"
        self.name_label = QLabel(f"{index}. {name}")
        self.name_label.setStyleSheet("font-weight: bold;")
        top_row.addWidget(self.name_label, 1, Qt.AlignmentFlag.AlignVCenter)

        # Run button
        self.run_btn = QPushButton("▶")
        self.run_btn.setFixedSize(35, 25)
        self.run_btn.setStyleSheet(
            "QPushButton { background-color:#2E7D32; color:white; border:none; border-radius:4px; }"
            "QPushButton:hover { background-color:#388E3C; }"
        )
        self.run_btn.clicked.connect(lambda _, r=self.row: self.run_clicked.emit(r))
        top_row.addWidget(self.run_btn, 0, Qt.AlignmentFlag.AlignVCenter)

        main_layout.addLayout(top_row)

        # Steps container
        if self.has_steps:
            self.steps_widget = QWidget()
            steps_layout = QVBoxLayout(self.steps_widget)
            steps_layout.setContentsMargins(28, 0, 0, 0)
            steps_layout.setSpacing(2)

            self.step_labels = []
            for idx, _step in enumerate(self.steps):
                step_btn = QPushButton()
                step_btn.setFlat(True)
                step_btn.setStyleSheet(
                    "QPushButton { font-size:11px; text-align:left; "
                    "border:none; padding:0px; color:#555; }"
                    "QPushButton:hover { color:#222; text-decoration:underline; }"
                )
                step_btn.clicked.connect(lambda _, r=self.row, i=idx: self.step_clicked.emit(r, i))
                steps_layout.addWidget(step_btn)
                self.step_labels.append(step_btn)

            self.update_step_labels()
            self.steps_widget.hide()
            main_layout.addWidget(self.steps_widget)

    def toggle_expand(self):
        """Show or hide the steps sub-widget and emit size_changed."""
        self.expanded = not self.expanded
        if self.expanded:
            self.steps_widget.show()
            self.expand_btn.setText("▼")
        else:
            self.steps_widget.hide()
            self.expand_btn.setText("▶")
        self.size_changed.emit()

    def update_step_labels(self):
        """Refresh the text and colour of each step label based on current status."""
        if not self.has_steps:
            return
        for i, (step, label) in enumerate(zip(self.steps, self.step_labels)):
            # Determine icon and color based on step status
            if i < self.action.completed_steps:
                icon = STATUS_ICONS[ActionStatus.COMPLETE]
                color = STATUS_COLORS[ActionStatus.COMPLETE]
            elif self.action.status == ActionStatus.ERROR and i == self.action.completed_steps:
                icon = STATUS_ICONS[ActionStatus.ERROR]
                color = STATUS_COLORS[ActionStatus.ERROR]
            else:
                icon = STATUS_ICONS[ActionStatus.PENDING]
                color = STATUS_COLORS[ActionStatus.PENDING]

            # Update label text and style
            label.setText(f"{icon} {step.title}")
            label.setStyleSheet(f"font-size: 11px; color: {color};")

    def update_status_icon(self):
        """Update the status icon label colour to match the action's current status."""
        self.status_label.setText(STATUS_ICONS[self.action.status])
        self.status_label.setStyleSheet(f"color: {STATUS_COLORS[self.action.status]}; font-weight: bold;")

    def update_status(self, status: ActionStatus):
        """Set a new action status and refresh all status-related UI elements.

        Args:
            status: The new ActionStatus to apply.
        """
        self.action.status = status
        self.update_status_icon()
        if self.has_steps:
            self.update_step_labels()