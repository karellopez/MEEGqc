"""Run ALL and the plotting step on datasets with nothing to plot.

Both used to end in a traceback: a dataset with no recordings stopped the
calculation with ``KeyError: 'subject'``, and a dataset whose every recording
failed made the reports raise ``FileNotFoundError``. They now say why and
finish, and a dataset that did calculate is still plotted.
"""
from __future__ import annotations

import json
from pathlib import Path

import meg_qc
from meg_qc.test import (
    _has_calculation_results,
    run_all_dispatch,
    run_plotting_dispatch,
)

SETTINGS = Path(meg_qc.__file__).parent / "settings"


def _dataset(root: Path, name: str, recording: bytes | None) -> Path:
    ds = root / name
    (ds / "sub-01" / "meg").mkdir(parents=True)
    (ds / "dataset_description.json").write_text(
        json.dumps({"Name": name, "BIDSVersion": "1.9.0"}))
    if recording is not None:
        (ds / "sub-01" / "meg" / "sub-01_task-rest_meg.fif").write_bytes(recording)
    return ds


def _run_all(datasets, log):
    run_all_dispatch(
        dataset_paths=[str(d) for d in datasets],
        config_file_path=str(SETTINGS / "settings.ini"),
        internal_config_file_path=str(SETTINGS / "settings_internal.ini"),
        calc_n_jobs=1,
        plot_njobs=1,
        logger=log.append,
    )


def test_run_all_on_a_dataset_without_recordings(tmp_path):
    log: list[str] = []
    _run_all([_dataset(tmp_path, "ds_empty", None)], log)
    assert "Nothing to plot: no dataset has calculated results." in log


def test_run_all_when_every_recording_fails(tmp_path):
    ds = _dataset(tmp_path, "ds_broken", b"not a FIF file" * 100)
    log: list[str] = []
    _run_all([ds], log)
    why = [line for line in log if line.startswith("No reports for")]
    assert why and "excluded_subjects_errors.json" in why[0]
    assert "Nothing to plot: no dataset has calculated results." in log


def test_plotting_skips_only_the_dataset_without_results(tmp_path, monkeypatch):
    """One dataset calculated, one not: only the second is reported as skipped,
    the first still reaches the subject reports."""
    good = _dataset(tmp_path, "ds_good", None)
    bad = _dataset(tmp_path, "ds_bad", None)
    calc = good / "derivatives" / "MEEGqc" / "calculation" / "sub-01"
    calc.mkdir(parents=True)
    (calc / "sub-01_desc-STDs_meg.tsv").write_text("x\n1\n")

    plotted = []
    monkeypatch.setattr("meg_qc.plotting.meg_qc_plots.make_plots_meg_qc",
                        lambda ds, **kw: plotted.append(Path(ds).name))
    log: list[str] = []
    modes = run_plotting_dispatch([str(good), str(bad)], qa_subject=True,
                                  qa_multi_dataset=True, logger=log.append)
    assert plotted == ["ds_good"]
    assert any(line.startswith(f"No reports for {bad}") for line in log)
    assert modes["qa_multi_dataset"] is False


def test_an_empty_calculation_folder_is_not_a_result(tmp_path):
    root = tmp_path / "MEEGqc"
    (root / "calculation").mkdir(parents=True)
    assert not _has_calculation_results(str(root))
    (root / "calculation" / "sub-01").mkdir()
    (root / "calculation" / "sub-01" / "a.tsv").write_text("")
    assert _has_calculation_results(str(root))
