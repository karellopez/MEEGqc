"""Unit tests for ``meg_qc.miscellaneous.GUI.updater`` (Qt-free).

These tests cover the network and version-comparison helpers and the
helper-staging logic. They never import Qt and never reach the real PyPI:
every network call is replaced by a controlled substitute.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

from meg_qc.miscellaneous.GUI import updater


@pytest.fixture(autouse=True)
def _helper_copies_stay_in_the_test_folder(monkeypatch, tmp_path):
    """``launch_update_helper`` stages a copy of the helper in a new temporary
    folder; in these tests that folder is inside the test's own, so nothing
    is left in the system's temporary folder."""
    made = iter(range(1000))

    def mkdtemp(prefix="tmp"):
        folder = tmp_path / f"{prefix}{next(made)}"
        folder.mkdir()
        return str(folder)

    monkeypatch.setattr(updater.tempfile, "mkdtemp", mkdtemp)


# ---------------------------------------------------------------------------
# installed_version
# ---------------------------------------------------------------------------


def test_installed_version_matches_package():
    import meg_qc
    assert updater.installed_version() == meg_qc.__version__


# ---------------------------------------------------------------------------
# is_newer
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "latest,current,expected",
    [
        ("1.0.3", "1.0.2", True),
        ("1.0.2", "1.0.2", False),
        ("1.0.1", "1.0.2", False),
        ("2.0.0", "1.99.99", True),
        ("1.0.0rc1", "1.0.0", False),     # pre-release < final
        ("1.0.0", "1.0.0rc1", True),      # final > pre-release
        ("", "1.0.0", False),             # empty latest: cannot tell
        ("1.0.0", "", False),             # empty current: cannot tell
    ],
)
def test_is_newer_handles_pep440(latest, current, expected):
    assert updater.is_newer(latest, current) is expected


def test_is_newer_string_fallback_when_packaging_missing(monkeypatch):
    """When ``packaging.version`` is unimportable, falls back to an
    equality-only string compare so identical versions still report
    ``False`` (no spurious update prompt)."""
    real_import = __import__

    def fake_import(name, *args, **kwargs):
        if name == "packaging.version" or name.startswith("packaging."):
            raise ImportError("packaging unavailable for this test")
        return real_import(name, *args, **kwargs)

    with monkeypatch.context() as m:
        m.setattr("builtins.__import__", fake_import)
        assert updater.is_newer("1.0.3", "1.0.2") is True
        assert updater.is_newer("1.0.2", "1.0.2") is False


# ---------------------------------------------------------------------------
# fetch_latest
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._body = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_fetch_latest_returns_info_version(monkeypatch):
    payload = {"info": {"version": "9.9.9"}, "releases": {"9.9.9": []}}
    monkeypatch.setattr(updater, "urlopen", lambda req, timeout=None, context=None:
                        _FakeResponse(payload))
    assert updater.fetch_latest() == updater.Latest(version="9.9.9", newest="9.9.9")


def test_fetch_latest_returns_none_on_network_error(monkeypatch):
    """A broken network must surface as ``None``, never raise."""

    def boom(*args, **kwargs):
        raise OSError("no internet")

    monkeypatch.setattr(updater, "urlopen", boom)
    assert updater.fetch_latest() is None


def test_fetch_latest_returns_none_on_malformed_json(monkeypatch):
    class BadResp:
        def read(self):
            return b"not-json-{"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(updater, "urlopen", lambda *a, **k: BadResp())
    assert updater.fetch_latest() is None


def test_fetch_latest_returns_none_when_info_missing(monkeypatch):
    """A PyPI response lacking ``info.version`` is treated as 'unknown'."""
    monkeypatch.setattr(updater, "urlopen", lambda *a, **k: _FakeResponse({"releases": {}}))
    assert updater.fetch_latest() is None


def test_the_check_asks_pypi_about_the_canonical_distribution():
    """``meeg_qc`` only pins ``meg_qc``: the version to compare is meg_qc's."""
    assert updater.PYPI_URL == "https://pypi.org/pypi/meg_qc/json"


