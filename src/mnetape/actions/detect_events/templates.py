"""Detect events action templates.

Variants:
  ecg: find heartbeat R-wave peaks
  eog: find eye-blink peaks
  threshold: annotate any segment where a channel exceeds a given amplitude threshold
"""

from __future__ import annotations

from typing import Annotated

import mne
from mnetape.actions.base import ParamMeta, builder, result_builder


@builder(key="ecg")
def _body_ecg(
    raw: mne.io.Raw,
    ecg_channel: Annotated[
        str,
        ParamMeta(
            label="Channel",
            description="ECG channel for R-wave detection. Auto-selected if an ECG-type channel is present.",
            default="",
        ),
    ] = "",
    ecg_label: Annotated[
        str,
        ParamMeta(
            type="text",
            label="Annotation label",
            description="Label used for the added annotations.",
            default="ECG",
        ),
    ] = "ECG",
) -> mne.io.Raw:
    ecg_events, _, _ = mne.preprocessing.find_ecg_events(raw, ch_name=ecg_channel or None, event_id=999)
    new_annotations = mne.annotations_from_events(
        ecg_events, raw.info['sfreq'],
        event_desc={999: ecg_label},
        first_samp=raw.first_samp,
    )
    raw.set_annotations(raw.annotations + new_annotations)
    return raw


@builder(key="eog")
def _body_eog(
    raw: mne.io.Raw,
    eog_channel: Annotated[
        str,
        ParamMeta(
            label="Channel",
            description="EOG channel for blink detection. Auto-selected if an EOG-type channel is present.",
            default="",
        ),
    ] = "",
    eog_label: Annotated[
        str,
        ParamMeta(
            type="text",
            label="Annotation label",
            description="Label used for the added annotations.",
            default="EOG",
        ),
    ] = "EOG",
) -> mne.io.Raw:
    eog_events = mne.preprocessing.find_eog_events(raw, ch_name=eog_channel or None, event_id=998)
    new_annotations = mne.annotations_from_events(
        eog_events, raw.info['sfreq'],
        event_desc={998: eog_label},
        first_samp=raw.first_samp,
    )
    raw.set_annotations(raw.annotations + new_annotations)
    return raw


@builder(key="threshold")
def _body_threshold(
    raw: mne.io.Raw,
    threshold_channel: Annotated[
        str,
        ParamMeta(
            type="text",
            label="Channel",
            description="Channel to scan. Leave empty to scan all channels (event created if any channel exceeds threshold).",
            default="",
        ),
    ] = "",
    threshold: Annotated[
        float,
        ParamMeta(
            type="float",
            label="Threshold",
            description="Amplitude threshold. Any sample exceeding this value (in absolute terms) triggers an annotation.",
            default=6.0,
            decimals=2,
        ),
    ] = 6.0,
    min_duration: Annotated[
        float,
        ParamMeta(
            type="float",
            label="Min duration (s)",
            description="Minimum duration in seconds for a detected segment to be kept.",
            default=0.01,
            min=0.0,
            decimals=3,
        ),
    ] = 0.01,
    threshold_label: Annotated[
        str,
        ParamMeta(
            type="text",
            label="Annotation label",
            description="Label used for the added annotations.",
            default="event",
        ),
    ] = "event",
) -> mne.io.Raw:
    import numpy as np
    picks = [c.strip() for c in threshold_channel.split(",") if c.strip()] if threshold_channel else None
    data = raw.get_data(picks=picks)  # (n_ch, n_times)
    above = np.any(np.abs(data) >= threshold, axis=0)  # (n_times,)
    sfreq = raw.info["sfreq"]
    min_samples = int(min_duration * sfreq)
    onsets, durations = [], []
    i = 0
    while i < len(above):
        if above[i]:
            j = i
            while j < len(above) and above[j]:
                j += 1
            if j - i >= min_samples:
                onsets.append(raw.times[i])
                durations.append((j - i) / sfreq)
            i = j
        else:
            i += 1
    new_annotations = mne.Annotations(
        onset=onsets, duration=durations,
        description=[threshold_label] * len(onsets),
        orig_time=raw.info.get("meas_date"),
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
