"""Base types and factories for EEG pipeline actions.

This module contains the building blocks used to define preprocessing actions:

- Fragment / @fragment: extract a function body as a code template and substitute literal values via inline().
- @step / StepBuilder: mark a builder function as a named pipeline step.
- ParamMeta: widget/schema metadata attached to template_builder parameters.
- extract_schema_from_signature: derive a params_schema dict from Annotated type hints on a template_builder function.
- TemplateSchema / FunctionParamGroup: schema used for reverse-parsing params from generated code
  and for discovering advanced MNE kwargs.
- StepDefinition / ActionDefinition: frozen dataclasses describing one step or a complete action
  (title, code builder, schema, prerequisites, etc.).
- action_from_templates: single call factory that loads a templates.py module, discovers @step builders,
  and wires everything into an ActionDefinition.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
import importlib.util
import inspect
import logging
from pathlib import Path
import sys
import textwrap
from typing import Annotated, Any, Callable, get_args, get_origin, get_type_hints

logger = logging.getLogger(__name__)

ParamsSchema = dict[str, dict]
CodeBuilder = Callable[..., str]
InteractiveRunner = Callable[..., object]


# -------- Value to AST conversion --------

def value_to_ast(value: object) -> ast.expr:
    """Convert a Python value to an AST expression node.

    Supports None, bool, int, float, str, list, and dict. Falls back to a string representation for other types.

    Args:
        value: Python value to convert.

    Returns:
        An AST expression node representing the value.
    """

    if value is None:
        return ast.Constant(value=None)
    if isinstance(value, bool):
        return ast.Constant(value=value)
    if isinstance(value, (int, float, str)):
        return ast.Constant(value=value)
    if isinstance(value, list):
        return ast.List(elts=[value_to_ast(v) for v in value], ctx=ast.Load())
    if isinstance(value, dict):
        return ast.Dict(
            keys=[ast.Constant(value=k) for k in value.keys()],
            values=[value_to_ast(v) for v in value.values()],
        )
    return ast.Constant(value=str(value))


# -------- Fragment system --------

class NameSubstitutor(ast.NodeTransformer):
    """AST transformer that replaces Name nodes in Load context with literal AST expressions.

    Example:
        - Body: raw.filter(l_freq=l_freq, h_freq=h_freq)
        - Substitutions: {"l_freq": 0.5, "h_freq": 45.0}
        - Result: raw.filter(l_freq=0.5, h_freq=45.0)
    """

    def __init__(self, subs: dict[str, ast.expr]) -> None:
        self.subs = subs

    def visit_Name(self, node: ast.Name) -> ast.AST:
        if isinstance(node.ctx, ast.Load) and node.id in self.subs:
            return ast.copy_location(self.subs[node.id], node)
        return node


class ConstantBranchPruner(ast.NodeTransformer):
    """AST transformer that removes dead branches after constant folding.

    After NameSubstitutor replaces variables with constants, conditional branches with a known-true or known-false test
    can be statically removed.
    Example:
         "if True:" keeps only its body; "if False:" is replaced by its else branch (or eliminated entirely).
    """

    def visit_If(self, node: ast.If) -> ast.AST | list[ast.stmt]:
        self.generic_visit(node)
        if not isinstance(node.test, ast.Constant):
            return node
        return node.body if node.test.value else node.orelse


class Fragment:
    """A code fragment extracted from a function body, used for code generation.

    Fragment functions are decorated with @fragment and are never called directly.
    Their body is extracted as source and composed into the generated pipeline code by inline().

    Raw may be declared as a parameter but is never injected as a literal assignment. It is always available in
    the exec scope at pipeline runtime.
    """

    # Variables always available in exec scope and never get injected
    SCOPE_VARS: frozenset[str] = frozenset({"raw"})

    def __init__(self, fn: Callable) -> None:
        self._fn = fn
        self._param_names: list[str] = []
        self._body_source: str = ""
        self.extract()

    def extract(self) -> None:
        source = inspect.getsource(self._fn)
        source = textwrap.dedent(source)
        tree = ast.parse(source)
        func_def = next(
            n for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == self._fn.__name__
        )
        self._param_names = [a.arg for a in func_def.args.args]
        self._body_source = "\n".join(ast.unparse(stmt) for stmt in func_def.body)

    def inline(self, **kwargs: object) -> str:
        """Return the fragment body with param names substituted by their literal values.

        Scope variables are never substituted. They are always available in the exec context.
        """

        substitutions: dict[str, ast.expr] = {}
        for name in self._param_names:
            if name in self.SCOPE_VARS:
                continue
            if name in kwargs:
                substitutions[name] = value_to_ast(kwargs[name])

        if not substitutions:
            return self._body_source

        tree = ast.parse(self._body_source)
        tree = NameSubstitutor(substitutions).visit(tree)
        tree = ConstantBranchPruner().visit(tree)
        ast.fix_missing_locations(tree)
        return ast.unparse(tree)

    def __call__(self) -> object:
        raise TypeError(
            f"Fragment '{self._fn.__name__}' is a code template, not a callable. "
            "Use .inline(**kwargs) to generate source code."
        )


def fragment(fn: Callable) -> Fragment:
    """Decorator that turns a function into a class Fragment code template.

    The decorated function's body becomes the template source. It is never executed directly.
    Use frag.inline(**kwargs) to produce injectable code strings.
    """
    return Fragment(fn)


@dataclass
class StepBuilder:
    """Typed wrapper returned by the @step decorator.

    Holds step metadata and the builder callable.
    """

    step_id: str
    title: str
    interactive: bool
    fn: Callable[..., str]


def step(
    step_id: str,
    *,
    title: str = "",
    interactive: bool = False,
) -> Callable[[Callable[..., str]], StepBuilder]:
    """Mark a builder function as an action step.

    Args:
        step_id: Unique identifier embedded in generated code markers.
        title: Step label shown in the UI. Defaults to step_id title-cased.
        interactive: When True, the step requires user interaction and is
            dispatched to an interactive_runner rather than a background thread.
    """

    def decorator(fn: Callable[..., str]) -> StepBuilder:
        return StepBuilder(
            step_id=step_id,
            title=title or step_id.replace("_", " ").title(),
            interactive=interactive,
            fn=fn,
        )

    return decorator


# -------- Parameter metadata type --------

@dataclass
class ParamMeta:
    """Widget/schema metadata for parameters annotations.

    Use in template_builder to describe how a parameter should be displayed and validated in the action editor.
    All fields are optional except type (defaults to "text").
    """

    type: str = "text"
    label: str = ""
    description: str = ""
    default: Any = None
    min: float | None = None
    max: float | None = None
    decimals: int | None = None
    choices: list[str] | None = None
    nullable: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return a params_schema-compatible dict, omitting unset optional fields."""
        d: dict[str, Any] = {"type": self.type, "default": self.default}
        if self.label:
            d["label"] = self.label
        if self.description:
            d["description"] = self.description
        if self.min is not None:
            d["min"] = self.min
        if self.max is not None:
            d["max"] = self.max
        if self.decimals is not None:
            d["decimals"] = self.decimals
        if self.choices is not None:
            d["choices"] = self.choices
        if self.nullable is not None:
            d["nullable"] = self.nullable
        return d


