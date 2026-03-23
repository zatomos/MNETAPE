"""Reusable non-dialog widgets for the EEG pipeline GUI.

Exports:
    Toolbar: Matplotlib navigation toolbar with a simplified tool set.
    PlotCanvas: QWidget that hosts a matplotlib figure and its toolbar.
    ActionListItem: Row widget for the pipeline action list, with optional
        step expansion and an inline run button.
"""

from PyQt6.QtWidgets import QAbstractItemView, QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QListWidget, QToolBar
from PyQt6.QtCore import pyqtSignal, QSize, Qt
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT
from matplotlib.figure import Figure

from mnetape.actions.registry import get_action_title
from mnetape.core.models import CUSTOM_ACTION_ID, ActionConfig, ActionStatus, STATUS_ICONS, STATUS_COLORS


def disable_psd_span_popups(fig: Figure) -> None:
    """Disable span selectors in MNE PSD figures that open popup windows."""
    from matplotlib.widgets import SpanSelector

    for ax in fig.axes:
        for attr in vars(ax).values():
            if isinstance(attr, SpanSelector):
                attr.set_active(False)


def sanitize_mne_browser_toolbar(browser: QWidget, *, allow_annotation_mode: bool) -> None:
    """Hide selected controls from MNE browser toolbars."""
    if browser is None or not hasattr(browser, "findChildren"):
        return

    hide_keywords = {"settings", "setting", "config", "options", "projector", "proj", "ssp"}
    if not allow_annotation_mode:
        hide_keywords.add("annotation")

    def _should_hide(action) -> bool:
        label = " ".join(
            filter(None, (action.text(), action.toolTip(), action.whatsThis(), action.objectName()))
        ).lower()
        return any(k in label for k in hide_keywords)

    for toolbar in browser.findChildren(QToolBar):
        for action in toolbar.actions():
            if _should_hide(action):
                action.setVisible(False)


