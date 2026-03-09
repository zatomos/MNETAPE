"""Detect events action."""

from mnetape.actions.base import action_from_templates
from mnetape.actions.detect_events.widgets import ecg_channel_factory, eog_channel_factory

ACTION = action_from_templates(
    action_id="detect_events",
    title="Detect Events",
    doc="Find ECG or EOG events and append them to raw.annotations for use in downstream steps.",
    action_file=__file__,
    mne_doc_urls={
        "mne.preprocessing.find_ecg_events": "https://mne.tools/stable/generated/mne.preprocessing.find_ecg_events.html",
        "mne.preprocessing.find_eog_events": "https://mne.tools/stable/generated/mne.preprocessing.find_eog_events.html",
    },
    param_widget_factories={
        "ecg_channel": ecg_channel_factory,
        "eog_channel": eog_channel_factory,
    },
)
