"""Shared GUI utility helpers."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def refresh_mne_browser_bads(widget, bads: set[str], filter_names: set[str] | None = None) -> None:
    """Refresh trace colors in a MNE browser widget given a set of bad channel names.

    Args:
        widget: An embedded MNE browser widget that exposes a `.mne` namespace.
        bads: Set of channel names currently marked as bad.
        filter_names: When provided, only update traces whose ch_name is in this set.
    """
    try:
        traces = getattr(widget.mne, "traces", [])
        if traces:
            for trace in traces:
                if filter_names is not None and trace.ch_name not in filter_names:
                    continue
                trace.isbad = trace.ch_name in bads
                if hasattr(trace, "update_color"):
                    trace.update_color()
            if hasattr(widget, "update_yaxis_labels"):
                widget.update_yaxis_labels()
        elif hasattr(widget, "_redraw"):
            widget._redraw(update_data=False)
        if hasattr(widget, "update"):
            widget.update()
    except Exception as e:
        logger.debug("Failed to refresh MNE browser bad channels: %s", e, exc_info=True)
