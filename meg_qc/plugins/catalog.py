"""Plugins MEEGqc knows how to install by name (``meegqc-plugins install megnet``,
or the GUI's Plugins dialog). Any other plugin installs from its package name,
a folder or a URL like every Python package.

A plugin can also be part of MEEGqc itself (``module``): its code ships with
MEEGqc and is loaded once its ``requirements`` are installed; installing it
installs only those, and removing it removes them. MEGnet is one: its code is
about 12 KB, the libraries it needs about 1 GB.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from importlib import metadata
from typing import Optional, Tuple


@dataclass(frozen=True)
class CatalogEntry:
    name: str        # the plugin's name once installed
    label: str
    package: str     # what pip installs (for a built-in plugin, its main requirement)
    summary: str
    homepage: str = ""
    module: str = ""                     # "package.module:factory" of a built-in plugin
    requirements: Tuple[str, ...] = ()   # what a built-in plugin needs installed

    @property
    def built_in(self) -> bool:
        return bool(self.module)

    def install_specs(self) -> Tuple[str, ...]:
        """What the installer is given to install this plugin."""
        return self.requirements if self.built_in else (self.package,)


CATALOG: Tuple[CatalogEntry, ...] = (
    CatalogEntry(
        name="megnet",
        label="MEGnet",
        package="megnet-neuro",
        summary=(
            "Finds heartbeat and eye-movement components with a neural network "
            "(MEGnet) and gives the ECG and EOG metrics a reference when a "
            "recording has no usable ECG or EOG channel. MEG recordings from "
            "MEGIN, CTF, 4D and KIT systems, at least 105 s long. Part of "
            "MEEGqc; installing it adds the libraries it needs, PyTorch and "
            "Keras (about 1 GB)."
        ),
        homepage="https://github.com/ANCPLabOldenburg/MEEGqc/tree/main/meg_qc/plugins/megnet",
        module="meg_qc.plugins.megnet:plugin",
        # Kept equal to the "megnet" extra in pyproject.toml (a test checks).
        # 0.3.6: older releases need TensorFlow and do not import with MNE 1.13.
        requirements=("megnet-neuro>=0.3.6",),
    ),
)

#: Packages that once held a plugin MEEGqc now includes, and which one.
SUPERSEDED = {"meegqc-megnet": "megnet"}


def entry(name: str) -> Optional[CatalogEntry]:
    return next((e for e in CATALOG if e.name == name), None)


def requirement_name(requirement: str) -> str:
    """The distribution a requirement names (``megnet-neuro>=0.3.6`` gives
    ``megnet-neuro``)."""
    return re.match(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)", requirement).group(1)


def requirements_installed(item: CatalogEntry) -> bool:
    """True when every requirement of a built-in plugin is installed."""
    for requirement in item.requirements:
        try:
            metadata.distribution(requirement_name(requirement))
        except metadata.PackageNotFoundError:
            return False
    return True
