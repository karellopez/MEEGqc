"""Run with: pytest plugins/template/tests (with the template installed or on the path)."""
from types import SimpleNamespace

import meegqc_example


def test_the_plugin_describes_itself():
    p = meegqc_example.plugin()
    assert p.name == "example" and p.default_settings.startswith("[EXAMPLE]")


def test_a_short_recording_is_refused_with_its_length():
    raw = SimpleNamespace(n_times=1000, info={"sfreq": 100.0})
    reason = meegqc_example.ExampleProvider().applies_to(raw, {"min_seconds": 30.0})
    assert "10.0 s" in reason
