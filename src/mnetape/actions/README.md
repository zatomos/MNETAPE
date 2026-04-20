# Actions

The `actions` package is a plugin system for preprocessing steps.
Each action lives in its own subpackage and is discovered automatically at startup.

## Directory structure

```text
actions/
├── base.py          # ActionDefinition, @builder decorator, action_from_templates
├── introspect.py    # Runtime introspection for advanced MNE kwargs
├── registry.py      # Auto-discovery and lookup
└── <action_id>/
    ├── action.py    # Exports ACTION = action_from_templates(...)
    ├── templates.py # @builder-decorated template function(s)
    └── widgets.py   # Optional custom param widgets or interactive runners
```

## Built-in actions

| Action ID           | Title                    | Input -> Output                    |
|---------------------|--------------------------|-----------------------------------|
| `load_file`         | Load File                | -> Raw                             |
| `filter`            | Bandpass Filter          | Raw/Epochs/Evoked -> same          |
| `notch`             | Notch Filter             | Raw/Epochs/Evoked -> same          |
| `resample`          | Resample                 | Raw/Epochs/Evoked -> same          |
| `crop`              | Crop                     | Raw/Epochs/Evoked -> same          |
| `normalize`         | Normalize                | Raw -> Raw                         |
| `set_montage`       | Set Montage              | Raw -> Raw                         |
| `reference`         | Re-reference             | Raw/Epochs/Evoked -> same          |
| `set_channel_types` | Set Channel Types        | Raw/Epochs/Evoked -> same          |
| `drop_channels`     | Drop Channels            | Raw/Epochs/Evoked -> same          |
| `interpolate`       | Interpolate Bad Channels | Raw/Epochs/Evoked -> same          |
| `set_annotations`   | Set Annotations          | Raw/Epochs -> same                 |
| `detect_events`     | Detect Events            | Raw -> Raw                         |
| `epoch_fixed`       | Fixed-Length Epochs      | Raw -> Epochs                      |
| `epoch_events`      | Event-Based Epochs       | Raw -> Epochs                      |
| `drop_bad_epochs`   | Drop Bad Epochs          | Epochs -> Epochs                   |
| `average_epochs`    | Average Epochs           | Epochs -> Evoked                   |
| `ica_fit`           | Fit ICA                  | Raw -> ICA                         |
| `ica_apply`         | Apply ICA                | ICA -> Raw                         |
| `custom`            | Custom Action            | any -> any                         |

## How actions are registered

On first access, `registry.py` scans all subpackages of `mnetape.actions`.
Any subpackage whose `action.py` exports an `ACTION` attribute of type `ActionDefinition` is registered.
Import failures are skipped.

## Adding a new action

1. Create `src/mnetape/actions/<action_id>/`.
2. Add `templates.py` with a `@builder`-decorated function.
3. Add `action.py`:

```python
from mnetape.actions.base import action_from_templates

ACTION = action_from_templates(
    action_id="my_action",
    title="My Action",
    doc="Short description shown in the UI.",
    action_file=__file__,
)
```

4. Restart the app — the action is discovered automatically.

## Writing a template function

The `@builder` decorator introspects the function via AST and inspection to extract:
- `body_source` — the function body, used verbatim in generated code.
- `input_vars` — the scope variable names declared as the first parameters.
- `param_names` — the remaining (user-facing) parameter names.
- `input_type` / `output_type` — inferred from the type annotations on the first scope argument and the return annotation.

**Scope variables** (MNE objects managed by the executor) must appear as the **first parameters**
of the template function with recognized MNE type annotations. Everything after them is a user-facing parameter.

```python
from mnetape.actions.base import builder
import mne

@builder
def bandpass(raw: mne.io.Raw, l_freq: float = 1.0, h_freq: float = 40.0) -> mne.io.Raw:
    raw.filter(l_freq=l_freq, h_freq=h_freq)
    return raw
```

Supported scope variable annotations and their corresponding data types:

| First parameter annotation   | Input `DataType` | Scope variables injected        |
|------------------------------|------------------|---------------------------------|
| `mne.io.Raw`                 | `RAW`            | `raw`                           |
| `mne.BaseEpochs`             | `EPOCHS`         | `epochs`                        |
| `mne.Evoked`                 | `EVOKED`         | `evoked`                        |
| `mne.preprocessing.ICA`      | `ICA`            | `ica`, `raw`                    |

Supported return annotations and their output types:

| Return annotation            | Output `DataType` |
|------------------------------|-------------------|
| `mne.io.Raw`                 | `RAW`             |
| `mne.BaseEpochs`             | `EPOCHS`          |
| `mne.Evoked`                 | `EVOKED`          |
| `tuple[mne.preprocessing.ICA, ...]` | `ICA`    |

