"""Crop action templates."""

from __future__ import annotations

from typing import Annotated

import mne
from mnetape.actions.base import ParamMeta, builder

_crop_mode = ParamMeta(
    type="choice",
    label="Mode",
    choices=["absolute", "trim"],
    default="absolute",
)
_tmin = ParamMeta(
    type="float",
    label="Start (s)",
    description="Start time in seconds.",
    default=0.0,
    min=0.0,
    decimals=3,
    visible_when={"crop_mode": ["absolute"]},
)
_tmax = ParamMeta(
    type="float",
    label="End (s)",
    description="End time in seconds.",
    default=0.0,
    visible_when={"crop_mode": ["absolute"]},
)
_trim_start = ParamMeta(
    type="float",
    label="Trim start (s)",
    description="Seconds to remove from the beginning of the recording.",
    default=0.0,
    min=0.0,
    decimals=3,
    visible_when={"crop_mode": ["trim"]},
)
_trim_end = ParamMeta(
    type="float",
    label="Trim end (s)",
    description="Seconds to remove from the end of the recording.",
    default=0.0,
    min=0.0,
    decimals=3,
    visible_when={"crop_mode": ["trim"]},
)


@builder
def crop_raw(
    raw: mne.io.Raw,
    crop_mode: Annotated[str, _crop_mode] = "absolute",
    tmin: Annotated[float, _tmin] = 0.0,
    tmax: Annotated[float, _tmax] = 0.0,
    trim_start: Annotated[float, _trim_start] = 0.0,
    trim_end: Annotated[float, _trim_end] = 0.0,
    **kwargs,
) -> mne.io.Raw:
    if crop_mode == "trim":
        raw.crop(tmin=trim_start, tmax=raw.times[-1] - trim_end, **kwargs)
    else:
        raw.crop(tmin=tmin, tmax=tmax, **kwargs)
    return raw


@builder
def crop_epochs(
    epochs: mne.BaseEpochs,
    crop_mode: Annotated[str, _crop_mode] = "absolute",
    tmin: Annotated[float, _tmin] = 0.0,
    tmax: Annotated[float, _tmax] = 0.0,
    trim_start: Annotated[float, _trim_start] = 0.0,
    trim_end: Annotated[float, _trim_end] = 0.0,
    **kwargs,
) -> mne.BaseEpochs:
    if crop_mode == "trim":
        epochs.crop(tmin=epochs.times[0] + trim_start, tmax=epochs.times[-1] - trim_end, **kwargs)
    else:
        epochs.crop(tmin=tmin, tmax=tmax, **kwargs)
    return epochs


@builder
def crop_evoked(
    evoked: mne.Evoked,
    crop_mode: Annotated[str, _crop_mode] = "absolute",
    tmin: Annotated[float, _tmin] = 0.0,
    tmax: Annotated[float, _tmax] = 0.0,
    trim_start: Annotated[float, _trim_start] = 0.0,
    trim_end: Annotated[float, _trim_end] = 0.0,
    **kwargs,
) -> mne.Evoked:
    if crop_mode == "trim":
        evoked.crop(tmin=evoked.times[0] + trim_start, tmax=evoked.times[-1] - trim_end, **kwargs)
    else:
        evoked.crop(tmin=tmin, tmax=tmax, **kwargs)
    return evoked
