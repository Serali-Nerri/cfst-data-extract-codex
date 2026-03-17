#!/usr/bin/env python3
"""MCP Server for reading PDF files as page images and text.

Renders PDF pages to PNG via pdftocairo (Poppler) and returns them as
image content that LLM agents can view directly. Also extracts text
layers via pdftotext for page navigation and keyword search.
Designed as a drop-in PDF reading tool for Codex and Claude Code.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import unicodedata
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from mcp.server.fastmcp.utilities.types import Image
from mcp.types import TextContent

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CACHE_ROOT = Path("/tmp/pdfread_cache")
MAX_PAGES_LIMIT = 20
DEFAULT_MAX_PAGES = 8
DEFAULT_DPI = 300
SUPPORTED_FORMATS = ("png", "jpeg")

# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------

mcp = FastMCP("pdfread_mcp")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _validate_pdf_path(raw_path: str) -> Path:
    """Resolve and validate that the path points to an existing PDF."""
    path = Path(raw_path).expanduser().resolve()
    if not path.exists():
        raise ValueError(f"File not found: {path}")
    if not path.is_file():
        raise ValueError(f"Not a file: {path}")
    if path.suffix.lower() != ".pdf":
        raise ValueError(f"Not a PDF file: {path}")
    return path


def _get_total_pages(pdf_path: Path) -> int:
    """Return total page count via pdfinfo."""
    proc = subprocess.run(
        ["pdfinfo", str(pdf_path)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"pdfinfo failed: {proc.stderr.strip()}")
    for line in proc.stdout.splitlines():
        if line.startswith("Pages:"):
            return int(line.split(":", 1)[1].strip())
    raise RuntimeError("Could not determine page count from pdfinfo output")


def _get_page_sizes(pdf_path: Path) -> list[dict[str, Any]]:
    """Return per-page sizes (points) via pdfinfo -l."""
    total = _get_total_pages(pdf_path)
    proc = subprocess.run(
        ["pdfinfo", "-l", str(total), str(pdf_path)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    sizes: list[dict[str, Any]] = []
    page_re = re.compile(
        r"Page\s+(\d+)\s+size:\s+([\d.]+)\s+x\s+([\d.]+)\s+pts"
    )
    for m in page_re.finditer(proc.stdout):
        sizes.append(
            {
                "page": int(m.group(1)),
                "width_pt": float(m.group(2)),
                "height_pt": float(m.group(3)),
            }
        )
    return sizes


def _cache_dir_for(pdf_path: Path) -> Path:
    """Deterministic cache directory for a given PDF file."""
    stat = pdf_path.stat()
    identity = f"{pdf_path}|{stat.st_size}|{stat.st_mtime_ns}"
    digest = hashlib.sha256(identity.encode()).hexdigest()[:16]
    return CACHE_ROOT / digest


def _render_page(
    pdf_path: Path, page: int, dpi: int, fmt: str
) -> Path:
    """Render one page to an image file, returning the cached path."""
    cache_dir = _cache_dir_for(pdf_path)
    ext = "png" if fmt == "png" else "jpg"
    cached = cache_dir / f"page_{page:04d}_{dpi}dpi.{ext}"
    if cached.exists():
        return cached

    cache_dir.mkdir(parents=True, exist_ok=True)

    fmt_flag = "-png" if fmt == "png" else "-jpeg"
    # pdftocairo renders to <output_prefix>-<pagenumber>.png
    # Use a temp prefix then rename to canonical name.
    prefix = cache_dir / f"_tmp_p{page}_{dpi}"
    cmd = [
        "pdftocairo",
        fmt_flag,
        "-r", str(dpi),
        "-f", str(page),
        "-l", str(page),
        "-singlefile",
        str(pdf_path),
        str(prefix),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        raise RuntimeError(
            f"pdftocairo failed for page {page}: {proc.stderr.strip()}"
        )

    rendered = Path(f"{prefix}.{ext}")
    if not rendered.exists():
        raise RuntimeError(
            f"pdftocairo did not produce expected file: {rendered}"
        )
    rendered.rename(cached)
    return cached


def _extract_text_by_page(pdf_path: Path) -> list[dict[str, Any]]:
    """Extract text from each page via pdftotext, split on form feeds."""
    proc = subprocess.run(
        ["pdftotext", "-layout", str(pdf_path), "-"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"pdftotext failed: {proc.stderr.strip()}")
    raw_pages = proc.stdout.split("\f")
    result: list[dict[str, Any]] = []
    for i, text in enumerate(raw_pages):
        stripped = text.strip()
        if i == len(raw_pages) - 1 and not stripped:
            break  # trailing empty split after last form feed
        result.append({"page": i + 1, "text": stripped, "chars": len(stripped)})
    return result


def _compute_text_quality(pages: list[dict[str, Any]]) -> float:
    """Fraction of readable characters (letters, digits, CJK, common punctuation)."""
    total = 0
    readable = 0
    for p in pages:
        for ch in p["text"]:
            if ch.isspace():
                continue
            total += 1
            cat = unicodedata.category(ch)
            # L=letter, N=number, P=punctuation, S=symbol
            if cat[0] in ("L", "N", "P", "S"):
                readable += 1
    return round(readable / total, 3) if total > 0 else 0.0


def _parse_page_ranges(spec: str, total: int) -> list[int]:
    """Parse a page specification string into a sorted list of page numbers.

    Accepted formats:
        "3"         -> [3]
        "1-5"       -> [1, 2, 3, 4, 5]
        "1,3,7"     -> [1, 3, 7]
        "2-4,8,10-12" -> [2, 3, 4, 8, 10, 11, 12]
    """
    pages: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            tokens = part.split("-", 1)
            start, end = int(tokens[0].strip()), int(tokens[1].strip())
            if start < 1:
                start = 1
            if end > total:
                end = total
            pages.update(range(start, end + 1))
        else:
            p = int(part)
            if 1 <= p <= total:
                pages.add(p)
    return sorted(pages)


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@mcp.tool(
    name="pdf_info",
    annotations=ToolAnnotations(
        title="Get PDF Metadata",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
)
async def pdf_info(path: str) -> str:
    """Get metadata for a PDF file: total page count and per-page sizes.

    Use this before pdf_pages to understand the document structure and
    plan which pages to read.

    Args:
        path: Absolute path to the PDF file.

    Returns:
        JSON object with:
        - path (str): resolved absolute path
        - total_pages (int): number of pages
        - page_sizes (list): [{page, width_pt, height_pt}, ...]
    """
    pdf_path = _validate_pdf_path(path)
    total = _get_total_pages(pdf_path)
    sizes = _get_page_sizes(pdf_path)
    return json.dumps(
        {
            "ok": True,
            "path": str(pdf_path),
            "total_pages": total,
            "page_sizes": sizes,
        },
        ensure_ascii=False,
    )


@mcp.tool(
    name="pdf_pages",
    annotations=ToolAnnotations(
        title="Read PDF Pages as Images",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
)
async def pdf_pages(
    path: str,
    pages: str = "1",
    max_pages: int = DEFAULT_MAX_PAGES,
    dpi: int = DEFAULT_DPI,
    format: str = "png",
    paths_only: bool = False,
) -> list:
    """Render PDF pages to images and return them for direct viewing.

    Each requested page is rendered to a PNG (or JPEG) image via
    pdftocairo and returned as inline image content. A JSON metadata
    block is included alongside the images.

    Rendered pages are cached on disk — repeated reads of the same page
    at the same DPI are instant.

    Args:
        path: Absolute path to the PDF file.
        pages: Page specification. Examples: "1", "1-5", "2,4,7",
               "1-3,8,10-12". Defaults to "1".
        max_pages: Maximum pages to render in one call (safety limit).
                   Defaults to 8, hard cap at 20.
        dpi: Rendering resolution in dots per inch. 300 is good for
             most documents; use 400-600 for small text or dense tables.
             Defaults to 300.
        format: Image format, "png" (lossless, default) or "jpeg".
        paths_only: When True, render and cache images but only return
                    file paths as text metadata — no inline ImageContent.
                    Use this to avoid loading images into the conversation
                    context. Then call view_image on individual paths as
                    needed. Defaults to False.

    Returns:
        A list containing:
        - TextContent: JSON metadata with total_pages, rendered page
          numbers, image paths, and has_more flag
        - ImageContent[]: one image per rendered page (omitted when
          paths_only=True)
    """
    pdf_path = _validate_pdf_path(path)
    total = _get_total_pages(pdf_path)

    # Validate format
    fmt = format.lower()
    if fmt not in SUPPORTED_FORMATS:
        raise ValueError(f"Unsupported format '{format}'. Use: {SUPPORTED_FORMATS}")

    # Validate DPI
    if not (72 <= dpi <= 1200):
        raise ValueError(f"DPI must be between 72 and 1200, got {dpi}")

    # Clamp max_pages
    effective_max = min(max(1, max_pages), MAX_PAGES_LIMIT)

    # Parse requested pages
    requested = _parse_page_ranges(pages, total)
    if not requested:
        raise ValueError(
            f"No valid pages in '{pages}'. PDF has {total} pages (1-{total})."
        )

    # Apply max_pages limit
    truncated = requested[:effective_max]
    has_more = len(requested) > effective_max or truncated[-1] < total

    # Render pages
    rendered_info: list[dict[str, Any]] = []
    images: list[Image] = []
    for pg in truncated:
        img_path = _render_page(pdf_path, pg, dpi, fmt)
        rendered_info.append(
            {"page": pg, "image_path": str(img_path)}
        )
        images.append(Image(path=str(img_path)))

    # Build metadata
    metadata = {
        "ok": True,
        "path": str(pdf_path),
        "total_pages": total,
        "rendered_pages": [r["page"] for r in rendered_info],
        "rendered_count": len(rendered_info),
        "dpi": dpi,
        "format": fmt,
        "has_more": has_more,
        "image_paths": [r["image_path"] for r in rendered_info],
    }

    # Return text metadata + image content
    result: list[Any] = [
        TextContent(type="text", text=json.dumps(metadata, ensure_ascii=False))
    ]
    if not paths_only:
        result.extend(images)
    return result


@mcp.tool(
    name="pdf_text",
    annotations=ToolAnnotations(
        title="Extract PDF Text Layer",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
)
async def pdf_text(path: str, max_pages: int = 80) -> str:
    """Extract the text layer from a PDF file, split by page number.

    Use this for keyword search and page navigation before calling
    pdf_pages or view_image on specific pages. The text layer is a
    navigation aid only — do not use it to extract specimen values.

    Args:
        path: Absolute path to the PDF file.
        max_pages: Advisory page limit. When total pages exceed this
                   value, the result includes an exceeds_limit warning.
                   The text is still extracted. Defaults to 80.

    Returns:
        JSON object with:
        - path (str): resolved absolute path
        - total_pages (int): number of pages
        - text_quality (float): fraction of readable characters (0-1)
        - exceeds_limit (bool): true when total_pages > max_pages
        - warning (str|null): human-readable warning if exceeds_limit
        - pages (list): [{page, text, chars}, ...]
    """
    pdf_path = _validate_pdf_path(path)
    total = _get_total_pages(pdf_path)
    text_pages = _extract_text_by_page(pdf_path)
    quality = _compute_text_quality(text_pages)
    exceeds = total > max_pages

    return json.dumps(
        {
            "ok": True,
            "path": str(pdf_path),
            "total_pages": total,
            "text_quality": quality,
            "exceeds_limit": exceeds,
            "warning": f"PDF has {total} pages (limit {max_pages})" if exceeds else None,
            "pages": text_pages,
        },
        ensure_ascii=False,
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    mcp.run()
