"""Running MEGnet on one recording: ICA, classification, reference signals.

Moved from MEEGqc's ECG/EOG module (and its vendored easy_megnet wrapper) when
MEGnet became a plugin; the behaviour is the same except that MEGnet's notch
filter now uses the recording's own line frequency (it was always 50 Hz).
"""
from __future__ import annotations

import hashlib
import inspect
import os
from typing import Optional, Tuple

import numpy as np

from meg_qc.plugins.api import ReferenceResult, ReferenceSignal

_NOTCH_PATCHED = False
_CPU_SET = False


def pick_components(classes: np.ndarray, probs: np.ndarray, class_id: int,
                    params: dict) -> Tuple[np.ndarray, np.ndarray]:
    """Component indices for one class, by probability, as the settings say."""
    idx = np.where(classes == class_id)[0]
    if idx.size == 0:
        return np.array([], dtype=int), np.array([], dtype=float)

    cls_probs = probs[idx, class_id]
    order = np.argsort(cls_probs)[::-1]
    idx = idx[order]
    cls_probs = cls_probs[order]

    keep = cls_probs >= float(params.get("probability_threshold", 0.9))
    if not np.any(keep):
        keep = np.zeros_like(cls_probs, dtype=bool)
        keep[0] = True
    idx = idx[keep]
    cls_probs = cls_probs[keep]

    n = max(1, int(params.get("max_components_per_class", 1))) \
        if params.get("combine_components", False) else 1
    return idx[:n], cls_probs[:n]


def compose_signal(ica_ts: np.ndarray, comp_idx: np.ndarray, comp_probs: np.ndarray) -> np.ndarray:
    """One reference from one or more ICA components (probability-weighted)."""
    if comp_idx.size == 0:
        return np.array([])
    selected = ica_ts[comp_idx]
    if selected.ndim == 1:
        return selected
    if selected.shape[0] == 1:
        return selected[0]
    w = np.asarray(comp_probs, dtype=float)
    if w.size != selected.shape[0] or np.sum(w) == 0:
        w = np.ones(selected.shape[0], dtype=float)
    w = w / np.sum(w)
    return np.sum(selected * w[:, None], axis=0)


def _patch_notch_filter() -> None:
    """Work around an upstream MEGnet bug for 50 Hz mains data.

    ``MEGnet.prep_inputs.ICA.raw_preprocess`` builds its notch frequencies as
    ``np.arange(mains_freq, 250 * 2/3, mains_freq)`` and applies them *before*
    resampling. For 50 Hz mains that yields 50/100/150 Hz, and 150 Hz is above
    the Nyquist frequency of any recording sampled at 300 Hz or less, so MNE
    raises. The patch drops notch frequencies at or above the Nyquist
    frequency instead of failing; everything else is unchanged.
    """
    global _NOTCH_PATCHED
    if _NOTCH_PATCHED:
        return
    from MEGnet.prep_inputs import ICA as megnet_ica
    original = getattr(megnet_ica, "raw_preprocess", None)
    if original is None or getattr(original, "_megqc_patched", False):
        _NOTCH_PATCHED = True
        return

    def raw_preprocess(raw, mains_freq=None):
        resample_freq = 250
        mains = int(mains_freq) if mains_freq else 50
        notch = np.arange(mains, resample_freq * 2 / 3, mains)
        nyquist = float(raw.info["sfreq"]) / 2.0
        safe = notch[notch < nyquist]
        if safe.size < notch.size:
            print(f"___MEGqc___: MEGnet notch {list(notch)} Hz exceeds Nyquist "
                  f"{nyquist:.1f} Hz at {raw.info['sfreq']:.0f} Hz; using {list(safe)} Hz.")
        if safe.size:
            raw.notch_filter(safe)
        raw.resample(resample_freq)
        raw.filter(1.0, 100)
        return raw

    raw_preprocess._megqc_patched = True
    megnet_ica.raw_preprocess = raw_preprocess
    _NOTCH_PATCHED = True


def _prefer_cpu() -> None:
    """Run MEGnet on the CPU unless ``MEGQC_MEGNET_GPU=1``.

    MEEGqc often runs many recordings in parallel, and each worker that touches
    the GPU reserves memory for a model of about 29 MB: a 28-way run gave 44
    "CUDA error: out of memory" failures. CPU inference of this model is cheap.
    """
    global _CPU_SET
    if _CPU_SET:
        return
    if os.environ.get("MEGQC_MEGNET_GPU", "").strip() in ("", "0", "false", "False"):
        os.environ.setdefault("KERAS_BACKEND", "torch")
        os.environ["CUDA_VISIBLE_DEVICES"] = ""
    _CPU_SET = True


def _model_path() -> str:
    import MEGnet
    return os.path.join(MEGnet.__path__[0], "model_v2k3", "model_v2.keras")


