#!/usr/bin/env python3
"""Run a worker command in a strict bubblewrap filesystem sandbox.

The sandbox only exposes:
- one paper directory (read-only)
- one output directory (read-write)
- skill policy paths (read-only): SKILL.md, references/, scripts/
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path, PurePosixPath


SYSTEM_RO_PATHS = (
    "/usr",
    "/bin",
    "/sbin",
    "/lib",
    "/lib64",
    "/etc",
    "/opt",
)


def _fail(message: str, code: int = 1) -> int:
    print(f"[FAIL] {message}", file=sys.stderr)
    return code


def _resolve_base_path(cwd: Path, raw_path: str, label: str) -> Path:
    base = Path(raw_path)
    abs_path = (cwd / base).resolve() if not base.is_absolute() else base.resolve()
    if not abs_path.exists():
        raise ValueError(f"{label} path does not exist: {abs_path}")
    return abs_path


def _resolve_under(base_dir: Path, raw_rel: str, label: str) -> tuple[Path, str]:
    raw = Path(raw_rel)
    if raw.is_absolute():
        raise ValueError(f"{label} must be a relative path under worktree: {raw_rel}")
    abs_path = (base_dir / raw).resolve()
    try:
        rel = abs_path.relative_to(base_dir).as_posix()
    except ValueError as exc:
        raise ValueError(f"{label} escapes worktree: {raw_rel}") from exc
    return abs_path, rel


def _resolve_mount_relpath(raw_rel: str, label: str) -> str:
    raw = PurePosixPath(raw_rel.replace("\\", "/"))
    if raw.is_absolute():
        raise ValueError(f"{label} must be a relative workspace path: {raw_rel}")

    cleaned_parts = [part for part in raw.parts if part not in ("", ".")]
    if any(part == ".." for part in cleaned_parts):
        raise ValueError(f"{label} escapes workspace: {raw_rel}")
    if not cleaned_parts:
        return "."
    return PurePosixPath(*cleaned_parts).as_posix()


def _resolve_host_path(cwd: Path, raw_path: str, label: str) -> Path:
    host_path = Path(raw_path)
    return (cwd / host_path).resolve() if not host_path.is_absolute() else host_path.resolve()


def _workspace_dirs_for(rel_path: str) -> list[str]:
    rel = PurePosixPath(rel_path)
    if rel == PurePosixPath("."):
        return ["/workspace"]
    dirs = ["/workspace"]
    current = PurePosixPath("/workspace")
    for part in rel.parts:
        current = current / part
        dirs.append(str(current))
    return dirs


def _unique_sorted_dirs(paths: set[str]) -> list[str]:
    return sorted(paths, key=lambda p: (p.count("/"), p))


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run one worker command in a strict bubblewrap sandbox."
    )
    parser.add_argument("--worktree-path", required=True, help="Worker worktree path.")
    parser.add_argument(
        "--paper-dir-relpath",
        required=True,
        help="Paper path (file or directory) relative to worktree root.",
    )
    parser.add_argument(
        "--skill-dir-relpath",
        default=".codex/skills/cfst-paper-extractor",
        help="Skill folder path relative to worktree root.",
    )
    parser.add_argument(
        "--output-dir",
        default="output",
        help="Output directory path mounted writable at the same relative path under /workspace.",
    )
    parser.add_argument(
        "--host-output-dir",
        default=None,
        help="Optional host path to bind writable at /workspace/<output-dir>. Use to persist outputs outside the worktree.",
    )
    parser.add_argument(
        "--cwd-mode",
        choices=("workspace", "paper"),
        default="workspace",
        help="Sandbox working directory for worker command.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=None,
        help="Optional wall-clock timeout for the worker command.",
    )
    parser.add_argument(
        "worker_cmd",
        nargs=argparse.REMAINDER,
        help="Command to run in sandbox. Pass after --, e.g. -- python run.py",
    )
    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    bwrap_bin = shutil.which("bwrap")
    if not bwrap_bin:
        return _fail("bubblewrap (bwrap) not found in PATH.")

    cwd = Path.cwd()
    try:
        worktree_path = _resolve_base_path(cwd, args.worktree_path, "Worktree")
    except ValueError as exc:
        return _fail(str(exc))
    if not worktree_path.is_dir():
        return _fail(f"Worktree path is not a directory: {worktree_path}")

    try:
        paper_abs, paper_rel = _resolve_under(worktree_path, args.paper_dir_relpath, "Paper dir")
        skill_abs, skill_rel = _resolve_under(worktree_path, args.skill_dir_relpath, "Skill dir")
    except ValueError as exc:
        return _fail(str(exc))

    try:
        if args.host_output_dir:
            output_abs = _resolve_host_path(cwd, args.host_output_dir, "Host output dir")
            output_rel = _resolve_mount_relpath(args.output_dir, "Output dir")
        else:
            output_abs, output_rel = _resolve_under(worktree_path, args.output_dir, "Output dir")
    except ValueError as exc:
        return _fail(str(exc))

    if not paper_abs.exists():
        return _fail(f"Paper path not found: {paper_abs}")
    if not skill_abs.is_dir():
        return _fail(f"Skill directory not found: {skill_abs}")

    skill_file = skill_abs / "SKILL.md"
    references_dir = skill_abs / "references"
    scripts_dir = skill_abs / "scripts"
    if not skill_file.is_file():
        return _fail(f"Missing skill file: {skill_file}")
    if not references_dir.is_dir():
        return _fail(f"Missing references directory: {references_dir}")
    if not scripts_dir.is_dir():
        return _fail(f"Missing scripts directory: {scripts_dir}")

    output_abs.mkdir(parents=True, exist_ok=True)

    worker_cmd = list(args.worker_cmd)
    if worker_cmd and worker_cmd[0] == "--":
        worker_cmd = worker_cmd[1:]
    if not worker_cmd:
        return _fail("Missing worker command. Use -- <command> <args...>")

    paper_dst = f"/workspace/{paper_rel}"
    output_dst = f"/workspace/{output_rel}"
    skill_base_dst = f"/workspace/{skill_rel}"
    skill_file_dst = f"{skill_base_dst}/SKILL.md"
    references_dst = f"{skill_base_dst}/references"
    scripts_dst = f"{skill_base_dst}/scripts"

    mkdir_targets = set()
    # For file inputs, only create parent directories before the bind mount.
    paper_workspace_rel = (
        str(PurePosixPath(paper_rel).parent) if paper_abs.is_file() else paper_rel
    )
    mkdir_targets.update(_workspace_dirs_for(paper_workspace_rel))
    mkdir_targets.update(_workspace_dirs_for(output_rel))
    mkdir_targets.update(_workspace_dirs_for(skill_rel))

    sandbox_cwd = "/workspace" if args.cwd_mode == "workspace" else paper_dst

    cmd: list[str] = [
        bwrap_bin,
        "--die-with-parent",
        "--new-session",
        "--unshare-net",
        "--proc",
        "/proc",
        "--dev",
        "/dev",
        "--tmpfs",
        "/tmp",
        "--setenv",
        "CFST_SANDBOX",
        "1",
        "--setenv",
        "HOME",
        "/tmp",
    ]

    for host_path in SYSTEM_RO_PATHS:
        if Path(host_path).exists():
            cmd.extend(["--ro-bind", host_path, host_path])

    for dst in _unique_sorted_dirs(mkdir_targets):
        cmd.extend(["--dir", dst])

    # Keep source paper inputs immutable; only the declared output directory is writable.
    cmd.extend(["--ro-bind", str(paper_abs), paper_dst])
    cmd.extend(["--bind", str(output_abs), output_dst])
    cmd.extend(["--ro-bind", str(skill_file), skill_file_dst])
    cmd.extend(["--ro-bind", str(references_dir), references_dst])
    cmd.extend(["--ro-bind", str(scripts_dir), scripts_dst])
    cmd.extend(["--chdir", sandbox_cwd])
    cmd.extend(worker_cmd)

    try:
        proc = subprocess.run(cmd, check=False, timeout=args.timeout_seconds)
        return proc.returncode
    except subprocess.TimeoutExpired:
        return _fail(
            f"Worker command exceeded timeout ({args.timeout_seconds}s).",
            code=124,
        )


if __name__ == "__main__":
    raise SystemExit(main())
