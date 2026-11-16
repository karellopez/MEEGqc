"""MEEGqc plugins: optional packages that add to MEEGqc without being part of it.

Plugin authors need only :mod:`meg_qc.plugins.api` (re-exported here). The
rest of this package finds, installs and removes plugins.
"""
from meg_qc.plugins.api import (
    ENTRY_POINT_GROUP,
    PLUGIN_API_VERSION,
    Plugin,
    ReferenceProvider,
    ReferenceResult,
    ReferenceSignal,
)

__all__ = [
    "ENTRY_POINT_GROUP",
    "PLUGIN_API_VERSION",
    "Plugin",
    "ReferenceProvider",
    "ReferenceResult",
    "ReferenceSignal",
]