## Parameter widget metadata

Use `Annotated[T, ParamMeta(...)]` to control how a parameter is rendered in the UI:

```python
from typing import Annotated
from mnetape.actions.base import builder, ParamMeta
import mne

@builder
def my_action(
    raw: mne.io.Raw,
    method: Annotated[str, ParamMeta(
        type="choices",
        label="Method",
        choices=["mean", "median"],
        default="mean",
    )],
) -> mne.io.Raw:
    ...
```

`ParamMeta` fields: `type`, `label`, `description`, `default`, `min`, `max`, `decimals`, `choices`, `nullable`, `visible_when`.

The `type` field maps to a Qt widget: `"text"`, `"float"`, `"int"`, `"bool"`, `"choices"`, `"list"`, `"dict"`.
Without `Annotated`, the widget type is inferred from the Python type annotation (`float` -> spinner, `bool` -> checkbox, etc.).

## Advanced parameters

To expose extra MNE kwargs in the **Advanced** tab of the action editor, declare a `_kwargs` group parameter
(or `**kwargs`) in the template signature and call the MNE function with it:

```python
@builder
def bandpass(raw: mne.io.Raw, l_freq: float, h_freq: float, filter_kwargs={}) -> mne.io.Raw:
    raw.filter(l_freq=l_freq, h_freq=h_freq, **filter_kwargs)
    return raw
```

`action_from_templates` introspects the target MNE function (`raw.filter`) to build the advanced schema
from its signature. Parameters already in `PRIMARY_PARAMS` are excluded. Users only see them when
they differ from the MNE default.

## Param variants

When a single action needs different code bodies depending on a user-selected parameter value,
use `@builder(key="value")` to define one body per variant:

```python
from mnetape.actions.base import builder, ParamMeta
from typing import Annotated
import mne

@builder
def schema(
    raw: mne.io.Raw,
    source: Annotated[str, ParamMeta(type="choices", choices=["stim", "annotations"], default="stim")],
) -> mne.io.Raw:
    pass  # schema-only; body provided by keyed builders below

@builder(key="stim")
def from_stim(raw: mne.io.Raw, source: str) -> mne.io.Raw:
    events = mne.find_events(raw)
    return raw

@builder(key="annotations")
def from_annot(raw: mne.io.Raw, source: str) -> mne.io.Raw:
    events, _ = mne.events_from_annotations(raw)
    return raw
```

Pass `variant_param="source"` to `action_from_templates`. At code-gen time the body matching
`action.params["source"]` is injected into the generated function.

## Type variants

When an action should behave differently depending on whether it receives `Raw` or `Epochs` data,
declare multiple builders with different input type annotations:

```python
@builder
def on_raw(raw: mne.io.Raw, ...) -> mne.io.Raw: ...

@builder
def on_epochs(epochs: mne.BaseEpochs, ...) -> mne.BaseEpochs: ...
```

`action_from_templates` groups them by input type and builds one inner `ActionDefinition` per type.
The correct variant is selected at code-gen and execution time based on the data flowing through
the pipeline.

## Custom inline actions

The `custom` action lets users write arbitrary Python directly in the code panel.
Custom blocks are delimited by `# [N] Title` headers and preserved verbatim through parse/generate round-trips.

## Interactive actions

Some actions open a dedicated dialog instead of a static configuration form.
Place an `INTERACTIVE_RUNNER` in `widgets.py`:

```python
from mnetape.actions.base import InteractiveRunner

def _run(action, data, parent):
    # Show a custom dialog, mutate action.params in-place, return updated data
    ...

INTERACTIVE_RUNNER = InteractiveRunner(
    run=_run,
    needs_inspection=lambda action: action.params.get("excluded") is None,
    managed_params=("excluded",),  # reset to defaults when saving the default pipeline
)
```

`InteractiveRunner` fields:
- `run(action, data, parent)` — called before `exec_action`; returns updated data.
- `needs_inspection(action)` — returns `True` when user review is still required.
- `build_editor_widget(data, action, parent, param_widgets)` — returns a `QWidget` embedded at the top of the action editor dialog.
- `managed_params` — param names reset to schema defaults when the pipeline is saved as project default.

## Custom widget bindings

To replace a specific parameter's default widget with a custom one, place a `WIDGET_BINDINGS` list in `widgets.py`:

```python
from mnetape.actions.base import ParamWidgetBinding

def channel_picker(current_value, raw, param_widgets):
    # Build and return (container_widget, value_widget)
    ...

WIDGET_BINDINGS = [
    ParamWidgetBinding(param_name="channels", factory=channel_picker),
]
```
