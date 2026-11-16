"""MEGnet through its plugin (plugins/meegqc-megnet), on real recordings.

Skipped when the plugin is not installed: MEEGqc itself no longer depends on
MEGnet. The testbench installs it in every environment
(``meegqc-plugins install plugins/meegqc-megnet``).
"""
from __future__ import annotations

import pytest

from meg_qc.plugins.registry import find

pytestmark = pytest.mark.realdata

if find("megnet") is None:
    pytest.skip("the MEGnet plugin is not installed (or MEEGQC_PLUGINS=0)",
                allow_module_level=True)


def test_meegqc_finds_the_plugin():
    from meg_qc.plugins.registry import discover
    found = {f.name: f for f in discover(refresh=True)}
    assert "megnet" in found, found
    assert found["megnet"].plugin is not None, found["megnet"].error


def test_megnet_is_not_run_on_an_eeg_recording(one_eeg, isolated_dataset, full_config, cli):
    """It used to start MEGnet's ICA on EEG and fail inside megnet-neuro."""
    ds = isolated_dataset(one_eeg[1])
    r = cli(["run-meegqc", "--inputdata", str(ds), "--config", str(full_config),
             "--n_jobs", "1"])
    out = r.stdout + r.stderr
    assert r.returncode == 0, out[-3000:]
    assert "MEGnet skipped - it needs MEG channels" in out
    assert "Computing MEGnet reference signals" not in out


def test_megnet_is_not_run_on_a_short_recording(one_meg, isolated_dataset, full_config, cli):
    """The bundle's recordings are 10 s; MEGnet needs two 60 s windows."""
    ds = isolated_dataset(one_meg[1])
    r = cli(["run-meegqc", "--inputdata", str(ds), "--config", str(full_config),
             "--n_jobs", "1"])
    out = r.stdout + r.stderr
    assert r.returncode == 0, out[-3000:]
    assert "MEGnet skipped - it needs at least 105 s of data" in out


def test_megnet_writes_its_references_and_the_report_shows_them(
        megnet_meg_dataset, isolated_dataset, _megnet_config_factory, cli):
    """A long MEG recording: MEGnet's ECG/EOG references are written beside
    the recorded ones (independent, its default) and named in the report."""
    name, path = megnet_meg_dataset
    ds = isolated_dataset(path)
    r = cli(["run-meegqc", "--inputdata", str(ds), "--config", str(_megnet_config_factory),
             "--n_jobs", "1"])
    out = r.stdout + r.stderr
    assert r.returncode == 0, f"{name}:\n{out[-4000:]}"
    if "MEGnet model weights unavailable" in out:
        pytest.skip("MEGnet's model weights could not be downloaded here")

    calc = ds / "derivatives" / "MEEGqc" / "calculation"
    ecg = list(calc.rglob("*_desc-ECGchannelmegnet_*.tsv"))
    assert ecg, f"{name}: no MEGnet ECG reference written:\n{out[-4000:]}"
    header, first = ecg[0].read_text().splitlines()[:2]
    row = dict(zip(header.split("\t"), first.split("\t")))
    assert row["signal_source"] == "megnet"
    assert row["signal_source_label"] == "MEGnet"
    assert row["synthetic_component_ids"]

    r = cli(["run-meegqc-plotting", "--inputdata", str(ds), "--qa-subject"])
    assert r.returncode == 0, (r.stdout + r.stderr)[-3000:]
    reports = list((ds / "derivatives" / "MEEGqc" / "reports").rglob("*.html"))
    assert reports
    assert any("MEGnet reference info" in p.read_text(encoding="utf-8", errors="replace")
               for p in reports)
