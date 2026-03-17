#!/usr/bin/env python3
"""Create and clean isolated git worktrees for per-paper worker agents."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path


def _run(
    cmd: list[str],
    cwd: Path | None = None,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        check=False,
        text=True,
        capture_output=True,
        input=input_text,
    )


def _fail(message: str, code: int = 1) -> int:
    print(f"[FAIL] {message}")
    return code


def _repo_root(cwd: Path) -> Path | None:
    proc = _run(["git", "-C", str(cwd), "rev-parse", "--show-toplevel"])
    if proc.returncode != 0:
        return None
    return Path(proc.stdout.strip()).resolve()


def _sanitize_slug(raw: str, max_len: int = 48) -> str:
    citation_match = re.search(r"\[(A\d+-\d+)\]", raw)
    if citation_match:
        return citation_match.group(1)

    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", raw.strip())
    cleaned = cleaned.strip("-_.")
    if len(cleaned) > max_len:
        cleaned = cleaned[:max_len].rstrip("-_.")
    return cleaned or "paper"


def _copy_tree(src: Path, dst: Path) -> None:
    if not src.exists():
        raise FileNotFoundError(f"Source path does not exist: {src}")
    if dst.exists():
        if dst.is_dir():
            shutil.rmtree(dst)
        else:
            dst.unlink()
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.is_dir():
        shutil.copytree(src, dst)
    else:
        shutil.copy2(src, dst)


def _delete_path(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink()


def _resolve_repo_relative(repo_root: Path, raw_path: str) -> tuple[Path, str]:
    raw = Path(raw_path)
    abs_path = (repo_root / raw).resolve() if not raw.is_absolute() else raw.resolve()
    try:
        rel = abs_path.relative_to(repo_root).as_posix()
    except ValueError as exc:
        raise ValueError(f"Path must be under repository root: {abs_path}") from exc
    return abs_path, rel


def _resolve_repo_path(repo_root: Path, raw_path: str, label: str) -> tuple[Path, str]:
    raw = Path(raw_path)
    if raw.is_absolute():
        abs_path = raw.resolve()
    else:
        abs_path = (repo_root / raw).resolve()
    try:
        rel = abs_path.relative_to(repo_root).as_posix()
    except ValueError as exc:
        raise ValueError(f"{label} must be under repository root: {abs_path}") from exc
    return abs_path, rel


def _is_under(base: Path, candidate: Path) -> bool:
    try:
        candidate.resolve().relative_to(base.resolve())
        return True
    except ValueError:
        return False


def _build_sandbox_paths(
    wt_path: Path,
    paper_rel: str,
    skill_rel: str,
    output_host_path: Path,
) -> tuple[list[str], list[str], str]:
    paper_path = (wt_path / paper_rel).resolve()
    skill_root = (wt_path / skill_rel).resolve()

    allowed_rw = [
        str(output_host_path.resolve()),
    ]
    allowed_ro = [
        str(paper_path),
        str(skill_root / "SKILL.md"),
        str(skill_root / "references"),
        str(skill_root / "scripts"),
    ]
    entry_cwd = str(paper_path)
    return allowed_rw, allowed_ro, entry_cwd


def _prune_worktree_payload(wt_path: Path, keep_relpaths: list[str]) -> None:
    targets = [Path(rel) for rel in keep_relpaths]

    def recurse(current: Path, rel_current: Path, relevant_targets: list[Path]) -> None:
        for child in list(current.iterdir()):
            child_rel = Path(child.name) if rel_current == Path(".") else rel_current / child.name
            child_targets = [
                target
                for target in relevant_targets
                if len(target.parts) >= len(child_rel.parts)
                and target.parts[: len(child_rel.parts)] == child_rel.parts
            ]
            if not child_targets:
                _delete_path(child)
                continue
            if any(target == child_rel for target in child_targets):
                continue
            if child.is_dir():
                recurse(child, child_rel, child_targets)
            else:
                _delete_path(child)

    recurse(wt_path, Path("."), targets)


def _create(args: argparse.Namespace) -> int:
    cwd = Path.cwd()
    repo_root = _repo_root(cwd)
    if not repo_root:
        return _fail(
            "Current directory is not a git repository. Run "
            "`python .codex/skills/cfst-paper-extractor/scripts/bootstrap_git_repo.py --repo-root . --initial-empty-commit` "
            "first, then retry.",
            code=2,
        )

    try:
        paper_abs, paper_rel = _resolve_repo_relative(repo_root, args.paper_dir)
        skill_abs, skill_rel = _resolve_repo_relative(repo_root, args.skill_dir)
        output_abs, output_rel = _resolve_repo_path(repo_root, args.output_dir, "Output dir")
    except ValueError as exc:
        return _fail(str(exc))

    if not paper_abs.exists():
        return _fail(f"Paper path not found: {paper_abs}")
    if not skill_abs.is_dir():
        return _fail(f"Skill folder not found: {skill_abs}")

    wt_root = (repo_root / args.worktrees_root).resolve()
    if _is_under(skill_abs, wt_root):
        return _fail(
            "Worktrees root must not be inside the skill directory, or skill copying will recurse."
        )
    if _is_under(paper_abs, wt_root):
        return _fail(
            "Worktrees root must not be inside the source paper directory."
        )
    wt_root.mkdir(parents=True, exist_ok=True)

    slug = _sanitize_slug(Path(paper_rel).name)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    suffix = f"{stamp}-{Path.cwd().name}-{os.getpid()}"
    branch = f"{args.branch_prefix}/{slug}-{suffix}"
    wt_path = wt_root / f"{slug}-{suffix}"

    add_proc = _run(
        [
            "git",
            "-C",
            str(repo_root),
            "worktree",
            "add",
            "-b",
            branch,
            str(wt_path),
            args.base_ref,
        ]
    )
    if add_proc.returncode != 0:
        return _fail(add_proc.stderr.strip() or add_proc.stdout.strip())

    try:
        _copy_tree(paper_abs, wt_path / paper_rel)
        _copy_tree(skill_abs, wt_path / skill_rel)
        _prune_worktree_payload(
            wt_path,
            keep_relpaths=[
                ".git",
                paper_rel,
                skill_rel,
            ],
        )
        output_abs.mkdir(parents=True, exist_ok=True)
    except Exception as exc:  # noqa: BLE001
        _run(["git", "-C", str(repo_root), "worktree", "remove", "--force", str(wt_path)])
        _run(["git", "-C", str(repo_root), "branch", "-D", branch])
        return _fail(f"Failed to prepare worktree payload: {exc}")

    sandbox_allowed_rw, sandbox_allowed_ro, sandbox_entry_cwd = _build_sandbox_paths(
        wt_path=wt_path,
        paper_rel=paper_rel,
        skill_rel=skill_rel,
        output_host_path=output_abs,
    )

    result = {
        "repo_root": str(repo_root),
        "paper_rel": paper_rel,
        "skill_rel": skill_rel,
        "worktree_path": str(wt_path),
        "branch": branch,
        "output_dir": output_rel,
        "output_host_path": str(output_abs),
        "sandbox_allowed_rw": sandbox_allowed_rw,
        "sandbox_allowed_ro": sandbox_allowed_ro,
        "sandbox_entry_cwd": sandbox_entry_cwd,
    }
    print(json.dumps(result, ensure_ascii=False))
    return 0


def _worktree_branch(repo_root: Path, worktree_path: Path) -> tuple[str | None, bool]:
    proc = _run(
        [
            "git",
            "-C",
            str(repo_root),
            "worktree",
            "list",
            "--porcelain",
        ]
    )
    if proc.returncode != 0:
        return None, False

    target_path = worktree_path.resolve()
    branch: str | None = None
    current_path: str | None = None
    matched = False
    for line in proc.stdout.splitlines():
        if line.startswith("worktree "):
            if matched:
                break
            current_path = line.removeprefix("worktree ").strip()
            branch = None
            continue
        if current_path and Path(current_path).resolve() == target_path:
            matched = True
        if matched and line.startswith("branch "):
            branch = line.removeprefix("branch ").strip()
            return branch.removeprefix("refs/heads/"), True
    return branch, matched


def _remove(args: argparse.Namespace) -> int:
    cwd = Path.cwd()
    repo_root = _repo_root(cwd)
    if not repo_root:
        return _fail(
            "Current directory is not a git repository. Run "
            "`python .codex/skills/cfst-paper-extractor/scripts/bootstrap_git_repo.py --repo-root . --initial-empty-commit` "
            "first, then retry.",
            code=2,
        )

    raw_wt = Path(args.worktree_path)
    wt_path = (repo_root / raw_wt).resolve() if not raw_wt.is_absolute() else raw_wt.resolve()
    detected_branch, tracked = _worktree_branch(repo_root, wt_path)
    branch = args.branch or detected_branch

    pruned_missing_worktree = False
    if wt_path.exists():
        rm_proc = _run(
            [
                "git",
                "-C",
                str(repo_root),
                "worktree",
                "remove",
                "--force",
                str(wt_path),
            ]
        )
        if rm_proc.returncode != 0:
            return _fail(rm_proc.stderr.strip() or rm_proc.stdout.strip())
    else:
        if not tracked:
            return _fail(f"Worktree path does not exist: {wt_path}")

        prune_proc = _run(["git", "-C", str(repo_root), "worktree", "prune"])
        if prune_proc.returncode != 0:
            return _fail(prune_proc.stderr.strip() or prune_proc.stdout.strip())

        _, still_tracked = _worktree_branch(repo_root, wt_path)
        if still_tracked:
            return _fail(
                "Worktree path does not exist and git still tracks it after prune: "
                f"{wt_path}"
            )
        pruned_missing_worktree = True

    deleted_branch = False
    if args.delete_branch and branch:
        del_proc = _run(["git", "-C", str(repo_root), "branch", "-D", branch])
        if del_proc.returncode == 0:
            deleted_branch = True

    result = {
        "repo_root": str(repo_root),
        "worktree_path": str(wt_path),
        "branch": branch,
        "pruned_missing_worktree": pruned_missing_worktree,
        "deleted_branch": deleted_branch,
    }
    print(json.dumps(result, ensure_ascii=False))
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage isolated git worktrees for CFST workers.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    create = sub.add_parser("create", help="Create one isolated worktree for one paper.")
    create.add_argument("--paper-dir", required=True, help="Paper path under repository root.")
    create.add_argument(
        "--skill-dir",
        default=".codex/skills/cfst-paper-extractor",
        help="Skill folder path under repository root.",
    )
    create.add_argument(
        "--worktrees-root",
        default="tmp/cfst-worktrees",
        help="Where to create per-paper worktrees.",
    )
    create.add_argument(
        "--branch-prefix",
        default="cfst-worker",
        help="Branch prefix for worker worktrees.",
    )
    create.add_argument("--base-ref", default="HEAD", help="Base git ref for worktree creation.")
    create.add_argument(
        "--output-dir",
        default="output",
        help="Persistent worker-local output directory under repository root (default: output).",
    )

    remove = sub.add_parser("remove", help="Remove one isolated worktree.")
    remove.add_argument("--worktree-path", required=True, help="Worktree path (absolute or repo-relative).")
    remove.add_argument("--branch", default=None, help="Optional branch name to delete.")
    remove.add_argument(
        "--delete-branch",
        action="store_true",
        help="Delete branch after worktree removal.",
    )

    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    if args.cmd == "create":
        return _create(args)
    if args.cmd == "remove":
        return _remove(args)
    return _fail(f"Unsupported command: {args.cmd}")


if __name__ == "__main__":
    raise SystemExit(main())
