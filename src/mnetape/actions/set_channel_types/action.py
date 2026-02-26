"""Set channel types action."""

from mnetape.actions.base import action_from_templates
from mnetape.actions.set_channel_types.widgets import channel_types_widget_factory

ACTION = action_from_templates(
    action_id="set_channel_types",
    title="Set Channel Types",
    doc="Set channel types for specified channels.",
    action_file=__file__,
    mne_doc_urls={
        "Set Channel Types": "https://mne.tools/stable/generated/mne.io.Raw.html#mne.io.Raw.set_channel_types"
    },
    param_widget_factories={"channel_types": channel_types_widget_factory},
)
