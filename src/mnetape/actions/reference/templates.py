"""Re-reference action templates."""

from __future__ import annotations

from typing import Annotated

import mne
from mnetape.actions.base import ParamMeta, builder


@builder
def template_builder(
    raw: mne.io.Raw,
    ref_channels: Annotated[
        str,
        ParamMeta(
            type="choice",
            choices=["average", "REST"],
            label="Reference type",
            description="Re-reference method.",
            default="average",
        ),
    ] = "average",
    projection: Annotated[
        bool,
        ParamMeta(
            type="bool",
            label="Apply as projection",
            description="If true, add an SSP projector instead of applying the reference directly.",
            default=False,
        ),
    ] = False,
    **kwargs,
) -> mne.io.Raw:
    raw.set_eeg_reference(ref_channels=ref_channels, projection=projection, **kwargs)
    return raw
