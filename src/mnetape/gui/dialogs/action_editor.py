"""Dialog for editing action parameters.

ActionEditor is an Adw.Dialog that builds a form from an action's params_schema,
an optional advanced params section, and a live code-preview panel.
"""

from __future__ import annotations

import inspect
import logging

from gi.repository import Adw, Gtk

from mnetape.actions.registry import get_action_by_id, get_action_title
from mnetape.core.codegen import generate_action_code
from mnetape.core.models import CUSTOM_ACTION_ID, ActionConfig, ActionStatus, DataType
from mnetape.gui.dialogs.base import ModalDialog
from mnetape.gui.widgets import create_code_preview

logger = logging.getLogger(__name__)

# -------- Widget helpers --------

class NullableWidget(Gtk.Box):
    """A wrapper widget that adds a checkbox to enable/disable a param widget.

    When the checkbox is unchecked, the inner widget is disabled and get_value() returns None.
    """

    def __init__(self, inner: Gtk.Widget, has_value: bool):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self.inner = inner
        self.value_changed_cbs: list = []

        self.checkbox = Gtk.CheckButton()
        self.checkbox.set_active(has_value)
        inner.set_sensitive(has_value)
        if not has_value:
            inner.add_css_class("dim-widget")

        self.checkbox.connect("toggled", self.on_toggle)
        self.append(self.checkbox)
        self.append(inner)

        # Re-emit value changes from inner widget
        connect_inner_signal(inner, lambda: self.emit_changed())

    def on_toggle(self, _cb):
        checked = self.checkbox.get_active()
        self.inner.set_sensitive(checked)
        if checked:
            self.inner.remove_css_class("dim-widget")
        else:
            self.inner.add_css_class("dim-widget")
        self.emit_changed()

    def emit_changed(self):
        for cb in self.value_changed_cbs:
            cb()

    def connect_value_changed(self, cb):
        self.value_changed_cbs.append(cb)

    def get_value(self):
        if not self.checkbox.get_active():
            return None
        return get_widget_value(self.inner)

def connect_inner_signal(widget: Gtk.Widget, slot):
    """Connect the value-changed signal of a param widget to a slot."""
    if isinstance(widget, Gtk.SpinButton):
        widget.connect("value-changed", lambda *_: slot())
    elif isinstance(widget, Gtk.DropDown):
        widget.connect("notify::selected", lambda *_: slot())
    elif isinstance(widget, Gtk.CheckButton):
        widget.connect("toggled", lambda *_: slot())
    elif isinstance(widget, Gtk.Entry):
        widget.connect("changed", lambda *_: slot())
    elif hasattr(widget, "connect_value_changed"):
        widget.connect_value_changed(slot)

def create_widget_for_param(param_def: dict, current_value) -> Gtk.Widget:
    """Create an appropriate GTK widget for a single parameter definition.

    Args:
        param_def: Parameter metadata dict.
        current_value: The value to pre-populate the widget with.

    Returns:
        A configured Gtk.Widget, or None if the type is not supported.
    """
    nullable = param_def.get("nullable", False)
    display_value = current_value if current_value is not None else param_def.get("default")
    ptype = param_def.get("type", "text")

    if ptype == "float":
        adj = Gtk.Adjustment(
            value=0.0,
            lower=param_def.get("min", -999999.0),
            upper=param_def.get("max", 999999.0),
            step_increment=10 ** (-param_def.get("decimals", 2)),
            page_increment=1.0,
        )
        widget = Gtk.SpinButton(adjustment=adj, climb_rate=1.0, digits=param_def.get("decimals", 2))
        try:
            widget.set_value(float(display_value) if display_value is not None else 0.0)
        except (TypeError, ValueError):
            widget.set_value(float(param_def.get("default", 0.0)))
        inner = widget

    elif ptype == "int":
        adj = Gtk.Adjustment(
            value=0.0,
            lower=param_def.get("min", -999999),
            upper=param_def.get("max", 999999),
            step_increment=1.0,
            page_increment=10.0,
        )
        widget = Gtk.SpinButton(adjustment=adj, climb_rate=1.0, digits=0)
        try:
            widget.set_value(int(display_value) if display_value is not None else 0)
        except (TypeError, ValueError):
            widget.set_value(int(param_def.get("default", 0)))
        inner = widget

    elif ptype == "choice":
        choices = param_def.get("choices", [])
        model = Gtk.StringList(strings=choices)
        widget = Gtk.DropDown(model=model)
        try:
            idx = choices.index(str(display_value))
            widget.set_selected(idx)
        except (ValueError, TypeError):
            widget.set_selected(0)
        inner = widget

    elif ptype == "bool":
        widget = Gtk.CheckButton()
        widget.set_active(bool(display_value))
        inner = widget

    else:
        # text / fallback
        widget = Gtk.Entry()
        widget.set_text(str(display_value) if display_value is not None else "")
        inner = widget

    if nullable:
        return NullableWidget(inner, has_value=(current_value is not None))
    return widget

