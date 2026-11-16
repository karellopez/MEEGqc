"""Plugins MEEGqc knows how to install by name (``meegqc-plugins install megnet``,
or the GUI's Plugins dialog). Any other plugin installs from its package name,
a folder or a URL like every Python package."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass(frozen=True)
class CatalogEntry:
    name: str        # the plugin's name once installed
    label: str
    package: str     # what pip installs
    summary: str
    homepage: str = ""


CATALOG: Tuple[CatalogEntry, ...] = (
    CatalogEntry(
        name="megnet",
        label="MEGnet",
        package="meegqc-megnet",
        summary=(
            "Finds heartbeat and eye-movement components with a neural network "
            "(MEGnet) and gives the ECG and EOG metrics a reference when a "
            "recording has no usable ECG or EOG channel. MEG recordings from "
            "MEGIN, CTF, 4D and KIT systems, at least 105 s long. Installs "
            "PyTorch and Keras (about 1 GB)."
        ),
        homepage="https://github.com/ANCPLabOldenburg/MEEGqc/tree/main/plugins/meegqc-megnet",
    ),
)


def entry(name: str) -> Optional[CatalogEntry]:
    return next((e for e in CATALOG if e.name == name), None)
