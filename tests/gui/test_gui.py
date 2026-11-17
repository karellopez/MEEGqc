"""Headless GUI tests (offscreen). Cover construction, the profile-mode controls,
and the settings-dialog grey-out system."""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytestmark = pytest.mark.gui


@pytest.fixture(scope="module")
def qapp():
    pytest.importorskip("PyQt6")
    from PyQt6.QtWidgets import QApplication
    yield QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _clean_run_settings(qapp):
    """Start every test from default parallel (run/) settings. The store is the
    session's own (MEEGQC_SETTINGS_DIR, set in conftest.py), never the user's."""
    from meg_qc.miscellaneous.GUI.settings_store import settings_store
    s = settings_store("MEEGqc")
    s.remove("run")
    s.sync()
    yield
    s.remove("run")
    s.sync()


def test_tests_never_touch_the_users_preferences(qapp):
    """Every GUI store points into the session's folder, on every OS."""
    from meg_qc.miscellaneous.GUI.settings_store import settings_store
    folder = os.path.realpath(os.environ["MEEGQC_SETTINGS_DIR"])
    for app in ("MEEGqc", "MEEGqc_Viewer", "MEGqc", "MEGqc_Viewer"):
        path = os.path.realpath(settings_store(app).fileName())
        assert path.startswith(folder), path


@pytest.fixture
def main_window(qapp):
    from meg_qc.miscellaneous.GUI.megqcGUI import MainWindow
    w = MainWindow()
    yield w
    w.close()


def test_window_constructs(main_window):
    assert main_window.isVisible() in (True, False)  # constructed without raising


def test_analysis_mode_options_are_canonical(main_window):
    cmb = main_window.cmb_analysis_mode
    data = [cmb.itemData(i) for i in range(cmb.count())]
    assert data == ["non-profile", "new-profile", "reuse-profile", "latest-profile"]


def test_non_profile_disables_profile_controls(main_window):
    w = main_window
    w._set_analysis_mode("non-profile")
    assert not w.edit_analysis_id.isEnabled()
    assert not w.btn_load_profiles.isEnabled()
    assert not w.btn_refresh_profiles.isEnabled()

    w._set_analysis_mode("reuse-profile")
    assert w.edit_analysis_id.isEnabled()
    assert w.btn_load_profiles.isEnabled()
    assert w.btn_refresh_profiles.isEnabled()


def test_collect_returns_canonical_mode(main_window):
    main_window._set_analysis_mode("new-profile")
    mode, analysis_id, cfg_policy, sub_policy = main_window._collect_analysis_profile_settings()
    assert mode == "new-profile"
    assert cfg_policy in ("provided", "latest_saved", "stop")
    assert sub_policy in ("skip", "rerun", "stop")


@pytest.fixture
def settings_dialog(qapp):
    import os.path as p
    import meg_qc
    from meg_qc.miscellaneous.GUI.megqcGUI import SettingsEditorDialog
    ini = p.join(p.dirname(meg_qc.__file__), "settings", "settings.ini")
    dlg = SettingsEditorDialog(ini, ini, "test")
    yield dlg
    dlg.close()


def test_metric_switch_greys_whole_section(settings_dialog, qapp):
    """Turning a [GENERAL] metric off greys that metric's whole section box."""
    dlg = settings_dialog
    std = dlg.widgets[("GENERAL", "STD")][0]
    std.setChecked(False); qapp.processEvents()
    assert not dlg.section_boxes["STD"].isEnabled()
    std.setChecked(True); qapp.processEvents()
    assert dlg.section_boxes["STD"].isEnabled()
    # Head defaults to False -> its section is greyed on open.
    assert not dlg.section_boxes["Head_movement"].isEnabled()


def test_compute_gqi_greys_rows_but_keeps_switch(settings_dialog, qapp):
    """compute_gqi=off greys the other GQI rows (widget + label) but the switch stays live."""
    dlg = settings_dialog
    gqi = dlg.widgets[("GlobalQualityIndex", "compute_gqi")][0]
    others = [k for (s, k) in dlg.widgets if s == "GlobalQualityIndex" and k != "compute_gqi"]
    gqi.setChecked(False); qapp.processEvents()
    assert gqi.isEnabled()
    for k in others:
        assert not dlg.widgets[("GlobalQualityIndex", k)][0].isEnabled()
        assert not dlg.labels[("GlobalQualityIndex", k)].isEnabled()