# ---------------------------------------------------------------------------
# latest_from: which release this Python can install
#
# Offering a release the environment's Python cannot run made the update
# report success, change nothing, and ask again at every start.
# ---------------------------------------------------------------------------


def _release(requires_python: str = "", yanked: bool = False) -> list[dict]:
    return [{"packagetype": "bdist_wheel", "requires_python": requires_python,
             "yanked": yanked}]


def _payload(newest: str, releases: dict[str, list[dict]]) -> dict:
    return {"info": {"version": newest}, "releases": releases}


def test_a_release_this_python_runs_is_offered_as_is():
    payload = _payload("1.2.0", {"1.1.2": _release(">=3.10"), "1.2.0": _release(">=3.10")})
    assert updater.latest_from(payload, "3.12.7") == updater.Latest("1.2.0", "1.2.0", "")


def test_a_release_needing_a_newer_python_names_it_and_offers_the_last_that_runs():
    payload = _payload("1.3.0", {
        "1.1.2": _release(">=3.10,<3.15"),
        "1.2.0": _release(">=3.10,<3.15"),
        "1.3.0": _release(">=3.13"),
    })
    latest = updater.latest_from(payload, "3.12.7")
    assert latest == updater.Latest(version="1.2.0", newest="1.3.0", needs_python="3.13")


def test_yanked_releases_and_prereleases_are_never_offered():
    payload = _payload("1.2.0", {
        "1.2.0": _release(">=3.13"),
        "1.1.3": _release(">=3.10", yanked=True),
        "1.1.4rc1": _release(">=3.10"),
        "1.1.2": _release(">=3.10"),
    })
    assert updater.latest_from(payload, "3.12.0").version == "1.1.2"


@pytest.mark.parametrize(
    ("requires", "python", "expected"),
    [(">=3.13", "3.12.7", "3.13"), (">=3.14", "3.12.7", "3.14"),
     (">=3.13.2", "3.12.7", "3.13"), ("<3.10", "3.12.7", "")],
)
def test_minimum_python_is_the_first_minor_version_that_fits(requires, python, expected):
    assert updater.minimum_python(requires, python) == expected


# ---------------------------------------------------------------------------
# plan_update
# ---------------------------------------------------------------------------


def test_an_installer_installation_moves_to_the_python_the_newest_release_needs():
    latest = updater.Latest(version="1.2.0", newest="1.3.0", needs_python="3.13")
    assert updater.plan_update(latest, "1.1.2", can_move_python=True) == (
        updater.UpdatePlan(version="1.3.0", move_to_python="3.13"))


def test_any_other_installation_is_offered_what_its_python_runs():
    latest = updater.Latest(version="1.2.0", newest="1.3.0", needs_python="3.13")
    assert updater.plan_update(latest, "1.1.2", can_move_python=False) == (
        updater.UpdatePlan(version="1.2.0"))


def test_nothing_is_offered_when_nothing_newer_runs_here():
    latest = updater.Latest(version="1.2.0", newest="1.3.0", needs_python="3.13")
    assert updater.plan_update(latest, "1.2.0", can_move_python=False) is None
    assert updater.plan_update(updater.Latest("1.2.0", "1.2.0"), "1.2.0",
                               can_move_python=True) is None


# ---------------------------------------------------------------------------
# find_installation: the installer's record, for this environment only
# ---------------------------------------------------------------------------


def _installation(tmp_path: Path, *, environment: Path | None = None,
                  uv_exists: bool = True) -> Path:
    install = tmp_path / "MEEGqc"
    env = install / "env"
    env.mkdir(parents=True)
    uv = install / "uv" / "uv"
    if uv_exists:
        uv.parent.mkdir()
        uv.write_text("")
    (install / "installation.json").write_text(json.dumps({
        "environment": str(environment or env),
        "uv": {"version": "0.13.1", "executable": str(uv)},
        "uv_environment": {"UV_NO_CONFIG": "1"},
    }))
    return env


def test_the_installer_record_beside_the_environment_is_found(tmp_path):
    env = _installation(tmp_path)
    found = updater.find_installation(env)
    assert found == updater.Installation(
        manifest=env.parent / "installation.json", uv=env.parent / "uv" / "uv")


