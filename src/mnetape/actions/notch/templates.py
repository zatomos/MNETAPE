"""Notch filter action templates."""

from __future__ import annotations

from typing import Annotated

from mnetape.actions.base import ParamMeta, fragment, step

PRIMARY_PARAMS = {"raw.notch_filter": ["freqs"]}


@fragment
def _do_notch(raw, freqs: list[float] | None = None) -> None:
    raw.notch_filter(freqs=freqs)


@step("apply")
def template_builder(
    freqs: Annotated[
        float,
        ParamMeta(
            type="float",
            label="Frequency (Hz)",
            description="Base frequency of the line noise.",
            default=50,
            min=1,
            max=500,
        ),
    ] = 50.0,
    harmonics: Annotated[
        int,
        ParamMeta(
            type="int",
            label="Harmonics",
            description="Number of harmonics to include.",
            default=3,
            min=1,
            max=10,
        ),
    ] = 3,
) -> str:
    """Generate code to notch-filter at the base frequency and its harmonics."""
    if isinstance(freqs, (list, tuple)):
        freqs_list = [float(f) for f in freqs if isinstance(f, (int, float))]
        if not freqs_list:
            freqs_list = [50.0]
    else:
        h = max(1, int(harmonics))
        base = float(freqs)
        freqs_list = [base * (i + 1) for i in range(h)]
    return _do_notch.inline(freqs=freqs_list)