# -------- Schema extraction from Annotated signatures --------

def infer_param_type(annotation: type | None) -> str:
    """Map a Python type annotation to an editor widget-type string.

    Used when no explicit ParamMeta is provided on an Annotated parameter.
    Unwraps Union/Optional to inspect the inner type.

    Args:
        annotation: A Python type annotation, or None.

    Returns:
        One of "float", "int", "bool", or "text".
    """
    if annotation is None:
        return "text"
    origin = get_origin(annotation)
    if origin is not None:
        # Filter out None and infer the inner type if it's a Union (e.g. Optional[T])
        inner = [a for a in get_args(annotation) if a is not type(None)]
        return infer_param_type(inner[0]) if inner else "text"
    if annotation is float:
        return "float"
    if annotation is int:
        return "int"
    if annotation is bool:
        return "bool"
    if annotation is str:
        return "text"
    return "text"


def extract_schema_from_signature(fn: Callable) -> dict[str, dict]:
    """Extract a params_schema dict from a template_builder function signature.

    Parameters annotated with Annotated[T, ParamMeta(...)] use the ParamMeta for widget metadata.
    Plain parameters get type and default inferred automatically.
    The raw parameter is always excluded. Mutable defaults (list, dict) are normalized to None to avoid
    mutable-default-argument issues.

    Args:
        fn: The template builder function to inspect.

    Returns:
        Dict mapping parameter names to widget spec dicts compatible with params_schema.
    """

    sig = inspect.signature(fn)
    try:
        module = inspect.getmodule(fn)
        global_namespace = vars(module) if module else {}
        hints = get_type_hints(fn, globalns=global_namespace, include_extras=True)
    except Exception as e:
        logger.warning("Failed to get type hints for function '%s': %s", fn.__name__, e)
        hints = {}

    result: dict[str, dict] = {}
    for name, param in sig.parameters.items():
        if name == "raw":
            continue

        annotation = hints.get(name)
        raw_default = param.default
        has_default = raw_default is not inspect.Parameter.empty

        # Mutable defaults to None
        if has_default and isinstance(raw_default, (list, dict)):
            default = None
        else:
            default = raw_default if has_default else None

        meta: dict = {}
        base_type: type | None = None

        if annotation is not None and get_origin(annotation) is Annotated:
            args = get_args(annotation)
            base_type = args[0]
            if len(args) > 1:
                meta_arg = args[1]
                if isinstance(meta_arg, ParamMeta):
                    meta = meta_arg.to_dict()
                elif isinstance(meta_arg, dict):
                    meta = dict(meta_arg)
        elif annotation is not None:
            base_type = annotation

        if "type" not in meta:
            meta["type"] = infer_param_type(base_type)
        if "default" not in meta:
            meta["default"] = default

        result[name] = meta

    return result


