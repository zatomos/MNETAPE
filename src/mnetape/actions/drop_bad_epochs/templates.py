"""Drop bad epochs action templates.

Two methods:
  - manual: user-specified amplitude thresholds
  - autoreject: data-driven thresholds + interpolation via the autoreject library
"""

from __future__ import annotations

from typing import Annotated

import mne
from mnetape.actions.base import ParamMeta, builder, result_builder


@builder
def template_builder(
    epochs: mne.BaseEpochs,
    method: Annotated[
        str,
        ParamMeta(
            type="choice",
            label="Method",
            description="Manual: set amplitude thresholds. AutoReject: auto-learn thresholds.",
            choices=["manual", "autoreject"],
            default="manual",
        ),
    ] = "manual",
    reject: Annotated[
        dict | None,
        ParamMeta(
            label="Reject",
            description="Drop epochs where peak-to-peak amplitude exceeds this threshold. None = disabled.",
            default=None,
            visible_when={"method": ["manual"]},
        ),
    ] = None,
    flat: Annotated[
        dict | None,
        ParamMeta(
            label="Flat",
            description="Drop epochs where peak-to-peak amplitude is below this threshold. None = disabled.",
            default=None,
            visible_when={"method": ["manual"]},
        ),
    ] = None,
    **kwargs,
) -> mne.BaseEpochs:
    if method == "autoreject":
        from autoreject import AutoReject
        ar = AutoReject(verbose=False)
        epochs = ar.fit_transform(epochs)
    else:
        epochs.drop_bad(reject=reject, flat=flat, **kwargs)
    return epochs


@result_builder
def build_result(data):
    from mnetape.core.models import ActionResult

    drop_log = getattr(data, "drop_log", None)
    if drop_log is None:
        return ActionResult(summary="Epochs processed.")

    n_total = len(drop_log)
    n_dropped = sum(1 for r in drop_log if r)
    n_kept = n_total - n_dropped

    fig = None
    if n_dropped:
        import numpy as np
        import mne
        from matplotlib.figure import Figure
        from matplotlib.colors import ListedColormap, BoundaryNorm
        from matplotlib.patches import Patch

        kept, reject, flat, both, auto = 0, 1, 2, 3, 4
        cat_names = {kept: "kept", reject: "Reject", flat: "Flat", both: "Reject + Flat", auto: "AutoReject"}

        reject_dict = getattr(data, "reject", {}) or {}
        flat_dict = getattr(data, "flat", {}) or {}
        ch_name_set = set(data.ch_names)
        ch_type_map = {
            data.ch_names[i]: mne.channel_type(data.info, i)
            for i in range(len(data.ch_names))
        }

        def category(reason: str) -> int:
            if reason in ch_name_set:
                ch_type = ch_type_map.get(reason, "")
                in_r = ch_type in reject_dict
                in_f = ch_type in flat_dict
                if in_r and in_f:
                    return both
                if in_r:
                    return reject
                if in_f:
                    return flat
            return auto

        # Collect which rows  appear in drop_log
        row_order: list[str] = []
        row_index: dict[str, int] = {}
        epoch_entries: list[dict[str, int]] = [{} for _ in range(n_total)]

        for ep_idx, reasons in enumerate(drop_log):
            for r in reasons:
                cat = category(r)
                if r not in row_index:
                    row_index[r] = len(row_order)
                    row_order.append(r)
                epoch_entries[ep_idx][r] = max(epoch_entries[ep_idx].get(r, 0), cat)

        # Sort rows by channel order then unknown strings
        ch_order = {ch: i for i, ch in enumerate(data.ch_names)}
        row_order.sort(key=lambda r: (ch_order.get(r, len(data.ch_names)), r))
        row_index = {r: i for i, r in enumerate(row_order)}
        n_rows = len(row_order)

        matrix = np.zeros((n_rows, n_total), dtype=np.int8)
        for ep_idx, entries in enumerate(epoch_entries):
            for r, cat in entries.items():
                matrix[row_index[r], ep_idx] = cat

        colors = ["#FFFFFF", "#EF5350", "#42A5F5", "#AB47BC", "#FFA726"]
        cmap = ListedColormap(colors)
        norm = BoundaryNorm([-0.5, 0.5, 1.5, 2.5, 3.5, 4.5], ncolors=5)

        fig_h = max(3.0, n_rows * 0.28 + 1.5)
        fig_w = max(8, min(16, n_total * 0.05 + 3))
        fig = Figure(figsize=(fig_w, fig_h))
        ax = fig.add_subplot(111)

        ax.imshow(matrix, aspect="auto", cmap=cmap, norm=norm, interpolation="nearest")
        ax.set_yticks(range(n_rows))
        ax.set_yticklabels(row_order, fontsize=7)
        ax.set_xlabel("Epoch index")
        ax.set_title(f"Rejection matrix — {n_dropped} of {n_total} epochs dropped")

        used_cats = sorted(set(matrix.flat) - {kept})
        cat_to_color = {reject: "#EF5350", flat: "#42A5F5", both: "#AB47BC", auto: "#FFA726"}
        handles = [Patch(facecolor=cat_to_color[c], label=cat_names[c]) for c in used_cats]
        ax.legend(handles=handles, fontsize=7, loc="upper right", framealpha=0.9)

        def formatter(x, y):
            col, row = int(round(x)), int(round(y))
            if 0 <= col < n_total and 0 <= row < n_rows:
                return f"Epoch {col}  |  {row_order[row]}  |  {cat_names[matrix[row, col]]}"
            return ""

        ax.format_coord = formatter

        fig.tight_layout()

    summary = f"{n_dropped} of {n_total} epochs dropped ({n_kept} kept)"
    return ActionResult(
        summary=summary, fig=fig,
        details={"Kept": n_kept, "Dropped": n_dropped, "Total": n_total},
    )
