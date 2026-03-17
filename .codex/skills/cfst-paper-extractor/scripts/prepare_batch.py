#!/usr/bin/env python3
"""Prepare a CFST batch workspace from processed PDF files."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PAPER_ID_PATTERN = re.compile(r"\[(A\d+-\d+)\]")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def discover_processed_pdfs(processed_root: Path, include_regex: str | None) -> dict[str, Path]:
    pattern = re.compile(include_regex) if include_regex else None
    paper_pdfs: dict[str, Path] = {}
    for item in sorted(processed_root.iterdir()):
        if not item.is_file() or item.suffix.lower() != ".pdf":
            continue
        if pattern and not pattern.search(item.name):
            continue
        match = PAPER_ID_PATTERN.search(item.name)
        if not match:
            continue
        paper_id = match.group(1)
        paper_pdfs[paper_id] = item
    return paper_pdfs


def git_repo_status(cwd: Path) -> dict[str, Any]:
    proc = subprocess.run(
        ["git", "-C", str(cwd), "rev-parse", "--is-inside-work-tree"],
        check=False,
        text=True,
        capture_output=True,
    )
    return {
        "is_git_repo": proc.returncode == 0 and proc.stdout.strip() == "true",
        "stdout": proc.stdout.strip(),
        "stderr": proc.stderr.strip(),
    }


def infer_paper_title_hint(pdf_path: Path) -> str:
    text = pdf_path.stem
    text = re.sub(r"^\[[^\]]+\]\s*", "", text)
    return text.strip()


def build_pdf_metadata(processed_pdfs: dict[str, Path], paper_id: str) -> dict[str, Any]:
    pdf_path = processed_pdfs.get(paper_id)
    return {
        "paper_id": paper_id,
        "citation_tag": f"[{paper_id}]",
        "paper_title_hint": infer_paper_title_hint(pdf_path) if pdf_path else "",
        "expected_specimen_count": None,
    }


def inspect_pdf_layout(pdf_path: Path) -> dict[str, Any]:
    issues: list[str] = []
    total_pages = 0

    if not pdf_path.is_file():
        issues.append("pdf_file_not_found")
    else:
        try:
            proc = subprocess.run(
                ["pdfinfo", str(pdf_path)],
                check=False,
                text=True,
                capture_output=True,
                timeout=30,
            )
            if proc.returncode != 0:
                issues.append("pdfinfo_failed")
            else:
                for line in proc.stdout.splitlines():
                    if line.startswith("Pages:"):
                        total_pages = int(line.split(":", 1)[1].strip())
                        break
                else:
                    issues.append("pdfinfo_no_page_count")
        except subprocess.TimeoutExpired:
            issues.append("pdfinfo_timeout")
        except FileNotFoundError:
            issues.append("pdfinfo_not_available")

    return {
        "ready": not issues,
        "pdf_file": pdf_path.name,
        "total_pages": total_pages,
        "issues": issues,
    }


def paper_pdf_relpath(worktree_root: Path, pdf_path: Path) -> str | None:
    try:
        return pdf_path.resolve().relative_to(worktree_root).as_posix()
    except ValueError:
        return None


def build_worker_job(
    output_root: Path,
    paper_id: str,
    paper_pdf_relpath_value: str | None,
    expected_specimen_count: int | None,
    status: str,
) -> dict[str, Any]:
    tmp_json = output_root / "tmp" / paper_id / f"{paper_id}.json"
    final_json = output_root / "output" / f"{paper_id}.json"
    return {
        "paper_id": paper_id,
        "paper_pdf_relpath": paper_pdf_relpath_value,
        "worker_output_json_path": str(tmp_json),
        "final_output_json_path": str(final_json),
        "expected_specimen_count": expected_specimen_count,
        "status": status,
    }


def selected_paper_ids(processed_pdfs: dict[str, Path], explicit_ids: list[str] | None) -> list[str]:
    ids = explicit_ids or sorted(processed_pdfs.keys())
    return sorted(set(ids), key=lambda value: tuple(int(x) for x in re.findall(r"\d+", value)) or (10**9,))


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare CFST batch workspace from processed PDF files.")
    parser.add_argument(
        "--processed-root",
        type=Path,
        required=True,
        help="Root containing processed PDF files matching [Ax-yy]*.pdf.",
    )
    parser.add_argument(
        "--worktree-root",
        type=Path,
        default=Path("."),
        help="Repository/worktree root used to compute worker paper_pdf_relpath values.",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("output"),
        help="Batch output root (default: output/).",
    )
    parser.add_argument(
        "--include-regex",
        default=r"^\[A\d+-\d+\]",
        help="Regex for processed PDF file discovery.",
    )
    parser.add_argument(
        "--paper-ids",
        nargs="*",
        default=None,
        help="Optional explicit list like A1-1 A1-2.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Do not write files.")
    args = parser.parse_args()

    processed_root = args.processed_root.resolve()
    if not processed_root.exists():
        print(f"[FAIL] Processed root not found: {processed_root}")
        return 1
    if not processed_root.is_dir():
        print(f"[FAIL] Processed root is not a directory: {processed_root}")
        return 1

    worktree_root = args.worktree_root.resolve()
    if not worktree_root.exists() or not worktree_root.is_dir():
        print(f"[FAIL] Worktree root not found or not a directory: {worktree_root}")
        return 1

    output_root = args.output_root.resolve()
    manifests_dir = output_root / "manifests"
    logs_dir = output_root / "logs"
    tmp_dir = output_root / "tmp"
    final_output_dir = output_root / "output"

    processed_pdfs = discover_processed_pdfs(processed_root, args.include_regex)
    selected_ids = selected_paper_ids(processed_pdfs, args.paper_ids)

    batch_entries: list[dict[str, Any]] = []
    worker_jobs: list[dict[str, Any]] = []
    state_entries: list[dict[str, Any]] = []

    for paper_id in selected_ids:
        pdf_metadata = build_pdf_metadata(processed_pdfs, paper_id)
        pdf_path = processed_pdfs.get(paper_id)
        layout = inspect_pdf_layout(pdf_path) if pdf_path else None
        pdf_relpath = paper_pdf_relpath(worktree_root, pdf_path) if pdf_path else None

        status = "missing_processed_data"
        if pdf_path and layout:
            if pdf_relpath is None:
                status = "outside_worktree"
            elif layout["ready"]:
                status = "prepared"
            else:
                status = "invalid_processed_layout"

        batch_entry = {
            "paper_id": paper_id,
            "citation_tag": pdf_metadata["citation_tag"],
            "paper_title_hint": pdf_metadata["paper_title_hint"],
            "expected_specimen_count": pdf_metadata["expected_specimen_count"],
            "processed_pdf": str(pdf_path) if pdf_path else None,
            "paper_pdf_relpath": pdf_relpath,
            "status": status,
            "layout": layout,
        }
        batch_entries.append(batch_entry)
        worker_jobs.append(
            build_worker_job(
                output_root=output_root,
                paper_id=paper_id,
                paper_pdf_relpath_value=pdf_relpath if status == "prepared" else None,
                expected_specimen_count=pdf_metadata["expected_specimen_count"],
                status=status,
            )
        )
        state_entries.append(
            {
                "paper_id": paper_id,
                "status": status,
                "retry_count": 0,
                "validated": False,
                "published": False,
                "last_error": None,
            }
        )

    batch_manifest = {
        "schema_version": "cfst-batch-manifest-v3",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_layout": "processed-pdf",
        "processed_root": str(processed_root),
        "worktree_root": str(worktree_root),
        "output_root": str(output_root),
        "git_status": git_repo_status(worktree_root),
        "paper_count": len(batch_entries),
        "papers": batch_entries,
    }

    batch_state = {
        "schema_version": "cfst-batch-state-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "paper_count": len(state_entries),
        "papers": state_entries,
    }

    if not args.dry_run:
        for directory in (manifests_dir, logs_dir, tmp_dir, final_output_dir):
            directory.mkdir(parents=True, exist_ok=True)
        write_json(manifests_dir / "batch_manifest.json", batch_manifest)
        write_json(manifests_dir / "worker_jobs.json", worker_jobs)
        write_json(manifests_dir / "batch_state.json", batch_state)

    prepared_count = sum(1 for item in batch_entries if item["status"] == "prepared")
    invalid_count = sum(1 for item in batch_entries if item["status"] == "invalid_processed_layout")
    print(f"[OK] Indexed {len(batch_entries)} papers from processed root.")
    print(f"[INFO] Prepared={prepared_count} InvalidLayout={invalid_count}")
    print(f"[INFO] Git repo present: {batch_manifest['git_status']['is_git_repo']}")
    print(f"[INFO] Output root: {output_root}")
    if not args.dry_run:
        print(f"[OK] Batch manifest: {manifests_dir / 'batch_manifest.json'}")
        print(f"[OK] Worker jobs: {manifests_dir / 'worker_jobs.json'}")
        print(f"[OK] Batch state: {manifests_dir / 'batch_state.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
