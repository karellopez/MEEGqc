"""What the update prompt says and does when a release needs a newer Python.

A release the environment's Python cannot run is never offered: the update
would report success, change nothing, and the prompt would come back at
every start. An installer installation is told the update moves it to the
newer Python; any other installation is offered only what it can run, is
left in peace at startup, and is told why on "Check updates".
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytestmark = pytest.mark.gui
pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QApplication, QMessageBox, QWidget  # noqa: E402

from meg_qc.miscellaneous.GUI import update_widgets, updater  # noqa: E402

BLOCKED = updater.Latest(version="1.2.0", newest="1.3.0", needs_python="3.13")


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def window(qapp):
    widget = QWidget()
    yield widget
    widget.deleteLater()


@pytest.fixture
def prompts(monkeypatch):
    shown = []
    monkeypatch.setattr(update_widgets, "_prompt_and_launch_update",
                        lambda parent, current, plan: shown.append(plan))
    return shown


def _installer_installation(monkeypatch, present: bool):
    found = updater.Installation(manifest=None, uv=None) if present else None
    monkeypatch.setattr(updater, "find_installation", lambda: found)


def test_startup_offers_an_installer_installation_the_move(window, prompts, monkeypatch):
    _installer_installation(monkeypatch, True)
    monkeypatch.setattr(updater, "installed_version", lambda: "1.1.2")
    update_widgets._on_startup_check_done(window, BLOCKED)
    assert prompts == [updater.UpdatePlan(version="1.3.0", move_to_python="3.13")]


def test_startup_offers_any_other_installation_what_it_can_run(window, prompts, monkeypatch):
    _installer_installation(monkeypatch, False)
    monkeypatch.setattr(updater, "installed_version", lambda: "1.1.2")
    update_widgets._on_startup_check_done(window, BLOCKED)
    assert prompts == [updater.UpdatePlan(version="1.2.0")]


def test_startup_stays_quiet_when_nothing_can_be_installed(window, prompts, monkeypatch):
    _installer_installation(monkeypatch, False)
    monkeypatch.setattr(updater, "installed_version", lambda: "1.2.0")
    update_widgets._on_startup_check_done(window, BLOCKED)
    assert prompts == []


def test_startup_stays_quiet_when_pypi_cannot_be_reached(window, prompts):
    update_widgets._on_startup_check_done(window, None)
    assert prompts == []


def _checker_answering(latest):
    class Checker:
        def __init__(self, parent):
            self.finished = self

        def connect(self, slot):
            self.slot = slot

        def start(self):
            self.slot(latest)

    return Checker


def test_check_updates_explains_why_the_newest_release_is_not_offered(window, monkeypatch):
    _installer_installation(monkeypatch, False)
    monkeypatch.setattr(updater, "installed_version", lambda: "1.2.0")
    told = []
    monkeypatch.setattr(QMessageBox, "information",
                        lambda parent, title, text: told.append(text))
    monkeypatch.setattr(update_widgets, "_UpdateChecker", _checker_answering(BLOCKED))
    update_widgets.check_for_updates_interactive(window)
    assert len(told) == 1
    assert "MEEGqc 1.3.0 needs Python 3.13" in told[0]
    assert "You have the newest release this installation can run (1.2.0)" in told[0]


def test_check_updates_says_up_to_date(window, monkeypatch):
    _installer_installation(monkeypatch, False)
    monkeypatch.setattr(updater, "installed_version", lambda: "1.2.0")
    told = []
    monkeypatch.setattr(QMessageBox, "information",
                        lambda parent, title, text: told.append(text))
    monkeypatch.setattr(update_widgets, "_UpdateChecker",
                        _checker_answering(updater.Latest("1.2.0", "1.2.0")))
    update_widgets.check_for_updates_interactive(window)
    assert told == ["You are up to date.\n\nInstalled: 1.2.0\nPyPI: 1.2.0"]


def _answer(monkeypatch, *buttons):
    """Click ``buttons`` in order, one per time the prompt is shown."""
    texts, queue = [], list(buttons)

    def fake_exec(box):
        texts.append(box.text())
        wanted = queue.pop(0)
        box.chosen = next(b for b in box.buttons() if b.text() == wanted)
        return 0

    monkeypatch.setattr(QMessageBox, "exec", fake_exec)
    monkeypatch.setattr(QMessageBox, "clickedButton", lambda box: box.chosen)
    return texts


def test_the_prompt_says_the_update_moves_python_and_yes_asks_for_the_move(window, monkeypatch):
    launched = []
    texts = _answer(monkeypatch, "Yes")
    monkeypatch.setattr(update_widgets, "_launch_helper_and_quit",
                        lambda parent, move: launched.append(move))
    update_widgets._prompt_and_launch_update(
        window, "1.1.2", updater.UpdatePlan(version="1.3.0", move_to_python="3.13"))
    assert "needs Python 3.13 or newer, so the update also moves this installation" in texts[0]
    assert "plugins included" in texts[0]
    assert "PyPI:        1.3.0" in texts[0]
    assert launched == ["3.13"]


def test_an_ordinary_update_says_nothing_about_python(window, monkeypatch):
    launched = []
    texts = _answer(monkeypatch, "Yes")
    monkeypatch.setattr(update_widgets, "_launch_helper_and_quit",
                        lambda parent, move: launched.append(move))
    update_widgets._prompt_and_launch_update(window, "1.1.2", updater.UpdatePlan("1.2.0"))
    assert "Python" not in texts[0]
    assert launched == [""]


def test_what_changed_opens_the_notes_and_asks_again(window, monkeypatch):
    opened, launched = [], []
    texts = _answer(monkeypatch, "What changed", "No")
    monkeypatch.setattr(update_widgets.QDesktopServices, "openUrl",
                        lambda url: opened.append(url.toString()))
    monkeypatch.setattr(update_widgets, "_launch_helper_and_quit",
                        lambda parent, move: launched.append(move))
    update_widgets._prompt_and_launch_update(window, "1.1.2", updater.UpdatePlan("1.2.0"))
    assert opened == ["https://ancplaboldenburg.github.io/megqc_documentation/updates.html#v120"]
    assert len(texts) == 2, "the question is asked again after the notes open"
    assert launched == []


def test_no_is_the_default_so_a_stray_enter_never_updates(window, monkeypatch):
    defaults = []

    def fake_exec(box):
        defaults.append(box.defaultButton().text())
        box.chosen = box.defaultButton()
        return 0

    monkeypatch.setattr(QMessageBox, "exec", fake_exec)
    monkeypatch.setattr(QMessageBox, "clickedButton", lambda box: box.chosen)
    monkeypatch.setattr(update_widgets, "_launch_helper_and_quit",
                        lambda parent, move: pytest.fail("No must not update"))
    update_widgets._prompt_and_launch_update(window, "1.1.2", updater.UpdatePlan("1.2.0"))
    assert defaults == ["No"]
