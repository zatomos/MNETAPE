"""Set Montage action."""

from mnetape.actions.base import action_from_templates

ACTION = action_from_templates(
    action_id="set_montage",
    title="Set Montage",
    doc="Apply a channel location montage to the raw data.",
    hidden=True,
)
