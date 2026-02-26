"""Bandpass filter action templates."""

from __future__ import annotations

from typing import Annotated

from mnetape.actions.base import ParamMeta, fragment, step

PRIMARY_PARAMS = {"raw.filter": ["l_freq", "h_freq"]}


@fragment
def _do_filter(raw, l_freq: float | None = None, h_freq: float | None = None) -> None:
    raw.filter(l_freq=l_freq, h_freq=h_freq)


@step("apply")
def template_builder(
    l_freq: Annotated[
        float | None,
        ParamMeta(
            type="float",
            label="Highpass (Hz)",
            description="Low cutoff frequency in Hz. Set to 0 for no highpass.",
            default=0.5,
            min=0,
            max=50,
            nullable=True,
        ),
    ] = 0.5,
    h_freq: Annotated[
        float | None,
        ParamMeta(
            type="float",
            label="Lowpass (Hz)",
            description="High cutoff frequency in Hz.",
            default=45.0,
            min=1,
            max=500,
            nullable=True,
        ),
    ] = 45.0,
) -> str:
    """Generate code to bandpass-filter the raw data."""
    return _do_filter.inline(l_freq=l_freq, h_freq=h_freq)
