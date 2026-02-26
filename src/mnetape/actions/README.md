# Actions

The actions package is a **plugin system**: every subdirectory that contains an `action.py` exposing an `ACTION`
constant is automatically discovered and registered at startup. <br>
The rest of the application interacts with actions through a stable interface defined in `base.py` and `registry.py`.

## Directory layout

```
actions/
├── base.py             # Core abstractions: Fragment, ParamMeta, ActionDefinition, decorators
├── registry.py         # Auto-discovery and lookup helpers
├── introspect.py       # Runtime MNE parameter introspection (for "Advanced" sections)
├── custom/             # Built-in "custom code" action (no templates)
└── <action_id>/        # Any subdirectory with action.py is auto-registered
```

---

## Key concepts

### Fragment

A `Fragment` wraps a plain Python function so its body can be extracted, parameterized, and turned into a code string
at generation time. The function is never called at runtime.

```python
from mnetape.actions.base import fragment

@fragment
def _do_filter(raw, l_freq, h_freq):
    raw.filter(l_freq=l_freq, h_freq=h_freq)

code = _do_filter.inline(l_freq=0.5, h_freq=45.0)
```

**How it works:**

1. `@fragment` parses the function body with Python's `ast` module.
2. `.inline(**kwargs)` substitutes every parameter name with its literal value using `NameSubstitutor`
   (an `ast.NodeTransformer`).
3. `ConstantBranchPruner` removes dead branches produced by the substitution (e.g. an `if True:` block becomes its body).
4. The modified AST is unparsed back to source and returned.

Variables listed in `Fragment.SCOPE_VARS` are never injected. They are expected to exist in the execution scope already.

---

### ParamMeta

`ParamMeta` is a dataclass attached to a builder parameter via `Annotated[T, ParamMeta(...)]`.
It carries the metadata needed to render the right widget in the action editor.

| Field         | Purpose                                                         |
|---------------|-----------------------------------------------------------------|
| `type`        | Widget kind: `"float"`, `"int"`, `"bool"`, `"choice"`, `"text"` |
| `label`       | Form label                                                      |
| `description` | Shown as tooltip / help text                                    |
| `default`     | Initial value                                                   |
| `min` / `max` | Spinbox bounds (numeric types)                                  |
| `decimals`    | Decimal places (float only)                                     |
| `choices`     | Options list (choice type)                                      |
| `nullable`    | Allows the field to be left empty (None)                        |

---

### Single-step action

Most actions have one step.
`action_from_templates` reads `templates.py` adjacent to `action.py`, discovers all `@step` builders,
extracts the `params_schema` from their signatures, and builds an `ActionDefinition`.

---

### Multi-step action

When preprocessing requires several stages that share state, define multiple `@step` builders.
Each step gets its own code block in the generated script, delimited by `# Step[id]` / `# EndStep[id]` markers.

Steps share an execution scope: a `dict` containing `raw`, `ica`, `mne`, `numpy`, etc.,
persisted in `action.step_state["scope"]` between steps.

Interactive steps run on the Qt main thread via an `interactive_runner` callable provided to `action_from_templates`.

---

### Advanced parameters

For any MNE function listed in `PRIMARY_PARAMS`, `introspect.py` uses Python's `inspect` module to enumerate all kwargs
not already owned by the action. These appear in the action editor's collapsible "Advanced" section.

At code generation time, `AdvancedParamInjector` (an `ast.NodeTransformer`) appends the non-default advanced kwargs to
the matching function call in the generated code.

---

### Registry

`registry.py` scans `mnetape.actions` for sub-packages at first access. <br>
Import errors in individual `action.py` files are caught and logged; they never prevent other actions from loading.

---

## Adding a new action

1. Create `src/mnetape/actions/<action_id>/` with `__init__.py`.

2. Write `templates.py`:
   - Define `PRIMARY_PARAMS` (the MNE kwargs you expose directly).
   - Write `@fragment` helpers that produce MNE calls.
   - Write a `@step("apply")` `template_builder(**params) -> str` function.

3. Write `action.py`:
   ```python
   from mnetape.actions.base import action_from_templates
   ACTION = action_from_templates(
       action_id="<action_id>",
       title="Title",
       doc="One-line description shown in the editor.",
       action_file=__file__,
   )
   ```

4. The action is registered automatically on the next launch.

### Custom widget factories

If a parameter type (e.g. `"channels"`) needs a custom Qt widget, pass `param_widget_factories`
to `action_from_templates`.

### Prerequisites

The user can be warned when the pipeline is missing a recommended upstream step by setting `prerequisites`.
