"""Interpolate bad channels action."""

from mnetape.actions.base import action_from_templates

ACTION = action_from_templates(
    action_id="interpolate",
    title="Interpolate Bad Channels",
    doc="Interpolate channels marked as bad using spherical splines.",
    action_file=__file__,
    mne_doc_urls={"Interpolate bads": "https://mne.tools/stable/generated/mne.io.Raw.html#mne.io.Raw.interpolate_bads"},
)
