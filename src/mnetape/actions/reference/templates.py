"""Re-reference action templates."""

from __future__ import annotations

from typing import Annotated

from mnetape.actions.base import ParamMeta, fragment, step

PRIMARY_PARAMS = {"raw.set_eeg_reference": ["ref_channels", "projection"]}


@fragment
def _do_reference(raw, ref_channels: str = "average", projection: bool = False) -> None:
    raw.set_eeg_reference(ref_channels=ref_channels, projection=projection)


@step("apply")
def template_builder(
    ref_channels: Annotated[
        str,
        ParamMeta(
            type="choice",
            choices=["average", "REST"],
            label="Reference type",
            description="Re-reference method.",
            default="average",
        ),
    ] = "average",
    projection: Annotated[
        bool,
        ParamMeta(
            type="bool",
            label="Apply as projection",
            description="If true, add an SSP projector instead of applying the reference directly.",
            default=False,
        ),
    ] = False,
) -> str:
    """Generate code to re-reference the raw data."""
    return _do_reference.inline(ref_channels=ref_channels, projection=projection)
