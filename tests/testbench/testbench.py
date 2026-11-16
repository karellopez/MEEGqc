#!/usr/bin/env python3
"""MEEGqc testbench: one folder per machine to test the newest MEEGqc on every
supported Python, through the CLI AND the GUI, with screenshots to look at.

    python testbench.py setup [--root DIR] [--pythons 3.10 ... 3.14] [--bundle DIR]
    python testbench.py round [--root DIR] [--pythons ...] [--full] [--label NAME]
    python testbench.py start [round options]      (Linux/macOS: the round in tmux)
    python testbench.py status [--root DIR]

``setup`` builds (or refreshes) the folder, installing the newest versions
MEEGqc allows (``--upgrade``, as a new user gets) plus the MEGnet plugin from
the repository, so its tests run:

    <root>/MEEGqc/                    a git clone of the repo under test
    <root>/envs/py310 .. py314/       one uv environment per Python (no admin)
    <root>/fixtures/meegqc_test_data  the 10 s fixture bundle (MEEGQC_TEST_DATA)
    <root>/full_data.json             the light full-length recordings to run
    <root>/runs/                      one folder per round: logs, results, shots
    <root>/.vscode/, *.code-workspace open <root> in VS Code

``round`` runs, for every Python: the whole pytest suite (unit, GUI, and the
real-data tests on the bundle, which drive the CLI and the GUI). With
``--full`` it also runs each recording of full_data.json through the CLI and
through the GUI (Run ALL), screenshots the GUI and the reports, and writes
``summary.md``. ``--without-plugins`` adds one more pass of the suite with
plugins switched off (MEEGQC_PLUGINS=0), as most users run MEEGqc.

``start`` runs a round in a detached tmux session (``meegqc-round``), so it
survives a dropped connection; ``tmux attach -t meegqc-round`` shows it live.
Windows has no tmux: run ``round`` there in a terminal.

Standard library only, so any Python 3.8+ can run it. Never uses the user's
GUI preferences (MEEGQC_SETTINGS_DIR points into the round's folder).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path

IS_WIN = os.name == "nt"
IS_MAC = sys.platform == "darwin"
PYTHONS = ["3.10", "3.11", "3.12", "3.13", "3.14"]
REPO_URL = "https://github.com/karellopez/MEEGqc.git"
BUNDLE_URL = os.environ.get("MEEGQC_FIXTURES_URL",
                            "https://cloud.uol.de/s/5JnCs6FxSnejjD6/download")
HERE = Path(__file__).resolve().parent

#: Where the full-length test data lives on the lab's machines.
FULL_DATA_ROOTS = [
    Path.home() / "Desktop/Development/MEG-QC_dataset/MEGqc_testing/MEGqc_full_test_data",
    Path.home() / "Downloads/datasets/MEGqc_full_test_data",
    Path.home() / "Development/datasets/MEGqc/MEGqc_full_test_data",
    # The Mac keeps the datasets themselves (read only; outputs go to runs/).
    Path.home() / "Development/datasets/MEGqc",
]
#: Light recordings, one per format. Never ds_mous (huge) and never iEEG
#: (not supported yet). (relative dataset path, format label)
FULL_DATA_PICKS = [
    ("meg_datasets/ds006035", "MEG FIF"),
    ("meg_datasets/ds000246", "MEG CTF"),
    ("eeg_datasets/ds_007262", "EEG BrainVision"),
    ("eeg_datasets/ds007338", "EEG EDF"),
    ("eeg_datasets/ds007315", "EEG EEGLAB"),
]
#: The Python that runs the full-length recordings on each OS.
FULL_PYTHON = "3.14" if IS_WIN else ("3.12" if IS_MAC else "3.13")


# ── small helpers ───────────────────────────────────────────────────────────
def say(*msg) -> None:
    print("[testbench]", *msg, flush=True)


def run(cmd, *, cwd=None, env=None, log=None, check=True, timeout=None,
        echo=True) -> int:
    """Run a command, streaming to the console (unless ``echo`` is False, for
    runs in parallel) and, optionally, to a log file."""
    say("$", " ".join(str(c) for c in cmd))
    with subprocess.Popen([str(c) for c in cmd], cwd=cwd, env=env,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          text=True, encoding="utf-8", errors="replace") as p:
        out = open(log, "a", encoding="utf-8") if log else None
        try:
            for line in p.stdout:
                if echo:
                    sys.stdout.write(line)
                if out:
                    out.write(line)
            rc = p.wait(timeout=timeout)
        finally:
            if out:
                out.close()
    if check and rc != 0:
        raise SystemExit(f"command failed ({rc}): {' '.join(str(c) for c in cmd)}")
    return rc


def find_uv() -> str:
    for cand in (shutil.which("uv"), Path.home() / ".local/bin/uv",
                 Path.home() / ".local/bin/uv.exe", Path.home() / ".cargo/bin/uv",
                 Path("C:/Program Files/uv/uv.exe")):
        if cand and Path(cand).is_file():
            return str(cand)
    raise SystemExit("uv not found. Install it without admin rights: "
                     "https://docs.astral.sh/uv/getting-started/installation/")


def env_dir(root: Path, py: str) -> Path:
    return root / "envs" / f"py{py.replace('.', '')}"


def env_python(root: Path, py: str) -> Path:
    d = env_dir(root, py)
    return d / ("Scripts/python.exe" if IS_WIN else "bin/python")


def env_bin(root: Path, py: str) -> Path:
    return env_dir(root, py) / ("Scripts" if IS_WIN else "bin")


def works(python: str) -> bool:
    try:
        return subprocess.run([python, "-c", "import sys"], capture_output=True,
                              timeout=60).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def interpreter(uv: str, py: str) -> str:
    """A usable interpreter for ``py``, installed by uv if needed.

    On Windows an SSH (elevated) session may refuse uv's minor-version folders,
    which are junctions ("untrusted mount point", os error 448); the
    full-version folders work, so every candidate is tried.
    """
    for attempt in range(2):
        out = subprocess.run([uv, "python", "list", py, "--only-installed",
                              "--output-format", "json"],
                             capture_output=True, text=True)
        try:
            entries = json.loads(out.stdout or "[]")
        except json.JSONDecodeError:
            entries = []
        def rank(e) -> int:
            # A real install lives in a folder named after its full version
            # (cpython-3.10.21-...). Shortcuts (~/.local/bin/python3.10.exe,
            # cpython-3.10-...) are links an SSH session on Windows may refuse.
            p = Path(str(e.get("path", "")))
            real = f"-{e.get('version', '')}-" in p.parent.name
            return (0 if real else 1) + (2 if ".local" in p.parts else 0)

        for e in sorted(entries, key=rank):
            path = e.get("path")
            if path and not Path(path).is_absolute():
                path = str(Path.home() / path)
            if path and works(path):
                return path
        if attempt == 0:
            run([uv, "python", "install", py])
    raise SystemExit(f"no working Python {py} found or installable with uv")


# ── setup ───────────────────────────────────────────────────────────────────
def setup(a) -> None:
    root = Path(a.root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    uv = find_uv()
    repo = root / "MEEGqc"
    if not (repo / ".git").is_dir():
        run(["git", "clone", a.repo, str(repo)])
    for py in a.pythons:
        envpy = env_python(root, py)
        if not envpy.is_file():
            # Python's own venv, not "uv venv": on Windows uv gives each
            # environment a uv launcher that starts the base Python through
            # uv's minor-version folder, a junction an SSH session runs
            # elevated and refuses to traverse ("untrusted mount point", os
            # error 448). venv's standard launcher points at the real folder.
            # uv still does the installing.
            run([interpreter(uv, py), "-m", "venv", str(env_dir(root, py))])
        if sys.platform.startswith("linux"):
            # The default Linux torch wheel brings several GB of CUDA libraries.
            run([uv, "pip", "install", "--python", str(envpy), "--upgrade", "torch",
                 "--index-url", "https://download.pytorch.org/whl/cpu"])
        run([uv, "pip", "install", "--python", str(envpy), "--upgrade", "-e", str(repo), "pytest"])
        plugin = repo / "plugins" / "meegqc-megnet"
        if plugin.is_dir():
            run([uv, "pip", "install", "--python", str(envpy), "--upgrade", "-e", str(plugin)])
    setup_bundle(root, a.bundle)
    write_full_data(root)
    write_vscode(root, a.pythons)
    write_readme(root)
    say("ready:", root)


def setup_bundle(root: Path, local: str | None) -> None:
    data = root / "fixtures" / "meegqc_test_data"
    if (data / "MANIFEST.json").is_file():
        return
    data.parent.mkdir(parents=True, exist_ok=True)
    if local:
        say("copying the fixture bundle from", local)
        shutil.copytree(local, data)
        return
    tgz = data.parent / "bundle.tar.gz"
    run(["curl", "-fSL", "-o", str(tgz), BUNDLE_URL])
    with tarfile.open(tgz) as t:
        if sys.version_info >= (3, 12):
            t.extractall(data.parent, filter="data")
        else:
            t.extractall(data.parent)
    tgz.unlink()
    if not (data / "MANIFEST.json").is_file():
        raise SystemExit(f"the bundle did not unpack to {data}")


def write_full_data(root: Path) -> None:
    path = root / "full_data.json"
    if path.is_file():
        return
    entries = []
    base = next((r for r in FULL_DATA_ROOTS if r.is_dir()), None)
    for rel, label in FULL_DATA_PICKS:
        ds = base / rel if base else None
        if not ds or not ds.is_dir():
            continue
        subs = sorted(p.name[4:] for p in ds.glob("sub-*") if p.is_dir())
        if subs:
            entries.append({"label": label, "dataset": str(ds), "subs": subs[:1]})
    path.write_text(json.dumps({"python": FULL_PYTHON, "n_jobs": 2,
                                "recordings": entries}, indent=2))
    say(f"full_data.json: {len(entries)} recording(s); edit it to change the selection")


def write_vscode(root: Path, pythons) -> None:
    vs = root / ".vscode"
    vs.mkdir(exist_ok=True)
    default_py = FULL_PYTHON if FULL_PYTHON in pythons else pythons[-1]
    rel = lambda py: str(env_python(root, py).relative_to(root)).replace("\\", "/")
    data = "${workspaceFolder}/fixtures/meegqc_test_data"
    settings = {
        "python.defaultInterpreterPath": "${workspaceFolder}/" + rel(default_py),
        "python.testing.pytestEnabled": True,
        "python.testing.cwd": "${workspaceFolder}/MEEGqc",
        "python.testing.pytestArgs": ["tests", "-p", "no:cacheprovider"],
        "python.envFile": "${workspaceFolder}/.vscode/testbench.env",
        "files.exclude": {"**/__pycache__": True, "envs": True},
        "search.exclude": {"envs": True, "fixtures": True, "runs": True},
    }
    (vs / "settings.json").write_text(json.dumps(settings, indent=2))
    (vs / "testbench.env").write_text(
        f"MEEGQC_TEST_DATA={root / 'fixtures' / 'meegqc_test_data'}\n"
        "QT_QPA_PLATFORM=offscreen\n")
    tb = "${workspaceFolder}/MEEGqc/tests/testbench/testbench.py"
    tasks = [{"label": "Testbench: round, all Pythons", "type": "shell",
              "command": "${config:python.defaultInterpreterPath}",
              "args": [tb, "round", "--root", "${workspaceFolder}"], "problemMatcher": []},
             {"label": "Testbench: round + full-length recordings", "type": "shell",
              "command": "${config:python.defaultInterpreterPath}",
              "args": [tb, "round", "--full", "--root", "${workspaceFolder}"],
              "problemMatcher": []},
             *([] if IS_WIN else [
                 {"label": "Testbench: full round in tmux (keeps running if disconnected)",
                  "type": "shell", "command": "${config:python.defaultInterpreterPath}",
                  "args": [tb, "start", "--root", "${workspaceFolder}", "--full"],
                  "problemMatcher": []}]),
             {"label": "Testbench: refresh environments", "type": "shell",
              "command": "${config:python.defaultInterpreterPath}",
              "args": [tb, "setup", "--root", "${workspaceFolder}"], "problemMatcher": []}]
    for py in pythons:
        tasks.append({"label": f"pytest: Python {py}", "type": "shell",
                      "command": "${workspaceFolder}/" + rel(py),
                      "args": ["-m", "pytest", "tests", "-q", "-p", "no:cacheprovider"],
                      "options": {"cwd": "${workspaceFolder}/MEEGqc",
                                  "env": {"MEEGQC_TEST_DATA": data,
                                          "QT_QPA_PLATFORM": "offscreen"}},
                      "problemMatcher": []})
    (vs / "tasks.json").write_text(json.dumps({"version": "2.0.0", "tasks": tasks}, indent=2))
    launch = {"version": "0.2.0", "configurations": [
        {"name": "MEEGqc GUI", "type": "debugpy", "request": "launch",
         "module": "meg_qc.miscellaneous.GUI.megqcGUI", "cwd": "${workspaceFolder}",
         "justMyCode": False},
        {"name": "CLI: calculation on one fixture dataset", "type": "debugpy",
         "request": "launch", "module": "meg_qc.test", "cwd": "${workspaceFolder}",
         "args": ["--inputdata", data + "/ds_meg_example_1", "--n_jobs", "2",
                  "--derivatives_output", "${workspaceFolder}/runs/manual"],
         "justMyCode": False}]}
    (vs / "launch.json").write_text(json.dumps(launch, indent=2))
    (root / "MEEGqc_testbench.code-workspace").write_text(json.dumps(
        {"folders": [{"path": "."}], "settings": {}}, indent=2))


def write_readme(root: Path) -> None:
    (root / "README.md").write_text(f"""# MEEGqc testbench ({platform.system()})

