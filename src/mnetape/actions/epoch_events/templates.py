"""Event-based epoching action templates."""

from __future__ import annotations

from typing import Annotated

import mne
from mnetape.actions.base import ParamMeta, builder


@builder
def template_builder(
    raw: mne.io.Raw,
    event_source: Annotated[
        str,
        ParamMeta(
            type="choice",
            label="Source",
            description="How to extract events from the recording.",
            choices=["annotations", "stim", "file"],
            default="annotations",
        ),
    ] = "annotations",
    events_file: Annotated[
        str,
        ParamMeta(
            type="events_file",
            label="Events file",
            description="Path to a BIDS .tsv, .fif, or .eve events file.",
            default="",
            visible_when={"event_source": ["file"]},
        ),
    ] = "",
    event_ids: Annotated[
        dict | None,
        ParamMeta(
            type="event_ids",
            label="Events",
            description="Event IDs to include. Leave empty to include all events.",
            default=None,
        ),
    ] = None,
    stim_channel: Annotated[
        str,
        ParamMeta(
            type="stim_channel",
            label="Stim channel",
            description="Stimulus channel to read events from.",
            default="",
            visible_when={"event_source": ["stim"]},
        ),
    ] = "",
    min_duration: Annotated[
        float,
        ParamMeta(
            type="float",
            label="Min duration (s)",
            description="Minimum duration of a stimulus to be considered an event.",
            default=0.0,
            min=0.0,
            decimals=4,
            visible_when={"event_source": ["stim"]},
        ),
    ] = 0.0,
    shortest_event: Annotated[
        int,
        ParamMeta(
            type="int",
            label="Min event length (samples)",
            description="Minimum number of samples an event must span.",
            default=1,
            min=1,
            visible_when={"event_source": ["stim"]},
        ),
    ] = 1,
    tmin: Annotated[
        float,
        ParamMeta(
            type="float",
            label="Start (s)",
            description="Start time of each epoch relative to event onset.",
            default=-0.2,
            decimals=3,
        ),
    ] = -0.2,
    tmax: Annotated[
        float,
        ParamMeta(
            type="float",
            label="End (s)",
            description="End time of each epoch relative to event onset.",
            default=0.8,
            decimals=3,
        ),
    ] = 0.8,
    baseline_tmin: Annotated[
        float | None,
        ParamMeta(
            type="float",
            label="Baseline start (s)",
            description="Start of the baseline window. Leave baseline end as None to skip correction.",
            default=None,
            decimals=3,
            nullable=True,
        ),
    ] = None,
    baseline_tmax: Annotated[
        float | None,
        ParamMeta(
            type="float",
            label="Baseline end (s)",
            description="End of the baseline window. Set to apply baseline correction.",
            default=None,
            decimals=3,
            nullable=True,
        ),
    ] = None,
    reject_by_annotation: Annotated[
        bool,
        ParamMeta(
            type="bool",
            label="Reject by annotation",
            description="If True, epochs overlapping bad annotations are rejected.",
            default=True,
        ),
    ] = True,
    **kwargs,
) -> mne.BaseEpochs:
    if event_source == "stim":
        events = mne.find_events(
            raw,
            stim_channel=stim_channel or None,
            min_duration=min_duration,
            shortest_event=shortest_event,
        )
    elif event_source == "file":
        if events_file.lower().endswith(".tsv"):
            import numpy as np
            import pandas as pd
            df = pd.read_csv(events_file, sep='\t')
            if 'event_type' in df.columns:
                col = 'event_type'
            elif 'trial_type' in df.columns:
                col = 'trial_type'
            else:
                raise ValueError(
                    f"BIDS events file has no 'event_type' or 'trial_type' column. "
                    f"Available columns: {list(df.columns)}"
                )
            descs = df[col].fillna('n/a').astype(str)
            id_map = {d: i + 1 for i, d in enumerate(sorted(set(descs)))}
            events = np.column_stack([
                (df['onset'].values * raw.info['sfreq']).round().astype(int),
                np.zeros(len(df), dtype=int),
                [id_map[d] for d in descs],
            ])
        else:
            events = mne.read_events(events_file)
    else:
        events, event_ids = mne.events_from_annotations(raw, event_id=event_ids)

    if baseline_tmin is not None and baseline_tmax is not None:
        baseline = (baseline_tmin, baseline_tmax)
    else:
        baseline = None

    epochs = mne.Epochs(
        raw,
        events,
        event_id=event_ids,
        tmin=tmin,
        tmax=tmax,
        baseline=baseline,
        reject_by_annotation=reject_by_annotation,
        preload=True,
        **kwargs,
    )
    return epochs
