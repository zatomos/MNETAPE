"""QC report generation for EEG preprocessing pipelines.

Generates a per-participant HTML report.
Each completed pipeline step gets a section with relevant before/after plots.
"""

from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

import mne
import numpy as np
from matplotlib.figure import Figure

from mnetape.actions.registry import get_action_title
from mnetape.core.models import ActionConfig, ActionStatus, ICASolution

if TYPE_CHECKING:
    from mnetape.gui.controllers.state import AppState

logger = logging.getLogger(__name__)


def generate_report(
    state: AppState,
    out_path: Path,
    title: str = "EEG QC Report",
) -> Path:
    """Generate and save an HTML QC report for the current pipeline state.

    Args:
        state: Shared app state (actions, data_states, raw_original).
        out_path: Destination path for the HTML file.
        title: Title shown at the top of the report.

    Returns:
        Path to the saved HTML file.
    """
    report = mne.Report(title=title, verbose=False)

    add_summary_table(report, state)

    if state.raw_original is not None:
        try:
            report.add_raw(state.raw_original, title="Original Data", tags=("original",), psd=True)
        except Exception as e:
            logger.warning("Could not add raw overview: %s", e)

    for i, action in enumerate(state.actions):
        if action.status != ActionStatus.COMPLETE:
            continue
        data_before = get_data_before(state, i)
        data_after = get_data_after(state, i)
        if data_after is None:
            continue
        section_title = f"Step {i + 1}: {get_action_title(action)}"
        try:
            add_action_section(report, action, section_title, data_before, data_after)
        except Exception as e:
            logger.warning("Section for action %d (%s) failed: %s", i + 1, action.action_id, e)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    report.save(str(out_path), overwrite=True, open_browser=False, verbose=False)
    return out_path


# -------- Data accessors --------

def get_data_before(state: AppState, i: int):
    if i == 0:
        return state.raw_original
    if i <= len(state.data_states):
        stored = state.data_states[i - 1]
        if stored is not None:
            return stored
    return state.raw_original


def get_data_after(state: AppState, i: int):
    if i < len(state.data_states):
        return state.data_states[i]
    return None


# -------- Summary table --------

def add_summary_table(report: mne.Report, state: AppState) -> None:
    rows = []
    for i, action in enumerate(state.actions):
        sym = {"COMPLETE": "✓", "ERROR": "✗", "PENDING": "○"}.get(action.status.name, "?")
        color = {"COMPLETE": "green", "ERROR": "red", "PENDING": "#DAA520"}.get(action.status.name, "")
        data_after = get_data_after(state, i)
        shape = shape_str(data_after)
        err = (
            f'<br><span style="color:red;font-size:0.85em">{action.error_msg}</span>'
            if action.error_msg else ""
        )
        rows.append(
            f"<tr>"
            f"<td>{i + 1}</td>"
            f"<td>{get_action_title(action)}</td>"
            f'<td style="color:{color}">{sym} {action.status.name.capitalize()}{err}</td>'
            f"<td>{shape}</td>"
            f"</tr>"
        )
    html = (
        "<h2>Pipeline Summary</h2>"
        '<table border="1" cellpadding="6" cellspacing="0"'
        ' style="border-collapse:collapse;width:100%;font-size:0.95em">'
        "<tr><th>#</th><th>Action</th><th>Status</th><th>Output</th></tr>"
        + "".join(rows)
        + "</table>"
        f'<p style="color:gray;font-size:0.85em">Generated {datetime.now().strftime("%Y-%m-%d %H:%M")}</p>'
    )
    report.add_html(html, title="Summary", tags=("summary",))


def shape_str(data) -> str:
    if data is None:
        return ""
    obj = data.raw if isinstance(data, ICASolution) else data
    if not hasattr(obj, "ch_names"):
        return ""
    n_ch = len(obj.ch_names)
    if isinstance(obj, mne.BaseEpochs):
        return f"{len(obj)} epochs × {n_ch} ch"
    if isinstance(obj, mne.Evoked):
        return f"{n_ch} ch"
    if hasattr(obj, "times") and len(obj.times) > 1:
        dur = obj.times[-1] - obj.times[0]
        return f"{n_ch} ch, {dur:.1f} s"
    return f"{n_ch} ch"


# -------- Per-action dispatcher --------