def test_a_record_naming_another_environment_is_ignored(tmp_path):
    env = _installation(tmp_path, environment=tmp_path / "elsewhere")
    assert updater.find_installation(env) is None


def test_a_record_whose_uv_is_gone_is_ignored(tmp_path):
    env = _installation(tmp_path, uv_exists=False)
    assert updater.find_installation(env) is None


def test_no_record_means_no_installation(tmp_path):
    assert updater.find_installation(tmp_path / "env") is None


def test_the_update_log_of_an_installer_installation_stays_in_its_folder(tmp_path):
    env = _installation(tmp_path)
    found = updater.find_installation(env)
    assert updater.update_log_path(found) == env.parent / "update.log"
    assert updater.update_log_path(None) == Path.home() / ".meg_qc" / "update.log"


# ---------------------------------------------------------------------------
# is_editable_install / installed_distribution_name
# ---------------------------------------------------------------------------


def test_is_editable_install_returns_bool():
    """The detection helper must always return a bool, never raise."""
    assert isinstance(updater.is_editable_install(), bool)


def test_is_editable_install_handles_missing_dist(monkeypatch):
    """When the distribution lookup fails we conservatively say 'not editable'."""
    from importlib import metadata

    def boom(name):
        raise metadata.PackageNotFoundError(name)

    monkeypatch.setattr("importlib.metadata.distribution", boom)
    assert updater.is_editable_install() is False


def test_the_wrapper_distribution_is_the_one_updated_when_installed(monkeypatch):
    from importlib import metadata

    def only(installed):
        def distribution(name):
            if name not in installed:
                raise metadata.PackageNotFoundError(name)
            return object()
        return distribution

    monkeypatch.setattr("importlib.metadata.distribution", only({"meg_qc", "meeg_qc"}))
    assert updater.installed_distribution_name() == "meeg_qc"
    monkeypatch.setattr("importlib.metadata.distribution", only({"meg_qc"}))
    assert updater.installed_distribution_name() == "meg_qc"


# ---------------------------------------------------------------------------
# _copy_helper_to_temp
# ---------------------------------------------------------------------------


def test_copy_helper_to_temp_copies_a_real_file():
    """The staged helper must exist and contain the expected entry point."""
    dst = updater._copy_helper_to_temp()
    try:
        assert dst.exists()
        assert dst.name == "_update_helper.py"
        body = dst.read_text(encoding="utf-8")
        assert "def main()" in body
        assert "wait_for_parent" in body
    finally:
        try:
            dst.unlink()
            dst.parent.rmdir()
        except OSError:
            pass


def test_the_helper_needs_nothing_but_the_standard_library():
    """It runs from a temp folder on the Python the environment was built
    from, which has none of MEEGqc's dependencies."""
    import ast

    source = Path(updater.__file__).with_name("_update_helper.py").read_text(encoding="utf-8")
    modules = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            modules.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            modules.add(node.module.split(".")[0])
    assert modules <= set(sys.stdlib_module_names) | {"__future__"}, modules


# ---------------------------------------------------------------------------
# launch_update_helper (smoke: spawn path is mocked)
# ---------------------------------------------------------------------------


def test_launch_update_helper_returns_false_when_spawn_fails(monkeypatch):
    """A subprocess failure during spawn must surface as ``False``, not
    propagate. Callers branch on the bool to decide whether to quit the GUI."""
    def boom(*args, **kwargs):
        raise OSError("nope")

    monkeypatch.setattr(updater, "find_installation", lambda: None)
    monkeypatch.setattr(updater.subprocess, "Popen", boom)
    assert updater.launch_update_helper(restart=False) is False


