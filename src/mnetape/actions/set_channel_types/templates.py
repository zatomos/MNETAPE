"""Set channel types action templates."""

from __future__ import annotations

import json
from typing import Annotated

from mnetape.actions.base import ParamMeta, fragment, step

PRIMARY_PARAMS = {"raw.set_channel_types": ["mapping"]}


def _parse_mapping(value: str | dict | None) -> dict[str, str]:
    """Parse channel_mapping input to a channel-name -> type dict.

    Accepts a dict directly, a JSON-encoded object string, or a
    comma-separated "ch:type" string (e.g. "EOG:eog, ECG:ecg").

    Args:
        value: The raw parameter value from the action config.

    Returns:
        Dict mapping channel names to type strings; empty dict for None or empty input.
    """
    if isinstance(value, dict):
        return {str(k): str(v) for k, v in value.items() if k and v}
    text = str(value or "").strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return {str(k): str(v) for k, v in parsed.items() if k and v}
    except (json.JSONDecodeError, ValueError):
        pass
    mapping: dict[str, str] = {}
    for pair in text.split(","):
        pair = pair.strip()
        if ":" in pair:
            ch, typ = pair.split(":", 1)
            ch, typ = ch.strip(), typ.strip()
            if ch and typ:
                mapping[ch] = typ
    return mapping


@fragment
def _do_set_channel_types(raw, channel_mapping: dict | None = None) -> None:
    raw.set_channel_types(mapping=channel_mapping)


@step("apply")
def template_builder(
    channel_mapping: Annotated[
        dict | None,
        ParamMeta(
            type="channel_types",
            label="Channel type mapping",
            description="Channel-to-type mapping (JSON dict or comma-separated ch:type pairs).",
            default=None,
        ),
    ] = None,
) -> str:
    """Generate code to set channel types."""
    mapping = _parse_mapping(channel_mapping)
    return _do_set_channel_types.inline(channel_mapping=mapping)
