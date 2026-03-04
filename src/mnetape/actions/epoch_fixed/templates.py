"""Fixed-length epoching action templates."""

from __future__ import annotations

from typing import Annotated, TYPE_CHECKING

from mnetape.actions.base import ParamMeta, fragment, step

PRIMARY_PARAMS = {"mne.make_fixed_length_epochs": ["duration", "overlap"]}

if TYPE_CHECKING:
    import mne


@fragment
def _do_epoch_fixed(raw, duration: float = 2.0, overlap: float = 0.0) -> None:
    epochs = mne.make_fixed_length_epochs(raw, duration=duration, overlap=overlap, preload=True)


@fragment
def _do_epoch_fixed_baseline(raw, duration, overlap, baseline_tmin, baseline_tmax) -> None:
    epochs = mne.make_fixed_length_epochs(raw, duration=duration, overlap=overlap, preload=True)
    epochs.apply_baseline(baseline=(baseline_tmin, baseline_tmax))


@step("epoch_fixed")
def template_builder(
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
            description="Start of the baseline window. None = beginning of epoch. Leave both baseline fields as None to skip baseline correction.",
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
            description="End of the baseline window. Set to apply baseline correction; leave as None to skip.",
            default=None,
            decimals=3,
            nullable=True,
        ),
    ] = None,
) -> str:
    if baseline_tmax is not None:
        return _do_epoch_fixed_baseline.inline(
            duration=duration,
            overlap=overlap,
            baseline_tmin=baseline_tmin,
            baseline_tmax=baseline_tmax,
        )
    return _do_epoch_fixed.inline(duration=duration, overlap=overlap)
