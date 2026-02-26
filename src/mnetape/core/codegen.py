"""Code generation and parsing for the EEG pipeline.

This module handles the bidirectional translation between ActionConfig objects
and Python source code:
"""

import ast
import logging
import re
from pathlib import Path

from mnetape.actions.base import match_dotted_name
from mnetape.actions.registry import (
    get_action_by_id,
    get_action_by_title,
    get_action_title,
)
from mnetape.core.models import ActionConfig, ActionStatus

logger = logging.getLogger(__name__)

# Base imports that are always present in the generated script
_BASE_IMPORTS = [
    "import mne",
    "import numpy as np",
    "from mnetape.core.data_io import load_raw_data",
]


def generate_action_code(action: ActionConfig) -> str:
    """Generate Python source code for a single action.

    Returns the action's custom_code verbatim if one exists, otherwise builds code from the action definition
    using the stored params and advanced_params.

    Args:
        action: The action configuration to generate code for.

    Returns:
        Python source string, or an empty string when no code can be produced.
    """

    if action.custom_code:
        return action.custom_code
    if action.is_custom:
        return ""

    action_def = get_action_by_id(action.action_id)
    if not action_def:
        return ""

    params = action_def.default_params()
    params.update(action.params)
    advanced = action.advanced_params or None
    return action_def.build_code(params, advanced_params=advanced)


def try_literal(node: ast.expr):
    """Attempt to evaluate an AST node as a Python literal.

    Args:
        node: The AST expression node to evaluate.

    Returns:
        A tuple (True, value) when the node is a safe literal (numbers, strings, lists, dicts, etc.),
        or (False, None) when it cannot be evaluated.
    """
    try:
        return True, ast.literal_eval(node)
    except (ValueError, TypeError):
        return False, None



def extract_params_from_schema(schema, code: str, defaults: dict) -> dict:
    """Walk code's AST and extract param values declared by a TemplateSchema.

    Recovers function-group param values from keyword arguments on matched
    function calls.

    Example:
        ``raw.filter(h_freq=40.0)`` would yield ``{"h_freq": 40.0}``.

    Args:
        schema: The TemplateSchema describing which function calls to look for.
        code: Python source code to parse and walk.
        defaults: Default param values used as the base; any parsed values override them.

    Returns:
        Dict of parameter names to their extracted (or default) values.
    """

    try:
        tree = ast.parse(code)
    except SyntaxError:
        return dict(defaults)

    result = dict(defaults)

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            for g in schema.function_groups:
                if match_dotted_name(node.func, g.dotted_name):
                    for kw in node.keywords:
                        if kw.arg and kw.arg in g.params:
                            ok, val = try_literal(kw.value)
                            if ok:
                                result[kw.arg] = val
                    break

    return result


def extract_advanced_from_schema(schema, code: str) -> dict:
    """Return non-schema kwargs found in function calls described by a TemplateSchema.

    Walks the AST and collects keyword arguments on matched calls that are not
    declared in the schema's function group params. Only literal values are kept.

    Args:
        schema: The TemplateSchema describing which function calls to inspect.
        code: Python source code to parse and walk.

    Returns:
        Dict keyed by dotted function name, each value a dict of advanced
        kwarg names to their literal values.
    """

    try:
        tree = ast.parse(code)
    except SyntaxError:
        return {}

    result: dict[str, dict] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            # Check if the call matches any function group in the schema
            for g in schema.function_groups:
                if match_dotted_name(node.func, g.dotted_name):
                    adv: dict = {}
                    # Collect kwargs that are not in the schema params
                    for kw in node.keywords:
                        # Only keep kwargs whose values are simple literals
                        if kw.arg and kw.arg not in g.params:
                            ok, val = try_literal(kw.value)
                            if ok:
                                adv[kw.arg] = val
                    if adv:
                        result[g.dotted_name] = adv
                    break

    return result


