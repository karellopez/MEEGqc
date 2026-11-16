"""Runs executed THROUGH the GUI, on real data.

The other real-data tests drive the console scripts. These press the GUI's own
Run buttons: MainWindow -> Worker (a QProcess running worker_entry) -> the
dispatcher, exactly as a user's click does, so they cover the GUI-to-pipeline
handover (the JSON payload, the subprocess, parallel workers started from it)
on every OS and Python the suite runs on.

Dialogs are replaced by recorders: a modal QMessageBox would block an offscreen
run forever. Preferences go to the session's own store (conftest.py).
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytestmark = [pytest.mark.realdata, pytest.mark.gui]

#: Generous: the slowest CI machine runs a 10 s recording in well under this.
TIMEOUT_S = 900


@pytest.fixture(scope="module")
def qapp():
    pytest.importorskip("PyQt6")
    from PyQt6.QtWidgets import QApplication
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def window(qapp, monkeypatch):
    from PyQt6.QtWidgets import QMessageBox
    from meg_qc.miscellaneous.GUI.megqcGUI import MainWindow
    dialogs = []

    def _record(kind):
        def _show(*args, **kwargs):
            dialogs.append((kind, [str(a) for a in args[1:3]]))
            return QMessageBox.StandardButton.Ok
        return _show

    for kind in ("warning", "critical", "information", "question"):
        monkeypatch.setattr(QMessageBox, kind, _record(kind))
    w = MainWindow()
    w.dialogs = dialogs
    yield w
    for worker in list(getattr(w, "workers", {}).values()):
        worker.stop()
    w.close()


def _press(window, button: str, dataset: Path, out_dir: Path, config: Path, n_jobs: int):
    """Set the inputs, press a Run button, wait for the worker; return its log."""
    from PyQt6.QtCore import QEventLoop, QTimer
    w = window
    w._collect_dataset_paths = lambda: [str(dataset)]
    w.derivatives_dir.setText(str(out_dir))
    w.global_config_path = str(config)
    w._set_analysis_mode("non-profile")
    w.chk_all_cores.setChecked(False)
    w.jobs.setValue(min(n_jobs, w.jobs.maximum()))
    w.plot_jobs.setValue(min(n_jobs, w.plot_jobs.maximum()))

    key = {"start_calc": "calc", "start_all": "all"}[button]
    getattr(w, button)()
    assert not w.dialogs, f"the GUI refused to start: {w.dialogs}"
    worker = w.workers[key]
    log, state = [], {"error": None, "done": False}
    loop = QEventLoop()
    worker.output_ready.connect(log.append)
    worker.finished.connect(lambda: (state.update(done=True), loop.quit()))
    worker.error.connect(lambda e: (state.update(error=e), loop.quit()))
    QTimer.singleShot(TIMEOUT_S * 1000, loop.quit)
    if not state["done"]:
        loop.exec()
    text = "".join(log)
    assert state["done"] or state["error"], f"no result after {TIMEOUT_S} s:\n{text[-3000:]}"
    assert state["error"] is None, f"worker error {state['error']}:\n{text[-3000:]}"
    return text


def test_calculation_through_the_gui_runs_recordings_in_parallel(
        window, one_meg, isolated_dataset, fast_config, tmp_path):
    """Run Calculation with n_jobs 2 and the guard off: two workers from the GUI."""
    _, src = one_meg
    ds = isolated_dataset(src)
    out = tmp_path / "out"
    window.chk_mem_guard.setChecked(False)
    log = _press(window, "start_calc", ds, out, fast_config, n_jobs=2)
    assert "memory guard off" in log
    calc = list(out.rglob("calculation"))
    assert calc, f"no calculation folder under {out}:\n{log[-2000:]}"
    written = [p for p in calc[0].rglob("*") if p.is_file()]
    assert written, f"calculation wrote nothing:\n{log[-2000:]}"
    for marker in ("Traceback", "TerminatedWorkerError", "BrokenProcessPool"):
        assert marker not in log, log[-3000:]


def test_run_all_through_the_gui_writes_reports(
        window, one_eeg, isolated_dataset, fast_config, tmp_path):
    """Run ALL (calculation, GQI, every report) from the GUI on an EEG dataset."""
    _, src = one_eeg
    ds = isolated_dataset(src)
    out = tmp_path / "out"
    log = _press(window, "start_all", ds, out, fast_config, n_jobs=1)
    reports = list(out.rglob("*.html"))
    assert reports, f"Run ALL wrote no HTML report under {out}:\n{log[-3000:]}"
    assert "Traceback" not in log, log[-3000:]
