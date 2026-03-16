"""Resample action templates."""

from __future__ import annotations

from typing import Annotated

import mne
from mnetape.actions.base import ParamMeta, builder

_sfreq = ParamMeta(
    type="float",
    label="Target frequency (Hz)",
    description="New sample rate in Hz.",
    default=250,
    min=1,
    max=10000,
)

@builder
def resample_raw(
    raw: mne.io.Raw,
    sfreq: Annotated[float, _sfreq] = 250.0,
    **kwargs,
) -> mne.io.Raw:
    raw.resample(sfreq=sfreq, **kwargs)
    return raw

@builder
def resample_epochs(
    epochs: mne.BaseEpochs,
    sfreq: Annotated[float, _sfreq] = 250.0,
    **kwargs,
) -> mne.BaseEpochs:
    epochs.resample(sfreq=sfreq, **kwargs)
    return epochs

@builder
def resample_evoked(
    evoked: mne.Evoked,
    sfreq: Annotated[float, _sfreq] = 250.0,
    **kwargs,
) -> mne.Evoked:
    evoked.resample(sfreq=sfreq, **kwargs)
    return evoked
