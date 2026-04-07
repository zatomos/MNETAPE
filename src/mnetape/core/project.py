"""Project data model for MNETAPE.

A Project groups a set of participants, a shared pipeline script, and a conditions map.
It serializes to/from a project.json file at the root of the project directory.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path


class ParticipantStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    ERROR = "error"
    INCOMPLETE = "incomplete"


STATUS_COLORS: dict[ParticipantStatus, str] = {
    ParticipantStatus.PENDING: "#888888",
    ParticipantStatus.RUNNING: "#3C7EDB",
    ParticipantStatus.DONE: "#2E7D32",
    ParticipantStatus.ERROR: "#C62828",
    ParticipantStatus.INCOMPLETE: "#E65100",
}

STATUS_LABELS: dict[ParticipantStatus, str] = {
    ParticipantStatus.PENDING: "Pending",
    ParticipantStatus.RUNNING: "Running",
    ParticipantStatus.DONE: "Done",
    ParticipantStatus.ERROR: "Error",
    ParticipantStatus.INCOMPLETE: "Incomplete",
}

STATUS_ICONS: dict[ParticipantStatus, str] = {
    ParticipantStatus.PENDING: "◌",
    ParticipantStatus.RUNNING: "◐",
    ParticipantStatus.DONE: "●",
    ParticipantStatus.ERROR: "✕",
    ParticipantStatus.INCOMPLETE: "◑",
}


@dataclass
class Session:
    """A single recording session for a participant.

    Attributes:
        id: Short session identifier.
        data_files: Ordered list of run file paths.
            Multiple files are concatenated at load time. Empty list = no file assigned.
        status: One of the ParticipantStatus string values.
        error_msg: Last pipeline error message, if any.
    """

    id: str
    data_files: list[str] = field(default_factory=list)
    status: ParticipantStatus = ParticipantStatus.PENDING
    error_msg: str = ""
    merge_runs: bool = False
    processed_files: list[str] = field(default_factory=list)

    @property
    def session_status(self) -> ParticipantStatus:
        """Derive the effective status from processed_files, trusting stored status only for ERROR/RUNNING.

        DONE/INCOMPLETE/PENDING are always computed from the actual processed run files so that
        re-opening and closing a session without running anything cannot overwrite prior progress.
        """
        if self.status in (ParticipantStatus.ERROR, ParticipantStatus.RUNNING):
            return self.status
        if not self.data_files:
            return ParticipantStatus.PENDING
        if self.merge_runs:
            return ParticipantStatus.DONE if any(self.processed_files) else ParticipantStatus.PENDING
        done_count = sum(1 for pf in self.processed_files if pf)
        if done_count == 0:
            return ParticipantStatus.PENDING
        return ParticipantStatus.DONE if done_count >= len(self.data_files) else ParticipantStatus.INCOMPLETE

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "data_files": self.data_files,
            "status": self.status,
            "error_msg": self.error_msg,
            "merge_runs": self.merge_runs,
            "processed_files": self.processed_files,
        }

    @classmethod
    def from_dict(cls, d: dict) -> Session:
        raw_status = d.get("status", ParticipantStatus.PENDING)
        try:
            status = ParticipantStatus(raw_status)
        except ValueError:
            status = ParticipantStatus.PENDING
        return cls(
            id=d.get("id", "01"),
            data_files=d.get("data_files") or [],
            status=status,
            error_msg=d.get("error_msg", ""),
            merge_runs=d.get("merge_runs", False),
            processed_files=d.get("processed_files") or [],
        )


@dataclass
class Participant:
    """A single study participant with one or more recording sessions.

    Attributes:
        id: Short identifier used as folder name.
        sessions: Ordered list of recording sessions.
        notes: Free-text notes for this participant.
        excluded: Whether this participant is excluded from analysis.
        exclusion_reason: Reason for exclusion.
    """

    id: str
    sessions: list[Session] = field(default_factory=list)
    notes: str = ""
    excluded: bool = False
    exclusion_reason: str = ""

    @property
    def participant_status(self) -> ParticipantStatus:
        """Aggregate status across sessions.

        DONE if all sessions done, ERROR if any error, RUNNING if any running,
        PENDING otherwise.
        """
        if not self.sessions:
            return ParticipantStatus.PENDING
        statuses = [s.session_status for s in self.sessions]
        if any(s == ParticipantStatus.ERROR for s in statuses):
            return ParticipantStatus.ERROR
        if any(s == ParticipantStatus.RUNNING for s in statuses):
            return ParticipantStatus.RUNNING
        if all(s == ParticipantStatus.DONE for s in statuses):
            return ParticipantStatus.DONE
        if any(s in (ParticipantStatus.DONE, ParticipantStatus.INCOMPLETE) for s in statuses):
            return ParticipantStatus.INCOMPLETE
        return ParticipantStatus.PENDING

    def get_session(self, session_id: str) -> Session | None:
        """Return the session with the given id, or None."""
        for s in self.sessions:
            if s.id == session_id:
                return s
        return None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "sessions": [s.to_dict() for s in self.sessions],
            "notes": self.notes,
            "excluded": self.excluded,
            "exclusion_reason": self.exclusion_reason,
        }

    @classmethod
    def from_dict(cls, d: dict) -> Participant:
        return cls(
            id=d.get("id", ""),
            sessions=[Session.from_dict(s) for s in d.get("sessions", [])],
            notes=d.get("notes", ""),
            excluded=d.get("excluded", False),
            exclusion_reason=d.get("exclusion_reason", ""),
        )


@dataclass
class ProjectContext:
    """Carries project information into a preprocessing MainWindow.

    Attributes:
        project: The parent Project object.
        project_dir: Absolute path to the project folder.
        participant: The participant being preprocessed.
        session: The specific session being preprocessed.
        data_files: Resolved absolute paths to load. Set by the run-selection dialog.
            either a single run or the full list for concatenation. Empty list = no file.
        on_status_update: Callback invoked when the preprocessing window closes,
            receiving the new status string ("pending", "done", "error").
    """

    project: Project
    project_dir: Path
    participant: Participant
    session: Session
    on_status_update: object  # Callable[[str], None]
    data_files: list[Path] = field(default_factory=list)
    run_index: int | None = None


@dataclass
class Project:
    """A study project grouping participants, conditions, and a shared pipeline.

    The project serializes to ``project.json`` inside the project directory.

    Attributes:
        name: Human-readable project name.
        participants: Ordered list of study participants.
        conditions: Mapping from condition id to human-readable label.
        pipeline_file: Filename of the shared pipeline script (relative to project dir).
        created_at: ISO-format creation timestamp.
        version: Schema version for future migrations.
    """

    name: str
    participants: list[Participant] = field(default_factory=list)
    conditions: dict[str, str] = field(default_factory=dict)
    pipeline_file: str = "pipeline.py"
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    version: str = "2"

    # -------- Path helpers --------

    def pipeline_path(self, project_dir: Path) -> Path:
        """Absolute path to the shared pipeline script."""
        return project_dir / self.pipeline_file

    def participant_pipeline_path(self, project_dir: Path, participant: "Participant", session: "Session") -> Path:
        """Absolute path to the participant/session-specific pipeline override.

        If this file exists it takes precedence over the project default pipeline.
        """
        return self.session_dir(project_dir, participant, session) / "pipeline.py"

    def participant_dir(self, project_dir: Path, participant: Participant) -> Path:
        """Absolute path to the participant's output folder."""
        return project_dir / "participants" / participant.id

    def session_dir(self, project_dir: Path, participant: Participant, session: Session) -> Path:
        """Absolute path to the participant/session output folder."""
        return self.participant_dir(project_dir, participant) / f"ses-{session.id}"

    def preprocessed_file(
        self, project_dir: Path, participant: Participant, session: Session
    ) -> Path:
        """Absolute path where preprocessed raw data will be saved (merged run)."""
        return self.session_dir(project_dir, participant, session) / "preprocessed.fif"

    def epochs_file(
        self, project_dir: Path, participant: Participant, session: Session
    ) -> Path:
        """Absolute path where epoch data will be saved (merged run)."""
        return self.session_dir(project_dir, participant, session) / "epochs.fif"

    def resolve_data_files(self, project_dir: Path, session: Session
    ) -> list[Path]:
        """Resolve session.data_files to a list of absolute Paths."""
        result = []
        for f in session.data_files:
            p = Path(f)
            result.append(p if p.is_absolute() else project_dir / p)
        return result

    def session_output_file(
        self,
        project_dir: Path,
        participant: Participant,
        session: Session,
        file_type: str,
        run_index: int | None = None,
    ) -> Path:
        """Absolute path for a processed output file.

        Args:
            project_dir: The project directory.
            participant: The participant.
            session: The session.
            file_type: ``"preprocessed"``, ``"epochs"``, or ``"evoked"``.
            run_index: Run index when ``merge_runs=False``; ``None`` for merged output.

        File names follow MNE naming conventions:
            - raw  → ``{prefix}preprocessed_raw.fif``
            - epochs → ``{prefix}epochs_epo.fif``
            - evoked → ``{prefix}evoked_ave.fif``
        """
        base = self.session_dir(project_dir, participant, session)
        run_part = f"_run-{run_index:02d}" if run_index is not None else ""
        suffix_map = {"epochs": "_epo.fif", "evoked": "_ave.fif"}
        suffix = suffix_map.get(file_type, "_raw.fif")
        stem = f"{participant.id}_ses-{session.id}{run_part}"
        return base / f"{stem}{suffix}"

    # -------- Participant lookup --------

    def get_participant(self, participant_id: str) -> Participant | None:
        """Return the participant with the given id, or None."""
        for p in self.participants:
            if p.id == participant_id:
                return p
        return None

    # -------- BIDS import --------

    @classmethod
    def from_bids(cls, bids_dir: Path, project_dir: Path) -> Project:
        """Create a Project by scanning a BIDS dataset directory.

        Discovers subject directories (sub-*) and their session directories (ses-*).
        All EEG files within a session are collected as runs (sorted alphabetically,
        which preserves BIDS run-NN ordering). Sessions with no EEG files are still
        included with an empty data_files list.

        Args:
            bids_dir: Root of the BIDS dataset (contains sub-* folders).
            project_dir: The MNETAPE project directory (used for relative path computation).

        Returns:
            A new Project instance (not yet saved to disk).
        """
        name = bids_dir.name
        participants: list[Participant] = []
        eeg_extensions = {".fif", ".edf", ".bdf", ".set", ".vhdr", ".brainvision"}

        def _collect_runs(search_dir: Path) -> list[str]:
            """Return sorted list of EEG file paths (relative to project_dir when possible)."""
            files = sorted(f for f in search_dir.rglob("*") if f.suffix.lower() in eeg_extensions)
            result = []
            for f in files:
                try:
                    result.append(str(f.relative_to(project_dir)))
                except ValueError:
                    result.append(str(f))
            return result

        sub_dirs = sorted(d for d in bids_dir.iterdir() if d.is_dir() and d.name.startswith("sub-"))
        for sub_dir in sub_dirs:
            sessions: list[Session] = []
            ses_dirs = sorted(d for d in sub_dir.iterdir() if d.is_dir() and d.name.startswith("ses-"))
            if ses_dirs:
                for ses_dir in ses_dirs:
                    sessions.append(Session(id=ses_dir.name[4:], data_files=_collect_runs(ses_dir)))
            else:
                sessions.append(Session(id="01", data_files=_collect_runs(sub_dir)))
            participants.append(Participant(id=sub_dir.name, sessions=sessions))

        return cls(name=name, participants=participants)

    # -------- Serialization --------

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "name": self.name,
            "conditions": self.conditions,
            "pipeline_file": self.pipeline_file,
            "created_at": self.created_at,
            "participants": [p.to_dict() for p in self.participants],
        }

    def save(self, project_dir: Path):
        """Write project.json to project_dir, creating the directory if needed."""
        project_dir.mkdir(parents=True, exist_ok=True)
        path = project_dir / "project.json"
        path.write_text(json.dumps(self.to_dict(), indent=2))

    @classmethod
    def load(cls, project_dir: Path) -> Project:
        """Load and parse project.json from project_dir.

        Args:
            project_dir: Directory containing project.json.

        Returns:
            A fully constructed Project instance.

        Raises:
            FileNotFoundError: If project.json does not exist.
            json.JSONDecodeError: If the file is malformed.
        """
        path = project_dir / "project.json"
        d = json.loads(path.read_text())
        return cls(
            name=d.get("name", ""),
            participants=[Participant.from_dict(p) for p in d.get("participants", [])],
            conditions=d.get("conditions", {}),
            pipeline_file=d.get("pipeline_file", "pipeline.py"),
            created_at=d.get("created_at", ""),
            version=d.get("version", "1"),
        )
