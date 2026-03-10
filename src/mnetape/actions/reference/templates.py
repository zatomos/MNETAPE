"""Re-reference action templates."""

from __future__ import annotations

from typing import Annotated

import mne
from mnetape.actions.base import ParamMeta, builder

_ref_channels = ParamMeta(
    type="choice",
    choices=["average", "REST"],
    label="Reference type",
    description="Re-reference method.",
    default="average",
)
_projection = ParamMeta(
    type="bool",
    label="Apply as projection",
    description="If true, add an SSP projector instead of applying the reference directly.",
    default=False,
)


@builder
def reference_raw(
    raw: mne.io.Raw,
    ref_channels: Annotated[str, _ref_channels] = "average",
    projection: Annotated[bool, _projection] = False,
    **kwargs,
) -> mne.io.Raw:
    raw.set_eeg_reference(ref_channels=ref_channels, projection=projection, **kwargs)
    return raw


@builder
def reference_epochs(
    epochs: mne.BaseEpochs,
    ref_channels: Annotated[str, _ref_channels] = "average",
    projection: Annotated[bool, _projection] = False,
    **kwargs,
) -> mne.BaseEpochs:
    epochs.set_eeg_reference(ref_channels=ref_channels, projection=projection, **kwargs)
    return epochs


@builder
def reference_evoked(
    evoked: mne.Evoked,
    ref_channels: Annotated[str, _ref_channels] = "average",
    projection: Annotated[bool, _projection] = False,
    **kwargs,
) -> mne.Evoked:
    evoked.set_eeg_reference(ref_channels=ref_channels, projection=projection, **kwargs)
    return evoked
