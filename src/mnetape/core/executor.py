"""Code execution for the EEG pipeline.

Provides exec_action as the primary entry point: runs a call-site string in a scope that
has function definitions preloaded, then extracts the output data object.
"""

import logging
import mne
import numpy

from mnetape.core.models import ActionConfig, DataType, ICASolution

logger = logging.getLogger(__name__)

# Maps DataType to the scope variable name used for non-ICA types
SCOPE_VAR: dict[DataType, str | None] = {
    DataType.RAW: "raw",
    DataType.EPOCHS: "epochs",
    DataType.EVOKED: "evoked",
    DataType.ICA: None,  # ICA is handled via structured unpacking
}

def exec_action(
    call_site: str,
    func_defs: str,
    data,
    action: ActionConfig,
    input_type: DataType = DataType.RAW,
    output_type: DataType = DataType.RAW,
):
    """Execute an action by running func_defs then call_site in a managed scope.

    The input data is injected by variable name (or unpacked for ICA).
    After execution, the output is extracted from scope and returned as the appropriate type.

    For custom actions (empty func_defs, arbitrary call_site), this degrades gracefully to
    direct exec of the call_site code.

    Args:
        call_site: Python statement to execute (e.g. assignment from a function call, or raw code).
        func_defs: Python source defining all needed functions (may be empty for custom code).
        data: The current data object.
        action: The ActionConfig being executed.
        input_type: DataType of the incoming data.
        output_type: DataType of the expected result.

    Returns:
        The data object extracted from scope after execution.

    Raises:
        Exception: Re-raises any exception thrown by the executed code.
    """
    logger.debug("Executing action_id=%s", action.action_id)

    scope: dict = {
        "mne": mne,
        "np": numpy,
        "numpy": numpy,
    }

    # Inject input data
    if input_type == DataType.ICA:
        if isinstance(data, ICASolution):
            scope["ica"] = data.ica
            scope["raw"] = data.raw
        else:
            scope["ica"] = data
            scope["raw"] = data
    else:
        var = SCOPE_VAR.get(input_type, "raw")
        scope[var] = data

    # Define functions
    if func_defs:
        try:
            exec(func_defs, scope, scope)
        except Exception:
            logger.exception("Failed to define functions for action_id=%s", action.action_id)
            raise

    # Run the call site / action code
    try:
        exec(call_site, scope, scope)
    except Exception:
        logger.exception("Action execution failed for action_id=%s", action.action_id)
        raise

    # Extract output
    if output_type == DataType.ICA:
        return ICASolution(
            ica=scope["ica"],
            raw=scope["raw"],
        )

    out_var = SCOPE_VAR.get(output_type, "raw")
    return scope.get(out_var, data)