def test_launch_update_helper_invokes_popen_with_helper_path(monkeypatch):
    """Happy path: helper is staged, Popen is called with the right args."""
    captured: dict[str, Any] = {}

    class FakePopen:
        def __init__(self, cmd, **kwargs):
            captured["cmd"] = list(cmd)
            captured["kwargs"] = kwargs

    monkeypatch.setattr(updater, "find_installation", lambda: None)
    monkeypatch.setattr(updater, "installed_distribution_name", lambda: "meg_qc")
    monkeypatch.setattr(updater.subprocess, "Popen", FakePopen)
    assert updater.launch_update_helper(restart=True) is True

    cmd = captured["cmd"]
    # python -I <helper.py> --parent-pid <pid> --python <python> --package meg_qc --restart-cmd <argv0>
    assert cmd[0] == sys.executable or cmd[0] == "python"
    assert cmd[1] == "-I"
    assert cmd[2].endswith("_update_helper.py")
    assert "--parent-pid" in cmd
    assert cmd[cmd.index("--package") + 1] == "meg_qc"
    assert "--restart-cmd" in cmd
    assert "--installation" not in cmd
    stdout = captured["kwargs"].get("stdout")
    if stdout is not None:
        stdout.close()


def test_an_installer_installation_updates_through_its_record(monkeypatch, tmp_path):
    """The helper is told where the installer's record is, so it uses that uv;
    it runs on the Python the environment was built from, which a move to a
    newer Python can replace the environment around (Windows cannot delete a
    running program); and on macOS and Linux it logs inside the install folder."""
    env = _installation(tmp_path)
    installation = updater.find_installation(env)
    captured: dict[str, Any] = {}

    class FakePopen:
        def __init__(self, cmd, **kwargs):
            captured["cmd"] = list(cmd)
            captured["kwargs"] = kwargs

    monkeypatch.setattr(updater, "find_installation", lambda: installation)
    monkeypatch.setattr(updater.subprocess, "Popen", FakePopen)
    monkeypatch.setattr(updater.platform, "system", lambda: "Darwin")
    assert updater.launch_update_helper(restart=False, move_to_python="3.13") is True

    cmd = captured["cmd"]
    assert cmd[0] == updater._base_python()
    assert cmd[1] == "-I"
    assert cmd[cmd.index("--installation") + 1] == str(installation.manifest)
    assert cmd[cmd.index("--move-to-python") + 1] == "3.13"
    assert Path(captured["kwargs"]["stdout"].name) == env.parent / "update.log"
    captured["kwargs"]["stdout"].close()


def test_only_an_installer_installation_can_move_to_another_python(monkeypatch):
    monkeypatch.setattr(updater, "find_installation", lambda: None)
    monkeypatch.setattr(updater.subprocess, "Popen",
                        lambda *a, **k: pytest.fail("nothing may be spawned"))
    assert updater.launch_update_helper(restart=False, move_to_python="3.13") is False


# ---------------------------------------------------------------------------
# The release-notes link in the update prompt
#
# Being asked to accept an update is exactly when somebody wants to know what
# is in it, and accepting closes the application to install itself, so the
# question has to be answerable before the dialog goes away.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("version", "expected_anchor"),
    [("1.1.2", "#v112"), ("1.0.9", "#v109"), ("0.9.8", "#v098"), ("2.0.0", "#v200")],
)
def test_release_notes_url_anchors_at_the_version(version, expected_anchor):
    """The documentation numbers its release cards by the version with the dots
    dropped, so the link lands on the right card rather than the top of a long
    page."""
    pytest.importorskip("PyQt6.QtWidgets")
    from meg_qc.miscellaneous.GUI.update_widgets import release_notes_url

    assert release_notes_url(version) == (
        "https://ancplaboldenburg.github.io/megqc_documentation/updates.html"
        + expected_anchor)


@pytest.mark.parametrize("version", ["1.1.2.dev0", "", "not-a-version", "1.2.0rc1"])
def test_release_notes_url_degrades_to_the_page(version):
    """A dev build or a local install has no card of its own. Opening the notes
    at the top beats sending somebody to a fragment that does not exist."""
    pytest.importorskip("PyQt6.QtWidgets")
    from meg_qc.miscellaneous.GUI.update_widgets import release_notes_url

    assert release_notes_url(version).endswith("updates.html")


def test_the_gui_opens_the_same_release_notes_page():
    """The main window's "Version notes" button and the update prompt's "What
    changed" must open one page."""
    source = (Path(updater.__file__).with_name("megqcGUI.py")).read_text(encoding="utf-8")
    assert "https://ancplaboldenburg.github.io/megqc_documentation/updates.html" in source
