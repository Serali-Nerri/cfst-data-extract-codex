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


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


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


def _text_cache_path(pdf_path: Path) -> Path:
    return _cache_dir_for(pdf_path) / "pdf_text.json"


def _load_or_build_text_cache(pdf_path: Path) -> tuple[Path, dict[str, Any]]:
    cache_path = _text_cache_path(pdf_path)
    if cache_path.exists():
        return cache_path, _read_json(cache_path)

    total = _get_total_pages(pdf_path)
    text_pages = _extract_text_by_page(pdf_path)
    payload = {
        "ok": True,
        "path": str(pdf_path),
        "total_pages": total,
        "text_quality": _compute_text_quality(text_pages),
        "pages": text_pages,
    }
    _write_json(cache_path, payload)
    return cache_path, payload


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
async def pdf_text(
    path: str,
    max_pages: int = 80,
    include_pages: bool = True,
    preview_pages: int | None = None,
    match_query: str | None = None,
    matched_pages_only: bool = False,
) -> str:
    """Extract the text layer from a PDF file, split by page number.

    Use this for keyword search and page navigation before calling
    pdf_pages or view_image on specific pages. The text layer is a
    navigation aid only — do not use it to extract specimen values.

    The extracted text layer is cached on disk so long papers do not
    need to be reprocessed on every call. The returned JSON includes
    both the inline page text and the cache file path.

    Args:
        path: Absolute path to the PDF file.
        max_pages: Advisory page limit. When total pages exceed this
                   value, the result includes an exceeds_limit warning.
                   The text is still extracted. Defaults to 80.
        include_pages: When True (default), inline page text in the
                       response. Set False to return metadata plus
                       `cache_path` only.
        preview_pages: Optional number of leading pages to inline when
                       `include_pages=True`.
        match_query: Optional case-insensitive substring used to locate
                     matching pages in the cached text layer.
        matched_pages_only: When True, inline only the pages matching
                            `match_query`. Requires `match_query` and
                            `include_pages=True`.

    Returns:
        JSON object with:
        - path (str): resolved absolute path
        - total_pages (int): number of pages
        - text_quality (float): fraction of readable characters (0-1)
        - cache_path (str): cached text-layer JSON file path
        - exceeds_limit (bool): true when total_pages > max_pages
        - warning (str|null): human-readable warning if exceeds_limit
        - pages_mode (str): one of `all`, `preview`, `matched`, `none`
        - returned_page_count (int): number of inline page entries
        - matched_pages (list[int]): pages matching `match_query` when provided
        - pages (list): [{page, text, chars}, ...] when `include_pages=True`
    """
    if preview_pages is not None and preview_pages < 0:
        raise ValueError(f"preview_pages must be >= 0, got {preview_pages}")
    if preview_pages is not None and not include_pages:
        raise ValueError("preview_pages requires include_pages=True")
    if matched_pages_only and not include_pages:
        raise ValueError("matched_pages_only requires include_pages=True")
    if matched_pages_only and not match_query:
        raise ValueError("matched_pages_only requires match_query")
    if matched_pages_only and preview_pages is not None:
        raise ValueError("preview_pages and matched_pages_only cannot be combined")

    pdf_path = _validate_pdf_path(path)
    cache_path, cached_payload = _load_or_build_text_cache(pdf_path)
    total = int(cached_payload["total_pages"])
    quality = float(cached_payload["text_quality"])
    text_pages = cached_payload["pages"]
    exceeds = total > max_pages
    matched_pages: list[int] | None = None
    if match_query:
        query = match_query.casefold()
        matched_pages = [
            int(page["page"])
            for page in text_pages
            if query in str(page.get("text", "")).casefold()
        ]

    returned_pages: list[dict[str, Any]] | None = None
    pages_mode = "none"
    if include_pages:
        if matched_pages_only:
            matched_set = set(matched_pages or [])
            returned_pages = [
                page for page in text_pages if int(page["page"]) in matched_set
            ]
            pages_mode = "matched"
        elif preview_pages is not None:
            returned_pages = text_pages[:preview_pages]
            pages_mode = "preview"
        else:
            returned_pages = text_pages
            pages_mode = "all"

    result = {
        "ok": True,
        "path": str(pdf_path),
        "total_pages": total,
        "text_quality": quality,
        "cache_path": str(cache_path),
        "exceeds_limit": exceeds,
        "warning": f"PDF has {total} pages (limit {max_pages})" if exceeds else None,
        "pages_mode": pages_mode,
        "returned_page_count": len(returned_pages or []),
    }
    if match_query is not None:
        result["match_query"] = match_query
        result["matched_pages"] = matched_pages or []
        result["matched_page_count"] = len(matched_pages or [])
    if include_pages:
        result["pages"] = returned_pages or []

    return json.dumps(result, ensure_ascii=False)


