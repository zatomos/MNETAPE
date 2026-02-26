"""ICA action with ICLabel automatic component classification.

Steps:
    - Fit ICA - fits the ICA decomposition
    - Classify Components - runs ICLabel + EOG/ECG/muscle detection
    - Manual Selection - interactive component inspection, then apply
"""

from mnetape.actions.base import Prerequisite, action_from_templates
from mnetape.actions.ica.widgets import run_interactive_step

ACTION = action_from_templates(
    action_id="ica",
    title="ICA",
    doc="ICA with automatic ICLabel classification. Fit, classify components, then manually select which to remove.",
    action_file=__file__,
    interactive_runners={"inspect": lambda action, raw, parent=None: run_interactive_step(action, raw, parent=parent)},
    mne_doc_urls={
        "ICA": "https://mne.tools/stable/generated/mne.preprocessing.ICA.html",
        "ICLabel": "https://mne.tools/mne-icalabel/dev/index.html",
    },
    prerequisites=(
        Prerequisite("notch", "Removing line noise prevents components being wasted on it."),
        Prerequisite("reference", "A common average improves ICA decomposition quality."),
    ),
)
