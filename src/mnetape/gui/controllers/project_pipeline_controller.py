"""Pipeline management operations for ProjectPage.

Handles setting, using, and viewing the project default pipeline, and stripping participant-specific values before saving.
"""

from __future__ import annotations

import dataclasses
import logging
from typing import TYPE_CHECKING

from PyQt6.QtWidgets import QDialog, QDialogButtonBox, QMessageBox, QVBoxLayout

from mnetape.actions.registry import get_action_by_id
from mnetape.core.codegen import generate_full_script, parse_script_to_actions

if TYPE_CHECKING:
    from mnetape.gui.pages.project_page import ProjectPage

logger = logging.getLogger(__name__)


def strip_managed_params(actions) -> str:
    """Generate pipeline code with participant-specific params cleared.

    Resets managed params to schema defaults and clears load_file's file_path.
    """
    clean = []
    for action in actions:
        if action.action_id == "load_file":
            clean.append(dataclasses.replace(action, params={**action.params, "file_path": ""}))
            continue
        action_def = get_action_by_id(action.action_id)
        ir = action_def.interactive_runner if action_def else None
        if action_def and ir and ir.managed_params:
            clean_params = dict(action.params)
            for param in ir.managed_params:
                clean_params[param] = action_def.params_schema.get(param, {}).get("default")
            clean.append(dataclasses.replace(action, params=clean_params))
        else:
            clean.append(action)
    return generate_full_script(clean)


class ProjectPipelineController:
    """Manages the project default pipeline: set, use, and view."""

    def __init__(self, page: "ProjectPage") -> None:
        self.w = page

    @property
    def project(self):
        return self.w.project

    @property
    def project_dir(self):
        return self.w.project_dir

    @staticmethod
    def normalize_actions_for_default(actions: list) -> list:
        """Return a copy of actions with participant-specific data stripped."""
        result = []
        for action in actions:
            if action.action_id == "load_file":
                continue
            action_def = get_action_by_id(action.action_id)
            managed = (
                action_def.interactive_runner.managed_params
                if action_def and action_def.interactive_runner
                else ()
            )
            if managed:
                defaults = action_def.default_params() if action_def else {}
                stripped = {k: v for k, v in action.params.items() if k not in managed}
                for param in managed:
                    if param in defaults:
                        stripped[param] = defaults[param]
                result.append(dataclasses.replace(action, params=stripped))
            else:
                result.append(action)
        return result

    def set_default_pipeline(self, *, confirm: bool = True):
        """Save the current pipeline as the project default; optionally reset participant overrides."""
        if not self.w.active_prep_page or not self.project or not self.project_dir:
            return
        self.w.active_prep_page.files.save_pipeline_default()
        code = strip_managed_params(self.w.active_prep_page.state.actions)
        if not code:
            return

        if confirm:
            reply = QMessageBox.question(
                self.w.window(),
                "Set as Default Pipeline?",
                "Overwrite the project default pipeline with the current participant's pipeline?\n"
                "Participants using the default will get this pipeline next time they are opened.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        ctx = self.w.active_prep_page.project_context
        current_key = (ctx.participant.id, ctx.session.id) if ctx else None

        current_session = None
        other_custom: list = []
        for p in self.project.participants:
            for s in p.sessions:
                if not s.has_custom_pipeline:
                    continue
                path = self.project.participant_pipeline_path(self.project_dir, p, s)
                if (p.id, s.id) == current_key:
                    current_session = s
                else:
                    other_custom.append((s, path))

        if other_custom:
            session_list = "\n".join(f"  \u2022 {s.id}" for s, _ in other_custom)
            reply = QMessageBox.question(
                self.w.window(),
                "Reset Participant Pipelines?",
                f"The following {len(other_custom)} session(s) have custom pipelines that will be overridden:\n\n"
                f"{session_list}\n\n"
                "Reset them to the new default?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel,
            )
            if reply == QMessageBox.StandardButton.Cancel:
                return
            if reply == QMessageBox.StandardButton.Yes:
                for s, path in other_custom:
                    path.unlink(missing_ok=True)
                    s.has_custom_pipeline = False

        if current_session is not None and ctx is not None:
            self.project.participant_pipeline_path(
                self.project_dir, ctx.participant, ctx.session
            ).unlink(missing_ok=True)
            current_session.has_custom_pipeline = False

        default_path = self.project.pipeline_path(self.project_dir)
        default_path.parent.mkdir(parents=True, exist_ok=True)
        default_path.write_text(code, encoding="utf-8")
        self.project.has_default_pipeline = True
        self.w.save_project()
        self.w.update_pipeline_status_label()
        logger.info("Set default pipeline: %s", default_path)

    def use_default_pipeline(self):
        """Reset this participant's pipeline to the project default."""
        if not self.w.active_prep_page or not self.project or not self.project_dir:
            return
        if not self.project.has_default_pipeline:
            QMessageBox.information(
                self.w.window(), "No Default Pipeline",
                "No default pipeline found for this project."
            )
            return
        default_path = self.project.pipeline_path(self.project_dir)
        try:
            code = default_path.read_text(encoding="utf-8")
            actions = parse_script_to_actions(code)
            data_fp = self.w.active_prep_page.state.data_filepath
            if data_fp and actions and actions[0].action_id == "load_file":
                actions[0].params["file_path"] = str(data_fp)
            self.w.active_prep_page.state.actions = actions
            self.w.active_prep_page.state.data_states.clear()
            self.w.active_prep_page.code_panel.set_code(code)
            self.w.active_prep_page.update_action_list()
            ctx = self.w.active_prep_page.project_context
            if ctx and ctx.session.has_custom_pipeline:
                self.project.participant_pipeline_path(
                    self.project_dir, ctx.participant, ctx.session
                ).unlink(missing_ok=True)
                ctx.session.has_custom_pipeline = False
                self.w.save_project()
                self.w.refresh_participant_item(ctx.participant.id)
        except Exception as e:
            QMessageBox.critical(self.w.window(), "Error", f"Failed to load default pipeline:\n{e}")

    def open_default_pipeline(self):
        """Show the default pipeline script in a read-only code viewer dialog."""
        if not self.project or not self.project_dir or not self.project.has_default_pipeline:
            return
        from mnetape.gui.widgets.code_editor import create_code_editor
        pipeline_path = self.project.pipeline_path(self.project_dir)
        try:
            code = pipeline_path.read_text(encoding="utf-8")
        except OSError:
            return
        dialog = QDialog(self.w)
        dialog.setWindowTitle("Default Pipeline")
        dialog.resize(800, 600)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(8, 8, 8, 8)
        editor = create_code_editor(dialog)
        editor.setReadOnly(True)
        editor.setText(code)
        layout.addWidget(editor)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, dialog)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.exec()
