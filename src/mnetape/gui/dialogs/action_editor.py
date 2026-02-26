"""Dialog for editing action parameters.

ActionEditor is a QDialog that builds a form from an action's params_schema, an optional advanced params section,
and a live code-preview panel. It supports both full-action editing and step-level editing.
"""

import logging

from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QAbstractSpinBox,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
import mne

from mnetape.actions.registry import get_action_by_id, get_action_title
from mnetape.actions.introspect import get_advanced_params
from mnetape.core.codegen import generate_action_code
from mnetape.core.models import ActionConfig, ActionStatus

logger = logging.getLogger(__name__)


# -------- Widget creation helpers --------

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
    ptype = param_def.get("type", "text")

    if ptype == "float":
        widget = QDoubleSpinBox()
        widget.setRange(param_def.get("min", -999999), param_def.get("max", 999999))
        widget.setDecimals(param_def.get("decimals", 2))
        widget.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.UpDownArrows)
        try:
            widget.setValue(float(current_value) if current_value is not None else 0.0)
        except (TypeError, ValueError):
            widget.setValue(param_def.get("default", 0.0))
        return widget

    if ptype == "int":
        widget = QSpinBox()
        widget.setRange(param_def.get("min", -999999), param_def.get("max", 999999))
        widget.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.UpDownArrows)
        try:
            widget.setValue(int(current_value) if current_value is not None else 0)
        except (TypeError, ValueError):
            widget.setValue(param_def.get("default", 0))
        return widget

    if ptype == "choice":
        widget = QComboBox()
        widget.addItems(param_def.get("choices", []))
        widget.setCurrentText(str(current_value))
        return widget

    if ptype == "bool":
        widget = QCheckBox()
        widget.setChecked(bool(current_value))
        return widget

    # text / fallback
    widget = QLineEdit(str(current_value) if current_value is not None else "")
    return widget


