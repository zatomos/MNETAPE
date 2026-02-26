"""ICA action templates: fit, classify, and apply steps.

Defines three pipeline steps:
- fit_builder: fits an ICA decomposition using extended infomax or another method.
- classify_builder: classifies components via ICLabel, EOG, ECG, and muscle detection.
- apply_builder: interactive step that opens the component inspector and applies
  the resulting exclusion list to the raw data.

Fragment functions (_fit_all_eeg, _fit_fixed, _classify) have their bodies
extracted by the Fragment system and must not contain docstrings.
"""

from __future__ import annotations

from typing import Annotated

import mne

from mnetape.actions.base import ParamMeta, fragment, step


# -------- Fit step --------

@fragment
def _fit_all_eeg(raw, method: str = "infomax", fit_params: dict | None = None) -> None:
    ica = mne.preprocessing.ICA(
        n_components=len(mne.pick_types(raw.info, eeg=True)),
        method=method,
        random_state=42,
        fit_params=fit_params,
    )
    ica.fit(raw)


@fragment
def _fit_fixed(
    raw, n_components: int = 20, method: str = "infomax", fit_params: dict | None = None
) -> None:
    ica = mne.preprocessing.ICA(
        n_components=n_components,
        method=method,
        random_state=42,
        fit_params=fit_params,
    )
    ica.fit(raw)


@step("fit", title="Fit ICA")
def fit_builder(
    n_components: Annotated[
        int,
        ParamMeta(
            type="int",
            label="Components (0 = all channels)",
            description="Number of ICA components. 0 means use number of EEG channels.",
            default=0,
            min=0,
            max=999,
        ),
    ] = 0,
    method: Annotated[
        str,
        ParamMeta(
            type="choice",
            choices=["fastica", "infomax", "picard"],
            label="Method",
            description="ICA algorithm to use. Infomax (extended) recommended for ICLabel.",
            default="infomax",
        ),
    ] = "infomax",
) -> str:
    """Generate code to fit ICA."""
    fit_params = {"extended": True} if method == "infomax" else None
    if int(n_components) == 0:
        return _fit_all_eeg.inline(method=method, fit_params=fit_params)
    return _fit_fixed.inline(
        n_components=int(n_components),
        method=method,
        fit_params=fit_params,
    )


# -------- Classify step --------

@fragment
def _classify(
    raw,
    ica,
    enable_iclabel: bool = True,
    iclabel_threshold: float = 0.5,
    enable_eog: bool = True,
    eog_threshold: float = 3.0,
    enable_ecg: bool = True,
    ecg_threshold: float = 0.25,
    enable_muscle: bool = True,
    muscle_threshold: float = 0.9,
) -> None:
    import warnings
    import numpy as np

    def _channel_meta(kind: str) -> tuple[bool, list[str]]:
        picked = mne.pick_types(raw.info, exclude=[], **{kind: True})
        has = len(picked) > 0
        names = [raw.ch_names[ch] for ch in picked] if has else []
        return has, names

    def _run_detector(label: str, fn):
        try:
            idx, scores = fn()
            return list(idx), scores
        except Exception as err:
            warnings.warn(f"{label} detection failed: {err}", RuntimeWarning, stacklevel=1)
            return [], None

    exclude: list[int] = []
    eog_indices: list[int] = []
    eog_scores = None
    ecg_indices: list[int] = []
    ecg_scores = None
    muscle_indices: list[int] = []
    muscle_scores = None
    has_eog_channel, eog_channel_names = _channel_meta("eog")
    has_ecg_channel, ecg_channel_names = _channel_meta("ecg")

    if enable_iclabel:
        try:
            from mne_icalabel import label_components
            raw_for_label = raw.copy()
            raw_for_label.filter(l_freq=1.0, h_freq=100.0, verbose=False)
            raw_for_label.resample(100, verbose=False)
            ic_labels = label_components(raw_for_label, ica, method='iclabel')
        except Exception as _ic_err:
            warnings.warn(f'ICLabel classification failed: {_ic_err}', RuntimeWarning, stacklevel=1)
            ic_labels = None
        if ic_labels is not None:
            labels = ic_labels['labels']
            probs = ic_labels['y_pred_proba']
            for i, (label, prob) in enumerate(zip(labels, probs)):
                if label != 'brain' and np.max(prob) >= iclabel_threshold:
                    exclude.append(i)

    if enable_eog:
        eog_indices, eog_scores = _run_detector(
            "EOG",
            lambda: ica.find_bads_eog(raw, threshold=eog_threshold, verbose=False),
        )

    if enable_ecg:
        ecg_indices, ecg_scores = _run_detector(
            "ECG",
            lambda: ica.find_bads_ecg(
                raw,
                method='correlation',
                threshold=ecg_threshold,
                verbose=False,
            ),
        )

    if enable_muscle:
        muscle_indices, muscle_scores = _run_detector(
            "Muscle",
            lambda: ica.find_bads_muscle(raw, threshold=muscle_threshold, verbose=False),
        )

    auto_exclude = sorted(set(exclude) | set(eog_indices) | set(ecg_indices) | set(muscle_indices))
    ica.exclude = auto_exclude
    detection_details = {
        'eog_scores': eog_scores,
        'ecg_scores': ecg_scores,
        'muscle_scores': muscle_scores,
        'eog_indices': eog_indices,
        'ecg_indices': ecg_indices,
        'muscle_indices': muscle_indices,
        'has_eog_channel': has_eog_channel,
        'has_ecg_channel': has_ecg_channel,
        'eog_channel_names': eog_channel_names,
        'ecg_channel_names': ecg_channel_names,
        'enable_iclabel': enable_iclabel,
        'enable_eog': enable_eog,
        'enable_ecg': enable_ecg,
        'enable_muscle': enable_muscle,
    }


