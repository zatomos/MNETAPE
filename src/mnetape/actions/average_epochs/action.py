"""Average epochs action."""

from mnetape.actions.base import action_from_templates

ACTION = action_from_templates(
    action_id="average_epochs",
    title="Average Epochs",
    doc="Average epochs to produce a single evoked response.",
    mne_doc_urls={
        "mne.Epochs.average": "https://mne.tools/stable/generated/mne.Epochs.html#mne.Epochs.average",
    },
)
