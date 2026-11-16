"""Shared pytest fixtures for the MEEGqc test suite.

Real-data tests run against the cropped 10-second fixture bundle produced by
``tests/fixtures/make_test_fixtures.py`` and hosted as a GitHub Release asset.
Point the suite at an unpacked bundle with::

    export MEEGQC_TEST_DATA=/path/to/meegqc_test_data

When the variable is unset (or the path is missing) all real-data tests are
skipped, so the fast unit + headless-GUI tests still run anywhere.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

#: The GUI preferences of this test session (see settings_store.py). Set before
#: any test runs, so no test ever reads or writes the user's own preferences
#: (macOS plist, Windows registry, ~/.config on Linux).
_SETTINGS_DIR = None
#: True when the folder is outside pytest's temp folder and must be removed here.
_SETTINGS_DIR_IS_OURS = False


@pytest.hookimpl(trylast=True)  # after pytest has set up its temp folder
def pytest_configure(config):
    global _SETTINGS_DIR, _SETTINGS_DIR_IS_OURS
    config.addinivalue_line("markers", "realdata: needs the MEEGQC_TEST_DATA bundle")
    config.addinivalue_line("markers", "gui: headless Qt GUI test (offscreen)")
    factory = getattr(config, "_tmp_path_factory", None)
    if factory is not None:
        # Inside pytest's own temp folder, so a run that is killed leaves no
        # folder behind in the system's temp (the next run clears --basetemp,
        # and pytest rotates its default ones).
        _SETTINGS_DIR = str(factory.mktemp("gui_settings", numbered=False))
    else:
        _SETTINGS_DIR = tempfile.mkdtemp(prefix="meegqc_test_settings_")
        _SETTINGS_DIR_IS_OURS = True
    os.environ["MEEGQC_SETTINGS_DIR"] = _SETTINGS_DIR


def pytest_unconfigure(config):
    if _SETTINGS_DIR and _SETTINGS_DIR_IS_OURS:
        shutil.rmtree(_SETTINGS_DIR, ignore_errors=True)


def pytest_generate_tests(metafunc):
    """Parametrize any test asking for any_dataset / meg_dataset / eeg_dataset."""
    mapping = {
        "any_dataset": ALL_DATASETS,
        "meg_dataset": MEG_DATASETS,
        "eeg_dataset": EEG_DATASETS,
    }
    for name, items in mapping.items():
        if name in metafunc.fixturenames:
            if not items:
                metafunc.parametrize(
                    name,
                    [pytest.param(None,
                                  marks=pytest.mark.skip(reason="no MEEGQC_TEST_DATA bundle"))],
                )
            else:
                metafunc.parametrize(name, items, ids=[n for n, _ in items])


# --------------------------------------------------------------------------- #
# Fixture-bundle discovery
# --------------------------------------------------------------------------- #
def _bundle_root() -> Path | None:
    root = os.environ.get("MEEGQC_TEST_DATA")
    if not root:
        return None
    p = Path(root)
    if not (p / "MANIFEST.json").is_file():
        return None
    return p


BUNDLE = _bundle_root()
_needs_data = pytest.mark.skipif(BUNDLE is None,
                                 reason="set MEEGQC_TEST_DATA to the fixture bundle")


@pytest.fixture(scope="session")
def bundle() -> Path:
    if BUNDLE is None:
        pytest.skip("MEEGQC_TEST_DATA not set")
    return BUNDLE


@pytest.fixture(scope="session")
def manifest(bundle) -> dict:
    return json.loads((bundle / "MANIFEST.json").read_text())


def _dataset_dirs():
    """(name, path) for every dataset dir in the bundle (empty if no bundle)."""
    if BUNDLE is None:
        return []
    out = []
    for entry in sorted(BUNDLE.iterdir()):
        if entry.is_dir() and any(entry.glob("sub-*")):
            out.append((entry.name, entry))
    return out


ALL_DATASETS = _dataset_dirs()


def _modality_of(ds_path: Path) -> str:
    """meg if the dataset has a meg/ datatype folder, else eeg."""
    if list(ds_path.glob("sub-*/**/meg")) or list(ds_path.glob("sub-*/meg")):
        return "meg"
    return "eeg"


MEG_DATASETS = [(n, p) for (n, p) in ALL_DATASETS if _modality_of(p) == "meg"]
EEG_DATASETS = [(n, p) for (n, p) in ALL_DATASETS if _modality_of(p) == "eeg"]


# --------------------------------------------------------------------------- #
# Helpers: config + running the pipeline against an isolated copy
# --------------------------------------------------------------------------- #
def _package_settings_ini() -> Path:
    import meg_qc
    return Path(meg_qc.__file__).parent / "settings" / "settings.ini"


def _write_config(tmp_dir, enabled) -> Path:
    """Write a settings.ini with only *enabled* metrics on (rest off)."""
    import re
    src = _package_settings_ini().read_text()
    for key in ("STD", "PSD", "PTP_manual", "ECG", "EOG", "Head", "Muscle"):
        src = re.sub(rf"(?m)^{key}\s*=.*$",
                     f"{key} = {'True' if key in enabled else 'False'}", src)
    # Fixtures are already ~10 s; do not force a runtime crop (an exact-length
    # crop is an edge case on some formats). Leave data_crop_tmax at its default.
    out = Path(tmp_dir) / "settings.ini"
    out.write_text(src)
    return out


@pytest.fixture(scope="session")
def fast_config(tmp_path_factory) -> Path:
    """STD + PSD only: fast broad coverage for the all-datasets sweep."""
    return _write_config(tmp_path_factory.mktemp("cfg_fast"), {"STD", "PSD"})


@pytest.fixture(scope="session")
def full_config(tmp_path_factory) -> Path:
    """All the common metrics, for representative deep tests."""
    return _write_config(tmp_path_factory.mktemp("cfg_full"),
                         {"STD", "PSD", "PTP_manual", "ECG", "EOG", "Muscle"})


@pytest.fixture(scope="session")
def one_meg():
    if not MEG_DATASETS:
        pytest.skip("no MEG dataset in bundle")
    return MEG_DATASETS[0]


@pytest.fixture(scope="session")
def megnet_meg_dataset():
    """MEGnet tests require ds_meg_example_1_complete (longer than MEGnet's 105 s)."""
    for name, path in ALL_DATASETS:
        if name == "ds_meg_example_1_complete":
            return (name, path)
    pytest.skip("ds_meg_example_1_complete not in the MEEGQC_TEST_DATA bundle")


@pytest.fixture(scope="session")
def one_eeg():
    if not EEG_DATASETS:
        pytest.skip("no EEG dataset in bundle")
    return EEG_DATASETS[0]


@pytest.fixture(scope="session")
def two_subject_dataset():
    """First dataset with >= 2 subjects, so n_jobs=2 parallelizes across them."""
    for name, path in ALL_DATASETS:
        if len(list(path.glob("sub-*"))) >= 2:
            return (name, path)
    pytest.skip("no dataset with >= 2 subjects in bundle")


@pytest.fixture(scope="session")
def two_datasets():
    if len(ALL_DATASETS) < 2:
        pytest.skip("need >= 2 datasets in bundle")
    # prefer two same-modality datasets for multi-dataset reports
    pool = MEG_DATASETS if len(MEG_DATASETS) >= 2 else ALL_DATASETS
    return pool[:2]


@pytest.fixture
def named_dataset():
    """Return a bundle dataset's path by its exact directory name; skip if absent.

    Lets a test target a specific dataset (e.g. ``ds_camcan`` for all-metrics
    coverage, ``ds_1`` for a session-nested one) while still degrading to a skip
    on bundles that do not ship that dataset.
    """
    def _get(name: str) -> Path:
        for n, p in ALL_DATASETS:
            if n == name:
                return p
        pytest.skip(f"dataset {name!r} not in the MEEGQC_TEST_DATA bundle")
    return _get


#: Files at least this large are hard-linked into a test's dataset copy, not
#: copied: they are recordings, which MEEGqc only reads. Smaller files
#: (sidecars, tables) are copied, so nothing a run writes can reach the bundle.
_LINK_FROM_BYTES = 1 << 20


def _link_or_copy(src: str, dst: str) -> str:
    if os.path.getsize(src) >= _LINK_FROM_BYTES:
        try:
            os.link(src, dst)
            return dst
        except OSError:  # another drive, or a filesystem without hard links
            pass
    return shutil.copy2(src, dst)


@pytest.fixture
def isolated_dataset(tmp_path):
    """A fixture dataset in tmp_path, so the run writes its derivatives there.

    Recordings are hard-linked rather than copied: every real-data test made
    its own copy (one test copies a 736 MB dataset), several GB per Python.
    """
    def _copy(ds_path: Path) -> Path:
        dst = tmp_path / Path(ds_path).name
        shutil.copytree(ds_path, dst, copy_function=_link_or_copy)
        return dst
    return _copy


def run_cli(args, timeout=1800):
    """Run a MEEGqc console script; return CompletedProcess. Skips if absent.

    Output is decoded as UTF-8 with errors='replace' so a Windows runner never
    crashes decoding the pipeline's unicode output (uV, fT/sqrt(Hz), ...) under a
    non-UTF-8 locale.
    """
    exe = shutil.which(args[0])
    if exe is None:
        pytest.skip(f"{args[0]} not on PATH (package not installed?)")
    return subprocess.run([exe, *args[1:]], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout)


@pytest.fixture(scope="session")
def _megnet_config_factory(tmp_path_factory):
    """settings.ini with ECG + EOG on, rest minimal for speed. MEGnet (when its
    plugin is installed) runs with its defaults: both references independent."""
    import re as _re

    ini_src = _package_settings_ini()
    text = ini_src.read_text()

    for key in ("STD", "PSD", "PTP_manual", "ECG", "EOG"):
        text = _re.sub(rf"(?m)^{key}\s*=.*$", f"{key} = True", text)
    for key in ("Head", "Muscle"):
        text = _re.sub(rf"(?m)^{key}\s*=.*$", f"{key} = False", text)

    out = tmp_path_factory.mktemp("cfg_megnet") / "settings.ini"
    out.write_text(text)
    return out


@pytest.fixture
def cli():
    return run_cli
