"""Drop channels action templates."""

from __future__ import annotations

from typing import Annotated

import mne
from mnetape.actions.base import ParamMeta, builder


@builder
def template_builder(
    raw: mne.io.Raw,
    channels: Annotated[
        list | None,
        ParamMeta(
            label="Channels",
            description="Channels to drop or mark as bad.",
            default=None,
        ),
    ] = None,
    mode: Annotated[
        str,
        ParamMeta(
            type="choice",
            choices=["drop", "mark_bad"],
            label="Channel handling",
            description="drop: remove channels entirely.  mark_bad: keep but flag as bad.",
            default="mark_bad",
        ),
    ] = "mark_bad",
) -> mne.io.Raw:
    if mode == "mark_bad":
        raw.info["bads"] = sorted(set(raw.info["bads"]) | set(channels or []))
    else:
        raw.drop_channels(ch_names=channels or [])
    return raw
