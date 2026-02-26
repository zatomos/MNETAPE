"""Drop channels action."""

from mnetape.actions.base import action_from_templates
from mnetape.actions.drop_channels.widgets import channels_widget_factory

ACTION = action_from_templates(
    action_id="drop_channels",
    title="Drop Channels",
    action_file=__file__,
    doc=(
        "Remove specified channels from the data.\n\n"
        "mark_bad keeps channels in the data structure but flags them as bad, "
        "which excludes them from most analysis. "
        "drop removes them entirely and cannot be undone."
    ),
    mne_doc_urls={
        "Drop Channels": "https://mne.tools/stable/generated/mne.io.Raw.html#mne.io.Raw.drop_channels",
    },
    param_widget_factories={"channels": channels_widget_factory},
)

