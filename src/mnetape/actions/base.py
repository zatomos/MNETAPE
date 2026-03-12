"""Base types and factories for EEG pipeline actions.

This module contains the building blocks used to define preprocessing actions:
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

from mnetape.actions.introspect import get_advanced_params
from mnetape.core.ast_utils import get_dotted_name, value_to_ast
from mnetape.core.models import ANNOTATION_TO_DATATYPE, RETURN_VARS, DataType, SCOPE_VARS

logger = logging.getLogger(__name__)

ParamsSchema = dict[str, dict]


# -------- Parameter metadata type --------

@dataclass
class ParamMeta:
    """Widget metadata for parameter annotations."""

    type: str = "text"
    label: str = ""
    description: str = ""
    default: Any = None
    min: float | None = None
    max: float | None = None
    decimals: int | None = None
    choices: list[str] | None = None
    nullable: bool | None = None
    visible_when: dict[str, list] | None = None

    def to_dict(self) -> dict[str, Any]:
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
        if self.visible_when is not None:
            d["visible_when"] = self.visible_when
        return d


# -------- Schema extraction from Annotated signatures --------

def infer_param_type(annotation: type | None) -> str:
    """Map Python type annotations to widget type strings. Defaults to 'text'."""
    if annotation is None:
        return "text"
    origin = get_origin(annotation)
    if origin is not None:
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
    """Extract a params_schema dict from a builder function signature.

    Parameters in SCOPE_VARS, ending with '_kwargs' or named 'kwargs' are excluded.
    Annotated[T, ParamMeta(...)] parameters use the ParamMeta for widget metadata.
    Mutable defaults (list, dict) are normalized to None.
    """
    sig = inspect.signature(fn)
    try:
        module = inspect.getmodule(fn)
        global_namespace = dict(vars(module)) if module else {}
        hints = get_type_hints(fn, globalns=global_namespace, include_extras=True)
    except Exception as e:
        logger.warning("Failed to get type hints for function '%s': %s", fn.__name__, e)
        hints = {}

    result: dict[str, dict] = {}
    for name, param in sig.parameters.items():
        if name in SCOPE_VARS:
            continue
        # Skip kwargs groups
        if param.kind == inspect.Parameter.VAR_KEYWORD:
            continue
        if name.endswith("_kwargs"):
            continue

        annotation = hints.get(name)
        raw_default = param.default
        has_default = raw_default is not inspect.Parameter.empty

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


# -------- Type inference from AST annotations --------

def infer_input_from_ast(func_def: ast.FunctionDef) -> DataType:
    """Infer input DataType from the annotation on the first scope-variable argument."""
    for arg in func_def.args.args:
        if arg.arg not in SCOPE_VARS or arg.annotation is None:
            continue
        dotted = get_dotted_name(arg.annotation)
        dt = ANNOTATION_TO_DATATYPE.get(dotted or "")
        if dt is not None:
            return dt
    raise TypeError(f"Builder '{func_def.name}' must annotate its first scope argument with a recognized MNE type.")


def infer_output_from_ast(func_def: ast.FunctionDef) -> DataType:
    """Infer output DataType from the function return annotation."""
    ret = func_def.returns
    if ret is None:
        raise TypeError(f"Builder '{func_def.name}' must declare a return annotation.")

    # Simple return type
    dotted = get_dotted_name(ret)
    if dotted:
        dt = ANNOTATION_TO_DATATYPE.get(dotted)
        if dt is not None:
            return dt

    # Tuple return
    if isinstance(ret, ast.Subscript) and get_dotted_name(ret.value) == "tuple":
        slice_node = ret.slice
        if isinstance(slice_node, ast.Tuple) and slice_node.elts:
            first_dotted = get_dotted_name(slice_node.elts[0])
            if first_dotted == "mne.preprocessing.ICA":
                return DataType.ICA

    raise TypeError(
        f"Builder '{func_def.name}' has unrecognized return annotation: {ast.unparse(ret)!r}. "
        f"Supported: mne.io.Raw, mne.BaseEpochs, mne.Evoked, tuple[mne.preprocessing.ICA, ...]."
    )


# -------- Result builder --------

@dataclass
class ResultBuilder:
    """Holds a result-builder callable registered via @result_builder."""
    fn: Callable


def result_builder(fn: Callable) -> ResultBuilder:
    """Mark a function as the result builder for an action.

    The function receives the output data object after execution and returns an ActionResult.
    """
    return ResultBuilder(fn=fn)


# -------- Builder --------

@dataclass
class ActionBuilder:
    """Holds the template builder callable plus the extracted body, param names, and inferred types."""

    fn: Callable
    body_source: str = ""
    input_vars: list = field(default_factory=list)
    param_names: list = field(default_factory=list)
    input_type: DataType = field(default_factory=lambda: DataType.RAW)
    output_type: DataType = field(default_factory=lambda: DataType.RAW)
    kwargs_groups: list = field(default_factory=list)
    kwargs_targets: dict = field(default_factory=dict)  # group_name -> dotted call name


def builder(fn: Callable) -> ActionBuilder:
    """Mark a function as the body template for an action.

    Scope variables should be declared as the first positional parameters of the function. They are automatically
    excluded from param_names and used to infer input_type and output_type.

    Args ending with '_kwargs' or **kwargs are detected and stored in kwargs_groups.
    They are excluded from param_names. The body AST is scanned to build kwargs_targets: a mapping from group_name
    to the dotted call name that unpacks it.
    """
    ab = ActionBuilder(fn=fn)

    source = textwrap.dedent(inspect.getsource(fn))
    tree = ast.parse(source)
    func_def = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == fn.__name__
    )
    all_args = [a.arg for a in func_def.args.args]
    ab.body_source = "\n".join(ast.unparse(stmt) for stmt in func_def.body)
    ab.input_vars = [a for a in all_args if a in SCOPE_VARS]

    # Detect kwargs groups
    kwargs_groups: list[str] = []

    # Named *_kwargs args
    for a in func_def.args.args:
        if a.arg not in SCOPE_VARS and a.arg.endswith("_kwargs"):
            kwargs_groups.append(a.arg)

    # **kwargs VAR_KEYWORD
    if func_def.args.kwarg is not None and func_def.args.kwarg.arg == "kwargs":
        kwargs_groups.append("kwargs")

    ab.kwargs_groups = kwargs_groups

    # param_names excludes scope vars and kwargs groups
    kwargs_group_set = set(kwargs_groups)
    ab.param_names = [
        a for a in all_args
        if a not in SCOPE_VARS and a not in kwargs_group_set
    ]

    ab.input_type = infer_input_from_ast(func_def)
    ab.output_type = infer_output_from_ast(func_def)

    # Scan body AST for calls that unpack a kwargs group via **group_name
    kwargs_targets: dict[str, str] = {}
    for node in ast.walk(ast.Module(body=func_def.body, type_ignores=[])):
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg is None and isinstance(kw.value, ast.Name):
                    group_name = kw.value.id
                    if group_name in kwargs_group_set:
                        dotted = get_dotted_name(node.func)
                        if dotted:
                            kwargs_targets[group_name] = dotted
    ab.kwargs_targets = kwargs_targets

    return ab


# -------- Action definitions --------

@dataclass(frozen=True)
class Prerequisite:
    """A prerequisite action that should be completed before this one."""

    action_id: str
    message: str


@dataclass(frozen=True)
class ParamWidgetBinding:
    """Binds a custom widget factory to a specific parameter by name."""

    param_name: str
    factory: Callable  # (current_value, raw, parent) -> (container, value_widget)


@dataclass(frozen=True)
class ActionDefinition:
    """Immutable descriptor for a pipeline action type.

    Attributes:
        action_id: Unique identifier string.
        title: Display name.
        params_schema: Dict mapping parameter names to widget spec dicts.
        doc: Short description shown in the action editor dialog.
        body_source: unparsed function body from the builder template.
        param_names: Ordered list of user-facing parameter names (excludes scope vars).
        kwargs_groups: Tuple of kwargs group names.
        kwargs_targets: Dict mapping group_name to the dotted call name that unpacks it.
        mne_doc_urls: Optional dict mapping label strings to MNE documentation URLs.
        prerequisites: Tuple of Prerequisite objects checked before running.
        widget_bindings: Tuple of ParamWidgetBinding objects mapping param names to factory callables.
        input_type: Expected input data type for this action.
        output_type: Output data type produced by this action.
    """

    action_id: str
    title: str
    params_schema: ParamsSchema
    doc: str
    body_source: str = ""
    input_vars: list = field(default_factory=list)
    param_names: list = field(default_factory=list)
    kwargs_groups: tuple = ()
    kwargs_targets: dict = field(default_factory=dict)
    extra_imports: tuple = ()
    mne_doc_urls: dict = field(default_factory=dict)
    prerequisites: tuple = ()
    widget_bindings: tuple = ()
    advanced_schema: dict = field(default_factory=dict)
    variants: dict = field(default_factory=dict)
    input_type: DataType = field(default_factory=lambda: DataType.RAW)
    output_type: DataType = field(default_factory=lambda: DataType.RAW)
    result_builder_fn: Callable | None = None

    def default_params(self) -> dict:
        """Return a dict of parameter defaults taken from params_schema."""
        return {name: spec["default"] for name, spec in self.params_schema.items()}

    def build_signature(self, func_name: str) -> str:
        """Return the canonical `def func_name(...):` line for this action."""
        sig_parts = list(self.input_vars) + list(self.param_names)
        for group in self.kwargs_groups:
            if group != "kwargs":
                sig_parts.append(f"{group}={{}}")
        sig = f"def {func_name}({', '.join(sig_parts)}"
        if "kwargs" in self.kwargs_groups:
            sep = ", " if sig_parts else ""
            sig += f"{sep}**kwargs"
        return sig + "):"

    def build_function_def(self, func_name: str, context_type: DataType | None = None) -> str:
        """Generate a Python function definition for this action.

        The signature starts with the data input args, followed by primary param names, then any named _kwargs groups
        (with {} defaults), then **kwargs if present.

        When this action has variants, delegates to the matching variant for the given context_type.

        Args:
            func_name: Name to give the generated function.
            context_type: The DataType flowing through the pipeline at this point.

        Returns:
            Complete Python function definition as a string.
        """
        if self.variants and context_type is not None:
            variant = self.variants.get(context_type)
            if variant is not None:
                return variant.build_function_def(func_name)
        return f"{self.build_signature(func_name)}\n{textwrap.indent(self.body_source, '    ')}"

    def build_function_def_with_body(self, func_name: str, body: str, context_type: DataType | None = None) -> str:
        """Generate a function definition using the canonical signature but a custom body.

        Used when the user has edited a function body in the code panel. The signature stays canonical so the call site
        remains valid.

        When this action has variants, delegates to the matching variant for the given context_type.

        Args:
            func_name: Name to give the generated function.
            body: Replacement function body source string.
            context_type: The DataType flowing through the pipeline at this point.

        Returns:
            Complete Python function definition as a string.
        """
        if self.variants and context_type is not None:
            variant = self.variants.get(context_type)
            if variant is not None:
                return variant.build_function_def_with_body(func_name, body)
        return f"{self.build_signature(func_name)}\n{textwrap.indent(body, '    ')}"

    def build_call_site(self, func_name: str, params: dict, advanced_params: dict | None = None, context_type: DataType | None = None) -> str:
        """Generate a call-site assignment statement for this action.

        When this action has variants, delegates to the matching variant for the given context_type.

        Args:
            func_name: Name of the function to call.
            params: Primary parameter values.
            advanced_params: Optional kwargs grouped by group name.
            context_type: The concrete DataType flowing through the pipeline at this point (used for variant lookup).

        Returns:
            Python assignment statement string.
        """
        if self.variants and context_type is not None:
            variant = self.variants.get(context_type)
            if variant is not None:
                return variant.build_call_site(func_name, params, advanced_params)
        return_var = RETURN_VARS.get(self.output_type, "raw")
        adv = advanced_params or {}

        call_parts = list(self.input_vars)
        for name in self.param_names:
            value = params.get(name, self.params_schema.get(name, {}).get("default"))
            call_parts.append(f"{name}={ast.unparse(value_to_ast(value))}")

        if "kwargs" in self.kwargs_groups:
            # Flat extra kwargs. Only emit non-empty ones.
            for k, v in adv.get("kwargs", {}).items():
                call_parts.append(f"{k}={ast.unparse(value_to_ast(v))}")
        else:
            # Named group. Always emit explicitly.
            for group in self.kwargs_groups:
                group_val = adv.get(group, {})
                call_parts.append(f"{group}={ast.unparse(value_to_ast(group_val))}")

        return f"{return_var} = {func_name}({', '.join(call_parts)})"


# -------- action_from_templates factory --------

def action_from_templates(
    *,
    action_id: str,
    title: str,
    doc: str,
    extra_imports: tuple[str, ...] = (),
    mne_doc_urls: dict[str, str] | None = None,
    prerequisites: tuple = (),
) -> ActionDefinition:
    """Build an ActionDefinition by loading and introspecting a templates.py module.

    Discovers the single builder function in the template module.
    input_type and output_type are inferred automatically from the function signature (scope vars as first params)
    and the return statement.

    If a widgets.py file exists, it is autoloaded and its WIDGET_BINDINGS list is used to bind custom widget factories
    to parameters by name.

    Args:
        action_id: Unique identifier string for the action.
        title: Human-readable display name shown in the UI.
        doc: Short description shown in the action editor dialog.
        extra_imports: Tuple of additional import statements to include in the generated function.
        mne_doc_urls: Optional dict mapping label strings to MNE documentation URLs.
        prerequisites: Tuple of Prerequisite objects checked before running.

    Returns:
        A fully wired ActionDefinition ready for registration.
    """
    here = Path(inspect.stack()[1].filename).parent

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

    action_builders: list[ActionBuilder] = [
        obj for obj in vars(module).values()
        if isinstance(obj, ActionBuilder)
    ]
    if not action_builders:
        raise AttributeError(f"{templates_path} must define at least one @builder function.")

    # Pick up @result_builder function
    result_builder_instance = next(
        (obj for obj in vars(module).values() if isinstance(obj, ResultBuilder)), None
    )
    rb_fn = result_builder_instance.fn if result_builder_instance else None

    primary_ab = action_builders[0]
    params_schema: dict[str, dict] = extract_schema_from_signature(primary_ab.fn)

    # Populate advanced_schema by introspecting MNE function signatures
    advanced_schema: dict[str, dict[str, dict]] = {}
    if primary_ab.kwargs_targets:
        primary_names = frozenset(params_schema.keys())
        for group_name, dotted_name in primary_ab.kwargs_targets.items():
            adv = get_advanced_params(dotted_name, primary_names)
            if adv:
                advanced_schema[group_name] = adv

    # Autoload widgets.py for widget bindings
    widget_bindings: tuple[ParamWidgetBinding, ...] = ()
    widgets_path = here / "widgets.py"
    if widgets_path.exists():
        w_module_name = f"mnetape.actions.{action_id}._widgets"
        w_module = sys.modules.get(w_module_name)
        if w_module is None:
            w_spec = importlib.util.spec_from_file_location(w_module_name, widgets_path)
            if w_spec is None or w_spec.loader is None:
                logger.warning("Cannot load widgets from %s", widgets_path)
            else:
                w_module = importlib.util.module_from_spec(w_spec)
                sys.modules[w_module_name] = w_module
                w_spec.loader.exec_module(w_module)
        if w_module is not None:
            bindings = getattr(w_module, "WIDGET_BINDINGS", None)
            if bindings:
                widget_bindings = tuple(bindings)

    if len(action_builders) == 1:
        # Single-type action
        ab = action_builders[0]
        return ActionDefinition(
            action_id=action_id,
            title=title,
            params_schema=params_schema,
            body_source=ab.body_source,
            input_vars=ab.input_vars,
            param_names=ab.param_names,
            kwargs_groups=tuple(ab.kwargs_groups),
            kwargs_targets=ab.kwargs_targets,
            extra_imports=extra_imports,
            doc=doc,
            mne_doc_urls=mne_doc_urls or {},
            prerequisites=prerequisites,
            widget_bindings=widget_bindings,
            advanced_schema=advanced_schema,
            variants={},
            input_type=ab.input_type,
            output_type=ab.output_type,
            result_builder_fn=rb_fn,
        )
    else:
        # Multi-type action. Build a variant ActionDefinition for each builder
        variants: dict = {}
        for ab in action_builders:
            variant = ActionDefinition(
                action_id=action_id,
                title=title,
                params_schema=params_schema,
                doc=doc,
                body_source=ab.body_source,
                input_vars=ab.input_vars,
                param_names=ab.param_names,
                kwargs_groups=tuple(ab.kwargs_groups),
                kwargs_targets=ab.kwargs_targets,
                extra_imports=extra_imports,
                mne_doc_urls=mne_doc_urls or {},
                prerequisites=prerequisites,
                widget_bindings=widget_bindings,
                advanced_schema=advanced_schema,
                variants={},
                input_type=ab.input_type,
                output_type=ab.output_type,
            )
            variants[ab.input_type] = variant

        return ActionDefinition(
            action_id=action_id,
            title=title,
            params_schema=params_schema,
            doc=doc,
            body_source=primary_ab.body_source,
            input_vars=primary_ab.input_vars,
            param_names=primary_ab.param_names,
            kwargs_groups=tuple(primary_ab.kwargs_groups),
            kwargs_targets=primary_ab.kwargs_targets,
            extra_imports=extra_imports,
            mne_doc_urls=mne_doc_urls or {},
            prerequisites=prerequisites,
            widget_bindings=widget_bindings,
            advanced_schema=advanced_schema,
            variants=variants,
            input_type=DataType.ANY,
            output_type=DataType.ANY,
            result_builder_fn=rb_fn,
        )
