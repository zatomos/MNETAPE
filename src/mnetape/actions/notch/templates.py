"""Notch filter action templates."""

from __future__ import annotations

from typing import Annotated

import mne
from mnetape.actions.base import ParamMeta, builder

_freqs = ParamMeta(
    type="float",
    label="Frequency (Hz)",
    description="Base frequency of the line noise.",
    default=50,
    min=1,
    max=500,
)
_harmonics = ParamMeta(
    type="int",
    label="Harmonics",
    description="Number of harmonics to include.",
    default=3,
    min=1,
    max=10,
)


@builder
def notch_raw(
    raw: mne.io.Raw,
    freqs: Annotated[float, _freqs] = 50.0,
    harmonics: Annotated[int, _harmonics] = 3,
    **kwargs,
) -> mne.io.Raw:
    freqs_list = [freqs * (i + 1) for i in range(harmonics)]
    raw.notch_filter(freqs=freqs_list, **kwargs)
    return raw


@builder
def notch_epochs(
    epochs: mne.BaseEpochs,
    freqs: Annotated[float, _freqs] = 50.0,
    harmonics: Annotated[int, _harmonics] = 3,
    **kwargs,
) -> mne.BaseEpochs:
    freqs_list = [freqs * (i + 1) for i in range(harmonics)]
    epochs.notch_filter(freqs=freqs_list, **kwargs)
    return epochs


@builder
def notch_evoked(
    evoked: mne.Evoked,
    freqs: Annotated[float, _freqs] = 50.0,
    harmonics: Annotated[int, _harmonics] = 3,
    **kwargs,
) -> mne.Evoked:
    freqs_list = [freqs * (i + 1) for i in range(harmonics)]
    evoked.notch_filter(freqs=freqs_list, **kwargs)
    return evoked
