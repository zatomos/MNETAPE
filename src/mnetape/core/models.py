"""Data models for the EEG pipeline.

This module defines the shared data structures used throughout the pipeline.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import mne


CUSTOM_ACTION_ID = "custom"
"""Sentinel action_id for user-written inline code blocks."""

# -------- Action status --------

class ActionStatus(Enum):
    """Execution status of a pipeline action."""

    PENDING = auto()
    COMPLETE = auto()
    ERROR = auto()


STATUS_ICONS = {
    ActionStatus.PENDING: "\u25cb",   # ○
    ActionStatus.COMPLETE: "\u2713",  # ✓
    ActionStatus.ERROR: "\u2717",     # ✗
}
"""Unicode icon characters keyed by ActionStatus, used in action list widgets."""

STATUS_COLORS = {
    ActionStatus.PENDING: "#DAA520",
    ActionStatus.COMPLETE: "green",
    ActionStatus.ERROR: "red",
}
"""CSS color strings keyed by ActionStatus, used in action list widgets."""


#-------- Data types --------

class DataType(Enum):
    """Data type flowing through the pipeline at a given point."""

    RAW = "raw"
    EPOCHS = "epochs"
    EVOKED = "evoked"
    ICA = "ica_solution"

    @property
    def label(self) -> str:
        """Display name for use in UI headers and messages."""
        return {"RAW": "Raw", "EPOCHS": "Epochs", "EVOKED": "Evoked", "ICA": "ICA"}[self.name]


ANNOTATION_TO_DATATYPE: dict[str, DataType] = {
    "mne.io.Raw": DataType.RAW,
    "mne.BaseEpochs": DataType.EPOCHS,
    "mne.Epochs": DataType.EPOCHS,
    "mne.Evoked": DataType.EVOKED,
    "mne.preprocessing.ICA": DataType.ICA,
}
"""Maps supported builder annotation names to pipeline DataType values."""


RETURN_VARS: dict[DataType, str] = {
    DataType.RAW: "raw",
    DataType.EPOCHS: "epochs",
    DataType.EVOKED: "evoked",
    DataType.ICA: "ica, raw, ic_labels",
}
"""Assignment targets used when generating action call sites."""


@dataclass
class ICASolution:
    """Bundle pairing a fitted ICA object with the raw data it was fitted on.

    Flows through the pipeline as DataType.ICA. Carries optional classification results.

    Attributes:
        ica: The fitted MNE ICA object.
        raw: The raw data the ICA was fitted on; used by ica_apply for source
            plotting and applying the decomposition.
        ic_labels: Optional dict produced by ica_classify. Contains keys such as
            "labels", "y_pred_proba", and "detected_artifacts" (a sorted list of
            component indices flagged as artifacts).
    """

    ica: mne.preprocessing.ICA
    raw: mne.io.Raw
    ic_labels: dict | None = None

    @property
    def detected_artifacts(self) -> list[int] | None:
        """Sorted list of artifact component indices."""
        if isinstance(self.ic_labels, dict):
            return self.ic_labels.get("detected_artifacts")
        return None

    def copy(self) -> ICASolution:
        """Return a copy with independent ica and raw objects."""
        return ICASolution(
            ica=copy.copy(self.ica),
            raw=self.raw.copy(),
            ic_labels=self.ic_labels,
        )


# ------- Action configuration --------

@dataclass
class ActionConfig:
    """Mutable runtime configuration for a single preprocessing action.

    Stores all user-facing settings for one pipeline step: which action to run, its parameter values, execution status,
    and any user-customized code.

    Attributes:
        action_id: Identifier matching an entry in the action registry.
        params: Primary parameter values keyed by parameter name.
        status: Current execution status.
        error_msg: Human-readable description of the last error, if any.
        custom_code: User-edited code string; takes precedence over generated code when non-empty.
        is_custom: True when the code was manually edited and no longer matches the generated output.
        title_override: Display name shown in the action list, falling back to the action definition title when empty.
        advanced_params: Non-primary kwargs grouped by dotted MNE function name.
    """

    action_id: str
    params: dict = field(default_factory=dict)
    status: ActionStatus = ActionStatus.PENDING
    error_msg: str = ""
    custom_code: str = ""
    is_custom: bool = False
    title_override: str = ""
    advanced_params: dict = field(default_factory=dict)

    def reset(self):
        """Reset action to pending state."""
        self.status = ActionStatus.PENDING
        self.error_msg = ""
