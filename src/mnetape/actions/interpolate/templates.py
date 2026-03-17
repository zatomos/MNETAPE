"""Interpolate bad channels action templates."""

from __future__ import annotations

import mne
from mnetape.actions.base import builder

@builder
def interpolate_raw(raw: mne.io.Raw, **kwargs) -> mne.io.Raw:
    raw.interpolate_bads(reset_bads=True, **kwargs)
    return raw

@builder
def interpolate_epochs(epochs: mne.BaseEpochs, **kwargs) -> mne.BaseEpochs:
    epochs.interpolate_bads(reset_bads=True, **kwargs)
    return epochs

@builder
def interpolate_evoked(evoked: mne.Evoked, **kwargs) -> mne.Evoked:
    evoked.interpolate_bads(reset_bads=True, **kwargs)
    return evoked
