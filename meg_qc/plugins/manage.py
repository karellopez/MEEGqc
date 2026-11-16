"""Installing and removing plugins in the environment MEEGqc runs from.

pip is used when this environment has it; environments made by uv often do
not, so uv (when found) installs into this Python instead. Nothing needs
administrator rights when MEEGqc itself was installed without them.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from importlib import metadata
from pathlib import Path
from typing import Callable, List, Optional

from meg_qc.plugins.catalog import entry


class InstallError(RuntimeError):
    pass


def _has_pip() -> bool:
    try:
        return subprocess.run([sys.executable, "-m", "pip", "--version"],
                              capture_output=True, timeout=60).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _uv() -> Optional[str]:
    found = shutil.which("uv")
    if found:
        return found
    for cand in (Path.home() / ".local/bin/uv", Path.home() / ".local/bin/uv.exe",
                 Path.home() / ".cargo/bin/uv"):
        if cand.is_file():
            return str(cand)
    return None


def resolve_spec(name_or_spec: str) -> str:
    """A catalog name ("megnet") becomes its package; anything else (a package
    name, ``pkg==1.2``, a folder, a URL) is passed to the installer as is."""
    known = entry(name_or_spec)
    return known.package if known else name_or_spec


def package_of(name: str) -> str:
    """The distribution to uninstall for a plugin or catalog name."""
    from meg_qc.plugins.registry import discover
    for found in discover(refresh=True):
        if name in (found.name, found.entry_point, found.package):
            return found.package
    return resolve_spec(name)


def installer_command(action: str, spec: str, *, upgrade: bool = False) -> List[str]:
    """The pip or uv command that installs or removes ``spec`` here."""
    if action not in ("install", "uninstall"):
        raise ValueError(action)
    if _has_pip():
        cmd = [sys.executable, "-m", "pip", action]
        if action == "uninstall":
            cmd.append("-y")
    else:
        uv = _uv()
        if uv is None:
            raise InstallError(
                "This Python environment has neither pip nor uv, so plugins "
                "cannot be installed into it. Install pip "
                f"({sys.executable} -m ensurepip) or uv, then try again.")
        cmd = [uv, "pip", action, "--python", sys.executable]
    if upgrade and action == "install":
        cmd.append("--upgrade")
    cmd.append(spec)
    return cmd


def run(cmd: List[str], echo: Callable[[str], None] = print) -> int:
    """Run an installer command, passing its output on line by line."""
    echo("$ " + " ".join(cmd))
    # pip writes in UTF-8 when asked to, so a path with "ü" in a warning is
    # read correctly instead of in the Windows code page.
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    with subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env,
                          text=True, encoding="utf-8", errors="replace") as proc:
        for line in proc.stdout:
            echo(line.rstrip("\n"))
        return proc.wait()


def installed_version(package: str) -> Optional[str]:
    try:
        return metadata.version(package)
    except metadata.PackageNotFoundError:
        return None
