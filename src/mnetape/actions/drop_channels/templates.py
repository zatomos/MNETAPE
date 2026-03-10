"""Drop channels action templates."""

from __future__ import annotations

from typing import Annotated

import mne
from mnetape.actions.base import ParamMeta, builder

_channels = ParamMeta(
    label="Channels",
    description="Channels to drop or mark as bad.",
    default=None,
)
_mode = ParamMeta(
    type="choice",
    choices=["drop", "mark_bad"],
    label="Channel handling",
    description="drop: remove channels entirely.  mark_bad: keep but flag as bad.",
    default="mark_bad",
)


@builder
def drop_channels_raw(
    raw: mne.io.Raw,
    channels: Annotated[list | None, _channels] = None,
    mode: Annotated[str, _mode] = "mark_bad",
) -> mne.io.Raw:
    if mode == "mark_bad":
        raw.info["bads"] = sorted(set(raw.info["bads"]) | set(channels or []))
    else:
        raw.drop_channels(ch_names=channels or [])
    return raw


@builder
def drop_channels_epochs(
    epochs: mne.BaseEpochs,
    channels: Annotated[list | None, _channels] = None,
    mode: Annotated[str, _mode] = "mark_bad",
) -> mne.BaseEpochs:
    if mode == "mark_bad":
        epochs.info["bads"] = sorted(set(epochs.info["bads"]) | set(channels or []))
    else:
        epochs.drop_channels(ch_names=channels or [])
    return epochs


@builder
def drop_channels_evoked(
    evoked: mne.Evoked,
    channels: Annotated[list | None, _channels] = None,
    mode: Annotated[str, _mode] = "mark_bad",
) -> mne.Evoked:
    if mode == "mark_bad":
        evoked.info["bads"] = sorted(set(evoked.info["bads"]) | set(channels or []))
    else:
        evoked.drop_channels(ch_names=channels or [])
    return evoked
