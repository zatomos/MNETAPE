"""ICA component classification helpers.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def run_background_classification(ica, raw) -> dict:
    """Run ICLabel, EOG, and ECG detection in-process.

    Returns:
        ic_labels dict with "detected_artifacts" key.
    """
    import mne
    import numpy as np

    ic_labels: dict = {}
    detected: list[int] = []

    try:
        from mne_icalabel import label_components
        label_result = label_components(raw, ica, method="iclabel")
        ic_labels.update(label_result)
        detected.extend(
            i for i, (lbl, prob) in enumerate(zip(label_result["labels"], label_result["y_pred_proba"]))
            if lbl != "brain" and np.max(prob) >= 0.5
        )
        logger.debug("ICLabel classification completed (%d components flagged)", len(detected))
    except Exception as e:
        logger.debug("ICLabel not available or failed, skipping: %s", e)

    if mne.pick_types(raw.info, eog=True).size > 0:
        try:
            eog_indices, _ = ica.find_bads_eog(raw, threshold=3.0, verbose=False)
            detected.extend(list(eog_indices))
            logger.debug("EOG detection: %d components flagged", len(eog_indices))
        except Exception as e:
            logger.debug("EOG detection failed: %s", e)

    if mne.pick_types(raw.info, ecg=True).size > 0:
        try:
            ecg_indices, _ = ica.find_bads_ecg(raw, method="correlation", threshold=0.25, verbose=False)
            detected.extend(list(ecg_indices))
            logger.debug("ECG detection: %d components flagged", len(ecg_indices))
        except Exception as e:
            logger.debug("ECG detection failed: %s", e)

    ic_labels["detected_artifacts"] = sorted(set(detected))
    return ic_labels


def get_auto_exclude(ic_labels: dict | None) -> list[int]:
    return list(ic_labels.get("detected_artifacts", [])) if ic_labels else []
