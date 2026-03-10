"""Set annotations action."""

from mnetape.actions.base import action_from_templates

ACTION = action_from_templates(
    action_id="set_annotations",
    title="Set Annotations",
    doc="Apply a set of time annotations to the raw recording.",
    mne_doc_urls={
        "set_annotations": "https://mne.tools/stable/generated/mne.io.Raw.html#mne.io.Raw.set_annotations",
    },
)