def _ensure_weights() -> Optional[str]:
    """``pip install megnet-neuro`` ships no model weights: they come from
    HuggingFace through ``megnet_init``. Fetch them before the ICA, which fails
    first without them. Returns the reason when they are still missing."""
    if os.path.exists(_model_path()):
        return None
    from MEGnet.megnet_init import main as megnet_init
    print("___MEGqc___: MEGnet model weights missing - downloading from HuggingFace...")
    os.environ.setdefault("KERAS_BACKEND", "torch")
    megnet_init()
    if not os.path.exists(_model_path()):
        return "MEGnet model weights unavailable after megnet_init; run `megnet_init` to download them"
    return None


def _load_model():
    os.environ["KERAS_BACKEND"] = "torch"
    import keras

    original = keras.src.ops.operation.Operation.from_config

    # The saved model carries keyword arguments that newer Keras versions
    # reject; keep only the ones each layer's constructor accepts.
    @classmethod
    def lenient_from_config(cls, config):
        if isinstance(config, dict):
            try:
                valid = set(inspect.signature(cls.__init__).parameters) - {"self"}
                cleaned = {k: v for k, v in config.items()
                           if k in valid or k in ("name", "dtype", "trainable")}
                return original.__func__(cls, cleaned)
            except Exception:
                pass
        return original.__func__(cls, config)

    keras.src.ops.operation.Operation.from_config = lenient_from_config
    try:
        return keras.models.load_model(_model_path())
    finally:
        keras.src.ops.operation.Operation.from_config = original


def _classify(run_dir: str) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Class and per-class probabilities of each ICA component, and the
    components' time courses."""
    from scipy.io import loadmat
    from MEGnet.megnet_utilities import fPredictChunkAndVoting_parrallel

    # Components are numbered contiguously; a short recording may have fewer.
    names = []
    for i in range(1, 51):
        path = os.path.join(run_dir, f"component{i}.mat")
        if not os.path.isfile(path):
            break
        names.append(path)
    if not names:
        raise FileNotFoundError(f"MEGnet wrote no component*.mat files in {run_dir}")

    ica_ts = loadmat(os.path.join(run_dir, "ICATimeSeries.mat"))["arrICATimeSeries"].T
    maps = np.stack([loadmat(path)["array"] for path in names])
    probs, _ = fPredictChunkAndVoting_parrallel(_load_model(), ica_ts, maps)
    probs = np.asarray(probs, dtype=float)
    if probs.ndim != 2:
        probs = np.reshape(probs, (probs.shape[0], -1))
    sums = probs.sum(axis=1, keepdims=True)
    sums[sums == 0] = 1.0
    probs = probs / sums
    return probs.argmax(axis=1).astype(int), probs, ica_ts


def compute(raw, params: dict, *, data_path: str, work_dir: str) -> ReferenceResult:
    """MEGnet's ECG and EOG references for one recording."""
    try:
        from MEGnet.prep_inputs.ICA import main as run_ica_pipeline
    except Exception as exc:
        return ReferenceResult("failed", f"MEGnet could not be imported (package megnet-neuro): {exc}")

    missing = _ensure_weights()
    if missing:
        return ReferenceResult("failed", missing)
    _patch_notch_filter()
    _prefer_cpu()

    line_freq = raw.info.get("line_freq") or 50.0
    outbasename = f"megqc_{hashlib.sha1(str(data_path).encode('utf-8')).hexdigest()[:12]}"
    run_ica_pipeline(
        str(data_path),
        outbasename=outbasename,
        mains_freq=float(line_freq),
        save_preproc=False,
        save_ica=True,
        seedval=0,
        results_dir=work_dir,
        filename_raw=None,
        do_assess_bads=False,
        bad_channels=[],
    )
    classes, probs, ica_ts = _classify(os.path.join(work_dir, outbasename))

    n = min(ica_ts.shape[0], classes.shape[0], probs.shape[0])
    classes, probs, ica_ts = classes[:n], probs[:n, :], ica_ts[:n, :]

    ecg_idx, ecg_p = pick_components(classes, probs, int(params.get("megnet_ecg_class", 2)), params)
    eog_idx, eog_p = pick_components(classes, probs, int(params.get("megnet_eog_primary_class", 1)), params)
    if eog_idx.size == 0:
        eog_idx, eog_p = pick_components(
            classes, probs, int(params.get("megnet_eog_secondary_class", 3)), params)

    def reference(idx, p) -> Optional[ReferenceSignal]:
        sig = compose_signal(ica_ts, idx, p)
        if sig.size == 0:
            return None
        return ReferenceSignal(sig, idx.tolist(), [float(x) for x in p.tolist()])

    return ReferenceResult("ok", ecg=reference(ecg_idx, ecg_p), eog=reference(eog_idx, eog_p))
