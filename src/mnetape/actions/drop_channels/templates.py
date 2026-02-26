"""Drop channels action templates."""

from __future__ import annotations

from typing import Annotated

from mnetape.actions.base import ParamMeta, fragment, step

PRIMARY_PARAMS = {"raw.drop_channels": ["ch_names"]}


@fragment
def _drop(raw, channels: list[str] | None = None) -> None:
    raw.drop_channels(ch_names=channels)


@fragment
def _mark_bad(raw, channels: list[str] = None) -> None:
    raw.info["bads"] = sorted(set(raw.info["bads"]) | set(channels))


def _parse_channels(value: str | list | None) -> list[str]:
    """Normalize channel input to a list of stripped, non-empty channel name strings.

    Accepts a Python list, a comma-separated string, or None.

    Args:
        value: Raw param value from the action config.

    Returns:
        List of non-empty, stripped channel name strings.
    """
    if value is None:
        return []
    if isinstance(value, list):
        return [str(c).strip() for c in value if str(c).strip()]
    return [c.strip() for c in str(value).split(",") if c.strip()]


@step("apply")
def template_builder(
    channels: Annotated[
        list[str] | None,
        ParamMeta(
            type="channels",
            label="Channels",
            description="Channels to drop or mark as bad.",
        ),
    ] = None,
    mode: Annotated[
        str,
        ParamMeta(
            type="choice",
            choices=["drop", "mark_bad"],
            label="Channel handling",
            description="drop: remove channels entirely.  mark_bad: keep but flag as bad.",
            default="mark_bad",
        ),
    ] = "mark_bad",
) -> str:
    """Generate code to drop or mark-bad a list of channels."""
    ch_list = _parse_channels(channels)
    if mode == "mark_bad":
        return _mark_bad.inline(channels=ch_list)
    return _drop.inline(channels=ch_list)
