"""Crop action templates."""

from __future__ import annotations

from typing import Annotated

import mne
from mnetape.actions.base import ParamMeta, builder


@builder
def template_builder(
    raw: mne.io.Raw,
    tmin: Annotated[
        float,
        ParamMeta(
            type="float",
            label="Start (s)",
            description="Start time in seconds.",
            default=0.0,
            min=0.0,
            decimals=3,
        ),
    ] = 0.0,
    tmax: Annotated[
        float,
        ParamMeta(
            label="End (s)",
            description="End time in seconds.",
            default=0.0,
        ),
    ] = 0.0,
    **kwargs,
) -> mne.io.Raw:
    raw.crop(tmin=tmin, tmax=tmax, **kwargs)
    return raw
