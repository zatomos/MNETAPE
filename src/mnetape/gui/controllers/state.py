"""Shared mutable state for the EEG pipeline GUI.

AppState is a single dataclass instance owned by MainWindow and passed by reference to every controller.
All GUI controllers read and write the same object, so any update is immediately visible to all other controllers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import mne
from PyQt6.QtCore import QSettings

from mnetape.core.models import ActionConfig


@dataclass(slots=True)
class AppState:
    """Mutable state shared across all GUI controllers.

    Attributes:
        raw_original: The raw MNE object loaded from disk. Never modified in-place;
            processing results are stored in raw_states.
        raw_states: One entry per completed action, each a processed copy of raw.
            raw_states[i] is the result after actions[i].
        actions: Ordered list of pipeline actions configured by the user.
        data_filepath: Absolute path of the currently loaded EEG file, or None.
        pipeline_filepath: Absolute path of the currently open pipeline script, or None.
        settings: Persistent QSettings instance for saving preferences across sessions.
        recent_fif: Ordered list of recently opened EEG file paths (most recent first).
    """

    raw_original: mne.io.Raw | None = None
    raw_states: list[mne.io.Raw] = field(default_factory=list)
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
        return cls(settings=settings, recent_fif=recent_fif)
