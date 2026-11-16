"""ECG and EOG reference signals supplied by plugins (for example MEGnet).

The ECG and EOG metrics need a reference: a recorded ECG/EOG channel, or for
ECG one MNE reconstructs from the MEG sensors. A plugin can add another one
(see :mod:`meg_qc.plugins.api`). This module asks every installed reference
plugin whether it applies to the recording, runs the ones that do, and hands
the metrics one :class:`SyntheticReference` per plugin, whatever happened.
"""
from __future__ import annotations

import hashlib
import os
import traceback
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np

from meg_qc.plugins import registry
from meg_qc.plugins.api import ReferenceResult, ReferenceSignal, reference_lowpass, reference_use

KINDS = ("ECG", "EOG")


@dataclass
class SyntheticReference:
    """One plugin's reference signals for one recording."""

    source: str                  # the plugin's name: desc-ECGchannel<source>
    label: str                   # shown in logs and reports
    use: Dict[str, str]          # {"ECG": "independent", "EOG": "off"}
    lowpass_h_freq: Dict[str, float]
    status: str = "skipped"      # "ok", "skipped" or "failed"
    reason: str = ""
    signals: Dict[str, Optional[ReferenceSignal]] = field(default_factory=dict)

    def requested(self, kind: str) -> bool:
        return self.use.get(kind, "off") != "off"

    def wants(self, kind: str, how: str) -> bool:
        """``how`` is "fallback" or "independent"."""
        return self.use.get(kind, "off") in (how, "both")

    def signal(self, kind: str) -> Optional[ReferenceSignal]:
        return self.signals.get(kind) if self.status == "ok" else None

    def channel_name(self, kind: str) -> str:
        return f"{self.label}_{kind}"

    @property
    def method(self) -> str:
        return f"correlation_{self.source}"


def resample_to_length(signal, target_len: int) -> np.ndarray:
    """A 1-D signal stretched to ``target_len`` samples (linear interpolation)."""
    arr = np.asarray(signal, dtype=float).reshape(-1)
    if arr.size == 0 or target_len <= 0:
        return np.zeros(max(target_len, 0), dtype=float)
    if arr.size == target_len:
        return arr
    return np.interp(np.linspace(0.0, 1.0, target_len), np.linspace(0.0, 1.0, arr.size), arr)


def _fit(sig: Optional[ReferenceSignal], n_times: int) -> Optional[ReferenceSignal]:
    if sig is None or np.asarray(sig.signal).size == 0:
        return None
    return ReferenceSignal(resample_to_length(sig.signal, n_times),
                           list(sig.component_ids), list(sig.component_probs))


def recording_as_recorded(data_file, default_settings: dict, work_root: str):
    """The recording for plugins: ``(raw, path_of)``, where ``path_of()``
    returns a file holding exactly ``raw``.

    The metrics' own copy is not it: for CTF recordings MEEGqc re-types the
    axial gradiometers from "mag" to "grad" for its metrics, which leaves them
    with no coil type, and a plugin such as MEGnet (which reads magnetometer
    channels) then finds none. So plugins get the file as recorded, with only
    the channels.tsv corrections, read lazily, and cropped exactly like the
    metrics' copy so both share one time axis. Without cropping the path is the
    original file; with it, a FIF in the run's .tmp folder (removed with it),
    written only when a plugin asks for the path, i.e. after it has accepted
    the recording.
    """
    from meg_qc.calculation.initial_meg_qc import _apply_bids_channel_types, load_data

    raw, _, _, _ = load_data(str(data_file), preload=False)
    raw, _ = _apply_bids_channel_types(raw, str(data_file))
    end = float(raw.times[-1])
    tmin = float(default_settings.get("crop_tmin") or 0.0)
    tmax = default_settings.get("crop_tmax")
    tmax = end if tmax is None or float(tmax) > end else float(tmax)
    if tmin <= 0 and tmax >= end:
        return raw, lambda: str(data_file)
    raw.crop(tmin=tmin, tmax=tmax)
    written: List[str] = []

    def path_of() -> str:
        if not written:
            folder = os.path.join(os.path.abspath(work_root), ".tmp", "plugins")
            os.makedirs(folder, exist_ok=True)
            tag = hashlib.sha1(str(data_file).encode("utf-8")).hexdigest()[:12]
            path = os.path.join(folder, f"recording_{tag}_raw.fif")
            raw.save(path, overwrite=True, verbose="ERROR")
            written.append(path)
        return written[0]

    return raw, path_of


