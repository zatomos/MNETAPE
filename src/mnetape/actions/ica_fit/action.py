"""ICA fit action: fits ICA decomposition on raw EEG data."""

from mnetape.actions.base import Prerequisite, action_from_templates
from mnetape.core.models import DataType

ACTION = action_from_templates(
    action_id="ica_fit",
    title="Fit ICA",
    doc=(
        "Fit an ICA decomposition on the raw data. "
        "Produces a fitted ICA object for downstream classification and application."
    ),
    action_file=__file__,
    mne_doc_urls={
        "mne.preprocessing.ICA": "https://mne.tools/stable/generated/mne.preprocessing.ICA.html",
    },
    prerequisites=(
        Prerequisite("notch", "Removing line noise prevents components being wasted on it."),
        Prerequisite("reference", "A common average reference improves ICA decomposition quality."),
    ),
    input_type=DataType.RAW,
    output_type=DataType.ICA,
)
