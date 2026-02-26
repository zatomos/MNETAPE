"""Custom action."""

from __future__ import annotations

from mnetape.actions.base import ActionDefinition


ACTION = ActionDefinition(
    action_id="custom",
    title="Custom Action",
    params_schema={},
    doc="Custom code block.",
)