@step("classify", title="Classify Components")
def classify_builder(
    enable_iclabel: Annotated[
        bool,
        ParamMeta(
            type="bool",
            label="Enable ICLabel",
            description="Use ICLabel neural network to classify components (brain, eye, heart, muscle, etc.).",
            default=True,
        ),
    ] = True,
    iclabel_threshold: Annotated[
        float,
        ParamMeta(
            type="float",
            label="ICLabel threshold",
            description="Probability threshold for ICLabel exclusion. Components classified as non-brain above this threshold are excluded.",
            default=0.5,
            min=0.0,
            max=1.0,
        ),
    ] = 0.5,
    enable_eog: Annotated[
        bool,
        ParamMeta(
            type="bool",
            label="Enable EOG detection",
            description="Detect ocular components via EOG channel correlation.",
            default=True,
        ),
    ] = True,
    eog_threshold: Annotated[
        float,
        ParamMeta(
            type="float",
            label="EOG threshold",
            description="Z-score threshold for EOG correlation detection.",
            default=3.0,
            min=0.5,
            max=10.0,
        ),
    ] = 3.0,
    enable_ecg: Annotated[
        bool,
        ParamMeta(
            type="bool",
            label="Enable ECG detection",
            description="Detect cardiac components via ECG channel correlation.",
            default=True,
        ),
    ] = True,
    ecg_threshold: Annotated[
        float,
        ParamMeta(
            type="float",
            label="ECG threshold",
            description="Correlation threshold for ECG detection (method='correlation').",
            default=0.25,
            min=0.01,
            max=1.0,
        ),
    ] = 0.25,
    enable_muscle: Annotated[
        bool,
        ParamMeta(
            type="bool",
            label="Enable muscle detection",
            description="Detect muscle artifact components via high-frequency power.",
            default=True,
        ),
    ] = True,
    muscle_threshold: Annotated[
        float,
        ParamMeta(
            type="float",
            label="Muscle threshold",
            description="Z-score threshold for muscle artifact detection.",
            default=0.9,
            min=0.1,
            max=5.0,
        ),
    ] = 0.9,
) -> str:
    """Generate code to classify ICA components."""
    return _classify.inline(
        enable_iclabel=enable_iclabel,
        iclabel_threshold=iclabel_threshold,
        enable_eog=enable_eog,
        eog_threshold=eog_threshold,
        enable_ecg=enable_ecg,
        ecg_threshold=ecg_threshold,
        enable_muscle=enable_muscle,
        muscle_threshold=muscle_threshold,
    )


# -------- Apply step --------

@step("inspect", title="Manual Selection", interactive=True)
def apply_builder() -> str:
    """Generate code to apply ICA (remove excluded components)."""
    return "raw = ica.apply(raw, verbose=False)"
