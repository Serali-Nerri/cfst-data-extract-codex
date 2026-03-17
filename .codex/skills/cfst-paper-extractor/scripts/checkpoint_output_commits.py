#!/usr/bin/env python3
"""Commit/push output checkpoints for batch single-paper extraction."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path


def _run(cmd: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        check=False,
        text=True,
        capture_output=True,
    )


def _repo_root(cwd: Path) -> Path | None:
    proc = _run(["git", "-C", str(cwd), "rev-parse", "--show-toplevel"])
    if proc.returncode != 0:
        return None
    return Path(proc.stdout.strip()).resolve()


def _fail(msg: str, code: int = 1) -> int:
    print(f"[FAIL] {msg}")
    return code


def _staged_files(repo_root: Path) -> list[str]:
    proc = _run(["git", "-C", str(repo_root), "diff", "--cached", "--name-only"])
    if proc.returncode != 0:
        return []
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]


def _current_branch(repo_root: Path) -> str | None:
    proc = _run(["git", "-C", str(repo_root), "rev-parse", "--abbrev-ref", "HEAD"])
    if proc.returncode != 0:
        return None
    branch = proc.stdout.strip()
    if not branch or branch == "HEAD":
        return None
    return branch


def _check_remote(repo_root: Path, remote: str) -> bool:
    proc = _run(["git", "-C", str(repo_root), "remote", "get-url", remote])
    return proc.returncode == 0


def _only_output_files(paths: list[str], output_dir: str) -> tuple[bool, list[str]]:
    clean = output_dir.strip("/").replace("\\", "/")
    bad = []
    for path in paths:
        norm = path.replace("\\", "/")
        if norm == clean or norm.startswith(f"{clean}/"):
            continue
        bad.append(path)
    return (len(bad) == 0), bad


def main() -> int:
    parser = argparse.ArgumentParser(description="Checkpoint output-only commits and periodic push.")
    parser.add_argument("--processed-count", required=True, type=int, help="Total processed papers so far.")
    parser.add_argument("--commit-every", type=int, default=10, help="Commit interval.")
    parser.add_argument("--push-every", type=int, default=20, help="Push interval.")
    parser.add_argument(
        "--output-dir",
        default="output",
        help="Final published output directory path in repo (default: output).",
    )
    parser.add_argument("--remote", default="origin", help="Remote name for push.")
    parser.add_argument("--branch", default=None, help="Branch name for push (defaults to current branch).")
    parser.add_argument(
        "--message-template",
        default="cfst-output: processed {count} papers",
        help="Commit message template. Use {count} placeholder.",
    )
    args = parser.parse_args()

    if args.processed_count <= 0:
        return _fail("--processed-count must be > 0.")
    if args.commit_every <= 0 or args.push_every <= 0:
        return _fail("--commit-every and --push-every must be > 0.")

    cwd = Path.cwd()
    repo_root = _repo_root(cwd)
    if not repo_root:
        return _fail(
            "Current directory is not a git repository. Initialize git first and retry.",
            code=2,
        )

    commit_due = args.processed_count % args.commit_every == 0
    push_due = args.processed_count % args.push_every == 0

    summary = {
        "repo_root": str(repo_root),
        "processed_count": args.processed_count,
        "commit_due": commit_due,
        "push_due": push_due,
        "commit_done": False,
        "push_done": False,
    }

    if commit_due:
        add_proc = _run(["git", "-C", str(repo_root), "add", "--", args.output_dir])
        if add_proc.returncode != 0:
            return _fail(add_proc.stderr.strip() or add_proc.stdout.strip())

        staged = _staged_files(repo_root)
        if not staged:
            summary["commit_skipped"] = "no staged output changes"
        else:
            ok, bad = _only_output_files(staged, args.output_dir)
            if not ok:
                return _fail(
                    "Staged changes include non-output files: "
                    + ", ".join(sorted(bad))
                    + ". Unstage them before checkpoint commit."
                )

            message = args.message_template.format(count=args.processed_count)
            commit_proc = _run(
                ["git", "-C", str(repo_root), "commit", "-m", message],
            )
            if commit_proc.returncode != 0:
                return _fail(commit_proc.stderr.strip() or commit_proc.stdout.strip())
            summary["commit_done"] = True
            summary["commit_message"] = message

    if push_due:
        branch = args.branch or _current_branch(repo_root)
        if not branch:
            return _fail("Cannot determine branch for push. Pass --branch explicitly.")
        if not _check_remote(repo_root, args.remote):
            return _fail(f"Remote not found: {args.remote}")

        push_proc = _run(["git", "-C", str(repo_root), "push", args.remote, branch])
        if push_proc.returncode != 0:
            return _fail(push_proc.stderr.strip() or push_proc.stdout.strip())
        summary["push_done"] = True
        summary["remote"] = args.remote
        summary["branch"] = branch

    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
