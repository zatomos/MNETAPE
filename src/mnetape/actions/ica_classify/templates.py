"""ICA classify action templates.

Runs automatic component classification using any combination of ICLabel, EOG, ECG, and muscle detection.
Stores all results in the ic_labels dict, including a "detected_artifacts" key with the combined flagged indices.
"""

from __future__ import annotations

from typing import Annotated

import mne
from mnetape.actions.base import ParamMeta, builder, result_builder

@builder
def template_builder(
    ica: mne.preprocessing.ICA, raw: mne.io.Raw, ic_labels: dict | None,
    enable_iclabel: Annotated[
        bool,
        ParamMeta(
            type="bool",
            label="Enable ICLabel",
            description="Use ICLabel neural network to label components.",
            default=True,
        ),
    ] = True,
    iclabel_threshold: Annotated[
        float,
        ParamMeta(
            type="float",
            label="ICLabel threshold",
            description="Probability threshold above which a non-brain component is flagged.",
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
            description="Correlation threshold for ECG detection.",
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
) -> tuple[mne.preprocessing.ICA, mne.io.Raw, dict | None]:
    ic_labels = {}
    detected = []
    if enable_iclabel:
        import numpy as np
        from mne_icalabel import label_components
        raw_lbl = raw.copy()
        raw_lbl.filter(l_freq=1.0, h_freq=100.0, verbose=False)
        raw_lbl.resample(100, verbose=False)
        label_result = label_components(raw_lbl, ica, method='iclabel')
        ic_labels.update(label_result)
        detected.extend(
            i for i, (lbl, prob) in enumerate(zip(label_result['labels'], label_result['y_pred_proba']))
            if lbl != 'brain' and np.max(prob) >= iclabel_threshold
        )
    if enable_eog:
        eog_indices, _ = ica.find_bads_eog(raw, threshold=eog_threshold, verbose=False)
        detected.extend(list(eog_indices))
    if enable_ecg:
        ecg_indices, _ = ica.find_bads_ecg(raw, method='correlation', threshold=ecg_threshold, verbose=False)
        detected.extend(list(ecg_indices))
    if enable_muscle:
        muscle_indices, _ = ica.find_bads_muscle(raw, threshold=muscle_threshold, verbose=False)
        detected.extend(list(muscle_indices))
    ic_labels["detected_artifacts"] = sorted(set(detected))
    return ica, raw, ic_labels

@result_builder
def build_result(data):
    import logging
    import numpy as np
    import mne
    from matplotlib.figure import Figure
    from mnetape.core.models import ActionResult

    logger = logging.getLogger(__name__)

    if data.ic_labels is None:
        return ActionResult(summary="No classification data available.")

    ic_labels = data.ic_labels
    detected = set(ic_labels.get("detected_artifacts", []))
    ica = data.ica
    n_comp = ica.n_components_

    iclabels = ic_labels.get("labels", [])
    proba = np.asarray(ic_labels.get("y_pred_proba", []), dtype=float)
    has_proba = proba.ndim == 2 and len(proba) == n_comp

    n_cols = min(10, n_comp)
    n_rows = (n_comp + n_cols - 1) // n_cols
    fig = Figure(figsize=(n_cols * 1.6, n_rows * 2.1 + 0.5))

    try:
        components = ica.get_components()  # (n_channels, n_components)
        for i in range(n_comp):
            ax = fig.add_subplot(n_rows, n_cols, i + 1)
            mne.viz.plot_topomap(
                components[:, i], ica.info, axes=ax,
                show=False, contours=4, sensors=False,
            )
            lbl = iclabels[i] if i < len(iclabels) else ""
            pct = f"\n{np.max(proba[i]) * 100:.0f}%" if has_proba else ""
            title = f"IC{i}\n{lbl}{pct}" if lbl else f"IC{i}{pct}"
            color = "red" if i in detected else "black"
            ax.set_title(title, fontsize=7, color=color, pad=2)
            if i in detected:
                for spine in ax.spines.values():
                    spine.set_edgecolor("red")
                    spine.set_linewidth(1.5)
        fig.tight_layout(pad=0.4)
    except Exception as e:
        logger.warning("ICA topomap plot failed: %s", e)
        fig = None

    n_detected = len(detected)
    summary = f"{n_detected} of {n_comp} component{'s' if n_comp != 1 else ''} flagged as artifacts"
    details = {"Flagged indices": ", ".join(str(i) for i in sorted(detected)) if detected else "None"}
    return ActionResult(summary=summary, fig=fig, details=details)
