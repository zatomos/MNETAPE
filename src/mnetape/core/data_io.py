"""Data import helpers for supported EEG file formats.

Maps file extensions to MNE reader functions and provides a unified entry point.
Supported formats: FIF, EDF, BDF, GDF, BrainVision (.vhdr), EEGLAB (.set), CNT, and EGI (.mff).
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

import mne

READERS: dict[str, tuple[Callable, str]] = {
    ".fif": (mne.io.read_raw_fif, "*.fif"),
    ".fif.gz": (mne.io.read_raw_fif, "*.fif.gz"),
    ".edf": (mne.io.read_raw_edf, "*.edf"),
    ".bdf": (mne.io.read_raw_bdf, "*.bdf"),
    ".gdf": (mne.io.read_raw_gdf, "*.gdf"),
    ".vhdr": (mne.io.read_raw_brainvision, "*.vhdr"),
    ".set": (mne.io.read_raw_eeglab, "*.set"),
    ".cnt": (mne.io.read_raw_cnt, "*.cnt"),
    ".mff": (mne.io.read_raw_egi, "*.mff"),
}


def detect_extension(path: str | Path) -> str:
    """Return the normalized file extension for path.

    Handles the compound .fif.gz extension as a single token instead of returning only .gz.

    Args:
        path: Filesystem path to inspect.

    Returns:
        Lowercase extension string.
    """
    p = Path(path)
    lower_name = p.name.lower()

    # Handle .fif.gz as a special case since it has two suffixes
    if lower_name.endswith(".fif.gz"):
        return ".fif.gz"

    return p.suffix.lower()


def open_file_dialog_filter() -> str:
    """Return a file dialog filter string for supported EEG formats."""

    patterns = sorted({pattern for _, pattern in READERS.values()})
    all_supported = " ".join(patterns)
    return (
        f"EEG Files ({all_supported});;"
        "FIF Files (*.fif *.fif.gz);;"
        "EDF/BDF/GDF Files (*.edf *.bdf *.gdf);;"
        "BrainVision (*.vhdr);;"
        "EEGLAB (*.set);;"
        "CNT (*.cnt);;"
        "EGI (*.mff);;"
        "All Files (*)"
    )


def load_raw_data(path: str | Path, *, preload: bool = True, verbose=False) -> mne.io.Raw:
    """Load EEG data from any supported file format.

    Automatically selects the correct MNE reader based on file extension.

    Args:
        path: Path to the EEG data file.
        preload: When True, data is loaded into memory immediately. Defaults to True.
        verbose: MNE verbosity level passed through to the reader. Defaults to False.

    Returns:
        A loaded MNE Raw object.

    Raises:
        ValueError: When the file extension is not recognized.
    """

    ext = detect_extension(path)
    reader_entry = READERS.get(ext)
    if reader_entry is None:
        supported = ", ".join(sorted(READERS.keys()))
        raise ValueError(f"Unsupported file format: {ext or '<none>'}. Supported: {supported}")
    reader, _ = reader_entry
    return reader(str(path), preload=preload, verbose=verbose)