def compute_references(plugin_settings: Dict[str, dict], *, run_ecg: bool, run_eog: bool,
                       work_root: str, load_recording) -> List[SyntheticReference]:
    """Run every reference plugin the settings ask for on one recording.

    ``plugin_settings`` is ``all_qc_params["PLUGINS"]``; ``load_recording()``
    returns ``(raw, path_of)`` (see :func:`recording_as_recorded`) and is
    called at most once, only when a plugin is requested. Nothing that goes
    wrong here stops the recording: the plugin's reference is then "failed",
    with the reason, and the metrics run without it.
    """
    refs: List[SyntheticReference] = []
    recording = None          # (raw, path_of) once loaded
    load_error = ""
    for plugin in registry.reference_providers():
        cfg = plugin_settings.get(plugin.name)
        if cfg is None:
            continue
        section, params = cfg["section"], cfg["params"]
        use = {"ECG": reference_use(section, "ecg") if run_ecg else "off",
               "EOG": reference_use(section, "eog") if run_eog else "off"}
        if all(u == "off" for u in use.values()):
            print(f"___MEGqc___: {plugin.label} not requested by its settings "
                  f"[{plugin.settings_section}] - skipping.")
            continue
        ref = SyntheticReference(
            source=plugin.name, label=plugin.label, use=use,
            lowpass_h_freq={k: reference_lowpass(section, k) for k in KINDS})
        refs.append(ref)
        if recording is None and not load_error:
            try:
                recording = load_recording()
            except Exception as exc:
                load_error = f"the recording could not be read for plugins ({type(exc).__name__}: {exc})"
        if load_error:
            ref.status, ref.reason = "failed", load_error
            print(f"___MEGqc___: {plugin.label} failed - {load_error}")
            continue
        raw, path_of = recording
        provider = plugin.reference_provider
        try:
            why_not = provider.applies_to(raw, params)
        except Exception as exc:
            why_not = f"could not check the recording ({type(exc).__name__}: {exc})"
        if why_not:
            ref.reason = why_not
            print(f"___MEGqc___: {plugin.label} skipped - {why_not}")
            continue
        print(f"___MEGqc___: Computing {plugin.label} reference signals...")
        try:
            work_dir = os.path.join(os.path.abspath(work_root), ".tmp", f"{plugin.name}_cache")
            os.makedirs(work_dir, exist_ok=True)
            result = provider.compute(raw, params, data_path=str(path_of()), work_dir=work_dir)
        except Exception as exc:
            traceback.print_exc()
            result = ReferenceResult("failed", f"{plugin.label} failed: {type(exc).__name__}: {exc}")
        if not isinstance(result, ReferenceResult):
            result = ReferenceResult("failed", f"{plugin.label} returned {type(result).__name__}, "
                                               "not a ReferenceResult")
        ref.status, ref.reason = result.status, result.reason
        ref.signals = {"ECG": _fit(result.ecg, raw.n_times), "EOG": _fit(result.eog, raw.n_times)}
        found = [k for k in KINDS if ref.signals.get(k) is not None]
        missing = [k for k in KINDS if ref.requested(k) and k not in found]
        detail = (f"; found {' and '.join(found) or 'no'} reference"
                  + (f", no {' or '.join(missing)} component" if missing else "")
                  if result.status == "ok" else "")
        print(f"___MEGqc___: {plugin.label}: {result.status}{detail}"
              + (f" - {result.reason}" if result.reason else ""))
    return refs
