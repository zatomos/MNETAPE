"""Dialog for editing action parameters.

ActionEditor is a QDialog that builds a form from an action's params_schema, an optional advanced params section,
and a live code-preview panel. It supports both full-action editing and step-level editing.
"""

import logging

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QGraphicsOpacityEffect,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
import mne

from mnetape.actions.registry import get_action_by_id, get_action_title
from mnetape.core.codegen import generate_action_code
from mnetape.core.models import CUSTOM_ACTION_ID, ActionConfig, ActionStatus, DataType


logger = logging.getLogger(__name__)


# -------- Widget creation helpers --------


class NullableWidget(QWidget):
    """A wrapper widget that adds a checkbox to enable/disable a param widget.

    When the checkbox is unchecked, the inner widget is disabled and get_value() returns None.
    When checked, returns the inner widget's current value.
    """

    value_changed = pyqtSignal()

    def __init__(self, inner: QWidget, has_value: bool, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self.checkbox = QCheckBox()
        self.checkbox.setChecked(has_value)
        self.inner = inner

        self.opacity = QGraphicsOpacityEffect()
        self.opacity.setOpacity(1.0 if has_value else 0.35)
        inner.setGraphicsEffect(self.opacity)
        inner.setEnabled(has_value)

        self.checkbox.toggled.connect(self.on_toggle)
        self.checkbox.toggled.connect(lambda _: self.value_changed.emit())

        if isinstance(inner, (QSpinBox, QDoubleSpinBox)):
            inner.valueChanged.connect(self.value_changed)
        elif isinstance(inner, QComboBox):
            inner.currentTextChanged.connect(self.value_changed)
        elif isinstance(inner, QCheckBox):
            inner.stateChanged.connect(self.value_changed)
        elif isinstance(inner, QLineEdit):
            inner.textChanged.connect(self.value_changed)

        layout.addWidget(self.checkbox)
        layout.addWidget(inner, 1)

    def on_toggle(self, checked: bool):
        self.inner.setEnabled(checked)
        self.opacity.setOpacity(1.0 if checked else 0.35)

    def get_value(self):
        if not self.checkbox.isChecked():
            return None
        return get_widget_value(self.inner)


def create_widget_for_param(param_def: dict, current_value):
    """Create an appropriate Qt widget for a single parameter definition.

    Selects the widget type and adapts accordingly for numeric ranges, choices, etc.
    Falls back to a text input for unrecognized types.

    Args:
        param_def: Parameter metadata dict.
        current_value: The value to pre-populate the widget with.

    Returns:
        A configured Qt widget, or None if the type is not supported.
    """
    nullable = param_def.get("nullable", False)
    # When current_value is None and the param is nullable, display the default in the widget
    display_value = current_value if current_value is not None else param_def.get("default")
    ptype = param_def.get("type", "text")

    if ptype == "float":
        widget = QDoubleSpinBox()
        widget.setRange(param_def.get("min", -999999), param_def.get("max", 999999))
        widget.setDecimals(param_def.get("decimals", 2))
        widget.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.UpDownArrows)
        try:
            widget.setValue(float(display_value) if display_value is not None else 0.0)
        except (TypeError, ValueError):
            widget.setValue(param_def.get("default", 0.0))
        inner = widget

    elif ptype == "int":
        widget = QSpinBox()
        widget.setRange(param_def.get("min", -999999), param_def.get("max", 999999))
        widget.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.UpDownArrows)
        try:
            widget.setValue(int(display_value) if display_value is not None else 0)
        except (TypeError, ValueError):
            widget.setValue(param_def.get("default", 0))
        inner = widget

    elif ptype == "choice":
        widget = QComboBox()
        widget.addItems(param_def.get("choices", []))
        widget.setCurrentText(str(display_value))
        inner = widget

    elif ptype == "bool":
        widget = QCheckBox()
        widget.setChecked(bool(display_value))
        inner = widget

    else:
        # text / fallback
        widget = QLineEdit(str(display_value) if display_value is not None else "")
        inner = widget

    if nullable:
        return NullableWidget(inner, has_value=(current_value is not None))
    return widget


