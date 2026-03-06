"""ICA apply action: sets the component exclusion list and applies ICA to produce clean raw data."""

from mnetape.actions.base import Prerequisite, action_from_templates
from mnetape.actions.ica_apply.widgets import exclude_components_factory
from mnetape.core.models import DataType

ACTION = action_from_templates(
    action_id="ica_apply",
    title="Apply ICA",
    doc=(
        "Set the ICA component exclusion list and apply it to produce clean raw data. "
        "Use 'Browse Components' in the editor to open the inspection dialog and select "
        "which components to remove. The exclusion list is stored in the generated code."
    ),
    action_file=__file__,
    mne_doc_urls={
        "ICA.apply": "https://mne.tools/stable/generated/mne.preprocessing.ICA.html#mne.preprocessing.ICA.apply",
    },
    prerequisites=(
        Prerequisite("ica_fit", "ICA must be fitted before it can be applied."),
    ),
    param_widget_factories={"exclude_components": exclude_components_factory},
    input_type=DataType.ICA,
    output_type=DataType.RAW,
)
