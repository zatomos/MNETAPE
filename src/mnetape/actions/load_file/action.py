"""Load File action: reads EEG data from disk."""

from mnetape.actions.base import ActionDefinition
from mnetape.core.models import DataType

ACTION = ActionDefinition(
    action_id="load_file",
    title="Load File",
    params_schema={
        "file_path": {"type": "text", "default": "", "label": "File Path", "description": "Path to the EEG data file"},
        "preload": {"type": "bool", "default": True, "label": "Preload into memory"},
    },
    doc="Load EEG data from a file using MNE's auto-detecting reader.",
    body_source="raw = mne.io.read_raw(file_path, preload=preload)\nreturn raw",
    input_vars=[],
    param_names=["file_path", "preload"],
    input_type=DataType.RAW,
    output_type=DataType.RAW,
    hidden=True,
)
