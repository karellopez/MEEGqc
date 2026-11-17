"""Installing and removing plugins in the environment MEEGqc runs from.

An installation made by the MEEGqc installer installs them through the
installer's own uv, with its settings, as its updates do: nothing lands
outside the install folder and the user's pip or uv settings do not apply.
Elsewhere pip is used when this environment has it; environments made by uv
often do not, so uv (when found) installs into this Python instead. Nothing
needs administrator rights when MEEGqc itself was installed without them.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from importlib import metadata
from pathlib import Path
from typing import Callable, List, Optional, Sequence, Union

from meg_qc.plugins.catalog import entry, requirement_name


class InstallError(RuntimeError):
    pass


def _installation():
    """The MEEGqc installer's record of this environment, or ``None``."""
    from meg_qc.miscellaneous.GUI.updater import find_installation
    return find_installation()


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


def resolve_spec(name_or_spec: str) -> List[str]:
    """What the installer is given for a name. A catalog name ("megnet")
    becomes its package, or for a plugin that is part of MEEGqc the libraries
    it needs; anything else (a package name, ``pkg==1.2``, a folder, a URL)
    is passed to the installer as is."""
    known = entry(name_or_spec)
    return list(known.install_specs()) if known else [name_or_spec]


def package_of(name: str) -> List[str]:
    """The distributions to uninstall for a plugin or catalog name: a
    separate plugin's package, or what a built-in plugin installed."""
    from meg_qc.plugins.registry import discover
    known = entry(name)
    for found in discover(refresh=True):
        if name in (found.name, found.entry_point, found.package):
            if known is not None and known.built_in and found.package == known.package:
                return [requirement_name(r) for r in known.requirements]
            return [found.package]
    if known is not None and known.built_in:
        return [requirement_name(r) for r in known.requirements]
    return resolve_spec(name)


def installer_command(action: str, spec: Union[str, Sequence[str]], *,
                      upgrade: bool = False) -> List[str]:
    """The pip or uv command that installs or removes ``spec`` here."""
    if action not in ("install", "uninstall"):
        raise ValueError(action)
    installation = _installation()
    if installation is not None:
        cmd = [str(installation.uv), "pip", action, "--python", sys.executable]
        if action == "install":
            cmd.append("--compile-bytecode")
    elif _has_pip():
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
    cmd.extend([spec] if isinstance(spec, str) else spec)
    return cmd


def run(cmd: List[str], echo: Callable[[str], None] = print) -> int:
    """Run an installer command, passing its output on line by line.

    The installer's uv runs with the installer's settings and none meant for
    another uv, and an install it cannot do is tried once more with the
    system's certificates, which networks that inspect HTTPS need (as the
    installer and the update do)."""
    # pip writes in UTF-8 when asked to, so a path with "ü" in a warning is
    # read correctly instead of in the Windows code page.
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    installation = _installation()
    installers_uv = installation is not None and bool(cmd) and cmd[0] == str(installation.uv)
    if installers_uv:
        from meg_qc.miscellaneous.GUI._update_helper import uv_environment
        record = json.loads(installation.manifest.read_text(encoding="utf-8"))
        env = uv_environment(record, env)
    rc = _stream(cmd, env, echo)
    if rc != 0 and installers_uv and cmd[2:3] == ["install"]:
        echo("Trying again with the system's certificates...")
        rc = _stream(cmd, dict(env, UV_SYSTEM_CERTS="1"), echo)
    return rc


def _stream(cmd: List[str], env: dict, echo: Callable[[str], None]) -> int:
    echo("$ " + " ".join(cmd))
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
