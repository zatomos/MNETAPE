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
    from matplotlib.figure import Figure

CUSTOM_ACTION_ID = "custom"

# Variables always available in the exec scope, excluded from action param schemas.
SCOPE_VARS: frozenset[str] = frozenset({"raw", "epochs", "evoked", "ica", "ic_labels"})

# -------- Action status --------

class ActionStatus(Enum):
    """Execution status of a pipeline action."""

    PENDING = auto()
    COMPLETE = auto()
    ERROR = auto()

# Unicode icon characters keyed by ActionStatus, used in action list widgets.
STATUS_ICONS = {
    ActionStatus.PENDING: "\u25cb",   # ○
    ActionStatus.COMPLETE: "\u2713",  # ✓
    ActionStatus.ERROR: "\u2717",     # ✗
}

# CSS color strings keyed by ActionStatus, used in action list widgets.
STATUS_COLORS = {
    ActionStatus.PENDING: "#DAA520",
    ActionStatus.COMPLETE: "green",
    ActionStatus.ERROR: "red",
}

#-------- Data types --------

class DataType(Enum):
    """Data type flowing through the pipeline at a given point."""

    RAW = "raw"
    EPOCHS = "epochs"
    EVOKED = "evoked"
    ANY = "any"
    ICA = "ica_solution"

    @property
    def label(self) -> str:
        """Display name for use in UI headers and messages."""
        return {"RAW": "Raw", "EPOCHS": "Epochs", "EVOKED": "Evoked", "ANY": "Any", "ICA": "ICA"}[self.name]

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

TYPE_TO_SCOPE_VAR: dict[DataType, str] = {
    DataType.RAW: "raw",
    DataType.EPOCHS: "epochs",
    DataType.EVOKED: "evoked",
}
"""Maps concrete DataType values to their pipeline scope variable names."""

@dataclass
class ICASolution:
    """Bundle pairing a fitted ICA object with the raw data it was fitted on.

    Flows through the pipeline as DataType.ICA. Carries optional classification results.

    Attributes:
        ica: The fitted MNE ICA object.
        raw: The raw data the ICA was fitted on; used by ica_apply for source
            plotting and applying the decomposition.
        ic_labels: Transient classification cache populated by ica_apply before opening the inspection
            dialog. Contains keys such as "labels", "y_pred_proba", and "detected_artifacts" (a sorted
            list of component indices flagged as artifacts). Not persisted to the pipeline script.
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

# ------- Action result --------

@dataclass
class ActionResult:
    """Feedback data produced by an action after execution.

    Attributes:
        summary: Description of the results.
        fig: Matplotlib Figure to display, or None if no plot was produced.
        details: Optional key/value pairs shown below the summary.
    """

    summary: str
    fig: Figure | None = None
    details: dict = field(default_factory=dict)

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
    result: ActionResult | None = None

    def reset(self):
        """Reset action to pending state."""
        self.status = ActionStatus.PENDING
        self.error_msg = ""
        self.result = None