# -------- TemplateSchema --------

@dataclass(frozen=True)
class FunctionParamGroup:
    """Tracks an MNE function call and which of its kwargs are owned by the template."""

    dotted_name: str
    params: dict[str, dict]  # kwargs owned by the template (excluded from advanced)

    def __hash__(self) -> int:
        return hash(self.dotted_name)


@dataclass(frozen=True)
class TemplateSchema:
    """Schema describing the params and MNE function calls for one action or step.

    Used for two purposes:
        - Reverse-parsing: function_groups describe which MNE call kwargs to extract when reading params back out of
          generated code.
        - Editor awareness: all_primary_params() returns the full set of configurable params so the action editor knows
          what fields to show and the context menu can tell whether a step is configurable.

    For single-step actions, function_groups is populated and virtual_params is empty.
    For multistep actions, function_groups is empty and virtual_params holds the step's params.

    Attributes:
        function_groups: MNE function calls to introspect for param recovery and advanced-param discovery.
        virtual_params: Params_schema dict for steps that do not map directly to a single MNE function call.
    """

    function_groups: tuple[FunctionParamGroup, ...]
    virtual_params: dict[str, dict] = field(default_factory=dict)

    def all_primary_params(self) -> dict[str, dict]:
        """Return all params (virtual and function-group) as a flat params_schema dict."""

        result = dict(self.virtual_params)
        for g in self.function_groups:
            result.update(g.params)
        return result


# -------- AST utilities --------

