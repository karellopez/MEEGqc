"""Bound MEEGqc's parallelism by memory as well as by cores: the memory guard.

``--n_jobs`` alone says nothing about RAM. Every recording processed
concurrently is held in memory (MNE loads with ``preload=True`` and keeps
filtered/resampled copies), so a large ``n_jobs`` on big recordings will
exhaust the machine and be killed by the OOM killer with no useful message.
The guard turns "how many cores" into "how many recordings can we actually
hold", and only ever *reduces* concurrency.

It is on by default and can be switched off (``enabled=False``; GUI checkbox,
CLI ``--memory-guard off``). How permissive it is comes from three numbers:

* the share of the currently AVAILABLE RAM a run may use (``mem_fraction``);
* the RAM always left free (``mem_reserve_gb``);
* the RAM one recording needs, as a multiple of its size on disk
  (``expansion``).

Named levels (``GUARD_LEVELS``: cautious, balanced, permissive) fill in all
three; the GUI and the CLI offer the same levels and let any number be set by
hand. The reserve of a level scales with the machine (a share of TOTAL RAM,
capped), because a fixed 32 GB reserve left nothing at all on a 32 GB laptop
and so ran every recording one after another there.

Everything is cross-platform: memory comes from ``psutil`` (already a
dependency) rather than ``/proc/meminfo``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Sequence, Tuple

# Measured on MEG FIF: a 1.92 GB recording produced a 7.7 GB resident worker,
# i.e. int32 on disk becomes float64 in memory plus filtered/resampled copies.
DEFAULT_EXPANSION = 4.0
DEFAULT_MEM_FRACTION = 0.5

_GB = 1024 ** 3


@dataclass(frozen=True)
class GuardLevel:
    """One named setting of the memory guard.

    The reserve is ``reserve_share`` of the machine's TOTAL RAM, never more
    than ``reserve_cap_gb``: a server keeps the cap, a laptop keeps a share it
    can afford.
    """

    label: str
    mem_fraction: float
    reserve_share: float
    reserve_cap_gb: float
    expansion: float


#: The levels offered by the GUI and by ``--memory-guard``. "balanced" is the
#: default and is the guard as it always was on a machine with 256 GB or more
#: (half the available RAM, 32 GB kept free, 4x the file size). Cautious
#: assumes 6x because small recordings were measured at 7-8x (the fixed cost of
#: Python and MNE in every worker weighs more on a small file).
GUARD_LEVELS: Dict[str, GuardLevel] = {
    "cautious": GuardLevel("Cautious", 0.35, 0.25, 64.0, 6.0),
    "balanced": GuardLevel("Balanced", DEFAULT_MEM_FRACTION, 0.125, 32.0,
                           DEFAULT_EXPANSION),
    "permissive": GuardLevel("Permissive", 0.80, 0.0625, 16.0,
                             DEFAULT_EXPANSION),
}
DEFAULT_GUARD_LEVEL = "balanced"


def level_values(level: str, total_bytes: Optional[int] = None
                 ) -> Tuple[float, float, float]:
    """``(mem_fraction, mem_reserve_gb, expansion)`` of a level on this machine.

    ``total_bytes`` is the machine's total RAM (measured when omitted). The
    reserve is rounded to 0.1 GB so the GUI shows the same number it uses.
    """
    lv = GUARD_LEVELS[level]
    total = memory_total_bytes() if total_bytes is None else total_bytes
    if total is None:
        reserve = lv.reserve_cap_gb
    else:
        reserve = min(lv.reserve_cap_gb, lv.reserve_share * total / _GB)
    return lv.mem_fraction, round(reserve, 1), lv.expansion


def default_reserve_gb(total_bytes: Optional[int] = None) -> float:
    """The default level's reserve on this machine, in GB."""
    return level_values(DEFAULT_GUARD_LEVEL, total_bytes)[1]


# ── size measurement ────────────────────────────────────────────────────────
def recording_size_bytes(path) -> int:
    """On-disk bytes MNE will actually read for one recording.

    ``Path.stat().st_size`` is wrong for most of what MEEGqc reads:

    * a CTF recording is a **directory** - ``st_size`` reports the directory
      inode (a few hundred bytes) for a multi-GB recording;
    * in a datalad/git-annex dataset the data files are symlinks, so the size
      must be taken after dereferencing (``stat`` does this, ``lstat`` does not);
    * a FIF recording may be split across ``_meg.fif``, ``_meg-1.fif``, ... and
      MNE loads the whole chain;
    * an EEGLAB ``.set`` keeps its samples in a sibling ``.fdt``.

    Returns 0 when the path cannot be measured; callers treat that as "unknown"
    rather than "free", so an unmeasurable file never inflates the budget.
    """
    p = Path(path)
    try:
        if p.is_dir():
            total = 0
            for child in p.rglob("*"):
                try:
                    if child.is_file():
                        total += child.stat().st_size
                except OSError:
                    continue
            return total

        total = p.stat().st_size
        name = p.name
        if name.endswith(".fif"):
            stem = name[:-4]
            for sibling in p.parent.glob(f"{stem}-[0-9]*.fif"):
                try:
                    total += sibling.stat().st_size
                except OSError:
                    continue
        elif name.endswith(".set"):
            fdt = p.with_suffix(".fdt")
            try:
                if fdt.exists():
                    total += fdt.stat().st_size
            except OSError:
                pass
        return total
    except OSError:
        return 0