def parse_params_from_code(action_id: str, code: str) -> dict:
    """Extract primary parameters from generated code for a registered action.

    Walks the AST to recover function-group param values from function call kwargs.
    For multistep actions, each step block is walked with its own step schema.
    Falls back to defaults for any param that cannot be parsed.

    Args:
        action_id: Identifier of the registered action whose schema to use.
        code: Python source code to parse.

    Returns:
        Dict mapping parameter names to their extracted values.
    """

    action_def = get_action_by_id(action_id)
    if not action_def:
        return {}
    defaults = action_def.default_params()

    # Single-step action: walk the whole code with the main schema
    if action_def.template_schema:
        return extract_params_from_schema(action_def.template_schema, code, defaults)

    # Multistep actions: each step has its own schema
    if action_def.steps:
        result = dict(defaults)
        for block in extract_step_blocks(code):
            step = next((s for s in action_def.steps if s.step_id == block["id"]), None)
            if step and step.template_schema:
                result.update(extract_params_from_schema(step.template_schema, block["code"], {}))
        return result

    return defaults


def parse_advanced_params_from_code(action_id: str, code: str) -> dict:
    """Extract advanced (non-schema) kwargs from function calls in generated code.

    Args:
        action_id: Identifier of the registered action whose schema to use.
        code: Python source code to parse.

    Returns:
        Dict keyed by dotted function name mapping to a dict of non-primary kwarg names and values.
    """

    action_def = get_action_by_id(action_id)
    if not action_def:
        return {}

    if action_def.template_schema:
        return extract_advanced_from_schema(action_def.template_schema, code)

    if action_def.steps:
        result: dict[str, dict] = {}
        for block in extract_step_blocks(code):
            step = next((s for s in action_def.steps if s.step_id == block["id"]), None)
            if step and step.template_schema:
                for func, adv in extract_advanced_from_schema(step.template_schema, block["code"]).items():
                    result.setdefault(func, {}).update(adv)
        return result

    return {}


# -------- Code generation utilities --------

def extract_imports(code: str) -> tuple[list[ast.stmt], str]:
    """Extract top-level import statements from code.

    Args:
        code: Python source code to parse.

    Returns:
        A tuple of (import_nodes, stripped_code) where import_nodes is the list of extracted Import
        andImportFrom AST nodes, and stripped_code is the code with those import lines removed.
    """

    try:
        tree = ast.parse(code)
    except SyntaxError:
        return [], code

    import_nodes: list[ast.stmt] = []
    remove_ranges: list[tuple[int, int]] = []

    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            import_nodes.append(node)
            start = max(0, node.lineno - 1)
            end = max(start, (node.end_lineno or node.lineno) - 1)
            remove_ranges.append((start, end))

    if not import_nodes:
        return [], code

    # Remove only top-level import source lines while preserving comments/format.
    lines = code.split("\n")
    remove_idx: set[int] = set()
    for start, end in remove_ranges:
        for idx in range(start, end + 1):
            remove_idx.add(idx)
    kept = [line for i, line in enumerate(lines) if i not in remove_idx]
    stripped = "\n".join(kept)
    return import_nodes, stripped


