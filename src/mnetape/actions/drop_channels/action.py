"""Drop channels action."""

from mnetape.actions.base import ParamMeta, action_from_templates

ACTION = action_from_templates(
    action_id="drop_channels",
    title="Drop/Mark Bad Channels",
    variant_param="mode",
    variant_param_meta=ParamMeta(
        type="choice",
        label="Channel handling",
        description="drop: remove channels entirely. mark_bad: keep but flag as bad.",
        choices=["drop", "mark_bad"],
        default="mark_bad",
    ),
    doc=(
        "Remove specified channels from the data.\n\n"
        "mark_bad keeps channels in the data structure but flags them as bad, "
        "which excludes them from most analysis. "
        "drop removes them entirely and cannot be undone."
    ),
    mne_doc_urls={
        "Drop Channels": "https://mne.tools/stable/generated/mne.io.Raw.html#mne.io.Raw.drop_channels",
    },
)

