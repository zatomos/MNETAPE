"""Disk store for MNE pipeline data states.

Each checkpoint is serialized to a FIF file in a per-session temp directory immediately after an action completes.
An LRU cache keeps recently accessed states in RAM to avoid redundant disk I/O during navigation
and sequential execution.
"""

from __future__ import annotations

import json
import logging
import shutil
import tempfile
from collections import OrderedDict
from pathlib import Path
from typing import Any, Callable

import threading

import numpy as np
import mne
from mnetape.core.models import ICASolution

logger = logging.getLogger(__name__)

MAIN_THREAD = threading.main_thread()

# -------- Serialization helpers --------

RAW = "raw"
EPOCHS = "epochs"
EVOKED = "evoked"
ICA = "ica"

class NumpyEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        return super().default(obj)

def p(base: Path, suffix: str) -> Path:
    """Construct a slot file path by appending a suffix to the base name."""
    return base.parent / (base.name + suffix)

def write_to_disk(data: Any, base: Path) -> str:
    """Serialize a pipeline data object to disk under paths derived from base.

    Returns the type tag string for the slot metadata.
    """

    logger.info("Writing checkpoint to disk at %s (type: %s)", base, type(data).__name__)

    if isinstance(data, ICASolution):
        data.ica.save(p(base, "_ica.fif"), overwrite=True)
        data.raw.save(p(base, "_ica_raw.fif"), overwrite=True)
        if data.ic_labels is not None:
            p(base, "_labels.json").write_text(json.dumps(data.ic_labels, cls=NumpyEncoder))
        return ICA

    if isinstance(data, mne.io.BaseRaw):
        data.save(p(base, "_raw.fif"), overwrite=True)
        return RAW

    if isinstance(data, mne.BaseEpochs):
        data.save(p(base, "_epo.fif"), overwrite=True)
        return EPOCHS

    if isinstance(data, mne.Evoked):
        data.save(p(base, "_ave.fif"), overwrite=True)
        return EVOKED

    raise TypeError(f"Unsupported data type for DataStore serialization: {type(data)}")

def read_from_disk(type_tag: str, base: Path) -> Any:
    """Deserialize a pipeline data object from disk."""

    logger.info("Reading checkpoint from disk at %s (type tag: %s)", base, type_tag)

    if type_tag == RAW:
        return mne.io.read_raw_fif(p(base, "_raw.fif"), preload=True, verbose=False)

    if type_tag == EPOCHS:
        return mne.read_epochs(p(base, "_epo.fif"), preload=True, verbose=False)

    if type_tag == EVOKED:
        return mne.read_evokeds(p(base, "_ave.fif"), verbose=False)[0]

    if type_tag == ICA:
        ica = mne.preprocessing.read_ica(p(base, "_ica.fif"), verbose=False)
        raw = mne.io.read_raw_fif(p(base, "_ica_raw.fif"), preload=True, verbose=False)
        ic_labels = None
        labels_path = p(base, "_labels.json")
        if labels_path.exists():
            ic_labels = json.loads(labels_path.read_text())
        return ICASolution(ica=ica, raw=raw, ic_labels=ic_labels)

    raise ValueError(f"Unknown DataStore type tag: {type_tag!r}")

def delete_slot_files(base: Path) -> None:
    """Remove all files belonging to a checkpoint slot (handles FIF split files too)."""
    try:
        for path in base.parent.glob(f"{base.name}*"):
            path.unlink(missing_ok=True)
            logger.info("Deleted checkpoint file: %s", path)
    except Exception as e:
        logger.debug("Failed to delete checkpoint files for %s: %s", base, e)

def is_main_thread() -> bool:
    """Return True when called from the main (GUI) thread."""
    return threading.current_thread() is MAIN_THREAD

# -------- DataStore --------