def match_dotted_name(node: ast.expr, dotted_str: str) -> bool:
    """Check whether an AST expression node matches a dotted name string.

    Recursively collects Name and Attribute nodes to build a list of name parts,
    then compares against the split dotted string.

    Args:
        node: An AST expression representing a name or attribute chain.
        dotted_str: A dotted name string such as "raw.filter".

    Returns:
        True when the node exactly matches the dotted name.
    """
    parts = dotted_str.split(".")

    def collect(n: ast.expr) -> list[str] | None:
        if isinstance(n, ast.Name):
            return [n.id]
        if isinstance(n, ast.Attribute):
            base = collect(n.value)
            if base is None:
                return None
            return base + [n.attr]
        return None

    node_parts = collect(node)
    return node_parts == parts if node_parts is not None else False


class AdvancedParamInjector(ast.NodeTransformer):
    """AST transformer that appends missing kwargs from advanced_params into matching function calls."""

    def __init__(self, advanced_params: dict[str, dict]) -> None:
        self.advanced_params = advanced_params

    def visit_Call(self, node: ast.Call) -> ast.AST:
        self.generic_visit(node)
        for dotted_name, params in self.advanced_params.items():
            if match_dotted_name(node.func, dotted_name):
                existing = {kw.arg for kw in node.keywords}
                for name, value in params.items():
                    if name not in existing:
                        node.keywords.append(
                            ast.keyword(arg=name, value=value_to_ast(value))
                        )
                break
        return node


# -------- Step and action definitions --------

@dataclass(frozen=True)
class StepDefinition:
    """Descriptor for a single step within a multistep action.

    Attributes:
        step_id: Unique identifier embedded in generated step-block markers.
        title: Human-readable label shown in the action list and context menu.
        code_builder: Callable that accepts a params dict and returns Python source for this step.
            None for interactive-only steps.
        interactive: When True, this step requires user interaction and is dispatched to interactive_runner
            instead of the background thread.
        interactive_runner: Callable invoked on the main Qt thread for interactive steps.
            Receives (action, raw, parent) and returns a new Raw object or None to cancel.
        template_schema: Schema used to reverse-parse params from the step's generated code block.
    """

    step_id: str
    title: str
    code_builder: Callable[[dict], str] | None = None
    interactive: bool = False
    interactive_runner: InteractiveRunner | None = None
    template_schema: TemplateSchema | None = None


@dataclass(frozen=True)
class Prerequisite:
    """A prerequisite action that should be run before this one.

    Used to generate a user-facing warning when a dependency has not been completed.

    Attributes:
        action_id: Identifier of the required preceding action.
        message: Warning text shown in the prerequisites' dialog.
    """

    action_id: str
    message: str


