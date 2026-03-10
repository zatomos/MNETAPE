"""Crop action."""

from mnetape.actions.base import action_from_templates

ACTION = action_from_templates(
    action_id="crop",
    title="Crop",
    action_file=__file__,
    doc="Trim the recording to a selected time range.",
    mne_doc_urls={
        "Crop": "https://mne.tools/stable/generated/mne.io.Raw.html#mne.io.Raw.crop",
    },
)
