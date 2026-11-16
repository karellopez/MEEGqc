"""How the reports recognise and name a plugin's ECG/EOG reference."""
from __future__ import annotations

import os

import pandas as pd
import pytest

from meg_qc.plotting import reference_sources as rs


@pytest.mark.parametrize("name, source", [
    ("sub-01_task-x_desc-ECGchannelmegnet_meg.tsv", "megnet"),
    ("sub-01_task-x_desc-EOGsmegnet_meg.tsv", "megnet"),
    ("sub-01_task-x_desc-ECGsanyplugin_meg.tsv", "anyplugin"),
    ("sub-01_task-x_desc-ECGchannel_meg.tsv", None),
    ("sub-01_task-x_desc-ECGs_meg.tsv", None),
    ("sub-01_task-x_desc-STDs_meg.tsv", None),
])
def test_the_plugin_source_of_a_file(name, source):
    assert rs.plugin_source_of_name(f"/some/folder/{name}") == source


def test_a_plugins_name_comes_from_its_table(tmp_path):
    f = tmp_path / "sub-01_desc-ECGchannelzz_meg.tsv"
    pd.DataFrame({"ECG": [1.0, 2.0], "signal_source": ["zz", None],
                  "signal_source_label": ["ZedZed", None]}).to_csv(f, sep="\t", index=False)
    assert rs.source_label("zz", f) == "ZedZed"
    assert rs.source_label("unknownplugin") == "unknownplugin"


def test_sources_are_described_and_marked(monkeypatch):
    rs.remember_label("megnet", "MEGnet")
    assert rs.describe_source("correlation_megnet") == "MEGnet synthetic component"
    assert rs.describe_source("recorded") == "recorded channel"
    assert rs.describe_source("n/a") == "n/a"
    assert rs.symbols_for(["recorded", "megnet", "mne_reconstructed", "other"]) == {
        "recorded": "circle", "mne_reconstructed": "diamond", "megnet": "star",
        "other": "triangle-up"}


def test_the_contribution_counts_analysed_and_replacing_references(monkeypatch):
    """MEGnet ran beside the recorded ECG on every recording (its default):
    the report said it "produced nothing usable"."""
    monkeypatch.setattr("meg_qc.plugins.registry.reference_providers", lambda: [])
    base = pd.DataFrame({
        "ecg_signal_source": ["recorded", "megnet", "recorded", "recorded"],
        "eog_signal_source": ["recorded", "recorded", "recorded", "n/a"],
        "plugin_references": ["megnet:MEGnet", "megnet:MEGnet", "megnet:MEGnet", ""]})
    html = rs.contribution_html(base, ("ecg_signal_source", "eog_signal_source"))
    assert "<strong>MEGnet contribution:</strong> its reference was analysed for " \
           "<strong>3</strong> of 4 recordings (75%)" in html
    assert "replaced the recorded or reconstructed reference in <strong>1</strong>" in html
    nothing = base.assign(plugin_references="", ecg_signal_source="recorded")
    nothing_html = rs.contribution_html(nothing, ("ecg_signal_source",))
    assert nothing_html == ""                       # no plugin installed, none in the data


def test_without_the_record_only_replacements_are_claimed(monkeypatch):
    """Outputs made before plugin_references existed."""
    monkeypatch.setattr("meg_qc.plugins.registry.reference_providers", lambda: [])
    rs.remember_label("megnet", "MEGnet")
    base = pd.DataFrame({"ecg_signal_source": ["recorded", "megnet", "megnet", "n/a"]})
    html = rs.contribution_html(base, ("ecg_signal_source",))
    assert "replaced the recorded or reconstructed one in <strong>2</strong> of 4" in html
    assert "produced no reference" not in html


def test_an_installed_plugin_that_produced_nothing_says_so(monkeypatch):
    plugin = type("P", (), {"name": "megnet"})()
    monkeypatch.setattr("meg_qc.plugins.registry.reference_providers", lambda: [plugin])
    base = pd.DataFrame({"ecg_signal_source": ["recorded"], "plugin_references": [""]})
    assert "produced no reference here" in rs.contribution_html(base, ("ecg_signal_source",))


def test_a_recordings_plugin_references_come_from_its_descs():
    refs = rs.plugin_references_in(["ECGchannel", "ECGs", "ECGchannelmegnet", "EOGsmegnet"],
                                   lambda desc: "MEGnet")
    assert refs == {"megnet": "MEGnet"}
    assert rs.parse_plugin_references(rs.format_plugin_references(refs)) == refs


@pytest.mark.skipif(os.name == "nt", reason="symbolic links need extra rights on Windows")
def test_a_file_found_by_the_query_is_not_added_twice_through_a_link(tmp_path):
    """On macOS /tmp is a link to /private/tmp: the report found each plugin
    file twice and stopped with 'Different number of tsvs and entities'."""
    from meg_qc.plotting.meg_qc_plots import _augment_with_plugin_metric_files
    real = tmp_path / "real"
    calc = real / "derivatives" / "MEEGqc" / "calculation" / "sub-01"
    calc.mkdir(parents=True)
    f = calc / "sub-01_task-x_desc-ECGsmegnet_meg.tsv"
    f.write_text("a\n1\n")
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    found = [str(link / "derivatives" / "MEEGqc" / "calculation" / "sub-01" / f.name)]
    paths, _ = _augment_with_plugin_metric_files(
        metric="ECGs", tsv_paths=list(found), entities_obj=[],
        calculated_derivs_folder="derivatives/MEEGqc/calculation", query_base=str(link))
    assert paths == found


def test_a_per_channel_table_takes_the_name_of_its_reference(tmp_path):
    """desc-ECGs<plugin> has no name column: without the plugin installed its
    figures went to a second tab named after the plugin's id."""
    ref = tmp_path / "sub-01_task-x_desc-ECGchannelzq_meg.tsv"
    pd.DataFrame({"zq_ECG": [1.0], "signal_source": ["zq"],
                  "signal_source_label": ["ZedQ"]}).to_csv(ref, sep="\t", index=False)
    per_channel = tmp_path / "sub-01_task-x_desc-ECGszq_meg.tsv"
    per_channel.write_text("name\tcorr\nA\t0.5\n")
    assert rs.source_label("zq", per_channel) == "ZedQ"


@pytest.mark.parametrize("value, key", [
    ("correlation_recorded", "recorded"), ("mean_threshold", "recorded"),
    ("reconstructed-bad", "mne_reconstructed"), ("correlation_reconstructed", "mne_reconstructed"),
    ("correlation_megnet", "megnet"), ("n/a", "")])
def test_old_method_names_are_the_calculations_own_sources(value, key):
    """Outputs before 2026-07 recorded the method; they are not plugins."""
    assert rs.source_key(value) == key
    assert rs.is_plugin_source(value) == (key not in ("", "recorded", "mne_reconstructed"))
