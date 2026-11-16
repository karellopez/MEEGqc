"""Line-thickness and dot-size controls of the report figures.

One definition for the subject, dataset and QC reports (each had its own copy).

Every button names the traces it changes. A restyle that lists a value for
EVERY trace also reaches traces meant to stay invisible: the quantile bands of
the channel x epoch heatmaps are drawn as a fill between two zero-width lines,
and a thickness button that set every line's width drew those edges in
plotly's default colours. A ``None`` in such a list does not help either:
plotly reads it as "reset to the default" (a 2 px line). The buttons are also
replayed by the report's Plot settings panel, which passes all their
arguments on, so naming the traces covers that path too.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import plotly.graph_objects as go

LEVELS = tuple(range(1, 9))
MARKER_SIZE_BY_LEVEL = {1: 3.0, 2: 4.0, 3: 5.5, 4: 7.0, 5: 8.5, 6: 10.0, 7: 12.0, 8: 14.0}
_SCATTER = {"scatter", "scattergl"}
_OUTLINED = {"violin", "box"}


def _kind(trace) -> Tuple[str, str]:
    return (str(getattr(trace, "type", "") or "").lower(),
            str(getattr(trace, "mode", "") or "").lower())


def _attr(trace, *path) -> Optional[float]:
    obj = trace
    for name in path:
        obj = getattr(obj, name, None)
        if obj is None:
            return None
    return obj


def draws_line(trace) -> bool:
    """True when the trace draws a line the thickness control should change.

    A line created with width 0 is deliberately invisible (a band edge) and is
    never thickened.
    """
    t, mode = _kind(trace)
    if not ((t in _SCATTER and "lines" in mode) or t in _OUTLINED):
        return False
    width = _attr(trace, "line", "width")
    return width is None or float(width) > 0


def _marker_line_width(trace, level: float) -> Optional[float]:
    t, mode = _kind(trace)
    if t == "histogram":
        return max(0.5, level * 0.6)
    if t in _SCATTER and "markers" in mode:
        return max(0.25, level * 0.2)
    return None


def line_restyle(fig: go.Figure, level: int) -> list:
    """``[update, trace_indices]`` for one "Line thickness level" button."""
    lw = float(level)
    idx: List[int] = []
    widths: List[Optional[float]] = []
    outlines: List[Optional[float]] = []
    for i, tr in enumerate(fig.data):
        line = lw if draws_line(tr) else None
        outline = _marker_line_width(tr, lw)
        if line is None and outline is None:
            continue
        idx.append(i)
        # A trace listed for one attribute keeps its own value of the other.
        widths.append(line if line is not None else _attr(tr, "line", "width"))
        outlines.append(outline if outline is not None else _attr(tr, "marker", "line", "width"))
    return [{"line.width": widths, "marker.line.width": outlines}, idx]


def marker_restyle(fig: go.Figure, level: int, default_size: float = 8.0) -> list:
    """``[update, trace_indices]`` for one "Dot size level" button."""
    size = float(MARKER_SIZE_BY_LEVEL.get(level, default_size))
    idx = [i for i, tr in enumerate(fig.data)
           if _kind(tr)[0] in _SCATTER and "markers" in _kind(tr)[1]]
    return [{"marker.size": [size] * len(idx)}, idx]


def attach_distribution_style_controls(
    fig: go.Figure,
    *,
    default_line_width: float = 2.0,
    default_marker_size: float = 8.0,
) -> None:
    """Add "Line thickness level" and "Dot size level" button rows when the
    figure has traces they would change (and does not have them already)."""
    if fig is None or not getattr(fig, "data", None):
        return
    has_lines = bool(line_restyle(fig, 1)[1])
    has_markers = bool(marker_restyle(fig, 1)[1])
    if not (has_lines or has_markers):
        return

    existing = list(fig.layout.updatemenus) if fig.layout.updatemenus else []
    names = {str(getattr(m, "name", "") or "") for m in existing}
    line_active = max(0, min(7, int(round(default_line_width)) - 1))
    marker_level = min(LEVELS, key=lambda lv: abs(MARKER_SIZE_BY_LEVEL[lv] - float(default_marker_size)))

    def _row(name: str, active: int, args_for) -> Dict[str, object]:
        return dict(name=name, type="buttons", direction="right", showactive=True,
                    active=active, buttons=[dict(label=str(lv), method="restyle",
                                                 args=args_for(lv)) for lv in LEVELS])

    if has_lines and "Line thickness level" not in names:
        existing.append(_row("Line thickness level", line_active,
                             lambda lv: line_restyle(fig, lv)))
    if has_markers and "Dot size level" not in names:
        existing.append(_row("Dot size level", marker_level - 1,
                             lambda lv: marker_restyle(fig, lv, default_marker_size)))
    fig.update_layout(updatemenus=existing)
