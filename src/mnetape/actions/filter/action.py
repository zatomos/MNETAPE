"""Bandpass filter action."""

from mnetape.actions.base import action_from_templates

ACTION = action_from_templates(
    action_id="filter",
    title="Bandpass Filter",
    doc="Bandpass filter to remove slow drifts and high-frequency noise.",
    action_file=__file__,
    mne_doc_urls={"Filter": "https://mne.tools/stable/generated/mne.io.Raw.html#mne.io.Raw.filter"},
)
