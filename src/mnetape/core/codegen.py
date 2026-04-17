"""Code generation and parsing for the EEG pipeline.

Bidirectional translation between ActionConfig objects and Python source code.

Script format:
  - Imports at the top
  - # --- Functions --- section with one def per unique action body
  - # --- Pipeline --- section with # [N] Title comments + call-site lines
  - Custom actions emit their code directly after the header
"""

import ast
import dataclasses
import logging
import re

from mnetape.actions.registry import get_action_by_id, get_action_title
from mnetape.core.models import CUSTOM_ACTION_ID, ActionConfig, ActionStatus, DataType

logger = logging.getLogger(__name__)

# Base imports always present in the generated script header
BASE_IMPORTS = [
    "import mne",
    "import numpy as np",
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

def get_canonical_body(action_def, context_type=None, params=None) -> str:
    """Return the body source for an action, resolving type and param variants when possible."""
    return action_def.resolve_body(context_type=context_type, params=params)


def all_canonical_bodies(action_def) -> list[str]:
    """Collect every possible canonical body across type and param variants.

    Used by the parser to decide whether a function body was user-modified.
    """
    bodies: list[str] = []

    def _collect(adef) -> None:
        bodies.append(adef.body_source)
        bodies.extend(adef.param_variants.values())
        for variant in adef.variants.values():
            _collect(variant)

    _collect(action_def)
    return bodies

def get_types_for_actions(actions: list[ActionConfig]) -> list[DataType]:
    """Return the input DataType for each action in the pipeline.

    ANY actions are pass-through and do not change the current type.
    """
    types: list[DataType] = []
    current_type = DataType.RAW
    for action in actions:
        types.append(current_type)
        action_def = get_action_by_id(action.action_id)
        if action_def and action_def.output_type != DataType.ANY:
            current_type = action_def.output_type
    return types

def assign_func_names(actions: list[ActionConfig], types: list[DataType] | None = None) -> list[str]:
    """Assign unique function names to actions, deduplicating shared bodies.

    Actions with identical bodies share one function definition.
    A modified body (custom-edited standard action) gets a numbered suffix.
    Custom action (action_id=="custom") always gets "custom" as placeholder.
    """
    func_names: list[str] = []
    # Maps action_id to a list of (body_key, func_name) tuples for each unique body seen so far
    seen: dict[str, list[tuple[tuple, str]]] = {}

    for idx, action in enumerate(actions):
        if action.action_id == CUSTOM_ACTION_ID:
            func_names.append(CUSTOM_ACTION_ID)
            continue

        action_def = get_action_by_id(action.action_id)
        if not action_def:
            func_names.append(action.action_id)
            continue

        context_type = types[idx] if types else DataType.RAW
        is_any = action_def and action_def.input_type == DataType.ANY and not (action.is_custom and action.custom_code)

        if action.is_custom and action.custom_code:
            body = action.custom_code
        else:
            body = get_canonical_body(action_def, context_type=context_type, params=action.params)

        body_key = (normalize_body(body), context_type if is_any else None)

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

def generate_action_code(action: ActionConfig, context_type: DataType | None = None) -> str:
    """Generate a function-definition preview for a single action.

    Used by the action editor's code preview pane. Returns the full function definition for standard actions,
    or the custom code verbatim.

    Args:
        action: the config of the action.
        context_type: The DataType of data flowing into this action. Required for ANY actions
            to generate the correct variant body.
    """

    if action.custom_code:
        return action.custom_code

    action_def = get_action_by_id(action.action_id)
    if not action_def:
        return ""

    return action_def.build_function_def(action.action_id, context_type, params=action.params)

def collect_func_defs(actions: list[ActionConfig], func_names: list[str], types: list[DataType]) -> dict[str, str]:
    """Collect unique function definitions for a list of actions, preserving first occurrence order."""
    emitted: dict[str, str] = {}
    for action, func_name, context_type in zip(actions, func_names, types):
        if action.action_id == CUSTOM_ACTION_ID or func_name == CUSTOM_ACTION_ID:
            continue
        if func_name in emitted:
            continue
        action_def = get_action_by_id(action.action_id)
        if not action_def:
            continue
        if action.is_custom and action.custom_code:
            emitted[func_name] = action_def.build_function_def_with_body(func_name, action.custom_code, context_type, params=action.params)
        else:
            emitted[func_name] = action_def.build_function_def(func_name, context_type, params=action.params)
    return emitted

def extract_custom_preamble(script: str, actions: list[ActionConfig]) -> list[str]:
    """Return user-added lines from the script preamble that are not auto-generated.

    Scans everything before the first structured section (``# --- Functions ---`` or
    ``# --- Pipeline ---``) and returns any line that ``generate_full_script`` would
    not emit on its own, preserving order.

    Args:
        script: Full pipeline script text (may contain manual edits).
        actions: Current action list, used to identify action-contributed imports.

    Returns:
        Ordered list of custom preamble lines (stripped, non-blank).
    """
    from mnetape.actions.registry import get_action_by_id as _get_action_by_id

    section_match = re.search(r"^#\s*---\s*(Functions|Pipeline)\s*---", script, re.MULTILINE)
    preamble = script[: section_match.start()].strip() if section_match else script.strip()

    standard: set[str] = {
        "# EEG Preprocessing Pipeline",
        "# Auto-generated by the MNETAPE software.",
        *BASE_IMPORTS,
    }
    for action in actions:
        action_def = _get_action_by_id(action.action_id)
        if action_def:
            standard.update(action_def.extra_imports)

    return [ln.strip() for ln in preamble.split("\n") if ln.strip() and ln.strip() not in standard]


def pipeline_canonical_code(actions: list[ActionConfig], extra_preamble: list[str] | None = None) -> str:
    """Return the generated script with participant-specific values cleared.
    """
    clean: list[ActionConfig] = []
    for a in actions:
        if a.action_id == "load_file" and a.params.get("file_path"):
            clean.append(dataclasses.replace(a, params={**a.params, "file_path": ""}))
        else:
            clean.append(a)
    return generate_full_script(clean, extra_preamble=extra_preamble)


def generate_full_script(actions: list[ActionConfig], extra_preamble: list[str] | None = None) -> str:
    """Generate a complete Python pipeline script from an action list.

    Produces the following format:
      - Imports
      - Functions section with one def per unique body
      - Pipeline section with call site for actions

    Args:
        actions: Ordered list of pipeline actions (including load_file and set_montage if present).
        extra_preamble: Optional list of user-added lines to inject after the auto-generated import block and before
        the Functions section.

    Returns:
        The complete Python script as a string.
    """
    logger.debug("Generating full script for %d actions", len(actions))

    types = get_types_for_actions(actions)
    func_names = assign_func_names(actions, types)
    emitted_funcs = collect_func_defs(actions, func_names, types)

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

    pipeline_lines: list[str] = []
    for i, (action, func_name, context_type) in enumerate(zip(actions, func_names, types), 1):
        title = get_action_title(action)
        pipeline_lines.append(f"# [{i}] {title}")

        if action.action_id == CUSTOM_ACTION_ID:
            pipeline_lines.append(action.custom_code or "")
        else:
            action_def = get_action_by_id(action.action_id)
            if action_def:
                params = {**action_def.default_params(), **action.params}
                pipeline_lines.append(action_def.build_call_site(func_name, params, action.advanced_params or None, context_type))
            else:
                pipeline_lines.append(f"# (unknown action: {action.action_id})")

        pipeline_lines.append("")

    lines: list[str] = [
        "# EEG Preprocessing Pipeline",
        "# Auto-generated by the MNETAPE software.",
        "",
        merged_imports,
        "",
    ]

    if extra_preamble:
        for ln in extra_preamble:
            if ln not in seen_imports:
                lines.append(ln)
        lines.append("")

    if emitted_funcs:
        lines += ["", "# --- Functions ---", ""]
        for func_def in emitted_funcs.values():
            lines.append(func_def)
            lines.append("")

    lines += ["", "# --- Pipeline ---", ""]
    lines.extend(pipeline_lines)

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
    # For function-name fallback lookup: extract name from "x = func_name(" call sites
    func_name_re = re.compile(r"=\s*(\w+)\s*\(")
    dedup_suffix_re = re.compile(r"_\d+$")

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

        # Collect every line belonging to this action (up to the next header)
        body_lines: list[str] = []
        while i < len(lines) and not header_re.match(lines[i].strip()):
            body_lines.append(lines[i])
            i += 1

        # Non-comment, non-blank code lines in the body
        code_lines = [l.strip() for l in body_lines if l.strip() and not l.strip().startswith("#")]

        if not code_lines:
            # Empty body, fall back to title lookup
            action_def = get_action_by_title(title)
            if action_def:
                action = ActionConfig(action_def.action_id, action_def.default_params())
            else:
                action = ActionConfig(CUSTOM_ACTION_ID, {}, is_custom=True, title_override=title)
            actions.append(action)
            continue

        if len(code_lines) == 1:
            call_site_line = code_lines[0]

            # Resolve action: title lookup first, then function-name fallback
            action_def = get_action_by_title(title)
            if action_def is None:
                m = func_name_re.search(call_site_line)
                if m:
                    base_name = dedup_suffix_re.sub("", m.group(1))
                    action_def = get_action_by_id(base_name)

            if action_def is not None:
                params, advanced_params, func_name = parse_call_site(call_site_line, action_def)
                action = ActionConfig(action_def.action_id, params, title_override=title)
                if advanced_params:
                    action.advanced_params = advanced_params

                if func_name and func_name in func_defs:
                    actual_body = func_defs[func_name]
                    if not any(bodies_match(actual_body, cb) for cb in all_canonical_bodies(action_def)):
                        action.custom_code = actual_body
                        action.is_custom = True

                actions.append(action)
                continue

        # Multi-line body or unresolvable single line: custom inline action
        custom_code = "\n".join(body_lines).strip()
        actions.append(ActionConfig(
            CUSTOM_ACTION_ID,
            {},
            ActionStatus.PENDING,
            custom_code=custom_code,
            is_custom=True,
            title_override=title,
        ))

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
    types = get_types_for_actions(actions)
    func_names = assign_func_names(actions, types)
    return "\n\n".join(collect_func_defs(actions, func_names, types).values())