@dataclass(frozen=True)
class ActionDefinition:
    """Immutable descriptor for a pipeline action type.

    Instances are constructed once at import time and stored in the registry.

    Attributes:
        action_id: Unique identifier string.
        title: Display name.
        params_schema: Dict mapping parameter names to widget spec dicts.
        doc: Short description shown in the action editor dialog.
        mne_doc_urls: Optional dict mapping label strings to MNE documentation URLs.
        code_builder: Callable that generates code from params; used for single-step actions.
        steps: Tuple of StepDefinition objects for multistep actions.
        template_schema: Schema for the whole action used by the editor to discover advanced MNE kwargs.
        prerequisites: Tuple of Prerequisite objects checked before running.
        param_widget_factories: Optional dict mapping custom param-type strings to factory callables that produce
            (container, value_widget) pairs.
    """

    action_id: str
    title: str
    params_schema: ParamsSchema
    doc: str
    mne_doc_urls: dict[str, str] = field(default_factory=dict)
    code_builder: CodeBuilder | None = None
    steps: tuple[StepDefinition, ...] | None = None
    template_schema: TemplateSchema | None = None
    prerequisites: tuple[Prerequisite, ...] = ()
    param_widget_factories: dict[str, Callable] | None = None

    def default_params(self) -> dict:
        """Return a dict of parameter defaults taken from params_schema.

        Returns:
            Dict mapping each param name to its declared default value.
        """
        return {name: spec["default"] for name, spec in self.params_schema.items()}

    def build_code(self, params: dict, advanced_params: dict | None = None) -> str:
        """Generate the full Python code string for this action.

        For multistep actions, each step's code is wrapped in "# Step[id]" / "# EndStep[id]" markers so the runner
        can split them at execution time. Advanced params are only injected for single-step actions.

        Args:
            params: Parameter values to substitute into the code template.
            advanced_params: Optional non-primary MNE kwargs to inject,
                grouped by dotted function name.

        Returns:
            Python source code string.
        """
        if self.steps:
            if len(self.steps) == 1:
                step_def = self.steps[0]
                if step_def.code_builder:
                    return step_def.code_builder(params)
                return ""
            parts = []
            for step_def in self.steps:
                if step_def.code_builder:
                    parts.append(f"# Step[{step_def.step_id}] {step_def.title}")
                    parts.append(step_def.code_builder(params))
                    parts.append(f"# EndStep[{step_def.step_id}]")
            return "\n\n".join(filter(None, parts))

        if not self.code_builder:
            return ""
        return self.code_builder(params, advanced_params=advanced_params)

    def has_steps(self) -> bool:
        """Return True when this action has more than one step.

        Actions with exactly one step are treated as single-step for execution
        purposes; they do not show step-level controls in the UI.
        """
        return bool(self.steps) and len(self.steps) > 1

    def single_step(self) -> StepDefinition | None:
        """Return the sole StepDefinition when there is exactly one step, else None."""
        if self.steps and len(self.steps) == 1:
            return self.steps[0]
        return None


# -------- Builder utilities --------

def wrap_builder(fn: Callable[..., str], defaults: dict[str, Any] | None = None) -> Callable[[dict], str]:
    """Wrap a template builder function into the (params: dict) -> str step interface.

    The returned callable filters the params dict to only the kwargs accepted by fn, filling missing keys from defaults.

    Args:
        fn: The template builder function to wrap.
        defaults: Optional dict of default values for fn's parameters, used
            when a param is absent from the runtime params dict.

    Returns:
        A callable that accepts a single params dict and returns a code string.
    """
    sig = inspect.signature(fn)
    param_names = frozenset(n for n in sig.parameters if n != "raw")
    _defaults: dict[str, Any] = defaults or {}

    def builder(params: dict) -> str:
        filtered: dict = {}
        for name in param_names:
            if name in params:
                filtered[name] = params[name]
            elif name in _defaults:
                filtered[name] = _defaults[name]
        return fn(**filtered)

    return builder


# -------- action_from_templates factory --------

