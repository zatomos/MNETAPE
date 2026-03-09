"""Resample action templates."""

from __future__ import annotations

from typing import Annotated

import mne
from mnetape.actions.base import ParamMeta, builder


@builder
def template_builder(
    raw: mne.io.Raw,
    sfreq: Annotated[
        float,
        ParamMeta(
            type="float",
            label="Target frequency (Hz)",
            description="New sample rate in Hz.",
            default=250,
            min=1,
            max=10000,
        ),
    ] = 250.0,
    **kwargs,
) -> mne.io.Raw:
    raw.resample(sfreq=sfreq, **kwargs)
    return raw
