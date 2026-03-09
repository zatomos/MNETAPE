"""Set channel types action templates."""

from __future__ import annotations

from typing import Annotated

import mne
from mnetape.actions.base import ParamMeta, builder


@builder
def template_builder(
    raw: mne.io.Raw,
    channel_mapping: Annotated[
        dict | None,
        ParamMeta(
            type="channel_types",
            label="Channel type mapping",
            description="Channel-to-type mapping (JSON dict or comma-separated ch:type pairs).",
            default=None,
        ),
    ] = None,
) -> mne.io.Raw:
    raw.set_channel_types(mapping=channel_mapping or {})
    return raw
