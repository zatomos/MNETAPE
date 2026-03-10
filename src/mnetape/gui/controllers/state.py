"""Shared mutable state for the EEG pipeline GUI.

AppState is a single dataclass instance owned by MainWindow and passed by reference to every controller.
All GUI controllers read and write the same object, so any update is immediately visible to all other controllers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import mne
from PyQt6.QtCore import QSettings

from mnetape.core.data_store import DataStore
from mnetape.core.models import ActionConfig


@dataclass(slots=True)
class AppState:
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

    @classmethod
    def create(cls) -> AppState:
        """Construct an AppState and restore the recent files list from QSettings.

        Returns:
            A fully initialised AppState instance.
        """
        settings = QSettings()
        recent_fif = settings.value("recent_fif", [], list)
        if not isinstance(recent_fif, list):
            recent_fif = []
        state = cls(settings=settings, recent_fif=recent_fif)
        state.data_states.cache_size = int(settings.value("data_store/cache_size", 2))
        state.data_states.max_disk_states = int(settings.value("data_store/max_disk_states", 0))
        return state
