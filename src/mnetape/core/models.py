"""Data models for the EEG pipeline.

This module defines the shared data structures used throughout the pipeline.
"""

from dataclasses import dataclass, field
from enum import Enum, auto


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


@dataclass
class ActionConfig:
    """Mutable runtime configuration for a single preprocessing action.

    Stores all user-facing settings for one pipeline step: which action to run, its parameter values, execution status,
    and transient step state accumulated during multistep execution.

    Attributes:
        action_id: Identifier matching an entry in the action registry.
        params: Primary parameter values keyed by parameter name.
        status: Current execution status.
        error_msg: Human-readable description of the last error, if any.
        custom_code: User-edited code string; takes precedence over generated code when non-empty.
        is_custom: True when the code was manually edited and no longer matches the generated output.
        title_override: Display name shown in the action list, falling back to the action definition title when empty.
        advanced_params: Non-primary kwargs grouped by dotted MNE function name.
        completed_steps: Number of steps that have successfully run for multistep actions.
        step_state: Transient inter-step data excluded from repr to avoid cluttering logs.
    """

    action_id: str
    params: dict = field(default_factory=dict)
    status: ActionStatus = ActionStatus.PENDING
    error_msg: str = ""
    custom_code: str = ""
    is_custom: bool = False
    title_override: str = ""
    advanced_params: dict = field(default_factory=dict)
    completed_steps: int = 0
    step_state: dict = field(default_factory=dict, repr=False)

    def reset(self):
        """Reset action to pending state, clearing step progress and transient state."""
        self.status = ActionStatus.PENDING
        self.error_msg = ""
        self.completed_steps = 0
        self.step_state = {}