class DataStore:
    """Disk-backed sequential store for pipeline data states.

    Attributes:
        cache_size: Number of data objects held in RAM simultaneously.
        max_disk_states: Maximum number of checkpoints kept on disk (0 = unlimited).
            When exceeded, the oldest on-disk checkpoint is deleted.
            The slot index is preserved as None so the pipeline structure stays intact.
        thread_runner: Optional callable. When set, disk reads triggered from the GUI main thread are wrapped
        in a background thread with a progress dialog. Worker-thread reads bypass this.
    """

    def __init__(self, cache_size: int = 2):
        self.tmpdir: Path | None = None
        # Each slot: None (no file) or (type_tag, base_path)
        self.slots: list[tuple[str, Path] | None] = []
        self.cache: OrderedDict[int, Any] = OrderedDict()
        self.cache_size = cache_size
        self.max_disk_states: int = 0
        self.thread_runner: Callable | None = None
        self.counter = 0

    # -------- Internal --------

    def ensure_tmpdir(self) -> Path:
        if self.tmpdir is None:
            self.tmpdir = Path(tempfile.mkdtemp(prefix="mnetape_states_"))
            logger.debug("DataStore: temp dir created at %s", self.tmpdir)
        return self.tmpdir

    def cache_put(self, index: int, data: Any) -> None:
        if index in self.cache:
            self.cache[index] = data
            self.cache.move_to_end(index)
        else:
            if len(self.cache) >= self.cache_size:
                self.cache.popitem(last=False)  # pop least recently used
            self.cache[index] = data

    def evict_oldest_disk_slot(self) -> None:
        """Delete the lowest-indexed on-disk slot when max_disk_states is exceeded."""
        if self.max_disk_states == 0:
            return
        on_disk = [i for i, s in enumerate(self.slots) if s is not None]
        while len(on_disk) > self.max_disk_states:
            i = on_disk.pop(0)
            slot = self.slots[i]
            if slot is not None:
                delete_slot_files(slot[1])
            self.slots[i] = None
            self.cache.pop(i, None)
            logger.debug("DataStore: evicted slot %d (max_disk_states=%d)", i, self.max_disk_states)

    def write_slot(self, index: int, data: Any) -> None:
        old = self.slots[index]

        if data is None:
            if old is not None:
                delete_slot_files(old[1])
            self.slots[index] = None
            self.cache.pop(index, None)
            return

        tmpdir = self.ensure_tmpdir()
        self.counter += 1
        base = tmpdir / f"slot_{index:04d}_{self.counter:06d}"

        try:
            type_tag = write_to_disk(data, base)
            if old is not None:
                delete_slot_files(old[1])
            self.slots[index] = (type_tag, base)
            self.cache_put(index, data)
            self.evict_oldest_disk_slot()
        except Exception as e:
            logger.error(
                "DataStore: failed to serialize checkpoint at index %d: %s",
                index, e, exc_info=True,
            )
            # On failure, leave the old slot intact if it exists, otherwise clear the slot and cache entry.
            if old is not None:
                delete_slot_files(old[1])
            self.slots[index] = None
            self.cache_put(index, data)

    # -------- List-compatible interface --------

    def __len__(self) -> int:
        return len(self.slots)

    def __bool__(self) -> bool:
        return bool(self.slots)

    def __getitem__(self, index: int) -> Any:
        n = len(self.slots)
        if index < 0:
            index = n + index
        if index < 0 or index >= n:
            raise IndexError(f"DataStore index {index} out of range (len={n})")

        slot = self.slots[index]
        if slot is None:
            # Slot may still be in cache if the disk write failed but data was kept in RAM
            if index in self.cache:
                self.cache.move_to_end(index)
                return self.cache[index]
            return None

        if index in self.cache:
            self.cache.move_to_end(index)
            return self.cache[index]

        # Cache miss: load from disk
        type_tag, base = slot
        try:
            if self.thread_runner is not None and is_main_thread():
                data = self.thread_runner(
                    lambda t=type_tag, b=base: read_from_disk(t, b),
                    "Loading checkpoint...",
                )
            else:
                data = read_from_disk(type_tag, base)
            self.cache_put(index, data)
            return data
        except Exception as e:
            logger.error(
                "DataStore: failed to load checkpoint at index %d from %s: %s",
                index, base, e, exc_info=True,
            )
            return None

    def __setitem__(self, index: int, data: Any) -> None:
        n = len(self.slots)
        if index < 0:
            index = n + index
        if index < 0 or index >= n:
            raise IndexError(f"DataStore index {index} out of range (len={n})")
        self.write_slot(index, data)

    def append(self, data: Any) -> None:
        index = len(self.slots)
        self.slots.append(None)  # reserve the slot
        self.write_slot(index, data)

    # -------- Management --------

    def truncate(self, n: int) -> None:
        """Drop all slots from index n onward, deleting their disk files."""
        for i in range(n, len(self.slots)):
            slot = self.slots[i]
            if slot is not None:
                delete_slot_files(slot[1])
            self.cache.pop(i, None)
        self.slots = self.slots[:n]

    def clear(self) -> None:
        """Remove all checkpoints and delete the temporary directory."""
        self.truncate(0)
        if self.tmpdir is not None:
            shutil.rmtree(self.tmpdir, ignore_errors=True)
            logger.debug("DataStore: temp dir removed at %s", self.tmpdir)
            self.tmpdir = None

    def close(self) -> None:
        """Release all resources."""
        self.clear()
