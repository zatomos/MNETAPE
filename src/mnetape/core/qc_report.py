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
    include_events_viewer: bool = True,
) -> Path:
    """Generate and save an HTML QC report for the current pipeline state.

    Args:
        state: Shared app state (actions, data_states, raw_original).
        out_path: Destination path for the HTML file.
        title: Title shown at the top of the report.
        include_events_viewer: Whether to include the Events Viewer section.

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

    if include_events_viewer:
        try:
            add_events_evolution_section(report, state)
        except Exception as e:
            logger.warning("Events evolution section failed: %s", e)

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
            label.set_horizontalalignment("right")
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


# -------- Events viewer helpers --------

MAX_EVO_CONDITIONS = 10
TFR_N_BINS = 20        # log-spaced frequency bins in the TFR heatmap
MAX_TFR_EPOCHS = 60    # epoch cap per condition/step to keep TFR fast


def eeg_picks_for(info: mne.Info) -> np.ndarray:
    """Return EEG pick indices, falling back to all non-bad channels."""
    picks = mne.pick_types(info, eeg=True, meg=False, exclude="bads")
    if len(picks) == 0:
        picks = mne.pick_types(info, meg=True, eeg=True, exclude="bads")
    if len(picks) == 0:
        picks = np.arange(len(info.ch_names))
    return picks


def channel_scale(info: mne.Info, picks: np.ndarray) -> tuple[float, str]:
    """Return (scale_factor, unit_label) for the dominant channel type in picks."""
    types = {mne.channel_type(info, int(p)) for p in picks}
    if "eeg" in types:
        return 1e6, "µV"
    if "mag" in types:
        return 1e15, "fT"
    if "grad" in types:
        return 1e13, "fT/cm"
    return 1.0, "a.u."


def top_conditions(epoch_list: list[mne.BaseEpochs]) -> list[str]:
    """Return the most-populated conditions across a list of epoch objects."""
    counts: Counter = Counter()
    for ep in epoch_list:
        for cond, code in ep.event_id.items():
            counts[cond] += int((ep.events[:, 2] == code).sum())
    return [cond for cond, _ in counts.most_common(MAX_EVO_CONDITIONS)]


def get_epoch_params(state: AppState) -> tuple[float, float, tuple]:
    """Return (tmin, tmax, baseline) from the pipeline's epoch action, or sane defaults."""
    for action in state.actions:
        if action.action_id in ("epoch_fixed", "epoch_events"):
            tmin = float(action.params.get("tmin", -0.2))
            tmax = float(action.params.get("tmax", 0.8))
            return tmin, tmax, (None, 0)
    return -0.2, 0.8, (None, 0)


def resolve_to_epochs(
    raw: mne.io.BaseRaw,
    tmin: float,
    tmax: float,
    baseline: tuple,
) -> mne.BaseEpochs | None:
    """Epoch a Raw object using stim-channel or annotation events. Returns None if no events found."""
    events = None
    event_id = None
    try:
        events = mne.find_events(raw, stim_channel="auto", verbose=False, min_duration=0.001)
    except Exception:
        pass
    if events is None or len(events) == 0:
        try:
            events, event_id = mne.events_from_annotations(raw, verbose=False)
        except Exception:
            return None
    if events is None or len(events) == 0:
        return None
    if event_id is None:
        event_id = {str(int(c)): int(c) for c in np.unique(events[:, 2])}
    try:
        return mne.Epochs(
            raw, events, event_id=event_id,
            tmin=tmin, tmax=tmax, baseline=baseline,
            preload=True, verbose=False,
        )
    except Exception:
        return None


