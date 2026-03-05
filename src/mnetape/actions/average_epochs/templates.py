"""Average epochs action templates."""

from __future__ import annotations

from typing import Annotated, TYPE_CHECKING

from mnetape.actions.base import ParamMeta, fragment, step

if TYPE_CHECKING:
    import mne


@fragment
def _do_average_all(epochs) -> None:
    evoked = epochs.average()


@fragment
def _do_average_event(epochs, event_key) -> None:
    subset = epochs[event_key]
    if len(subset) == 0:
        raise ValueError(f"No epochs found for selected condition: {event_key!r}")
    evoked = subset.average()


@step("average_epochs")
def template_builder(
    event_key: Annotated[
        str | None,
        ParamMeta(
            type="event_key",
            label="Condition",
            description="Event condition to average. Leave empty to average all epochs.",
            default=None,
            nullable=True,
        ),
    ] = None,
) -> str:
    if event_key:
        return _do_average_event.inline(event_key=event_key)
    return _do_average_all.inline()
