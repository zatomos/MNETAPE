"""Set annotations action."""

from mnetape.actions.base import action_from_templates
from mnetape.actions.set_annotations.widgets import annotations_factory
from mnetape.core.models import DataType

ACTION = action_from_templates(
    action_id="set_annotations",
    title="Set Annotations",
    doc="Apply a set of time annotations to the raw recording.",
    action_file=__file__,
    mne_doc_urls={
        "set_annotations": "https://mne.tools/stable/generated/mne.io.Raw.html#mne.io.Raw.set_annotations",
    },
    input_type=DataType.RAW,
    output_type=DataType.RAW,
    param_widget_factories={"annotations": annotations_factory},
)
