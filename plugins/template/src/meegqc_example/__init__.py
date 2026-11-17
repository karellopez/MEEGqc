"""A MEEGqc plugin template: a reference provider that finds nothing.

Copy the folder, rename ``example`` everywhere, and replace ``compute``. The
guidelines are in plugins/README.md; meg_qc/plugins/megnet (MEGnet) is a complete
plugin.
"""
from __future__ import annotations

from importlib import resources
from typing import Mapping, Optional

from meg_qc.plugins import PLUGIN_API_VERSION, Plugin, ReferenceResult


def parse_settings(section: Mapping[str, str]) -> dict:
    """The [EXAMPLE] section as the provider's parameters."""
    return {"min_seconds": float(section.get("min_seconds", 30))}


class ExampleProvider:
    def applies_to(self, raw, params: dict) -> Optional[str]:
        seconds = raw.n_times / raw.info["sfreq"]
        if seconds < params["min_seconds"]:
            return (f"it needs at least {params['min_seconds']:g} s of data; "
                    f"this recording is {seconds:.1f} s")
        return None

    def compute(self, raw, params: dict, *, data_path: str, work_dir: str) -> ReferenceResult:
        # Import heavy libraries here, not at the top of the module.
        return ReferenceResult("ok", ecg=None, eog=None)


def plugin() -> Plugin:
    return Plugin(
        name="example",
        label="Example",
        version="0.1.0",
        summary="A template that adds nothing.",
        api_version=PLUGIN_API_VERSION,
        settings_section="EXAMPLE",
        default_settings=resources.files("meegqc_example").joinpath("settings.ini")
        .read_text(encoding="utf-8"),
        parse_settings=parse_settings,
        reference_provider=ExampleProvider(),
    )
