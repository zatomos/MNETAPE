"""GTK4 / libadwaita compatibility helpers.

This module provides version-check utilities now that GTK4 is the primary UI framework.
"""

from __future__ import annotations

from gi.repository import Gtk

def check_gtk_version(required_major: int = 4, required_minor: int = 0) -> bool:
    """Return True when the installed GTK version meets the minimum requirement.

    Args:
        required_major: Minimum GTK major version (default 4).
        required_minor: Minimum GTK minor version (default 0).

    Returns:
        True if the installed GTK is at least required_major.required_minor.
    """
    major = Gtk.get_major_version()
    minor = Gtk.get_minor_version()
    return (major, minor) >= (required_major, required_minor)
