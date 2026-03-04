"""Drop bad epochs action."""

from mnetape.actions.base import action_from_templates
from mnetape.actions.drop_bad_epochs.widgets import (
    flat_thresholds_factory,
    reject_thresholds_factory,
)
from mnetape.core.models import DataType

ACTION = action_from_templates(
    action_id="drop_bad_epochs",
    title="Drop Bad Epochs",
    doc="Remove epochs exceeding amplitude thresholds or use AutoReject for data-driven cleaning.",
    action_file=__file__,
    mne_doc_urls={
        "mne.Epochs.drop_bad": "https://mne.tools/stable/generated/mne.Epochs.html#mne.Epochs.drop_bad",
        "https://autoreject.github.io/stable/index.html"
    },
    input_type=DataType.EPOCHS,
    output_type=DataType.EPOCHS,
    param_widget_factories={
        "reject_thresholds": reject_thresholds_factory,
        "flat_thresholds": flat_thresholds_factory,
    },
)
