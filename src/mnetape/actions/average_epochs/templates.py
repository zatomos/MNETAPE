"""Average epochs action templates."""

from __future__ import annotations

from typing import Annotated

import mne
from mnetape.actions.base import ParamMeta, builder


@builder
def template_builder(
    epochs: mne.BaseEpochs,
    event_key: Annotated[
        str | None,
        ParamMeta(
            type="event_key",
            label="Condition",
            description="Event condition to average. Leave empty to average all epochs.",
            default=None,
            nullable=True,
        ),
    ] = None,
    **kwargs,
) -> mne.Evoked:
    if event_key:
        subset = epochs[event_key]
        if len(subset) == 0:
            raise ValueError(f"No epochs found for selected condition: {event_key!r}")
        evoked = subset.average(**kwargs)
    else:
        evoked = epochs.average(**kwargs)
    return evoked
