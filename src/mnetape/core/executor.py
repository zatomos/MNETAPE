"""Code execution for the EEG pipeline.

Provides a single entry point for executing generated action code inside a controlled Python scope.
"""

import logging
import mne
import numpy
from autoreject import AutoReject

from mnetape.core.models import ActionConfig, DataType, DATA_BUNDLERS

logger = logging.getLogger(__name__)

# Maps DataType to the variable name used in the exec scope
SCOPE_VAR: dict[DataType, str] = {
    DataType.RAW: "raw",
    DataType.EPOCHS: "epochs",
    DataType.EVOKED: "evoked",
    DataType.ICA: "ica",
}


def exec_action_code(
    code: str,
    data,
    action: ActionConfig,
    input_type: DataType = DataType.RAW,
    output_type: DataType = DataType.RAW,
):
    """Execute action code in a managed scope and return the resulting data object.

    The input data is injected when defined, otherwise under its SCOPE_VAR name.
    The output is extracted via DATA_BUNDLERS when registered, otherwise from SCOPE_VAR.

    Args:
        code: Python source code to execute.
        data: The current data object.
        action: The ActionConfig being executed.
        input_type: DataType of the incoming data; determines scope variable injection.
        output_type: DataType of the expected result; determines which scope variable to return.

    Returns:
        The data object found in scope after execution, reconstructed via the appropriate
        bundler when registered. Falls back to the input data when the output variable is absent.

    Raises:
        Exception: Re-raises any exception thrown by the executed code.
    """
    logger.debug("Executing action code for action_id=%s", action.action_id)

    scope: dict = {
        "mne": mne,
        "np": numpy,
        "numpy": numpy,
        "AutoReject": AutoReject,
    }

    if hasattr(data, "scope_vars"):
        scope.update(data.scope_vars())
    else:
        scope[SCOPE_VAR[input_type]] = data

    try:
        exec(code, scope, scope)
    except Exception:
        logger.exception("Action code execution failed for action_id=%s", action.action_id)
        raise

    if output_type in DATA_BUNDLERS:
        return DATA_BUNDLERS[output_type].from_scope(scope, data)

    out_var = SCOPE_VAR[output_type]
    return scope.get(out_var, data)