Open this folder in VS Code (File > Open Workspace from File >
`MEEGqc_testbench.code-workspace`). The code under test is `MEEGqc/`, a git
clone: Source Control shows exactly which changes are being tested.

| Folder | What |
|---|---|
| `MEEGqc/` | the code under test |
| `envs/py310` .. `py314` | one environment per Python, MEEGqc installed editable |
| `fixtures/meegqc_test_data` | the 10 s fixture bundle (every format) |
| `full_data.json` | the light full-length recordings (edit to change) |
| `runs/` | one folder per test round: `summary.md`, logs, screenshots |

Terminal > Run Task offers a round on every Python, a round with the
full-length recordings, and pytest on one Python. Run and Debug starts the GUI
or a CLI calculation with the default environment.

    python MEEGqc/tests/testbench/testbench.py round            # every Python
    python MEEGqc/tests/testbench/testbench.py round --full     # + full data
    python MEEGqc/tests/testbench/testbench.py status
""")


# ── round ───────────────────────────────────────────────────────────────────
def junit_counts(xml: Path) -> dict:
    try:
        r = ET.parse(xml).getroot()
    except (OSError, ET.ParseError):
        return {"tests": 0, "failures": 0, "errors": 1, "skipped": 0}
    suites = [r] if r.tag == "testsuite" else list(r)
    tot = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
    for s in suites:
        for k in tot:
            tot[k] += int(s.get(k, 0))
    return tot


def round_(a) -> int:
    root = Path(a.root).expanduser().resolve()
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    rdir = root / "runs" / (stamp + (f"_{a.label}" if a.label else ""))
    rdir.mkdir(parents=True)
    settings_dir = rdir / "gui_settings"
    data = root / "fixtures" / "meegqc_test_data"
    pythons = [p for p in a.pythons if env_python(root, p).is_file()]
    results = {"machine": platform.node(), "os": platform.platform(),
               "started": stamp, "repo": git_state(root / "MEEGqc"),
               "pytest": {}, "full": [], "shots": []}

    def one_python(py: str) -> None:
        pdir = rdir / f"py{py.replace('.', '')}"
        pdir.mkdir()
        # Each Python gets its own preferences folder, so parallel runs never
        # share a store.
        env = base_env(root, py, settings_dir / f"py{py.replace('.', '')}")
        env["MEEGQC_TEST_DATA"] = str(data)
        if a.jobs > 1:
            # Every numerical library starts one thread per CPU by default, so
            # five Pythons at once ran 5 x 12 threads on 12 CPUs and timed out.
            env.update(thread_caps(a.jobs))
        t0 = time.time()
        rc = run([env_python(root, py), "-m", "pytest", *a.tests, "-q",
                  "-p", "no:cacheprovider", f"--basetemp={pdir / 'tmp'}",
                  f"--junitxml={pdir / 'junit.xml'}"],
                 cwd=root / "MEEGqc", env=env, log=pdir / "pytest.log", check=False,
                 echo=a.jobs == 1)
        results["pytest"][py] = {"exit": rc, "seconds": round(time.time() - t0),
                                 **junit_counts(pdir / "junit.xml")}
        shutil.rmtree(pdir / "tmp", ignore_errors=True)
        save(rdir, results)
        say(f"Python {py}: {results['pytest'][py]}")

    todo = [] if a.skip_tests else pythons
    if a.without_plugins and todo:
        # MEEGqc as most users have it: no plugin installed.
        py = todo[0]
        pdir = rdir / f"py{py.replace('.', '')}_without_plugins"
        pdir.mkdir()
        env = base_env(root, py, settings_dir / pdir.name)
        env["MEEGQC_TEST_DATA"] = str(data)
        env["MEEGQC_PLUGINS"] = "0"
        t0 = time.time()
        rc = run([env_python(root, py), "-m", "pytest", *a.tests, "-q",
                  "-p", "no:cacheprovider", f"--basetemp={pdir / 'tmp'}",
                  f"--junitxml={pdir / 'junit.xml'}"],
                 cwd=root / "MEEGqc", env=env, log=pdir / "pytest.log", check=False)
        results["pytest"][f"{py} without plugins"] = {
            "exit": rc, "seconds": round(time.time() - t0), **junit_counts(pdir / "junit.xml")}
        shutil.rmtree(pdir / "tmp", ignore_errors=True)
        save(rdir, results)
    if a.jobs > 1:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=a.jobs) as pool:
            list(pool.map(one_python, todo))
    else:
        for py in todo:
            one_python(py)

    if a.full:
        cfg = json.loads((root / "full_data.json").read_text())
        py = a.full_python or cfg.get("python", FULL_PYTHON)
        env = base_env(root, py, settings_dir)
        for rec in cfg["recordings"]:
            for how in ("cli", "gui"):
                results["full"].append(full_run(root, rdir, py, env, rec, how,
                                                cfg.get("n_jobs", 2)))
                save(rdir, results)
        results["shots"] = shots(root, rdir, py, env)

    save(rdir, results)
    write_summary(rdir, results)
    bad = [p for p, r in results["pytest"].items() if r["exit"] != 0]
    bad += [f"{r['label']} {r['how']}" for r in results["full"] if not r["ok"]]
    notes = sum(len(r.get("caught", [])) for r in results["full"])
    say("round finished:", rdir, "| problems:", ", ".join(bad) or "none",
        f"| caught errors: {notes}" if notes else "")
    return 1 if bad else 0


def base_env(root: Path, py: str, settings_dir: Path) -> dict:
    env = dict(os.environ)
    env["PATH"] = str(env_bin(root, py)) + os.pathsep + env.get("PATH", "")
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["MEGQC_NO_UPDATE_CHECK"] = "1"
    env["MEEGQC_SETTINGS_DIR"] = str(settings_dir)
    env["PYTHONIOENCODING"] = "utf-8"
    env.pop("VIRTUAL_ENV", None)
    return env


def thread_caps(jobs: int) -> dict:
    """Thread limits for ``jobs`` processes sharing this machine's CPUs."""
    n = str(max(1, (os.cpu_count() or 1) // jobs))
    return {k: n for k in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS",
                           "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS",
                           "VECLIB_MAXIMUM_THREADS", "NUMBA_NUM_THREADS")}


#: Signs that a run itself broke, as opposed to one step catching an error.
FATAL = ("TerminatedWorkerError", "BrokenProcessPool", "MemoryError")


def caught_errors(text: str) -> list:
    """The exception line of every traceback in a log, once each.

    MEEGqc catches a failing metric and carries on, so a traceback in the log
    does not fail the run; it is listed so it gets looked at.
    """
    found, lines = [], text.splitlines()
    for i, line in enumerate(lines):
        if not line.startswith("Traceback (most recent call last)"):
            continue
        for nxt in lines[i + 1:]:
            if nxt and not nxt[0].isspace():
                exc = nxt.strip()[:160]
                if exc not in found:
                    found.append(exc)
                break
    return found


def full_run(root, rdir, py, env, rec, how, n_jobs) -> dict:
    name = Path(rec["dataset"]).name
    out = rdir / "full" / f"{name}_{how}"
    out.mkdir(parents=True)
    log = out / "run.log"
    t0 = time.time()
    if how == "cli":
        cmd = [env_bin(root, py) / ("run-meegqc.exe" if IS_WIN else "run-meegqc"),
               "--inputdata", rec["dataset"], "--subs", *rec["subs"],
               "--n_jobs", str(n_jobs), "--derivatives_output", str(out / "derivatives"),
               "--run-all"]
    else:
        cmd = [env_python(root, py), str(HERE / "gui_run.py"), "--dataset", rec["dataset"],
               "--subs", *rec["subs"], "--out", str(out / "derivatives"),
               "--button", "all", "--n-jobs", str(n_jobs), "--log", str(out / "worker.log")]
    rc = run(cmd, env=env, log=log, check=False)
    text = log.read_text(encoding="utf-8", errors="replace")
    if (out / "worker.log").is_file():
        text += (out / "worker.log").read_text(encoding="utf-8", errors="replace")
    reports = sorted(str(p.relative_to(out)) for p in out.rglob("*.html"))
    problems = [m for m in FATAL if m in text]
    if rc != 0:
        problems.append(f"exit {rc}")
    if not reports:
        problems.append("no report")
    guard = next((l.split("___:", 1)[-1].strip() for l in text.splitlines()
                  if "memory guard" in l), "")
    return {"label": rec["label"], "dataset": name, "how": how, "python": py,
            "exit": rc, "seconds": round(time.time() - t0), "reports": len(reports),
            "guard": guard, "problems": problems, "caught": caught_errors(text),
            "ok": not problems}


def shots(root, rdir, py, env) -> list:
    """GUI in two themes, then the first page of a few reports."""
    sdir = rdir / "shots"
    sdir.mkdir(exist_ok=True)
    run([env_python(root, py), str(HERE / "shots.py"), "gui", str(sdir)], env=env,
        log=sdir / "shots.log", check=False)
    reports = sorted((rdir / "full").rglob("*.html"))
    picked = []
    for key in ("QA_subject", "QA_dataset", "QC_dataset", "qa_subject", "qa_dataset"):
        picked += [r for r in reports if key in r.name and r not in picked][:1]
    for r in (picked or reports)[:4]:
        run([env_python(root, py), str(HERE / "shots.py"), "report", str(r),
             str(sdir / (r.parent.parent.name + "_" + r.stem[:60] + ".png"))],
            env=env, log=sdir / "shots.log", check=False)
    return sorted(p.name for p in sdir.glob("*.png"))


def git_state(repo: Path) -> dict:
    def g(*args):
        r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
        return r.stdout.strip()
    return {"head": g("rev-parse", "--short", "HEAD"),
            "changed": g("status", "--porcelain").splitlines()}


def save(rdir: Path, results: dict) -> None:
    (rdir / "results.json").write_text(json.dumps(results, indent=2))


def write_summary(rdir: Path, r: dict) -> None:
    lines = [f"# Test round {r['started']} on {r['machine']}", "",
             f"{r['os']}; repo {r['repo']['head']} with "
             f"{len(r['repo']['changed'])} changed file(s)", "",
             "| Python | passed | failed | errors | skipped | time |",
             "|---|---|---|---|---|---|"]
    for py, t in r["pytest"].items():
        passed = t["tests"] - t["failures"] - t["errors"] - t["skipped"]
        lines.append(f"| {py} | {passed} | {t['failures']} | {t['errors']} | "
                     f"{t['skipped']} | {t['seconds']} s |")
    if r["full"]:
        lines += ["", "| Recording | via | Python | ok | reports | time | guard |",
                  "|---|---|---|---|---|---|---|"]
        for f in r["full"]:
            lines.append(f"| {f['label']} ({f['dataset']}) | {f['how'].upper()} | "
                         f"{f['python']} | {'yes' if f['ok'] else 'NO ' + ', '.join(f['problems'])} | "
                         f"{f['reports']} | {f['seconds']} s | {f['guard']} |")
        caught = [(f, e) for f in r["full"] for e in f.get("caught", [])]
        if caught:
            lines += ["", "Errors caught during the runs (the run carried on; "
                      "look at each):", ""]
            lines += [f"- {f['dataset']} {f['how'].upper()}: `{e}`" for f, e in caught]
    if r["shots"]:
        lines += ["", "Screenshots (shots/): " + ", ".join(r["shots"])]
    (rdir / "summary.md").write_text("\n".join(lines) + "\n")


def start(a) -> int:
    """The round in a detached tmux session, so it survives a dropped SSH
    connection or a closed laptop lid; attach to watch it."""
    if IS_WIN:
        raise SystemExit("Windows has no tmux: run `testbench.py round ...` in a terminal "
                         "(in an SSH session it keeps running after the session closes).")
    tmux = shutil.which("tmux")
    if tmux is None:
        raise SystemExit("tmux is not installed (Linux: sudo apt install tmux; macOS: brew install tmux)")
    if subprocess.run([tmux, "has-session", "-t", a.session], capture_output=True).returncode == 0:
        raise SystemExit(f"A round is already running in tmux session {a.session!r}: "
                         f"tmux attach -t {a.session}")
    import shlex
    root = Path(a.root).expanduser().resolve()
    (root / "runs").mkdir(parents=True, exist_ok=True)
    log = root / "runs" / f"{dt.datetime.now():%Y%m%d_%H%M%S}_console.log"
    cmd = " ".join(shlex.quote(c) for c in [sys.executable, str(Path(__file__).resolve()),
                                             "round", "--root", str(root), *a.round_args])
    subprocess.run([tmux, "new-session", "-d", "-s", a.session,
                    f"{cmd} 2>&1 | tee {shlex.quote(str(log))}"], check=True)
    # Keep the window after the round ends, so its last lines can be read.
    subprocess.run([tmux, "set-option", "-t", a.session, "remain-on-exit", "on"],
                   capture_output=True)
    say(f"round started in tmux session {a.session!r}; console log: {log}")
    say(f"watch it: tmux attach -t {a.session}   (detach again: Ctrl-b then d)")
    return 0


def status(a) -> None:
    root = Path(a.root).expanduser().resolve()
    say("root:", root, "| repo:", git_state(root / "MEEGqc"))
    for py in PYTHONS:
        p = env_python(root, py)
        if p.is_file():
            v = subprocess.run([str(p), "-c", "import sys, meg_qc; print(sys.version.split()[0], "
                                "meg_qc.__version__)"], capture_output=True, text=True)
            say(f"py{py}:", v.stdout.strip() or v.stderr.strip()[-200:])
        else:
            say(f"py{py}: not set up")


def main() -> int:
    # The console log of a Windows round is a file in the code page (cp1252):
    # a character MEEGqc prints that it cannot hold ("->" as an arrow) stopped
    # the whole round. Replace it in the console instead; run.log keeps UTF-8.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--root", default=os.environ.get(
        "MEEGQC_TESTBENCH", str(Path.home() / "MEEGqc_testbench")))
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("setup")
    s.add_argument("--pythons", nargs="+", default=PYTHONS)
    s.add_argument("--repo", default=REPO_URL)
    s.add_argument("--bundle", default=None, help="copy the bundle from this folder")
    r = sub.add_parser("round")
    r.add_argument("--pythons", nargs="+", default=PYTHONS)
    r.add_argument("--tests", nargs="+", default=["tests"])
    r.add_argument("--skip-tests", action="store_true")
    r.add_argument("--full", action="store_true")
    r.add_argument("--full-python", default=None)
    r.add_argument("--label", default="")
    r.add_argument("--jobs", type=int, default=1,
                   help="Pythons tested at the same time (default 1; each run "
                        "has its own temp and preferences folders)")
    r.add_argument("--without-plugins", action="store_true",
                   help="one more pass of the suite with plugins switched off")
    st = sub.add_parser("start", help="a round in tmux (Linux/macOS); any other "
                                      "option is passed to `round` (e.g. --full --jobs 2)")
    st.add_argument("--session", default="meegqc-round")
    sub.add_parser("status")
    for p in (s, r, st, sub.choices["status"]):
        p.add_argument("--root", default=argparse.SUPPRESS)
    a, extra = ap.parse_known_args()
    if a.cmd == "start":
        a.round_args = extra          # everything else is the round's
    elif extra:
        ap.error("unrecognized arguments: " + " ".join(extra))
    if a.cmd == "setup":
        setup(a)
        return 0
    if a.cmd == "round":
        return round_(a)
    if a.cmd == "start":
        return start(a)
    status(a)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
