#!/bin/bash
# =======================================================================
# MEEGqc installer for macOS (Apple Silicon and Intel)
# Installer version 2.0.0. History: installers/CHANGELOG.md
# Licence: MIT, ANCP Lab, University of Oldenburg
# https://github.com/ANCPLabOldenburg/MEEGqc
#
# Double-click this file. Without administrator rights, and entirely
# inside ~/MEEGqc, it installs:
#
#   uv/        a pinned copy of uv, checked against its published SHA-256.
#              A uv you already have is never used, nor its settings.
#   python/    the Python version below, downloaded by that uv
#   env/       an environment with meg-qc from PyPI
#
# and adds MEEGqc.app to ~/Applications. Running it again updates MEEGqc
# to the newest release, or uninstalls it.
#
# Optional settings, for testing and for administrators:
#   MEEGQC_INSTALL_DIR     where to install (default ~/MEEGqc)
#   MEEGQC_PYTHON          Python version (default 3.10)
#   MEEGQC_PACKAGE         what to install: "meg-qc==1.1.2", a wheel file...
#   MEEGQC_YES=1           take the default answer to every question
#   MEEGQC_NO_SHORTCUTS=1  no app, no Desktop uninstaller, nothing registered
#   MEEGQC_TARGET          force a build: aarch64-apple-darwin, x86_64-apple-darwin
#   MEEGQC_UNINSTALL=1     remove MEEGqc, without asking
# =======================================================================
set -euo pipefail

INSTALLER_VERSION="2.0.0"
UV_VERSION="0.13.1"
UV_SHA256_aarch64_apple_darwin="cfe454573412ff7380d15fc7dae5997ab53ffce72fce0a78e91ddbc84001acd2"
UV_SHA256_x86_64_apple_darwin="bb7b805bdcf3d0bc8c74ceb94569e6b3e8aa35bd492acdbb9d46bf46d9d4ff6b"

# 3.10, as installer 1.x and BIDS Manager's installer use. MEEGqc is tested
# on 3.10 to 3.14, and a newer Python brings newer libraries (MNE 1.13 from
# 3.11). The update button moves an installation to a newer Python when a
# release needs one.
PYTHON_VERSION="${MEEGQC_PYTHON:-3.10}"
PYTHON_MINOR="$(echo "$PYTHON_VERSION" | cut -d. -f1-2)"
PACKAGE="${MEEGQC_PACKAGE:-meg-qc}"
INSTALL_DIR="${MEEGQC_INSTALL_DIR:-$HOME/MEEGqc}"
ASSUME_YES="${MEEGQC_YES:-0}"
NO_SHORTCUTS="${MEEGQC_NO_SHORTCUTS:-0}"