# ── memory probe ────────────────────────────────────────────────────────────
def memory_available_bytes() -> Optional[int]:
    """Currently available RAM, or None when it cannot be determined.

    Uses ``psutil`` so Linux, macOS and Windows behave identically. "Available"
    rather than "free" matters: on this machine free was 164 GB while available
    was 896 GB, the difference being reclaimable page cache. Sizing against
    "free" would throttle to nothing on an otherwise idle machine.
    """
    try:
        import psutil
        return int(psutil.virtual_memory().available)
    except Exception:
        return None


def memory_total_bytes() -> Optional[int]:
    """The machine's total RAM, or None when it cannot be determined."""
    try:
        import psutil
        return int(psutil.virtual_memory().total)
    except Exception:
        return None


def usable_bytes(
    *,
    mem_fraction: float = DEFAULT_MEM_FRACTION,
    mem_reserve_gb: Optional[float] = None,
    available: Optional[int] = None,
) -> Tuple[Optional[int], Optional[float]]:
    """``(available, usable)`` in bytes: what the guard may hand to recordings.

    ``usable`` is ``(available - reserve) * mem_fraction``, never negative.
    ``mem_reserve_gb=None`` means the default level's reserve on this machine.
    Both are None when the available memory cannot be measured.
    """
    avail = memory_available_bytes() if available is None else available
    if avail is None:
        return None, None
    reserve = default_reserve_gb() if mem_reserve_gb is None else mem_reserve_gb
    return avail, max(0.0, avail - reserve * _GB) * float(mem_fraction)


# ── the budget ──────────────────────────────────────────────────────────────
def memory_budget(
    candidates: Sequence,
    *,
    mem_fraction: float = DEFAULT_MEM_FRACTION,
    mem_reserve_gb: Optional[float] = None,
    expansion: float = DEFAULT_EXPANSION,
    sizes: Optional[Sequence[int]] = None,
) -> Tuple[int, str]:
    """How many of ``candidates`` can be held in RAM at once, right now.

    Packs greedily from the largest recording down, rather than assuming the
    mean: a single subject can mix a 517 MB and a 1 MB recording, and a
    mean-based estimate is optimistic exactly when it matters.

    ``mem_reserve_gb=None`` means the default level's reserve on this machine.

    Returns ``(count, explanation)``. ``count`` is never below 1 - the guard
    limits concurrency, it never refuses to make progress.
    """
    n = len(candidates)
    if n == 0:
        return 1, "no candidates"

    if mem_reserve_gb is None:
        mem_reserve_gb = default_reserve_gb()
    avail, usable = usable_bytes(mem_fraction=mem_fraction,
                                 mem_reserve_gb=mem_reserve_gb)
    if avail is None:
        return n, "memory unknown (psutil unavailable) - not limiting"

    if sizes is None:
        sizes = [recording_size_bytes(c) for c in candidates]
    known = sorted((s for s in sizes if s > 0), reverse=True)
    if not known:
        return n, "sizes unknown - not limiting"

    # Unmeasurable files are charged the median known size rather than zero.
    median = known[len(known) // 2]
    full = sorted((s if s > 0 else median for s in sizes), reverse=True)

    total = 0.0
    take = 0
    for s in full:
        cost = s * float(expansion)
        if take >= 1 and total + cost > usable:
            break
        total += cost
        take += 1

    take = max(1, min(take, n))
    return take, (
        f"{avail / _GB:.1f} GB available, reserve {mem_reserve_gb:.1f} GB, "
        f"fraction {mem_fraction:.2f}, largest {full[0] / _GB:.2f} GB "
        f"x{expansion:.1f} -> {take} of {n}"
    )


def effective_jobs(
    requested_jobs: int,
    candidates: Sequence,
    *,
    enabled: bool = True,
    mem_fraction: float = DEFAULT_MEM_FRACTION,
    mem_reserve_gb: Optional[float] = None,
    expansion: float = DEFAULT_EXPANSION,
) -> Tuple[int, Optional[str]]:
    """Clamp a requested worker count to what memory allows.

    ``enabled=False`` switches the guard off: the request is returned as it is.
    ``mem_reserve_gb=None`` means the default level's reserve on this machine.

    Returns ``(jobs, note)``. ``note`` says what the guard decided, so a run's
    log shows it whether or not it reduced anything; it is None only when no
    parallelism was requested (``n_jobs`` 0 or 1), where there is nothing to
    decide.
    """
    if requested_jobs in (0, 1):
        return requested_jobs, None
    cpu = os.cpu_count() or 1
    want = cpu if requested_jobs < 0 else requested_jobs   # -1 means "all cores"

    if not enabled:
        return requested_jobs, (
            f"memory guard off: {want} worker(s) as requested, whatever the "
            f"memory")

    allowed, why = memory_budget(
        candidates, mem_fraction=mem_fraction,
        mem_reserve_gb=mem_reserve_gb, expansion=expansion)
    # memory_budget caps at len(candidates); when everything fits, memory was
    # never the constraint and the guard should not claim credit for a
    # reduction that simply reflects how many recordings exist.
    if allowed >= len(candidates):
        return requested_jobs, (
            f"memory guard: all {len(candidates)} recording(s) fit in memory "
            f"at once ({why})")
    if allowed >= want:
        return requested_jobs, (
            f"memory guard: the {want} requested worker(s) fit in memory ({why})")
    return allowed, (
        f"n_jobs {want} reduced to {allowed} by the memory guard ({why})")