@mcp.tool(
    name="pdf_montage",
    annotations=ToolAnnotations(
        title="Montage PDF Pages into One Image",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
)
async def pdf_montage(
    path: str,
    pages: str,
    cols: int = 2,
    dpi: int = 200,
    label: bool = True,
) -> list:
    """Combine selected PDF pages into a single side-by-side montage image.

    Renders the requested pages, then tiles them into one composite image
    using ImageMagick montage. Useful for cross-page comparison without
    switching between individual page images.

    This is a navigation/comparison aid only — do not use the montage
    to extract specimen values. Use view_image on individual pages for
    that purpose.

    Args:
        path: Absolute path to the PDF file.
        pages: Page specification. Examples: "26,27,54,55" or "3-6".
               Maximum 8 pages per montage.
        cols: Number of columns in the tile grid. Defaults to 2.
              Rows are computed automatically.
        dpi: Rendering resolution for individual pages before tiling.
             Lower than pdf_pages default for smaller output.
             Defaults to 200.
        label: When True, overlay the page number on each tile.
               Defaults to True.

    Returns:
        A list containing:
        - TextContent: JSON metadata with source pages and montage path
        - ImageContent: the composite montage image
    """
    pdf_path = _validate_pdf_path(path)
    total = _get_total_pages(pdf_path)

    requested = _parse_page_ranges(pages, total)
    if not requested:
        raise ValueError(
            f"No valid pages in '{pages}'. PDF has {total} pages (1-{total})."
        )
    if len(requested) > 8:
        raise ValueError(
            f"Montage supports at most 8 pages, got {len(requested)}. "
            "Narrow your selection."
        )

    cols = max(1, min(cols, len(requested)))

    # Render individual pages (reuses cache)
    page_paths: list[Path] = []
    for pg in requested:
        img = _render_page(pdf_path, pg, dpi, "png")
        page_paths.append(img)

    # Build montage output path in cache
    cache_dir = _cache_dir_for(pdf_path)
    cache_dir.mkdir(parents=True, exist_ok=True)
    page_tag = "_".join(str(p) for p in requested)
    montage_path = cache_dir / f"montage_{page_tag}_{dpi}dpi_{cols}c.png"

    if not montage_path.exists():
        cmd: list[str] = ["montage"]
        for i, pp in enumerate(page_paths):
            if label:
                cmd.extend(["-label", f"Page {requested[i]}"])
            cmd.append(str(pp))
        cmd.extend([
            "-tile", f"{cols}x",
            "-geometry", "+4+4",
            "-background", "white",
            "-border", "2",
            "-bordercolor", "#cccccc",
        ])
        if label:
            cmd.extend(["-font", "DejaVu-Sans", "-pointsize", "14"])
        cmd.append(str(montage_path))

        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if proc.returncode != 0:
            raise RuntimeError(f"montage failed: {proc.stderr.strip()}")

    metadata = {
        "ok": True,
        "path": str(pdf_path),
        "total_pages": total,
        "montage_pages": requested,
        "cols": cols,
        "dpi": dpi,
        "montage_path": str(montage_path),
    }

    return [
        TextContent(type="text", text=json.dumps(metadata, ensure_ascii=False)),
        Image(path=str(montage_path)),
    ]


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    mcp.run()
