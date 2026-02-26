"""Registry for EEG UI pipeline actions.

Actions are discovered automatically at first access: every sub-package of mnetape.actions
whose action.py module exposes an ACTION attribute of type ActionDefinition is registered by its action_id.

The registry is lazily loaded and cached as a module-level singleton so the import cost is paid only once.
"""

from __future__ import annotations

import importlib
import logging
import pkgutil
from pathlib import Path

from mnetape.actions.base import ActionDefinition
from mnetape.core.models import ActionConfig

logger = logging.getLogger(__name__)

ACTION_REGISTRY: dict[str, ActionDefinition] | None = None


def load_actions() -> dict[str, ActionDefinition]:
    """Scan the actions package and return a mapping of action_id -> ActionDefinition.

    Imports every action_id/action.py submodule and collects the ACTION constant
    when it is an ActionDefinition instance.
    Import errors are logged and skipped so a single broken action does not prevent others from loading.

    Returns:
        Dict mapping each registered action_id to its ActionDefinition.
    """
    actions_dir = Path(__file__).parent
    registry: dict[str, ActionDefinition] = {}
    for module_info in pkgutil.iter_modules([str(actions_dir)]):
        if not module_info.ispkg:
            continue
        module_name = f"{__package__}.{module_info.name}.action"
        try:
            module = importlib.import_module(module_name)
        except Exception as e:
            logger.exception("Failed to import action module '%s': %s", module_name, e)
            continue
        action_def = getattr(module, "ACTION", None)
        if not isinstance(action_def, ActionDefinition):
            continue
        registry[action_def.action_id] = action_def
    return registry


def get_action_registry() -> dict[str, ActionDefinition]:
    """Return the singleton action registry, loading it on first call.

    Returns:
        Dict mapping action_id to ActionDefinition for all registered actions.
    """
    global ACTION_REGISTRY
    if ACTION_REGISTRY is None:
        ACTION_REGISTRY = load_actions()
    return ACTION_REGISTRY


def list_actions() -> list[ActionDefinition]:
    """Return all registered ActionDefinition objects.

    Returns:
        List of all loaded ActionDefinition instances.
    """
    return list(get_action_registry().values())


def get_action_by_id(action_id: str) -> ActionDefinition | None:
    """Look up an action by its unique identifier.

    Args:
        action_id: The action identifier string (e.g. "filter").

    Returns:
        The ActionDefinition, or None if not registered.
    """
    return get_action_registry().get(action_id)


def get_action_by_title(title: str) -> ActionDefinition | None:
    """Look up an action by its display title.

    Performs an exact string match. Returns the first match found.

    Args:
        title: The action title string.

    Returns:
        The ActionDefinition, or None if no action has that title.
    """
    for action_def in get_action_registry().values():
        if action_def.title == title:
            return action_def
    return None


def get_action_title(action: ActionConfig) -> str:
    """Return the display title for an ActionConfig.

    Returns the title_override if set, then the registered definition title, then the action_id as a last resort.

    Args:
        action: The action configuration to get the title for.

    Returns:
        Human-readable display name string.
    """
    if action.title_override:
        return action.title_override
    action_def = get_action_by_id(action.action_id)
    if action_def:
        return action_def.title
    return action.action_id
