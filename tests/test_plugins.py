"""The plugin system: discovery, settings, installer commands, references.

Uses a fake plugin registered through a fake entry point, so it runs without
any plugin installed (and with MEGnet installed, which these tests ignore).
"""
from __future__ import annotations

import configparser
import sys
from types import SimpleNamespace

import numpy as np
import pytest

from meg_qc.plugins import api, cli, manage, registry
from meg_qc.plugins.api import Plugin, ReferenceResult, ReferenceSignal

FAKE_SETTINGS = """[FAKEREF]
#ecg (str) - How the ECG metric uses it. Default: independent
ecg = independent
#eog (str) - How the EOG metric uses it. Default: off
eog = off
ecg_lowpass_h_freq = 30
threshold = 0.5
"""


class FakeProvider:
    def __init__(self, why=None, fail=False, n=50):
        self.why, self.fail, self.n = why, fail, n

    def applies_to(self, raw, params):
        return self.why

    def compute(self, raw, params, *, data_path, work_dir):
        if self.fail:
            raise RuntimeError("boom")
        return ReferenceResult("ok", ecg=ReferenceSignal(np.arange(self.n, dtype=float), [3], [0.97]))


def fake_plugin(**changes) -> Plugin:
    values = dict(name="fakeref", label="FakeRef", version="1.0", summary="A test plugin.",
                  settings_section="FAKEREF", default_settings=FAKE_SETTINGS,
                  parse_settings=lambda s: {"threshold": float(s["threshold"])},
                  reference_provider=FakeProvider())
    values.update(changes)
    return Plugin(**values)


class FakeEntryPoint:
    def __init__(self, name, factory, package="meegqc-fakeref"):
        self.name = name
        self._factory = factory
        self.dist = SimpleNamespace(metadata={"Name": package}, version="1.0")

    def load(self):
        return self._factory


@pytest.fixture
def installed(monkeypatch):
    """Install fake entry points; returns a function taking (name, factory) pairs."""
    def _install(*eps):
        monkeypatch.setattr(registry.metadata, "entry_points",
                            lambda group=None: [FakeEntryPoint(n, f) for n, f in eps])
        registry.discover(refresh=True)
    monkeypatch.delenv("MEEGQC_PLUGINS", raising=False)
    yield _install
    registry._FOUND = None


# ── discovery ─────────────────────────────────────────────────────────────

def test_a_plugin_is_found_and_loaded(installed):
    installed(("fakeref", fake_plugin))
    (found,) = registry.discover()
    assert found.plugin.name == "fakeref" and found.package == "meegqc-fakeref"
    assert registry.find("fakeref").label == "FakeRef"
    assert [p.name for p in registry.reference_providers()] == ["fakeref"]


@pytest.mark.parametrize("factory, error", [
    (lambda: fake_plugin(api_version=99), "written for plugin API 99"),
    (lambda: fake_plugin(name="Bad-Name"), "invalid name"),
    (lambda: fake_plugin(name="recorded"), "reserved for MEEGqc's own references"),
    (lambda: fake_plugin(default_settings="[OTHER]\nx = 1\n"), "do not start a [FAKEREF] section"),
    (lambda: "not a plugin", "did not return"),
    (lambda: 1 / 0, "ZeroDivisionError"),
])
def test_a_broken_plugin_is_listed_with_the_reason_and_never_loaded(installed, factory, error):
    installed(("broken", factory))
    (found,) = registry.discover()
    assert found.plugin is None and error in found.error
    assert registry.plugins() == []


def test_two_plugins_cannot_share_a_name(installed):
    installed(("a", fake_plugin), ("b", fake_plugin))
    found = registry.discover()
    assert [f.plugin is not None for f in found] == [True, False]
    assert "already named" in found[1].error


def test_plugins_can_be_switched_off(installed, monkeypatch):
    installed(("fakeref", fake_plugin))
    monkeypatch.setenv("MEEGQC_PLUGINS", "0")
    assert registry.discover(refresh=True) == []


# ── settings ──────────────────────────────────────────────────────────────

def test_a_plugins_section_joins_the_settings_once(installed):
    installed(("fakeref", fake_plugin))
    text = registry.with_plugin_sections("[GENERAL]\nSTD = True\n")
    assert text.count("[FAKEREF]") == 1 and "#ecg (str)" in text
    assert registry.with_plugin_sections(text) == text


