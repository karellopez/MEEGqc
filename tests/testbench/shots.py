"""Screenshots for a test round: the GUI (offscreen) and HTML reports (headless
browser). Run with the Python of the environment under test.

    python shots.py gui OUT_DIR                 # main window, two themes
    python shots.py report REPORT.html OUT.png  # first screens of a report

Preferences go to MEEGQC_SETTINGS_DIR, never the user's own. The browser runs
with a fresh profile, so a report's saved "Plot settings" (kept in the
browser's local storage) never change what is captured.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MEGQC_NO_UPDATE_CHECK", "1")
# Qt's offscreen platform looks for fonts only beside Qt, and PyQt6 ships
# none: on Windows every letter of the GUI screenshots was an empty box.
if os.name == "nt" and os.environ["QT_QPA_PLATFORM"] == "offscreen":
    os.environ.setdefault("QT_QPA_FONTDIR",
                          os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"))

BROWSERS = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "C:/Program Files/Google/Chrome/Application/chrome.exe",
    "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
    "google-chrome", "google-chrome-stable", "chromium", "chromium-browser",
]


def gui(out_dir: str, themes=("Light", "Dark")) -> None:
    if not os.environ.get("MEEGQC_SETTINGS_DIR"):
        sys.exit("set MEEGQC_SETTINGS_DIR so screenshots cannot touch your own preferences")
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from meg_qc.miscellaneous.GUI.megqcGUI import MainWindow
    w = MainWindow()
    w.resize(1280, 1500)
    w.show()
    app.processEvents()
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    calc = w.chk_mem_guard.parentWidget()
    while calc is not None and type(calc).__name__ != "QGroupBox":
        calc = calc.parentWidget()
    for theme in themes:
        if theme not in w.themes:
            continue
        w.apply_theme(theme, persist=False)
        app.processEvents()
        w.grab().save(str(out / f"gui_window_{theme}.png"))
        if calc is not None:
            calc.grab().save(str(out / f"gui_calculation_{theme}.png"))
    w.close()


def browser() -> str:
    for b in BROWSERS:
        path = b if Path(b).is_file() else shutil.which(b)
        if path:
            return path
    sys.exit("no Chrome/Edge/Chromium found for report screenshots")


def report(html: str, png: str, width: int = 1440, height: int = 2400) -> None:
    # Chrome reads a relative --screenshot path from its own folder.
    png = str(Path(png).resolve())
    profile = tempfile.mkdtemp(prefix="meegqc_shot_profile_")
    try:
        # Software WebGL (SwiftShader): with --disable-gpu every plotly
        # scattergl/3-D figure showed "WebGL is not supported" instead of dots.
        # Rendering that way is slow: a CTF dataset QC report needed more than
        # 3 minutes on the Windows laptop, so allow 10.
        subprocess.run([browser(), "--headless=new", "--use-angle=swiftshader",
                        "--enable-unsafe-swiftshader", "--hide-scrollbars",
                        "--no-first-run", "--no-default-browser-check",
                        f"--user-data-dir={profile}", f"--window-size={width},{height}",
                        "--virtual-time-budget=25000", f"--screenshot={png}",
                        Path(html).resolve().as_uri()],
                       capture_output=True, timeout=600)
    except subprocess.TimeoutExpired:
        print("timed out after 600 s:", html, flush=True)
    finally:
        shutil.rmtree(profile, ignore_errors=True)
    print("saved" if Path(png).is_file() else "FAILED", png, flush=True)


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "gui":
        gui(sys.argv[2])
    elif len(sys.argv) >= 4 and sys.argv[1] == "report":
        report(sys.argv[2], sys.argv[3])
    else:
        sys.exit(__doc__)
