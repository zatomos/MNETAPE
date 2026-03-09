"""Code generation and parsing for the EEG pipeline.

Bidirectional translation between ActionConfig objects and Python source code.

New script format (two-section):
  - Imports + load line at the top
  - # --- Functions --- section with one def per unique action body
  - # --- Pipeline --- section with # [N] Title comments + call-site lines
  - Custom actions use # --inline-- / # --end-- blocks instead of a call site
"""

import ast
import logging
import re
from pathlib import Path

from mnetape.actions.registry import get_action_by_id, get_action_title
from mnetape.core.models import CUSTOM_ACTION_ID, ActionConfig, ActionStatus

logger = logging.getLogger(__name__)

# Base imports always present in the generated script header
BASE_IMPORTS = [
    "import mne",
    "import numpy as np",
    "from mnetape.core.data_io import load_raw_data",
]


# -------- Function name deduplication --------

def normalize_body(body_source: str) -> str:
    """Return ast dump of a body string for structural comparison."""
    try:
        return ast.dump(ast.parse(body_source), include_attributes=False)
    except SyntaxError:
        return body_source


def bodies_match(actual: str, canonical: str) -> bool:
    """Return True when two body strings have the same AST structure."""
    return normalize_body(actual) == normalize_body(canonical)


def get_canonical_body(action_def) -> str:
    """Return the action body source (advanced params are now encoded in the function signature)."""
    return action_def.body_source


def assign_func_names(actions: list[ActionConfig]) -> list[str]:
    """Assign unique function names to actions, deduplicating shared bodies.

    Actions with identical bodies share one function definition.
    A modified body (custom-edited standard action) gets a numbered suffix.
    Custom action (action_id=="custom") always gets "custom" as placeholder.
    """
    func_names: list[str] = []
    # Maps action_id to a list of (body_key, func_name) tuples for each unique body seen so far
    seen: dict[str, list[tuple[str, str]]] = {}

    for action in actions:
        if action.action_id == CUSTOM_ACTION_ID:
            func_names.append(CUSTOM_ACTION_ID)
            continue

        action_def = get_action_by_id(action.action_id)
        if not action_def:
            func_names.append(action.action_id)
            continue

        if action.is_custom and action.custom_code:
            body = action.custom_code
        else:
            body = get_canonical_body(action_def)

        body_key = normalize_body(body)

        if action.action_id not in seen:
            seen[action.action_id] = []

        for existing_key, existing_name in seen[action.action_id]:
            if body_key == existing_key:
                func_names.append(existing_name)
                break
        else:
            n = len(seen[action.action_id]) + 1
            func_name = action.action_id if n == 1 else f"{action.action_id}_{n}"
            seen[action.action_id].append((body_key, func_name))
            func_names.append(func_name)

    return func_names


# -------- Script generation --------

def generate_action_code(action: ActionConfig) -> str:
    """Generate a function-definition preview for a single action.

    Used by the action editor's code preview pane. Returns the full function definition for standard actions,
    or the custom code verbatim.
    """

    if action.custom_code:
        return action.custom_code

    action_def = get_action_by_id(action.action_id)
    if not action_def:
        return ""

    return action_def.build_function_def(action.action_id)


