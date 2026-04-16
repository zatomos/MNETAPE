"""Crop action."""

from mnetape.actions.base import ParamMeta, action_from_templates

ACTION = action_from_templates(
    action_id="crop",
    title="Crop",
    doc="Trim the recording to a selected time range.",
    variant_param="crop_mode",
    variant_param_meta=ParamMeta(
        type="choice",
        label="Mode",
        choices=["absolute", "trim"],
        default="absolute",
    ),
    mne_doc_urls={
        "Crop": "https://mne.tools/stable/generated/mne.io.Raw.html#mne.io.Raw.crop",
    },
)
