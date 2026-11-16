"""Where an ECG/EOG reference came from, as the reports show it.

The calculation writes the reference it analysed as ``desc-ECGchannel`` /
``desc-EOGchannel`` (with ``desc-ECGs`` / ``desc-EOGs`` per channel). A
plugin's reference analysed beside it (e.g. MEGnet's) is written under the
plugin's name: ``desc-ECGchannel<source>`` and ``desc-ECGs<source>``. Each
reference table records its source in ``signal_source`` ("recorded",
"mne_reconstructed" or the plugin's name) and, for a plugin, how the plugin
calls itself in ``signal_source_label``, so a report can name it whether or
not the plugin is installed where the report is made.
"""
from __future__ import annotations

import csv
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Dict, Iterable, Optional, Set

#: Sources the calculation itself provides.
CORE_SOURCES = ("recorded", "mne_reconstructed")

_DESC = re.compile(r"desc-(ECG|EOG)(channel|s)([a-z][a-z0-9]*)?(?=[_.])", re.IGNORECASE)


def plugin_source_of_name(name) -> Optional[str]:
    """The plugin whose reference a derivative file holds ("megnet" for
    ``..._desc-ECGsmegnet_meg.tsv``), or ``None`` for the core files."""
    match = _DESC.search(os.path.basename(str(name or "")))
    return match.group(3).lower() if match and match.group(3) else None


#: What the calculation has written for its own references, in this and older
#: versions (outputs before 2026-07 recorded the method name), by source.
_CORE_VALUES = {
    "recorded": "recorded", "correlation_recorded": "recorded",
    "mean_threshold": "recorded", "no_ecg": "recorded",
    "mne_reconstructed": "mne_reconstructed", "reconstructed": "mne_reconstructed",
    "correlation_reconstructed": "mne_reconstructed", "reconstructed-bad": "mne_reconstructed",
}
_NOT_A_SOURCE = ("", "n/a", "nan", "none")


def plugin_source_of_value(source) -> Optional[str]:
    """``"megnet"`` for ``"megnet"`` or ``"correlation_megnet"``, ``None`` for
    the calculation's own sources (any spelling) and for n/a."""
    value = str(source or "").strip().lower()
    if value in _NOT_A_SOURCE or value in _CORE_VALUES:
        return None
    return value[len("correlation_"):] if value.startswith("correlation_") else value


def is_plugin_source(source) -> bool:
    """True for a ``signal_source`` value that names a plugin."""
    return plugin_source_of_value(source) is not None


