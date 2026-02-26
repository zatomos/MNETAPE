"""Interpolate bad channels action templates."""

from __future__ import annotations

from mnetape.actions.base import fragment, step

PRIMARY_PARAMS = {"raw.interpolate_bads": ["reset_bads"]}


@fragment
def _do_interpolate(raw) -> None:
    raw.interpolate_bads(reset_bads=True)


@step("apply")
def template_builder() -> str:
    """Generate code to interpolate channels marked as bad."""
    return _do_interpolate.inline()