def generate_full_script(filepath: Path | None, actions: list[ActionConfig]) -> str:
    """Generate a complete Python pipeline script from an action list.

    Produces the new two-section format:
      - imports + load line
      - # --- Functions --- section with one def per unique body
      - # --- Pipeline --- section with # [N] Title + call site per action

    Args:
        filepath: Path to the loaded EEG file, injected into the load line.
        actions: Ordered list of pipeline actions to serialize.

    Returns:
        The complete Python script as a string.
    """
    logger.debug("Generating full script for %d actions", len(actions))

    func_names = assign_func_names(actions)

    # Collect unique function defs, preserving order of first occurrence
    emitted_funcs: dict[str, str] = {}  # func_name -> func_def_str

    for action, func_name in zip(actions, func_names):
        if action.action_id == CUSTOM_ACTION_ID or func_name == CUSTOM_ACTION_ID:
            continue
        if func_name in emitted_funcs:
            continue

        action_def = get_action_by_id(action.action_id)
        if not action_def:
            continue

        if action.is_custom and action.custom_code:
            # Custom-edited body: keep canonical signature, replace only the body
            emitted_funcs[func_name] = action_def.build_function_def_with_body(func_name, action.custom_code)
        else:
            emitted_funcs[func_name] = action_def.build_function_def(func_name)

    # Collect imports: base set + any extra imports declared by the actions in this pipeline
    all_imports: list[str] = list(BASE_IMPORTS)
    seen_imports: set[str] = set(BASE_IMPORTS)
    for action in actions:
        action_def = get_action_by_id(action.action_id)
        if action_def:
            for imp in action_def.extra_imports:
                if imp not in seen_imports:
                    all_imports.append(imp)
                    seen_imports.add(imp)
    merged_imports = "\n".join(all_imports)

    load_line = (
        f'raw = load_raw_data("{filepath}", preload=True)'
        if filepath
        else "# raw = load_raw_data('your_file.fif', preload=True)"
    )

    # Build pipeline section
    pipeline_lines: list[str] = []
    for i, (action, func_name) in enumerate(zip(actions, func_names), 1):
        title = get_action_title(action)
        pipeline_lines.append(f"# [{i}] {title}")

        if action.action_id == CUSTOM_ACTION_ID:
            pipeline_lines.append("# --inline--")
            pipeline_lines.append(action.custom_code or "")
            pipeline_lines.append("# --end--")
        else:
            action_def = get_action_by_id(action.action_id)
            if action_def:
                params = {**action_def.default_params(), **action.params}
                pipeline_lines.append(action_def.build_call_site(func_name, params, action.advanced_params or None))
            else:
                pipeline_lines.append(f"# (unknown action: {action.action_id})")

        pipeline_lines.append("")

    # Assemble script
    lines: list[str] = [
        "# EEG Preprocessing Pipeline",
        "# Auto-generated - edit here or in the GUI.",
        "",
        merged_imports,
        "",
        load_line,
    ]

    if emitted_funcs:
        lines += ["", "# --- Functions ---", ""]
        for func_def in emitted_funcs.values():
            lines.append(func_def)
            lines.append("")

    lines += ["", "# --- Pipeline ---", ""]
    lines.extend(pipeline_lines)
    lines += ["# Save result", "# raw.save('preprocessed.fif', overwrite=True)"]

    return "\n".join(lines)


# -------- Script parsing --------

def extract_func_defs(script: str) -> dict[str, str]:
    """Extract function definitions from the # --- Functions --- section.

    Returns:
        Dict mapping function name to its body source string.
    """
    result: dict[str, str] = {}

    # Find Functions section
    func_match = re.search(r"^#\s*---\s*Functions\s*---", script, re.MULTILINE)
    pipeline_match = re.search(r"^#\s*---\s*Pipeline\s*---", script, re.MULTILINE)
    if not func_match:
        return result

    func_start = func_match.end()
    func_end = pipeline_match.start() if pipeline_match else len(script)
    func_section = script[func_start:func_end]

    try:
        tree = ast.parse(func_section)
    except SyntaxError:
        return result

    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            body_stmts = "\n".join(ast.unparse(stmt) for stmt in node.body)
            result[node.name] = body_stmts

    return result


def parse_script_to_actions(script: str) -> list[ActionConfig]:
    """Parse a pipeline script back into a list of ActionConfig objects.
    Falls back gracefully to treating unknown blocks as custom actions.

    Args:
        script: Full Python pipeline script to parse.

    Returns:
        Ordered list of ActionConfig objects.
    """
    # Detect format
    if "# --- Pipeline ---" not in script:
        logger.warning("Script does not contain '# --- Pipeline ---' section; returning empty list")
        return []

    func_defs = extract_func_defs(script)

    # Find pipeline section
    pipeline_match = re.search(r"^#\s*---\s*Pipeline\s*---", script, re.MULTILINE)
    if not pipeline_match:
        return []

    pipeline_text = script[pipeline_match.end():]
    lines = pipeline_text.split("\n")

    header_re = re.compile(r"^#\s*\[(\d+)]\s*(.*?)\s*$")
    inline_start_re = re.compile(r"^#\s*--inline--\s*$")
    inline_end_re = re.compile(r"^#\s*--end--\s*$")

    actions: list[ActionConfig] = []
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        hm = header_re.match(line)
        if not hm:
            i += 1
            continue

        title = hm.group(2).strip()
        i += 1

        # Check for inline block (custom action)
        if i < len(lines) and inline_start_re.match(lines[i].strip()):
            i += 1  # skip # --inline--
            inline_lines: list[str] = []
            while i < len(lines) and not inline_end_re.match(lines[i].strip()):
                inline_lines.append(lines[i])
                i += 1
            if i < len(lines):
                i += 1  # skip # --end--
            custom_code = "\n".join(inline_lines).strip()

            action = ActionConfig(
                CUSTOM_ACTION_ID,
                {},
                ActionStatus.PENDING,
                custom_code=custom_code,
                is_custom=True,
                title_override=title,
            )
            actions.append(action)
            continue

        # Find the call-site line
        call_site_line = ""
        while i < len(lines):
            stripped = lines[i].strip()
            if stripped and not stripped.startswith("#"):
                call_site_line = stripped
                i += 1
                break
            i += 1

        if not call_site_line:
            # Empty action slot
            action_def = get_action_by_title(title)
            if action_def:
                action = ActionConfig(action_def.action_id, action_def.default_params())
            else:
                action = ActionConfig(CUSTOM_ACTION_ID, {}, is_custom=True, title_override=title)
            actions.append(action)
            continue

        # Parse the call site
        action_def = get_action_by_title(title)
        if not action_def:
            # Unknown title → custom action with the call site as code
            action = ActionConfig(
                CUSTOM_ACTION_ID,
                {},
                ActionStatus.PENDING,
                custom_code=call_site_line,
                is_custom=True,
                title_override=title,
            )
            actions.append(action)
            continue

        # Parse call kwargs from AST
        params, advanced_params, func_name = parse_call_site(call_site_line, action_def)
        action = ActionConfig(action_def.action_id, params, title_override=title)
        if advanced_params:
            action.advanced_params = advanced_params

        # Check if the function body in the script matches the canonical body
        if func_name and func_name in func_defs:
            actual_body = func_defs[func_name]
            canonical_body = get_canonical_body(action_def)
            if not bodies_match(actual_body, canonical_body):
                action.custom_code = actual_body
                action.is_custom = True

        actions.append(action)

    return actions


