"""Bandpass filter action templates."""

from __future__ import annotations

from typing import Annotated

import mne
from mnetape.actions.base import ParamMeta, builder


@builder
def template_builder(
    raw: mne.io.Raw,
    l_freq: Annotated[
        float | None,
        ParamMeta(
            type="float",
            label="Highpass (Hz)",
            description="Low cutoff frequency in Hz. Set to 0 for no highpass.",
            default=0.5,
            min=0,
            max=50,
            nullable=True,
        ),
    ] = 0.5,
    h_freq: Annotated[
        float | None,
        ParamMeta(
            type="float",
            label="Lowpass (Hz)",
            description="High cutoff frequency in Hz.",
            default=45.0,
            min=1,
            max=500,
            nullable=True,
        ),
    ] = 45.0,
    **kwargs,
) -> mne.io.Raw:
    raw.filter(l_freq=l_freq, h_freq=h_freq, **kwargs)
    return raw
