"""Set annotations action templates."""

from __future__ import annotations

from typing import Annotated

from mnetape.actions.base import ParamMeta, builder, fragment


@fragment
def _do_set_annotations(raw, onsets, durations, descriptions) -> None:
    raw.set_annotations(mne.Annotations(onset=onsets, duration=durations, description=descriptions))


@builder
def template_builder(
    annotations: Annotated[
        list,
        ParamMeta(
            type="annotations",
            label="Annotations",
            description="Time annotations to apply to the recording.",
            default=[],
        ),
    ] = [],
) -> str:
    onsets = [a["onset"] for a in (annotations or [])]
    durations = [a["duration"] for a in (annotations or [])]
    descriptions = [a["description"] for a in (annotations or [])]
    return _do_set_annotations.inline(onsets=onsets, durations=durations, descriptions=descriptions)