def add_action_section(
    report: mne.Report,
    action: ActionConfig,
    title: str,
    data_before,
    data_after,
) -> None:
    aid = action.action_id
    tags = (aid,)

    if aid in ("filter", "notch", "normalize"):
        add_psd_comparison(report, title, tags, data_before, data_after)

    elif aid == "drop_channels":
        add_drop_channels_section(report, title, tags, data_before, data_after)

    elif aid == "interpolate":
        add_interpolate_section(report, title, tags, data_after)

    elif aid == "ica_fit":
        add_ica_fit_section(report, title, tags, data_after)

    elif aid == "ica_classify":
        add_ica_classify_section(report, title, tags, data_after)

    elif aid == "ica_apply":
        raw_before = data_before.raw if isinstance(data_before, ICASolution) else data_before
        add_psd_comparison(report, title, tags, raw_before, data_after)

    elif aid in ("epoch_fixed", "epoch_events"):
        add_epochs_section(report, title, tags, data_after)

    elif aid == "drop_bad_epochs":
        add_drop_epochs_section(report, title, tags, data_after)

    elif aid == "average_epochs":
        add_evoked_section(report, title, tags, data_after)


# -------- Section builders --------

def add_psd_comparison(
    report: mne.Report, title: str, tags: tuple, data_before, data_after
) -> None:
    raw_b = data_before.raw if isinstance(data_before, ICASolution) else data_before
    raw_a = data_after.raw if isinstance(data_after, ICASolution) else data_after
    if not hasattr(raw_b, "compute_psd") or not hasattr(raw_a, "compute_psd"):
        return

    sfreq = raw_b.info["sfreq"]
    fmax = min(60.0, sfreq / 2)

    psd_b = raw_b.compute_psd(fmax=fmax, verbose=False)
    psd_a = raw_a.compute_psd(fmax=fmax, verbose=False)

    freqs = psd_b.freqs
    db_b = 10 * np.log10(psd_b.get_data().mean(axis=0) + 1e-30)
    db_a = 10 * np.log10(psd_a.get_data().mean(axis=0) + 1e-30)

    fig = Figure(figsize=(10, 4))
    ax = fig.add_subplot(111)
    ax.plot(freqs, db_b, color="#4C72B0", alpha=0.85, label="Before", linewidth=1.5)
    ax.plot(freqs, db_a, color="#DD8452", alpha=0.85, label="After", linewidth=1.5)
    ax.set_xlabel("Frequency (Hz)")
    ax.set_ylabel("Power (dB, avg across channels)")
    ax.set_title("PSD: Before vs After")
    ax.legend()
    ax.grid(True, alpha=0.3)
    fig.tight_layout()

    report.add_figure(fig, title=title, tags=tags, caption="PSD before (blue) vs after (orange)")


def add_drop_channels_section(
    report: mne.Report, title: str, tags: tuple, data_before, data_after
) -> None:
    if not hasattr(data_before, "ch_names") or not hasattr(data_after, "ch_names"):
        return
    dropped = sorted(set(data_before.ch_names) - set(data_after.ch_names))
    if not dropped:
        return

    try:
        figs = data_before.plot_sensors(show_names=True, show=False)
        fig = figs if isinstance(figs, Figure) else figs.get_figure()
        report.add_figure(
            fig, title=title, tags=tags,
            caption=f"Dropped {len(dropped)} channel(s): {', '.join(dropped)}"
        )
    except Exception:
        report.add_html(
            f"<p>Dropped {len(dropped)} channel(s): {', '.join(dropped)}</p>",
            title=title, tags=tags,
        )


def add_interpolate_section(
    report: mne.Report, title: str, tags: tuple, data_after
) -> None:
    if not hasattr(data_after, "info"):
        return
    bads = list(data_after.info.get("bads", []))
    try:
        figs = data_after.plot_sensors(show_names=True, show=False)
        fig = figs if isinstance(figs, Figure) else figs.get_figure()
        caption = (
            f"Interpolated {len(bads)} channel(s): {', '.join(bads)}"
            if bads else "Channels after interpolation."
        )
        report.add_figure(fig, title=title, tags=tags, caption=caption)
    except Exception as e:
        logger.warning("Interpolate sensor map failed: %s", e)


def add_ica_fit_section(
    report: mne.Report, title: str, tags: tuple, data_after
) -> None:
    if not isinstance(data_after, ICASolution):
        return
    ica = data_after.ica
    try:
        figs = ica.plot_components(inst=data_after.raw, show=False, verbose=False)
        fig_list = figs if isinstance(figs, list) else [figs]
        for j, fig in enumerate(fig_list):
            page_title = f"{title} (page {j + 1})" if len(fig_list) > 1 else title
            report.add_figure(fig, title=page_title, tags=tags)
    except Exception as e:
        logger.warning("ICA component plot failed: %s", e)