def merge_imports(import_nodes: list[ast.stmt]) -> str:
    """Merge and deduplicate import statements into a sorted block.

    Groups "from X import a" and "from X import b" into "from X import a, b".
    Deduplicates plain "import X" statements.

    Args:
        import_nodes: List of Import and ImportFrom AST nodes to merge.

    Returns:
        A string containing the merged, sorted import block.
    """

    # Plain imports: set of module names
    plain_imports: set[str] = set()
    # from imports: module -> set of (name, asname)
    from_imports: dict[str, set[tuple[str, str | None]]] = {}

    for node in import_nodes:
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    plain_imports.add(f"import {alias.name} as {alias.asname}")
                else:
                    plain_imports.add(f"import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module not in from_imports:
                from_imports[module] = set()
            for alias in node.names:
                from_imports[module].add((alias.name, alias.asname))

    lines: list[str] = []

    # Plain imports sorted
    for imp in sorted(plain_imports):
        lines.append(imp)

    # From imports sorted by module, names sorted
    for module in sorted(from_imports.keys()):
        names = sorted(from_imports[module], key=lambda t: t[0])
        name_strs = []
        for name, asname in names:
            if asname:
                name_strs.append(f"{name} as {asname}")
            else:
                name_strs.append(name)
        lines.append(f"from {module} import {', '.join(name_strs)}")

    return "\n".join(lines)


def generate_full_script(filepath: Path | None, actions: list[ActionConfig]) -> str:
    """Generate a complete Python pipeline script from an action list.

    Extracts imports from each action's code, merges and deduplicates them with the base imports,
    and places the merged import block at the top of the script.
    Action blocks in the output contain only import-free code.
    The script structure is driven by a template file with marker comments.

    Args:
        filepath: Path to the loaded EEG file, injected as the load_raw_data call.
            When None, a commented-out placeholder is written instead.
        actions: Ordered list of pipeline actions to serialize.

    Returns:
        The complete Python script as a string.

    Raises:
        RuntimeError: When the pipeline template file cannot be read.
    """

    logger.debug("Generating full script for %d actions", len(actions))
    template_path = Path(__file__).with_name("templates") / "default_script"
    try:
        template_lines = template_path.read_text().split("\n")
    except OSError as exc:
        raise RuntimeError(f"Could not read pipeline template: {template_path}") from exc

    all_import_nodes: list[ast.stmt] = []
    action_lines: list[str] = []

    for i, action in enumerate(actions, 1):
        title = get_action_title(action)
        code = generate_action_code(action)
        action_def = get_action_by_id(action.action_id)

        # Keep multi-step action code intact so step markers and in-step imports
        # round-trip correctly when reloading scripts.
        if action_def and action_def.has_steps():
            stripped = code
        else:
            imports, stripped = extract_imports(code)
            all_import_nodes.extend(imports)

        action_lines.append(f"# In[{i}] {title}")
        action_lines.append(stripped)
        action_lines.append(f"# End[{i}]")
        action_lines.append("")

    # Merge all action imports with base imports
    base_nodes: list[ast.stmt] = []
    for imp_str in _BASE_IMPORTS:
        base_nodes.extend(ast.parse(imp_str).body)
    all_nodes = base_nodes + all_import_nodes
    merged_imports = merge_imports(all_nodes)

    load_line = (
        f'raw = load_raw_data("{filepath}", preload=True)'
        if filepath
        else "# raw = load_raw_data('your_file.fif', preload=True)"
    )

    output: list[str] = []
    i = 0
    while i < len(template_lines):
        line = template_lines[i]
        stripped = line.strip()
        if stripped == "# IMPORTS":
            output.append(merged_imports)
            i += 1
            continue
        if stripped == "# LOAD_DATA":
            output.append(load_line)
            i += 1
            continue
        if stripped == "# ACTIONS_START":
            output.append(line)
            output.extend(action_lines)
            i += 1
            while i < len(template_lines) and template_lines[i].strip() != "# ACTIONS_END":
                i += 1
            if i < len(template_lines):
                output.append(template_lines[i])
                i += 1
            continue
        output.append(line)
        i += 1

    return "\n".join(output)


def structure_signature(code: str) -> str:
    """Return a normalized structural signature of code.

    Used to compare code blocks for equivalence while ignoring formatting and literal values.
    Two blocks that differ only in their literal arguments will produce the same signature.

    Args:
        code: Python source code to normalize.

    Returns:
        A string derived from the AST dump with literals replaced by placeholders.
        Falls back to whitespace-stripped code on SyntaxError.
    """

    try:
        tree = ast.parse(code)
    except SyntaxError:
        return re.sub(r"\s+", "", code)

    transformer = CallStructureNormalizer()
    normalized = transformer.visit(tree)
    ast.fix_missing_locations(normalized)
    return ast.dump(normalized, include_attributes=False)



class CallStructureNormalizer(ast.NodeTransformer):
    """AST transformer that normalizes function call structures for structural comparison.

    Argument values are replaced with a placeholder while keyword argument names are preserved.
    Two calls that differ only in their literal values will produce identical output.
    """

    def visit_Call(self, node: ast.Call) -> ast.AST:
        self.generic_visit(node)
        node.args = [ast.Name(id="_", ctx=ast.Load()) for _ in node.args]
        new_keywords = []
        for kw in node.keywords:
            if kw.arg is None:
                new_keywords.append(kw)
            else:
                new_keywords.append(ast.keyword(arg=kw.arg, value=ast.Name(id="_", ctx=ast.Load())))
        node.keywords = new_keywords
        return node


def extract_action_blocks(script: str) -> list[dict]:
    """Extract action blocks delimited by "# In[N]" and "# End[N]" markers.

    Args:
        script: Full pipeline script to parse.

    Returns:
        List of dicts each with keys: number (int), name (str), code (str).
    """

    blocks = []
    lines = script.split("\n")
    header_re = re.compile(r'^#\s*In\[(\d+)]\s*(.*?)\s*$')
    footer_re = re.compile(r'^#\s*End\[(\d+)]\s*$')

    i = 0
    while i < len(lines):
        # Look for In[N] header
        match = header_re.match(lines[i].strip())
        if match:
            block = {
                "number": int(match.group(1)),
                "name": match.group(2).strip(),
                "code_lines": [],
            }
            i += 1
            while i < len(lines):
                # Stop if we hit another header, the corresponding footer, or a save marker
                if header_re.match(lines[i].strip()):
                    break
                footer_match = footer_re.match(lines[i].strip())
                if footer_match and int(footer_match.group(1)) == block["number"]:
                        i += 1
                        break
                if lines[i].strip().startswith("# Save result"):
                    break
                block["code_lines"].append(lines[i])
                i += 1

            while block["code_lines"] and not block["code_lines"][-1].strip():
                block["code_lines"].pop()
            block["code"] = "\n".join(block["code_lines"])
            blocks.append(block)
        else:
            i += 1

    return blocks


def extract_step_blocks(code: str) -> list[dict]:
    """Extract step blocks delimited by "# Step[id]" and "# EndStep[id]" markers.

    Args:
        code: Python source code to parse.

    Returns:
        List of dicts each with keys: id (str), title (str), code (str).
    """

    blocks = []
    lines = code.split("\n")
    header_re = re.compile(r'^#\s*Step\[(.+?)]\s*(.*?)\s*$')
    footer_re = re.compile(r'^#\s*EndStep\[(.+?)]\s*$')

    i = 0
    while i < len(lines):
        # Look for Step[id] header
        match = header_re.match(lines[i].strip())
        if match:
            block = {
                "id": match.group(1).strip(),
                "title": match.group(2).strip(),
                "code_lines": [],
            }
            i += 1
            while i < len(lines):
                # Stop if we hit another header or the corresponding footer
                if header_re.match(lines[i].strip()):
                    break
                footer_match = footer_re.match(lines[i].strip())
                if footer_match and footer_match.group(1).strip() == block["id"]:
                        i += 1
                        break
                block["code_lines"].append(lines[i])
                i += 1

            while block["code_lines"] and not block["code_lines"][-1].strip():
                block["code_lines"].pop()
            block["code"] = "\n".join(block["code_lines"])
            blocks.append(block)
        else:
            i += 1

    return blocks


def parse_script_to_actions(script: str) -> list[ActionConfig]:
    """Parse a pipeline script back into a list of ActionConfig objects.

    Extracts action blocks from the script using "# In[N]" delimiters, then attempts to match each block's title
    to a registered action. Blocks with unrecognized titles become custom actions.
    Params and advanced params are reverse-parsed; blocks whose structure deviates from generated code are
    treated as custom.

    Args:
        script: Full Python pipeline script to parse.

    Returns:
        Ordered list of ActionConfig objects representing the parsed pipeline.
    """

    blocks = extract_action_blocks(script)
    logger.debug("Parsing script into actions: found %d action blocks", len(blocks))

    if not blocks:
        return []

    actions = []
    for block in blocks:
        action_def = get_action_by_title(block["name"])
        if not action_def:
            fallback = get_action_by_id("custom")
            params = fallback.default_params() if fallback else {}
            action = ActionConfig(
                "custom",
                params,
                ActionStatus.PENDING,
                custom_code=block["code"],
                is_custom=True,
                title_override=block["name"],
            )
            actions.append(action)
            continue

        # Setup action params
        params = parse_params_from_code(action_def.action_id, block["code"])
        advanced = parse_advanced_params_from_code(action_def.action_id, block["code"])
        action = ActionConfig(action_def.action_id, params, title_override=block["name"])
        if advanced:
            action.advanced_params = advanced

        # Check if code structurally matches what we'd expect from the params. If not, treat as custom.
        expected_code = action_def.build_code(params, advanced_params=advanced or None)
        if structure_signature(block["code"]) != structure_signature(expected_code):
            action.custom_code = block["code"]
            action.is_custom = True

        actions.append(action)

    return actions
