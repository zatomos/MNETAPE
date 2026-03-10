"""Detect events action."""

from mnetape.actions.base import action_from_templates

ACTION = action_from_templates(
    action_id="detect_events",
    title="Detect Events",
    doc="Find ECG or EOG events and append them to raw.annotations for use in downstream steps.",
    mne_doc_urls={
        "mne.preprocessing.find_ecg_events": "https://mne.tools/stable/generated/mne.preprocessing.find_ecg_events.html",
        "mne.preprocessing.find_eog_events": "https://mne.tools/stable/generated/mne.preprocessing.find_eog_events.html",
    },
)
