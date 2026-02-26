"""Resample action."""

from mnetape.actions.base import action_from_templates

ACTION = action_from_templates(
    action_id="resample",
    title="Resample",
    doc="Resample data to target frequency.",
    action_file=__file__,
    mne_doc_urls={"Resample": "https://mne.tools/stable/generated/mne.io.Raw.html#mne.io.Raw.resample"},
)
