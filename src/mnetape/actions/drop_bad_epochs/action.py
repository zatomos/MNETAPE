"""Drop bad epochs action."""

from mnetape.actions.base import action_from_templates

ACTION = action_from_templates(
    action_id="drop_bad_epochs",
    title="Drop Bad Epochs",
    doc="Remove epochs exceeding amplitude thresholds or use AutoReject for data-driven cleaning.",
    mne_doc_urls={
        "mne.Epochs.drop_bad": "https://mne.tools/stable/generated/mne.Epochs.html#mne.Epochs.drop_bad",
        "autoreject": "https://autoreject.github.io/stable/index.html"
    },
)