def test_njobs_default_is_safe_and_all_cores_toggles(main_window, qapp):
    """Calc/plot jobs default to 1 (not -1); the 'All cores' checkbox sets -1
    and restores the previous value when unticked."""
    w = main_window
    assert w.jobs.value() == 1
    assert w.plot_jobs.value() == 1
    assert not w.chk_all_cores.isChecked()

    # Use a value within the CPU-capped range: the spinbox max is os.cpu_count()
    # (few cores on CI runners), so a hard-coded 4 would clamp to the maximum.
    n = w.jobs.maximum()
    w.jobs.setValue(n); qapp.processEvents()
    w.chk_all_cores.setChecked(True); qapp.processEvents()
    assert w.jobs.value() == -1 and not w.jobs.isEnabled()
    w.chk_all_cores.setChecked(False); qapp.processEvents()
    assert w.jobs.value() == n and w.jobs.isEnabled()

    w.chk_plot_all_cores.setChecked(True); qapp.processEvents()
    assert w.plot_jobs.value() == -1 and not w.plot_jobs.isEnabled()
    w.chk_plot_all_cores.setChecked(False); qapp.processEvents()
    assert w.plot_jobs.value() == 1 and w.plot_jobs.isEnabled()


def test_reset_parallel_defaults_and_persistence(qapp):
    """Reset-defaults restores 1 core; the parallel settings persist across a
    fresh window."""
    from meg_qc.miscellaneous.GUI.megqcGUI import MainWindow
    w = MainWindow()
    try:
        # Value within the CPU-capped range (spinbox max is os.cpu_count()).
        n = w.jobs.maximum()
        w.jobs.setValue(n); w.chk_plot_all_cores.setChecked(True); qapp.processEvents()
        w2 = MainWindow()  # restores from QSettings
        try:
            assert w2.jobs.value() == n
            assert w2.chk_plot_all_cores.isChecked() and w2.plot_jobs.value() == -1
            w2._reset_parallel_defaults(); qapp.processEvents()
            assert w2.jobs.value() == 1 and not w2.chk_all_cores.isChecked()
            assert w2.plot_jobs.value() == 1 and not w2.chk_plot_all_cores.isChecked()
        finally:
            w2.close()
    finally:
        w.close()


def test_greyout_dims_label_not_just_field(settings_dialog, qapp):
    """The label dims alongside the field (the colour fix)."""
    dlg = settings_dialog
    cmb = dlg.widgets[("Epoching", "epoching_strategy")][0]
    cmb.setCurrentText("fixed"); qapp.processEvents()
    assert not dlg.widgets[("Epoching", "event_dur")][0].isEnabled()
    assert not dlg.labels[("Epoching", "event_dur")].isEnabled()
    assert dlg.widgets[("Epoching", "fixed_epoch_duration")][0].isEnabled()
    cmb.setCurrentText("events"); qapp.processEvents()
    assert dlg.widgets[("Epoching", "event_dur")][0].isEnabled()
    assert not dlg.widgets[("Epoching", "fixed_epoch_duration")][0].isEnabled()


def test_every_setting_name_is_shown_whole(settings_dialog, qapp):
    """Long names (min_distance_between_different_muscle_events) were cut off
    by the section boxes; they now break after an underscore instead. A box
    could also be squeezed below what its form needs, which with Windows'
    Segoe UI cut allowed_range_of_peaks_stds: the dialog now opens wide enough
    for every column, and a narrower window scrolls sideways instead."""
    from PyQt6.QtWidgets import QScrollArea
    dlg = settings_dialog
    dlg.show(); qapp.processEvents()
    assert not dlg.findChild(QScrollArea).horizontalScrollBar().isVisible()
    for width in (dlg.width(), 700):
        dlg.resize(width, 760); qapp.processEvents()
        cut = [k for k, lab in dlg.labels.items()
               if lab.width() < lab.sizeHint().width()
               or lab.height() < lab.sizeHint().height()]
        assert cut == [], width
    name = dlg.labels[("Muscle", "min_distance_between_different_muscle_events")]
    assert "\n" in name.text()
    assert name.text().replace("\n", "") == "min_distance_between_different_muscle_events"
    assert "\n" not in dlg.labels[("Epoching", "event_dur")].text()