def action_from_templates(
    *,
    action_id: str,
    title: str,
    action_file: str,
    doc: str,
    mne_doc_urls: dict[str, str] | None = None,
    prerequisites: tuple[Prerequisite, ...] = (),
    param_widget_factories: dict[str, Callable] | None = None,
    interactive_runners: dict[str, InteractiveRunner] | None = None,
) -> ActionDefinition:
    """Build an ActionDefinition by loading and introspecting a templates.py module.

    Discovers all @step-decorated builder functions in the templates module adjacent to action_file,
    then wires them into an ActionDefinition automatically.

    For single-step actions, the builder signature becomes the params_schema.
    Declare PRIMARY_PARAMS at module level to enable advanced-param introspection. All other function kwargs become
    available in the Advanced section of the editor.

    For multistep actions, each @step builder becomes a StepDefinition in definition order.
    Pass interactive_runners to handle interactive steps. PRIMARY_PARAMS is ignored for multistep actions.

    Args:
        action_id: Unique identifier string for the action.
        title: Human-readable display name shown in the UI.
        action_file: Path to the action's action.py file; templates.py is looked up in the same directory.
        doc: Short description shown in the action editor dialog.
        mne_doc_urls: Optional dict mapping label strings to MNE documentation URLs displayed as clickable links.
        prerequisites: Tuple of Prerequisite objects checked before running.
        param_widget_factories: Optional dict mapping custom param-type strings to factory callables that produce
            (container, value_widget) pairs.
        interactive_runners: Optional dict mapping step_id strings to interactive runner callables
            for interactive steps.

    Returns:
        A fully wired ActionDefinition ready for registration.

    Raises:
        ImportError: When the templates module cannot be loaded.
        AttributeError: When the templates module has no @step-decorated builder.
    """
    here = Path(action_file).parent

    # Import templates.py
    templates_path = here / "templates.py"
    module_name = f"mnetape.actions.{action_id}._templates"
    if module_name not in sys.modules:
        spec = importlib.util.spec_from_file_location(module_name, templates_path)
        if spec is None or spec.loader is None:
            raise ImportError(f"Cannot load templates from {templates_path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
    else:
        module = sys.modules[module_name]

    # Collect @step builders in definition order
    step_builders: list[StepBuilder] = [
        obj for obj in vars(module).values()
        if isinstance(obj, StepBuilder)
    ]
    if not step_builders:
        raise AttributeError(f"{templates_path} must define at least one @step(...) builder.")

    # --- Single-step action ---
    if len(step_builders) == 1:
        sb = step_builders[0]
        params_schema: dict[str, dict] = extract_schema_from_signature(sb.fn)

        primary_raw: dict = getattr(module, "PRIMARY_PARAMS", {})
        groups: list[FunctionParamGroup] = [
            FunctionParamGroup(dotted_name=fn_name, params={k: {} for k in (owned or [])})
            for fn_name, owned in primary_raw.items()
        ]
        template_schema = TemplateSchema(function_groups=tuple(groups))

        sig = inspect.signature(sb.fn)
        builder_param_names = frozenset(n for n in sig.parameters if n != "raw")

        def code_builder(params: dict, advanced_params: dict | None = None) -> str:
            filtered: dict = {}
            for name in builder_param_names:
                if name in params:
                    filtered[name] = params[name]
                elif name in params_schema and "default" in params_schema[name]:
                    filtered[name] = params_schema[name]["default"]
            code: str = sb.fn(**filtered)
            if advanced_params:
                try:
                    tree = ast.parse(code)
                    tree = AdvancedParamInjector(advanced_params).visit(tree)
                    ast.fix_missing_locations(tree)
                    code = ast.unparse(tree)
                except Exception as e:
                    logger.exception("Failed to inject advanced params for action '%s': %s", action_id, e)
            return code

        return ActionDefinition(
            action_id=action_id,
            title=title,
            params_schema=params_schema,
            code_builder=code_builder,
            template_schema=template_schema,
            doc=doc,
            mne_doc_urls=mne_doc_urls or {},
            prerequisites=prerequisites,
            param_widget_factories=param_widget_factories,
        )

    # --- Multi-step action ---
    all_params: dict[str, dict] = {}
    step_defs: list[StepDefinition] = []
    runners: dict[str, InteractiveRunner] = interactive_runners or {}
    for sb in step_builders:
        step_params = extract_schema_from_signature(sb.fn)
        all_params.update(step_params)
        defaults = {n: s.get("default") for n, s in step_params.items()}
        step_defs.append(StepDefinition(
            step_id=sb.step_id,
            title=sb.title,
            code_builder=wrap_builder(sb.fn, defaults),
            interactive=sb.interactive,
            interactive_runner=runners.get(sb.step_id),
            template_schema=TemplateSchema(function_groups=(), virtual_params=step_params),
        ))

    return ActionDefinition(
        action_id=action_id,
        title=title,
        params_schema=all_params,
        steps=tuple(step_defs),
        doc=doc,
        mne_doc_urls=mne_doc_urls or {},
        prerequisites=prerequisites,
        param_widget_factories=param_widget_factories,
    )
