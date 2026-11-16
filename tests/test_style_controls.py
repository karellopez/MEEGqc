"""The report figures' "Line thickness level" and "Dot size level" buttons.

They used to send one value per trace in the figure, so a thickness button also
reached the invisible edges of the quantile bands in the channel x epoch
heatmaps (lines of width 0 with no colour) and drew them in plotly's default
pink, orange and purple. The buttons now name the traces they change, and the
band edges are transparent as well.

``_apply`` replays a button the way plotly.js does (``Plotly.restyle(gd,
update, indices)``, which is also what the report's Plot settings panel calls),
so the tests check what a reader would actually see.
"""
from __future__ import annotations

import copy

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import pytest

from meg_qc.plotting.style_controls import (
    attach_distribution_style_controls, draws_line, line_restyle, marker_restyle,
)


def _apply(fig: go.Figure, args: list) -> go.Figure:
    """Replay one restyle button on a copy of the figure, as plotly.js does:
    ``None`` puts an attribute back to its default, and an attribute the
    trace type does not have (``line`` on a histogram) is ignored."""
    update, indices = args
    out = go.Figure(copy.deepcopy(fig.to_dict()))
    for key, values in update.items():
        for i, value in zip(indices, values):
            try:
                out.data[i].update(_nested(key, value) if "." in key else {key: value})
            except ValueError:
                pass
    return out


def _own(trace, key: str):
    """The trace's own value of a dotted attribute; None when unset or when
    its type has no such attribute."""
    obj = trace
    for part in key.split("."):
        try:
            obj = getattr(obj, part)
        except AttributeError:
            return None
        if obj is None:
            return None
    return obj


def _nested(key: str, value):
    head, _, rest = key.partition(".")
    return {head: _nested(rest, value) if "." in rest else {rest: value}}


def _mixed_figure() -> go.Figure:
    fig = go.Figure()
    fig.add_scatter(y=[1, 2], mode="lines", line=dict(width=0), name="band edge")       # 0
    fig.add_scatter(y=[0, 1], mode="lines", line=dict(width=0), fill="tonexty",
                    name="band fill")                                                  # 1
    fig.add_scatter(y=[1, 1], mode="lines", line=dict(width=2.4, color="#123"))        # 2
    fig.add_scatter(y=[1, 3], mode="markers", marker=dict(size=6))                     # 3
    fig.add_scatter(y=[2, 3], mode="lines+markers", line=dict(width=0))                # 4
    fig.add_violin(y=[1, 2, 3])                                                        # 5
    fig.add_histogram(x=[1, 2, 2], marker=dict(line=dict(width=1.5)))                  # 6
    fig.add_bar(y=[1, 2], marker=dict(line=dict(width=1)))                             # 7
    return fig


def _buttons(fig: go.Figure, name: str) -> list:
    menu = next(m for m in fig.layout.updatemenus if m.name == name)
    return [b.args for b in menu.buttons]


def test_zero_width_lines_are_never_thickened():
    fig = _mixed_figure()
    attach_distribution_style_controls(fig)
    for args in _buttons(fig, "Line thickness level"):
        after = _apply(fig, args)
        for i in (0, 1, 4):
            assert after.data[i].line.width == 0, (i, args)


def test_visible_lines_follow_the_button():
    fig = _mixed_figure()
    attach_distribution_style_controls(fig)
    after = _apply(fig, _buttons(fig, "Line thickness level")[4])     # level 5
    assert after.data[2].line.width == 5
    assert after.data[5].line.width == 5                             # violin outline
    assert after.data[6].marker.line.width == pytest.approx(3.0)     # histogram outline


def test_traces_a_button_does_not_own_keep_their_own_values():
    fig = _mixed_figure()
    attach_distribution_style_controls(fig)
    after = _apply(fig, _buttons(fig, "Line thickness level")[7])     # level 8
    assert after.data[7].marker.line.width == 1                      # the bar is untouched
    assert after.data[2].line.color == "#123"


def test_a_null_only_ever_means_the_default_the_trace_already_has():
    """plotly reads null as "reset to the default" (a 2 px line), which would
    draw an invisible edge. A button may send null only for an attribute the
    trace never set (or its type does not have), where nothing changes."""
    fig = _mixed_figure()
    attach_distribution_style_controls(fig)
    for name in ("Line thickness level", "Dot size level"):
        for update, indices in _buttons(fig, name):
            for key, values in update.items():
                assert len(values) == len(indices)
                for i, v in zip(indices, values):
                    if v is None:
                        assert _own(fig.data[i], key) is None, (name, key, i)


