"""Fixed-length epochs action."""

from mnetape.actions.base import action_from_templates
from mnetape.core.models import DataType

ACTION = action_from_templates(
    action_id="epoch_fixed",
    title="Fixed-Length Epochs",
    doc="Split the recording into fixed-length epochs of equal duration.",
    action_file=__file__,
    mne_doc_urls={
        "make_fixed_length_epochs": "https://mne.tools/stable/generated/mne.make_fixed_length_epochs.html",
    },
    input_type=DataType.RAW,
    output_type=DataType.EPOCHS,
)
