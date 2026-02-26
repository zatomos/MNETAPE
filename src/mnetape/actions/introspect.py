"""Introspect MNE function signatures to discover advanced kwargs.

At runtime, the action editor calls get_advanced_params() to enumerate all keyword arguments of an MNE function
that are not already covered by the action's primary params_schema. These are presented to the user as an
expandable "Advanced" section in the editor.
"""

from __future__ import annotations

import inspect
import logging
from typing import Any, Callable

logger = logging.getLogger(__name__)

# Parameters to always exclude from advanced params
EXCLUDED_PARAMS = frozenset({
    "self", "return", "inst", "raw", "epochs",
    "verbose", "n_jobs",
})


def resolve_method(dotted_name: str) -> Callable | None:
    """Resolve a dotted name string to a callable MNE object.

    Supported root prefixes:

    - raw.* - methods on mne.io.Raw
    - ica.* - methods on mne.preprocessing.ICA
    - mne.* - any object reachable under the mne module

    Args:
        dotted_name: A dotted path string.

    Returns:
        The resolved callable, or None when the name cannot be resolved or MNE is not installed.
    """

    try:
        import mne
        import mne.preprocessing
    except Exception as e:
        logger.exception("Failed to import MNE: %s", e)
        return None

    if dotted_name.startswith("raw."):
        obj: Any = mne.io.Raw
        path = dotted_name.split(".")[1:]
    elif dotted_name.startswith("ica."):
        obj = mne.preprocessing.ICA
        path = dotted_name.split(".")[1:]
    elif dotted_name.startswith("mne."):
        obj = mne
        path = dotted_name.split(".")[1:]
    else:
        return None

    try:
        for part in path:
            obj = getattr(obj, part)
    except Exception as e:
        logger.warning("Failed to resolve '%s': %s", dotted_name, e)
        return None

    # If class
    if inspect.isclass(obj):
        return obj.__init__
    # If function or method
    return obj if callable(obj) else None


def get_advanced_params(
    dotted_name: str,
    primary_param_names: frozenset[str],
) -> dict[str, dict]:
    """Discover advanced kwargs for a function, excluding primary params.

    Args:
        dotted_name: Dotted function path to introspect.
        primary_param_names: Parameter names already covered by the action's primary params_schema;
            these are excluded from the result.

    Returns:
        A params_schema-compatible dict of advanced parameters with inferred
        widget types and constraints.
    """

    func = resolve_method(dotted_name)
    if func is None:
        return {}

    try:
        sig = inspect.signature(func)
    except (ValueError, TypeError):
        return {}

    result: dict[str, dict] = {}
    for name, param in sig.parameters.items():
        if name in EXCLUDED_PARAMS or name in primary_param_names:
            continue
        if param.kind in (
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        ):
            continue

        spec = infer_param_spec(name, param)
        if spec is not None:
            result[name] = spec

    return result


def infer_param_spec(name: str, param: inspect.Parameter) -> dict[str, Any] | None:
    """Infer a params_schema-compatible spec dict from an inspect.Parameter.

    Widget type and constraints are determined from the parameter's default value.
    Parameters with no default and parameters whose type cannot be inferred are exposed as nullable text fields.

    Args:
        name: The parameter name.
        param: The inspect.Parameter object from the function signature.

    Returns:
        A params_schema entry dict, or None when the parameter should be skipped.
    """

    default = param.default
    has_default = default is not inspect.Parameter.empty

    spec: dict[str, Any] = {
        "label": name.replace("_", " ").title(),
    }

    # Determine type from annotation or default
    if has_default:
        spec["default"] = default
        if default is None:
            spec["type"] = "text"
            spec["nullable"] = True
        elif isinstance(default, bool):
            spec["type"] = "bool"
        elif isinstance(default, int):
            spec["type"] = "int"
            spec["min"] = -999999
            spec["max"] = 999999
        elif isinstance(default, float):
            spec["type"] = "float"
            spec["min"] = -999999.0
            spec["max"] = 999999.0
        elif isinstance(default, str):
            spec["type"] = "text"
        else:
            # Fallback
            spec["type"] = "text"
            spec["default"] = repr(default)
    else:
        # No default
        spec["type"] = "text"
        spec["default"] = ""
        spec["nullable"] = True

    return spec
