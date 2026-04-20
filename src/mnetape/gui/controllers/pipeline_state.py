"""Shared mutable state for the EEG pipeline GUI.

PipelineState is a single dataclass instance owned by PreprocessingPage and passed by reference to every controller.
All GUI controllers read and write the same object, so any update is immediately visible to all other controllers.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path

import mne
from PyQt6.QtCore import QSettings

from mnetape.core.data_store import DataStore
from mnetape.core.models import ActionConfig

MAX_UNDO = 20


@dataclass(slots=True)
class PipelineState:
    """Mutable state shared across all GUI controllers.

    Attributes:
        raw_original: The raw MNE object loaded from disk. Never modified in-place;
            processing results are stored in data_states.
        data_states: Disk-backed store of one entry per completed action.
            data_states[i] is the result after actions[i]. Checkpoints are serialized
            to FIF files in a temp directory; an LRU cache keeps recent states in RAM.
        actions: Ordered list of pipeline actions configured by the user.
        data_filepath: Absolute path of the currently loaded EEG file, or None.
        pipeline_filepath: Absolute path of the currently open pipeline script, or None.
        settings: Persistent QSettings instance for saving preferences across sessions.
        recent_fif: Ordered list of recently opened EEG file paths (most recent first).
    """

    raw_original: mne.io.Raw | None = None
    data_states: DataStore = field(default_factory=DataStore)
    actions: list[ActionConfig] = field(default_factory=list)
    data_filepath: Path | None = None
    pipeline_filepath: Path | None = None
    settings: QSettings = field(default_factory=QSettings)
    recent_fif: list[str] = field(default_factory=list)
    undo_stack: list = field(default_factory=list, repr=False)
    redo_stack: list = field(default_factory=list, repr=False)
    custom_preamble: list[str] = field(default_factory=list)
    pipeline_dirty: bool = False
    pipeline_modified_this_session: bool = False

    def push_undo(self) -> None:
        """Snapshot the current actions list onto the undo stack and clear redo."""
        self.undo_stack.append(copy.deepcopy(self.actions))
        if len(self.undo_stack) > MAX_UNDO:
            self.undo_stack.pop(0)
        self.redo_stack.clear()

    def pop_undo(self) -> list[ActionConfig] | None:
        """Pop the most recent undo snapshot; push current state to redo."""
        if not self.undo_stack:
            return None
        self.redo_stack.append(copy.deepcopy(self.actions))
        return self.undo_stack.pop()

    def pop_redo(self) -> list[ActionConfig] | None:
        """Pop the most recent redo snapshot; push current state to undo."""
        if not self.redo_stack:
            return None
        self.undo_stack.append(copy.deepcopy(self.actions))
        return self.redo_stack.pop()

    @classmethod
    def create(cls) -> "PipelineState":
        """Construct an PipelineState and restore the recent files list from QSettings.

        Returns:
            A fully initialised PipelineState instance.
        """
        settings = QSettings()
        return cls.create_with_settings(settings)

    @classmethod
    def create_with_settings(cls, settings: QSettings) -> "PipelineState":
        """Construct an PipelineState using the provided QSettings instance.

        Args:
            settings: An already-constructed QSettings object to use.

        Returns:
            A fully initialized PipelineState instance.
        """
        recent_fif = settings.value("recent_fif", [], list)
        if not isinstance(recent_fif, list):
            recent_fif = []
        state = cls(settings=settings, recent_fif=recent_fif)
        state.data_states.cache_size = int(settings.value("data_store/cache_size", 2))
        state.data_states.max_disk_states = int(settings.value("data_store/max_disk_states", 0))
        return state
