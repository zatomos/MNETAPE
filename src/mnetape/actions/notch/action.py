"""Notch filter action."""

from mnetape.actions.base import action_from_templates

ACTION = action_from_templates(
    action_id="notch",
    title="Notch Filter",
    doc="Remove line noise at specified frequency and harmonics.",
    action_file=__file__,
    mne_doc_urls={"Notch Filter": "https://mne.tools/stable/generated/mne.io.Raw.html#mne.io.Raw.notch_filter"},
)