def add_ica_classify_section(
    report: mne.Report, title: str, tags: tuple, data_after
) -> None:
    if not isinstance(data_after, ICASolution):
        return
    ic_labels = data_after.ic_labels
    if not ic_labels or "labels" not in ic_labels:
        return

    labels = ic_labels["labels"]
    counts = Counter(labels)
    categories = sorted(counts.keys())

    label_colors = {
        "brain": "#4CAF50",
        "eye blink": "#F44336",
        "heart beat": "#E91E63",
        "muscle artifact": "#FF9800",
        "channel noise": "#9E9E9E",
        "other": "#2196F3",
    }

    fig = Figure(figsize=(8, max(3, len(categories) * 0.6)))
    ax = fig.add_subplot(111)
    bar_colors = [label_colors.get(c, "#607D8B") for c in categories]
    ax.barh(categories, [counts[c] for c in categories], color=bar_colors)
    ax.set_xlabel("Number of components")
    ax.set_title("ICA Component Classification")
    fig.tight_layout()

    excluded = data_after.detected_artifacts or []
    caption = (
        f"{len(excluded)} component(s) marked for exclusion: {excluded}"
        if excluded else "No components marked for exclusion."
    )
    report.add_figure(fig, title=title, tags=tags, caption=caption)


def add_epochs_section(
    report: mne.Report, title: str, tags: tuple, data_after
) -> None:
    if not isinstance(data_after, mne.BaseEpochs):
        return

    event_id = data_after.event_id
    if event_id:
        names = list(event_id.keys())
        counts = [len(data_after[n]) for n in names]

        fig = Figure(figsize=(max(6, len(names) * 1.4), 4))
        ax = fig.add_subplot(111)
        ax.bar(names, counts, color="#4C72B0")
        ax.set_ylabel("Count")
        ax.set_title("Epochs per condition")
        for label in ax.get_xticklabels():
            label.set_rotation(30)
            label.set_ha("right")
        fig.tight_layout()

        caption = (
            f"{len(data_after)} total epochs - "
            f"tmin={data_after.tmin:.3f} s, tmax={data_after.tmax:.3f} s"
        )
        report.add_figure(fig, title=title, tags=tags, caption=caption)

    try:
        report.add_epochs(data_after, title=f"{title} (overview)", tags=tags, psd=False)
    except Exception as e:
        logger.warning("add_epochs failed: %s", e)


def add_drop_epochs_section(
    report: mne.Report, title: str, tags: tuple, data_after
) -> None:
    if not isinstance(data_after, mne.BaseEpochs):
        return

    drop_log = data_after.drop_log
    n_total = len(drop_log)
    n_kept = sum(1 for d in drop_log if not d)
    n_dropped = n_total - n_kept
    pct = 100 * n_dropped / n_total if n_total else 0.0

    chan_counts: Counter = Counter()
    for entry in drop_log:
        for ch in entry:
            if ch not in ("USER", "IGNORED"):
                chan_counts[ch] += 1

    fig = Figure(figsize=(12, 4))

    # Pie
    ax_pie = fig.add_subplot(121)
    ax_pie.pie(
        [n_kept, n_dropped],
        labels=[f"Kept ({n_kept})", f"Dropped ({n_dropped})"],
        colors=["#4CAF50", "#F44336"],
        autopct="%1.1f%%",
    )
    ax_pie.set_title("Epoch retention")

    # Top offending channels
    ax_bar = fig.add_subplot(122)
    if chan_counts:
        top = chan_counts.most_common(10)
        chans, ch_counts = zip(*top)
        ax_bar.barh(list(chans), list(ch_counts), color="#F44336")
        ax_bar.set_title("Channels causing most rejections")
        ax_bar.set_xlabel("Epochs dropped")
    else:
        ax_bar.text(0.5, 0.5, "No channel-based rejections", ha="center", va="center",
                    transform=ax_bar.transAxes)
        ax_bar.set_axis_off()

    fig.tight_layout()
    report.add_figure(
        fig, title=title, tags=tags,
        caption=f"{n_dropped}/{n_total} epochs dropped ({pct:.1f}%)"
    )


def add_evoked_section(
    report: mne.Report, title: str, tags: tuple, data_after
) -> None:
    if not isinstance(data_after, mne.Evoked):
        return
    try:
        report.add_evoked(data_after, title=title, tags=tags, n_time_points=5)
    except Exception as e:
        logger.warning("Evoked section failed: %s", e)