def add_events_evolution_section(
    report: mne.Report,
    state: AppState,
) -> None:
    """Add Events Viewer section: per-condition ERP+TFR grid across every pipeline step.

    For Raw/ICA steps, epochs the data on-the-fly using stim-channel or annotation events and the same window as
    any existing epoch action in the pipeline.
    Steps with no detectable events are skipped. TFR frequency range is computed automatically.
    """
    tmin, tmax, baseline = get_epoch_params(state)

    # Resolve each completed step to an epoch or evoked object
    step_data: list[tuple[str, mne.BaseEpochs | mne.Evoked]] = []
    for i, action in enumerate(state.actions):
        if action.status != ActionStatus.COMPLETE:
            continue
        data = get_data_after(state, i)
        if data is None:
            continue
        label = f"Step {i + 1}: {get_action_title(action)}"
        if isinstance(data, mne.BaseEpochs):
            step_data.append((label, data))
        elif isinstance(data, mne.Evoked):
            step_data.append((label, data))
        else:
            raw = data.raw if isinstance(data, ICASolution) else data
            if isinstance(raw, mne.io.BaseRaw):
                ep = resolve_to_epochs(raw, tmin, tmax, baseline)
                if ep is not None and len(ep) > 0:
                    step_data.append((label, ep))

    if not step_data:
        return

    epoch_objs = [d for _, d in step_data if isinstance(d, mne.BaseEpochs)]
    conditions = top_conditions(epoch_objs)
    if not conditions:
        return

    report.add_html(
        "<h2>Events Viewer</h2>"
        f"<p>ERP and TFR per event type across all pipeline steps "
        f"(tmin={tmin:.2f} s, tmax={tmax:.2f} s). "
        "TFR frequency range is derived automatically from each step's epoch length."
        "Power in dB with a shared colorscale per condition.</p>",
        title="Events Viewer",
        tags=("events_viewer",),
    )
    for cond in conditions:
        try:
            add_condition_panel(report, cond, step_data)
        except Exception as e:
            logger.warning("Events viewer panel for '%s' failed: %s", cond, e)

