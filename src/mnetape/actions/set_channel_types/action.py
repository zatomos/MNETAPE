"""Set channel types action."""

from mnetape.actions.base import action_from_templates

ACTION = action_from_templates(
    action_id="set_channel_types",
    title="Set Channel Types",
    doc="Set channel types for specified channels.",
    action_file=__file__,
    mne_doc_urls={
        "Set Channel Types": "https://mne.tools/stable/generated/mne.io.Raw.html#mne.io.Raw.set_channel_types"
    },
)
