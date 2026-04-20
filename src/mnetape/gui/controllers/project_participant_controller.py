"""Participant and session CRUD and import operations for ProjectPage."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from PyQt6.QtWidgets import (
    QButtonGroup,
    QFileDialog,
    QInputDialog,
    QMessageBox,
)

from mnetape.core.project import Participant, ParticipantStatus, Project, Session

if TYPE_CHECKING:
    from mnetape.gui.pages.project_page import ProjectPage

logger = logging.getLogger(__name__)


class ProjectParticipantController:
    """Manages participant and session CRUD and BIDS/folder import."""

    def __init__(self, page: "ProjectPage") -> None:
        self.w = page

    @property
    def project(self):
        return self.w.project

    @property
    def project_dir(self):
        return self.w.project_dir

    def add_participant(self):
        if not self.project:
            return
        from mnetape.gui.dialogs.add_participant_dialog import AddParticipantDialog
        dlg = AddParticipantDialog(
            existing_ids=[p.id for p in self.project.participants],
            project_dir=self.project_dir,
            parent=self.w.window(),
        )
        if dlg.exec() != AddParticipantDialog.DialogCode.Accepted:
            return
        initial_file = dlg.get_file()
        session = Session(id=dlg.get_session_id(), data_files=[initial_file] if initial_file else [])
        participant = Participant(id=dlg.get_id(), sessions=[session])
        self.project.participants.append(participant)
        self.w.save_project()
        self.w.rebuild_tree()
        last = self.w.participant_tree.topLevelItem(self.w.participant_tree.topLevelItemCount() - 1)
        if last:
            self.w.participant_tree.setCurrentItem(last)

    def add_session_to_selected_participant(self):
        """Add a new session to the currently selected participant."""
        p = self.w.get_selected_participant()
        if not p:
            return
        sid, ok = QInputDialog.getText(self.w.window(), "Add Session", "Session ID:", text="01")
        if not ok or not sid.strip():
            return
        sid = sid.strip()
        if p.get_session(sid):
            QMessageBox.warning(self.w.window(), "Duplicate", f'Session "{sid}" already exists.')
            return
        from mnetape.core.data_io import open_file_dialog_filter
        path, _ = QFileDialog.getOpenFileName(
            self.w.window(), "Select EEG File (optional)", "", open_file_dialog_filter()
        )
        data_files: list[str] = []
        if path:
            if self.project_dir:
                try:
                    data_files = [str(Path(path).relative_to(self.project_dir))]
                except ValueError:
                    data_files = [path]
            else:
                data_files = [path]
        session = Session(id=sid, data_files=data_files)
        p.sessions.append(session)
        self.w.save_project()
        self.w.rebuild_tree()
        self.w.populate_participant_detail(p)

    def add_session_run(self):
        """Append a run file to the current session's data_files list."""
        from mnetape.core.data_io import open_file_dialog_filter
        p, s = self.w.get_selected_session()
        if not p or not s:
            return
        path, _ = QFileDialog.getOpenFileName(
            self.w.window(), "Select EEG Run File", "", open_file_dialog_filter()
        )
        if not path:
            return
        if self.project_dir:
            try:
                file_str = str(Path(path).relative_to(self.project_dir))
            except ValueError:
                file_str = path
        else:
            file_str = path
        if file_str not in s.data_files:
            s.data_files.append(file_str)
            self.w.save_project()
            self.w.populate_session_detail(p, s)

    def remove_session_run(self):
        """Remove the selected run file from the current session's data_files list."""
        p, s = self.w.get_selected_session()
        if not p or not s:
            return
        button_group: QButtonGroup = self.w.session_detail_refs["runs_button_group"]
        row = button_group.checkedId()
        if row < 0 or row >= len(s.data_files):
            return
        filename = Path(s.data_files[row]).name
        reply = QMessageBox.question(
            self.w.window(),
            "Remove Run",
            f'Remove "{filename}" from this session?',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        s.data_files.pop(row)
        if row < len(s.processed_files):
            s.processed_files.pop(row)
        if row < len(s.pipeline_hashes):
            s.pipeline_hashes.pop(row)
        self.w.save_project()
        self.w.populate_session_detail(p, s)

    def on_merge_runs_toggled(self, checked: bool):
        """Handle the merge-runs checkbox toggle; optionally apply to all sessions."""
        p, s = self.w.get_selected_session()
        if not p or not s:
            return
        all_sessions = (
            [(pp, ss) for pp in self.project.participants for ss in pp.sessions]
            if self.project else [(p, s)]
        )
        p_sessions = [(p, ps) for ps in p.sessions]
        if len(all_sessions) > 1:
            action = "Enable" if checked else "Disable"
            reply = QMessageBox.question(
                self.w.window(),
                "Apply to all?",
                f"{action} merge runs for all participants in the project?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.Cancel:
                self.w.session_detail_refs["merge_runs_check"].blockSignals(True)
                self.w.session_detail_refs["merge_runs_check"].setChecked(s.merge_runs)
                self.w.session_detail_refs["merge_runs_check"].blockSignals(False)
                return
            pairs_to_update = all_sessions if reply == QMessageBox.StandardButton.Yes else p_sessions
        else:
            pairs_to_update = p_sessions
        affected_pids = set()
        for participant, session in pairs_to_update:
            session.merge_runs = checked
            session.status = ParticipantStatus.PENDING
            session.error_msg = ""
            session.processed_files = []
            affected_pids.add(participant.id)
        self.w.save_project()
        for pid in affected_pids:
            self.w.refresh_participant_item(pid)
        self.w.populate_session_detail(p, s)

    def on_notes_changed(self):
        p = self.w.get_selected_participant()
        if p:
            p.notes = self.w.participant_detail_refs["notes_edit"].toPlainText()
            self.w.save_project()

    def remove_selected(self):
        item_type, pid, sid = self.w.get_selected_item_data()
        if item_type == "participant":
            self.remove_participant()
        elif item_type == "session" and pid and sid:
            self.remove_session(pid, sid)

    def remove_participant(self):
        p = self.w.get_selected_participant()
        if not p:
            return
        if self.w.active_prep_page and self.w.active_prep_page.project_context:
            if self.w.active_prep_page.project_context.participant.id == p.id:
                QMessageBox.information(
                    self.w.window(), "Participant in use",
                    "Close the preprocessing session before removing this participant."
                )
                return
        reply = QMessageBox.question(
            self.w.window(), "Remove Participant",
            f'Remove participant "{p.id}" from the project?\n\n'
            "This does not delete any files from disk.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        if not self.project:
            return
        self.project.participants.remove(p)
        self.w.save_project()
        self.w.rebuild_tree()
        if self.w.participant_tree.topLevelItemCount() == 0:
            self.w.right_stack.setCurrentWidget(self.w.no_selection_widget)

    def remove_session(self, participant_id: str, session_id: str):
        if not self.project:
            return
        p = self.project.get_participant(participant_id)
        if not p:
            return
        s = p.get_session(session_id)
        if not s:
            return
        reply = QMessageBox.question(
            self.w.window(), "Remove Session",
            f'Remove session "ses-{s.id}" from participant "{p.id}"?\n\n'
            "This does not delete any files from disk.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        p.sessions.remove(s)
        self.w.save_project()
        self.w.rebuild_tree()
        self.w.right_stack.setCurrentWidget(self.w.no_selection_widget)

    def rename_participant(self, pid: str):
        if not self.project:
            return
        p = self.project.get_participant(pid)
        if not p:
            return
        new_id, ok = QInputDialog.getText(
            self.w.window(), "Rename Participant", "Participant ID:", text=p.id
        )
        new_id = new_id.strip()
        if not ok or not new_id or new_id == p.id:
            return
        if any(x.id == new_id for x in self.project.participants if x.id != pid):
            QMessageBox.warning(
                self.w.window(), "Duplicate ID",
                f'A participant with ID "{new_id}" already exists.'
            )
            return
        p.id = new_id
        self.w.save_project()
        self.w.rebuild_tree()

    def rename_session_id(self, pid: str, sid: str):
        if not self.project:
            return
        p = self.project.get_participant(pid)
        if not p:
            return
        s = p.get_session(sid)
        if not s:
            return
        new_id, ok = QInputDialog.getText(
            self.w.window(), "Rename Session", "Session ID:", text=s.id
        )
        new_id = new_id.strip()
        if not ok or not new_id or new_id == s.id:
            return
        if any(x.id == new_id for x in p.sessions if x.id != sid):
            QMessageBox.warning(
                self.w.window(), "Duplicate ID",
                f'A session with ID "{new_id}" already exists.'
            )
            return
        s.id = new_id
        self.w.save_project()
        self.w.rebuild_tree()

    def import_from_folder(self):
        if not self.project:
            return
        folder = QFileDialog.getExistingDirectory(self.w.window(), "Select Folder with EEG Files")
        if not folder:
            return
        folder_path = Path(folder)
        existing_ids = {p.id for p in self.project.participants}
        extensions = (".fif", ".edf", ".bdf", ".set", ".vhdr", ".brainvision")
        files = sorted(f for f in folder_path.iterdir() if f.suffix.lower() in extensions)
        if not files:
            QMessageBox.information(
                self.w.window(), "No Files Found",
                "No recognized EEG files found in the selected folder."
            )
            return
        added = 0
        for f in files:
            pid = f.stem
            if pid in existing_ids:
                continue
            try:
                file_str = str(f.relative_to(self.project_dir)) if self.project_dir else str(f)
            except ValueError:
                file_str = str(f)
            session = Session(id="01", data_files=[file_str])
            self.project.participants.append(Participant(id=pid, sessions=[session]))
            existing_ids.add(pid)
            added += 1
        if added:
            self.w.save_project()
            self.w.rebuild_tree()
        else:
            QMessageBox.information(
                self.w.window(), "No New Participants", "All files already have entries."
            )

    def import_bids(self):
        """Import participants and sessions from a BIDS dataset directory."""
        bids_dir = QFileDialog.getExistingDirectory(self.w.window(), "Select BIDS Dataset Root")
        if not bids_dir:
            return
        bids_path = Path(bids_dir)
        if not self.project:
            if self.w._create_and_load_project() is None:
                return
        assert self.project is not None
        assert self.project_dir is not None
        try:
            bids_project = Project.from_bids(bids_path, self.project_dir)
        except Exception as e:
            QMessageBox.critical(
                self.w.window(), "BIDS Import Error",
                f"Failed to parse BIDS dataset:\n{e}"
            )
            logger.exception("BIDS import failed for %s", bids_path)
            return
        existing_ids = {p.id for p in self.project.participants}
        added = 0
        for p in bids_project.participants:
            if p.id not in existing_ids:
                self.project.participants.append(p)
                existing_ids.add(p.id)
                added += 1
        if added:
            self.w.save_project()
            self.w.rebuild_tree()
        else:
            QMessageBox.information(
                self.w.window(), "No New Participants", "All BIDS subjects already exist."
            )
