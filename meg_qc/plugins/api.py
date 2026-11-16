"""The MEEGqc plugin API, version 1.

A plugin is a separate Python package that MEEGqc finds through the entry-point
group ``meegqc.plugins``. The entry point names a callable that takes no
arguments and returns a :class:`Plugin`. Everything a plugin can do is
described by that object, so MEEGqc never imports a plugin's internals.

What version 1 offers:

* **Settings**: a plugin may own one section of ``settings.ini``. Its default
  text (with ``#`` comments, which become the settings editor's tooltips) is
  merged into every place MEEGqc shows or writes settings, and the user's
  values for it reach the plugin at run time.
* **Reference signals** for the ECG and EOG metrics: a
  :class:`ReferenceProvider` computes a heartbeat and/or an eye-movement signal
  for a recording (MEGnet does this from ICA components). MEEGqc decides, from
  the section's standard keys, whether that signal replaces a recorded or
  reconstructed channel that failed its checks ("fallback"), is analysed beside
  it ("independent"), or both. Its results are written as their own
  derivatives, named after the plugin, and the reports show them.

See ``plugins/README.md`` in the MEEGqc repository for the guidelines and
``plugins/meegqc-megnet`` for a complete plugin.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, List, Mapping, Optional, Protocol, runtime_checkable

import numpy as np

#: The API this MEEGqc implements. A plugin built for another version is
#: listed but not loaded.
PLUGIN_API_VERSION = 1

#: The entry-point group plugins register under.
ENTRY_POINT_GROUP = "meegqc.plugins"

#: A plugin's name: lowercase letters and digits, starting with a letter. It is
#: also the source name in derivative files (``desc-ECGchannel<name>``), so it
#: must stay a single BIDS-safe token.
NAME_PATTERN = re.compile(r"^[a-z][a-z0-9]*$")

#: Names a plugin cannot take: the sources of the calculation's own references.
RESERVED_NAMES = frozenset({"recorded", "reconstructed", "channel", "s"})

#: How the ECG or EOG metric uses a plugin's reference signal.
USES = ("off", "fallback", "independent", "both")


@dataclass
class ReferenceSignal:
    """One synthetic reference: a time course with one value per sample of the
    recording (MEEGqc's sampling rate), plus what it was made from."""

    signal: np.ndarray
    component_ids: List[int] = field(default_factory=list)
    component_probs: List[float] = field(default_factory=list)


@dataclass
class ReferenceResult:
    """What a provider produced for one recording.

    ``status`` is ``"ok"`` (``ecg``/``eog`` hold the signals it found; either
    may be ``None``), ``"skipped"`` (the provider does not apply; ``reason``
    says why, in a sentence a user can act on) or ``"failed"``.
    """

    status: str
    reason: str = ""
    ecg: Optional[ReferenceSignal] = None
    eog: Optional[ReferenceSignal] = None


@runtime_checkable
class ReferenceProvider(Protocol):
    """Computes ECG/EOG reference signals for a recording."""

    def applies_to(self, raw, params: dict) -> Optional[str]:
        """``None`` when the provider can run on this recording, otherwise the
        reason it cannot ("needs at least 105 s; this recording is 60 s").
        Called before :meth:`compute`, and cheap."""

    def compute(self, raw, params: dict, *, data_path: str,
                work_dir: str) -> ReferenceResult:
        """Compute the references. ``raw`` is the loaded recording (do not
        modify it), ``work_dir`` a folder for intermediate files that MEEGqc
        does not keep. Exceptions are caught and reported as ``failed``."""


@dataclass(frozen=True)
class Plugin:
    """Everything MEEGqc needs to know about a plugin."""

    #: Short name, see :data:`NAME_PATTERN` ("megnet").
    name: str
    #: Name shown to people ("MEGnet").
    label: str
    #: The plugin's own version.
    version: str
    #: One or two sentences: what it adds and where it applies.
    summary: str
    #: :data:`PLUGIN_API_VERSION` the plugin was written for.
    api_version: int = PLUGIN_API_VERSION
    #: The ``settings.ini`` section the plugin owns ("MEGNET"), if any.
    settings_section: Optional[str] = None
    #: That section's default text, starting with its ``[SECTION]`` line.
    default_settings: str = ""
    #: Turns the section's values (defaults overlaid with the user's, as
    #: strings) into the ``params`` dict the provider receives.
    parse_settings: Optional[Callable[[Mapping[str, str]], dict]] = None
    #: Present when the plugin supplies ECG/EOG reference signals.
    reference_provider: Optional[ReferenceProvider] = None
    #: Where to read more.
    homepage: str = ""


def reference_use(section: Mapping[str, str], kind: str) -> str:
    """The standard ``ecg``/``eog`` key of a reference provider's section:
    one of :data:`USES` (an unknown value counts as ``off``)."""
    value = str(section.get(kind.lower(), "off")).strip().lower()
    return value if value in USES else "off"


def reference_lowpass(section: Mapping[str, str], kind: str) -> float:
    """The standard ``ecg_lowpass_h_freq``/``eog_lowpass_h_freq`` key, in Hz;
    0 (or an unreadable value) means no low-pass filter."""
    try:
        value = float(section.get(f"{kind.lower()}_lowpass_h_freq", 0) or 0)
    except (TypeError, ValueError):
        return 0.0
    return max(value, 0.0)
