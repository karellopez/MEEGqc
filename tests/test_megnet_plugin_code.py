"""The MEGnet plugin without running MEGnet: which recordings it applies to,
its settings, how it picks components, and how MEEGqc sees it.

It is part of MEEGqc (``meg_qc/plugins/megnet``); these tests need neither
MEGnet's libraries nor test data.
"""
from __future__ import annotations

import mne
import numpy as np
import pytest

import meg_qc.plugins.megnet as meegqc_megnet
from meg_qc.plugins.megnet.applies import MIN_SECONDS, why_not
from meg_qc.plugins.megnet.run import compose_signal, pick_components


# ── which recordings ──────────────────────────────────────────────────────

def _raw(kinds, *, sfreq=1000.0, seconds=MIN_SECONDS + 5, coil=None, n=40):
    names = [f"{k.upper()}{i:03d}" for k in kinds for i in range(n)]
    types = [k for k in kinds for _ in range(n)]
    info = mne.create_info(names, sfreq, types)
    rng = np.random.default_rng(0)
    for ch in info["chs"]:
        ch["loc"][:3] = rng.normal(size=3)
        if coil is not None:
            ch["coil_type"] = coil
    return mne.io.RawArray(np.zeros((len(names), int(seconds * sfreq))), info, verbose=False)


def test_a_megin_recording_long_enough_applies():
    assert why_not(_raw(["mag", "grad"])) is None


@pytest.mark.parametrize("coil, system", [(5001, "CTF"), (4001, "4D"), (6001, "KIT")])
def test_the_other_trained_systems_apply(coil, system):
    assert why_not(_raw(["mag"], coil=coil)) is None


@pytest.mark.parametrize("coil", [8001, 8101, 8201, 2, 2000, 7001, 9001])
def test_systems_it_was_never_trained_on_are_refused(coil):
    reason = why_not(_raw(["mag"], coil=coil))
    assert reason and "trained on" in reason and str(coil) in reason


def test_an_eeg_recording_is_refused():
    assert why_not(_raw(["eeg"])) == "it needs MEG channels and this recording has none"


def test_a_short_recording_is_refused_with_its_length():
    reason = why_not(_raw(["mag", "grad"], seconds=60))
    assert "at least 105 s" in reason and "60.0 s" in reason


def test_a_low_sampling_rate_is_refused():
    assert "250 Hz" in why_not(_raw(["mag", "grad"], sfreq=200.0))


def test_too_few_magnetometers_are_refused():
    assert "at least 20" in why_not(_raw(["mag", "grad"], n=10))


def test_missing_sensor_positions_are_refused():
    raw = _raw(["mag", "grad"])
    raw.info["chs"][0]["loc"][:3] = 0
    assert "sensor positions" in why_not(raw)


def test_gradiometers_alone_are_refused():
    assert "magnetometers" in why_not(_raw(["grad"]))


# ── settings ──────────────────────────────────────────────────────────────

def test_the_plugin_describes_itself():
    p = meegqc_megnet.plugin()
    assert (p.name, p.label, p.settings_section) == ("megnet", "MEGnet", "MEGNET")
    assert p.default_settings.startswith("[MEGNET]")
    assert p.reference_provider is not None


def test_the_default_settings_parse_to_the_documented_defaults():
    import configparser
    cfg = configparser.ConfigParser()
    cfg.read_string(meegqc_megnet.plugin().default_settings)
    section = dict(cfg["MEGNET"])
    assert section["ecg"] == section["eog"] == "independent"
    assert (section["ecg_lowpass_h_freq"], section["eog_lowpass_h_freq"]) == ("40", "20")
    params = meegqc_megnet.parse_settings(section)
    assert params["probability_threshold"] == 0.9 and params["max_components_per_class"] == 1
    for dead in ("megnet_optional_dependency", "save_full_ica_timeseries",
                 "megnet_component_strategy"):
        assert dead not in section


def test_an_old_threshold_above_one_is_kept_and_explained(capsys):
    """An existing configuration gives the same result as before, and says why."""
    assert meegqc_megnet.parse_settings({"probability_threshold": "9.0"})["probability_threshold"] == 9.0
    assert "outside 0-1" in capsys.readouterr().out


def test_meegqc_loads_the_installed_plugin():
    from meg_qc.plugins.registry import discover
    found = {f.name: f for f in discover(refresh=True)}
    if "megnet" not in found:
        pytest.skip("MEGnet's libraries (megnet-neuro) are not installed here")
    assert found["megnet"].plugin is not None, found["megnet"].error


# ── component choice ──────────────────────────────────────────────────────

def _probs(values, class_id=2):
    classes = np.full(len(values), class_id)
    probs = np.zeros((len(values), 4))
    probs[:, class_id] = values
    return classes, probs


def test_combining_uses_the_components_that_reach_the_threshold():
    classes, probs = _probs([0.95, 0.92, 0.40])
    idx, _ = pick_components(classes, probs, 2, {
        "probability_threshold": 0.9, "combine_components": True, "max_components_per_class": 3})
    assert list(idx) == [0, 1]


def test_the_most_likely_component_is_used_when_none_reaches_it():
    classes, probs = _probs([0.6, 0.3])
    idx, _ = pick_components(classes, probs, 2, {
        "probability_threshold": 0.9, "combine_components": True, "max_components_per_class": 3})
    assert list(idx) == [0]


def test_without_combining_only_the_most_likely_is_used():
    classes, probs = _probs([0.95, 0.94])
    assert list(pick_components(classes, probs, 2, {"probability_threshold": 0.9})[0]) == [0]


def test_combined_components_are_weighted_by_probability():
    ts = np.array([[1.0, 1.0], [3.0, 3.0]])
    assert compose_signal(ts, np.array([0, 1]), np.array([1.0, 3.0])).tolist() == [2.5, 2.5]
    assert compose_signal(ts, np.array([], dtype=int), np.array([])).size == 0
