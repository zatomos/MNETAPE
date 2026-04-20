# GUI

The `gui` package contains the full Qt6 user interface built with PyQt6.

## Top-level layout

```text
gui/
├── app.py                     # QApplication entry point; sets up Fusion style, QSS, matplotlib backend, app icon
├── main_window.py             # QMainWindow shell, hosts the pages on a QStackedWidget
├── utils.py                   # Shared GUI helpers
├── pages/
│   ├── project_page.py        # Project management: participant/session tree, status badges, run selection
│   └── preprocessing_page.py  # Pipeline editor and runner
├── controllers/               # Logic for the different parts of the preprocessing page
├── panels/
├── dialogs/                   # Modal dialogs
├── widgets/                   # Reusable custom widgets
└── assets/                    # Icons, images, stylesheets
```

## Interaction model

`MainWindow` hosts two pages on a `QStackedWidget`:

1. **`ProjectPage`** — shown at startup, manages the participant/session roster.
2. **`PreprocessingPage`** — created fresh each time a session is opened; destroyed when the user goes back.

`PreprocessingPage` owns one `PipelineState` instance and three controllers. Each controller stores `self.w` (the page) and `self.state` (the shared state). Controllers never call each other directly; they mutate state and then call `self.w` helpers (`refresh_action_list`, `update_code_panel`, `update_visualization`) to propagate changes to the UI.

```
PreprocessingPage
│
├── PipelineState      single source of truth for all controllers
│
├── FileHandler        reads/writes EEG files and pipeline scripts
│
├── PipelineRunner     runs actions on a QThread; emits progress signals
│                      back to the page, which updates status icons
│
└── ActionController   adds/removes/reorders actions and reconciles
                       hand-edited code back into the action list
```

`open_browser()` (MNE interactive browser for the current step) lives directly on `PreprocessingPage`.

The **action list** (left panel) and **code panel** (right panel) stay synchronized in both directions: any edit to the code panel is debounced, parsed by `parse_script_to_actions`, and reconciled back into `PipelineState.actions`, then the action list re-renders.

The **visualization panel** updates whenever the current step changes; it reads the corresponding `data_states[i]` entry and renders the appropriate tabs for the data type.

## Standalone mode

**File > Open Single EEG File...** opens the preprocessing page without a project context.
The loaded file is not associated with any participant or session; the pipeline can still be saved/loaded as `.py` scripts.

---

## PipelineState (`controllers/state.py`)

Shared mutable state owned by `PreprocessingPage` and read by all controllers:

| Field               | Type                                           | Purpose                                             |
|---------------------|------------------------------------------------|-----------------------------------------------------|
| `actions`           | `list[ActionConfig]`                           | Ordered pipeline steps                              |
| `data_states`       | `list[Raw \| Epochs \| ICASolution \| None]`   | Per-step computed data (None = not yet run)         |
| `raw_original`      | `Raw \| None`                                  | Unmodified source data, used for pipeline resets    |
| `data_filepath`     | `Path \| None`                                 | Currently loaded EEG file path                      |
| `pipeline_filepath` | `Path \| None`                                 | Currently loaded or saved pipeline script path      |
| `pipeline_dirty`    | `bool`                                         | Unsaved edits exist in project mode                 |
| `custom_preamble`   | `list[str] \| None`                            | Extra imports/setup lines from the script header    |
| `recent_fif`        | `list[str]`                                    | Recently opened EEG files (for the recent menu)     |

---

## Visualization panel tabs

| Data type | Tabs                                                        |
|-----------|-------------------------------------------------------------|
| `Raw`     | PSD, Time Series, Sensors, Topomap                          |
| `Epochs`  | PSD, Epochs Browser, Sensors, Topomap, Image                |
| `Evoked`  | PSD, Time Series, Sensors, Topomap                          |
| `ICA`     | Renders the underlying `.raw` from the `ICASolution` bundle |

The MNE Qt browser (mne-qt-browser) is used for interactive time series and epochs browsing.
Static plots (PSD, Topomap, Sensor layout) use matplotlib rendered in `QtAgg` mode.

---

## Action list widget (`widgets/common.py`)

`ActionListItem` is the custom row widget for each pipeline step:

- Constructor: `(index, action, parent=None, type_mismatch=False)`
- Shows the step index, title, status icon, and a **Run** button.
- `type_mismatch=True` flags actions whose input type does not match the current pipeline data type.

---

## Threading

Long-running operations (file load, pipeline execution) run on a `QThread` managed by `PipelineRunner`.
The UI shows a cancelable progress dialog during execution.

`data_store.py` uses `threading.current_thread() is threading.main_thread()` (not Qt API) for thread-safety checks to avoid Qt dependency in core code.

---