def test_decimals_use_a_point_whatever_the_system_language(qapp):
    """A German or Spanish system showed 0,900 for settings.ini's 0.9."""
    import os.path as p
    import meg_qc
    from PyQt6.QtCore import QLocale
    from meg_qc.miscellaneous.GUI.megqcGUI import SettingsEditorDialog
    ini = p.join(p.dirname(meg_qc.__file__), "settings", "settings.ini")
    before = QLocale()
    QLocale.setDefault(QLocale(QLocale.Language.German, QLocale.Country.Germany))
    try:
        dlg = SettingsEditorDialog(ini, ini, "test")
        try:
            shown = [w.text() for w, kind in dlg.widgets.values() if kind == "float"]
            assert shown and all("," not in t for t in shown), shown
            assert dlg.widgets[("Epoching", "epoch_tmax")][0].text() == "1.000"
        finally:
            dlg.close()
    finally:
        QLocale.setDefault(before)



# ── parallelism ──────────────────────────────────────────────────────────
# The old "Hungry jobs" checkbox is gone: parallelism is per recording, so
# --n_jobs already spreads one subject's runs across workers and there is
# nothing left to opt into.

def test_no_hungry_job_control_remains(main_window):
    assert not hasattr(main_window, "chk_hungry_job")


def test_calc_jobs_control_still_present(main_window):
    """--n_jobs is now the only parallelism control, so it must stay wired."""
    assert hasattr(main_window, "jobs")
    assert main_window.jobs.value() >= 1


# ── memory guard ─────────────────────────────────────────────────────────
# On by default, can be switched off, and how permissive it is comes from a
# level (Cautious / Balanced / Permissive) or three numbers; editing a number
# makes the level Custom. Both Run buttons pass the settings to the run.

def test_memory_guard_defaults_to_on_and_balanced(main_window):
    from meg_qc.calculation.resource_budget import level_values
    w = main_window
    assert w.chk_mem_guard.isChecked()
    assert w.mem_level.currentData() == "balanced"
    fraction, reserve, expansion = level_values("balanced")
    assert w.mem_fraction.value() == round(fraction * 100)
    assert w.mem_reserve.value() == pytest.approx(reserve)
    assert w.mem_expansion.value() == pytest.approx(expansion)
    assert w.mem_status.text().startswith("Now:")


def test_choosing_a_level_fills_in_its_numbers(main_window, qapp):
    from meg_qc.calculation.resource_budget import level_values
    w = main_window
    w.mem_level.setCurrentIndex(w.mem_level.findData("cautious")); qapp.processEvents()
    fraction, reserve, expansion = level_values("cautious")
    assert (w.mem_fraction.value(), w.mem_reserve.value(), w.mem_expansion.value()) == (
        round(fraction * 100), pytest.approx(reserve), pytest.approx(expansion))


def test_editing_a_number_makes_it_custom_and_back(main_window, qapp):
    w = main_window
    w.mem_fraction.setValue(70); qapp.processEvents()
    assert w.mem_level.currentData() == "custom"
    w.mem_fraction.setValue(50); qapp.processEvents()   # balanced's numbers again
    assert w.mem_level.currentData() == "balanced"


def test_switching_the_guard_off_greys_its_controls(main_window, qapp):
    w = main_window
    w.chk_mem_guard.setChecked(False); qapp.processEvents()
    for widget in (w.mem_level, w.mem_fraction, w.mem_reserve, w.mem_expansion):
        assert not widget.isEnabled()
    assert w.mem_status.text().startswith("Off:")
    w.chk_mem_guard.setChecked(True); qapp.processEvents()
    assert w.mem_level.isEnabled() and w.mem_status.text().startswith("Now:")


