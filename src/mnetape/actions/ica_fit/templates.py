"""ICA fit action templates."""

from __future__ import annotations

from typing import Annotated

import mne
from mnetape.actions.base import ParamMeta, builder, result_builder

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

@result_builder
def build_result(data):
    import numpy as np
    from matplotlib.figure import Figure
    from mnetape.core.models import ActionResult, ICASolution

    if not isinstance(data, ICASolution):
        return ActionResult(summary="ICA fitted.")

    ica = data.ica
    n_comp = ica.n_components_
    var = getattr(ica, "pca_explained_variance_", None)

    fig = None
    if var is not None and len(var) > 0:
        total = var.sum()
        pct = var / total * 100
        cum_pct = np.cumsum(pct)

        fig = Figure(figsize=(max(6, len(var) * 0.22), 3.8))
        ax = fig.add_subplot(111)
        colors = ["steelblue" if i < n_comp else "#BDBDBD" for i in range(len(var))]
        ax.bar(range(len(var)), pct, color=colors, alpha=0.85)
        ax2 = ax.twinx()
        ax2.plot(range(len(var)), cum_pct, color="tomato", linewidth=1.5)
        ax2.set_ylabel("Cumulative variance (%)", color="tomato", fontsize=9)
        ax2.tick_params(axis="y", labelcolor="tomato")
        ax2.set_ylim(0, 105)
        if n_comp < len(var):
            ax.axvline(n_comp - 0.5, color="orange", linestyle="--", linewidth=1.2)
        ax.set_xlabel("PCA component")
        ax.set_ylabel("Explained variance (%)")
        ax.set_title("PCA Explained Variance")
        fig.tight_layout()

    comp_var_pct = var[:n_comp].sum() / var.sum() * 100 if var is not None else None
    summary = f"ICA fitted: {n_comp} components"
    if comp_var_pct is not None:
        summary += f" ({comp_var_pct:.1f}% of total variance)"
    return ActionResult(summary=summary, fig=fig)
