"""The installers agree with each other, with their changelog and with the zip.

Three scripts in three languages pin the same uv, carry the same version and
must never ask for administrator rights. Nothing but a test notices when one
of them drifts, and the zip the documentation links to is a copy that can
silently fall behind the sources it was built from.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
INSTALLERS = ROOT / "installers"
MACOS = INSTALLERS / "MacOS" / "install_MEEGqc.command"
LINUX = INSTALLERS / "Linux" / "install_MEEGqc.sh"
WINDOWS = INSTALLERS / "Windows" / "install_MEEGqc.bat"
UNIX = [MACOS, LINUX]
ALL = [MACOS, LINUX, WINDOWS]


def _builder():
    spec = importlib.util.spec_from_file_location(
        "build_installers", ROOT / "scripts" / "build_installers.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _text(path: Path) -> str:
    return path.read_bytes().decode("utf-8")


def _installer_version(path: Path) -> str:
    pattern = (r"^\$InstallerVersion = '([^']+)'" if path == WINDOWS
               else r'^INSTALLER_VERSION="([^"]+)"')
    return re.search(pattern, _text(path), re.M).group(1)


def _uv_version(path: Path) -> str:
    pattern = r"^\$UvVersion = '([^']+)'" if path == WINDOWS else r'^UV_VERSION="([^"]+)"'
    return re.search(pattern, _text(path), re.M).group(1)


def _uv_checksums(path: Path) -> dict[str, str]:
    """Target triple -> SHA-256, as the script pins them."""
    text = _text(path)
    if path == WINDOWS:
        target = re.search(r"^\$UvTarget = '([^']+)'", text, re.M).group(1)
        return {target: re.search(r"^\$UvSha256 = '([^']+)'", text, re.M).group(1)}
    return {name.replace("_", "-").replace("x86-64", "x86_64"): value
            for name, value in re.findall(r'^UV_SHA256_(\w+)="([^"]+)"', text, re.M)}


def test_the_zip_holds_exactly_the_sources():
    builder = _builder()
    assert builder.contents(builder.ZIP) == builder.expected(), (
        "installers.zip is out of date: run python scripts/build_installers.py")


def test_the_zip_keeps_the_scripts_executable_and_carries_no_mac_metadata():
    names = _builder().contents()
    for name, (_data, mode) in names.items():
        assert "__MACOSX" not in name and "/._" not in name and ".DS_Store" not in name
        if name.endswith((".command", ".sh", ".bat")):
            assert mode == 0o755, name


def test_the_zip_unpacks_into_the_folder_the_documentation_names():
    """The README and the documentation link to installers/installers.zip and
    tell people to open installers/MacOS, installers/Linux, installers/Windows."""
    names = set(_builder().contents())
    assert {"installers/MacOS/install_MEEGqc.command", "installers/Linux/install_MEEGqc.sh",
            "installers/Windows/install_MEEGqc.bat"} <= names


def test_every_script_carries_the_changelog_version():
    newest = re.search(r"^## (\S+)", _text(INSTALLERS / "CHANGELOG.md"), re.M).group(1)
    assert {_installer_version(path) for path in ALL} == {newest}


def test_every_script_pins_the_same_uv_with_the_same_checksums():
    assert len({_uv_version(path) for path in ALL}) == 1
    pinned: dict[str, str] = {}
    for path in ALL:
        for target, digest in _uv_checksums(path).items():
            assert re.fullmatch(r"[0-9a-f]{64}", digest), (path.name, target)
            assert pinned.setdefault(target, digest) == digest, target
    assert set(pinned) == {
        "aarch64-apple-darwin", "x86_64-apple-darwin",
        "x86_64-unknown-linux-gnu", "aarch64-unknown-linux-gnu",
        "x86_64-pc-windows-msvc",
    }


@pytest.mark.parametrize("path", UNIX, ids=lambda p: p.name)
def test_unix_scripts_never_run_anything_as_administrator(path):
    for number, line in enumerate(_text(path).splitlines(), 1):
        code = line.split("#", 1)[0].strip()
        if code.startswith("echo"):
            continue  # advice printed for the user, not a command run
        assert not re.search(r"(^|[|;&(`]\s*)sudo\b", code), f"{path.name}:{number}"
        assert not re.search(r"\b(su|doas|pkexec)\s", code), f"{path.name}:{number}"


def test_the_windows_script_never_asks_for_elevation():
    text = _text(WINDOWS)
    assert not re.search(r"runas", text, re.I)
    assert "Start-Process" not in text


@pytest.mark.parametrize("path", ALL, ids=lambda p: p.name)
def test_every_script_isolates_its_uv(path):
    text = _text(path)
    for variable in ("UV_NO_CONFIG", "UV_CACHE_DIR", "UV_PYTHON_INSTALL_DIR", "UV_MANAGED_PYTHON"):
        assert variable in text, variable
    # Variables set for another uv are removed before this one runs.
    assert "UV_*" in text
    # The Python it downloads is never put on PATH or in the registry.
    assert "--no-bin" in text
    if path == WINDOWS:
        assert "--no-registry" in text
    # An update installs the newest release, not the one already there.
    assert "--upgrade" in text


def test_the_windows_script_is_ascii_with_crlf_line_endings():
    data = WINDOWS.read_bytes()
    data.decode("ascii")
    assert data.count(b"\n") == data.count(b"\r\n")
    # The batch part ends where the PowerShell block comment closes.
    assert data.startswith(b"<# :")


@pytest.mark.parametrize("path", UNIX, ids=lambda p: p.name)
def test_unix_scripts_use_lf_line_endings(path):
    assert b"\r" not in path.read_bytes()


@pytest.mark.parametrize("path", ALL, ids=lambda p: p.name)
def test_every_call_to_the_new_environments_python_is_isolated(path):
    """Without -I, Python looks in the current folder first: an installer
    started from inside a checkout checked, recorded and took the icon from
    that copy instead of the one it had just installed (found in BIDS
    Manager's installer, 2026-10-16)."""
    text = _text(path)
    if path == WINDOWS:
        calls = re.findall(r"& \$EnvPy (\S+)", text) + re.findall(
            r"Invoke-Program \$EnvPy @\(([^,)]+)", text)
        assert calls and all(call in ("-I", "'-I'") for call in calls), calls
    else:
        calls = re.findall(r'"\$ENV_PY" (-\S*)', text)
        # "--python "$ENV_PY" --upgrade" is an argument to uv, not a call.
        calls = [call for call in calls if not call.startswith("--")]
        assert calls and all(call == "-I" for call in calls), calls


@pytest.mark.parametrize(
    ("have", "want", "kept"),
    [("3.12", "3.12", True), ("3.13", "3.12", True), ("3.11", "3.12", False),
     ("4.0", "3.12", True), ("3.12", "3.12.4", True), ("3.9", "3.12", False)],
)
def test_an_update_keeps_an_environment_on_the_requested_python_or_newer(have, want, kept):
    """MEEGqc's own update can move an installation to a newer Python;
    re-running the installer must not move it back, which would install an
    older MEEGqc."""
    import shutil
    import subprocess

    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("needs bash to run the installer's own comparison")
    text = _text(MACOS)
    function = re.search(r"^version_at_least\(\) \{.*?^\}", text, re.M | re.S).group(0)
    assert function in _text(LINUX), "the two Unix installers must compare alike"
    rc = subprocess.run([bash, "-c", f"{function}\nversion_at_least {have} {want}"]).returncode
    assert (rc == 0) is kept


@pytest.mark.parametrize("path", ALL, ids=lambda p: p.name)
def test_every_installer_uses_python_3_10_by_default(path):
    """As installer 1.x and BIDS Manager's installer. MEGnet installs on it:
    megnet-neuro's ``Requires-Python: >3.10`` admits 3.10.1 and later (an
    offline resolution for "3.10" assumes 3.10.0 and wrongly says no)."""
    pattern = (r"^\$PythonVersion = Setting 'MEEGQC_PYTHON' '([^']+)'" if path == WINDOWS
               else r'^PYTHON_VERSION="\$\{MEEGQC_PYTHON:-([^}]+)\}"')
    assert re.search(pattern, _text(path), re.M).group(1) == "3.10"


def test_the_mac_app_starts_natively_on_apple_silicon():
    """A script launcher can be started under Rosetta, and every program
    Python started would then run as Intel."""
    text = _text(MACOS)
    assert "<key>LSRequiresNativeExecution</key>  <true/>" in text
    assert "<array><string>arm64</string></array>" in text
    assert "${ARCH_KEYS}" in text


@pytest.mark.parametrize("path", ALL, ids=lambda p: p.name)
def test_every_launcher_puts_the_environments_programs_first(path):
    """As installer 1.x did by activating the environment."""
    text = _text(path)
    if path == WINDOWS:
        assert "('set \"PATH=' + (Batch-Path (Join-Path $EnvDir 'Scripts')) + ';%PATH%\"')" in text
    else:
        assert 'export PATH="$ENV_DIR/bin:\\$PATH"' in text


@pytest.mark.parametrize("path", ALL, ids=lambda p: p.name)
def test_every_launcher_starts_the_gui_entry_point(path):
    """``meegqc`` is the GUI's console script (pyproject.toml)."""
    text = _text(path)
    pyproject = _text(ROOT / "pyproject.toml")
    assert re.search(r'^meegqc = "meg_qc\.miscellaneous\.GUI\.megqcGUI:run_megqc_gui"',
                     pyproject, re.M)
    if path == WINDOWS:
        assert "'Scripts\\meegqc.exe'" in text
    else:
        assert 'exec "$ENV_DIR/bin/meegqc" "\\$@"' in text


@pytest.mark.parametrize("path", ALL, ids=lambda p: p.name)
def test_only_a_python_the_update_button_moved_to_is_kept_newer(path):
    """Keeping any newer Python would mean an installation could never
    return to the default. Only the update button's mark keeps one, and the
    installer carries the mark over when it rewrites the record."""
    text = _text(path)
    assert "moved_by_update" in text
    assert "MQ_KEEP_MOVE" in text
    assert 'record["python"]["moved_by_update"] = True' in text


def test_the_update_helper_marks_a_move_the_installers_look_for():
    helper = _text(ROOT / "meg_qc" / "miscellaneous" / "GUI" / "_update_helper.py")
    assert 'record["python"]["moved_by_update"] = True' in helper


@pytest.mark.parametrize("path", ALL, ids=lambda p: p.name)
def test_every_installer_lists_plugins_with_the_helpers_own_code(path):
    """An environment built again must get its plugins back, and the
    installers and the update button must agree on what a plugin is and
    how to install it again. One piece of code says it, copied verbatim."""
    from meg_qc.miscellaneous.GUI._update_helper import PLUGIN_SPECS_CODE

    text = _text(path).replace("\r\n", "\n")
    assert PLUGIN_SPECS_CODE in text
    # ... listed only for an update that replaces the environment, and
    # installed again after MEEGqc, with editable installs kept editable.
    if path == WINDOWS:
        assert "if ($Mode -eq 'update' -and $envPython) {" in text
        assert "foreach ($spec in $pluginSpecs) {" in text
        assert "$spec.StartsWith('-e ')" in text
    else:
        assert 'if [[ "$MODE" == "update" && -n "$env_python" ]]; then' in text
        assert 'done < "$TMP/plugins.txt"' in text
        assert 'if [[ "$spec" == "-e "* ]]; then args=(-e "${spec#-e }")' in text


@pytest.mark.parametrize("path", ALL, ids=lambda p: p.name)
def test_every_installer_offers_to_uninstall_and_asks_first(path):
    text = _text(path)
    assert "[x] Uninstall: remove MEEGqc from this computer" in text
    assert "MEEGQC_UNINSTALL" in text
    # The confirmation defaults to No, so MEEGQC_YES=1 (take every
    # default) can never uninstall anything.
    if path == WINDOWS:
        assert "$confirm = Ask '  Remove it? [y/N]' 'n'" in text
    else:
        assert 'confirm="$(ask "  Remove it? [y/N] " n)"' in text


@pytest.mark.parametrize("path", ALL, ids=lambda p: p.name)
def test_uninstalling_removes_every_launcher_the_installer_creates(path):
    """Whatever starts MEEGqc must not outlive it: a launcher left behind
    points at a folder that is gone."""
    text = _text(path)
    block = text[text.index("Uninstall: everything this installer"):]
    if path == MACOS:
        for removed in ('rm -rf "$APP_BUNDLE"', "-u \"$APP_BUNDLE\"",
                        'rm -f "$DESKTOP_DIR/Uninstall_MEEGqc.command"'):
            assert removed in block, removed
    elif path == LINUX:
        for removed in ('"$APPS_DIR" "$DESKTOP_DIR" "$HOME/Desktop"', "MEEGqc.desktop",
                        "Uninstall_MEEGqc.desktop", 'rm -f "$ICON_FILE"'):
            assert removed in block, removed
    else:
        for removed in ("'MEEGqc.lnk'", "'Uninstall MEEGqc.lnk'",
                        "Remove-Item -LiteralPath $StartMenu -Recurse"):
            assert removed in block, removed


@pytest.mark.parametrize("path", ALL, ids=lambda p: p.name)
def test_every_banner_is_one_box(path):
    lines = [line.replace("Write-Host '", "").rstrip("'\r")
             for line in _text(path).splitlines()
             if re.match(r"(Write-Host ')?    [|+][- ]", line)]
    assert len(lines) == 8, lines
    assert len({len(line) for line in lines}) == 1, lines


def _owns_function(path: Path) -> str:
    return re.search(r'^owns\(\) \{.*\}$', _text(path), re.M).group(0)


@pytest.mark.parametrize("path", UNIX, ids=lambda p: p.name)
@pytest.mark.parametrize(
    ("launcher", "owned"),
    [
        # installer 2.0: the app's launcher and the Linux menu entry
        ('exec "/home/u/MEEGqc/env/bin/meegqc" "$@"', True),
        ('Exec="/home/u/MEEGqc/run_MEEGqc.sh"', True),
        # installer 1.0.0: the app's launcher, the menu entry, the uninstaller
        ('source "/home/u/MEEGqc/env/bin/activate"', True),
        ('Exec=bash -c "/home/u/MEEGqc/run_MEEGqc.sh"', True),
        ('rm -rf "/home/u/MEEGqc"', True),
        # another installation, and one whose folder merely starts alike
        ('exec "/tmp/test/MEEGqc/env/bin/meegqc" "$@"', False),
        ('exec "/home/u/MEEGqc2/env/bin/meegqc" "$@"', False),
        ('rm -rf "/home/u/MEEGqc-old"', False),
    ],
)
def test_a_launcher_is_this_installations_only_when_it_names_its_folder(
        path, launcher, owned, tmp_path):
    """Uninstalling a test installation elsewhere removed the real one's
    MEEGqc.app and Desktop uninstaller (2026-11-16, also true of BIDS
    Manager's installer 2.0.3): the launchers were removed by name, whoever
    they belonged to."""
    import shutil
    import subprocess

    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("needs bash to run the installer's own check")
    file = tmp_path / "launcher"
    file.write_text(launcher + "\n")
    script = f'INSTALL_DIR="/home/u/MEEGqc"\n{_owns_function(path)}\nowns "{file}"'
    rc = subprocess.run([bash, "-c", script]).returncode
    assert (rc == 0) is owned
    missing = f'INSTALL_DIR="/home/u/MEEGqc"\n{_owns_function(path)}\nowns "{tmp_path}/none"'
    assert subprocess.run([bash, "-c", missing]).returncode != 0


@pytest.mark.parametrize("path", ALL, ids=lambda p: p.name)
def test_every_uninstall_path_removes_only_its_own_launchers(path):
    """The installer's Uninstall and the uninstaller it leaves in the
    install folder (and on the Desktop) both check before they remove."""
    text = _text(path).replace("\r\n", "\n")
    installer_block = text[text.index("Uninstall: everything this installer"):
                           text.index('if [[ "$MODE" == "reinstall" ]]' if path != WINDOWS
                                      else "if ($Mode -eq 'reinstall')")]
    if path == MACOS:
        generated = text[text.index('cat > "$UNINSTALL_SCRIPT"'):text.index("UNINSTALLER_EOF\nchmod")]
        for block in (installer_block, generated):
            guarded = re.search(r'if owns "\$APP_BUNDLE/Contents/MacOS/MEEGqc"; then\n(.*?)\n\s*fi\n',
                                block, re.S).group(1)
            assert 'rm -rf "$APP_BUNDLE"' in guarded and '-u "$APP_BUNDLE"' in guarded
            assert re.search(r'if owns "\$DESKTOP_DIR/Uninstall_MEEGqc.command"; then\n'
                             r'\s*rm -f "\$DESKTOP_DIR/Uninstall_MEEGqc.command"', block)
            assert block.count("rm -rf \"$APP_BUNDLE\"") == 1
        assert "owns() {" in generated
    elif path == LINUX:
        generated = text[text.index('cat > "$UNINSTALL_SCRIPT"'):text.index("UNINSTALLER_EOF\nchmod")]
        for block in (installer_block, generated):
            assert re.search(r'if owns "\$APPS_DIR/MEEGqc.desktop"; then\n\s*rm -f "\$ICON_FILE"',
                             block)
            assert re.search(r'if owns "\\?\$entry"; then rm -f "\\?\$entry"; fi', block)
            assert not re.search(r'rm -f "\$(APPS_DIR|DESKTOP_DIR|dir)/', block), block
        assert "owns() {" in generated
    else:
        assert "if (Owns $link) { Remove-Item -LiteralPath $link" in installer_block
        assert "-not (Get-ChildItem -LiteralPath $StartMenu -Force)" in installer_block
        generated = text[text.index("[IO.File]::WriteAllText($UninstallBat"):
                         text.index("if ($NoShortcuts)")]
        assert "del /q" not in generated and "rd /s /q \"' + (Batch-Path $StartMenu)" not in generated
        assert "('set \"MEEGQC_REMOVING=' + (Batch-Path $InstallDir) + '\"')" in generated
        assert "$RemoveLinks" in generated
        remover = text[text.index("$RemoveLinks = @'"):text.index("'@.Trim()")]
        assert "TargetPath.StartsWith($mine, [StringComparison]::OrdinalIgnoreCase)" in remover
        assert "-not (Get-ChildItem -LiteralPath $menu -Force)" in remover
        # It is one batch line: anything cmd would read as an operator must
        # sit inside its one pair of double quotes.
        line = remover.split("\n", 1)[1].strip()
        assert line.count('"') == 2 and line.endswith('"')
        assert "%" not in line


@pytest.mark.parametrize("path", UNIX, ids=lambda p: p.name)
def test_the_owns_check_is_the_same_in_the_installer_and_its_uninstaller(path):
    text = _text(path)
    installer = _owns_function(path)
    generated = re.search(r'^owns\(\) \{ \[\[ -f "\\\$1".*\}$', text, re.M).group(0)
    assert generated.replace("\\$1", "$1") == installer



@pytest.mark.parametrize("path", ALL, ids=lambda p: p.name)
def test_no_installer_tells_anyone_to_use_administrator_rights(path):
    """Nothing MEEGqc needs requires them. A library the window needs and the
    system lacks (libxcb-cursor on Linux) MEEGqc fetches itself at its first
    start, without root (megqcGUI._ensure_xcb_cursor), as BIDS Manager does."""
    text = _text(path)
    assert not re.search(r"\bsudo\b|\bdnf install\b|apt install", text)
    gui = _text(ROOT / "meg_qc" / "miscellaneous" / "GUI" / "megqcGUI.py")
    assert '["apt-get", "download", "libxcb-cursor0"]' in gui
