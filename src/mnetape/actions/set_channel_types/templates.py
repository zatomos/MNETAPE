"""Set channel types action templates."""

from __future__ import annotations

from typing import Annotated

import mne
from mnetape.actions.base import ParamMeta, builder

_channel_mapping = ParamMeta(
    label="Channel type mapping",
    description="Channel-to-type mapping (JSON dict or comma-separated ch:type pairs).",
    default=None,
)

@builder
def set_channel_types_raw(
    raw: mne.io.Raw,
    channel_mapping: Annotated[dict | None, _channel_mapping] = None,
) -> mne.io.Raw:
    raw.set_channel_types(mapping=channel_mapping or {})
    return raw

@builder
def set_channel_types_epochs(
    epochs: mne.BaseEpochs,
    channel_mapping: Annotated[dict | None, _channel_mapping] = None,
) -> mne.BaseEpochs:
    epochs.set_channel_types(mapping=channel_mapping or {})
    return epochs

@builder
def set_channel_types_evoked(
    evoked: mne.Evoked,
    channel_mapping: Annotated[dict | None, _channel_mapping] = None,
) -> mne.Evoked:
    evoked.set_channel_types(mapping=channel_mapping or {})
    return evoked
