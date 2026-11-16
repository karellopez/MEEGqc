"""The memory guard: on by default, can be switched off, and how permissive it
is comes from named levels or three numbers, the same in the GUI and the CLI.

The laptop numbers below are the ones measured on a 32 GB MacBook with 16 GB
available, processing ds_meg_example_1 (four FIF recordings of 0.50-0.72 GB):
the old fixed 32 GB reserve left nothing, so every request ran one recording
at a time.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys

import pytest

import meg_qc.calculation.resource_budget as rb

GB = 1024 ** 3
LAPTOP_TOTAL = 32 * GB
LAPTOP_AVAILABLE = 16 * GB
# ds_meg_example_1, largest first.
RECORDINGS = {"a": int(0.72 * GB), "b": int(0.68 * GB),
              "c": int(0.55 * GB), "d": int(0.50 * GB)}


@pytest.fixture
def laptop(monkeypatch):
    """Pretend to be the 32 GB laptop, with the four recordings' sizes."""
    monkeypatch.setattr(rb, "memory_total_bytes", lambda: LAPTOP_TOTAL)
    monkeypatch.setattr(rb, "memory_available_bytes", lambda: LAPTOP_AVAILABLE)
    monkeypatch.setattr(rb, "recording_size_bytes", lambda p: RECORDINGS[p])
    return list(RECORDINGS)


# ── levels ───────────────────────────────────────────────────────────────

def test_levels_are_ordered_from_strict_to_permissive():
    assert list(rb.GUARD_LEVELS) == ["cautious", "balanced", "permissive"]
    assert rb.DEFAULT_GUARD_LEVEL == "balanced"
    c, b, p = (rb.GUARD_LEVELS[k] for k in rb.GUARD_LEVELS)
    assert c.mem_fraction < b.mem_fraction < p.mem_fraction
    assert c.reserve_share > b.reserve_share > p.reserve_share
    assert c.expansion >= b.expansion >= p.expansion


@pytest.mark.parametrize("total_gb, reserve", [(16, 2.0), (32, 4.0), (64, 8.0),
                                                (256, 32.0), (896, 32.0)])
def test_balanced_reserve_scales_with_the_machine(total_gb, reserve):
    """12.5% of total RAM, never more than 32 GB."""
    assert rb.level_values("balanced", total_gb * GB) == (0.5, reserve, 4.0)


def test_balanced_is_the_old_guard_on_a_large_machine():
    """From 256 GB up the default is exactly what the guard always did."""
    assert rb.level_values("balanced", 896 * GB) == (0.5, 32.0, 4.0)


def test_unknown_total_ram_keeps_the_cap(monkeypatch):
    monkeypatch.setattr(rb, "memory_total_bytes", lambda: None)
    assert rb.default_reserve_gb() == 32.0


# ── switching it off ─────────────────────────────────────────────────────

def test_off_returns_the_request_and_says_so(laptop):
    jobs, note = rb.effective_jobs(4, laptop, enabled=False)
    assert jobs == 4
    assert note.startswith("memory guard off")


def test_off_keeps_all_cores_as_minus_one(laptop):
    jobs, note = rb.effective_jobs(-1, laptop, enabled=False)
    assert jobs == -1 and "memory guard off" in note


def test_one_job_needs_no_decision(laptop):
    assert rb.effective_jobs(1, laptop) == (1, None)
    assert rb.effective_jobs(1, laptop, enabled=False) == (1, None)


# ── how permissive ───────────────────────────────────────────────────────

def test_old_fixed_reserve_ran_one_at_a_time_on_the_laptop(laptop):
    jobs, note = rb.effective_jobs(4, laptop, mem_reserve_gb=32.0)
    assert jobs == 1 and "reduced to 1" in note


def test_default_now_runs_two_on_the_laptop(laptop):
    """(16 - 4 GB) x 0.5 = 6 GB: room for 0.72 and 0.68 GB at 4x, not a third."""
    jobs, note = rb.effective_jobs(4, laptop)
    assert jobs == 2
    assert "reduced to 2" in note and "reserve 4.0 GB" in note


@pytest.mark.parametrize("level, expected", [("cautious", 1), ("balanced", 2),
                                             ("permissive", 4)])
def test_each_level_on_the_laptop(laptop, level, expected):
    fraction, reserve, expansion = rb.level_values(level)
    jobs, _ = rb.effective_jobs(4, laptop, mem_fraction=fraction,
                                mem_reserve_gb=reserve, expansion=expansion)
    assert jobs == expected


def test_note_when_everything_fits(laptop):
    jobs, note = rb.effective_jobs(4, laptop, mem_fraction=1.0, mem_reserve_gb=0.0)
    assert jobs == 4
    assert note.startswith("memory guard: all 4 recording(s) fit")


def test_note_when_the_request_fits_but_not_everything(laptop):
    """The default level has room for 2 of the 4 here; asking for 2 is fine."""
    jobs, note = rb.effective_jobs(2, laptop)
    assert jobs == 2
    assert note.startswith("memory guard: the 2 requested worker(s) fit")


