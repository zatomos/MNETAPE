"""ICA apply action templates.

Generates code that sets ica.exclude to the stored list and applies ICA to produce clean raw data.
Exclusion can be done automatically or manually.
"""

from __future__ import annotations

from typing import Annotated

from mnetape.actions.base import ParamMeta, builder, fragment


@fragment
def _apply(ica, raw, exclude: list = None) -> None:
    ica.exclude = exclude
    raw = ica.apply(raw, verbose=False)


@builder
def apply_builder(
    exclude: Annotated[
        list | None,
        ParamMeta(
            type="exclude_components",
            label="Excluded components",
            description="ICA component indices to remove. Use 'Browse Components' to inspect and select.",
            default=None,
        ),
    ] = None,
) -> str:
    return _apply.inline(exclude=exclude or [])