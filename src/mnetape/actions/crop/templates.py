"""Crop action templates.

Variants/Modes:
  absolute: crop to an explicit start/end time
  trim: remove a fixed duration from each end of the recording
"""

from __future__ import annotations

from typing import Annotated

import mne
from mnetape.actions.base import ParamMeta, builder

_tmin = ParamMeta(
    type="float",
    label="Start (s)",
    description="Start time in seconds.",
    default=0.0,
    min=0.0,
    decimals=3,
)
_tmax = ParamMeta(
    type="float",
    label="End (s)",
    description="End time in seconds.",
    default=0.0,
)
_trim_start = ParamMeta(
    type="float",
    label="Trim start (s)",
    description="Seconds to remove from the beginning of the recording.",
    default=0.0,
    min=0.0,
    decimals=3,
)
_trim_end = ParamMeta(
    type="float",
    label="Trim end (s)",
    description="Seconds to remove from the end of the recording.",
    default=0.0,
    min=0.0,
    decimals=3,
)


# ---------- Raw variants ----------

@builder(key="absolute")
def _raw_absolute(
    raw: mne.io.Raw,
    tmin: Annotated[float, _tmin] = 0.0,
    tmax: Annotated[float, _tmax] = 0.0,
    **kwargs,
) -> mne.io.Raw:
    raw.crop(tmin=tmin, tmax=tmax, **kwargs)
    return raw


@builder(key="trim")
def _raw_trim(
    raw: mne.io.Raw,
    trim_start: Annotated[float, _trim_start] = 0.0,
    trim_end: Annotated[float, _trim_end] = 0.0,
    **kwargs,
) -> mne.io.Raw:
    raw.crop(tmin=trim_start, tmax=raw.times[-1] - trim_end, **kwargs)
    return raw


# ---------- Epochs variants ----------

@builder(key="absolute")
def _epochs_absolute(
    epochs: mne.BaseEpochs,
    tmin: Annotated[float, _tmin] = 0.0,
    tmax: Annotated[float, _tmax] = 0.0,
    **kwargs,
) -> mne.BaseEpochs:
    epochs.crop(tmin=tmin, tmax=tmax, **kwargs)
    return epochs


@builder(key="trim")
def _epochs_trim(
    epochs: mne.BaseEpochs,
    trim_start: Annotated[float, _trim_start] = 0.0,
    trim_end: Annotated[float, _trim_end] = 0.0,
    **kwargs,
) -> mne.BaseEpochs:
    epochs.crop(tmin=epochs.times[0] + trim_start, tmax=epochs.times[-1] - trim_end, **kwargs)
    return epochs


# ---------- Evoked variants ----------

@builder(key="absolute")
def _evoked_absolute(
    evoked: mne.Evoked,
    tmin: Annotated[float, _tmin] = 0.0,
    tmax: Annotated[float, _tmax] = 0.0,
    **kwargs,
) -> mne.Evoked:
    evoked.crop(tmin=tmin, tmax=tmax, **kwargs)
    return evoked


@builder(key="trim")
def _evoked_trim(
    evoked: mne.Evoked,
    trim_start: Annotated[float, _trim_start] = 0.0,
    trim_end: Annotated[float, _trim_end] = 0.0,
    **kwargs,
) -> mne.Evoked:
    evoked.crop(tmin=evoked.times[0] + trim_start, tmax=evoked.times[-1] - trim_end, **kwargs)
    return evoked
