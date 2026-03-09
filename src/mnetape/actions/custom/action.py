"""Custom action."""

from __future__ import annotations

from mnetape.actions.base import ActionDefinition
from mnetape.core.models import CUSTOM_ACTION_ID


ACTION = ActionDefinition(
    action_id=CUSTOM_ACTION_ID,
    title="Custom Action",
    params_schema={},
    doc="Custom code block.",
)
