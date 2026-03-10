"""ICA apply action: sets the component exclusion list and applies ICA to produce clean raw data."""

from mnetape.actions.base import Prerequisite, action_from_templates

ACTION = action_from_templates(
    action_id="ica_apply",
    title="Apply ICA",
    doc=(
        "Set the ICA component exclusion list and apply it to produce clean raw data. "
    ),
    mne_doc_urls={
        "ICA.apply": "https://mne.tools/stable/generated/mne.preprocessing.ICA.html#mne.preprocessing.ICA.apply",
    },
    prerequisites=(
        Prerequisite("ica_fit", "ICA must be fitted before it can be applied."),
    ),
)
