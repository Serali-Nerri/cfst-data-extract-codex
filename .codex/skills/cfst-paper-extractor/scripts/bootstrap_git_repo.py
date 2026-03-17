#!/usr/bin/env python3
"""Initialize a git repository for worktree-based CFST batch execution."""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path


def run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=str(cwd), check=False, text=True, capture_output=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Bootstrap a git repository for CFST batch runs.")
    parser.add_argument("--repo-root", type=Path, required=True, help="Repository root to initialize.")
    parser.add_argument(
        "--initial-empty-commit",
        action="store_true",
        help="Create an initial empty commit so git worktree can be used immediately.",
    )
    parser.add_argument(
        "--commit-message",
        default="bootstrap: initialize CFST extractor workspace",
        help="Commit message used with --initial-empty-commit.",
    )
    args = parser.parse_args()

    repo_root = args.repo_root.resolve()
    repo_root.mkdir(parents=True, exist_ok=True)

    status = run(["git", "rev-parse", "--is-inside-work-tree"], repo_root)
    if status.returncode == 0 and status.stdout.strip() == "true":
        print(f"[INFO] Already a git repository: {repo_root}")
    else:
        init_proc = run(["git", "init", "-b", "main"], repo_root)
        if init_proc.returncode != 0:
            print(init_proc.stderr.strip() or init_proc.stdout.strip())
            return 1
        print(f"[OK] Initialized git repository: {repo_root}")

    if args.initial_empty_commit:
        rev_proc = run(["git", "rev-parse", "--verify", "HEAD"], repo_root)
        if rev_proc.returncode == 0:
            print("[INFO] HEAD already exists. Skipping empty commit.")
        else:
            commit_proc = run(["git", "commit", "--allow-empty", "-m", args.commit_message], repo_root)
            if commit_proc.returncode != 0:
                print(commit_proc.stderr.strip() or commit_proc.stdout.strip())
                return 1
            print("[OK] Created initial empty commit.")
    else:
        print("[INFO] No initial commit created. `git worktree add` will still need a HEAD commit.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
