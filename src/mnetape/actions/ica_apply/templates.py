"""ICA apply action templates.

Generates code that sets ica.exclude to the stored list and applies ICA to produce clean raw data.
Exclusion can be done automatically or manually.
"""

from __future__ import annotations

from typing import Annotated

import mne
from mnetape.actions.base import ParamMeta, builder


@builder
def template_builder(
    ica: mne.preprocessing.ICA, raw: mne.io.Raw,
    exclude: Annotated[list | None, ParamMeta(type="list", label="Exclude", default=None, nullable=True)],
    **kwargs,
) -> mne.io.Raw:
    ica.exclude = list(exclude or [])
    raw = ica.apply(raw.copy(), **kwargs)
    return raw
