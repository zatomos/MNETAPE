"""Bandpass filter action templates."""

from __future__ import annotations

from typing import Annotated

import mne
from mnetape.actions.base import ParamMeta, builder

_l_freq = ParamMeta(
    type="float",
    label="Highpass (Hz)",
    description="Low cutoff frequency in Hz. Set to 0 for no highpass.",
    default=0.5,
    min=0,
    max=50,
    nullable=True,
)
_h_freq = ParamMeta(
    type="float",
    label="Lowpass (Hz)",
    description="High cutoff frequency in Hz.",
    default=45.0,
    min=1,
    max=500,
    nullable=True,
)


@builder
def filter_raw(
    raw: mne.io.Raw,
    l_freq: Annotated[float | None, _l_freq] = 0.5,
    h_freq: Annotated[float | None, _h_freq] = 45.0,
    **kwargs,
) -> mne.io.Raw:
    raw.filter(l_freq=l_freq, h_freq=h_freq, **kwargs)
    return raw


@builder
def filter_epochs(
    epochs: mne.BaseEpochs,
    l_freq: Annotated[float | None, _l_freq] = 0.5,
    h_freq: Annotated[float | None, _h_freq] = 45.0,
    **kwargs,
) -> mne.BaseEpochs:
    epochs.filter(l_freq=l_freq, h_freq=h_freq, **kwargs)
    return epochs


@builder
def filter_evoked(
    evoked: mne.Evoked,
    l_freq: Annotated[float | None, _l_freq] = 0.5,
    h_freq: Annotated[float | None, _h_freq] = 45.0,
    **kwargs,
) -> mne.Evoked:
    evoked.filter(l_freq=l_freq, h_freq=h_freq, **kwargs)
    return evoked
