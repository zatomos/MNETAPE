"""Main application window for the EEG preprocessing pipeline.

MainWindow is the top-level Adw.ApplicationWindow. It owns the shared AppState and instantiates all controller objects
that implement all user-facing operations.
"""

from __future__ import annotations

import logging
from pathlib import Path

import mne
from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from mnetape.actions.registry import get_action_by_id, get_action_title
from mnetape.core.codegen import generate_full_script
from mnetape.core.models import CUSTOM_ACTION_ID, DataType, ICASolution
from mnetape.gui.dialogs.action_result_dialog import ActionResultDialog
from mnetape.gui.dialogs.preferences_dialog import PreferencesDialog
from mnetape.gui.controllers.action_controller import ActionController
from mnetape.gui.controllers.file_handler import FileHandler
from mnetape.gui.controllers.nav_controller import NavController
from mnetape.gui.controllers.pipeline_runner import PipelineRunner
from mnetape.gui.controllers.state import AppState
from mnetape.gui.panels import CodePanel, VisualizationPanel
from mnetape.gui.widgets import ActionListItem

logger = logging.getLogger(__name__)

class MainWindow:
    """Top-level application window.

    Composes the left action-list panel, the right view stack, and a status bar at the bottom.
    All operations are delegated to the controller objects stored as instance attributes.

    Attributes:
        window: The Adw.ApplicationWindow.
        toast_overlay: Adw.ToastOverlay wrapping the main content.
        state: Shared mutable application state.
        files: File I/O controller.
        runner: Pipeline execution controller.
        action_ctrl: Action management controller.
        nav: Navigation controller.
        viz_panel: Visualisation panel.
        code_panel: Code editor panel.
        action_list: Gtk.ListBox for pipeline steps.
    """

    def __init__(self, app: Adw.Application):
        self.tab_bar_box = None
        self.recent_submenu = None
        self.action_list = None
        self.btn_move_up = None
        self.btn_move_down = None
        self.btn_viz = None
        self.btn_code = None
        self.view_stack = None
        self.viz_panel = None
        self.code_panel = None
        self.app = app

        # State
        self.state = AppState.create()
        self.state.data_states.close()
        self.open_dialogs: list = []

        # Controllers
        self.files = FileHandler(self)
        self.runner = PipelineRunner(self)
        self.action_ctrl = ActionController(self)
        self.nav = NavController(self)

        self.state.data_states.thread_runner = self.runner.run_in_thread

        # Build main window
        self.window = Adw.ApplicationWindow(application=app)
        self.window.set_title("MNETAPE")
        self.window.set_default_size(1400, 900)

        # Toast overlay wraps everything
        self.toast_overlay = Adw.ToastOverlay()

        # Outer vertical box: header bar + content
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.toast_overlay.set_child(outer)
        self.window.set_content(self.toast_overlay)

        # Header bar
        self.setup_header(outer)

        # Main content area
        self.setup_ui(outer)

        # Bottom status bar
        self.status_label = Gtk.Label(label="Ready - Open a FIF file to begin")
        self.status_label.set_xalign(0.0)
        self.status_label.set_margin_start(8)
        self.status_label.set_margin_end(8)
        self.status_label.set_margin_top(4)
        self.status_label.set_margin_bottom(4)
        self.status_label.add_css_class("status-bar")

        self.raw_info_label = Gtk.Label(label="")
        self.raw_info_label.add_css_class("dim-label")
        self.raw_info_label.set_xalign(1.0)
        self.raw_info_label.set_hexpand(True)
        self.raw_info_label.set_margin_end(8)

        status_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        status_bar.append(self.status_label)
        status_bar.append(self.raw_info_label)
        outer.append(status_bar)

        # Keyboard shortcuts
        self.setup_shortcuts()

        self.window.connect("close-request", self.on_close_request)

    # -------- Header / menu setup --------

    def setup_header(self, outer: Gtk.Box):
        """Build the Adw.HeaderBar with menu button and action buttons."""
        header = Adw.HeaderBar()
        outer.append(header)

        # Menu button
        menu_btn = Gtk.MenuButton()
        menu_btn.set_icon_name("open-menu-symbolic")
        menu_btn.set_menu_model(self.build_menu())
        header.pack_end(menu_btn)

        # Add Action button
        add_btn = Gtk.Button(label="+ Add Action")
        add_btn.connect("clicked", self.action_ctrl.add_action)
        header.pack_start(add_btn)

        # Run All button
        run_btn = Gtk.Button(label="▶  Run All")
        run_btn.add_css_class("suggested-action")
        run_btn.connect("clicked", lambda _: self.runner.run_all())
        header.pack_start(run_btn)

    def build_menu(self) -> Gio.Menu:
        """Build the application menu model."""
        menu = Gio.Menu()

        # File section
        file_section = Gio.Menu()
        file_section.append("Open EEG File...", "win.open_file")
        self.recent_submenu = Gio.Menu()
        recent_item = Gio.MenuItem.new_submenu("Open Recent", self.recent_submenu)
        file_section.append_item(recent_item)
        file_section.append("Close File", "win.close_file")
        file_section.append("Export Processed...", "win.export_file")
        menu.append_section("File", file_section)

        # Pipeline section
        pipeline_section = Gio.Menu()
        pipeline_section.append("New Pipeline", "win.new_pipeline")
        pipeline_section.append("Save Pipeline...", "win.save_pipeline")
        pipeline_section.append("Load Pipeline...", "win.load_pipeline")
        pipeline_section.append("Run All", "win.run_all")
        menu.append_section("Pipeline", pipeline_section)

        # View section
        view_section = Gio.Menu()
        view_section.append("Show Visualization", "win.show_viz")
        view_section.append("Show Code", "win.show_code")
        menu.append_section("View", view_section)

        # App section
        app_section = Gio.Menu()
        app_section.append("Preferences...", "win.open_prefs")
        menu.append_section("App", app_section)

        return menu

    def register_actions(self):
        """Register Gio.SimpleAction objects on the window."""
        actions = [
            ("open_file", self.files.open_file),
            ("close_file", self.files.close_file),
            ("export_file", lambda *_: self.files.export_file()),
            ("new_pipeline", self.files.new_pipeline),
            ("save_pipeline", self.files.save_pipeline),
            ("load_pipeline", self.files.load_pipeline),
            ("run_all", lambda *_: self.runner.run_all()),
            ("open_browser", self.nav.open_browser),
            ("show_viz", lambda *_: self.set_view_mode("viz")),
            ("show_code", lambda *_: self.set_view_mode("code")),
            ("open_prefs", lambda *_: self.open_preferences()),
        ]
        for name, cb in actions:
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", cb)
            self.window.add_action(action)

    def rebuild_recent_actions(self):
        """Rebuild the recent files menu actions and menu model."""
        # Remove old recent actions
        for i in range(20):
            try:
                self.window.lookup_action(f"open_recent_{i}")
                self.window.remove_action(f"open_recent_{i}")
            except Exception as e:
                logger.debug(f"No existing action open_recent_{i} to remove: {e}")

        # Rebuild menu model
        while self.recent_submenu.get_n_items() > 0:
            self.recent_submenu.remove(0)

        if not self.state.recent_fif:
            self.recent_submenu.append("No recent files", None)
        else:
            for i, path in enumerate(self.state.recent_fif):
                label = Path(path).name
                self.recent_submenu.append(label, f"win.open_recent_{i}")
                action = Gio.SimpleAction.new(f"open_recent_{i}", None)

                def _make_cb(p):
                    return lambda *_: self.files.load_data_path(p)

                action.connect("activate", _make_cb(path))
                self.window.add_action(action)

    # -------- Main UI setup --------

    def setup_ui(self, outer: Gtk.Box):
        """Build the resizable paned layout: action list left, view stack right."""
        paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        paned.set_hexpand(True)
        paned.set_vexpand(True)
        outer.append(paned)

        # ---- Left panel: action list ----
        left_panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        left_panel.set_size_request(240, -1)
        left_panel.set_margin_start(8)
        left_panel.set_margin_end(4)
        left_panel.set_margin_top(8)
        left_panel.set_margin_bottom(8)

        paned.set_start_child(left_panel)
        paned.set_position(290)

        # Scrolled action list
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        self.action_list = Gtk.ListBox()
        self.action_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.action_list.set_activate_on_single_click(False)
        self.action_list.connect("row-selected", self.action_ctrl.on_action_row_selected)
        self.action_list.connect("row-activated", self.action_ctrl.on_action_row_activated)
        scrolled.set_child(self.action_list)
        left_panel.append(scrolled)

        # Move up/down buttons
        move_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)

        self.btn_move_up = Gtk.Button(label="▲")
        self.btn_move_up.set_size_request(40, -1)
        self.btn_move_up.connect("clicked", lambda _: self.action_ctrl.move_action(-1))
        self.btn_move_up.set_sensitive(False)
        move_row.append(self.btn_move_up)

        self.btn_move_down = Gtk.Button(label="▼")
        self.btn_move_down.set_size_request(40, -1)
        self.btn_move_down.connect("clicked", lambda _: self.action_ctrl.move_action(1))
        self.btn_move_down.set_sensitive(False)
        move_row.append(self.btn_move_down)

        left_panel.append(move_row)

        # ---- Right panel: view stack ----
        right_panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        right_panel.set_hexpand(True)
        right_panel.set_vexpand(True)
        paned.set_end_child(right_panel)

        # Toggle row: Visualization / Code
        toggle_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        toggle_row.set_margin_start(8)
        toggle_row.set_margin_top(6)
        toggle_row.set_margin_bottom(4)

        self.tab_bar_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        self.tab_bar_box.add_css_class("linked")
        self.tab_bar_box.set_margin_start(0)
        self.tab_bar_box.set_margin_top(4)
        self.tab_bar_box.set_margin_bottom(4)

        self.btn_viz = Gtk.ToggleButton(label="Visualization")
        self.btn_viz.add_css_class("view-toggle-viz")
        self.btn_viz.set_active(True)
        self.btn_viz.connect("clicked", lambda _: self.set_view_mode("viz"))
        self.tab_bar_box.append(self.btn_viz)

        self.btn_code = Gtk.ToggleButton(label="Code")
        self.btn_code.add_css_class("view-toggle-code")
        self.btn_code.connect("clicked", lambda _: self.set_view_mode("code"))
        self.tab_bar_box.append(self.btn_code)

        toggle_row.append(self.tab_bar_box)

        right_panel.append(toggle_row)

        # View stack
        self.view_stack = Gtk.Stack()
        self.view_stack.set_hexpand(True)
        self.view_stack.set_vexpand(True)
        self.view_stack.set_transition_type(Gtk.StackTransitionType.NONE)
        right_panel.append(self.view_stack)

        # Visualization panel
        self.viz_panel = VisualizationPanel()
        self.view_stack.add_named(self.viz_panel, "viz")

        # Code panel
        self.code_panel = CodePanel()
        self.code_panel.on_external_change = self.files.on_external_code_change
        self.code_panel.on_manual_edit = self.action_ctrl.on_manual_code_edit
        self.view_stack.add_named(self.code_panel, "code")

        # Register window actions and recent files
        self.register_actions()
        self.rebuild_recent_actions()

    def setup_shortcuts(self):
        """Register global keyboard shortcuts."""
        sc = Gtk.ShortcutController()
        sc.set_scope(Gtk.ShortcutScope.GLOBAL)

        # Ctrl+Return: run all
        sc.add_shortcut(
            Gtk.Shortcut(
                trigger=Gtk.ShortcutTrigger.parse_string("<Control>Return"),
                action=Gtk.CallbackAction.new(lambda *_: self.runner.run_all() or True),
            )
        )
        # Ctrl+Shift+Return: run all
        sc.add_shortcut(
            Gtk.Shortcut(
                trigger=Gtk.ShortcutTrigger.parse_string("<Control><Shift>Return"),
                action=Gtk.CallbackAction.new(lambda *_: self.runner.run_all() or True),
            )
        )
        # Ctrl+O: open file
        sc.add_shortcut(
            Gtk.Shortcut(
                trigger=Gtk.ShortcutTrigger.parse_string("<Control>o"),
                action=Gtk.CallbackAction.new(lambda *_: self.files.open_file() or True),
            )
        )
        # Ctrl+N: new pipeline
        sc.add_shortcut(
            Gtk.Shortcut(
                trigger=Gtk.ShortcutTrigger.parse_string("<Control>n"),
                action=Gtk.CallbackAction.new(lambda *_: self.files.new_pipeline() or True),
            )
        )
        # Ctrl+S: save pipeline
        sc.add_shortcut(
            Gtk.Shortcut(
                trigger=Gtk.ShortcutTrigger.parse_string("<Control>s"),
                action=Gtk.CallbackAction.new(lambda *_: self.files.save_pipeline() or True),
            )
        )
        # Ctrl+B: open browser
        sc.add_shortcut(
            Gtk.Shortcut(
                trigger=Gtk.ShortcutTrigger.parse_string("<Control>b"),
                action=Gtk.CallbackAction.new(lambda *_: self.nav.open_browser() or True),
            )
        )
        self.window.add_controller(sc)

    # -------- Toggle between code/viz --------

    def set_view_mode(self, mode: str):
        """Switch the right panel between visualization and code editor."""
        if mode == "viz":
            self.view_stack.set_visible_child_name("viz")
            self.btn_viz.set_active(True)
            self.btn_code.set_active(False)
        else:
            self.view_stack.set_visible_child_name("code")
            self.btn_code.set_active(True)
            self.btn_viz.set_active(False)
            self.update_code()

    # -------- UI update helpers --------

    def set_status(self, message: str):
        """Update the status bar text."""
        self.status_label.set_text(message)

    def update_action_list(self, sync_code: bool = True):
        """Rebuild the action list widget and synchronize dependent UI elements."""
        # Remove all existing rows
        while True:
            row = self.action_list.get_row_at_index(0)
            if row is None:
                break
            self.action_list.remove(row)

        pipeline_type = DataType.RAW

        def add_header(dt: DataType):
            row = Gtk.ListBoxRow()
            row.set_selectable(False)
            lbl = Gtk.Label(label=f"── {dt.label} ──")
            lbl.set_xalign(0.5)
            lbl.add_css_class("type-header")
            lbl.set_margin_top(4)
            lbl.set_margin_bottom(4)
            row.set_child(lbl)
            setattr(row, "_action_row", -1)
            self.action_list.append(row)

        if self.state.actions:
            add_header(DataType.RAW)

        for i, action in enumerate(self.state.actions):
            action_def = get_action_by_id(action.action_id)
            input_type = action_def.input_type if action_def else DataType.RAW
            output_type = action_def.output_type if action_def else DataType.RAW
            is_mismatch = input_type != DataType.ANY and input_type != pipeline_type

            item_widget = ActionListItem(
                i + 1,
                action,
                type_mismatch=is_mismatch,
                run_clicked_cb=self.runner.run_action_at,
            )

            row = Gtk.ListBoxRow()
            setattr(row, "_action_row", i)
            row.set_child(item_widget)

            # Right-click context menu
            gesture = Gtk.GestureClick()
            gesture.set_button(3)  # right button
            _i = i
            _row = row
            gesture.connect(
                "pressed",
                lambda g, n, x, y, r=_row, ri=_i: (
                    g.set_state(Gtk.EventSequenceState.CLAIMED),
                    self.action_ctrl.show_action_context_menu(ri, x, y, r),
                ),
            )
            row.add_controller(gesture)

            # Drag-and-drop reordering
            drag_source = Gtk.DragSource.new()
            drag_source.set_actions(Gdk.DragAction.MOVE)
            drag_source.connect(
                "prepare",
                lambda src, x, y, ri=i: Gdk.ContentProvider.new_for_value(GLib.Variant("i", ri)),
            )
            drag_source.connect(
                "drag-begin",
                lambda src, drag, w=item_widget: src.set_icon(Gtk.WidgetPaintable.new(w), 0, 0),
            )
            row.add_controller(drag_source)

            drop_target = Gtk.DropTarget.new(GLib.Variant, Gdk.DragAction.MOVE)
            drop_target.connect(
                "drop",
                lambda target, value, x, y, dest=i: (
                    self.action_ctrl.move_action_from_to(value.get_int32(), dest)
                    if isinstance(value, GLib.Variant) and value.get_int32() != dest
                    else None
                ) or True,
            )
            row.add_controller(drop_target)

            self.action_list.append(row)

            if not is_mismatch:
                new_type = output_type
                if new_type != DataType.ANY and new_type != pipeline_type:
                    pipeline_type = new_type
                    add_header(pipeline_type)

        if sync_code:
            self.update_code()
        self.update_button_states()

    def get_selected_action_row(self) -> int:
        """Return the action index of the currently selected list item, or -1."""
        row = self.action_list.get_selected_row()
        if row is None:
            return -1
        idx = getattr(row, "_action_row", -1)
        return idx if isinstance(idx, int) and idx >= 0 else -1

    def set_selected_action_row(self, action_row: int):
        """Select the list item corresponding to action_row."""
        i = 0
        while True:
            row = self.action_list.get_row_at_index(i)
            if row is None:
                break
            if getattr(row, "_action_row", -1) == action_row:
                self.action_list.select_row(row)
                return
            i += 1

    def update_button_states(self):
        """Enable or disable the move-up and move-down buttons based on selection."""
        row = self.get_selected_action_row()
        has_selection = row >= 0
        self.btn_move_up.set_sensitive(has_selection and row > 0)
        self.btn_move_down.set_sensitive(has_selection and row < len(self.state.actions) - 1)

    def update_code(self):
        """Regenerate the full pipeline script and push it to the code panel."""
        code = generate_full_script(self.state.data_filepath, self.state.actions)
        self.code_panel.set_code(code)
        self.files.auto_save()

    def update_visualization(self, row: int = -1):
        """Refresh the visualization panel.

        If row >= 0, show the data stored after that action (or raw_original if not yet
        computed). If row == -1 (default), show the latest computed data.
        """
        if row >= 0:
            stored = self.state.data_states[row] if row < len(self.state.data_states) else None
            if stored is None:
                data_to_show = self.state.raw_original
            elif isinstance(stored, ICASolution):
                data_to_show = stored.raw
            else:
                data_to_show = stored
        else:
            data_to_show = self.state.raw_original
            for i in range(len(self.state.data_states) - 1, -1, -1):
                candidate = self.state.data_states[i]
                if candidate is not None:
                    data_to_show = candidate.raw if isinstance(candidate, ICASolution) else candidate
                    break

        self.viz_panel.update_plots(data_to_show)
        self.update_raw_info(data_to_show)

    def update_raw_info(self, data):
        """Update the raw info label in the status bar."""
        if data is None:
            self.raw_info_label.set_text("")
            return
        name = self.state.data_filepath.name if self.state.data_filepath else ""
        n_ch = len(data.ch_names)
        sfreq = data.info["sfreq"]
        if isinstance(data, mne.Epochs):
            n_epochs = len(data)
            self.raw_info_label.set_text(f"{name}  ·  {n_ch} ch  ·  {sfreq:.0f} Hz  ·  {n_epochs} epochs")
        elif isinstance(data, mne.Evoked):
            n_ave = getattr(data, "nave", 0)
            dur = data.times[-1] - data.times[0] if len(data.times) else 0.0
            self.raw_info_label.set_text(
                f"{name}  ·  {n_ch} ch  ·  {sfreq:.0f} Hz  ·  {dur:.3f} s  ·  nave={n_ave}"
            )
        else:
            dur = data.times[-1]
            self.raw_info_label.set_text(f"{name}  ·  {n_ch} ch  ·  {sfreq:.0f} Hz  ·  {dur:.1f} s")

    # -------- Code generation and execution --------

    def get_execution_code(self, index: int, action) -> tuple[str, str]:
        """Return (call_site, func_defs) for executing a single action."""
        if action.action_id == CUSTOM_ACTION_ID:
            return action.custom_code or "", ""

        action_def = get_action_by_id(action.action_id)
        if not action_def:
            return action.custom_code or "", ""

        context_type = self.runner.get_data_type_at(index)

        if action.is_custom and action.custom_code:
            func_defs = action_def.build_function_def_with_body(
                action.action_id, action.custom_code, context_type
            )
            params = {**action_def.default_params(), **action.params}
            adv = action.advanced_params or None
            call_site = action_def.build_call_site(action.action_id, params, adv, context_type)
            return call_site, func_defs

        params = {**action_def.default_params(), **action.params}
        adv = action.advanced_params or None
        func_defs = action_def.build_function_def(action.action_id, context_type)
        call_site = action_def.build_call_site(action.action_id, params, adv, context_type)
        return call_site, func_defs

    def open_action_results(self, row: int):
        """Open the ActionResultDialog for the action at row."""
        if row < 0 or row >= len(self.state.actions):
            return
        action = self.state.actions[row]
        if action.result is None:
            return
        self.show_action_result(action.result, get_action_title(action))

    def show_action_result(self, result, title: str):
        """Open the ActionResultDialog for a given result and title."""
        dlg = ActionResultDialog(result, title, parent_window=self.window)
        self.open_dialogs.append(dlg)
        dlg.show()

    def open_preferences(self):
        """Open the preferences' dialog."""
        dlg = PreferencesDialog(self.state, parent_window=self.window)
        dlg.exec()

    def show(self):
        """Present the main window."""
        self.window.present()

    def on_close_request(self, _window) -> bool:
        self.state.data_states.close()
        self.app.quit()
        return False
