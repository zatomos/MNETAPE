"""Event-based epoching action templates.

Variants:
  annotations: extract events from raw.annotations
  stim: extract events from a stimulus channel via mne.find_events
  file: load events from a BIDS .tsv, .fif, or .eve events file
"""

from __future__ import annotations

from typing import Annotated

import mne
from mnetape.actions.base import ParamMeta, builder, result_builder


_event_ids = ParamMeta(
    label="Events",
    description="Event IDs to include. Leave empty to include all events.",
    default=None,
)
_stim_channel = ParamMeta(
    label="Stim channel",
    description="Stimulus channel to read events from.",
    default="",
)
_min_duration = ParamMeta(
    type="float",
    label="Min duration (s)",
    description="Minimum duration of a stimulus to be considered an event.",
    default=0.0,
    min=0.0,
    decimals=4,
)
_shortest_event = ParamMeta(
    type="int",
    label="Min event length (samples)",
    description="Minimum number of samples an event must span.",
    default=1,
    min=1,
)
_tmin = ParamMeta(
    type="float",
    label="Start (s)",
    description="Start time of each epoch relative to event onset.",
    default=-0.2,
    decimals=3,
)
_tmax = ParamMeta(
    type="float",
    label="End (s)",
    description="End time of each epoch relative to event onset.",
    default=0.8,
    decimals=3,
)
_baseline_tmin = ParamMeta(
    type="float",
    label="Baseline start (s)",
    description="Start of the baseline window. Leave baseline end as None to skip correction.",
    default=None,
    decimals=3,
    nullable=True,
)
_baseline_tmax = ParamMeta(
    type="float",
    label="Baseline end (s)",
    description="End of the baseline window. Set to apply baseline correction.",
    default=None,
    decimals=3,
    nullable=True,
)
_reject_by_annotation = ParamMeta(
    type="bool",
    label="Reject by annotation",
    description="If True, epochs overlapping bad annotations are rejected.",
    default=True,
)
_events_file = ParamMeta(
    label="Events file",
    description="Path to a BIDS .tsv, .fif, or .eve events file.",
    default="",
)


# ---------- variant bodies ----------

@builder(key="annotations")
def _body_annotations(
    raw: mne.io.Raw,
    event_ids: Annotated[dict | None, _event_ids] = None,
    tmin: Annotated[float, _tmin] = -0.2,
    tmax: Annotated[float, _tmax] = 0.8,
    baseline_tmin: Annotated[float | None, _baseline_tmin] = None,
    baseline_tmax: Annotated[float | None, _baseline_tmax] = None,
    reject_by_annotation: Annotated[bool, _reject_by_annotation] = True,
    **kwargs,
) -> mne.BaseEpochs:
    events, event_ids = mne.events_from_annotations(raw, event_id=event_ids)
    baseline = (baseline_tmin, baseline_tmax) if baseline_tmin is not None and baseline_tmax is not None else None
    epochs = mne.Epochs(
        raw, events, event_id=event_ids, tmin=tmin, tmax=tmax,
        baseline=baseline, reject_by_annotation=reject_by_annotation,
        preload=True, **kwargs,
    )
    return epochs


@builder(key="stim")
def _body_stim(
    raw: mne.io.Raw,
    stim_channel: Annotated[str, _stim_channel] = "",
    min_duration: Annotated[float, _min_duration] = 0.0,
    shortest_event: Annotated[int, _shortest_event] = 1,
    event_ids: Annotated[dict | None, _event_ids] = None,
    tmin: Annotated[float, _tmin] = -0.2,
    tmax: Annotated[float, _tmax] = 0.8,
    baseline_tmin: Annotated[float | None, _baseline_tmin] = None,
    baseline_tmax: Annotated[float | None, _baseline_tmax] = None,
    reject_by_annotation: Annotated[bool, _reject_by_annotation] = True,
    **kwargs,
) -> mne.BaseEpochs:
    events = mne.find_events(
        raw,
        stim_channel=stim_channel or None,
        min_duration=min_duration,
        shortest_event=shortest_event,
    )
    baseline = (baseline_tmin, baseline_tmax) if baseline_tmin is not None and baseline_tmax is not None else None
    epochs = mne.Epochs(
        raw, events, event_id=event_ids, tmin=tmin, tmax=tmax,
        baseline=baseline, reject_by_annotation=reject_by_annotation,
        preload=True, **kwargs,
    )
    return epochs


@builder(key="file")
def _body_file(
    raw: mne.io.Raw,
    events_file: Annotated[str, _events_file] = "",
    event_ids: Annotated[dict | None, _event_ids] = None,
    tmin: Annotated[float, _tmin] = -0.2,
    tmax: Annotated[float, _tmax] = 0.8,
    baseline_tmin: Annotated[float | None, _baseline_tmin] = None,
    baseline_tmax: Annotated[float | None, _baseline_tmax] = None,
    reject_by_annotation: Annotated[bool, _reject_by_annotation] = True,
    **kwargs,
) -> mne.BaseEpochs:
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
    baseline = (baseline_tmin, baseline_tmax) if baseline_tmin is not None and baseline_tmax is not None else None
    epochs = mne.Epochs(
        raw, events, event_id=event_ids, tmin=tmin, tmax=tmax,
        baseline=baseline, reject_by_annotation=reject_by_annotation,
        preload=True, **kwargs,
    )
    return epochs


@result_builder
def build_result(data):
    import numpy as np
    from matplotlib.figure import Figure
    from mnetape.core.models import ActionResult

    event_id = data.event_id or {}
    counts = {cond: int(np.sum(data.events[:, 2] == eid)) for cond, eid in event_id.items()}
    n_total = len(data)
    n_conditions = len(counts)

    fig = None
    if len(counts) > 1:
        fig = Figure(figsize=(max(4, n_conditions * 0.8), 3.8))
        ax = fig.add_subplot(111)
        ax.bar(list(counts.keys()), list(counts.values()), color="steelblue", alpha=0.85)
        ax.set_ylabel("Epochs")
        ax.set_title("Epochs per condition")
        ax.tick_params(axis="x", rotation=30)
        fig.tight_layout()

    summary = f"{n_total} epochs across {n_conditions} condition{'s' if n_conditions != 1 else ''}"
    return ActionResult(summary=summary, fig=fig, details=counts)
