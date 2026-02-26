"""Re-reference action."""

from mnetape.actions.base import action_from_templates

ACTION = action_from_templates(
    action_id="reference",
    title="Re-reference",
    doc="Re-reference to average or REST.",
    action_file=__file__,
    mne_doc_urls={"Set EEG Re-reference": "https://mne.tools/stable/generated/mne.io.Raw.html#mne.io.Raw.set_eeg_reference"},
)