def test_dot_size_only_lists_marker_traces():
    fig = _mixed_figure()
    update, indices = marker_restyle(fig, 3)
    assert indices == [3, 4] and update["marker.size"] == [5.5, 5.5]


def test_controls_are_added_once_and_only_when_useful():
    fig = _mixed_figure()
    attach_distribution_style_controls(fig)
    attach_distribution_style_controls(fig)
    names = [m.name for m in fig.layout.updatemenus]
    assert names.count("Line thickness level") == 1 and names.count("Dot size level") == 1
    heat = go.Figure(go.Heatmap(z=[[1, 2], [3, 4]]))
    attach_distribution_style_controls(heat)
    assert not heat.layout.updatemenus


def test_a_trace_at_index_zero_still_gets_a_control():
    fig = go.Figure(go.Scatter(y=[1, 2], mode="lines"))
    attach_distribution_style_controls(fig)
    assert [m.name for m in fig.layout.updatemenus] == ["Line thickness level"]
    assert line_restyle(fig, 2)[1] == [0]


# ── the real figure: STD channel x epoch heatmap ───────────────────────────

def _std_table(path, n_channels=6, n_epochs=12):
    rng = np.random.default_rng(0)
    rows = []
    for c in range(n_channels):
        row = {"Name": f"MEG{c:04d}", "Type": "mag", "Lobe": "Left Frontal",
               "Lobe Color": "#1f77b4", "System": "TRIUX", "Sensor_location_0": 0.0,
               "Sensor_location_1": 0.0, "Sensor_location_2": 0.0, "Time series": np.nan,
               "STD all": 4e-13}
        row.update({f"STD epoch_{e}": float(rng.uniform(2e-13, 6e-13)) for e in range(n_epochs)})
        rows.append(row)
    pd.DataFrame(rows).to_csv(path, sep="\t")
    return path


def test_heatmap_band_edges_stay_hidden_through_every_button(tmp_path):
    from meg_qc.plotting.universal_plots import boxplot_epoched_xaxis_channels_csv
    derivs = boxplot_epoched_xaxis_channels_csv(str(_std_table(tmp_path / "std.tsv")), "mag", "stds")
    fig = derivs[0].content
    edges = [i for i, tr in enumerate(fig.data)
             if getattr(tr, "line", None) is not None and tr.line.width == 0]
    assert len(edges) == 8, edges
    for i in edges:
        assert fig.data[i].line.color == "rgba(0,0,0,0)"
    attach_distribution_style_controls(fig)
    for args in _buttons(fig, "Line thickness level"):
        after = _apply(fig, args)
        assert all(after.data[i].line.width == 0 for i in edges)


def test_heatmap_legend_shows_the_bands_themselves(tmp_path):
    """The band entries belong to the filled traces, grouped with their edge."""
    from meg_qc.plotting.universal_plots import boxplot_epoched_xaxis_channels_csv
    fig = boxplot_epoched_xaxis_channels_csv(str(_std_table(tmp_path / "std.tsv")), "mag", "stds")[0].content
    entries = {tr.name: tr for tr in fig.data
               if tr.showlegend is not False and str(tr.name).startswith("Middle")}
    assert set(entries) == {"Middle 90% of channels", "Middle 50% of channels"}
    for tr in entries.values():
        assert tr.fill == "tonexty" and tr.legendgroup in {"band90", "band50"}


def test_plot_settings_matches_buttons_whatever_their_padding():
    """The dataset heatmap pads its thickness labels ("   3   ") to widen the
    buttons; a choice saved as "3" must still find them, and the panel must
    not list "3" twice."""
    from meg_qc.plotting.meg_qc_plots import _PLOT_SETTINGS_JS as js
    assert "function __psLabel(x) { return String(x || '').trim(); }" in js
    assert "__psLabel(btns[i] && btns[i].label) === __psLabel(label)" in js
    assert "var lab = __psLabel(b && b.label);" in js
    assert "=== label) return btns[i]" not in js
