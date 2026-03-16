"""Shared GUI utility helpers."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

def refresh_mne_browser_bads(widget, bads: set[str], filter_names: set[str] | None = None) -> None:
    """Refresh trace colors in an MNE browser widget given a set of bad channel names.

    Args:
        widget: An embedded MNE browser widget that exposes a `.mne` namespace.
        bads: Set of channel names currently marked as bad.
        filter_names: When provided, only update traces whose ch_name is in this set.
    """
    try:
        if not hasattr(widget, "mne"):
            return

        if filter_names is not None:
            widget.mne.info["bads"] = [
                name for name in widget.mne.info.get("bads", [])
                if name not in filter_names
            ] + sorted(bads)

        if hasattr(widget, "_update_yaxis_labels"):
            widget._update_yaxis_labels()
        if hasattr(widget, "_redraw"):
            widget._redraw(update_data=False)
        elif hasattr(widget, "canvas") and hasattr(widget.canvas, "draw_idle"):
            widget.canvas.draw_idle()
    except Exception as e:
        logger.debug("Failed to refresh MNE browser bad channels: %s", e, exc_info=True)
