"""ICA classify action: automatic component classification via ICLabel, EOG, ECG, and muscle detection."""

from mnetape.actions.base import Prerequisite, action_from_templates

ACTION = action_from_templates(
    action_id="ica_classify",
    title="Classify ICA Components",
    doc=(
        "Automatically classify ICA components using ICLabel, EOG/ECG channel correlation, "
        "and muscle artifact detection. Produces a list of artifact component indices "
        "available for review and exclusion in the Apply step."
    ),
    action_file=__file__,
    mne_doc_urls={
        "ICLabel": "https://mne.tools/mne-icalabel/dev/index.html",
        "find_bads_eog": "https://mne.tools/stable/generated/mne.preprocessing.ICA.html#mne.preprocessing.ICA.find_bads_eog",
        "find_bads_ecg": "https://mne.tools/stable/generated/mne.preprocessing.ICA.html#mne.preprocessing.ICA.find_bads_ecg",
    },
    prerequisites=(
        Prerequisite("ica_fit", "ICA must be fitted before classification."),
    ),
)