def disable_mne_browser_channel_clicks(browser: QWidget) -> None:
    """Disable channel click interactions in an MNE browser widget."""
    if browser is None:
        return

    mne_state = getattr(browser, "mne", None)
    if mne_state is None:
        return

    for trace in getattr(mne_state, "traces", []):
        if hasattr(trace, "setClickable"):
            trace.setClickable(False)

    ch_axis = getattr(mne_state, "channel_axis", None)
    if ch_axis is not None and hasattr(ch_axis, "mouseClickEvent"):
        ch_axis.mouseClickEvent = lambda ev: ev.ignore()


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

    def set_message(self, s):
        try:
            super().set_message(s)
        except RuntimeError:
            pass


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
        creating new ones. The old matplotlib figure is explicitly closed to
        remove it from matplotlib's internal figure registry and free its data.

        Args:
            fig: The new matplotlib Figure to display.
        """
        import matplotlib.pyplot as plt

        layout = self.layout()
        layout.removeWidget(self.toolbar)
        layout.removeWidget(self.canvas)

        old_fig = self.canvas.figure

        # Detach callbacks and schedule destruction
        self.toolbar.set_message = lambda s: None
        self.toolbar.deleteLater()
        self.canvas.deleteLater()

        # Release the old figure from the registry
        plt.close(old_fig)

        # Create new canvas and toolbar
        self.canvas = FigureCanvasQTAgg(fig)
        self.toolbar = Toolbar(self.canvas, self)
        layout.addWidget(self.toolbar)
        layout.addWidget(self.canvas, 1)
        self.canvas.draw()


class PinnedActionItem(QWidget):
    """Non-moveable, non-selectable action row for implicit pipeline steps.

    Used for the Load File step (always present) and Set Montage step.
    Left-clicking emits ``clicked``; right-clicking emits ``right_clicked``.
    """

    clicked = pyqtSignal()
    right_clicked = pyqtSignal()

    def __init__(self, label: str, detail: str = "", warning: bool = False, parent=None):
        super().__init__(parent)
        self.setObjectName("pinned_action_item")
        self._warning = warning

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 0, 8, 0)
        layout.setSpacing(6)

        icon = QLabel("🔒")
        icon.setFixedWidth(20)
        icon.setStyleSheet("color: #AAAAAA;")
        layout.addWidget(icon, 0, Qt.AlignmentFlag.AlignVCenter)

        text = f"{label}  ·  {detail}" if detail else label
        self.name_label = QLabel(text)
        color = "#C62828" if warning else "#888888"
        self.name_label.setStyleSheet(f"color: {color}; font-style: italic;")
        layout.addWidget(self.name_label, 1, Qt.AlignmentFlag.AlignVCenter)

    def sizeHint(self) -> QSize:
        return QSize(super().sizeHint().width(), 46)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.RightButton:
            self.right_clicked.emit()
        else:
            self.clicked.emit()
        super().mousePressEvent(event)


class ActionListItem(QWidget):
    """Row widget representing a single pipeline action in the action list.

    Displays the action number, status icon, title, and a run button.
    A type_mismatch flag shows a warning icon and disables the run button
    when the action's expected input type doesn't match the pipeline's current type.

    Signals:
        size_changed: Emitted when the widget's size changes.
        run_clicked (row): Emitted when the inline run button is clicked.
    """

    size_changed = pyqtSignal()
    run_clicked = pyqtSignal(int)

    def __init__(self, index: int, action: ActionConfig, parent=None, type_mismatch: bool = False):
        super().__init__(parent)
        self.index = index
        self.row = index - 1
        self.action = action
        self.type_mismatch = type_mismatch

        self.setObjectName("action_item_widget")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 0, 8, 0)
        layout.setSpacing(6)

        # Status icon
        self.status_label = QLabel()
        self.status_label.setFixedWidth(20)
        self.update_status_icon()
        layout.addWidget(self.status_label, 0, Qt.AlignmentFlag.AlignVCenter)

        # Action name with custom/edited badge
        name = get_action_title(action)
        if action.is_custom:
            name += " [CUSTOM]" if action.action_id == CUSTOM_ACTION_ID else " [EDITED]"
        self.name_label = QLabel(f"{index}. {name}")
        self.name_label.setStyleSheet("font-weight: bold;")
        layout.addWidget(self.name_label, 1, Qt.AlignmentFlag.AlignVCenter)

        # Run button
        self.run_btn = QPushButton("▶")
        self.run_btn.setFixedSize(35, 26)
        self.run_btn.setStyleSheet(
            "QPushButton { background-color:#2E7D32; color:white; border:none; border-radius:4px;"
            " padding: 8; min-height: 0; }"
            "QPushButton:hover { background-color:#388E3C; }"
            "QPushButton:disabled { background-color:#BDBDBD; color:#757575; }"
        )
        self.run_btn.clicked.connect(lambda _, r=self.row: self.run_clicked.emit(r))
        if type_mismatch:
            self.run_btn.setEnabled(False)
        layout.addWidget(self.run_btn, 0, Qt.AlignmentFlag.AlignVCenter)

    def sizeHint(self) -> QSize:
        return QSize(super().sizeHint().width(), 46)

    def update_status_icon(self):
        """Update the status icon label color to match the action's current status."""
        if self.type_mismatch:
            self.status_label.setText("⚠")
            self.status_label.setStyleSheet("color: #D32F2F; font-weight: bold;")
        else:
            self.status_label.setText(STATUS_ICONS[self.action.status])
            self.status_label.setStyleSheet(f"color: {STATUS_COLORS[self.action.status]}; font-weight: bold;")

    def update_status(self, status: ActionStatus):
        """Set a new action status and refresh the status icon.

        Args:
            status: The new ActionStatus to apply.
        """
        self.action.status = status
        self.update_status_icon()


class ActionListWidget(QListWidget):
    """QListWidget with drag-and-drop reordering for action items.

    Emits items_reordered(from_row, to_row) when an action item is dragged to a new
    position. Header items (UserRole == -1) cannot be dragged or used as drop targets.
    """

    items_reordered = pyqtSignal(int, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)

    def dropEvent(self, event):
        source_item = self.currentItem()
        if source_item is None:
            event.ignore()
            return

        source_row = source_item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(source_row, int) or source_row < 0:
            event.ignore()
            return

        target_item = self.itemAt(event.position().toPoint())
        if target_item is None:
            event.ignore()
            return

        target_row = target_item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(target_row, int) or target_row < 0:
            event.ignore()
            return

        if source_row != target_row:
            self.items_reordered.emit(source_row, target_row)
        event.ignore()
