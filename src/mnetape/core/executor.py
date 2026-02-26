"""Code execution for the EEG pipeline.

Provides a single entry point for executing generated action code inside a controlled Python scope that exposes
mne, numpy, and the current raw object.
The scope is persisted on the action's step_state so that multistep actions can share variables (e.g. an ICA object
created in step 1 and used in step 2).
"""

import logging
import mne
import numpy

from mnetape.core.models import ActionConfig

logger = logging.getLogger(__name__)


def exec_action_code(
    code: str,
    raw: mne.io.Raw,
    action: ActionConfig,
    reuse_scope: bool = False,
) -> mne.io.Raw:
    """Execute action code in a managed scope and return the resulting raw object.

    The scope always exposes raw, mne, np, and numpy. When reuse_scope is True and a prior scope exists on the action,
    that scope is reused so variables assigned in earlier steps remain accessible.

    After execution the scope is stored so subsequent steps can access intermediate results.

    Args:
        code: Python source code to execute.
        raw: The current raw object. Injected into the scope as the "raw" variable.
        action: The action whose step_state will hold the scope after execution.
        reuse_scope: When True, reuse the existing scope from a previous step
            rather than creating a fresh one.

    Returns:
        The raw object found in scope after execution. If the code replaced the raw variable, the new value is returned;
        otherwise the original raw is returned unchanged.

    Raises:
        Exception: Re-raises any exception thrown by the executed code.
    """

    logger.debug("Executing action code (reuse_scope=%s) for action_id=%s", reuse_scope, action.action_id)

    if reuse_scope and action.step_state.get("scope"):
        scope = action.step_state["scope"]
        scope["raw"] = raw
    else:
        scope = {"raw": raw, "mne": mne, "np": numpy, "numpy": numpy}
    try:
        exec(code, scope, scope)
    except Exception:
        logger.exception("Action code execution failed for action_id=%s", action.action_id)
        raise
    action.step_state["scope"] = scope
    return scope.get("raw", raw)
