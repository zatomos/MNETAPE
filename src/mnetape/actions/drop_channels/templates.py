"""Drop channels action templates.

Variants/Modes:
  mark_bad: flag channels as bad in info["bads"] (keeps them in the data)
  drop: remove channels entirely
"""

from __future__ import annotations

from typing import Annotated

import mne
from mnetape.actions.base import ParamMeta, builder

_channels = ParamMeta(
    label="Channels",
    description="Channels to drop or mark as bad.",
    default=None,
)


# ---------- Raw variants ----------

@builder(key="mark_bad")
def _raw_mark(
    raw: mne.io.Raw,
    channels: Annotated[list | None, _channels] = None,
) -> mne.io.Raw:
    raw.info["bads"] = sorted(set(raw.info["bads"]) | set(channels or []))
    return raw


@builder(key="drop")
def _raw_drop(
    raw: mne.io.Raw,
    channels: Annotated[list | None, _channels] = None,
) -> mne.io.Raw:
    raw.drop_channels(ch_names=channels or [])
    return raw


# ---------- Epochs variants ----------

@builder(key="mark_bad")
def _epochs_mark(
    epochs: mne.BaseEpochs,
    channels: Annotated[list | None, _channels] = None,
) -> mne.BaseEpochs:
    epochs.info["bads"] = sorted(set(epochs.info["bads"]) | set(channels or []))
    return epochs


@builder(key="drop")
def _epochs_drop(
    epochs: mne.BaseEpochs,
    channels: Annotated[list | None, _channels] = None,
) -> mne.BaseEpochs:
    epochs.drop_channels(ch_names=channels or [])
    return epochs


# ---------- Evoked variants ----------

@builder(key="mark_bad")
def _evoked_mark(
    evoked: mne.Evoked,
    channels: Annotated[list | None, _channels] = None,
) -> mne.Evoked:
    evoked.info["bads"] = sorted(set(evoked.info["bads"]) | set(channels or []))
    return evoked


@builder(key="drop")
def _evoked_drop(
    evoked: mne.Evoked,
    channels: Annotated[list | None, _channels] = None,
) -> mne.Evoked:
    evoked.drop_channels(ch_names=channels or [])
    return evoked
