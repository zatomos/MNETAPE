"""Drop bad epochs action templates.

Two methods:
  - manual: user-specified amplitude thresholds
  - autoreject: data-driven thresholds + interpolation via the autoreject library
"""

from __future__ import annotations

from typing import Annotated

import mne
from mnetape.actions.base import ParamMeta, builder


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
