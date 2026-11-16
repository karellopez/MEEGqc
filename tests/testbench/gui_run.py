"""Run MEEGqc THROUGH the GUI, offscreen, exactly as a click on a Run button does.

MainWindow -> start_calc / start_all -> Worker (QProcess) -> worker_entry ->
the dispatcher. Used by testbench.py for full-length recordings; the pytest
suite has its own version (tests/realdata/test_gui_runs.py).

    python gui_run.py --dataset DS --out OUT [--subs 001 002] [--button all]
                      [--n-jobs 2] [--guard on|off] [--level balanced]
                      [--log LOG]

Preferences go to MEEGQC_SETTINGS_DIR (set it; testbench.py does), never to the
user's own store. Modal dialogs are recorded instead of shown. Exit code 0 when
the worker finished without error.
"""
from __future__ import annotations

import argparse
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MEGQC_NO_UPDATE_CHECK", "1")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--subs", nargs="*", default=None)
    ap.add_argument("--button", choices=["calc", "all"], default="all")
    ap.add_argument("--n-jobs", type=int, default=1)
    ap.add_argument("--guard", choices=["on", "off"], default="on")
    ap.add_argument("--level", default="balanced")
    ap.add_argument("--log", default=None)
    ap.add_argument("--timeout", type=int, default=6 * 3600)
    a = ap.parse_args()

    if not os.environ.get("MEEGQC_SETTINGS_DIR"):
        sys.exit("set MEEGQC_SETTINGS_DIR so the run cannot touch your own preferences")

    from PyQt6.QtCore import QEventLoop, QTimer
    from PyQt6.QtWidgets import QApplication, QMessageBox
    # Held for the whole run: a QApplication nothing refers to is destroyed.
    _app = QApplication.instance() or QApplication([])
    from meg_qc.miscellaneous.GUI.megqcGUI import MainWindow

    dialogs = []
    for kind in ("warning", "critical", "information", "question"):
        setattr(QMessageBox, kind,
                staticmethod(lambda *x, _k=kind: (dialogs.append((_k, x[1:3])),
                                                  QMessageBox.StandardButton.Ok)[1]))

    w = MainWindow()
    dataset = os.path.abspath(a.dataset)
    w._collect_dataset_paths = lambda: [dataset]
    if a.subs:
        w._collect_dataset_sub_overrides = lambda: {dataset: list(a.subs)}
    w.derivatives_dir.setText(os.path.abspath(a.out))
    w._set_analysis_mode("non-profile")
    w.chk_all_cores.setChecked(False)
    w.jobs.setValue(min(a.n_jobs, w.jobs.maximum()))
    w.plot_jobs.setValue(min(a.n_jobs, w.plot_jobs.maximum()))
    w.mem_level.setCurrentIndex(w.mem_level.findData(a.level))
    w.chk_mem_guard.setChecked(a.guard == "on")
    print("GUI settings:", w._memory_guard_kwargs(), "n_jobs", w.jobs.value(), flush=True)

    {"calc": w.start_calc, "all": w.start_all}[a.button]()
    if dialogs:
        print("GUI refused to start:", dialogs, flush=True)
        return 2
    worker = w.workers[a.button]
    log = open(a.log, "w", encoding="utf-8") if a.log else None
    state = {"error": None, "done": False}
    loop = QEventLoop()
    if log:
        worker.output_ready.connect(lambda t: (log.write(t), log.flush()))
    worker.finished.connect(lambda: (state.update(done=True), loop.quit()))
    worker.error.connect(lambda e: (state.update(error=e), loop.quit()))
    QTimer.singleShot(a.timeout * 1000, loop.quit)
    loop.exec()
    if log:
        log.close()
    if dialogs:
        print("dialogs during the run:", dialogs, flush=True)
    print("done:", state["done"], "worker error:", state["error"], flush=True)
    return 0 if state["done"] and not state["error"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
