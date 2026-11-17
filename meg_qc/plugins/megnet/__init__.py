"""MEGnet for MEEGqc: ECG and EOG references from ICA components.

MEGnet (Treacher et al., NeuroImage 2021; package ``megnet-neuro``) labels the
ICA components of a MEG recording as heartbeat, blink, eye movement or other.
This plugin gives MEEGqc's ECG and EOG metrics those components as reference
signals, for recordings without a usable ECG or EOG channel or to compare with
one.

It is part of MEEGqc, and the only plugin that is: its code ships with every
MEEGqc and is loaded only once its dependencies are installed (``megnet-neuro``,
which brings PyTorch and Keras), from MEEGqc's Plugins window, with
``meegqc-plugins install megnet`` or with ``pip install meg_qc[megnet]``.
Nothing imports MEGnet, Keras or PyTorch before a recording is analysed with it
(``run.py`` does, inside ``compute``). The catalog entry
(``meg_qc.plugins.catalog``) says where the plugin is and what it needs.
"""
from __future__ import annotations

from importlib import resources
from typing import Mapping, Optional

from meg_qc import __version__
from meg_qc.plugins.api import PLUGIN_API_VERSION, Plugin, ReferenceResult
from meg_qc.plugins.megnet.applies import SYSTEMS, why_not


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
        from meg_qc.plugins.megnet.run import compute
        return compute(raw, params, data_path=data_path, work_dir=work_dir)


def plugin() -> Plugin:
    """What the registry loads once ``megnet-neuro`` is installed."""
    return Plugin(
        name="megnet",
        label="MEGnet",
        version=__version__,
        summary=("Heartbeat and eye-movement components found by MEGnet, used as "
                 f"ECG/EOG references. MEG recordings from {SYSTEMS} systems, at "
                 "least 105 s long."),
        api_version=PLUGIN_API_VERSION,
        settings_section="MEGNET",
        default_settings=resources.files("meg_qc.plugins.megnet").joinpath("settings.ini")
        .read_text(encoding="utf-8"),
        parse_settings=parse_settings,
        reference_provider=MegnetProvider(),
        homepage="https://github.com/ANCPLabOldenburg/MEEGqc/tree/main/meg_qc/plugins/megnet",
    )
