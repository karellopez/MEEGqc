"""ECG/EOG reference signals: ranking against any reference source and the QC
report's provenance.

Each test pins a defect found in the 2026-11 review:

* a plugin's reference (``correlation_<plugin>``, e.g. MEGnet's) was accepted
  by the ECG/EOG loops but rejected by the functions that write the simple
  metrics, so a run whose primary reference came from a plugin failed;
* the QC report matched ``desc-ECGchannel*`` and so also read a plugin's
  independent reference (``desc-ECGchannelmegnet``), showing the wrong source.

MEGnet's own settings and component choice are tested in its plugin
(tests/test_megnet_plugin_code.py).
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from meg_qc.calculation.metrics import ECG_EOG_meg_qc as ee

def _ch(name, r, p=0.01):
    return SimpleNamespace(name=name, corr_coef=r, p_value=p, main_peak_magnitude=abs(r))


# ── ranking against a reference, whatever its source ──────────────────────

@pytest.mark.parametrize("method", ["correlation_recorded", "correlation_reconstructed",
                                    "correlation_megnet", "correlation_anyplugin"])
def test_every_correlation_reference_ranks_channels_by_strength(method):
    out = ee.make_dict_global_ECG_EOG([_ch("A", 0.2), _ch("B", -0.9)], method)
    assert list(out["details"]) == ["B", "A"]


def test_an_unknown_method_is_named_in_the_error():
    with pytest.raises(ValueError, match="nope"):
        ee.make_dict_global_ECG_EOG([_ch("A", 0.1)], "nope")


def test_simple_metric_with_a_plugin_reference_names_it():
    ranked = {"mag": [_ch("A", 0.5)], "grad": [], "eeg": []}
    metric = ee.make_simple_metric_ECG_EOG(
        ranked, ["mag", "grad"], "ecg", {"mag": "", "grad": "no grad", "eeg": ""},
        "correlation_megnet")
    assert "(megnet)" in str(metric)


@pytest.mark.parametrize("method, source", [
    ("correlation_recorded", "recorded"), ("mean_threshold", "recorded"),
    ("no_ecg", "recorded"), ("correlation_reconstructed", "mne_reconstructed"),
    ("reconstructed-bad", "mne_reconstructed"), ("correlation_megnet", "megnet"),
    ("correlation_anyplugin", "anyplugin")])
def test_the_source_of_each_method(method, source):
    assert ee._source_from_method(method) == source


# ── the QC report's provenance ────────────────────────────────────────────

def _reference_tsv(folder: Path, desc: str, source: str) -> None:
    pd.DataFrame({"ECG": [1.0, 2.0], "signal_source": [source, None]}).to_csv(
        folder / f"sub-01_task-rest_desc-{desc}_meg.tsv", sep="\t", index=False)


def test_qc_provenance_reads_the_primary_reference_not_a_plugins(tmp_path):
    from meg_qc.plotting.meg_qc_dataset_qc_plots import _attach_signal_provenance
    calc = tmp_path / "calculation" / "meg" / "sub-01"
    calc.mkdir(parents=True)
    _reference_tsv(calc, "ECGchannel", "recorded")
    _reference_tsv(calc, "ECGchannelmegnet", "megnet")   # a plugin's independent one
    out = _attach_signal_provenance(pd.DataFrame({"subject": ["sub-01"], "task": ["rest"]}),
                                    tmp_path)
    assert out.loc[0, "ecg_signal_source"] == "recorded"


# ── timestamps ───────────────────────────────────────────────────────────

def test_progress_timestamps_keep_their_format():
    import re
    from meg_qc.calculation.meg_qc_pipeline import _utc_timestamp
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", _utc_timestamp())