# The folder is removed whole on reinstall and uninstall, so it must be one
# of its own: never empty, relative, the home folder or the disk.
INSTALL_DIR="${INSTALL_DIR%/}"
case "$INSTALL_DIR" in
    /*/*) ;;
    *) echo "MEEGQC_INSTALL_DIR must be an absolute path to a folder of its own."; exit 1 ;;
esac
if [[ "$INSTALL_DIR" == "${HOME%/}" ]]; then
    echo "MEEGqc needs a folder of its own, not the home folder itself."; exit 1
fi

UV_DIR="$INSTALL_DIR/uv"
UV="$UV_DIR/uv"
ENV_DIR="$INSTALL_DIR/env"
ENV_PY="$ENV_DIR/bin/python"
MANIFEST="$INSTALL_DIR/installation.json"
LOG_FILE="$INSTALL_DIR/install.log"
DESKTOP_DIR="$HOME/Desktop"
APPLICATIONS_DIR="$HOME/Applications"
APP_BUNDLE="$APPLICATIONS_DIR/MEEGqc.app"
MIN_DISK_MB=2500

# -- Output ---------------------------------------------------------------
if [[ -t 1 ]]; then
    RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[0;33m'
    CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'
else
    RED=''; GREEN=''; YELLOW=''; CYAN=''; BOLD=''; RESET=''
fi
log_info()  { echo -e "${GREEN}[ok]${RESET} $*"; }
log_step()  { echo -e "${CYAN}${BOLD}[..]${RESET} $*"; }
log_warn()  { echo -e "${YELLOW}[!]${RESET} $*"; }
log_error() { echo -e "${RED}[x]${RESET} $*" >&2; }
die()       { log_error "$*"; echo "    Log file: $LOG_FILE"; exit 1; }

# ask QUESTION DEFAULT: the answer, or DEFAULT when nobody can answer.
ask() {
    local reply=""
    if [[ "$ASSUME_YES" == "1" || ! -t 0 ]]; then
        echo "$2"
        return
    fi
    read -rp "$1" reply
    echo "${reply:-$2}"
}

# owns FILE: FILE (a launcher, an uninstaller) starts or removes this
# installation. Only those are ever removed: a test installation elsewhere
# must leave the launchers of the real one alone.
owns() { [[ -f "$1" ]] && grep -qF -e "$INSTALL_DIR/" -e "\"$INSTALL_DIR\"" "$1"; }

# version_at_least A B: A is B or newer, comparing major.minor ("3.13" "3.12").
version_at_least() {
    local a_major="${1%%.*}" b_major="${2%%.*}" a_rest="${1#*.}" b_rest="${2#*.}"
    local a_minor="${a_rest%%.*}" b_minor="${b_rest%%.*}"
    (( a_major > b_major || (a_major == b_major && a_minor >= b_minor) ))
}

# -- Isolation --------------------------------------------------------------
# Only this installer's uv, with only this installer's settings: variables
# set for another uv (an index, a cache, a Python preference) and any active
# environment must not reach it.
for name in $(compgen -e); do
    if [[ "$name" == UV_* ]]; then unset "$name"; fi
done
unset VIRTUAL_ENV CONDA_PREFIX PYTHONHOME PYTHONPATH 2>/dev/null || true
export UV_NO_CONFIG=1
export UV_CACHE_DIR="$INSTALL_DIR/cache"
export UV_PYTHON_INSTALL_DIR="$INSTALL_DIR/python"
export UV_MANAGED_PYTHON=1
export UV_PYTHON_DOWNLOADS=automatic

# -- Platform ---------------------------------------------------------------
if [[ -n "${MEEGQC_TARGET:-}" ]]; then
    TARGET="$MEEGQC_TARGET"
elif [[ "$(sysctl -n hw.optional.arm64 2>/dev/null || echo 0)" == "1" ]]; then
    # Not `uname -m`: in a terminal running under Rosetta it says x86_64
    # on an Apple Silicon Mac.
    TARGET="aarch64-apple-darwin"
else
    TARGET="x86_64-apple-darwin"
fi
case "$TARGET" in
    aarch64-apple-darwin) UV_SHA256="$UV_SHA256_aarch64_apple_darwin"; ARCH="aarch64"; CHIP="Apple Silicon" ;;
    x86_64-apple-darwin)  UV_SHA256="$UV_SHA256_x86_64_apple_darwin";  ARCH="x86_64";  CHIP="Intel" ;;
    *) echo "Unknown build: $TARGET"; exit 1 ;;
esac
# The architecture is part of the request, so the Python and every package
# match the build chosen above.
PYTHON_REQUEST="cpython-${PYTHON_VERSION}-macos-${ARCH}-none"

# -- Banner -----------------------------------------------------------------
clear 2>/dev/null || true
echo -e "${BOLD}"
cat << 'BANNER'

    +--------------------------------------------------+
    |                                                  |
    |                   M E E G q c                    |
    |                                                  |
    |      Automated MEG and EEG quality control       |
    |        ANCP Lab, University of Oldenburg         |
    |                                                  |
    +--------------------------------------------------+

BANNER
echo -e "${RESET}"
echo -e "  Installer version:  ${BOLD}${INSTALLER_VERSION}${RESET}"
echo -e "  Platform:           macOS, ${CHIP} (${ARCH})"
echo -e "  Python:             ${PYTHON_VERSION}"
echo -e "  Install folder:     ${BOLD}${INSTALL_DIR}${RESET}"
echo ""

# -- Before anything is written ------------------------------------------------
mkdir -p "$(dirname "$INSTALL_DIR")"
AVAIL_MB=$(df -m "$(dirname "$INSTALL_DIR")" | awk 'NR==2 {print $4}')
if (( AVAIL_MB < MIN_DISK_MB )); then
    log_warn "Only ${AVAIL_MB} MB free; about ${MIN_DISK_MB} MB are needed."
fi
if ! curl -fsS --max-time 8 -o /dev/null https://pypi.org/simple/ 2>/dev/null; then
    log_warn "pypi.org cannot be reached. Check the internet connection."
fi

MODE="install"
if [[ "${MEEGQC_UNINSTALL:-0}" == "1" ]]; then
    MODE="uninstall"
elif [[ -f "$MANIFEST" || -d "$ENV_DIR" ]]; then
    echo -e "${YELLOW}  MEEGqc is already installed in:${RESET}"
    echo "    $INSTALL_DIR"
    echo ""
    echo "  [u] Update: install the newest MEEGqc (default)"
    echo "  [r] Reinstall: remove this installation and start again"
    echo "  [x] Uninstall: remove MEEGqc from this computer"
    echo "  [a] Abort"
    echo ""
    while true; do
        choice="$(ask "  Your choice [U/r/x/a]: " u)"
        case "$choice" in
            [uU]) MODE="update"; break ;;
            [rR]) MODE="reinstall"; break ;;
            [xX]) MODE="uninstall"; break ;;
            [aA]) echo "  Nothing was changed."; exit 0 ;;
            *)    echo "  Please type u, r, x or a." ;;
        esac
    done
else
    echo -e "${BOLD}  The installer will:${RESET}"
    echo "    1. Download uv ${UV_VERSION} (about 20 MB) into the install folder"
    echo "    2. Download Python ${PYTHON_VERSION} with it (about 20 MB)"
    echo "    3. Install meg-qc and its dependencies (about 1 GB)"
    echo "    4. Add MEEGqc.app to ~/Applications"
    echo "    No administrator password is needed."
    echo ""
    confirm="$(ask "  Continue? [Y/n] " y)"
    if [[ "$confirm" == [nN]* ]]; then
        echo "  Nothing was changed."
        exit 0
    fi
fi

# Removing a program while it runs would leave it to fail at random later.
if [[ "$MODE" == "reinstall" || "$MODE" == "uninstall" ]] && command -v pgrep >/dev/null 2>&1 \
    && pgrep -f "$ENV_DIR/" >/dev/null 2>&1; then
    echo "  MEEGqc is running. Close it, then run the installer again."
    exit 1
fi

# Uninstall: everything this installer (or installer 1.x) put on the
# computer. Datasets and MEEGqc's settings are not touched.
if [[ "$MODE" == "uninstall" ]]; then
    echo ""
    echo "  This removes MEEGqc completely:"
    echo "    $INSTALL_DIR"
    if owns "$APP_BUNDLE/Contents/MacOS/MEEGqc"; then echo "    $APP_BUNDLE"; fi
    echo "  Your datasets and your settings are not touched."
    if [[ "${MEEGQC_UNINSTALL:-0}" != "1" ]]; then
        confirm="$(ask "  Remove it? [y/N] " n)"
        if [[ "$confirm" != [yY]* ]]; then
            echo "  Nothing was removed."
            exit 0
        fi
    fi
    if owns "$APP_BUNDLE/Contents/MacOS/MEEGqc"; then
        /System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister \
            -u "$APP_BUNDLE" 2>/dev/null || true
        rm -rf "$APP_BUNDLE"
    fi
    if owns "$DESKTOP_DIR/Uninstall_MEEGqc.command"; then
        rm -f "$DESKTOP_DIR/Uninstall_MEEGqc.command"
    fi
    rm -rf "${INSTALL_DIR:?}"
    log_info "MEEGqc has been removed."
    exit 0
fi

if [[ "$MODE" == "reinstall" ]]; then
    log_step "Removing the previous installation..."
    rm -rf "${INSTALL_DIR:?}"
fi

mkdir -p "$INSTALL_DIR"
: > "$LOG_FILE"
exec > >(tee -a "$LOG_FILE") 2>&1
echo "MEEGqc installer $INSTALLER_VERSION ($MODE) started $(date)"
echo "Build $TARGET, Python $PYTHON_REQUEST, package $PACKAGE"
echo ""

TMP="$(mktemp -d "${TMPDIR:-/tmp}/meegqc-install.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT

# run_uv ARGS...: once as is, then once more with the system's certificates,
# which networks that inspect HTTPS (many universities) need.
run_uv() {
    if "$UV" "$@"; then
        return 0
    fi
    log_warn "Trying again with the system's certificates..."
    UV_SYSTEM_CERTS=1 "$UV" "$@"
}

# =======================================================================
# Step 1/5: uv
# =======================================================================
if [[ -x "$UV" && "$("$UV" --version 2>/dev/null | awk '{print $2}')" == "$UV_VERSION" ]]; then
    log_info "Step 1/5: uv ${UV_VERSION} is already here."
else
    log_step "Step 1/5: Downloading uv ${UV_VERSION}..."
    url="https://github.com/astral-sh/uv/releases/download/${UV_VERSION}/uv-${TARGET}.tar.gz"
    curl -fsSL --retry 3 --retry-delay 3 -o "$TMP/uv.tar.gz" "$url" \
        || die "uv could not be downloaded from $url"
    actual="$(shasum -a 256 "$TMP/uv.tar.gz" | awk '{print $1}')"
    if [[ "$actual" != "$UV_SHA256" ]]; then
        die "The uv download does not match its published checksum, so it was not used."
    fi
    tar -xzf "$TMP/uv.tar.gz" -C "$TMP" || die "The uv archive could not be unpacked."
    mkdir -p "$UV_DIR"
    rm -f "$UV"
    mv "$TMP/uv-${TARGET}/uv" "$UV"
    chmod +x "$UV"
    "$UV" --version >/dev/null || die "uv does not run on this Mac."
    log_info "uv ${UV_VERSION} installed in $UV_DIR"
fi

# =======================================================================
# Step 2/5: Python
# =======================================================================
log_step "Step 2/5: Python ${PYTHON_VERSION}..."
run_uv python install "$PYTHON_REQUEST" --no-bin \
    || die "Python ${PYTHON_VERSION} could not be installed."
log_info "Python ready: $("$UV" python find "$PYTHON_REQUEST")"

# =======================================================================
# Step 3/5: the environment
# =======================================================================
# Every call to the environment's Python uses -I: without it, Python looks
# in the current folder first, and run from inside a MEEGqc checkout it
# would read that copy instead of the one just installed.
#
# Kept on update when it runs the requested Python for this build, or a
# newer one the update button moved it to because a release needed it
# (the record says so: moving it back would install an older MEEGqc);
# built again otherwise (an installation from installer 1.x, another
# Python, a Mac migrated from Intel, a broken environment).
env_python="$("$ENV_PY" -I -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || true)"
same_build=0
moved=0
if [[ -f "$MANIFEST" ]] && grep -q "\"target\": \"$TARGET\"" "$MANIFEST"; then
    same_build=1
    if grep -q '"moved_by_update": true' "$MANIFEST"; then moved=1; fi
fi
KEEP_MOVE=0
: > "$TMP/plugins.txt"
if [[ "$MODE" == "update" && "$same_build" == "1" && -n "$env_python" ]] \
    && { [[ "$env_python" == "$PYTHON_MINOR" ]] \
         || { [[ "$moved" == "1" ]] && version_at_least "$env_python" "$PYTHON_MINOR"; }; }; then
    if [[ "$env_python" != "$PYTHON_MINOR" ]]; then KEEP_MOVE=1; fi
    log_info "Step 3/5: The environment is kept (Python $env_python)."
else
    log_step "Step 3/5: Creating the environment..."
    # The plugins installed in the environment about to be replaced, one
    # requirement per line, so the new one gets them back.
    if [[ "$MODE" == "update" && -n "$env_python" ]]; then
        "$ENV_PY" -I - > "$TMP/plugins.txt" 2>/dev/null << 'PYEOF' || : > "$TMP/plugins.txt"
import json
import re
from importlib.metadata import PackageNotFoundError, distribution, entry_points
from urllib.parse import urlparse
from urllib.request import url2pathname

specs = {}
for point in entry_points(group="meegqc.plugins"):
    dist = getattr(point, "dist", None)
    if dist is None:
        continue
    name = dist.metadata["Name"]
    if name == "meegqc-megnet":
        continue  # MEGnet is part of MEEGqc now: its libraries follow below
    try:
        origin = json.loads(dist.read_text("direct_url.json") or "{}")
    except ValueError:
        origin = {}
    url = origin.get("url", "")
    vcs = origin.get("vcs_info")
    if not url:
        specs[name] = name
    elif vcs:
        specs[name] = "%s @ %s+%s@%s" % (name, vcs["vcs"], url, vcs.get("commit_id", ""))
    elif origin.get("dir_info", {}).get("editable"):
        specs[name] = "-e " + url2pathname(urlparse(url).path)
    else:
        specs[name] = "%s @ %s" % (name, url)

# A plugin that is part of MEEGqc (MEGnet) is one of its extras: the
# libraries of every extra whose libraries are all installed.
try:
    required = distribution("meg_qc").requires or []
except PackageNotFoundError:
    required = []
extras = {}
for line in required:
    requirement, _, marker = line.partition(";")
    found = re.search("extra *== *.([A-Za-z0-9_.-]+)", marker)
    if found:
        extras.setdefault(found.group(1), []).append(requirement.strip())
for requirements in extras.values():
    try:
        for requirement in requirements:
            distribution(re.match("[A-Za-z0-9._-]+", requirement).group(0))
    except PackageNotFoundError:
        continue
    for requirement in requirements:
        specs[requirement] = requirement
for spec in sorted(specs.values()):
    print(spec)
PYEOF
    fi
    # Installer 1.x unpacked a portable Python 3.10 into the install folder
    # itself; nothing uses it any more.
    if [[ -x "$INSTALL_DIR/bin/python3.10" ]]; then
        rm -rf "${INSTALL_DIR:?}/bin" "${INSTALL_DIR:?}/include" "${INSTALL_DIR:?}/lib" "${INSTALL_DIR:?}/share"
    fi
    # --seed adds pip, which the update button of MEEGqc 1.1.2 uses.
    "$UV" venv --clear --seed --prompt MEEGqc --python "$PYTHON_REQUEST" "$ENV_DIR" \
        || die "The environment could not be created."
    log_info "Environment ready in $ENV_DIR"
fi

# Pythons no environment uses any more (an earlier default, a move made by
# the update button), about 70 MB each.
in_use="$("$ENV_PY" -I -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || true)"
for dir in "$UV_PYTHON_INSTALL_DIR"/cpython-*; do
    name="$(basename "$dir")"
    if [[ -n "$in_use" && "$name" =~ ^cpython-([0-9]+\.[0-9]+)\.[0-9]+[^-]*-(.+)$ \
          && "${BASH_REMATCH[1]}" != "$in_use" ]]; then
        "$UV" python uninstall "cpython-${BASH_REMATCH[1]}-${BASH_REMATCH[2]}" >/dev/null 2>&1 \
            && log_info "Removed Python ${BASH_REMATCH[1]}, which nothing uses any more."
    fi
done

# =======================================================================
# Step 4/5: MEEGqc
# =======================================================================
log_step "Step 4/5: Installing ${PACKAGE} (a few minutes the first time)..."
# --upgrade: an update installs the newest release, not the one already here.
# --compile-bytecode: what pip does, and what keeps the first start quick.
run_uv pip install --python "$ENV_PY" --upgrade --compile-bytecode "$PACKAGE" \
    || die "meg-qc could not be installed."
"$ENV_PY" -I -c "import meg_qc, PyQt6.QtWidgets" \
    || die "meg-qc was installed but does not start."
MQ_VERSION="$("$ENV_PY" -I -c 'from importlib.metadata import version; print(version("meg_qc"))')"
log_info "MEEGqc ${MQ_VERSION} installed."

# The plugins of the environment this one replaced. One that cannot be
# installed again does not stop the installation: MEEGqc runs without it.
while IFS= read -r spec; do
    [[ -n "$spec" ]] || continue
    if [[ "$spec" == "-e "* ]]; then args=(-e "${spec#-e }"); else args=("$spec"); fi
    if run_uv pip install --python "$ENV_PY" --compile-bytecode "${args[@]}"; then
        log_info "Plugin installed again: $spec"
    else
        log_warn "The plugin $spec could not be installed again. Add it in MEEGqc's Plugins window."
    fi
done < "$TMP/plugins.txt"
"$UV" cache prune >/dev/null 2>&1 || true

# The record of this installation. MEEGqc reads it to find the uv and the
# Python it was installed with.
MQ_MANIFEST="$MANIFEST" MQ_INSTALLER="$INSTALLER_VERSION" MQ_TARGET="$TARGET" \
MQ_UV="$UV" MQ_UV_VERSION="$UV_VERSION" MQ_PYTHON="$PYTHON_VERSION" \
MQ_ENV="$ENV_DIR" MQ_PACKAGE="$PACKAGE" MQ_VERSION="$MQ_VERSION" MQ_KEEP_MOVE="$KEEP_MOVE" \
"$ENV_PY" -I - << 'PYEOF' || log_warn "installation.json could not be written."
import datetime, json, os, platform, sys
e = os.environ
path = e["MQ_MANIFEST"]
now = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat()
try:
    with open(path, encoding="utf-8") as fh:
        previous = json.load(fh)
except (OSError, ValueError):
    previous = {}
first = previous.get("installed", now)
record = {
    "format": 1,
    "installed": first,
    "updated": now,
    "installer": {"version": e["MQ_INSTALLER"], "os": "macos", "target": e["MQ_TARGET"]},
    "uv": {"version": e["MQ_UV_VERSION"], "executable": e["MQ_UV"]},
    "uv_environment": {k: e[k] for k in (
        "UV_NO_CONFIG", "UV_CACHE_DIR", "UV_PYTHON_INSTALL_DIR", "UV_MANAGED_PYTHON")},
    "python": {"requested": e["MQ_PYTHON"], "version": platform.python_version(),
               "executable": sys.executable},
    "environment": e["MQ_ENV"],
    "package": {"name": "meg-qc", "requested": e["MQ_PACKAGE"],
                "version": e["MQ_VERSION"]},
}
if e.get("MQ_KEEP_MOVE") == "1":
    # A Python the update button moved to stays recorded as such.
    record["python"]["requested"] = previous["python"]["requested"]
    record["python"]["moved_by_update"] = True
with open(path, "w", encoding="utf-8") as fh:
    json.dump(record, fh, indent=2)
    fh.write("\n")
PYEOF

# =======================================================================
# Step 5/5: MEEGqc.app and the uninstaller
# =======================================================================
UNINSTALL_SCRIPT="$INSTALL_DIR/uninstall_MEEGqc.command"
cat > "$UNINSTALL_SCRIPT" << UNINSTALLER_EOF
#!/bin/bash
echo ""
echo "  MEEGqc uninstaller"
echo ""
echo "  This removes MEEGqc completely:"
echo "    $INSTALL_DIR"
echo "  and the app and Desktop uninstaller that belong to it."
echo "  Your datasets and your settings are not touched."
echo ""
read -rp "  Remove it? [y/N] " confirm
if [[ "\$confirm" != [yY]* ]]; then
    echo "  Nothing was removed."
    exit 0
fi
# Only the launchers of this installation: another one may own them now.
owns() { [[ -f "\$1" ]] && grep -qF -e "$INSTALL_DIR/" -e "\"$INSTALL_DIR\"" "\$1"; }
if owns "$APP_BUNDLE/Contents/MacOS/MEEGqc"; then
    /System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -u "$APP_BUNDLE" 2>/dev/null
    rm -rf "$APP_BUNDLE"
fi
if owns "$DESKTOP_DIR/Uninstall_MEEGqc.command"; then
    rm -f "$DESKTOP_DIR/Uninstall_MEEGqc.command"
fi
rm -rf "$INSTALL_DIR"
echo ""
echo "  MEEGqc has been removed."
echo ""
UNINSTALLER_EOF
chmod +x "$UNINSTALL_SCRIPT"

if [[ "$NO_SHORTCUTS" == "1" ]]; then
    log_info "Step 5/5: No app or shortcuts (MEEGQC_NO_SHORTCUTS=1)."
else
    log_step "Step 5/5: Adding MEEGqc.app to ~/Applications..."
    mkdir -p "$APPLICATIONS_DIR"
    rm -rf "$APP_BUNDLE"
    mkdir -p "$APP_BUNDLE/Contents/MacOS" "$APP_BUNDLE/Contents/Resources"

    # Runs the program itself, so macOS treats it as a real application
    # (Dock icon, Spotlight, Keep in Dock). Its output goes to meegqc.log.
    # The environment's bin folder goes first on PATH, as activating it
    # would, so the programs installed with MEEGqc are the ones found.
    cat > "$APP_BUNDLE/Contents/MacOS/MEEGqc" << APP_EXEC_EOF
#!/bin/bash
export PATH="$ENV_DIR/bin:\$PATH"
exec "$ENV_DIR/bin/meegqc" "\$@" >> "$INSTALL_DIR/meegqc.log" 2>&1
APP_EXEC_EOF
    chmod +x "$APP_BUNDLE/Contents/MacOS/MEEGqc"

    # The launcher is a script, and macOS cannot tell a script's
    # architecture, so on Apple Silicon it may start it under Rosetta.
    # Python (arm64 only) still runs natively, but every universal program
    # it starts then runs as Intel (in BIDS Manager this made a compiler
    # refuse to build for the Mac it ran on). The keys below make macOS
    # start the launcher natively.
    if [[ "$ARCH" == "aarch64" ]]; then
        ARCH_KEYS="<key>LSRequiresNativeExecution</key>  <true/>
    <key>LSArchitecturePriority</key>     <array><string>arm64</string></array>"
    else
        ARCH_KEYS="<key>LSArchitecturePriority</key>     <array><string>x86_64</string></array>"
    fi
    cat > "$APP_BUNDLE/Contents/Info.plist" << PLIST_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
    "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleExecutable</key>         <string>MEEGqc</string>
    <key>CFBundleIdentifier</key>         <string>de.uni-oldenburg.ancplab.meegqc</string>
    <key>CFBundleName</key>               <string>MEEGqc</string>
    <key>CFBundleDisplayName</key>        <string>MEEGqc</string>
    <key>CFBundleIconFile</key>           <string>AppIcon</string>
    <key>CFBundleVersion</key>            <string>${INSTALLER_VERSION}</string>
    <key>CFBundleShortVersionString</key> <string>${INSTALLER_VERSION}</string>
    <key>CFBundlePackageType</key>        <string>APPL</string>
    <key>NSHighResolutionCapable</key>    <true/>
    <key>LSMinimumSystemVersion</key>     <string>11.0</string>
    ${ARCH_KEYS}
</dict>
</plist>
PLIST_EOF

    PKG_DIR="$("$ENV_PY" -I -c 'import importlib.util, os; print(os.path.dirname(importlib.util.find_spec("meg_qc").origin))' 2>/dev/null || true)"
    if [[ -n "$PKG_DIR" && -f "$PKG_DIR/miscellaneous/GUI/assets/macos/AppIcon.icns" ]]; then
        cp "$PKG_DIR/miscellaneous/GUI/assets/macos/AppIcon.icns" "$APP_BUNDLE/Contents/Resources/AppIcon.icns"
    else
        log_warn "The app icon was not found; the app shows the default icon."
    fi

    # Opens on the first double-click, and Spotlight finds it straight away.
    xattr -rd com.apple.quarantine "$APP_BUNDLE" 2>/dev/null || true
    /System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister \
        -f "$APP_BUNDLE" 2>/dev/null || true

    if [[ -d "$DESKTOP_DIR" ]]; then
        cp "$UNINSTALL_SCRIPT" "$DESKTOP_DIR/Uninstall_MEEGqc.command"
        chmod +x "$DESKTOP_DIR/Uninstall_MEEGqc.command"
    fi
    log_info "MEEGqc.app is in ~/Applications."
fi

# =======================================================================
# Done
# =======================================================================
echo ""
echo -e "${BOLD}=======================================================${RESET}"
echo -e "${GREEN}${BOLD}  MEEGqc ${MQ_VERSION} is installed.${RESET}"
echo -e "${BOLD}=======================================================${RESET}"
echo ""
echo -e "  ${BOLD}Start it:${RESET}"
echo -e "    Finder, Applications, ${BOLD}MEEGqc${RESET}; or Spotlight (Command Space)."
echo -e "    To keep it in the Dock: right-click its icon, Options, Keep in Dock."
echo -e "    Its output is saved in ${CYAN}$INSTALL_DIR/meegqc.log${RESET}"
echo ""
echo -e "  ${BOLD}Update:${RESET}     run this installer again"
echo -e "  ${BOLD}Uninstall:${RESET}  Uninstall_MEEGqc.command on the Desktop"
echo -e "  ${BOLD}Log:${RESET}        $LOG_FILE"
echo ""
