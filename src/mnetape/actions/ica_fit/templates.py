"""ICA fit action templates.

Fits an ICA decomposition on the raw data.
"""

from __future__ import annotations

from typing import Annotated, TYPE_CHECKING

from mnetape.actions.base import ParamMeta, fragment, builder

if TYPE_CHECKING:
    import mne

PRIMARY_PARAMS = {
    "mne.preprocessing.ICA": ["n_components", "method", "fit_params"],
}


@fragment
def _fit_all_eeg(raw, method: str = "infomax", fit_params: dict | None = None) -> None:
    ica = mne.preprocessing.ICA(
        n_components=len(mne.pick_types(raw.info, eeg=True)),
        method=method,
        random_state=42,
        fit_params=fit_params,
    )
    ica.fit(raw)


@fragment
def _fit_fixed(
    raw, n_components: int = 20, method: str = "infomax", fit_params: dict | None = None
) -> None:
    ica = mne.preprocessing.ICA(
        n_components=n_components,
        method=method,
        random_state=42,
        fit_params=fit_params,
    )
    ica.fit(raw)


@builder
def fit_builder(
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
) -> str:
    fit_params = {"extended": True} if method == "infomax" else None
    if int(n_components) == 0:
        return _fit_all_eeg.inline(method=method, fit_params=fit_params)
    return _fit_fixed.inline(
        n_components=int(n_components),
        method=method,
        fit_params=fit_params,
    )
