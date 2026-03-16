"""Drop bad epochs action widgets."""

from __future__ import annotations

import logging


from gi.repository import Adw, Gtk

from mnetape.actions.base import ParamWidgetBinding
from mnetape.gui.dialogs.base import ModalDialog

logger = logging.getLogger(__name__)

# -------- Unit / default tables --------

UNITS: dict[str, tuple[str, float]] = {
    "eeg":  ("µV",    1e-6),
    "eog":  ("µV",    1e-6),
    "emg":  ("µV",    1e-6),
    "ecg":  ("µV",    1e-6),
    "mag":  ("fT",    1e-15),
    "grad": ("fT/cm", 1e-13),
}

REJECT_DEFAULTS: dict[str, float] = {
    "eeg":  150.0,
    "eog":  250.0,
    "emg":  500.0,
    "ecg":  500.0,
    "mag":  3000.0,
    "grad": 3500.0,
}

FLAT_DEFAULTS: dict[str, float] = {
    "eeg":  0.1,
    "eog":  0.1,
    "emg":  0.1,
    "ecg":  0.1,
    "mag":  0.1,
    "grad": 0.1,
}

# -------- ThresholdsValueWidget --------

class ThresholdsValueWidget(Gtk.Box):
    """Hidden value widget storing a thresholds dict (SI units) or None."""

    def __init__(self, value: dict | None):
        super().__init__()
        self.set_visible(False)
        self.value = value
        self.changed_cbs: list = []

    def set_value(self, v: dict | None):
        self.value = v
        for cb in self.changed_cbs:
            cb()

    def get_value(self) -> dict | None:
        return self.value

    def connect_value_changed(self, cb):
        self.changed_cbs.append(cb)

# -------- ThresholdsDialog --------

class ThresholdsDialog(ModalDialog):
    """Dialog for configuring per-channel-type amplitude thresholds."""

    def __init__(
        self,
        raw,
        current_value: dict | None,
        defaults: dict[str, float],
        title: str,
        parent_window=None,
    ):
        self.dialog = Adw.Dialog()
        self.dialog.set_title(title)
        self.dialog.set_content_width(380)

        toolbar_view = Adw.ToolbarView()
        toolbar_view.add_top_bar(Adw.HeaderBar())
        self.dialog.set_child(toolbar_view)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        content.set_margin_start(16)
        content.set_margin_end(16)
        content.set_margin_top(12)
        content.set_margin_bottom(12)
        toolbar_view.set_content(content)

        present_types = sorted(set(raw.get_channel_types())) if raw is not None else list(UNITS.keys())
        supported_types = [t for t in present_types if t in UNITS]

        self.rows: dict[str, tuple[Gtk.CheckButton, Gtk.SpinButton]] = {}

        if not supported_types:
            content.append(Gtk.Label(label="No supported channel types found in data."))
        else:
            content.append(Gtk.Label(label="Set a threshold for each channel type to reject:"))

            for ch_type in supported_types:
                unit, factor = UNITS[ch_type]

                cb = Gtk.CheckButton()
                adj = Gtk.Adjustment(value=0.0, lower=0.001, upper=999999.0,
                                     step_increment=0.001, page_increment=1.0)
                spinbox = Gtk.SpinButton(adjustment=adj, climb_rate=1.0, digits=3)

                if current_value is not None and ch_type in current_value:
                    cb.set_active(True)
                    spinbox.set_value(current_value[ch_type] / factor)
                else:
                    cb.set_active(current_value is None)
                    spinbox.set_value(defaults.get(ch_type, 100.0))

                spinbox.set_sensitive(cb.get_active())
                cb.connect("toggled", lambda _cb, sb=spinbox: sb.set_sensitive(_cb.get_active()))

                row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
                row.append(cb)
                lbl = Gtk.Label(label=f"{ch_type} ({unit}):")
                lbl.set_size_request(90, -1)
                lbl.set_xalign(0.0)
                row.append(lbl)
                spinbox.set_hexpand(True)
                row.append(spinbox)
                content.append(row)
                self.rows[ch_type] = (cb, spinbox)

        btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        btn_row.set_halign(Gtk.Align.END)
        cancel_btn = Gtk.Button(label="Cancel")
        cancel_btn.connect("clicked", self.reject)
        ok_btn = Gtk.Button(label="OK")
        ok_btn.add_css_class("suggested-action")
        ok_btn.connect("clicked", self.accept)
        btn_row.append(cancel_btn)
        btn_row.append(ok_btn)
        content.append(btn_row)

        self.setup_modal(parent_window)

    def get_value(self) -> dict | None:
        result = {}
        for ch_type, (cb, spinbox) in self.rows.items():
            if cb.get_active():
                _, factor = UNITS[ch_type]
                result[ch_type] = float(f"{spinbox.get_value() * factor:.10g}")
        return result if result else None

