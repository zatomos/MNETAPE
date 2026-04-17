"""Event-based epochs action."""

from mnetape.actions.base import ParamMeta, action_from_templates

ACTION = action_from_templates(
    action_id="epoch_events",
    title="Event-Based Epochs",
    doc="Create epochs locked to event onsets.",
    variant_param="event_source",
    variant_param_meta=ParamMeta(
        type="choice",
        label="Source",
        description="How to extract events from the recording.",
        choices=["annotations", "stim", "file"],
        default="annotations",
    ),
    mne_doc_urls={
        "mne.Epochs": "https://mne.tools/stable/generated/mne.Epochs.html",
        "mne.events_from_annotations": "https://mne.tools/stable/generated/mne.events_from_annotations.html",
        "mne.find_events": "https://mne.tools/stable/generated/mne.find_events.html",
        "mne.read_events": "https://mne.tools/stable/generated/mne.read_events.html",
    },
)