def add_condition_panel(
    report: mne.Report,
    cond: str,
    step_data: list[tuple[str, mne.BaseEpochs | mne.Evoked]],
) -> None:
    """Render ERP and TFR side-by-side for each step in its own row."""

    # Precompute ERP and TFR for every step
    erp_results: list[tuple] = []
    tfr_results: list[tuple] = []

    for label, data in step_data:
        ep_cond = data[cond] if isinstance(data, mne.BaseEpochs) and cond in data.event_id else None

        # ERP
        ev = None
        if isinstance(data, mne.Evoked):
            ev = data.copy()
            if ev.tmin < -0.05:
                ev.apply_baseline((None, 0))
        elif ep_cond is not None:
            try:
                ev = ep_cond.average()
                if ev.tmin < -0.05:
                    ev.apply_baseline((None, 0))
            except Exception:
                pass

        if ev is not None:
            picks = eeg_picks_for(ev.info)
            sc, unit = channel_scale(ev.info, picks)
            erp_results.append((ev.data[picks] * sc, ev.times * 1000, unit))
        else:
            erp_results.append((None, None, ""))

        # TFR
        if ep_cond is not None:
            try:
                ep = ep_cond
                if len(ep) == 0:
                    raise ValueError("no epochs")

                sfreq_ep = ep.info["sfreq"]
                n_times = len(ep.times)
                fmax = min(40.0, sfreq_ep / 2.0 - 1.0)
                n_cycles_fixed = 7.0
                # Start with a broad range and let MNE itself reject too-short wavelets.
                # Retry by dropping the lowest frequency each time.
                step_freqs = np.logspace(np.log10(1.0), np.log10(fmax), TFR_N_BINS)

                picks_arr = eeg_picks_for(ep.info)
                decim = max(1, int(sfreq_ep / 50))
                ep_sub = ep[:MAX_TFR_EPOCHS].copy().pick(picks_arr)

                tfr = None
                while len(step_freqs) > 0:
                    try:
                        tfr = ep_sub.compute_tfr(
                            method="morlet",
                            freqs=step_freqs,
                            n_cycles=n_cycles_fixed,
                            average=True,
                            verbose=False,
                        )
                        break
                    except ValueError as ve:
                        if "longer than the signal" not in str(ve):
                            raise
                        step_freqs = step_freqs[1:]

                if tfr is None or len(step_freqs) == 0:
                    raise ValueError(
                        f"epoch too short ({n_times} samples at {sfreq_ep} Hz) "
                        f"for any TFR frequency up to {fmax:.0f} Hz"
                    )

                logger.info(
                    "TFR cond '%s': sfreq=%.0f n_times=%d freqs %.1f–%.1f Hz",
                    cond, sfreq_ep, n_times, step_freqs[0], step_freqs[-1],
                )
                tfr = tfr.decimate(decim)
                power_db = 10 * np.log10(tfr.data.mean(axis=0) + 1e-30)
                tfr_results.append((power_db, tfr.times * 1000, step_freqs, None))

            except Exception as e:
                logger.warning("TFR failed for cond '%s': %s", cond, e)
                tfr_results.append((None, None, None, str(e)))
        else:
            tfr_results.append((None, None, None, None))

    # Shared TFR color scale across all steps
    valid_power = [p for p, _, _, _ in tfr_results if p is not None]
    if valid_power:
        all_vals = np.concatenate([p.ravel() for p in valid_power])
        vmin, vmax = np.percentile(all_vals, [5, 95])
    else:
        vmin, vmax = -30.0, 0.0

    # One row per step, ERP left; TFR right
    n_rows = len(step_data)
    row_h = 3.5
    fig = Figure(figsize=(13, row_h * n_rows))

    for row, (label, _) in enumerate(step_data):
        erp_data, times_erp, unit = erp_results[row]
        power_db, times_tfr, step_freqs, error_msg = tfr_results[row]

        # ERP
        ax_e = fig.add_subplot(n_rows, 2, row * 2 + 1)
        if erp_data is not None:
            for ch in erp_data:
                ax_e.plot(times_erp, ch, color="steelblue", alpha=0.2, linewidth=0.6)
            ax_e.plot(times_erp, np.mean(erp_data, axis=0), color="crimson", linewidth=1.5)
            ax_e.axvline(0, color="k", linestyle="--", linewidth=0.7, alpha=0.5)
            ax_e.axhline(0, color="k", linewidth=0.3, alpha=0.2)
            ax_e.set_ylabel(f"({unit})", fontsize=7)
        else:
            ax_e.text(0.5, 0.5, "N/A", ha="center", va="center", transform=ax_e.transAxes)
            ax_e.set_axis_off()
        ax_e.set_title(f"{label} - ERP", fontsize=8)
        ax_e.tick_params(labelsize=6)
        if row == n_rows - 1:
            ax_e.set_xlabel("Time (ms)", fontsize=7)

        # TFR
        ax_t = fig.add_subplot(n_rows, 2, row * 2 + 2)
        if power_db is not None:
            ax_t.pcolormesh(
                times_tfr, step_freqs, power_db,
                cmap="RdBu_r", vmin=vmin, vmax=vmax, shading="auto",
            )
            ax_t.set_yscale("log")
            ax_t.axvline(0, color="k", linestyle="--", linewidth=0.7, alpha=0.5)
            ax_t.set_ylabel("Freq (Hz)", fontsize=7)
        else:
            ax_t.set_facecolor("#f8f8f8")
            msg = "TFR failed"
            if error_msg:
                msg += f"\n{error_msg}"
            ax_t.text(0.5, 0.5, msg, ha="center", va="center",
                      fontsize=6, color="red", transform=ax_t.transAxes, wrap=True)
            ax_t.set_xticks([])
            ax_t.set_yticks([])
        ax_t.set_title(f"{label} - TFR", fontsize=8)
        ax_t.tick_params(labelsize=6)
        if row == n_rows - 1:
            ax_t.set_xlabel("Time (ms)", fontsize=7)

    fig.suptitle(f"Events Viewer - {cond}", fontsize=10)
    fig.subplots_adjust(left=0.07, right=0.99, top=0.97, bottom=0.04, hspace=0.55, wspace=0.2)

    report.add_figure(
        fig,
        title=f"Events: {cond}",
        tags=("events_viewer",),
    )