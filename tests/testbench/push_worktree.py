#!/usr/bin/env python3
"""Send THIS working tree to a remote testbench clone over SSH, so the remote
tests exactly what is here (commits not yet pushed included).

    python tests/testbench/push_worktree.py HOST [--windows] [--root PATH]

1. Commits the remote clone lacks travel as a git bundle and are checked out
   there (detached), so nothing has to be pushed to GitHub first.
2. The remote working tree is reset to that commit, then every changed or new
   file here is copied over it and every file deleted here is deleted there.
   VS Code's Source Control on the remote then shows exactly these changes.

Run from the repository. HOST is an ssh alias (e.g. linuxlaptop, winlaptop).
"""
from __future__ import annotations

import argparse
import io
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def git(*args: str) -> str:
    return subprocess.run(["git", "-C", str(REPO), *args], check=True,
                          capture_output=True, text=True).stdout


def ssh(host: str, command: str, data: bytes | None = None) -> str:
    r = subprocess.run(["ssh", host, command], input=data, capture_output=True)
    if r.returncode != 0:
        sys.exit(f"[{host}] failed: {command}\n{r.stderr.decode(errors='replace')}")
    return r.stdout.decode(errors="replace")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("host")
    ap.add_argument("--windows", action="store_true")
    ap.add_argument("--root", default=None)
    a = ap.parse_args()
    root = a.root or ("%USERPROFILE%\\MEEGqc_testbench" if a.windows else "~/MEEGqc_testbench")
    sep = "\\" if a.windows else "/"
    clone = f"{root}{sep}MEEGqc"
    head = git("rev-parse", "HEAD").strip()

    ssh(a.host, f"git -C {clone} fetch -q origin")
    # "Does the remote have this commit?" exits non-zero when it does not.
    have = ssh(a.host, f"git -C {clone} cat-file -t {head}" + (" 2>nul & exit /b 0" if a.windows
                                                               else " 2>/dev/null || true")).strip()
    if have != "commit":
        base = git("merge-base", "HEAD", "origin/main").strip()
        with tempfile.TemporaryDirectory() as tmp:
            bundle = Path(tmp) / "head.bundle"
            subprocess.run(["git", "-C", str(REPO), "bundle", "create", str(bundle),
                            f"{base}..HEAD"], check=True, capture_output=True)
            remote_bundle = f"{root}{sep}head.bundle"
            subprocess.run(["scp", "-q", str(bundle),
                            f"{a.host}:{'MEEGqc_testbench/head.bundle' if a.root is None else remote_bundle}"],
                           check=True)
        ssh(a.host, f"git -C {clone} fetch -q {remote_bundle} HEAD")
    ssh(a.host, f"git -C {clone} checkout -q -f --detach {head}")
    ssh(a.host, f"git -C {clone} reset -q --hard {head}")
    ssh(a.host, f"git -C {clone} clean -q -fd -e tests/_local")

    changed, deleted = [], []
    for line in git("status", "--porcelain", "--untracked-files=all").splitlines():
        code, path = line[:2], line[3:].split(" -> ")[-1].strip('"')
        (deleted if "D" in code else changed).append(path)
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        for p in changed:
            t.add(REPO / p, arcname=p)
    if changed:
        ssh(a.host, f"tar -xzf - -C {clone}", buf.getvalue())
    for p in deleted:
        target = f"{clone}{sep}{p.replace('/', sep)}"
        ssh(a.host, f"del /q {target}" if a.windows else f"rm -f {target}")
    print(f"[{a.host}] {head[:8]} + {len(changed)} changed, {len(deleted)} deleted file(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
