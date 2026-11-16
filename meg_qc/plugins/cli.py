"""``meegqc-plugins``: list, install and remove MEEGqc plugins.

    meegqc-plugins list                 installed plugins and installable ones
    meegqc-plugins info megnet          one plugin in detail
    meegqc-plugins install megnet       a catalog name, a package, a folder or a URL
    meegqc-plugins remove megnet

Also ``python -m meg_qc.plugins.cli ...`` (what the GUI runs).
"""
from __future__ import annotations

import argparse
import sys
from typing import List, Optional

from meg_qc.plugins import manage, registry
from meg_qc.plugins.api import PLUGIN_API_VERSION
from meg_qc.plugins.catalog import CATALOG, entry


def _list() -> int:
    found = registry.discover(refresh=True)
    print(f"MEEGqc plugin API {PLUGIN_API_VERSION}; Python {sys.executable}")
    print()
    if found:
        print("Installed:")
        for f in found:
            if f.plugin:
                print(f"  {f.plugin.name:<12} {f.plugin.label} {f.plugin.version} "
                      f"(package {f.package} {f.package_version})")
            else:
                print(f"  {f.entry_point:<12} NOT LOADED: {f.error} "
                      f"(package {f.package} {f.package_version})")
    else:
        print("Installed: none")
    installed = {f.name for f in found} | {f.package for f in found}
    available = [e for e in CATALOG if e.name not in installed and e.package not in installed]
    if available:
        print()
        print("Available (meegqc-plugins install NAME):")
        for e in available:
            print(f"  {e.name:<12} {e.label}: {e.summary}")
    return 0


def _info(name: str) -> int:
    found = next((f for f in registry.discover(refresh=True)
                  if name in (f.name, f.entry_point, f.package)), None)
    known = entry(name)
    if found is None and known is None:
        print(f"No plugin named {name!r} is installed or in the catalog.")
        return 1
    if found and found.plugin:
        p = found.plugin
        print(f"{p.label} ({p.name}) {p.version}, package {found.package} {found.package_version}")
        print(p.summary)
        if p.settings_section:
            print(f"Settings: section [{p.settings_section}] of settings.ini")
        if p.reference_provider is not None:
            print("Adds: ECG/EOG reference signals")
        if p.homepage:
            print(p.homepage)
    elif found:
        print(f"{found.entry_point}: installed (package {found.package} "
              f"{found.package_version}) but not loaded: {found.error}")
    else:
        print(f"{known.label} ({known.name}): not installed. Package {known.package}.")
        print(known.summary)
        if known.homepage:
            print(known.homepage)
    return 0


def _install(spec: str, upgrade: bool) -> int:
    try:
        cmd = manage.installer_command("install", manage.resolve_spec(spec), upgrade=upgrade)
    except manage.InstallError as exc:
        print(exc)
        return 1
    rc = manage.run(cmd)
    if rc == 0:
        print("Installed. Restart MEEGqc (the GUI, or start a new run) to use it.")
    return rc


def _remove(name: str) -> int:
    try:
        cmd = manage.installer_command("uninstall", manage.package_of(name))
    except manage.InstallError as exc:
        print(exc)
        return 1
    rc = manage.run(cmd)
    if rc == 0:
        print("Removed. Restart MEEGqc for this to take effect.")
    return rc


def main(argv: Optional[List[str]] = None) -> int:
    # A character the console cannot show (a Windows code page) is replaced,
    # never a UnicodeEncodeError in the middle of an installation.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(prog="meegqc-plugins",
                                 description="List, install and remove MEEGqc plugins.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="installed plugins and the ones MEEGqc can install")
    p_info = sub.add_parser("info", help="details of one plugin")
    p_info.add_argument("name")
    p_inst = sub.add_parser("install", help="install a plugin into this environment")
    p_inst.add_argument("spec", help="catalog name (megnet), package, folder or URL")
    p_inst.add_argument("--upgrade", action="store_true", help="upgrade if installed")
    p_rm = sub.add_parser("remove", help="uninstall a plugin")
    p_rm.add_argument("name")
    a = ap.parse_args(argv)
    if a.cmd == "list":
        return _list()
    if a.cmd == "info":
        return _info(a.name)
    if a.cmd == "install":
        return _install(a.spec, a.upgrade)
    return _remove(a.name)


if __name__ == "__main__":
    raise SystemExit(main())
