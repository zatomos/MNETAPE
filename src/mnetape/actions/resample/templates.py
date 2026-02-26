"""Resample action templates."""

from __future__ import annotations

from typing import Annotated

from mnetape.actions.base import ParamMeta, fragment, step

PRIMARY_PARAMS = {"raw.resample": ["sfreq"]}


@fragment
def _do_resample(raw, sfreq: float = 250.0) -> None:
    raw.resample(sfreq=sfreq)


@step("apply")
def template_builder(
    sfreq: Annotated[
        float,
        ParamMeta(
            type="float",
            label="Target frequency (Hz)",
            description="New sample rate in Hz.",
            default=250,
            min=1,
            max=10000,
        ),
    ] = 250.0,
) -> str:
    """Generate code to resample the raw data."""
    return _do_resample.inline(sfreq=sfreq)
