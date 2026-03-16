"""Shared mutable state for the EEG pipeline GUI.

AppState is a single dataclass instance owned by MainWindow and passed by reference to every controller.
All GUI controllers read and write the same object, so any update is immediately visible to all other controllers.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

import mne
from gi.repository import GLib

from mnetape.core.data_store import DataStore
from mnetape.core.models import ActionConfig

logger = logging.getLogger(__name__)

# Settings are stored as a JSON file in the user data directory.
def settings_path() -> Path:
    app_dir = Path(GLib.get_user_data_dir()) / "mnetape"
    app_dir.mkdir(parents=True, exist_ok=True)
    return app_dir / "settings.json"

def load_settings() -> dict:
    path = settings_path()
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception as e:
            logger.warning("Failed to load settings from %s: %s", path, e)
    return {}

def save_settings(data: dict) -> None:
    path = settings_path()
    try:
        path.write_text(json.dumps(data, indent=2))
    except Exception as e:
        logger.warning("Failed to save settings to %s: %s", path, e)

class Settings:
    """Simple JSON-backed key/value store, replacing QSettings."""

    def __init__(self):
        self.data: dict = load_settings()

    def value(self, key: str, default=None, value_type=None):
        val = self.data.get(key, default)
        if value_type is not None and val is not None:
            try:
                val = value_type(val)
            except (TypeError, ValueError):
                val = default
        return val

    def set_value(self, key: str, value) -> None:
        self.data[key] = value
        save_settings(self.data)

@dataclass(slots=True)
class AppState:
    """Mutable state shared across all GUI controllers.

    Attributes:
        raw_original: The raw MNE object loaded from disk. Never modified in-place;
            processing results are stored in data_states.
        data_states: Disk-backed store of one entry per completed action.
        actions: Ordered list of pipeline actions configured by the user.
        data_filepath: Absolute path of the currently loaded EEG file, or None.
        pipeline_filepath: Absolute path of the currently open pipeline script, or None.
        settings: Persistent JSON-backed settings for saving preferences across sessions.
        recent_fif: Ordered list of recently opened EEG file paths (most recent first).
    """

    raw_original: mne.io.Raw | None = None
    data_states: DataStore = field(default_factory=DataStore)
    actions: list[ActionConfig] = field(default_factory=list)
    data_filepath: Path | None = None
    pipeline_filepath: Path | None = None
    settings: Settings = field(default_factory=Settings)
    recent_fif: list[str] = field(default_factory=list)

    @classmethod
    def create(cls) -> AppState:
        """Construct an AppState and restore the recent files list from settings.

        Returns:
            A fully initialised AppState instance.
        """
        settings = Settings()
        recent_fif = settings.value("recent_fif", [], list)
        if not isinstance(recent_fif, list):
            recent_fif = []
        state = cls(settings=settings, recent_fif=recent_fif)
        state.data_states.cache_size = int(settings.value("data_store/cache_size", 2))
        state.data_states.max_disk_states = int(settings.value("data_store/max_disk_states", 0))
        return state