def get_widget_value(widget):
    """Extract the current value from a param widget.

    Args:
        widget: A Qt widget created by create_widget_for_param.

    Returns:
        The widget's current value in its native Python type, or None for unrecognized widget types.
    """
    # Custom get_value() takes priority over built-in type detection
    if hasattr(widget, "get_value") and callable(widget.get_value):
        return widget.get_value()
    if isinstance(widget, (QSpinBox, QDoubleSpinBox)):
        return widget.value()
    if isinstance(widget, QComboBox):
        return widget.currentText()
    if isinstance(widget, QCheckBox):
        return widget.isChecked()
    if isinstance(widget, QLineEdit):
        return widget.text()
    return None


def connect_widget_signal(widget, slot):
    """Connect the value-changed signal of a param widget to a slot function.

    Args:
        widget: A Qt widget created by create_widget_for_param.
        slot: Callable to invoke whenever the widget's value changes.
    """
    if isinstance(widget, NullableWidget):
        widget.value_changed.connect(slot)
    elif isinstance(widget, (QSpinBox, QDoubleSpinBox)):
        widget.valueChanged.connect(slot)
    elif isinstance(widget, QComboBox):
        widget.currentTextChanged.connect(slot)
    elif isinstance(widget, QCheckBox):
        widget.stateChanged.connect(slot)
    elif isinstance(widget, QLineEdit):
        widget.textChanged.connect(slot)
    elif hasattr(widget, "value_changed"):
        widget.value_changed.connect(slot)


# -------- Main dialog --------

