"""Set annotations action templates."""

from __future__ import annotations

from typing import Annotated

import mne
from mnetape.actions.base import ParamMeta, builder


@builder
def template_builder(
    raw: mne.io.Raw,
    annotations: Annotated[
        list,
        ParamMeta(
            label="Annotations",
            description="Time annotations to apply to the recording.",
            default=[],
        ),
    ] = [],
) -> mne.io.Raw:
    onsets = [a["onset"] for a in (annotations or [])]
    durations = [a["duration"] for a in (annotations or [])]
    descriptions = [a["description"] for a in (annotations or [])]
    raw.set_annotations(mne.Annotations(onset=onsets, duration=durations, description=descriptions))
    return raw
