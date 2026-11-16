"""MEGnet for MEEGqc: ECG and EOG references from ICA components.

MEGnet (Treacher et al., NeuroImage 2021; package ``megnet-neuro``) labels the
ICA components of a MEG recording as heartbeat, blink, eye movement or other.
This plugin gives MEEGqc's ECG and EOG metrics those components as reference
signals, for recordings without a usable ECG or EOG channel or to compare with
one. Installed with ``meegqc-plugins install megnet`` or from MEEGqc's Plugins
dialog; MEEGqc finds it through the ``meegqc.plugins`` entry point.
"""
from __future__ import annotations

from importlib import metadata, resources
from typing import Mapping, Optional

from meg_qc.plugins.api import PLUGIN_API_VERSION, Plugin, ReferenceResult

from meegqc_megnet.applies import SYSTEMS, why_not

try:
    __version__ = metadata.version("meegqc-megnet")
except metadata.PackageNotFoundError:
    __version__ = "0.0.0"


def _bool(value) -> bool:
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def parse_settings(section: Mapping[str, str]) -> dict:
    """The [MEGNET] section as MEGnet's parameters."""
    params = {
        "probability_threshold": float(section.get("probability_threshold", 0.9)),
        "combine_components": _bool(section.get("combine_components", False)),
        "max_components_per_class": int(float(section.get("max_components_per_class", 1))),
        "megnet_ecg_class": int(float(section.get("megnet_ecg_class", 2))),
        "megnet_eog_primary_class": int(float(section.get("megnet_eog_primary_class", 1))),
        "megnet_eog_secondary_class": int(float(section.get("megnet_eog_secondary_class", 3))),
    }
    thr = params["probability_threshold"]
    if not 0.0 <= thr <= 1.0:
        # Kept as given, so an existing configuration gives the same result,
        # but said once: 9.0 was the shipped default until 2026-11 and made
        # combine_components use a single component without saying why.
        print(f"___MEGqc___: [MEGNET] probability_threshold = {thr} is outside "
              "0-1 (MEGnet probabilities are 0-1), so no component reaches it "
              "and only the most likely one is used. The default is 0.9.")
    return params


class MegnetProvider:
    """MEGnet as a source of ECG/EOG reference signals."""

    def applies_to(self, raw, params: dict) -> Optional[str]:
        return why_not(raw)

    def compute(self, raw, params: dict, *, data_path: str, work_dir: str) -> ReferenceResult:
        from meegqc_megnet.run import compute
        return compute(raw, params, data_path=data_path, work_dir=work_dir)


def plugin() -> Plugin:
    """The entry point MEEGqc calls (group ``meegqc.plugins``, name ``megnet``)."""
    return Plugin(
        name="megnet",
        label="MEGnet",
        version=__version__,
        summary=("Heartbeat and eye-movement components found by MEGnet, used as "
                 f"ECG/EOG references. MEG recordings from {SYSTEMS} systems, at "
                 "least 105 s long."),
        api_version=PLUGIN_API_VERSION,
        settings_section="MEGNET",
        default_settings=resources.files("meegqc_megnet").joinpath("settings.ini")
        .read_text(encoding="utf-8"),
        parse_settings=parse_settings,
        reference_provider=MegnetProvider(),
        homepage="https://github.com/ANCPLabOldenburg/MEEGqc/tree/main/plugins/meegqc-megnet",
    )
