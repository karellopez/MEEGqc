"""Finding the installed plugins.

A plugin that cannot be loaded (an import error, a wrong API version, a bad
name) is listed with the reason and otherwise ignored: a broken plugin never
stops MEEGqc. Set ``MEEGQC_PLUGINS=0`` to load none at all, for example to
check whether a problem comes from a plugin.
"""
from __future__ import annotations

import configparser
import os
from dataclasses import dataclass
from importlib import metadata
from typing import Dict, List, Mapping, Optional

from meg_qc.plugins.api import (
    ENTRY_POINT_GROUP,
    NAME_PATTERN,
    PLUGIN_API_VERSION,
    RESERVED_NAMES,
    Plugin,
)


@dataclass(frozen=True)
class FoundPlugin:
    """An installed plugin, loaded or not."""

    entry_point: str            # the entry point's name
    package: str                # the distribution that installed it
    package_version: str
    plugin: Optional[Plugin]    # None when it could not be loaded
    error: str = ""             # why it could not be loaded

    @property
    def name(self) -> str:
        return self.plugin.name if self.plugin else self.entry_point


_FOUND: Optional[List[FoundPlugin]] = None


def _disabled() -> bool:
    return os.environ.get("MEEGQC_PLUGINS", "").strip().lower() in ("0", "false", "off", "no")


def _load(ep) -> FoundPlugin:
    dist = getattr(ep, "dist", None)
    package = dist.metadata["Name"] if dist is not None else "?"
    package_version = dist.version if dist is not None else "?"

    def found(plugin=None, error=""):
        return FoundPlugin(ep.name, package, package_version, plugin, error)

    try:
        factory = ep.load()
        plugin = factory()
    except Exception as exc:  # a plugin's own bug must not stop MEEGqc
        return found(error=f"could not be loaded: {type(exc).__name__}: {exc}")
    if not isinstance(plugin, Plugin):
        return found(error="its entry point did not return a meg_qc.plugins.Plugin")
    if plugin.api_version != PLUGIN_API_VERSION:
        return found(error=(f"written for plugin API {plugin.api_version}; this "
                            f"MEEGqc provides API {PLUGIN_API_VERSION}"))
    if not NAME_PATTERN.match(plugin.name):
        return found(error=f"invalid name {plugin.name!r} (lowercase letters and digits)")
    if plugin.name in RESERVED_NAMES:
        return found(error=f"the name {plugin.name!r} is reserved for MEEGqc's own references")
    if plugin.settings_section and f"[{plugin.settings_section}]" not in plugin.default_settings:
        return found(error=(f"its default settings do not start a "
                            f"[{plugin.settings_section}] section"))
    return found(plugin)


def discover(refresh: bool = False) -> List[FoundPlugin]:
    """Every installed plugin, sorted by name. Cached; ``refresh`` re-reads."""
    global _FOUND
    if _FOUND is not None and not refresh:
        return _FOUND
    if _disabled():
        _FOUND = []
        return _FOUND
    found = [_load(ep) for ep in metadata.entry_points(group=ENTRY_POINT_GROUP)]
    names = set()
    unique = []
    for item in sorted(found, key=lambda f: f.name):
        if item.plugin and item.plugin.name in names:
            item = FoundPlugin(item.entry_point, item.package, item.package_version,
                               None, f"another plugin is already named {item.plugin.name!r}")
        elif item.plugin:
            names.add(item.plugin.name)
        unique.append(item)
    _FOUND = unique
    return _FOUND


def plugins() -> List[Plugin]:
    """The plugins that loaded."""
    return [f.plugin for f in discover() if f.plugin is not None]


def find(name: str) -> Optional[Plugin]:
    return next((p for p in plugins() if p.name == name), None)


def reference_providers() -> List[Plugin]:
    """Plugins that supply ECG/EOG reference signals."""
    return [p for p in plugins() if p.reference_provider is not None]


# ── settings ──────────────────────────────────────────────────────────────
def _section_defaults(plugin: Plugin) -> Dict[str, str]:
    cfg = configparser.ConfigParser()
    cfg.optionxform = str
    cfg.read_string(plugin.default_settings)
    return dict(cfg[plugin.settings_section]) if plugin.settings_section in cfg else {}


def section_values(plugin: Plugin, config: Optional[configparser.ConfigParser]) -> Dict[str, str]:
    """The plugin's section as strings: its defaults overlaid with the user's
    values (a config written before the plugin was installed has none)."""
    values = _section_defaults(plugin)
    if config is not None and plugin.settings_section in config:
        values.update(dict(config[plugin.settings_section]))
    return values


def plugin_params(plugin: Plugin, values: Mapping[str, str]) -> dict:
    """What the plugin's provider receives: its own parsing of the section."""
    if plugin.parse_settings is None:
        return dict(values)
    return plugin.parse_settings(values)


def _add_missing_keys(ini_text: str, section: str, entries) -> str:
    """Append ``key = value`` lines at the end of ``[section]`` in the text,
    keeping everything else (comments included) as it is."""
    lines = ini_text.split("\n")
    start = next(i for i, line in enumerate(lines) if line.strip() == f"[{section}]")
    end = next((i for i in range(start + 1, len(lines))
                if lines[i].strip().startswith("[") and lines[i].strip().endswith("]")), len(lines))
    while end > start + 1 and not lines[end - 1].strip():
        end -= 1
    added = ["# Not in the settings file; the run used these defaults:"]
    added += [f"{key} = {value}" for key, value in entries]
    return "\n".join(lines[:end] + added + lines[end:])


def with_plugin_sections(ini_text: str) -> str:
    """``ini_text`` with every loaded plugin's section complete.

    A section the text lacks is appended whole; one it has but that misses
    keys (written by an older version of the plugin) gets those keys with
    their defaults. Used wherever MEEGqc shows or writes settings
    (``get-meegqc-config``, the settings editor, the copy saved with each
    run's results), so the copy shows the values a run used.
    """
    cfg = configparser.ConfigParser()
    try:
        cfg.read_string(ini_text)
    except configparser.Error:
        return ini_text
    missing = []
    for p in plugins():
        if not p.settings_section or not p.default_settings.strip():
            continue
        if p.settings_section not in cfg:
            missing.append(p.default_settings.strip())
            continue
        have = set(cfg[p.settings_section])   # lowercased, like configparser
        absent = [(k, v) for k, v in _section_defaults(p).items() if k.lower() not in have]
        if absent:
            ini_text = _add_missing_keys(ini_text, p.settings_section, absent)
    if not missing:
        return ini_text
    return ini_text.rstrip("\n") + "\n\n" + "\n\n".join(missing) + "\n"


def settings_for_run(config: Optional[configparser.ConfigParser]) -> Dict[str, dict]:
    """``{plugin name: {"section": values, "params": parsed}}`` for each loaded
    plugin, read from a run's configuration. A plugin whose settings cannot be
    read is left out, with the reason printed: it then does not run."""
    out: Dict[str, dict] = {}
    for plugin in plugins():
        values = section_values(plugin, config) if plugin.settings_section else {}
        try:
            params = plugin_params(plugin, values)
        except Exception as exc:
            print(f"___MEGqc___: {plugin.label}: its settings "
                  f"[{plugin.settings_section}] could not be read ({exc}); it will not run.")
            continue
        out[plugin.name] = {"section": values, "params": params}
    return out
