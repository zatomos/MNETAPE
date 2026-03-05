"""Code execution for the EEG pipeline.

Provides a single entry point for executing generated action code inside a controlled Python scope that exposes
mne, numpy, and the current raw object.
The scope is persisted on the action's step_state so that multistep actions can share variables (e.g. an ICA object
created in step 1 and used in step 2).
"""

import logging
import mne
import numpy
from autoreject import AutoReject

from mnetape.core.models import ActionConfig, DataType

logger = logging.getLogger(__name__)

# Maps DataType to the variable name used in the exec scope
SCOPE_VAR: dict[DataType, str] = {
    DataType.RAW: "raw",
    DataType.EPOCHS: "epochs",
    DataType.EVOKED: "evoked",
}


def exec_action_code(
    code: str,
    data: mne.io.Raw | mne.Epochs | mne.Evoked,
    action: ActionConfig,
    reuse_scope: bool = False,
    input_type: DataType = DataType.RAW,
    output_type: DataType = DataType.RAW,
) -> mne.io.Raw | mne.Epochs | mne.Evoked:
    """Execute action code in a managed scope and return the resulting data object.

    The scope always exposes mne, np, numpy, and the input data under its type-appropriate variable name.
    When reuse_scope is True and a prior scope exists on the action, that scope is reused so variables assigned in
    earlier steps remain accessible.

    After execution the scope is stored so subsequent steps can access intermediate results.

    Args:
        code: Python source code to execute.
        data: The current data object. Injected into the scope.
        action: The action whose step_state will hold the scope after execution.
        reuse_scope: When True, reuse the existing scope from a previous step
            rather than creating a fresh one.
        input_type: DataType of the incoming data; determines the scope variable name on entry.
        output_type: DataType of the expected result; determines which scope variable to return.

    Returns:
        The data object found in scope after execution under SCOPE_VAR[output_type].
        Falls back to the input data when the output variable is absent.

    Raises:
        Exception: Re-raises any exception thrown by the executed code.
    """

    in_var = SCOPE_VAR[input_type]
    out_var = SCOPE_VAR[output_type]

    logger.debug("Executing action code (reuse_scope=%s) for action_id=%s", reuse_scope, action.action_id)

    if reuse_scope and action.step_state.get("scope"):
        scope = action.step_state["scope"]
        scope[in_var] = data
    else:
        scope = {
            in_var: data,
            "mne": mne,
            "np": numpy,
            "numpy": numpy,
            # Backward compatibility for older action snippets using AutoReject directly.
            "AutoReject": AutoReject,
        }
    try:
        exec(code, scope, scope)
    except Exception:
        logger.exception("Action code execution failed for action_id=%s", action.action_id)
        raise
    action.step_state["scope"] = scope
    return scope.get(out_var, data)
