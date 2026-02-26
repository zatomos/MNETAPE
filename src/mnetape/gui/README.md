# GUI

The `gui` package contains all Qt6 interface code. It is organized into four layers:

| Layer       | Directory      | What it contains                                    |
|-------------|----------------|-----------------------------------------------------|
| Entry point | `app.py`       | Application bootstrap, stylesheet, `main()`         |
| Controllers | `controllers/` | Business logic - own no widgets directly            |
| Panels      | `panels/`      | Large persistent views (code editor, visualisation) |
| Dialogs     | `dialogs/`     | Modal dialogs                                       |
| Widgets     | `widgets/`     | Reusable leaf components                            |

---

## Application bootstrap - `app.py`

`main()` is the console-script entry point (`eeg-ui`).

---

## Shared state - `controllers/state.py`

`AppState` is a plain dataclass that holds every piece of mutable application state. It is created once by `MainWindow`
and passed by reference to every controller.


`raw_states[i]` is the `Raw` object produced by `actions[i]`. Downstream actions always read from the last entry
in `raw_states`.

---

## MainWindow - `controllers/main_window.py`

`MainWindow` is the top-level `QMainWindow`. It owns the layout and wires all controllers together.

**Layout:**

```
┌─────────────────────────────────────────┐
│  Menu bar                               │
├───────────────┬─────────────────────────┤
│  Action list  │  Code panel             │
│               │      or                 │
│               │  Visualisation panel    │
├───────────────┴─────────────────────────┤
│  Status bar                             │
└─────────────────────────────────────────┘
```

---

## Controllers

Controllers hold the business logic for a specific concern. They receive `AppState` and `MainWindow` references
but do not own any top-level widgets.

### FileHandler - `controllers/file_handler.py`

Handles all file I/O.

### PipelineRunner - `controllers/pipeline_runner.py`

Executes actions and steps.

**Threading model:**

- Non-interactive steps and full-action runs execute in a background `QThread`. A modal `QProgressDialog` blocks the UI.
- Interactive steps run on the **main Qt thread**. The background worker signals the main thread, waits for the user
  to finish, then continues.
- On completion, `state.raw_states` is updated and the visualization panel is refreshed.

### ActionController - `controllers/action_controller.py`

Manages the action sidebar and the code→action sync.

| Operation     | Trigger                                       |
|---------------|-----------------------------------------------|
| Add action    | "+ Add action" -> `AddActionDialog`           |
| Remove action | Delete key / context menu                     |
| Edit action   | Double-click / context menu -> `ActionEditor` |
| Reorder       | Up/Down buttons                               |
| Run next step | Right-click context menu (multi-step actions) |
| Reset steps   | Right-click context menu                      |

When an action is removed or moved, all downstream actions are reset to `PENDING` because their input `raw` may
have changed.

`ActionController` also owns the code-action sync: it debounces manual code edits and reconciles the action list when
the user edits the script directly.

### NavController - `controllers/nav_controller.py`

Manages the step selector combo box (prev/next navigation, selection sync with the action list) and provides
the MNE browser shortcut.

---

## Panels

### CodePanel - `panels/code_panel.py`

A `QScintilla`-based code editor with action-block highlighting.

- Each action block (`# In[N]` ... `# End[N]`) is highlighted with a unique background color derived from the
  action title.
- A `QFileSystemWatcher` monitors the backing `.py` file for external edits.
- An `internal_update` flag prevents feedback loops when `MainWindow` programmatically updates the editor.
- When the user edits the code manually, we call a timer debounce. We then parse the script into actions and update
  the action list to reflect any changes.

### VisualizationPanel - `panels/visualization_panel.py`

Displays the current `mne.io.Raw` object across four tabs (PSD, Time Series, Sensors, and Topomap) for the pipeline step
selected in the combo box. Tabs render on demand when switched. <br>
The Time Series tab embeds an MNE interactive browser widget. PSD and Topomap results are cached by raw object identity
to skip unnecessary redraws.

---

## Dialogs

### ActionEditor - `dialogs/action_editor.py`

The main parameter-editing dialog. Opens when the user double-clicks an action or a step.

- Dynamically builds a form from the action's params schema.
- Looks up custom widget factories. This allows actions to have custom editors for specific parameters.
- Shows a collapsible "Advanced" section populated by advanced parameters. These are automatically determined
  by introspecting the MNE function's signature at runtime and filtering out kwargs already owned by
  the action's primary parameters.
- A read-only code preview updates in real time as the user changes values.
- When `step_idx` is provided, only the parameters for that specific step are shown.
- MNE documentation links are shown at the bottom.

### AddActionDialog - `dialogs/add_action_dialog.py`

Lists all registered actions with their titles and descriptions. Returns the selected `action_id`.

### MontageDialog - `dialogs/montage_dialog.py`

Interactive channel montage editor used when importing an EEG file.

---

## Code & UI synchronisation

The bidirectional sync works as follows:

```
User edits GUI params
    - ActionEditor.accept()
    - action.params updated
    - MainWindow.update_code()  [internal_update = True]
    - CodePanel.set_code(new_script)  [signal blocked]

User edits code panel
    - debounce timer fires
    - parse_script_to_actions(code)
    - state.actions replaced
    - MainWindow.update_action_list() [sync_code = False]
```

The `internal_update` / `sync_code = False` flags prevent each path from triggering the other.
