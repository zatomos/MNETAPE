"""Event-based epochs action."""

from mnetape.actions.base import action_from_templates
from mnetape.actions.epoch_events.widgets import (
    event_ids_factory,
    events_file_factory,
    stim_channel_factory,
)
from mnetape.core.models import DataType

ACTION = action_from_templates(
    action_id="epoch_events",
    title="Event-Based Epochs",
    doc="Create epochs locked to event onsets.",
    action_file=__file__,
    mne_doc_urls={
        "mne.Epochs": "https://mne.tools/stable/generated/mne.Epochs.html",
        "mne.events_from_annotations": "https://mne.tools/stable/generated/mne.events_from_annotations.html",
        "mne.find_events": "https://mne.tools/stable/generated/mne.find_events.html",
        "mne.read_events": "https://mne.tools/stable/generated/mne.read_events.html",
    },
    input_type=DataType.RAW,
    output_type=DataType.EPOCHS,
    param_widget_factories={
        "event_ids": event_ids_factory,
        "stim_channel": stim_channel_factory,
        "events_file": events_file_factory,
    },
)