def get_widget_value(widget):
    """Extract the current value from a param widget.

    Args:
        widget: A Qt widget created by create_widget_for_param.

    Returns:
        The widget's current value in its native Python type, or None for unrecognized widget types.
    """
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
    if isinstance(widget, (QSpinBox, QDoubleSpinBox)):
        widget.valueChanged.connect(slot)
    elif isinstance(widget, QComboBox):
        widget.currentTextChanged.connect(slot)
    elif isinstance(widget, QCheckBox):
        widget.stateChanged.connect(slot)
    elif isinstance(widget, QLineEdit):
        widget.textChanged.connect(slot)


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
        step_idx: When set, restricts editing to the params of this step.
    """

    def __init__(
        self,
        action: ActionConfig,
        raw: mne.io.Raw | None = None,
        parent=None,
        step_idx: int | None = None,
    ):
        super().__init__(parent)
        self.action = action
        self.raw = raw
        self.action_def = get_action_by_id(action.action_id)
        self.step_idx = step_idx
        self.step_def = None

        # Determine title and which params to show
        if step_idx is not None and self.action_def and self.action_def.steps:
            self.step_def = self.action_def.steps[step_idx]
            self.setWindowTitle(f"Edit: {self.step_def.title}")

            # Filter params_schema to only this step's params
            step_params = (
                self.step_def.template_schema.all_primary_params()
                if self.step_def.template_schema
                else {}
            )

            visible_params = step_params
        else:
            self.setWindowTitle(f"Edit: {get_action_title(action)}")
            visible_params = self.action_def.params_schema if self.action_def else {}

        self.setMinimumWidth(400)

        layout = QVBoxLayout(self)

        # Warn about custom code if present
        self.custom_warning = None
        self.btn_reset_custom = None
        self.custom_was_reset = False
        if action.is_custom and action.action_id != "custom":
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
        form = QFormLayout()
        self.param_widgets: dict[str, QWidget] = {}

        for param_name, param_def in visible_params.items():
            current_value = action.params.get(param_name, param_def.get("default"))
            if (
                param_name == "ch_names"
                and param_def.get("type") == "channels"
                and (current_value is None or current_value == "")
            ):
                current_value = action.params.get("channels", current_value)

            # Allow action definitions to specify custom widget factories for specific param types
            factory = (self.action_def.param_widget_factories or {}).get(param_def.get("type"))
            custom = factory(param_def, current_value, self.raw, self) if factory else None
            if custom is not None:
                container, value_widget = custom
                self.param_widgets[param_name] = value_widget
                form.addRow(param_def.get("label", param_name) + ":", container)
                continue

            widget = create_widget_for_param(param_def, current_value)
            self.param_widgets[param_name] = widget
            form.addRow(param_def.get("label", param_name) + ":", widget)

        layout.addLayout(form)

        # Advanced params
        self.advanced_widgets: dict[str, dict[str, QWidget]] = {}  # func_name -> {param: widget}
        self.advanced_specs: dict[str, dict[str, dict]] = {}  # func_name -> {param: spec}
        self.advanced_group_box: QGroupBox | None = None
        self.advanced_toggle_btn: QPushButton | None = None
        self.build_advanced_section(layout)

        # Code preview
        layout.addWidget(QLabel("Generated code:"))
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
        layout.addWidget(self.code_preview)

        # MNE doc links
        if self.action_def and self.action_def.mne_doc_urls:
            doc_links = []
            # Add title
            layout.addWidget(QLabel("MNE documentation:"))
            for func, url in self.action_def.mne_doc_urls.items():
                link = f'<a href="{url}" style="color: #569CD6;">{func} docs</a>'
                doc_links.append(link)
            doc_label = QLabel(" • ".join(doc_links))
            doc_label.setOpenExternalLinks(True)
            layout.addWidget(doc_label)

        # Connect primary param signals
        for widget in self.param_widgets.values():
            connect_widget_signal(widget, self.update_code_preview)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def build_advanced_section(self, parent_layout: QVBoxLayout):
        """Build the collapsible advanced params section."""

        # Use step-specific schema when editing a single step
        if self.step_def is not None:
            schema = self.step_def.template_schema
        elif self.action_def:
            schema = self.action_def.template_schema
        else:
            schema = None

        if not schema:
            return

        primary_names = frozenset(self.action_def.params_schema.keys())

        # Collect advanced params from all function groups
        all_advanced: dict[str, dict[str, dict]] = {}  # func -> {param: spec}
        for group in schema.function_groups:
            group_primary = frozenset(group.params.keys())
            adv = get_advanced_params(group.dotted_name, primary_names | group_primary)
            if adv:
                all_advanced[group.dotted_name] = adv

        if not all_advanced:
            return

        self.advanced_toggle_btn = QPushButton("Show Advanced")
        self.advanced_toggle_btn.setCheckable(True)
        parent_layout.addWidget(self.advanced_toggle_btn)

        group_box = QGroupBox("Advanced")
        self.advanced_group_box = group_box
        adv_layout = QVBoxLayout(group_box)

        for func_name, adv_params in all_advanced.items():
            if len(all_advanced) > 1:
                func_label = QLabel(f"<b>{func_name}</b>")
                adv_layout.addWidget(func_label)

            func_form = QFormLayout()
            self.advanced_widgets[func_name] = {}
            self.advanced_specs[func_name] = {}

            existing_advanced = self.action.advanced_params.get(func_name, {})

            for pname, pdef in adv_params.items():
                current = existing_advanced.get(pname, pdef.get("default"))
                widget = create_widget_for_param(pdef, current)
                self.advanced_widgets[func_name][pname] = widget
                self.advanced_specs[func_name][pname] = pdef
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
        """Show or hide the advanced params group box and resize the dialog.

        Args:
            checked: True when the section should be visible.
        """
        if self.advanced_group_box is None or self.advanced_toggle_btn is None:
            return
        self.advanced_group_box.setVisible(checked)
        self.advanced_toggle_btn.setText("Hide Advanced" if checked else "Show Advanced")
        if not checked:
            self.resize(self.width(), self.minimumSizeHint().height())
        self.adjustSize()

    def reset_custom(self):
        """Clear the action's custom code and restore generated-code mode."""
        if self.action.action_id == "custom":
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
        """Return advanced params grouped by function name, only non-default values."""

        # When editing a specific step, the schema lives on the step, not the action.
        if self.step_def is not None:
            schema = self.step_def.template_schema
        elif self.action_def:
            schema = self.action_def.template_schema
        else:
            schema = None
        if not schema:
            return {}

        result: dict[str, dict] = {}
        for func_name, widgets in self.advanced_widgets.items():
            func_params: dict = {}
            for pname, widget in widgets.items():
                value = get_widget_value(widget)

                pdef = self.advanced_specs.get(func_name, {}).get(pname, {})
                default = pdef.get("default")

                # For nullable text params, keep empty input as None so that untouched fields won't get emitted as kwargs
                if isinstance(widget, QLineEdit):
                    if value == "" and (pdef.get("nullable") or default is None):
                        value = None

                if value != default:
                    func_params[pname] = value

            if func_params:
                result[func_name] = func_params

        return result

    def update_code_preview(self):
        """Regenerate the code preview from current widget values."""
        if self.action.is_custom and self.action.custom_code:
            code = self.action.custom_code
        elif self.step_def is not None and self.step_def.code_builder:
            # Show only this step's generated code
            all_params = {**self.action.params, **self.get_current_params()}
            code = self.step_def.code_builder(all_params)
        else:
            temp_action = ActionConfig(
                self.action.action_id,
                self.get_current_params(),
                advanced_params=self.get_advanced_params(),
            )
            code = generate_action_code(temp_action)
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
