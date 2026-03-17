"""Detect events action templates.

Two methods:
  - ecg: find heartbeat R-wave peaks via mne.preprocessing.find_ecg_events
  - eog: find eye-blink peaks via mne.preprocessing.find_eog_events
"""

from __future__ import annotations

from typing import Annotated

import mne
from mnetape.actions.base import ParamMeta, builder, result_builder

@builder
def template_builder(
    raw: mne.io.Raw,
    method: Annotated[
        str,
        ParamMeta(
            type="choice",
            label="Method",
            description="ECG: locate R-wave peaks. EOG: locate eye-blink peaks.",
            choices=["ecg", "eog"],
            default="eog",
        ),
    ] = "eog",
    ecg_label: Annotated[
        str,
        ParamMeta(
            type="text",
            label="Annotation label",
            description="Label used for the added annotations.",
            default="ECG",
            visible_when={"method": ["ecg"]},
        ),
    ] = "ECG",
    eog_label: Annotated[
        str,
        ParamMeta(
            type="text",
            label="Annotation label",
            description="Label used for the added annotations.",
            default="EOG",
            visible_when={"method": ["eog"]},
        ),
    ] = "EOG",
    ecg_channel: Annotated[
        str,
        ParamMeta(
            label="Channel",
            description="ECG channel for R-wave detection. Auto-selected if an ECG-type channel is present.",
            default="",
            visible_when={"method": ["ecg"]},
        ),
    ] = "",
    eog_channel: Annotated[
        str,
        ParamMeta(
            label="Channel",
            description="EOG channel for blink detection. Auto-selected if an EOG-type channel is present.",
            default="",
            visible_when={"method": ["eog"]},
        ),
    ] = "",
) -> mne.io.Raw:
    if method == "ecg":
        ecg_events, _, _ = mne.preprocessing.find_ecg_events(raw, ch_name=ecg_channel or None, event_id=999)
        new_annotations = mne.annotations_from_events(
            ecg_events, raw.info['sfreq'],
            event_desc={999: ecg_label},
            first_samp=raw.first_samp,
        )
    else:
        eog_events = mne.preprocessing.find_eog_events(raw, ch_name=eog_channel or None, event_id=998)
        new_annotations = mne.annotations_from_events(
            eog_events, raw.info['sfreq'],
            event_desc={998: eog_label},
            first_samp=raw.first_samp,
        )
    raw.set_annotations(raw.annotations + new_annotations)
    return raw

@result_builder
def build_result(data):
    from collections import Counter
    from mnetape.core.models import ActionResult

    counts = Counter(data.annotations.description)
    total = sum(counts.values())
    if not counts:
        return ActionResult(summary="No events detected.")

    summary = f"{total} event{'s' if total != 1 else ''} detected"
    return ActionResult(summary=summary, details=dict(counts.most_common()))
