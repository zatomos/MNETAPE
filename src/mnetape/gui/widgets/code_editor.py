"""GtkSource.View code editor factory with a dark IDE theme and Python syntax highlighting.

Exports a single factory function, create_code_editor(), that returns a fully configured GtkSource.View instance.
"""

from __future__ import annotations

from gi.repository import Gtk, GtkSource

def apply_python_highlighting(buffer: GtkSource.Buffer) -> None:
    """Configure Python syntax highlighting and a dark color scheme on a GtkSource.Buffer."""
    lang_manager = GtkSource.LanguageManager.get_default()
    python_lang = lang_manager.get_language("python3") or lang_manager.get_language("python")
    if python_lang:
        buffer.set_language(python_lang)
    buffer.set_highlight_syntax(True)

    scheme_manager = GtkSource.StyleSchemeManager.get_default()
    for scheme_id in ("Adwaita-dark", "oblivion", "solarized-dark", "classic-dark"):
        scheme = scheme_manager.get_scheme(scheme_id)
        if scheme is not None:
            buffer.set_style_scheme(scheme)
            break


def create_code_preview() -> GtkSource.View:
    """Create a read-only GtkSource.View with Python syntax highlighting.

    Suitable for embedding a code preview panel where the user should not edit
    the content. Uses the same dark color scheme as create_code_editor().

    Returns:
        A fully configured read-only GtkSource.View instance.
    """
    buffer = GtkSource.Buffer()
    apply_python_highlighting(buffer)

    view = GtkSource.View.new_with_buffer(buffer)
    view.set_editable(False)
    view.set_cursor_visible(False)
    view.set_show_line_numbers(False)
    view.set_wrap_mode(Gtk.WrapMode.NONE)
    view.set_monospace(True)
    view.set_left_margin(8)
    view.set_right_margin(8)
    view.set_top_margin(6)
    view.set_bottom_margin(6)
    view.set_hexpand(True)

    provider = Gtk.CssProvider()
    provider.load_from_data(b"textview { font-size: 11pt; }")
    view.get_style_context().add_provider(provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    return view


def create_code_editor() -> GtkSource.View:
    """Create a GtkSource.View editor with a dark theme and Python syntax highlighting.

    Returns:
        A fully configured GtkSource.View instance ready to embed in a layout.
    """
    buffer = GtkSource.Buffer()
    apply_python_highlighting(buffer)

    view = GtkSource.View.new_with_buffer(buffer)
    view.set_show_line_numbers(True)
    view.set_auto_indent(True)
    view.set_indent_width(4)
    view.set_insert_spaces_instead_of_tabs(True)
    view.set_tab_width(4)
    view.set_wrap_mode(Gtk.WrapMode.NONE)
    view.set_monospace(True)

    # Font size via CSS (override_font was removed in GTK4)
    provider = Gtk.CssProvider()
    provider.load_from_data(b"textview { font-size: 11pt; }")
    view.get_style_context().add_provider(provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    view.set_hexpand(True)
    view.set_vexpand(True)

    return view