def test_usable_bytes_is_never_negative():
    avail, usable = rb.usable_bytes(mem_fraction=0.5, mem_reserve_gb=32.0,
                                    available=16 * GB)
    assert avail == 16 * GB and usable == 0.0
    _, usable = rb.usable_bytes(mem_fraction=0.5, mem_reserve_gb=4.0,
                                available=16 * GB)
    assert usable == 6 * GB


# ── the CLI ──────────────────────────────────────────────────────────────

def _ns(**kw):
    base = dict(memory_guard="balanced", mem_fraction=None, mem_reserve_gb=None,
                assume_expansion=None)
    base.update(kw)
    return argparse.Namespace(**base)


def test_cli_level_sets_all_three_numbers(laptop):
    from meg_qc.test import _memory_guard_kwargs
    assert _memory_guard_kwargs(_ns(memory_guard="cautious")) == {
        "memory_guard": True, "mem_fraction": 0.35, "mem_reserve_gb": 8.0,
        "assume_expansion": 6.0}


def test_cli_explicit_numbers_override_the_level(laptop):
    from meg_qc.test import _memory_guard_kwargs
    kw = _memory_guard_kwargs(_ns(memory_guard="permissive", mem_reserve_gb=10.0))
    assert kw == {"memory_guard": True, "mem_fraction": 0.8,
                  "mem_reserve_gb": 10.0, "assume_expansion": 4.0}


def test_cli_off_switches_the_guard_off(laptop):
    from meg_qc.test import _memory_guard_kwargs
    assert _memory_guard_kwargs(_ns(memory_guard="off"))["memory_guard"] is False


@pytest.mark.parametrize("run_all", [False, True])
def test_cli_passes_the_guard_to_the_dispatcher(monkeypatch, laptop, run_all):
    """Through the real argument parser. --run-all used to crash here: it passed
    the guard numbers to run_all_dispatch, which did not accept them."""
    import meg_qc.test as cli
    seen = {}

    def fake_dispatch(**kwargs):
        seen.update(kwargs)

    monkeypatch.setattr(cli, "run_all_dispatch" if run_all else "run_calculation_dispatch",
                        fake_dispatch)
    argv = ["run-meegqc", "--inputdata", "/data/ds", "--n_jobs", "4",
            "--memory-guard", "permissive", "--assume-expansion", "5"]
    if run_all:
        argv.append("--run-all")
    monkeypatch.setattr(sys, "argv", argv)
    cli.run_megqc()
    assert (seen["memory_guard"], seen["mem_fraction"], seen["mem_reserve_gb"],
            seen["assume_expansion"]) == (True, 0.8, 2.0, 5.0)


def test_cli_rejects_an_unknown_level(monkeypatch):
    import meg_qc.test as cli
    monkeypatch.setattr(sys, "argv", ["run-meegqc", "--inputdata", "/d",
                                      "--memory-guard", "reckless"])
    with pytest.raises(SystemExit):
        cli.run_megqc()


# ── the dispatchers reach the pipeline ───────────────────────────────────

def test_run_all_dispatch_forwards_the_guard(monkeypatch):
    import meg_qc.test as cli
    seen = {}
    monkeypatch.setattr(cli, "run_calculation_dispatch", lambda **kw: seen.update(kw))
    monkeypatch.setattr(cli, "run_plotting_dispatch", lambda **kw: None)
    cli.run_all_dispatch(["/d"], "c.ini", "i.ini", memory_guard=False,
                         mem_fraction=0.7, mem_reserve_gb=3.0, assume_expansion=6.0,
                         logger=lambda *_: None)
    assert (seen["memory_guard"], seen["mem_fraction"], seen["mem_reserve_gb"],
            seen["assume_expansion"]) == (False, 0.7, 3.0, 6.0)


def test_run_calculation_dispatch_forwards_the_guard(monkeypatch):
    import meg_qc.calculation.meg_qc_pipeline as pipe
    import meg_qc.test as cli
    seen = {}
    monkeypatch.setattr(pipe, "make_derivative_meg_qc", lambda **kw: seen.update(kw))
    cli.run_calculation_dispatch(["/d"], "c.ini", "i.ini", memory_guard=False,
                                 mem_fraction=0.7, mem_reserve_gb=3.0,
                                 assume_expansion=6.0, logger=lambda *_: None)
    assert (seen["memory_guard"], seen["mem_fraction"], seen["mem_reserve_gb"],
            seen["assume_expansion"]) == (False, 0.7, 3.0, 6.0)


def test_pipeline_defaults_to_the_guard_on_with_the_machine_reserve():
    import inspect
    import meg_qc.calculation.meg_qc_pipeline as pipe
    params = inspect.signature(pipe.make_derivative_meg_qc).parameters
    assert params["memory_guard"].default is True
    assert params["mem_reserve_gb"].default is None


# ── the GUI's worker process takes named arguments ───────────────────────

def test_worker_entry_passes_positional_and_named_arguments():
    payload = json.dumps({"args": ["a", "b"], "kwargs": {"sep": "|"}})
    out = subprocess.run(
        [sys.executable, "-m", "meg_qc.miscellaneous.GUI.worker_entry",
         "--func", "builtins:print", "--args", payload],
        capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "a|b"


def test_worker_entry_rejects_a_bare_list():
    from meg_qc.miscellaneous.GUI.worker_entry import _decode_args
    with pytest.raises(ValueError):
        _decode_args(json.dumps(["a"]))