def get_widget_value(widget: Gtk.Widget):
    """Extract the current value from a param widget.

    Args:
        widget: A GTK widget created by create_widget_for_param.

    Returns:
        The widget's current value in its native Python type.
    """
    if isinstance(widget, NullableWidget):
        return widget.get_value()
    if isinstance(widget, Gtk.SpinButton):
        if widget.get_digits() == 0:
            return int(widget.get_value())
        return widget.get_value()
    if isinstance(widget, Gtk.DropDown):
        selected = widget.get_selected()
        model = widget.get_model()
        if model is not None and selected != Gtk.INVALID_LIST_POSITION:
            item = model.get_item(selected)
            if hasattr(item, "get_string"):
                return item.get_string()
        return ""
    if isinstance(widget, Gtk.CheckButton):
        return widget.get_active()
    if isinstance(widget, Gtk.Entry):
        return widget.get_text()
    # Generic custom widget
    if hasattr(widget, "get_value") and callable(widget.get_value):
        return widget.get_value()
    return None

def connect_widget_signal(widget: Gtk.Widget, slot):
    """Connect the value-changed signal of a param widget to a slot function."""
    if isinstance(widget, NullableWidget):
        widget.connect_value_changed(slot)
    else:
        connect_inner_signal(widget, slot)

# -------- Main dialog --------

