"""The Plugins dialog and plugin sections in the settings editor (offscreen)."""
from __future__ import annotations

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytestmark = pytest.mark.gui

SETTINGS = """[FAKEREF]
#ecg (str) - How the ECG metric uses it. Default: independent
ecg = independent
#eog (str) - How the EOG metric uses it. Default: off
eog = off
"""


@pytest.fixture(scope="module")
def qapp():
    pytest.importorskip("PyQt6")
    from PyQt6.QtWidgets import QApplication
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def fake_plugins(monkeypatch):
    """One loaded plugin with a settings section and one that failed to load."""
    from meg_qc.plugins import registry
    from meg_qc.plugins.api import Plugin
    from meg_qc.plugins.registry import FoundPlugin

    class Provider:
        def applies_to(self, raw, params):
            return None

        def compute(self, raw, params, *, data_path, work_dir):
            raise AssertionError("not called here")

    plugin = Plugin(name="fakeref", label="FakeRef", version="2.0",
                    summary="A plugin used by the tests.", settings_section="FAKEREF",
                    default_settings=SETTINGS, reference_provider=Provider())
    found = [FoundPlugin("fakeref", "meegqc-fakeref", "2.0", plugin),
             FoundPlugin("broken", "meegqc-broken", "0.1", None, "could not be loaded: oops")]
    monkeypatch.setattr(registry, "discover", lambda refresh=False: found)
    yield plugin


def _texts(widget):
    from PyQt6.QtWidgets import QLabel, QGroupBox
    return ([w.title() for w in widget.findChildren(QGroupBox)]
            + [w.text() for w in widget.findChildren(QLabel)])


def test_the_dialog_lists_installed_broken_and_installable_plugins(qapp, fake_plugins):
    from meg_qc.miscellaneous.GUI.plugins_dialog import PluginsDialog
    d = PluginsDialog()
    try:
        texts = _texts(d)
        assert "FakeRef" in texts and "Installed, version 2.0" in texts
        assert any("but not loaded: could not be loaded: oops" in t for t in texts)
        assert "MEGnet" in texts and "Not installed (package megnet-neuro)" in texts
        assert any("[FAKEREF] section" in t for t in texts)
    finally:
        d.close()


def test_an_installer_command_runs_in_the_background_and_reports(qapp, fake_plugins):
    """Runs `meegqc-plugins list` the way Install/Remove run pip: in a QProcess."""
    from meg_qc.miscellaneous.GUI.plugins_dialog import PluginsDialog
    d = PluginsDialog()
    try:
        d._run(["list"], "Listing")
        assert not d.btn_close.isEnabled()          # no closing mid-install
        deadline = time.time() + 120
        while d._process is not None and time.time() < deadline:
            qapp.processEvents()
            time.sleep(0.02)
        assert d._process is None
        assert d.status.text() == "Listing: done. New runs use the change."
        assert "MEEGqc plugin API" in d.output.toPlainText()
        assert d.btn_close.isEnabled()
    finally:
        d.close()


def test_changes_are_refused_while_a_calculation_runs(qapp, fake_plugins):
    from meg_qc.miscellaneous.GUI.plugins_dialog import PluginsDialog
    asked = []
    d = PluginsDialog(can_change=lambda action: asked.append(action) or False)
    try:
        d._install("megnet", "MEGnet")
        assert asked == ["Install plugin"] and d._process is None
    finally:
        d.close()


def test_the_settings_editor_shows_a_plugins_section(qapp, fake_plugins, monkeypatch):
    import os.path as p
    import meg_qc
    from meg_qc.miscellaneous.GUI import megqcGUI
    monkeypatch.setattr(megqcGUI, "reference_providers", lambda: [fake_plugins])
    ini = p.join(p.dirname(meg_qc.__file__), "settings", "settings.ini")
    dlg = megqcGUI.SettingsEditorDialog(ini, ini, "test")
    try:
        assert "FAKEREF" in dlg.section_boxes
        widget, kind = dlg.widgets[("FAKEREF", "ecg")]
        assert kind == "enum" and widget.currentText() == "independent"
        assert [widget.itemText(i) for i in range(widget.count())] == [
            "off", "fallback", "independent", "both"]
        assert "How the ECG metric uses it" in dlg.labels[("FAKEREF", "ecg")].toolTip()
        assert dlg.to_config()["FAKEREF"]["eog"] == "off"
    finally:
        dlg.close()


def test_the_main_window_has_a_plugins_button(qapp):
    from meg_qc.miscellaneous.GUI.megqcGUI import MainWindow
    w = MainWindow()
    try:
        assert w.btn_plugins.text() == "Plugins"
    finally:
        w.close()


def test_esc_does_not_close_the_dialog_during_an_installation(qapp, fake_plugins):
    from meg_qc.miscellaneous.GUI.plugins_dialog import PluginsDialog
    d = PluginsDialog()
    try:
        d._process = object()          # an installation is running
        d.show()
        d.reject()                     # what Esc does
        assert d.isVisible() and "Wait for the installer" in d.status.text()
        d._process = None
    finally:
        d.close()


def test_an_installer_that_cannot_start_releases_the_dialog(qapp, fake_plugins, monkeypatch):
    from meg_qc.miscellaneous.GUI import plugins_dialog
    monkeypatch.setattr(plugins_dialog.sys, "executable", "/no/such/python")
    d = plugins_dialog.PluginsDialog()
    try:
        d._run(["list"], "Listing")
        deadline = time.time() + 30
        while d._process is not None and time.time() < deadline:
            qapp.processEvents()
            time.sleep(0.02)
        assert d._process is None and d.btn_close.isEnabled()
        assert "could not start" in d.status.text()
    finally:
        d.close()


def test_the_settings_editor_keeps_the_section_of_a_removed_plugin(qapp, tmp_path):
    import meg_qc
    from pathlib import Path
    from meg_qc.miscellaneous.GUI.megqcGUI import SettingsEditorDialog
    defaults = Path(meg_qc.__file__).parent / "settings" / "settings.ini"
    mine = tmp_path / "settings.ini"
    mine.write_text(defaults.read_text() + "\n[GONEPLUGIN]\nmode = keep\n")
    dlg = SettingsEditorDialog(mine, defaults, "test")
    try:
        assert "GONEPLUGIN" not in dlg.section_boxes        # not shown
        assert dict(dlg.to_config()["GONEPLUGIN"]) == {"mode": "keep"}   # but kept
    finally:
        dlg.close()