def get_action_by_title(title: str):
    """Look up an action definition by display title."""
    from mnetape.actions.registry import get_action_by_title
    return get_action_by_title(title)


def parse_call_site(
    call_site: str,
    action_def,
) -> tuple[dict, dict, str]:
    """Parse a call-site line into (params, advanced_params, func_name).

    Splits kwargs into primary params (in action_def.param_names), named kwargs groups, and flat extra kwargs
    for the **kwargs group.

    Returns:
        (params dict, advanced_params dict, func_name string)
    """
    call_node = None

    # Try expression mode first (bare call), then statement mode (assignment)
    try:
        expr_tree = ast.parse(call_site, mode="eval")
        if isinstance(expr_tree.body, ast.Call):
            call_node = expr_tree.body
    except SyntaxError:
        pass

    if call_node is None:
        try:
            stmt_tree = ast.parse(call_site)
            for node in ast.walk(stmt_tree):
                if isinstance(node, ast.Call):
                    call_node = node
                    break
        except SyntaxError:
            pass

    if call_node is None:
        return action_def.default_params(), {}, ""

    # Extract function name
    func_name = ""
    if isinstance(call_node.func, ast.Name):
        func_name = call_node.func.id
    elif isinstance(call_node.func, ast.Attribute):
        func_name = call_node.func.attr

    # Parse kwargs
    all_kwargs: dict = {}
    for kw in call_node.keywords:
        if kw.arg is None:
            continue
        try:
            all_kwargs[kw.arg] = ast.literal_eval(kw.value)
        except (ValueError, TypeError):
            pass

    # Split into primary vs advanced
    primary_names = set(action_def.param_names)
    kwargs_group_names = set(action_def.kwargs_groups) - {"kwargs"}
    params = action_def.default_params()
    advanced_params: dict[str, dict] = {}

    for name, value in all_kwargs.items():
        if name in primary_names:
            params[name] = value
        elif name in kwargs_group_names:
            # Named group dict value
            if isinstance(value, dict):
                advanced_params[name] = value
        elif "kwargs" in action_def.kwargs_groups:
            # Extra kwarg for **kwargs group
            advanced_params.setdefault("kwargs", {})[name] = value
        # else: unknown kwarg, ignore

    return params, advanced_params, func_name


# -------- Helpers for execution --------

def build_func_defs_for_execution(actions: list[ActionConfig]) -> str:
    """Build a string containing all function definitions for the current pipeline.

    Used by the executor when running individual actions. All defs are made available in the exec scope so
    functions can reference each other if needed.
    """

    func_names = assign_func_names(actions)
    emitted: dict[str, str] = {}

    for action, func_name in zip(actions, func_names):
        if action.action_id == CUSTOM_ACTION_ID or func_name == CUSTOM_ACTION_ID:
            continue
        if func_name in emitted:
            continue

        action_def = get_action_by_id(action.action_id)
        if not action_def:
            continue

        if action.is_custom and action.custom_code:
            emitted[func_name] = action_def.build_function_def_with_body(func_name, action.custom_code)
        else:
            emitted[func_name] = action_def.build_function_def(func_name)

    return "\n\n".join(emitted.values())
