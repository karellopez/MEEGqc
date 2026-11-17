"""The detached update helper, for installations made by the installer.

It runs after MEEGqc has closed, with nobody watching, so what it must never
do is leave the installation worse than it found it: a failed move to a
newer Python puts the previous environment back, the plugins of the old
environment are installed into the new one, the installer's uv runs with
the installer's settings and none of the user's, and the installation's
record ends up describing what is really installed.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from meg_qc.miscellaneous.GUI import _update_helper as helper


def _record(tmp_path: Path, target: str = "aarch64-apple-darwin") -> dict:
    env = tmp_path / "MEEGqc" / "env"
    env.mkdir(parents=True)
    (env / "MARKER").write_text("the environment as it was")
    return {
        "environment": str(env),
        "installer": {"version": "2.0.0", "os": "macos", "target": target},
        "uv": {"version": "0.13.1", "executable": str(tmp_path / "uv")},
        "uv_environment": {"UV_NO_CONFIG": "1", "UV_CACHE_DIR": "/install/cache",
                           "UV_PYTHON_INSTALL_DIR": "/install/python",
                           "UV_MANAGED_PYTHON": "1"},
        "python": {"requested": "3.12", "version": "3.12.12",
                   "executable": str(env / "bin" / "python")},
        "package": {"name": "meg-qc", "requested": "meg-qc", "version": "1.1.2"},
    }


def test_the_installers_uv_gets_the_installers_settings_and_none_of_the_users():
    record = {"uv_environment": {"UV_NO_CONFIG": "1", "UV_CACHE_DIR": "/install/cache"}}
    base = {"PATH": "/usr/bin", "UV_INDEX_URL": "http://elsewhere", "UV_CACHE_DIR": "/mine",
            "VIRTUAL_ENV": "/some/env", "PYTHONPATH": "/x", "HOME": "/home/u"}
    env = helper.uv_environment(record, base)
    assert env == {"PATH": "/usr/bin", "HOME": "/home/u",
                   "UV_NO_CONFIG": "1", "UV_CACHE_DIR": "/install/cache"}


@pytest.mark.parametrize(
    ("target", "expected"),
    [("aarch64-apple-darwin", "cpython-3.13-macos-aarch64-none"),
     ("x86_64-apple-darwin", "cpython-3.13-macos-x86_64-none"),
     ("x86_64-unknown-linux-gnu", "cpython-3.13-linux-x86_64-gnu"),
     ("aarch64-unknown-linux-gnu", "cpython-3.13-linux-aarch64-gnu"),
     # Windows on ARM runs the x64 build, and a move keeps it there.
     ("x86_64-pc-windows-msvc", "cpython-3.13-windows-x86_64-none"),
     ("something-new", "3.13")],
)
def test_a_move_keeps_the_build_the_installer_chose(target, expected):
    assert helper.python_request({"installer": {"target": target}}, "3.13") == expected


def _fake_uv(monkeypatch, *, fail_on: str = "", removed: list | None = None,
             plugins: list | None = None):
    """Stand in for uv: ``venv`` creates the folder, and the step named in
    ``fail_on`` fails. Returns the list of calls; Pythons removed go to
    ``removed``. ``plugins`` is what the old environment holds."""
    calls = []
    monkeypatch.setattr(helper, "remove_pythons",
                        lambda record, requests: (removed if removed is not None else [])
                        .extend(sorted(requests)))

    def run_uv(record, args):
        calls.append(args)
        if args[0] == fail_on:
            return 1
        if args[0] == "venv":
            new = Path(args[-1])
            new.mkdir()
            (new / "NEW").write_text("a new environment")
        return 0

    monkeypatch.setattr(helper, "run_uv", run_uv)
    monkeypatch.setattr(helper, "_starts", lambda python: True)
    monkeypatch.setattr(helper, "plugin_specs", lambda python: list(plugins or []))
    return calls


@pytest.mark.parametrize("failing_step", ["venv", "pip"])
def test_a_failed_move_puts_the_previous_environment_back(tmp_path, monkeypatch, failing_step):
    record = _record(tmp_path)
    env = Path(record["environment"])
    _fake_uv(monkeypatch, fail_on=failing_step)
    rc = helper.move_to_python(record, "3.13", record["python"]["executable"], "meg_qc")
    assert rc == 1
    assert (env / "MARKER").read_text() == "the environment as it was"
    assert not (env / "NEW").exists()
    assert not env.with_name("env.previous").exists()


def test_an_environment_that_does_not_start_is_also_rolled_back(tmp_path, monkeypatch):
    record = _record(tmp_path)
    env = Path(record["environment"])
    _fake_uv(monkeypatch)
    monkeypatch.setattr(helper, "_starts", lambda python: False)
    assert helper.move_to_python(record, "3.13", record["python"]["executable"], "x") == 1
    assert (env / "MARKER").exists()


def test_a_python_that_cannot_be_installed_touches_nothing(tmp_path, monkeypatch):
    record = _record(tmp_path)
    env = Path(record["environment"])
    calls = _fake_uv(monkeypatch, fail_on="python")
    assert helper.move_to_python(record, "3.13", record["python"]["executable"], "x") == 1
    assert (env / "MARKER").exists()
    assert [c[0] for c in calls] == ["python"]


def test_a_successful_move_replaces_the_environment_and_clears_the_old_one(tmp_path, monkeypatch):
    record = _record(tmp_path)
    env = Path(record["environment"])
    calls = _fake_uv(monkeypatch)
    assert helper.move_to_python(record, "3.13", record["python"]["executable"],
                                 "meg_qc") == 0
    assert (env / "NEW").exists() and not (env / "MARKER").exists()
    assert not env.with_name("env.previous").exists()
    assert calls[0][:3] == ["python", "install", "cpython-3.13-macos-aarch64-none"]
    assert calls[1][:1] == ["venv"] and "cpython-3.13-macos-aarch64-none" in calls[1]
    assert calls[1][calls[1].index("--prompt") + 1] == "MEEGqc"
    assert calls[2][:4] == ["pip", "install", "--upgrade", "--compile-bytecode"]
    assert calls[2][-1] == "meg_qc"


def test_a_move_takes_the_plugins_along(tmp_path, monkeypatch):
    """The old environment is asked for its plugins BEFORE it is set aside,
    and each is installed into the new one after MEEGqc, editable ones
    editable again."""
    record = _record(tmp_path)
    python = record["python"]["executable"]
    asked = []
    calls = _fake_uv(monkeypatch, plugins=["meegqc-megnet", "-e /work/my-plugin"])
    monkeypatch.setattr(helper, "plugin_specs",
                        lambda py: asked.append(Path(record["environment"]).joinpath(
                            "MARKER").exists()) or ["meegqc-megnet", "-e /work/my-plugin"])
    assert helper.move_to_python(record, "3.13", python, "meg_qc") == 0
    assert asked == [True], "listed while the old environment was still in place"
    plugin_calls = calls[3:]
    assert plugin_calls == [
        ["pip", "install", "--compile-bytecode", "--python", python, "meegqc-megnet"],
        ["pip", "install", "--compile-bytecode", "--python", python, "-e", "/work/my-plugin"],
    ]


def test_a_plugin_that_cannot_be_installed_again_does_not_undo_the_move(tmp_path, monkeypatch):
    """MEEGqc runs without it, and the Plugins window can add it later;
    putting the old environment back would leave the update undone at every
    start, for a plugin."""
    record = _record(tmp_path)
    env = Path(record["environment"])
    calls = _fake_uv(monkeypatch, plugins=["meegqc-megnet"])
    real_run_uv = helper.run_uv
    monkeypatch.setattr(helper, "run_uv", lambda record, args: 1 if args[-1] == "meegqc-megnet"
                        else real_run_uv(record, args))
    assert helper.move_to_python(record, "3.13", record["python"]["executable"], "meg_qc") == 0
    assert (env / "NEW").exists()
    assert not env.with_name("env.previous").exists()
    assert [c[0] for c in calls] == ["python", "venv", "pip"]


def test_a_failed_move_does_not_install_plugins(tmp_path, monkeypatch):
    record = _record(tmp_path)
    calls = _fake_uv(monkeypatch, fail_on="pip", plugins=["meegqc-megnet"])
    assert helper.move_to_python(record, "3.13", record["python"]["executable"], "x") == 1
    assert not any(c[-1] == "meegqc-megnet" for c in calls)


def test_the_record_describes_what_is_really_installed(tmp_path):
    """Read from the environment itself (here the test's own), never assumed."""
    import platform
    from importlib.metadata import version

    record = _record(tmp_path)
    manifest = tmp_path / "installation.json"
    helper.record_update(manifest, record, sys.executable, moved_to="3.13")
    written = json.loads(manifest.read_text(encoding="utf-8"))
    assert written["package"]["version"] == version("meg_qc")
    assert written["python"]["version"] == platform.python_version()
    assert written["python"]["requested"] == "3.13"
    assert written["python"]["moved_by_update"] is True
    assert written["updated"]


@pytest.mark.parametrize(("move_to", "expected"),
                         [("", ["upgrade", "record"]), ("3.13", ["move", "record", "prune"])])
def test_update_installation_moves_only_when_asked_and_records_only_success(
        tmp_path, monkeypatch, move_to, expected):
    record = _record(tmp_path)
    manifest = tmp_path / "installation.json"
    manifest.write_text(json.dumps(record), encoding="utf-8")
    done = []
    monkeypatch.setattr(helper, "upgrade_with_uv", lambda *a: done.append("upgrade") or 0)
    monkeypatch.setattr(helper, "move_to_python", lambda *a: done.append("move") or 0)
    monkeypatch.setattr(helper, "record_update", lambda *a: done.append("record"))
    monkeypatch.setattr(helper, "remove_pythons", lambda *a: done.append("prune"))
    assert helper.update_installation(manifest, "python", "meg_qc", move_to) == 0
    assert done == expected
    expected = expected[:1]

    done.clear()
    monkeypatch.setattr(helper, "upgrade_with_uv", lambda *a: done.append("upgrade") or 1)
    monkeypatch.setattr(helper, "move_to_python", lambda *a: done.append("move") or 1)
    assert helper.update_installation(manifest, "python", "meg_qc", move_to) == 1
    assert done == expected


def test_the_pythons_in_the_install_folder_are_named_as_uv_names_them(tmp_path):
    root = tmp_path / "python"
    for name in ("cpython-3.11-macos-aarch64-none", "cpython-3.11.17-macos-aarch64-none",
                 "cpython-3.13.16-macos-aarch64-none", ".lock", ".temp"):
        (root / name).mkdir(parents=True)
    record = {"uv_environment": {"UV_PYTHON_INSTALL_DIR": str(root)}}
    assert helper.installed_pythons(record) == {
        "cpython-3.11-macos-aarch64-none", "cpython-3.13-macos-aarch64-none"}


def test_a_failed_move_removes_the_python_it_downloaded(tmp_path, monkeypatch):
    record = _record(tmp_path)
    removed: list = []
    _fake_uv(monkeypatch, fail_on="pip", removed=removed)
    monkeypatch.setattr(helper, "installed_pythons", lambda record: set())
    helper.move_to_python(record, "3.13", record["python"]["executable"], "x")
    assert removed == ["cpython-3.13-macos-aarch64-none"]


def test_a_failed_move_keeps_a_python_that_was_already_there(tmp_path, monkeypatch):
    record = _record(tmp_path)
    removed: list = []
    _fake_uv(monkeypatch, fail_on="pip", removed=removed)
    monkeypatch.setattr(helper, "installed_pythons",
                        lambda record: {"cpython-3.13-macos-aarch64-none"})
    helper.move_to_python(record, "3.13", record["python"]["executable"], "x")
    assert removed == []


def test_after_a_move_only_the_new_python_stays(tmp_path, monkeypatch):
    record = _record(tmp_path)
    manifest = tmp_path / "installation.json"
    manifest.write_text(json.dumps(record), encoding="utf-8")
    removed: list = []
    monkeypatch.setattr(helper, "move_to_python", lambda *a: 0)
    monkeypatch.setattr(helper, "record_update", lambda *a: None)
    monkeypatch.setattr(helper, "installed_pythons", lambda record: {
        "cpython-3.11-macos-aarch64-none", "cpython-3.12-macos-aarch64-none",
        "cpython-3.13-macos-aarch64-none"})
    monkeypatch.setattr(helper, "remove_pythons",
                        lambda record, requests: removed.extend(sorted(requests)))
    helper.update_installation(manifest, "python", "meg_qc", "3.13")
    assert removed == ["cpython-3.11-macos-aarch64-none", "cpython-3.12-macos-aarch64-none"]


# ---------------------------------------------------------------------------
# PLUGIN_SPECS_CODE: what a plugin is, and how to install it again
# ---------------------------------------------------------------------------


def _plugin_dist(site: Path, name: str, direct_url: dict | None = None) -> None:
    """A distribution that registers a MEEGqc plugin, as an installer leaves it."""
    info = site / f"{name.replace('-', '_')}-1.0.dist-info"
    info.mkdir(parents=True)
    (info / "METADATA").write_text(f"Metadata-Version: 2.1\nName: {name}\nVersion: 1.0\n")
    (info / "entry_points.txt").write_text(
        f"[meegqc.plugins]\n{name.split('-')[-1]} = {name.replace('-', '_')}:Plugin\n")
    if direct_url is not None:
        (info / "direct_url.json").write_text(json.dumps(direct_url))


def _specs(site: Path) -> list:
    # -S, not -I: no site-packages (the plugins installed where the tests run
    # must not be listed), and PYTHONPATH still reaches the fake ones.
    # From the fake environment's folder: the current folder comes first on
    # sys.path without -I, and a checkout's stale meg_qc.egg-info would answer.
    site.mkdir(parents=True, exist_ok=True)
    out = subprocess.run([sys.executable, "-S", "-c", helper.PLUGIN_SPECS_CODE],
                         env={"PYTHONPATH": str(site), "PATH": ""}, cwd=site,
                         capture_output=True, text=True, check=True)
    return out.stdout.splitlines()


def test_every_kind_of_plugin_is_named_so_it_can_be_installed_again(tmp_path):
    site = tmp_path / "site"
    folder = tmp_path / "my plugin"
    _plugin_dist(site, "meegqc-example")                                  # from PyPI
    _plugin_dist(site, "meegqc-local", {"url": folder.as_uri(), "dir_info": {}})
    _plugin_dist(site, "meegqc-dev", {"url": folder.as_uri(),
                                      "dir_info": {"editable": True}})
    _plugin_dist(site, "meegqc-git", {"url": "https://example.org/p.git",
                                      "vcs_info": {"vcs": "git", "commit_id": "abc123"}})
    specs = _specs(site)
    assert sorted(specs) == sorted([
        "meegqc-example",
        f"meegqc-local @ {folder.as_uri()}",
        f"-e {folder}",
        "meegqc-git @ git+https://example.org/p.git@abc123",
    ])


def test_a_package_that_is_not_a_plugin_is_not_listed(tmp_path):
    site = tmp_path / "site"
    info = site / "numpy-2.0.dist-info"
    info.mkdir(parents=True)
    (info / "METADATA").write_text("Metadata-Version: 2.1\nName: numpy\nVersion: 2.0\n")
    (info / "entry_points.txt").write_text("[console_scripts]\nf2py = numpy.f2py:main\n")
    assert _specs(site) == []


def test_plugin_specs_of_an_environment_that_cannot_run_is_empty(tmp_path):
    assert helper.plugin_specs(str(tmp_path / "no-python")) == []


def test_the_helper_removes_its_own_temporary_copy_and_nothing_else(tmp_path, monkeypatch):
    """It is copied to a temporary folder before it runs (the update may
    replace the package it came from); that folder is not left behind."""
    own = tmp_path / "meg_qc-update-abc123"
    own.mkdir()
    (own / "_update_helper.py").write_text("")
    monkeypatch.setattr(helper, "__file__", str(own / "_update_helper.py"))
    helper._remove_own_copy()
    assert not own.exists()

    other = tmp_path / "somewhere-else"
    other.mkdir()
    (other / "_update_helper.py").write_text("")
    monkeypatch.setattr(helper, "__file__", str(other / "_update_helper.py"))
    helper._remove_own_copy()
    assert other.exists()


def test_the_helper_cleans_up_even_when_the_update_fails(monkeypatch):
    removed = []
    monkeypatch.setattr(helper, "_remove_own_copy", lambda: removed.append(True))
    monkeypatch.setattr(helper, "_run", lambda: 1)
    assert helper.main() == 1
    assert removed == [True]


def _dist(site: Path, name: str, *requires: str) -> None:
    info = site / f"{name.replace('-', '_')}-1.0.dist-info"
    info.mkdir(parents=True)
    lines = ["Metadata-Version: 2.1", f"Name: {name}", "Version: 1.0"]
    lines += [f"Requires-Dist: {r}" for r in requires]
    (info / "METADATA").write_text("\n".join(lines) + "\n")


def test_megnet_part_of_meegqc_follows_through_its_libraries(tmp_path):
    """MEGnet has no package of its own to install again: what a new
    environment needs is the "megnet" extra's libraries, when the old
    environment had them."""
    site = tmp_path / "site"
    _dist(site, "meg_qc", "numpy>=1.24", 'megnet-neuro>=0.3.6; extra == "megnet"')
    assert _specs(site) == []
    _dist(site, "megnet-neuro")
    assert _specs(site) == ["megnet-neuro>=0.3.6"]


def test_the_old_separate_megnet_package_is_not_installed_again(tmp_path):
    site = tmp_path / "site"
    _plugin_dist(site, "meegqc-megnet")
    _plugin_dist(site, "meegqc-other")
    assert _specs(site) == ["meegqc-other"]


def test_the_extra_is_read_from_the_real_metadata_of_meegqc():
    """What the listing code parses is what pyproject.toml declares."""
    from importlib.metadata import distribution
    requires = distribution("meg_qc").requires or []
    if not any('extra == "megnet"' in r for r in requires):
        pytest.skip("this meg_qc was installed before the megnet extra existed")
    assert "megnet-neuro>=0.3.6; extra == \"megnet\"" in requires