def test_the_users_values_override_the_plugins_defaults(installed):
    installed(("fakeref", fake_plugin))
    cfg = configparser.ConfigParser()
    cfg.read_string("[FAKEREF]\nthreshold = 0.8\n")
    run = registry.settings_for_run(cfg)["fakeref"]
    assert run["params"] == {"threshold": 0.8}
    assert run["section"]["ecg"] == "independent"        # default kept


def test_a_plugin_whose_settings_cannot_be_read_does_not_run(installed, capsys):
    installed(("fakeref", fake_plugin))
    cfg = configparser.ConfigParser()
    cfg.read_string("[FAKEREF]\nthreshold = high\n")
    assert registry.settings_for_run(cfg) == {}
    assert "it will not run" in capsys.readouterr().out


@pytest.mark.parametrize("value, use", [("independent", "independent"), (" Both ", "both"),
                                        ("fallback", "fallback"), ("nonsense", "off"), ("", "off")])
def test_reference_use(value, use):
    assert api.reference_use({"ecg": value}, "ECG") == use


def test_reference_lowpass():
    assert api.reference_lowpass({"eog_lowpass_h_freq": "20"}, "EOG") == 20.0
    assert api.reference_lowpass({"eog_lowpass_h_freq": "x"}, "EOG") == 0.0
    assert api.reference_lowpass({}, "EOG") == 0.0


# ── references for one recording ──────────────────────────────────────────

def _references(installed, provider, *, run_eog=True):
    from meg_qc.calculation.references import compute_references
    installed(("fakeref", lambda: fake_plugin(reference_provider=provider)))
    raw = SimpleNamespace(n_times=100, info={})
    return compute_references(registry.settings_for_run(None),
                              run_ecg=True, run_eog=run_eog, work_root="/tmp/meegqc_refs_test",
                              load_recording=lambda: (raw, lambda: "rec.fif"))


def test_a_reference_is_stretched_to_the_recording(installed):
    (ref,) = _references(installed, FakeProvider())
    assert ref.status == "ok" and ref.method == "correlation_fakeref"
    sig = ref.signal("ECG")
    assert sig.signal.shape == (100,) and sig.component_ids == [3]
    assert ref.wants("ECG", "independent") and not ref.requested("EOG")
    assert ref.channel_name("ECG") == "FakeRef_ECG"


def test_a_reference_that_does_not_apply_says_why(installed, capsys):
    (ref,) = _references(installed, FakeProvider(why="needs at least 105 s"))
    assert ref.status == "skipped" and ref.signal("ECG") is None
    assert "FakeRef skipped - needs at least 105 s" in capsys.readouterr().out


def test_a_failing_reference_does_not_stop_the_recording(installed):
    (ref,) = _references(installed, FakeProvider(fail=True))
    assert ref.status == "failed" and "boom" in ref.reason


def test_a_recording_that_cannot_be_read_fails_only_the_plugin(installed, capsys):
    from meg_qc.calculation.references import compute_references
    installed(("fakeref", fake_plugin))

    def unreadable():
        raise OSError("disk full")
    (ref,) = compute_references(registry.settings_for_run(None), run_ecg=True, run_eog=True,
                                work_root="/tmp/meegqc_refs_test", load_recording=unreadable)
    assert ref.status == "failed" and "disk full" in ref.reason
    assert "FakeRef failed - the recording could not be read" in capsys.readouterr().out


def test_the_recording_file_is_written_only_for_a_plugin_that_runs(installed):
    from meg_qc.calculation.references import compute_references
    installed(("fakeref", lambda: fake_plugin(reference_provider=FakeProvider(why="no MEG"))))
    asked = []
    raw = SimpleNamespace(n_times=100, info={})
    compute_references(registry.settings_for_run(None), run_ecg=True, run_eog=True,
                       work_root="/tmp/meegqc_refs_test",
                       load_recording=lambda: (raw, lambda: asked.append(1) or "rec.fif"))
    assert asked == []


def test_a_reference_nobody_asked_for_is_not_computed(installed, monkeypatch):
    settings = FAKE_SETTINGS.replace("ecg = independent", "ecg = off")
    from meg_qc.calculation.references import compute_references
    installed(("fakeref", lambda: fake_plugin(default_settings=settings)))
    loads = []
    refs = compute_references(registry.settings_for_run(None), run_ecg=True,
                              run_eog=True, work_root="/tmp/meegqc_refs_test",
                              load_recording=lambda: loads.append(1))
    assert refs == [] and loads == []


