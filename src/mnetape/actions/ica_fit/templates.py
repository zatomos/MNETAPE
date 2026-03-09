"""ICA fit action templates."""

from __future__ import annotations

from typing import Annotated

import mne
from mnetape.actions.base import ParamMeta, builder


@builder
def template_builder(
    raw: mne.io.Raw,
    n_components: Annotated[
        int,
        ParamMeta(
            type="int",
            label="Components (0 = all EEG channels)",
            description="Number of ICA components. 0 means use the number of EEG channels.",
            default=0,
            min=0,
            max=999,
        ),
    ] = 0,
    method: Annotated[
        str,
        ParamMeta(
            type="choice",
            choices=["fastica", "infomax", "picard"],
            label="Method",
            description="ICA algorithm to use. Infomax (extended) recommended for ICLabel.",
            default="infomax",
        ),
    ] = "infomax",
    ica_kwargs={},
    fit_kwargs={},
) -> tuple[mne.preprocessing.ICA, mne.io.Raw, dict | None]:
    fit_params = {"extended": True} if method == "infomax" else None
    n_comp = len(mne.pick_types(raw.info, eeg=True)) if n_components == 0 else n_components
    ica = mne.preprocessing.ICA(n_components=n_comp, method=method, max_iter="auto", fit_params=fit_params, **ica_kwargs)
    ica.fit(raw, picks="eeg", **fit_kwargs)
    ic_labels = None
    return ica, raw, ic_labels