class ActionEditor(ModalDialog):
    """Dialog for editing the parameters of a pipeline action.

    Builds a dynamic form from the action's params_schema, with an optional
    collapsible "Advanced" section. A read-only code preview updates live.

    This uses a modal Adw.Dialog (window-level) presented on a parent window.
    Results are returned via the accepted/rejected pattern with a callback.

    Args:
        action: The ActionConfig being edited.
        raw: The current MNE Raw object, or None.
        parent_window: The Adw.ApplicationWindow to present the dialog on.
        context_type: DataType flowing into this action.
    """

    def __init__(
        self,
        action: ActionConfig,
        raw=None,
        parent_window=None,
        context_type: DataType | None = None,
    ):
        self.action = action
        self.raw = raw
        self.context_type = context_type
        self.action_def = get_action_by_id(action.action_id)

        # Build dialog
        self.dialog = Adw.Dialog()
        self.dialog.set_title(f"Edit: {get_action_title(action)}")
        self.dialog.set_content_width(440)

        toolbar_view = Adw.ToolbarView()
        header = Adw.HeaderBar()
        toolbar_view.add_top_bar(header)

        self.dialog.set_child(toolbar_view)

        # Scrolled content
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_propagate_natural_height(True)
        scrolled.set_max_content_height(600)

        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        content_box.set_margin_start(16)
        content_box.set_margin_end(16)
        content_box.set_margin_top(12)
        content_box.set_margin_bottom(12)
        scrolled.set_child(content_box)
        toolbar_view.set_content(scrolled)

        self.visible_params = self.action_def.params_schema if self.action_def else {}
        self.param_rows: dict[str, Gtk.Widget] = {}  # row label widget
        self.param_widgets: dict[str, Gtk.Widget] = {}
        self.custom_was_reset = False

        # Custom code warning
        if action.is_custom and action.action_id != CUSTOM_ACTION_ID:
            warning = Gtk.Label(label="⚠ This action has custom code. Editing parameters will reset it.")
            warning.add_css_class("warning-label")
            warning.set_wrap(True)
            content_box.append(warning)

            reset_btn = Gtk.Button(label="Reset to Original")
            reset_btn.connect("clicked", self.on_reset_custom)
            content_box.append(reset_btn)
            self.reset_btn = reset_btn
            self.warning_label = warning
        else:
            self.reset_btn = None
            self.warning_label = None

        # Doc label
        if self.action_def and self.action_def.doc:
            doc_label = Gtk.Label(label=self.action_def.doc)
            doc_label.set_wrap(True)
            doc_label.add_css_class("dim-label")
            doc_label.set_xalign(0.0)
            content_box.append(doc_label)

        # Primary params form
        self.form_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        for param_name, param_def in self.visible_params.items():
            current_value = action.params.get(param_name, param_def.get("default"))

            binding = next((b for b in self.action_def.widget_bindings if b.param_name == param_name), None) \
                if self.action_def else None
            factory = binding.factory if binding else None
            if factory is not None:
                nparams = len(inspect.signature(factory).parameters)
                custom = factory(current_value, self.raw, self.param_widgets) if nparams >= 3 \
                    else factory(current_value, self.raw)
            else:
                custom = None

            if custom is not None:
                container, value_widget = custom
                self.param_widgets[param_name] = value_widget
                row = make_form_row(param_def.get("label", param_name), container)
            else:
                widget = create_widget_for_param(param_def, current_value)
                self.param_widgets[param_name] = widget
                row = make_form_row(param_def.get("label", param_name), widget)

            self.param_rows[param_name] = row
            self.form_box.append(row)

        content_box.append(self.form_box)

        # Wire visibility
        controller_params: set[str] = set()
        for pdef in self.visible_params.values():
            vw = pdef.get("visible_when")
            if vw:
                controller_params |= set(vw.keys())
        for ctrl_name in controller_params:
            ctrl_widget = self.param_widgets.get(ctrl_name)
            if ctrl_widget is not None:
                connect_widget_signal(ctrl_widget, self.update_visibility)
        self.update_visibility()

        # Advanced params
        self.advanced_widgets: dict[str, dict[str, Gtk.Widget]] = {}
        self.advanced_specs: dict[str, dict[str, dict]] = {}
        self.advanced_group: Gtk.Box | None = None
        self.advanced_expander: Gtk.Expander | None = None
        self.build_advanced_section(content_box)

        # Code preview
        lbl = Gtk.Label()
        lbl.set_markup("<b>Generated code:</b>")
        lbl.set_xalign(0.0)
        content_box.append(lbl)
        self.code_preview = create_code_preview()
        self.code_buf = self.code_preview.get_buffer()
        self.update_code_preview()
        code_scroll = Gtk.ScrolledWindow()
        code_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        code_scroll.set_size_request(-1, 120)
        code_scroll.set_child(self.code_preview)
        code_scroll.add_css_class("code-preview-scroll")
        content_box.append(code_scroll)

        # MNE doc links
        if self.action_def and self.action_def.mne_doc_urls:
            mne_lbl = Gtk.Label()
            mne_lbl.set_markup("<b>MNE documentation:</b>")
            mne_lbl.set_xalign(0.0)
            content_box.append(mne_lbl)
            links_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            for func, url in self.action_def.mne_doc_urls.items():
                btn = Gtk.LinkButton(uri=url, label=f"{func} docs")
                links_box.append(btn)
            content_box.append(links_box)

        # Connect primary param signals for live code preview
        for widget in self.param_widgets.values():
            connect_widget_signal(widget, self.update_code_preview)

        # Buttons row (outside scroll area so always visible)
        btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        btn_row.set_halign(Gtk.Align.END)
        btn_row.set_margin_start(16)
        btn_row.set_margin_end(16)
        btn_row.set_margin_top(8)
        btn_row.set_margin_bottom(8)

        cancel_btn = Gtk.Button(label="Cancel")
        cancel_btn.connect("clicked", self.reject)
        btn_row.append(cancel_btn)

        ok_btn = Gtk.Button(label="OK")
        ok_btn.add_css_class("suggested-action")
        ok_btn.connect("clicked", self.accept)
        btn_row.append(ok_btn)

        toolbar_view.add_bottom_bar(btn_row)

        self.setup_modal(parent_window)

    def build_advanced_section(self, parent_box: Gtk.Box):
        if not self.action_def or not self.action_def.advanced_schema:
            return

        all_advanced = self.action_def.advanced_schema

        expander = Gtk.Expander(label="Advanced")
        has_advanced = bool(self.action.advanced_params)
        expander.set_expanded(has_advanced)
        self.advanced_expander = expander

        adv_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        adv_box.set_margin_start(12)
        self.advanced_group = adv_box

        for group_name, adv_params in all_advanced.items():
            if len(all_advanced) > 1:
                lbl = Gtk.Label(label=f"<b>{group_name}</b>")
                lbl.set_use_markup(True)
                lbl.set_xalign(0.0)
                adv_box.append(lbl)

            self.advanced_widgets[group_name] = {}
            self.advanced_specs[group_name] = {}
            existing_advanced = self.action.advanced_params.get(group_name, {})

            for pname, pdef in adv_params.items():
                current = existing_advanced.get(pname, pdef.get("default"))
                widget = create_widget_for_param(pdef, current)
                self.advanced_widgets[group_name][pname] = widget
                self.advanced_specs[group_name][pname] = pdef
                row = make_form_row(pdef.get("label", pname), widget)
                adv_box.append(row)
                connect_widget_signal(widget, self.update_code_preview)

        expander.set_child(adv_box)
        parent_box.append(expander)

    def on_reset_custom(self, _btn):
        if self.action.action_id == CUSTOM_ACTION_ID:
            return
        self.custom_was_reset = True
        self.action.custom_code = ""
        self.action.is_custom = False
        self.action.status = ActionStatus.PENDING
        self.update_code_preview()
        if self.warning_label:
            self.warning_label.set_visible(False)
        if self.reset_btn:
            self.reset_btn.set_sensitive(False)

    def get_current_params(self) -> dict:
        params = {}
        for param_name, widget in self.param_widgets.items():
            params[param_name] = get_widget_value(widget)
        return params

    def get_current_advanced_params(self) -> dict:
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

                if isinstance(widget, Gtk.Entry):
                    if value == "" and (pdef.get("nullable") or default is None):
                        value = None

                if value != default:
                    group_params[pname] = value

            if group_params:
                result[group_name] = group_params

        return result

    def update_code_preview(self):
        if self.action.is_custom and self.action.custom_code:
            code = self.action.custom_code
        else:
            temp_action = ActionConfig(
                self.action.action_id,
                self.get_current_params(),
                advanced_params=self.get_current_advanced_params(),
            )
            code = generate_action_code(temp_action, self.context_type)
        self.code_buf.set_text(code, -1)

    def update_visibility(self):
        current = self.get_current_params()
        for param_name, param_def in self.visible_params.items():
            visible_when = param_def.get("visible_when")
            should_show = True
            if visible_when:
                for controller_name, allowed_values in visible_when.items():
                    if current.get(controller_name) not in allowed_values:
                        should_show = False
                        break
            row_widget = self.param_rows.get(param_name)
            if row_widget is not None:
                row_widget.set_visible(should_show)

    # -------- Public exec-style interface --------

    def get_params(self) -> dict:
        return self.get_current_params()

    def get_advanced_params(self) -> dict:
        return self.get_current_advanced_params()

    def should_clear_custom(self) -> bool:
        return self.custom_was_reset

def make_form_row(label_text: str, widget: Gtk.Widget) -> Gtk.Box:
    """Create a horizontal label+widget form row."""
    row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    lbl = Gtk.Label(label=label_text + ":")
    lbl.set_size_request(160, -1)
    lbl.set_xalign(1.0)
    row.append(lbl)
    widget.set_hexpand(True)
    row.append(widget)
    return row