# ── installing ────────────────────────────────────────────────────────────

def test_a_catalog_name_installs_its_package():
    assert manage.resolve_spec("megnet") == "meegqc-megnet"
    assert manage.resolve_spec("./my-plugin") == "./my-plugin"


def test_pip_is_used_when_the_environment_has_it(monkeypatch):
    monkeypatch.setattr(manage, "_has_pip", lambda: True)
    assert manage.installer_command("install", "pkg") == [sys.executable, "-m", "pip", "install", "pkg"]
    assert manage.installer_command("uninstall", "pkg")[-2:] == ["-y", "pkg"]


def test_uv_installs_into_this_python_when_there_is_no_pip(monkeypatch):
    monkeypatch.setattr(manage, "_has_pip", lambda: False)
    monkeypatch.setattr(manage, "_uv", lambda: "/bin/uv")
    assert manage.installer_command("install", "pkg", upgrade=True) == [
        "/bin/uv", "pip", "install", "--python", sys.executable, "--upgrade", "pkg"]


def test_without_pip_or_uv_the_reason_is_given(monkeypatch):
    monkeypatch.setattr(manage, "_has_pip", lambda: False)
    monkeypatch.setattr(manage, "_uv", lambda: None)
    with pytest.raises(manage.InstallError, match="neither pip nor uv"):
        manage.installer_command("install", "pkg")


def test_the_cli_lists_installed_and_available_plugins(installed, capsys):
    installed(("fakeref", fake_plugin), ("broken", lambda: 1 / 0))
    assert cli.main(["list"]) == 0
    out = capsys.readouterr().out
    assert "fakeref      FakeRef 1.0" in out
    assert "broken       NOT LOADED" in out
    assert "megnet       MEGnet" in out          # from the catalog


def test_the_cli_describes_one_plugin(installed, capsys):
    installed(("fakeref", fake_plugin))
    assert cli.main(["info", "fakeref"]) == 0
    out = capsys.readouterr().out
    assert "section [FAKEREF]" in out and "ECG/EOG reference signals" in out
    assert cli.main(["info", "nothing-like-this"]) == 1


def test_an_older_section_of_a_plugin_is_completed_with_its_defaults(installed):
    """An old [MEGNET] without ecg/eog: the saved copy shows what the run used."""
    installed(("fakeref", fake_plugin))
    text = "[GENERAL]\nSTD = True\n\n[FAKEREF]\n# mine\nthreshold = 0.7\n\n[EEG]\nmontage = auto\n"
    out = registry.with_plugin_sections(text)
    cfg = configparser.ConfigParser()
    cfg.read_string(out)
    assert dict(cfg["FAKEREF"]) == {"threshold": "0.7", "ecg": "independent", "eog": "off",
                                    "ecg_lowpass_h_freq": "30"}
    assert "# mine" in out and cfg["EEG"]["montage"] == "auto"
    assert registry.with_plugin_sections(out) == out


def test_settings_this_meegqc_does_not_read_are_named(installed, tmp_path, capsys):
    from pathlib import Path
    import meg_qc
    from meg_qc.calculation.initial_meg_qc import get_all_config_params
    installed()
    ini = tmp_path / "settings.ini"
    text = (Path(meg_qc.__file__).parent / "settings" / "settings.ini").read_text()
    ini.write_text(text.replace("[EOG]\n", "[EOG]\nmegnet_fallback = True\n")
                   + "\n[MEGNET]\nprobability_threshold = 0.9\n")
    get_all_config_params(str(ini))
    out = capsys.readouterr().out
    assert "ignored: [EOG] megnet_fallback, [MEGNET]." in out
    assert "MEGnet is a plugin now" in out


def test_the_runs_settings_copy_keeps_a_non_utf8_file_and_adds_the_plugin(installed, tmp_path):
    """A settings.ini saved as ANSI on Windows used to stop the run's save."""
    from meg_qc.calculation.meg_qc_pipeline import _write_used_settings
    installed(("fakeref", fake_plugin))
    src, dst = tmp_path / "settings.ini", tmp_path / "copy.ini"
    original = "[GENERAL]\n# J\xfcrgen\nSTD = True\n".encode("cp1252")
    src.write_bytes(original)
    _write_used_settings(str(src), str(dst))
    out = dst.read_bytes()
    assert out.startswith(original) and b"[FAKEREF]" in out