def test_memory_guard_settings_persist(qapp):
    from meg_qc.miscellaneous.GUI.megqcGUI import MainWindow
    w = MainWindow()
    try:
        w.mem_reserve.setValue(1.5); w.mem_expansion.setValue(7.0)
        w.chk_mem_guard.setChecked(False); qapp.processEvents()
        w2 = MainWindow()
        try:
            assert not w2.chk_mem_guard.isChecked()
            assert w2.mem_level.currentData() == "custom"
            assert w2.mem_reserve.value() == pytest.approx(1.5)
            assert w2.mem_expansion.value() == pytest.approx(7.0)
            w2._reset_parallel_defaults(); qapp.processEvents()
            assert w2.chk_mem_guard.isChecked()
            assert w2.mem_level.currentData() == "balanced"
        finally:
            w2.close()
    finally:
        w.close()


def test_a_saved_level_follows_the_machine(qapp, monkeypatch):
    """A named level is stored by name, so its reserve is recomputed where the
    GUI starts (a 64 GB machine keeps 8 GB free under Balanced, not 4)."""
    import meg_qc.calculation.resource_budget as rb
    from meg_qc.miscellaneous.GUI.megqcGUI import MainWindow
    w = MainWindow()
    try:
        w.mem_level.setCurrentIndex(w.mem_level.findData("balanced")); qapp.processEvents()
        w._persist_parallel_options()
    finally:
        w.close()
    monkeypatch.setattr(rb, "memory_total_bytes", lambda: 64 * 1024 ** 3)
    w2 = MainWindow()
    try:
        assert w2.mem_level.currentData() == "balanced"
        assert w2.mem_reserve.value() == pytest.approx(8.0)
    finally:
        w2.close()


@pytest.mark.parametrize("button", ["start_calc", "start_all"])
def test_run_buttons_send_the_guard_settings(main_window, qapp, monkeypatch, button):
    """What the Run button hands to the worker process, without starting it."""
    import json
    from meg_qc.miscellaneous.GUI import megqcGUI
    sent = []
    monkeypatch.setattr(megqcGUI.Worker, "start",
                        lambda self: sent.append(json.loads(self._build_payload())))
    w = main_window
    monkeypatch.setattr(w, "_collect_dataset_paths", lambda: ["/data/ds"])
    w.jobs.setValue(min(4, w.jobs.maximum()))
    w.mem_level.setCurrentIndex(w.mem_level.findData("permissive"))
    w.mem_expansion.setValue(5.0)
    w.chk_mem_guard.setChecked(False); qapp.processEvents()
    getattr(w, button)()
    assert len(sent) == 1
    guard = {k: sent[0]["kwargs"][k] for k in
             ("memory_guard", "mem_fraction", "mem_reserve_gb", "assume_expansion")}
    assert guard == {"memory_guard": False, "mem_fraction": 0.8,
                     "mem_reserve_gb": w.mem_reserve.value(), "assume_expansion": 5.0}
    w.workers.clear()


# ── output layout (PR #144) ──────────────────────────────────────────────
# The literal layout needs an output folder, so the choice is only active when
# one is set, and every Run action passes it on.

def test_output_layout_needs_an_output_folder(main_window, qapp):
    w = main_window
    assert not w.output_layout.isEnabled() and w._output_layout() == "bids"
    w.derivatives_dir.setText("/tmp/meegqc_out"); qapp.processEvents()
    assert w.output_layout.isEnabled()
    w.output_layout.setCurrentIndex(w.output_layout.findData("literal"))
    assert w._output_layout() == "literal"
    w.derivatives_dir.setText(""); qapp.processEvents()
    assert not w.output_layout.isEnabled() and w._output_layout() == "bids"


