# MEEGqc installers

One file per system. Each installs MEEGqc for the current user, in a folder
of its own, without administrator rights. Running it again updates MEEGqc
to the newest release.

| System | File | How to run it |
|---|---|---|
| macOS 11 or newer, Apple Silicon or Intel | `MacOS/install_MEEGqc.command` | Double-click it. If macOS refuses a file from the internet: right-click it, Open, Open. |
| Linux, x86_64 or ARM64 (Ubuntu, Debian, Fedora...) | `Linux/install_MEEGqc.sh` | `bash install_MEEGqc.sh` |
| Windows 10 or 11 (x64), Windows 11 on ARM | `Windows/install_MEEGqc.bat` | Double-click it. If Windows says it protected your PC: More info, Run anyway. |

## What it installs

Everything goes into `~/MEEGqc` (`%USERPROFILE%\MEEGqc` on Windows):

```
MEEGqc/
  uv/                 uv, checked against its published SHA-256
  python/             Python 3.10, downloaded by that uv
  env/                the environment MEEGqc and its plugins run in
  cache/              uv's download cache, which makes updates quick
  installation.json   what is installed, with which versions
  install.log         what the last run did
  uninstall_MEEGqc.*
```

plus a way to start it: `MEEGqc.app` in `~/Applications` on macOS, a menu
entry and a Desktop file on Linux, shortcuts on the Desktop and in the
Start menu on Windows.

The installer uses only its own uv. A uv already on the computer is never
called, and the user's uv settings (`uv.toml`, `UV_*` variables) do not
reach it. The Python it downloads is not added to `PATH` or to the Windows
registry, so other programs never see it.

Python 3.10, as installer 1.x and BIDS Manager's installer use; the update
button moves the installation to a newer Python when a release needs one.

## Updating

MEEGqc's Check updates button, or running the installer again. From the
release after MEEGqc 1.1.2, the button updates through the installer's own
uv, so an update leaves nothing outside the install folder; and when a new
release needs a newer Python, it moves the installation to it, putting the
previous one back if anything goes wrong. (1.1.2's button updates with pip,
which works but keeps its download cache outside.)

Plugins (MEEGqc's Plugins window, or `meegqc-plugins install`) go into the
same environment, through the same uv. When an update has to build the
environment again (a move to a newer Python, an installation from installer
1.x), the plugins it held are installed into the new one.

## Removing it

Run the installer again and choose `[x] Uninstall`, or run
`uninstall_MEEGqc` (on the Desktop, in the menu, or in the install folder).
Either removes the install folder and the launchers. Datasets, MEEGqc's
results and its settings are not touched.

## Options

For testing and for administrators. Environment variables, all optional:

| Variable | Effect |
|---|---|
| `MEEGQC_INSTALL_DIR` | Install somewhere else (an absolute path to a folder of its own) |
| `MEEGQC_PYTHON` | Another Python version (default `3.10`) |
| `MEEGQC_PACKAGE` | What to install: `meg-qc==1.1.2`, a wheel file, ... |
| `MEEGQC_YES=1` | Take the default answer to every question |
| `MEEGQC_NO_SHORTCUTS=1` | No app, menu entry or shortcuts |
| `MEEGQC_UNINSTALL=1` | Remove MEEGqc without asking (`MEEGQC_YES=1` alone never does) |
| `MEEGQC_TARGET` | macOS and Linux: force a build, for example `x86_64-apple-darwin` on an Apple Silicon Mac (it runs through Rosetta) |

A test installation that leaves the real one alone, on macOS or Linux:

```bash
MEEGQC_INSTALL_DIR=/tmp/meegqc-test MEEGQC_YES=1 MEEGQC_NO_SHORTCUTS=1 \
    bash install_MEEGqc.command
```

On Windows, in a Command Prompt:

```bat
set MEEGQC_INSTALL_DIR=%TEMP%\meegqc-test
set MEEGQC_YES=1
set MEEGQC_NO_SHORTCUTS=1
install_MEEGqc.bat
```

## For maintainers

- The scripts in this folder are the source. `installers.zip` is built from
  them with `python scripts/build_installers.py`, and
  `tests/test_installers.py` fails when the zip is out of date or the
  scripts disagree with each other.
- A change: edit the scripts, raise `INSTALLER_VERSION` in all three, add a
  `CHANGELOG.md` entry, rebuild the zip.
- A new uv: change `UV_VERSION` and the SHA-256 of each archive, published
  next to it as `https://github.com/astral-sh/uv/releases/download/<version>/<archive>.sha256`.
- The Windows file is ASCII with CRLF line endings, the others use LF;
  `.gitattributes` keeps them so on every checkout.
- The installers follow BIDS Manager's (`ANCPLabOldenburg/BIDS-Manager`,
  `Installers/`), so a fix found in one usually belongs in the other.