# -------- Factories --------

def make_summary(value: dict | None) -> str:
    if not value:
        return ""
    parts = []
    for ch_type, si_val in sorted(value.items()):
        if ch_type in UNITS:
            unit, factor = UNITS[ch_type]
            parts.append(f"{ch_type}: {si_val / factor:.1f} {unit}")
        else:
            parts.append(f"{ch_type}: {si_val:.2e}")
    return ",  ".join(parts)

def build_defaults_dict(raw, defaults: dict[str, float]) -> dict:
    present = set(raw.get_channel_types()) if raw is not None else set(UNITS.keys())
    return {
        t: float(f"{defaults[t] * UNITS[t][1]:.10g}")
        for t in present
        if t in defaults and t in UNITS
    }

def thresholds_factory(defaults: dict[str, float], title: str = "Thresholds"):
    """Return a param widget factory for a threshold dict param."""

    def factory(current_value, raw):
        value_widget = ThresholdsValueWidget(current_value)

        toggle = Gtk.CheckButton()
        toggle.set_active(current_value is not None)

        summary_label = Gtk.Label(label=make_summary(current_value))
        summary_label.set_xalign(0.0)
        summary_label.set_hexpand(True)
        btn = Gtk.Button(label="Configure…")

        def set_active(enabled: bool):
            summary_label.set_sensitive(enabled)
            btn.set_sensitive(enabled)

        set_active(current_value is not None)

        def on_toggle(_cb):
            checked = toggle.get_active()
            if checked and value_widget.get_value() is None:
                value_widget.set_value(build_defaults_dict(raw, defaults))
                summary_label.set_text(make_summary(value_widget.get_value()))
            elif not checked:
                value_widget.set_value(None)
                summary_label.set_text("")
            set_active(checked)

        toggle.connect("toggled", on_toggle)

        def open_dialog(_btn):
            parent_window = None
            widget = btn
            while widget is not None:
                parent_window = widget.get_root()
                break
            dlg = ThresholdsDialog(
                raw=raw,
                current_value=value_widget.get_value(),
                defaults=defaults,
                title=title,
                parent_window=parent_window,
            )
            if dlg.exec():
                new_val = dlg.get_value()
                value_widget.set_value(new_val)
                summary_label.set_text(make_summary(new_val))
                if new_val is None:
                    toggle.set_active(False)

        btn.connect("clicked", open_dialog)

        container = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        container.set_hexpand(True)
        container.append(toggle)
        container.append(summary_label)
        container.append(btn)

        return container, value_widget

    return factory

reject_thresholds_factory = thresholds_factory(REJECT_DEFAULTS, title="Reject thresholds")
flat_thresholds_factory = thresholds_factory(FLAT_DEFAULTS, title="Flat thresholds")

# -------- Widget bindings --------

WIDGET_BINDINGS = [
    ParamWidgetBinding("reject", reject_thresholds_factory),
    ParamWidgetBinding("flat", flat_thresholds_factory),
]
