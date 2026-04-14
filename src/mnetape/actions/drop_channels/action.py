"""Drop channels action."""

from mnetape.actions.base import action_from_templates

ACTION = action_from_templates(
    action_id="drop_channels",
    title="Drop/Mark Bad Channels",
    doc=(
        "Remove specified channels from the data.\n\n"
        "mark_bad keeps channels in the data structure but flags them as bad, "
        "which excludes them from most analysis. "
        "drop removes them entirely and cannot be undone."
    ),
    mne_doc_urls={
        "Drop Channels": "https://mne.tools/stable/generated/mne.io.Raw.html#mne.io.Raw.drop_channels",
    },
)

