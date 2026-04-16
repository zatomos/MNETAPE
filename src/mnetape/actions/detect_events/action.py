"""Detect events action."""

from mnetape.actions.base import ParamMeta, action_from_templates

ACTION = action_from_templates(
    action_id="detect_events",
    title="Detect Events",
    doc="Find ECG or EOG events and append them to raw.annotations for use in downstream steps.",
    variant_param="method",
    variant_param_meta=ParamMeta(
        type="choice",
        label="Method",
        description="ECG: locate R-wave peaks. EOG: locate eye-blink peaks. Threshold: annotate segments exceeding an amplitude threshold.",
        choices=["ecg", "eog", "threshold"],
        default="eog",
    ),
    mne_doc_urls={
        "mne.preprocessing.find_ecg_events": "https://mne.tools/stable/generated/mne.preprocessing.find_ecg_events.html",
        "mne.preprocessing.find_eog_events": "https://mne.tools/stable/generated/mne.preprocessing.find_eog_events.html",
    },
)
