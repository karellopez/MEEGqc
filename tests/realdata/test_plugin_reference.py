"""A reference plugin through the whole calculation and the subject report.

Uses a fake plugin whose ECG reference is a synthetic heartbeat, so the
generic path (settings, the plugin's derivatives, the report's source tab) is
tested without MEGnet. One job keeps the calculation in this process, where
the fake plugin is registered.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from meg_qc.plugins import registry
from meg_qc.plugins.api import Plugin, ReferenceResult, ReferenceSignal
from meg_qc.plugins.registry import FoundPlugin

pytestmark = pytest.mark.realdata

SETTINGS = """[FAKEREF]
#ecg (str) - How the ECG metric uses the fake heartbeat. Default: independent
ecg = independent
eog = off
ecg_lowpass_h_freq = 40
"""


class Heartbeat:
    """A clean 72 beats-per-minute pulse train."""

    def applies_to(self, raw, params):
        return None

    def compute(self, raw, params, *, data_path, work_dir):
        sfreq = raw.info["sfreq"]
        t = np.arange(raw.n_times) / sfreq
        beats = np.exp(-0.5 * ((t[:, None] - np.arange(0.4, t[-1], 60 / 72)[None, :]) / 0.012) ** 2)
        return ReferenceResult("ok", ecg=ReferenceSignal(beats.sum(axis=1), [0], [0.99]))


@pytest.fixture
def fake_plugin(monkeypatch):
    plugin = Plugin(name="fakeref", label="FakeRef", version="1.0", summary="test",
                    settings_section="FAKEREF", default_settings=SETTINGS,
                    reference_provider=Heartbeat())
    monkeypatch.setattr(registry, "_FOUND",
                        [FoundPlugin("fakeref", "meegqc-fakeref", "1.0", plugin)])
    yield plugin
    registry._FOUND = None


def _internal_cfg() -> str:
    import meg_qc
    return str(Path(meg_qc.__file__).parent / "settings" / "settings_internal.ini")


def test_a_plugin_reference_is_written_and_reported(fake_plugin, one_meg, isolated_dataset,
                                                     full_config):
    from meg_qc.test import run_calculation_dispatch, run_plotting_dispatch

    ds = isolated_dataset(one_meg[1])
    run_calculation_dispatch(dataset_paths=[str(ds)], config_file_path=str(full_config),
                             internal_config_file_path=_internal_cfg(), n_jobs=1,
                             analysis_mode="non-profile")
    root = ds / "derivatives" / "MEEGqc"
    ref = list((root / "calculation").rglob("*_desc-ECGchannelfakeref_*.tsv"))
    assert ref, "the plugin's ECG reference was not written"
    header, first = ref[0].read_text().splitlines()[:2]
    row = dict(zip(header.split("\t"), first.split("\t")))
    assert row["signal_source"] == "fakeref" and row["signal_source_label"] == "FakeRef"
    assert int(float(row["n_events"])) > 0

    used = list((root / "config").glob("*UsedSettings*.ini"))
    assert used and "[FAKEREF]" in used[0].read_text(), "the run's settings copy lacks the plugin"

    run_plotting_dispatch(dataset_paths=[str(ds)], qa_subject=True, analysis_mode="non-profile")
    html = "".join(p.read_text(encoding="utf-8", errors="replace")
                   for p in (root / "reports").rglob("*.html"))
    assert "FakeRef reference info" in html
