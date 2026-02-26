# Core

The `core` package handles the core logic of the app, independently of the GUI.

## Modules

| Module              | Responsibility                                         |
|---------------------|--------------------------------------------------------|
| `models.py`         | Data structures: `ActionConfig`, `ActionStatus`        |
| `data_io.py`        | Load EEG files; format detection                       |
| `executor.py`       | Execute generated code against a Raw object            |
| `codegen.py`        | Generate code from actions; parse code back to actions |
| `logging_config.py` | Application-wide logging setup                         |

---

## models.py

### ActionStatus

```
PENDING: action has not been run yet
COMPLETE: action ran successfully
ERROR: action raised an exception
```

### ActionConfig

The central data structure. One instance per action in the pipeline.

`step_state["scope"]` holds the shared Python execution scope that is kept alive between steps so that objects
are available in later steps.

---

## data_io.py

A thin wrapper around MNE's reader functions with uniform error handling and format detection.

```python
from mnetape.core.data_io import load_raw_data

raw = load_raw_data("/path/to/recording.fif", preload=True)
```

Supported extensions: `.fif`, `.fif.gz`, `.edf`, `.bdf`, `.gdf`, `.vhdr`, `.set`, `.cnt`, `.mff`.

---

## executor.py

`exec_action_code` executes a code string in an isolated scope and returns the (potentially modified) `raw` object.


The execution scope always contains `raw`, `mne`, `np`, `numpy`, and anything already in `action.step_state["scope"]`
from a previous step.

**`reuse_scope=True`** is used by multistep actions: the scope from a previous step
(stored in `action.step_state["scope"]`) is merged in.

After execution, the scope is saved back to `action.step_state["scope"]` so the next step can reuse it.

---

## codegen.py

Performs two complementary tasks:

### 1. Actions -> code

**`generate_action_code(action)`** returns the Python source for a single action.

- If `action.is_custom` and `action.custom_code` is set, the verbatim code is returned unchanged.
- Otherwise, `action.build_code(params, advanced_params)` is called. This invokes the action's template builder(s)
  and then runs `AdvancedParamInjector` to splice in any non-default advanced kwargs.

For multistep actions the code for all steps is concatenated, each wrapped in `# Step[id] Title` / `# EndStep[id]`
delimiters.

**`generate_full_script(filepath, actions)`** assembles the complete pipeline script:

- Merges `import` statements from all action code blocks. 
- Inserts a `load_raw_data(...)` call (or a placeholder comment if no file is loaded).
- Wraps each action in `# In[N] Title` / `# End[N]` delimiters.
- Returns a standalone, runnable Python script.

### 2. Code -> actions

**`parse_script_to_actions(script)`** is the reverse: it reads a `.py` pipeline script and reconstructs the list
of `ActionConfig` objects.

```
script
  - extract_action_blocks()             # find # In[N] / # End[N] pairs
  - get_action_by_title()               # match title to registered action
  - parse_params_from_code()            # extract primary param values from AST
  - parse_advanced_params_from_code()   # extract advanced param values from AST
  - structure_signature()               # compare code structure to expected
    -> ActionConfig list
```

If the structure of a code block does not match what the action would generate (because the user hand-edited it),
the block is imported as a `custom` action and its code is stored verbatim in `custom_code`.

### Reverse-parsing detail

`extract_params_from_schema(schema, code, defaults)` walks the AST of a code block looking for function calls that match
dotted names in the schema (e.g. `raw.filter`). <br>
For each match, it reads the keyword argument values and converts them from AST nodes back to Python literals.
This is how `l_freq=0.5` in the code becomes `{"l_freq": 0.5}` in `ActionConfig.params`.

### Structure comparison

`structure_signature(code)` normalizes a code block for structural comparison: all literal argument values are replaced
with a `_` placeholder, leaving only the function names and keyword argument names. Two structurally equivalent blocks
will produce the same signature even if their parameter values differ.