@pytest.mark.parametrize("button", ["start_calc", "start_plot", "start_gqi", "start_all"])
def test_every_run_action_sends_the_output_layout(main_window, qapp, monkeypatch, tmp_path, button):
    import json
    from PyQt6.QtWidgets import QMessageBox
    from meg_qc.miscellaneous.GUI import megqcGUI
    sent, dialogs = [], []
    monkeypatch.setattr(megqcGUI.Worker, "start",
                        lambda self: sent.append(json.loads(self._build_payload())))
    for kind in ("warning", "critical", "information", "question"):
        # A modal box would wait forever offscreen; record it instead.
        monkeypatch.setattr(QMessageBox, kind, lambda *a, _k=kind: (
            dialogs.append((_k, a[1:3])), QMessageBox.StandardButton.Ok)[1])
    w = main_window
    ds = tmp_path / "ds"
    ds.mkdir()
    monkeypatch.setattr(w, "_collect_dataset_paths", lambda: [str(ds)])
    w._set_analysis_mode("non-profile")   # plotting a new profile needs an ID
    w.derivatives_dir.setText(str(tmp_path / "out"))
    w.output_layout.setCurrentIndex(w.output_layout.findData("literal")); qapp.processEvents()
    getattr(w, button)()
    assert len(sent) == 1, f"{button} did not start a worker; dialogs: {dialogs}"
    assert sent[0]["kwargs"]["output_layout"] == "literal"
    w.workers.clear()


@pytest.mark.parametrize("button", ["start_plot", "start_gqi"])
def test_a_new_profile_without_id_is_explained_with_the_menus_names(
        main_window, qapp, monkeypatch, tmp_path, button):
    from PyQt6.QtWidgets import QMessageBox
    shown = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: (
        shown.append(str(a[2])), QMessageBox.StandardButton.Ok)[1])
    w = main_window
    monkeypatch.setattr(w, "_collect_dataset_paths", lambda: [str(tmp_path)])
    w._set_analysis_mode("new-profile")
    w.edit_analysis_id.setText("")
    getattr(w, button)()
    assert len(shown) == 1
    labels = {w.cmb_analysis_mode.itemText(i) for i in range(w.cmb_analysis_mode.count())}
    for name in ("New profile", "Reuse profile", "Latest profile", "Non-profile (single run)"):
        assert name in labels and f'"{name}"' in shown[0]
    for old in ("'new'", "'reuse'", "'latest'", "'legacy'"):
        assert old not in shown[0]


def test_the_gui_keeps_dates_readable_whatever_the_system_language(main_window):
    """Qt switches the process to the system locale; MNE then cannot parse an
    English month in a CTF header ("Illegal date: 01-Jan-1925") on a machine
    whose time locale is, say, Spanish. The window puts LC_TIME back to "C"."""
    import locale
    import time
    assert locale.setlocale(locale.LC_TIME) == "C"
    assert time.strptime("01-Jan-1925", "%d-%b-%Y").tm_year == 1925


# ── macOS: the stale-accessibility message ─────────────────────────────────

@pytest.mark.parametrize(("message", "shown"), [
    ("Called attribute on invalid object:  2147484176", False),
    ("Called accessibilityRole on invalid object:  2147484185", False),
    ("Called attribute on a valid object", True),
    ("QWindowsWindow::setGeometry: Unable to set geometry", True),
    ("Some message about an invalid object", True),
])
def test_only_the_stale_accessibility_message_is_dropped(message, shown, capsys):
    """Programs that read windows (screen readers, window or menu-bar
    managers, desktop AI apps) ask Qt about widgets it has already deleted,
    such as a faded tooltip, and Qt prints one line per question. The user's
    terminal filled with them (2026-11-16); every other message must still
    reach it, with Qt's category prefix."""
    from types import SimpleNamespace
    from meg_qc.miscellaneous.GUI import megqcGUI

    megqcGUI._qt_message(None, SimpleNamespace(category="default"), message)
    assert (capsys.readouterr().err == message + "\n") is shown
    megqcGUI._qt_message(None, SimpleNamespace(category="qt.qpa.window"), "geometry")
    assert capsys.readouterr().err == "qt.qpa.window: geometry\n"


def test_the_message_filter_is_installed_on_macos_only(monkeypatch):
    from meg_qc.miscellaneous.GUI import megqcGUI
    import PyQt6.QtCore as qtcore

    installed = []
    monkeypatch.setattr(qtcore, "qInstallMessageHandler", lambda h: installed.append(h))
    monkeypatch.setattr(megqcGUI.sys, "platform", "linux")
    megqcGUI._quiet_stale_accessibility_messages()
    assert installed == []
    monkeypatch.setattr(megqcGUI.sys, "platform", "darwin")
    megqcGUI._quiet_stale_accessibility_messages()
    assert installed == [megqcGUI._qt_message]
