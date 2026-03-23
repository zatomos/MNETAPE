"""Set Montage action templates."""

from __future__ import annotations

from typing import Annotated

import mne
from mnetape.actions.base import ParamMeta, builder

_montage_name = ParamMeta(
    type="text",
    label="Standard Montage",
    description="MNE built-in montage name. Leave empty when using a file.",
    default="standard_1020",
)
_montage_file = ParamMeta(
    type="text",
    label="Montage File",
    description="Path to a custom montage file (.loc, .sfp, .elc, .bvct, …). Leave empty when using a standard name.",
    default="",
)


@builder
def set_montage(
    raw: mne.io.Raw,
    montage_name: Annotated[str, _montage_name] = "standard_1020",
    montage_file: Annotated[str, _montage_file] = "",
    renames: dict | None = None,
) -> mne.io.Raw:
    if renames:
        raw.rename_channels(renames)
    if montage_file:
        if montage_file.lower().endswith(".bvct"):
            montage = mne.channels.read_dig_captrak(montage_file)
        else:
            montage = mne.channels.read_custom_montage(montage_file)
    else:
        montage = mne.channels.make_standard_montage(montage_name)
    raw.set_montage(montage, on_missing="warn")
    return raw
