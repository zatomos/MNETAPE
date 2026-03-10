"""Crop action templates."""

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
    label="End (s)",
    description="End time in seconds.",
    default=0.0,
)


@builder
def crop_raw(
    raw: mne.io.Raw,
    tmin: Annotated[float, _tmin] = 0.0,
    tmax: Annotated[float, _tmax] = 0.0,
    **kwargs,
) -> mne.io.Raw:
    raw.crop(tmin=tmin, tmax=tmax, **kwargs)
    return raw


@builder
def crop_epochs(
    epochs: mne.BaseEpochs,
    tmin: Annotated[float, _tmin] = 0.0,
    tmax: Annotated[float, _tmax] = 0.0,
    **kwargs,
) -> mne.BaseEpochs:
    epochs.crop(tmin=tmin, tmax=tmax, **kwargs)
    return epochs


@builder
def crop_evoked(
    evoked: mne.Evoked,
    tmin: Annotated[float, _tmin] = 0.0,
    tmax: Annotated[float, _tmax] = 0.0,
    **kwargs,
) -> mne.Evoked:
    evoked.crop(tmin=tmin, tmax=tmax, **kwargs)
    return evoked
