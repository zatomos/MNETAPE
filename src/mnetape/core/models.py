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


class ActionStatus(Enum):
    """Execution status of a pipeline action."""

    PENDING = auto()
    COMPLETE = auto()
    ERROR = auto()


class DataType(Enum):
    """Data type flowing through the pipeline at a given point."""

    RAW = "raw"
    EPOCHS = "epochs"
    EVOKED = "evoked"
    ICA = "ica_solution"


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


@dataclass
class ICASolution:
    """Bundle pairing a fitted ICA object with the raw data it was fitted on.

    Flows through the pipeline as DataType.ICA. Carries optional classification results.

    Attributes:
        ica: The fitted MNE ICA object.
        raw: The raw data the ICA was fitted on; used by ica_apply for source
            plotting and applying the decomposition.
        ic_labels: Optional ICLabel output dict.
        detected_artifacts: Optional sorted list of component indices flagged as artifacts.
    """

    ica: mne.preprocessing.ICA
    raw: mne.io.Raw
    ic_labels: dict | None = None
    detected_artifacts: list[int] | None = None

    def copy(self) -> ICASolution:
        """Return a copy with independent ica and raw objects."""
        return ICASolution(
            ica=copy.copy(self.ica),
            raw=self.raw.copy(),
            ic_labels=self.ic_labels,
            detected_artifacts=self.detected_artifacts,
        )

    def scope_vars(self) -> dict:
        """Variables to inject into the executor scope for this data type."""
        return {"ica": self.ica, "raw": self.raw}

    @classmethod
    def from_scope(cls, scope: dict, original: "ICASolution") -> "ICASolution":
        """Bundle a new ICASolution from exec scope variables."""
        if "ica" in scope:
            ica = scope["ica"]
        else:
            ica = getattr(original, "ica", None)
        if ica is None:
            raise RuntimeError("ICA action did not produce an 'ica' object in execution scope.")

        if "raw" in scope:
            raw = scope["raw"]
        else:
            raw = getattr(original, "raw", None)
        if raw is None:
            raise RuntimeError("ICA action did not provide a 'raw' object in execution scope.")

        return cls(
            ica=ica,
            raw=raw,
            ic_labels=scope.get("ic_labels"),
            detected_artifacts=scope.get("detected_component_artifacts"),
        )


# Registry mapping DataType to a bundler class.
# Used to reconstruct typed data objects after code execution without containing any type-specific logic itself.
DATA_BUNDLERS: dict[DataType, type] = {
    DataType.ICA: ICASolution,
}


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
