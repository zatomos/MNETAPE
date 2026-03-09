"""Interpolate bad channels action templates."""

from __future__ import annotations

import mne
from mnetape.actions.base import builder


@builder
def template_builder(raw: mne.io.Raw, **kwargs) -> mne.io.Raw:
    raw.interpolate_bads(reset_bads=True, **kwargs)
    return raw
