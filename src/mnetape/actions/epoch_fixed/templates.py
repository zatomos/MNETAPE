"""Fixed-length epoching action templates."""

from __future__ import annotations

from typing import Annotated

import mne
from mnetape.actions.base import ParamMeta, builder, result_builder

@builder
def template_builder(
    raw: mne.io.Raw,
    duration: Annotated[
        float,
        ParamMeta(
            type="float",
            label="Duration (s)",
            description="Length of each epoch in seconds.",
            default=2.0,
            min=0.1,
            decimals=3,
        ),
    ] = 2.0,
    overlap: Annotated[
        float,
        ParamMeta(
            type="float",
            label="Overlap (s)",
            description="Overlap between consecutive epochs in seconds.",
            default=0.0,
            min=0.0,
            decimals=3,
        ),
    ] = 0.0,
    baseline_tmin: Annotated[
        float | None,
        ParamMeta(
            type="float",
            label="Baseline start (s)",
            description="Start of the baseline window. Leave both as None to skip baseline correction.",
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
    **kwargs,
) -> mne.BaseEpochs:
    epochs = mne.make_fixed_length_epochs(raw, duration=duration, overlap=overlap, preload=True, **kwargs)
    if baseline_tmax is not None:
        epochs.apply_baseline(baseline=(baseline_tmin, baseline_tmax))
    return epochs

@result_builder
def build_result(data):
    from mnetape.core.models import ActionResult

    n = len(data)
    dur = data.tmax - data.tmin
    return ActionResult(
        summary=f"{n} fixed-length epochs created ({dur:.3f}s each)",
        details={"Epochs": n, "Duration (s)": f"{dur:.3f}"},
    )
