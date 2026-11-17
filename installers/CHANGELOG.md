# Installer changelog

The installers have their own version, separate from MEEGqc's: an installer
puts MEEGqc on a computer, and MEEGqc then updates itself.
`INSTALLER_VERSION` in each script, the first heading below and the
installers in `installers.zip` always agree; `tests/test_installers.py`
checks it.

## 2.0.0 (2026-11-16)

The installers are built on uv instead of a portable Python kept in this
repository, the same as BIDS Manager's installer 2.0.3.

- uv 0.13.1 is downloaded into the install folder and checked against its
  published SHA-256 before it is used. A uv already on the computer is never
  called, and its settings (`uv.toml`, `UV_*` variables) are ignored.
- Python 3.10, as before, comes from that uv, into the install folder.
  Nothing is added to the system's `PATH` or the Windows registry.
- New platforms: Intel Macs, Linux on ARM64, Windows 11 on ARM (through its
  x64 emulation: numba has no ARM64 build for Windows at the versions MEEGqc
  uses).
- No administrator rights anywhere.
- Running the installer again offers Update (the default: the newest
  release), Reinstall, Uninstall and Abort. Uninstall asks once more, No by
  default, and removes the install folder and everything that starts MEEGqc:
  the app on macOS (unregistered from Launch Services first), the menu
  entries, Desktop files and icon on Linux, the Desktop and Start-menu
  shortcuts on Windows. Datasets, results and settings are kept.
  Reinstall and Uninstall wait until MEEGqc is closed.
- An installation from installer 1.0.0 is converted in place: its portable
  Python 3.10 is removed and the environment is rebuilt on uv's Python 3.10.
- When an update builds the environment again, the plugins installed in
  the old one are installed into the new one. One that cannot be is named,
  and MEEGqc is installed without it.
- `installation.json` in the install folder records the installer, uv,
  Python and MEEGqc versions, and how to call that uv. MEEGqc's update
  button reads it: from the release after 1.1.2 it updates through this uv,
  and moves the installation to a newer Python when a release needs one
  (marked `moved_by_update`, so running the installer again keeps it).
- macOS: MEEGqc.app declares `LSRequiresNativeExecution` and
  `LSArchitecturePriority`, so its launcher script is never started under
  Rosetta on Apple Silicon. The launchers put the environment's own programs
  first on PATH, as activating it would.
- Every call to the new environment's Python uses `-I`, so an installer
  started from inside a MEEGqc checkout checks and records the copy it
  installed, not the checkout.
- Nothing needs administrator rights, and the installers never suggest
  them: a system library the window needs and Linux lacks (libxcb-cursor)
  is fetched by MEEGqc itself at its first start, as before.
- The scripts are tracked uncompressed in `installers/`; `installers.zip` is
  built from them by `scripts/build_installers.py`, without macOS metadata.
- Optional settings for testing and for administrators: `MEEGQC_INSTALL_DIR`,
  `MEEGQC_PYTHON`, `MEEGQC_PACKAGE`, `MEEGQC_YES`, `MEEGQC_NO_SHORTCUTS`,
  `MEEGQC_TARGET`, `MEEGQC_UNINSTALL`.

## 1.0.0

Download a portable Python 3.10 from `external/python-embed/` in this
repository, create an environment, `pip install meg-qc`, and register a
launcher. Apple Silicon Macs, Linux x86_64 and Windows x64 only.
