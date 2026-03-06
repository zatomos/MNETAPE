"""Detect events action templates.

Two methods:
  - ecg: find heartbeat R-wave peaks via mne.preprocessing.find_ecg_events
  - eog: find eye-blink peaks via mne.preprocessing.find_eog_events

Detected events are appended to raw.annotations so they can be used downstream.
"""

from __future__ import annotations

from typing import Annotated, TYPE_CHECKING

from mnetape.actions.base import ParamMeta, builder, fragment

if TYPE_CHECKING:
    import mne


@fragment
def _do_find_ecg(raw, ch_name, annotation_label) -> None:
    ecg_events, _, _ = mne.preprocessing.find_ecg_events(raw, ch_name=ch_name, event_id=999)
    new_annotations = mne.annotations_from_events(
        ecg_events, raw.info['sfreq'],
        event_desc={999: annotation_label},
        first_samp=raw.first_samp,
    )
    raw.set_annotations(raw.annotations + new_annotations)


@fragment
def _do_find_eog(raw, ch_name, annotation_label) -> None:
    eog_events = mne.preprocessing.find_eog_events(raw, ch_name=ch_name, event_id=998)
    new_annotations = mne.annotations_from_events(
        eog_events, raw.info['sfreq'],
        event_desc={998: annotation_label},
        first_samp=raw.first_samp,
    )
    raw.set_annotations(raw.annotations + new_annotations)


@builder
def template_builder(
    method: Annotated[
        str,
        ParamMeta(
            type="choice",
            label="Method",
            description="ECG: locate R-wave peaks. EOG: locate eye-blink peaks.",
            choices=["ecg", "eog"],
            default="eog",
        ),
    ] = "eog",
    ecg_label: Annotated[
        str,
        ParamMeta(
            type="text",
            label="Annotation label",
            description="Label used for the added annotations.",
            default="ECG",
            visible_when={"method": ["ecg"]},
        ),
    ] = "ECG",
    eog_label: Annotated[
        str,
        ParamMeta(
            type="text",
            label="Annotation label",
            description="Label used for the added annotations.",
            default="EOG",
            visible_when={"method": ["eog"]},
        ),
    ] = "EOG",
    ecg_channel: Annotated[
        str,
        ParamMeta(
            type="ecg_channel",
            label="Channel",
            description="ECG channel to use for R-wave detection. Auto-selected if an ECG-type channel is present.",
            default="",
            visible_when={"method": ["ecg"]},
        ),
    ] = "",
    eog_channel: Annotated[
        str,
        ParamMeta(
            type="eog_channel",
            label="Channel",
            description="EOG channel to use for blink detection. Auto-selected if an EOG-type channel is present.",
            default="",
            visible_when={"method": ["eog"]},
        ),
    ] = "",
) -> str:
    if method == "ecg":
        return _do_find_ecg.inline(
            ch_name=ecg_channel or None,
            annotation_label=ecg_label,
        )
    return _do_find_eog.inline(
        ch_name=eog_channel or None,
        annotation_label=eog_label,
    )