class ActionEditor(QDialog):
    """Dialog for editing the parameters of a pipeline action.

    Builds a dynamic form from the action's params_schema, with an optional collapsible "Advanced" section
    containing additional MNE function kwargs.
    A read-only code preview updates in real time as parameters change.

    When step_idx is provided, only the parameters of that specific step are shown and returned.

    Args:
        action: The ActionConfig being edited.
        raw: The current MNE Raw object, used by param widget factories that need channel information.
            May be None if no file is loaded.
        parent: Optional parent widget.
    """

    def __init__(
        self,
        action: ActionConfig,
        raw: mne.io.Raw | None = None,
        parent=None,
        context_type: DataType | None = None,
    ):
        super().__init__(parent)
        self.action = action
        self.raw = raw
        self.context_type = context_type
        self.action_def = get_action_by_id(action.action_id)

        self.setWindowTitle(f"Edit: {get_action_title(action)}")
        visible_params = self.action_def.params_schema if self.action_def else {}

        self.setMinimumWidth(420)

        # Cap dialog height at 85% of available screen height so "Show Advanced"
        # never grows the dialog off-screen; the scroll area handles overflow.
        if screen := QApplication.primaryScreen():
            self.setMaximumHeight(int(screen.availableGeometry().height() * 0.85))

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Scrollable params section
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # Always reserve scrollbar space so its appearance never reflows content.
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        scroll_inner = QWidget()
        layout = QVBoxLayout(scroll_inner)
        layout.setContentsMargins(12, 10, 12, 8)
        layout.setSpacing(6)
        scroll.setWidget(scroll_inner)
        outer.addWidget(scroll, 1)

        # Fixed bottom: code preview + doc links + buttons
        bottom = QWidget()
        bottom_layout = QVBoxLayout(bottom)
        bottom_layout.setContentsMargins(12, 4, 12, 10)
        bottom_layout.setSpacing(6)
        outer.addWidget(bottom)

        # Warn about custom code if present
        self.custom_warning = None
        self.btn_reset_custom = None
        self.custom_was_reset = False
        if action.is_custom and action.action_id != CUSTOM_ACTION_ID:
            self.custom_warning = QLabel("⚠ This action has custom code. Editing parameters will reset it.")
            self.custom_warning.setStyleSheet("color: orange; margin-bottom: 10px;")
            layout.addWidget(self.custom_warning)

            self.btn_reset_custom = QPushButton("Reset to Original")
            self.btn_reset_custom.clicked.connect(self.reset_custom)
            layout.addWidget(self.btn_reset_custom)

        # Show action docstring if available
        doc_label = QLabel(self.action_def.doc if self.action_def else "")
        doc_label.setWordWrap(True)
        doc_label.setStyleSheet("color: gray; margin-bottom: 10px;")
        layout.addWidget(doc_label)

        # Primary params
        self.form = QFormLayout()
        self.visible_params = visible_params
        self.param_rows: dict[str, int] = {}
        self.param_widgets: dict[str, QWidget] = {}
        row_idx = 0

        for param_name, param_def in visible_params.items():
            current_value = action.params.get(param_name, param_def.get("default"))

            # Look up a custom widget factory by param name
            binding = next((b for b in self.action_def.widget_bindings if b.param_name == param_name), None)
            factory = binding.factory if binding else None
            custom = factory(current_value, self.raw, self) if factory else None
            if custom is not None:
                container, value_widget = custom
                self.param_widgets[param_name] = value_widget
                self.form.addRow(param_def.get("label", param_name) + ":", container)
            else:
                widget = create_widget_for_param(param_def, current_value)
                self.param_widgets[param_name] = widget
                self.form.addRow(param_def.get("label", param_name) + ":", widget)

            self.param_rows[param_name] = row_idx
            row_idx += 1

        layout.addLayout(self.form)

        # Wire visibility
        controller_params: set[str] = set()
        for pdef in visible_params.values():
            vw = pdef.get("visible_when")
            if vw:
                controller_params |= set(vw.keys())
        for ctrl_name in controller_params:
            ctrl_widget = self.param_widgets.get(ctrl_name)
            if ctrl_widget is not None:
                connect_widget_signal(ctrl_widget, self.update_visibility)
        self.update_visibility()

        # Advanced params
        self.advanced_widgets: dict[str, dict[str, QWidget]] = {}  # func_name -> {param: widget}
        self.advanced_specs: dict[str, dict[str, dict]] = {}  # func_name -> {param: spec}
        self.advanced_group_box: QGroupBox | None = None
        self.advanced_toggle_btn: QPushButton | None = None
        self.build_advanced_section(layout)
        layout.addStretch()

        # Connect primary param signals
        for widget in self.param_widgets.values():
            connect_widget_signal(widget, self.update_code_preview)

        # ---- Fixed bottom section ----
        # Code preview
        bottom_layout.addWidget(QLabel("Generated code:"))
        self.code_preview = QTextEdit()
        self.code_preview.setReadOnly(True)
        self.code_preview.setMaximumHeight(100)
        self.code_preview.setFont(QFont("Consolas", 10))
        self.code_preview.setStyleSheet(
            """
            QTextEdit {
                background-color: #1E1E1E;
                color: #A9B7C6;
                border: 1px solid #3C3F41;
                border-radius: 4px;
                padding: 6px;
            }
        """
        )
        self.update_code_preview()
        bottom_layout.addWidget(self.code_preview)

        # MNE doc links
        if self.action_def and self.action_def.mne_doc_urls:
            doc_links = []
            bottom_layout.addWidget(QLabel("MNE documentation:"))
            for func, url in self.action_def.mne_doc_urls.items():
                link = f'<a href="{url}" style="color: #569CD6;">{func} docs</a>'
                doc_links.append(link)
            doc_label = QLabel(" • ".join(doc_links))
            doc_label.setOpenExternalLinks(True)
            bottom_layout.addWidget(doc_label)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        bottom_layout.addWidget(buttons)

    def build_advanced_section(self, parent_layout: QVBoxLayout):
        """Build the collapsible advanced params section."""

        if not self.action_def or not self.action_def.advanced_schema:
            return

        all_advanced = self.action_def.advanced_schema

        self.advanced_toggle_btn = QPushButton("Show Advanced")
        self.advanced_toggle_btn.setCheckable(True)
        parent_layout.addWidget(self.advanced_toggle_btn)

        group_box = QGroupBox("Advanced")
        self.advanced_group_box = group_box
        adv_layout = QVBoxLayout(group_box)

        for group_name, adv_params in all_advanced.items():
            if len(all_advanced) > 1:
                func_label = QLabel(f"<b>{group_name}</b>")
                adv_layout.addWidget(func_label)

            func_form = QFormLayout()
            self.advanced_widgets[group_name] = {}
            self.advanced_specs[group_name] = {}

            existing_advanced = self.action.advanced_params.get(group_name, {})

            for pname, pdef in adv_params.items():
                current = existing_advanced.get(pname, pdef.get("default"))
                widget = create_widget_for_param(pdef, current)
                self.advanced_widgets[group_name][pname] = widget
                self.advanced_specs[group_name][pname] = pdef
                func_form.addRow(pdef.get("label", pname) + ":", widget)
                connect_widget_signal(widget, self.update_code_preview)

            adv_layout.addLayout(func_form)

        parent_layout.addWidget(group_box)

        has_advanced = bool(self.action.advanced_params)
        group_box.setVisible(has_advanced)
        self.advanced_toggle_btn.setChecked(has_advanced)
        self.advanced_toggle_btn.setText("Hide Advanced" if has_advanced else "Show Advanced")
        self.advanced_toggle_btn.toggled.connect(self.on_toggle_advanced)

    def on_toggle_advanced(self, checked: bool):
        """Show or hide the advanced params group box.

        Args:
            checked: True when the section should be visible.
        """
        if self.advanced_group_box is None or self.advanced_toggle_btn is None:
            return
        self.advanced_group_box.setVisible(checked)
        self.advanced_toggle_btn.setText("Hide Advanced" if checked else "Show Advanced")

    def reset_custom(self):
        """Clear the action's custom code and restore generated-code mode."""
        if self.action.action_id == CUSTOM_ACTION_ID:
            return
        self.custom_was_reset = True
        self.action.custom_code = ""
        self.action.is_custom = False
        self.action.status = ActionStatus.PENDING
        self.update_code_preview()
        if self.custom_warning:
            self.custom_warning.hide()
        if self.btn_reset_custom:
            self.btn_reset_custom.setDisabled(True)

    def get_current_params(self) -> dict:
        """Read the current value of every primary param widget.

        Returns:
            Dict mapping param names to their current widget values.
        """
        params = {}
        for param_name, widget in self.param_widgets.items():
            params[param_name] = get_widget_value(widget)
        return params

    def get_advanced_params(self) -> dict:
        """Return advanced params grouped by group name, only non-default values."""

        kwargs_targets = self.action_def.kwargs_targets if self.action_def else {}
        if not kwargs_targets:
            return {}

        result: dict[str, dict] = {}
        for group_name, widgets in self.advanced_widgets.items():
            group_params: dict = {}
            for pname, widget in widgets.items():
                value = get_widget_value(widget)

                pdef = self.advanced_specs.get(group_name, {}).get(pname, {})
                default = pdef.get("default")

                # For nullable text params, keep empty input as None so that untouched fields won't get emitted as kwargs
                if isinstance(widget, QLineEdit):
                    if value == "" and (pdef.get("nullable") or default is None):
                        value = None

                if value != default:
                    group_params[pname] = value

            if group_params:
                result[group_name] = group_params

        return result

    def update_code_preview(self):
        """Regenerate the code preview from current widget values."""
        if self.action.is_custom and self.action.custom_code:
            code = self.action.custom_code
        else:
            temp_action = ActionConfig(
                self.action.action_id,
                self.get_current_params(),
                advanced_params=self.get_advanced_params(),
            )
            code = generate_action_code(temp_action, self.context_type)
        self.code_preview.setPlainText(code)

    def get_params(self) -> dict:
        """Return the accepted primary parameter values.

        Returns:
            Dict of param name -> value for all primary params.
        """
        return self.get_current_params()

    def should_clear_custom(self) -> bool:
        """Return True if the user chose to reset custom code during this session."""
        return self.custom_was_reset

    def update_visibility(self):
        """Apply per-parameter visible_when rules to primary form rows."""
        current = self.get_current_params()

        for param_name, param_def in self.visible_params.items():
            visible_when = param_def.get("visible_when")
            should_show = True

            if visible_when:
                for controller_name, allowed_values in visible_when.items():
                    if current.get(controller_name) not in allowed_values:
                        should_show = False
                        break

            row = self.param_rows.get(param_name)
            if row is None:
                continue

            self.form.setRowVisible(row, should_show)
