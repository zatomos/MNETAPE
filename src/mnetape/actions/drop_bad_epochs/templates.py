"""Drop bad epochs action templates.

Two methods:
  - manual: user-specified amplitude thresholds
  - autoreject: data-driven thresholds + interpolation via the autoreject library
"""

from __future__ import annotations

from typing import Annotated, TYPE_CHECKING

from mnetape.actions.base import ParamMeta, fragment, step

if TYPE_CHECKING:
    import mne


@fragment
def _do_drop_bad_manual(epochs, reject, flat) -> None:
    epochs.drop_bad(reject=reject, flat=flat)


@fragment
def _do_autoreject(epochs) -> None:
    from autoreject import AutoReject as AutoReject
    ar = AutoReject(verbose=False)
    epochs = ar.fit_transform(epochs)


@step("drop_bad_epochs")
def template_builder(
    method: Annotated[
        str,
        ParamMeta(
            type="choice",
            label="Method",
            description="Manual: set amplitude thresholds per channel type. AutoReject: automatically learn thresholds using cross-validation.",
            choices=["manual", "autoreject"],
            default="manual",
        ),
    ] = "manual",
    reject: Annotated[
        dict | None,
        ParamMeta(
            type="reject_thresholds",
            label="Reject",
            description="Drop epochs where peak-to-peak amplitude exceeds this threshold. None = disabled.",
            default=None,
            visible_when={"method": ["manual"]},
        ),
    ] = None,
    flat: Annotated[
        dict | None,
        ParamMeta(
            type="flat_thresholds",
            label="Flat",
            description="Drop epochs where peak-to-peak amplitude is below this threshold (flat signal). None = disabled.",
            default=None,
            visible_when={"method": ["manual"]},
        ),
    ] = None,
) -> str:
    if method == "autoreject":
        return _do_autoreject.inline()
    return _do_drop_bad_manual.inline(reject=reject, flat=flat)
