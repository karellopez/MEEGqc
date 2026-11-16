"""The Plugins dialog: see, install and remove MEEGqc plugins.

Installing and removing run ``python -m meg_qc.plugins.cli`` in a QProcess, so
the window stays responsive and the installer's own output is shown as it
comes. No restart is needed: every calculation starts fresh worker processes,
which find the plugins installed at that moment, and this window re-reads the
list when an installation ends.
"""
from __future__ import annotations

import importlib
import sys
from typing import Callable, List, Optional

from PyQt6.QtCore import QProcess, QProcessEnvironment, Qt
from PyQt6.QtGui import QColor, QFontDatabase, QPalette
from PyQt6.QtWidgets import (
    QDialog,
    QFileDialog,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from meg_qc.plugins import registry
from meg_qc.plugins.catalog import CATALOG


class _Card:
    """What one card shows: an installed plugin, or one MEEGqc can install."""

    def __init__(self, name, label, summary, *, version="", package="", error="",
                 settings_section="", installed=False):
        self.name, self.label, self.summary = name, label, summary
        self.version, self.package, self.error = version, package, error
        self.settings_section, self.installed = settings_section, installed


def _cards() -> List[_Card]:
    cards = []
    names = set()
    for found in registry.discover(refresh=True):
        p = found.plugin
        if p is not None:
            cards.append(_Card(p.name, p.label, p.summary, version=p.version,
                               package=found.package, settings_section=p.settings_section or "",
                               installed=True))
        else:
            cards.append(_Card(found.entry_point, found.entry_point, "", package=found.package,
                               version=found.package_version, error=found.error, installed=True))
        names |= {found.name, found.package}
    for entry in CATALOG:
        if entry.name not in names and entry.package not in names:
            cards.append(_Card(entry.name, entry.label, entry.summary, package=entry.package))
    return cards


class _WrappedLabel(QLabel):
    """Word-wrapped text whose height follows its width. Inside a scroll area
    Qt sizes a wrapped label for a narrower width than it gets, so the text was
    drawn in the middle of a box several lines too tall."""

    def __init__(self, text: str = ""):
        super().__init__(text)
        self.setWordWrap(True)
        self.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        height = self.heightForWidth(self.width())
        if height > 0 and height != self.height():
            self.setFixedHeight(height)


def _muted(label: QLabel) -> QLabel:
    """Secondary text: the theme's text colour two thirds of the way from its
    background, readable in light and dark themes alike (the palette's
    placeholder colour is too faint on dark backgrounds)."""
    pal = label.palette()
    fg = pal.color(QPalette.ColorRole.WindowText)
    bg = pal.color(QPalette.ColorRole.Window)
    mix = QColor(round(fg.red() * 0.66 + bg.red() * 0.34),
                 round(fg.green() * 0.66 + bg.green() * 0.34),
                 round(fg.blue() * 0.66 + bg.blue() * 0.34))
    pal.setColor(QPalette.ColorRole.WindowText, mix)
    label.setPalette(pal)
    return label


class PluginsDialog(QDialog):
    """See, install and remove plugins."""

    def __init__(self, parent=None, can_change: Optional[Callable[[str], bool]] = None):
        super().__init__(parent)
        self.setWindowTitle("Plugins")
        self.resize(640, 560)
        self.setMinimumSize(420, 360)
        # Asked before installing or removing: the main window refuses while a
        # calculation runs (removing a plugin a running calculation uses would
        # break it).
        self._can_change = can_change or (lambda _action: True)
        self._process: Optional[QProcess] = None
        self._action_label = ""

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        intro = QLabel(
            "Plugins add optional features to MEEGqc. They install into the Python "
            "environment MEEGqc runs from, and new runs use them at once.")
        intro.setWordWrap(True)
        root.addWidget(intro)
        where = _muted(QLabel(sys.executable))
        where.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        where.setToolTip("The Python environment plugins are installed into.")
        root.addWidget(where)

        self._cards_box = QWidget()
        self._cards_layout = QVBoxLayout(self._cards_box)
        self._cards_layout.setContentsMargins(0, 0, 0, 0)
        self._cards_layout.setSpacing(10)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(self._cards_box)
        root.addWidget(scroll, stretch=3)

        other = QGroupBox("Install another plugin")
        other_lay = QHBoxLayout(other)
        self.spec_edit = QLineEdit()
        self.spec_edit.setPlaceholderText("Package name, folder, wheel file or URL")
        self.spec_edit.setToolTip(
            "Anything pip installs: a package name from PyPI, a folder with the "
            "plugin's source, a .whl file, or a git URL.")
        self.spec_edit.returnPressed.connect(self._install_other)
        btn_browse = QPushButton("Browse...")
        btn_browse.setToolTip("Choose a plugin's source folder.")
        btn_browse.clicked.connect(self._browse)
        self.btn_install_other = QPushButton("Install")
        self.btn_install_other.clicked.connect(self._install_other)
        other_lay.addWidget(self.spec_edit, stretch=1)
        other_lay.addWidget(btn_browse)
        other_lay.addWidget(self.btn_install_other)
        root.addWidget(other)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        root.addWidget(self.status)

        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.output.setPlaceholderText("Installer output appears here.")
        self.output.setVisible(False)
        root.addWidget(self.output, stretch=2)

        bottom = QHBoxLayout()
        self.btn_output = QPushButton("Show output")
        self.btn_output.setCheckable(True)
        self.btn_output.toggled.connect(self._toggle_output)
        bottom.addWidget(self.btn_output)
        bottom.addStretch(1)
        self.btn_close = QPushButton("Close")
        self.btn_close.clicked.connect(self.close)
        bottom.addWidget(self.btn_close)
        root.addLayout(bottom)

        self._action_buttons: List[QPushButton] = [self.btn_install_other]
        self._refresh()

    # ── cards ─────────────────────────────────────────────────────────────
    def _refresh(self) -> None:
        importlib.invalidate_caches()
        while self._cards_layout.count():
            item = self._cards_layout.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        self._action_buttons = [self.btn_install_other]
        cards = _cards()
        for card in cards:
            self._cards_layout.addWidget(self._card_widget(card))
        if not cards:
            self._cards_layout.addWidget(_muted(QLabel("No plugins are installed or known.")))
        self._cards_layout.addStretch(1)
        self._set_busy(self._process is not None)

    def _card_widget(self, card: _Card) -> QGroupBox:
        box = QGroupBox(card.label)
        lay = QVBoxLayout(box)
        lay.setSpacing(6)

        if card.error:
            state = f"Installed (package {card.package} {card.version}) but not loaded: {card.error}"
        elif card.installed:
            state = f"Installed, version {card.version}"
        else:
            state = f"Not installed (package {card.package})"
        state_label = _WrappedLabel(state) if card.error else QLabel(state)
        if not card.installed:
            _muted(state_label)
        lay.addWidget(state_label)

        if card.summary:
            lay.addWidget(_WrappedLabel(card.summary))
        if card.settings_section:
            hint = _muted(QLabel(f"Settings: the [{card.settings_section}] section "
                                 "(Global settings...)."))
            lay.addWidget(hint)

        row = QHBoxLayout()
        row.addStretch(1)
        if card.installed:
            btn = QPushButton("Remove")
            btn.setToolTip(f"Uninstall {card.package} from this environment.")
            btn.clicked.connect(lambda _=False, c=card: self._remove(c))
        else:
            btn = QPushButton("Install")
            btn.setToolTip(f"Install {card.package} into this environment.")
            btn.clicked.connect(lambda _=False, c=card: self._install(c.name, c.label))
        row.addWidget(btn)
        self._action_buttons.append(btn)
        lay.addLayout(row)
        return box

    # ── actions ───────────────────────────────────────────────────────────
    def _browse(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Plugin source folder")
        if folder:
            self.spec_edit.setText(folder)

    def _install_other(self) -> None:
        spec = self.spec_edit.text().strip()
        if spec:
            self._install(spec, spec)

    def _install(self, spec: str, label: str) -> None:
        if not self._can_change("Install plugin"):
            return
        self._run(["install", spec], f"Installing {label}")

    def _remove(self, card: _Card) -> None:
        if not self._can_change("Remove plugin"):
            return
        answer = QMessageBox.question(
            self, "Remove plugin",
            f"Remove {card.label} (package {card.package})?\n\n"
            "Its section stays in your settings files and is ignored until it is "
            "installed again.")
        if answer == QMessageBox.StandardButton.Yes:
            self._run(["remove", card.package or card.name], f"Removing {card.label}")

    def _run(self, args: List[str], what: str) -> None:
        if self._process is not None:
            return
        self._action_label = what
        self.output.clear()
        self.status.setText(f"{what}... (this can take a few minutes)")
        proc = QProcess(self)
        proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        env = QProcessEnvironment.systemEnvironment()
        env.insert("PYTHONIOENCODING", "utf-8")   # pip's and our output, any user name
        env.insert("PYTHONUTF8", "1")
        proc.setProcessEnvironment(env)
        proc.readyReadStandardOutput.connect(self._read_output)
        proc.finished.connect(self._finished)
        proc.errorOccurred.connect(self._failed_to_start)
        self._process = proc
        self._set_busy(True)
        proc.start(sys.executable, ["-m", "meg_qc.plugins.cli", *args])

    def _read_output(self) -> None:
        if self._process is None:
            return
        text = bytes(self._process.readAllStandardOutput()).decode("utf-8", "replace")
        self.output.appendPlainText(text.rstrip("\n"))

    def _failed_to_start(self, error) -> None:
        # finished() is not emitted when the program never started.
        if self._process is None or error != QProcess.ProcessError.FailedToStart:
            return
        self._process.deleteLater()
        self._process = None
        self.status.setText(f"{self._action_label} could not start: {sys.executable} "
                            "did not run.")
        self._set_busy(False)

    def _finished(self, code: int, _status) -> None:
        self._read_output()
        self._process.deleteLater()
        self._process = None
        if code == 0:
            self.status.setText(f"{self._action_label}: done. New runs use the change.")
        else:
            self.status.setText(f"{self._action_label} failed (exit code {code}). "
                                "The output says why.")
            self.btn_output.setChecked(True)
        self._refresh()

    def _set_busy(self, busy: bool) -> None:
        for btn in self._action_buttons:
            btn.setEnabled(not busy)
        self.btn_close.setEnabled(not busy)

    def _toggle_output(self, shown: bool) -> None:
        self.output.setVisible(shown)
        self.btn_output.setText("Hide output" if shown else "Show output")

    def _busy_message(self) -> None:
        # An interrupted pip leaves a half-installed package behind.
        self.status.setText("Wait for the installer to finish before closing.")

    def closeEvent(self, event) -> None:
        if self._process is not None:
            self._busy_message()
            event.ignore()
            return
        super().closeEvent(event)

    def reject(self) -> None:
        # Esc calls reject(), not closeEvent().
        if self._process is not None:
            self._busy_message()
            return
        super().reject()
