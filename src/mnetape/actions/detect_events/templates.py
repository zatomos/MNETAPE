"""Detect events action templates.

Three methods:
  - ecg: find heartbeat R-wave peaks via mne.preprocessing.find_ecg_events
  - eog: find eye-blink peaks via mne.preprocessing.find_eog_events
  - threshold: annotate any segment where a channel exceeds a given amplitude threshold
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
            description="ECG: locate R-wave peaks. EOG: locate eye-blink peaks. Threshold: annotate segments exceeding an amplitude threshold.",
            choices=["ecg", "eog", "threshold"],
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
    threshold_channel: Annotated[
        str,
        ParamMeta(
            type="text",
            label="Channel",
            description="Channel to scan. Leave empty to scan all channels (event created if any channel exceeds threshold).",
            default="",
            visible_when={"method": ["threshold"]},
        ),
    ] = "",
    threshold: Annotated[
        float,
        ParamMeta(
            type="float",
            label="Threshold",
            description="Amplitude threshold. Any sample exceeding this value (in absolute terms) triggers an annotation. If the signal was z-scored beforehand, this is in standard deviations.",
            default=6.0,
            decimals=2,
            visible_when={"method": ["threshold"]},
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
            visible_when={"method": ["threshold"]},
        ),
    ] = 0.01,
    threshold_label: Annotated[
        str,
        ParamMeta(
            type="text",
            label="Annotation label",
            description="Label used for the added annotations.",
            default="event",
            visible_when={"method": ["threshold"]},
        ),
    ] = "event",
) -> mne.io.Raw:
    if method == "ecg":
        ecg_events, _, _ = mne.preprocessing.find_ecg_events(raw, ch_name=ecg_channel or None, event_id=999)
        new_annotations = mne.annotations_from_events(
            ecg_events, raw.info['sfreq'],
            event_desc={999: ecg_label},
            first_samp=raw.first_samp,
        )
    elif method == "eog":
        eog_events = mne.preprocessing.find_eog_events(raw, ch_name=eog_channel or None, event_id=998)
        new_annotations = mne.annotations_from_events(
            eog_events, raw.info['sfreq'],
            event_desc={998: eog_label},
            first_samp=raw.first_samp,
        )
    else:
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
