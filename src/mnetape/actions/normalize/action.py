"""Normalize action."""

from mnetape.actions.base import action_from_templates

ACTION = action_from_templates(
    action_id="normalize",
    title="Normalize",
    doc="Normalize channel amplitudes using z-score or min-max scaling, globally or over a rolling window.",
)
