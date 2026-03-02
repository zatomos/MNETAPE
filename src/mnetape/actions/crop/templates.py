"""Crop action templates."""

from __future__ import annotations

from typing import Annotated

from mnetape.actions.base import ParamMeta, fragment, step

PRIMARY_PARAMS = {"raw.crop": ["tmin", "tmax"]}


@fragment
def _do_crop(raw, tmin: float = 0.0, tmax: float = 0.0) -> None:
    raw.crop(tmin=tmin, tmax=tmax)


@step("crop")
def template_builder(
    tmin: Annotated[
        float,
        ParamMeta(
            type="float",
            label="Start (s)",
            description="Start time in seconds.",
            default=0.0,
            min=0.0,
            decimals=3,
        ),
    ] = 0.0,
    tmax: Annotated[
        float,
        ParamMeta(
            type="crop_tmax",
            label="End (s)",
            description="End time in seconds.",
            default=0.0,
        ),
    ] = 0.0,
) -> str:
    return _do_crop.inline(tmin=tmin, tmax=tmax)
