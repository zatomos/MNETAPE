"""Notch filter action templates."""

from __future__ import annotations

from typing import Annotated

import mne
from mnetape.actions.base import ParamMeta, builder


@builder
def template_builder(
    raw: mne.io.Raw,
    freqs: Annotated[
        float,
        ParamMeta(
            type="float",
            label="Frequency (Hz)",
            description="Base frequency of the line noise.",
            default=50,
            min=1,
            max=500,
        ),
    ] = 50.0,
    harmonics: Annotated[
        int,
        ParamMeta(
            type="int",
            label="Harmonics",
            description="Number of harmonics to include.",
            default=3,
            min=1,
            max=10,
        ),
    ] = 3,
    **kwargs,
) -> mne.io.Raw:
    freqs_list = [freqs * (i + 1) for i in range(harmonics)]
    raw.notch_filter(freqs=freqs_list, **kwargs)
    return raw
