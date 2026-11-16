"""Where the GUI keeps its preferences.

Normally ``QSettings("ANCP", <application>)``: a plist on macOS, the registry on
Windows, an INI file under ``~/.config`` on Linux.

``MEEGQC_SETTINGS_DIR`` redirects every store to INI files in that folder, so
test runs and scratch scripts never read or write the user's own preferences.
The test suite sets it for the whole session (``tests/conftest.py``).
"""
from __future__ import annotations

import os

from PyQt6.QtCore import QSettings

ORGANISATION = "ANCP"
SETTINGS_DIR_ENV = "MEEGQC_SETTINGS_DIR"


def settings_store(application: str) -> QSettings:
    """The preferences store of ``application`` (e.g. "MEEGqc", "MEEGqc_Viewer")."""
    folder = os.environ.get(SETTINGS_DIR_ENV, "").strip()
    if folder:
        os.makedirs(folder, exist_ok=True)
        return QSettings(os.path.join(folder, f"{ORGANISATION}_{application}.ini"),
                         QSettings.Format.IniFormat)
    return QSettings(ORGANISATION, application)
