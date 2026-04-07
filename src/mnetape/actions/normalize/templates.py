"""Normalize action templates."""

from __future__ import annotations

from typing import Annotated

import mne
from mnetape.actions.base import ParamMeta, builder

_method = ParamMeta(
    type="choice",
    label="Method",
    description="zscore: subtract mean and divide by std. minmax: scale to [0, 1].",
    choices=["zscore", "minmax"],
    default="zscore",
)
_window = ParamMeta(
    type="choice",
    label="Window",
    description="global: statistics computed over the full recording. rolling_causal: statistics from preceding window only. rolling_centered: statistics from symmetric window around each sample.",
    choices=["global", "rolling_causal", "rolling_centered"],
    default="global",
)
_window_duration = ParamMeta(
    type="float",
    label="Window duration (s)",
    description="Duration of the rolling window in seconds.",
    default=10.0,
    min=0.1,
    decimals=2,
    visible_when={"window": ["rolling_causal", "rolling_centered"]},
)
_per_channel = ParamMeta(
    type="bool",
    label="Per channel",
    description="If enabled, each channel is normalized independently. If disabled, statistics are computed across all channels.",
    default=True,
)


@builder
def normalize_raw(
    raw: mne.io.Raw,
    method: Annotated[str, _method] = "zscore",
    window: Annotated[str, _window] = "global",
    window_duration: Annotated[float, _window_duration] = 10.0,
    per_channel: Annotated[bool, _per_channel] = True,
) -> mne.io.Raw:
    import numpy as np

    data = raw.get_data()  # (n_channels, n_times)
    win_samples = int(window_duration * raw.info["sfreq"])

    if window == "global":
        axis = 1 if per_channel else None
        if method == "zscore":
            m = data.mean(axis=axis, keepdims=True)
            s = data.std(axis=axis, keepdims=True)
            s = np.where(s == 0, 1.0, s)
            result = (data - m) / s
        else:
            lo = data.min(axis=axis, keepdims=True)
            hi = data.max(axis=axis, keepdims=True)
            r = np.where(hi - lo == 0, 1.0, hi - lo)
            result = (data - lo) / r

    else:
        result = np.empty_like(data)
        for i in range(data.shape[1]):
            if window == "rolling_causal":
                start, end = max(0, i - win_samples), i + 1
            else:  # rolling_centered
                half = win_samples // 2
                start, end = max(0, i - half), min(data.shape[1], i + half + 1)
            win = data[:, start:end]
            if per_channel:
                if method == "zscore":
                    m = win.mean(axis=1)
                    s = win.std(axis=1)
                    s = np.where(s == 0, 1.0, s)
                    result[:, i] = (data[:, i] - m) / s
                else:
                    lo = win.min(axis=1)
                    hi = win.max(axis=1)
                    r = np.where(hi - lo == 0, 1.0, hi - lo)
                    result[:, i] = (data[:, i] - lo) / r
            else:
                if method == "zscore":
                    m, s = win.mean(), win.std()
                    s = s if s != 0 else 1.0
                    result[:, i] = (data[:, i] - m) / s
                else:
                    lo, hi = win.min(), win.max()
                    r = (hi - lo) if hi != lo else 1.0
                    result[:, i] = (data[:, i] - lo) / r

    raw._data[:] = result
    return raw