@lru_cache(maxsize=512)
def _label_in_file(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as f:
            row = next(csv.DictReader(f, delimiter="\t"), None) or {}
    except (OSError, UnicodeDecodeError, csv.Error):
        return ""
    return str(row.get("signal_source_label") or "").strip()


def _label_beside(path: str, source: str) -> str:
    """The name recorded by the plugin's reference table next to ``path``
    (a per-channel table, desc-ECGs<source>, has no name column)."""
    folder = os.path.dirname(path)
    stem = os.path.basename(path).split("_desc-")[0]
    for kind in ("ECG", "EOG"):
        for name in (f"{stem}_desc-{kind}channel{source}_meg.tsv",
                     f"{stem}_desc-{kind}channel{source}_eeg.tsv"):
            label = _label_in_file(os.path.join(folder, name))
            if label:
                return label
    return ""


def source_label(source: str, sample_file=None) -> str:
    """How to name a plugin source in a report: the name its reference table
    recorded (or the one beside it), else the installed plugin's label, else
    the source itself."""
    if sample_file:
        label = _label_in_file(str(sample_file)) or _label_beside(str(sample_file), source)
        if label:
            return label
    if source in _KNOWN_LABELS:
        return _KNOWN_LABELS[source]
    try:
        from meg_qc.plugins.registry import find
        plugin = find(source)
        if plugin is not None:
            return plugin.label
    except Exception:
        pass
    return source


def plugin_sources_in(folder, kinds: Iterable[str] = ("ECG", "EOG")) -> Set[str]:
    """Every plugin source with reference files under ``folder``."""
    root = Path(folder)
    if not root.is_dir():
        return set()
    wanted = {k.upper() for k in kinds}
    found = set()
    for path in root.rglob("*desc-*"):
        match = _DESC.search(path.name)
        if match and match.group(3) and match.group(1).upper() in wanted:
            found.add(match.group(3).lower())
    return found


# ── what the dataset-level reports show ──────────────────────────────────
_KNOWN_LABELS = {}

#: Marker symbols: recorded channels and MNE reconstructions keep theirs, each
#: plugin source gets the next of these.
_CORE_SYMBOLS = {"recorded": "circle", "mne_reconstructed": "diamond"}
_PLUGIN_SYMBOLS = ("star", "triangle-up", "square", "pentagon", "hexagon")


def remember_label(source, label) -> None:
    """Note a plugin source's display name, read from one of its tables."""
    if source and label:
        _KNOWN_LABELS[str(source)] = str(label)


def label_for(source: str) -> str:
    if source in _KNOWN_LABELS:
        return _KNOWN_LABELS[source]
    return source_label(source)


def source_key(value) -> str:
    """"recorded", "mne_reconstructed", a plugin's name, or "" for n/a."""
    v = str(value or "").strip().lower()
    if v in _NOT_A_SOURCE:
        return ""
    return _CORE_VALUES.get(v) or plugin_source_of_value(v) or v


def describe_source(value, *, short: bool = False) -> str:
    """How a report names a reference source."""
    key = source_key(value)
    if key == "recorded":
        return "recorded channel"
    if key == "mne_reconstructed":
        return "MNE reconstruction"
    if not key:
        return "n/a"
    return f"{label_for(key)} synthetic" + ("" if short else " component")


def symbols_for(values) -> dict:
    """``{source key: marker symbol}`` for the sources among ``values``."""
    keys = {source_key(v) for v in values} - {""}
    plugins = sorted(k for k in keys if k not in CORE_SOURCES)
    out = {k: s for k, s in _CORE_SYMBOLS.items() if k in keys}
    for i, k in enumerate(plugins):
        out[k] = _PLUGIN_SYMBOLS[i % len(_PLUGIN_SYMBOLS)]
    return out


def plugin_sources_used(df, columns) -> list:
    keys = set()
    for col in columns:
        if col in getattr(df, "columns", ()):
            keys |= {source_key(v) for v in df[col].astype(str)}
    return sorted(k for k in keys - {""} if k not in CORE_SOURCES)


def parse_plugin_references(value) -> Dict[str, str]:
    """``"megnet:MEGnet,x:X"`` (a run row's plugin_references) as {source: label}."""
    out: Dict[str, str] = {}
    for part in str(value or "").split(","):
        source, _, label = part.partition(":")
        if source.strip():
            out[source.strip()] = label.strip()
    return out


def format_plugin_references(pairs: Dict[str, str]) -> str:
    return ",".join(f"{s}:{pairs[s]}" for s in sorted(pairs))


def plugin_references_in(descs, label_of) -> Dict[str, str]:
    """The plugins with their own reference among a recording's desc values
    (``ECGchannelmegnet`` -> megnet), with ``label_of(desc)`` as their names."""
    out: Dict[str, str] = {}
    for desc in descs:
        source = plugin_source_of_name(f"desc-{desc}_x.tsv")
        if source and str(desc).lower().startswith(("ecgchannel", "eogchannel")):
            out[source] = out.get(source) or label_of(desc) or ""
    return out


def contribution_html(base, columns, *, refs_col: str = "plugin_references",
                      scope: str = "here", group_col: str = "", n_groups: int = 0) -> str:
    """One paragraph per reference plugin: for how many recordings its
    reference was analysed (beside the recorded one, or instead of it) and
    in how many it replaced the recorded or reconstructed reference. Covers
    plugins installed now and plugins found in the data, so a plugin that
    produced nothing is not silent."""
    n_total = int(base.shape[0]) if base is not None else 0
    if not n_total:
        return ""
    sources = set(plugin_sources_used(base, columns))
    has_refs = refs_col in base.columns
    refs = base[refs_col].map(parse_plugin_references) if has_refs else None
    if has_refs:
        for pairs in refs:
            for source, label in pairs.items():
                remember_label(source, label)
                sources.add(source)
    try:
        from meg_qc.plugins.registry import reference_providers
        sources |= {p.name for p in reference_providers()}
    except Exception:
        pass
    out = []
    for src in sorted(sources):
        label = label_for(src)
        replaced = None
        for col in columns:
            if col in base.columns:
                m = base[col].astype(str).map(source_key) == src
                replaced = m if replaced is None else (replaced | m)
        n_replaced = int(replaced.sum()) if replaced is not None else 0
        analysed = (refs.map(lambda pairs: src in pairs) | replaced) if (has_refs and replaced is not None) \
            else (refs.map(lambda pairs: src in pairs) if has_refs else None)
        counted = analysed if analysed is not None else replaced
        across = ""
        if group_col and n_groups and counted is not None and group_col in base.columns:
            n_with = int(base.loc[counted, group_col].astype(str).nunique())
            across = f", across <strong>{n_with}</strong> of {n_groups} datasets"
        if analysed is None:
            # Outputs without the record of independent references: only the
            # replacements are known, so claim nothing about the rest.
            pct = 100.0 * n_replaced / n_total
            out.append(f"<p><strong>{label} contribution:</strong> its reference replaced the "
                       f"recorded or reconstructed one in <strong>{n_replaced}</strong> of "
                       f"{n_total} recordings ({pct:.0f}%){across}.</p>")
            continue
        n_used = int(analysed.sum())
        if n_used == 0:
            out.append(f"<p><strong>{label} contribution:</strong> {label} produced no reference "
                       f"{scope}: it was off in the settings, did not apply to these recordings "
                       "(each recording's log says why) or failed on them.</p>")
            continue
        pct = 100.0 * n_used / n_total
        out.append(f"<p><strong>{label} contribution:</strong> its reference was analysed for "
                   f"<strong>{n_used}</strong> of {n_total} recordings ({pct:.0f}%){across}; it "
                   f"replaced the recorded or reconstructed reference in <strong>{n_replaced}"
                   "</strong> of them.</p>")
    return "".join(out)
