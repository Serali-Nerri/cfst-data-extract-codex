# Single-Paper Worker Flow V2.1

Use this file as the worker execution contract for one paper.

Section map:

- `## 1-3`: enforce worker scope, required inputs, and execution order.
- `## 4-5`: apply validity and ordinary-CFST gates.
- `## 6-10`: resolve setup figures, read PDF pages directly, resolve concrete-strength basis, and preserve numeric and evidence traces.
- `## 11-12`: enforce validation expectations and final output goals.

## 1. Worker Contract

- process exactly one paper PDF
- treat the parent-supplied worker brief plus this file and `references/extraction-rules.md` as the complete worker contract
- read the owned paper through the canonical sequence `pdf_info` → `pdf_text` → optional `pdf_montage` → `pdf_pages` → `view_image`; run sandbox-only helpers only through the parent-provided `worker_sandbox.py` command
- use `pdf_text` output only for page navigation and keyword search; never extract specimen values from the text layer
- `scripts/safe_calc.py` and `scripts/validate_single_output.py` require `CFST_SANDBOX=1`; do not call them directly from the parent shell
- read only the owned paper PDF and the two worker references by default
- do not read `SKILL.md`, `runs/`, prior outputs, or `scripts/` to infer schema, validation, or path rules
- when the parent provides both `temp_json_host_path` and `temp_json_workspace_path`, write the JSON on disk to `temp_json_host_path`; the workspace path is the sandbox-visible alias of that same file
- never create or rely on a worktree-local relative `runs/...` JSON path
- before building the final JSON, prepare one structured extraction draft at `output/tmp/<paper_id>/_scratch/extraction_draft.yaml`; do not write a second JSON artifact on disk
- if a concrete runtime blocker remains unresolved after following the documented command, you may inspect the one named helper script involved and report that you did so
- write only to the worker-local temp directory
- never write directly to final published output
- treat repository as non-exclusive runtime; other workers may change unrelated files concurrently
- do not edit, revert, or publish outside declared worker ownership

## 2. Required Input Layout

The worker receives:

- `paper_id`: owned paper id
- `worktree_path`: worker-local worktree root for sandbox execution
- `paper_pdf_path`: absolute path to the PDF file on the host filesystem — use this path when calling `pdf_info`, `pdf_text`, `pdf_montage`, and `pdf_pages` MCP tools
- `paper_pdf_relpath`: relative path to the PDF file under the worktree root — use this path in sandbox commands (`--paper-dir-relpath`)
- `output_dir`: worker-local output directory inside the worktree
- `output_host_path`: host-backed directory bound into `output_dir`
- `temp_json_workspace_path`: sandbox-visible JSON path inside `output_dir`
- `temp_json_host_path`: host-backed JSON path that must be written on disk

These fields, plus the parent brief and the two worker references, should be enough to execute the paper without reading extra scripts or repository files for hidden rules.

The canonical PDF-reading sequence is `pdf_info` → `pdf_text` → optional `pdf_montage` → `pdf_pages(paths_only=true)` → `view_image`. `pdf_text` returns a `cache_path` pointing to the MCP-managed cached text-layer JSON file and can optionally inline all pages, a preview subset, or only matched pages. Prefer metadata plus `cache_path` when you only need navigation. Use `pdf_montage` only as a navigation/comparison aid when several already-identified pages need to be seen side by side.

Long paper filenames are allowed and do not need to be renamed.

If the PDF file does not exist at the given path or cannot be read by the MCP tool, fail fast and report the missing or unreadable file.

## 3. Mandatory Execution Order

1. Read `references/extraction-rules.md` and this file.
2. Verify the paper PDF exists at the given path.
3. Call `pdf_info` on the paper PDF to get the total page count.
4. Call `pdf_text` to extract the text-layer index for the entire paper.
   - prefer `include_pages=false` when you only need metadata, `cache_path`, and optional page-hit metadata
   - use `preview_pages` only when you need a small inline preview
   - use `match_query` plus `matched_pages_only=true` only when you intentionally want just the matched pages inline
   - If `text_quality < 0.3`, proceed with image-first scanning.
   - If you call MCP tools from `js_repl` via `codex.tool(...)`, remember that JSON-like tool payloads are typically exposed through `output.content[0].text`, not a direct `result` field.
5. Search the text index for keywords to locate target pages:
   - specimen tables: "Table", "表", "Specimen", "试件"
   - material properties: "Material", "Concrete", "材料", "混凝土", "C30"–"C80"
   - setup/loading figures: "Fig", "Figure", "图", "loading", "setup", "test"
   - paper metadata: title, authors, abstract on page 1
6. Build an internal evidence-anchor checklist before extraction:
   - `design_table_page`
   - `results_table_page`
   - `replicate_average_rule_page`
   - `setup_figure_page`
   - `loading_program_page`
   - `concrete_basis_page`
   - `steel_properties_page`
7. Build a structured extraction draft before final JSON assembly.
   - write exactly one non-canonical scratch file at `output/tmp/<paper_id>/_scratch/extraction_draft.yaml`
   - do not write a second JSON file on disk
   - minimum recommended sections:
     - `specimen_universe`
     - `controls_policy`
     - `replicate_policy`
     - `materials_map`
     - `results_map`
     - `setup_trace`
     - `ordinary_scope_notes`
8. Use `pdf_montage` only when it helps compare already-identified pages side by side.
   - montage is for navigation/comparison only, never for final value reading
   - low DPI broad scanning is optional and conditional; if you need it, prefer roughly `150-200 dpi`
9. Call `pdf_pages(paths_only=true)` on the identified target pages to render and cache them without injecting images into context.
   - use normal single-page reading at about `300 dpi`
   - if a page has small headers, footnotes, merged cells, or symbol ambiguity, rerender that page at higher DPI before reading values
10. Use `view_image` on each target page path to inspect it visually. Read values directly from the rendered single-page images. The single-page image is the source of truth for all specimen values.
11. Identify specimen-bearing tables, setup/loading figures, grouped-average notes, and non-CFST control rows from the viewed page images.
12. Resolve concrete-strength basis evidence from `Materials`, `Specimens`, `Concrete properties`, notation sections, and table footnotes before assigning `fc_basis`. First search for nearby concrete-strength-grade signals such as `C30`, `C40`, `C50`, `C60`, or `C60/75`, then interpret symbols such as `fck`, `fc`, `f'c`, or `Fc`.
13. Run the validity gate.
14. Build the specimen universe for this paper.
   - keep only CFST specimens for `Group_A` / `Group_B` / `Group_C`
   - exclude hollow steel tube / bare steel tube / empty steel tube / other non-CFST controls before ordinary tagging
15. Run the ordinary-CFST Tier 1 paper-level preconditions.
16. Resolve the setup figure from PDF page image evidence.
17. Extract specimen rows directly from PDF page images.
18. When a paper reports grouped average measured capacity for an explicit repeated-specimen group, expand the reported group label `G` into `G-1 ... G-q`, assign that same average `n_exp` to each defensibly identified member row, and mark `group_average_n_exp`.
19. Normalize units and derived values with `scripts/safe_calc.py`.
20. Run the ordinary-CFST Tier 2 per-specimen evaluation and tag each specimen with `is_ordinary` and `ordinary_exclusion_reasons`.
21. Derive paper-level `is_ordinary_cfst` and `ordinary_filter` summary from specimen flags.
22. Build schema-v2.1 JSON from `output/tmp/<paper_id>/_scratch/extraction_draft.yaml` plus the final page-image evidence.
23. Write that JSON on disk to `temp_json_host_path` from the worker brief. Do not create a worktree-local relative `runs/...` JSON path.
24. Validate that same file through `temp_json_workspace_path` with the parent-provided `worker_sandbox.py` command.
25. If validation fails for schema, data, or evidence reasons, repair once, overwrite the same host-backed JSON path, and validate once more.
26. If validation fails for path, mount, sandbox startup, or ownership reasons, stop and report the failure; do not relocate the JSON and do not create a second copy elsewhere.

## 4. Validity Gate

Stop as invalid when the paper is:

- FE-only
- theory-only or review-only
- non-column CFST study without recoverable specimen data
- no usable ultimate experimental load data

Grouped average measured capacities do not make a paper invalid by themselves. If the repeated-specimen group membership is explicit enough to map the same reported average to each member row defensibly, keep the paper valid and mark the affected rows with `group_average_n_exp`.

For invalid papers:

- `is_valid=false`
- `is_ordinary_cfst=false`
- empty specimen groups
- non-empty single-line `reason`

## 5. Ordinary-CFST Gate (Two-Tier, Specimen-Level)

Even when `is_valid=true`, evaluate each specimen individually for ordinary-CFST inclusion using the two-tier model defined in `references/extraction-rules.md` section 2.

The ordinary gate applies only to the kept CFST specimen rows. Non-CFST controls are excluded before this stage and must not be written into `Group_A`, `Group_B`, or `Group_C`.

### Tier 1 -- Paper-Level Preconditions

Check once for the whole paper. If any fails, set all specimens to `is_ordinary=false` with the paper-level reason in each specimen's `ordinary_exclusion_reasons`.

- `test_temperature = ambient`
- `loading_regime = static`
- no paper-wide durability conditioning (fire, corrosion, freeze-thaw)

### Tier 2 -- Per-Specimen Evaluation

When Tier 1 passes, check each specimen individually:

- `section_shape in {circular, square, rectangular, round-ended}`
- `steel_type = carbon_steel`
- `concrete_type in {normal, high_strength, recycled}`
- `loading_pattern = monotonic`
- eccentric compression is single-direction when present
- no strengthening or special confinement
- recycled aggregate `R%` is explicitly extractable when `concrete_type = recycled`

Tag each specimen:

- `is_ordinary = true` with `ordinary_exclusion_reasons = []` when all conditions pass
- `is_ordinary = false` with non-empty `ordinary_exclusion_reasons` listing each failing condition

### Paper-Level Derivation

After all specimens are tagged, derive paper-level fields:

- `is_ordinary_cfst` = true when at least one specimen has `is_ordinary=true`
- `ordinary_filter.include_in_dataset` = `is_ordinary_cfst`
- `ordinary_filter.ordinary_count` = count of ordinary specimens
- `ordinary_filter.total_count` = total specimen count
- `ordinary_filter.special_factors`: paper-level special tags
- `ordinary_filter.exclusion_reasons`: paper-level exclusion summaries

## 6. Setup Figure Resolution

- identify the setup/loading figure from PDF page images
- look for pages containing loading apparatus diagrams, test setup schematics, or captions such as `Fig.`, `Figure`, `loading device`, `test setup`
- determine loading mode from visual evidence when possible
- do not decide loading mode from text alone when setup image evidence exists
- note the PDF page number where the setup figure appears

Store the resolved setup trace in:

- `paper_level.loading_mode`
- `paper_level.setup_figure` (with `image_path = null` and `page` set to the PDF page number)
- specimen `loading_mode`
- specimen `evidence.setup_image` (set to `null`; the page reference goes in `evidence.page` or `paper_level.setup_figure.page`)

## 7. Direct PDF Reading

The worker reads the paper using a metadata-first, then text-and-image approach:
1. `pdf_info` captures total-page metadata and supports page-planning.
2. `pdf_text` extracts a text-layer index for page navigation and keyword search.
3. `pdf_montage` may be used on already-identified pages for side-by-side comparison.
4. `pdf_pages(paths_only=true)` renders target pages to cached image files.
5. `view_image` loads individual page images for visual inspection.

The text layer is a navigation aid only. Do not extract specimen values from it.

`pdf_montage` is also a navigation aid only. Use it to compare a few pages side by side, not to read specimen values.

When `pdf_text` cannot localize the paper reliably, you may do a low-DPI visual sweep to find candidate pages. Treat that sweep as page discovery only. Re-render any page that supplies specimen values, table headers, footnotes, or row boundaries at normal or high DPI and confirm those values through single-page `view_image`.

The page image is the single source of truth for all specimen values including row boundaries, merged cells, units, symbols, and signs.

Do not extract specimen values from any text layer or OCR output. Read values directly from the rendered PDF page images.

There is no separate markdown or table image layer to reconcile. The PDF page image is the authoritative evidence for every specimen-bearing table.

### 7.1 `pdf_text` Template

When using `pdf_text` through `js_repl`, use this wrapper shape instead of guessing the returned structure:

```javascript
const res = await codex.tool("mcp__pdfread__pdf_text", {
  path: paper_pdf_path,
  include_pages: false,
  match_query: "Table"
});
const textIndex = JSON.parse(res.output.content[0].text);
const cachePath = textIndex.cache_path;
const hits = textIndex.matched_pages || [];
```

This note is specifically for `js_repl` + `codex.tool(...)`. Outside that wrapper, follow the runtime's normal tool-return convention. The returned object includes `cache_path`, which points to the MCP-managed on-disk JSON cache for the text layer, plus optional inline `pages` depending on `include_pages`, `preview_pages`, and `matched_pages_only`.

## 8. Concrete-Strength Basis Rules

- `fc_type` must stay in validator-compatible measurement form only: `cube`, `cylinder`, `prism`, `unknown`, or sized forms such as `Cube 150`, `Cylinder 100x200`, or `Prism 150x150x300`
- never store shorthand notation or explanatory prose such as `fck`, `fcu`, `f'c`, `fc`, or `Prism-equivalent fck converted from Cube 150` inside `fc_type`

- treat explicit material/property evidence as first priority: `Materials`, `Specimens`, `Concrete properties`, notation sections, table headers, and table footnotes outrank shorthand labels such as `C60`
- before interpreting notation symbols, search nearby material/property text, the same sentence or paragraph, table headers, and footnotes for concrete-strength-grade signals such as `C30`, `C40`, `C50`, `C60`, or `C60/75`
- resolve `fc_basis` before doing any normalization or downstream interpretation of `fc_value`
- map explicit `150 mm cube` or equivalent standard-cube wording to `fc_basis = cube`
- map explicit cylinder wording, cylinder dimensions, `ASTM C39`, `JIS A 1108`, `JIS A 1132`, or equivalent cylinder-test descriptions to `fc_basis = cylinder`
- map explicit prism-strength / axial-compressive-strength wording to `fc_basis = prism`
- in Chinese GB/T 50010-type context, treat bare `C30`, `C40`, `C50`, `C60`, `C70`, and similar `C` grades as code-defined cube-strength grades unless the paper itself contradicts that reading
- in the same Chinese GB/T 50010-type context, a nearby single-grade `C30` / `C40` / `C50` / `C60`-style signal belongs to the code-context layer and must be checked before a nearby bare `fck` / `fc` symbol is allowed to lock `fc_basis = prism`
- in the same Chinese GB/T 50010-type context, when a reported measured strength value is numerically consistent with the nearby cube-grade system and clearly inconsistent with the prism/axial reading of a nearby `fck` / `fc` symbol, you may resolve `fc_basis = cube`; keep `fc_type` consistent with the stored measurement, typically `Cube 150` unless the paper explicitly gives another cube size or converted equivalent
- in the same Chinese GB/T 50010-type context, treat `fck` and `fc` as prism/axial-system values, not cylinder strengths
- in Eurocode / EN 206 context, read `Cx/y` as `x = cylinder`, `y = cube`; treat `Cx/y` as code-context evidence and do not collapse it to a single-basis guess
- in Eurocode / EN 206 context, treat `fck` as the characteristic cylinder compressive strength; when a European paper writes `fck` without a `Cx/y` grade, use `fc_basis = cylinder`
- in United States ACI / ASTM C39 context, treat `f'c` as cylinder-based specified compressive strength
- in Japanese `Fc` / JIS A 1108 / JIS A 1132 context, treat `Fc` as cylinder-based unless the paper explicitly defines another basis
- treat a bare single-value `C60` outside explicit Chinese cube context as ambiguous; inspect the cited code and the material/property section before choosing `cube` or `cylinder`
- the same symbol means different things across codes: China `fck` (axial/prism, e.g., C60 -> 38.5 MPa) is NOT Eurocode `fck` (cylinder, e.g., C60/75 -> 60 MPa); China `fc` (axial design value) is NOT US `f'c` (specified cylinder strength); Japan `Fc` (JIS cylinder-based design standard strength) is NOT interchangeable with Chinese `fc` or US `f'c`; always check which code governs the specimen before interpreting these symbols
- when both cube and cylinder values are reported, prefer the value the authors explicitly use in the specimen-property table, material parameters, constitutive model, or design/check calculations
- if a nearby `Cxx` grade signal and a nearby `fck` / `fc` symbol point to different bases, and no explicit cube / cylinder / prism test description resolves the conflict, use the Chinese GB/T cube-grade plus measured-value exception above when it applies; otherwise set `fc_basis = unknown`
- if the paper still does not identify the basis defensibly, set `fc_basis = unknown` and keep `fcy150 = null`
- when the basis is inferred from code/notation context rather than an explicit specimen description, mark `quality_flags` with `context_inferred_fc_basis`

## 9. Numeric Rules

- every conversion or derivation must use `scripts/safe_calc.py`
- store published JSON numbers in canonical `MPa / mm / kN / %` units
- round to `0.001`
- keep the `fcy150` key present; it may stay `null` when project-level strength normalization is deferred
- `boundary_condition` may be `unknown` or `null` when the paper does not define it defensibly
- `L` means project geometric specimen length, not effective length
- keep eccentricity signs as source evidence shows them
- do not use the sign pattern of `e1` and `e2` alone to exclude a specimen from the ordinary dataset
- recycled concrete rows must preserve `R%` in `r_ratio`
- when the paper does not define `L`, use steel-tube net height only when the figure evidence makes that geometry explicit, and record the derivation
- never infer `L` from boundary-condition assumptions or effective-length formulas

Use the parent-provided sandbox wrapper when calling `safe_calc.py`. Command pattern:

```bash
python .codex/skills/cfst-paper-extractor/scripts/worker_sandbox.py \
  --worktree-path <worktree_path> \
  --paper-dir-relpath <paper_pdf_relpath> \
  --output-dir <output_dir> \
  --host-output-dir <output_host_path> \
  --cwd-mode workspace \
  -- \
  python3 .codex/skills/cfst-paper-extractor/scripts/safe_calc.py "D / 2" \
    --var D=164 \
    --round 3
```

Use the same wrapper shape for other derived values and unit conversions; do not infer `safe_calc.py` parameters from ad hoc script reading unless a concrete runtime blocker forces that inspection.

## 10. Evidence Rules

Every specimen row must preserve:

- concise `source_evidence`
- structured `evidence.page`
- `evidence.table_id`
- `evidence.figure_id`
- `evidence.table_image`
- `evidence.setup_image`
- `evidence.value_origin`

`evidence.page` is required and should always be populated since the worker knows which PDF page it read. Use explicit wording such as `Page 4` in both `evidence.page` and `source_evidence`.

`evidence.table_image` may be `null` when the paper is read directly from PDF. The page reference in `evidence.page` serves as the locator for the specimen-bearing table.

`evidence.setup_image` may be `null`. The page reference goes in `evidence.page` or `paper_level.setup_figure.page`.

If page localization still cannot be determined, set `evidence.page = null` and keep the best available table/figure/text locator in `source_evidence` rather than inventing a page number.

When a stored value is converted to canonical units, keep the original raw unit/value trace in `evidence.value_origin` and preserve `quality_flags` such as `unit_converted`.

When a value is derived, the field-level evidence must record:

- formula
- raw text
- raw unit if any
- source location

### 10.1 Reducing Redundancy in `value_origin`

When many specimens share identical evidence (e.g., all specimens use the same concrete strength from a single material section), you may simplify the JSON using one of these approaches:

1. **Omit fully redundant entries**: If **all specimens** share identical `value_origin` for a field (e.g., `fc_value`, `e1`, `e2`), document the shared evidence once in `paper_level.notes` and omit those fields from individual specimen `value_origin` objects.

2. **Use minimal row-specific entries**: For fields extracted from a specimen table where only the row identifier changes (e.g., `fy`, `b`, `h`, `t`, `L`, `n_exp`), you may shorten the `source` to just the table name instead of repeating "Page X Table Y row SC-Z" for every specimen. The row identifier is already implicit in `specimen_label`.

3. **Add `paper_level.shared_evidence`**: Store evidence that applies to all specimens in an optional `paper_level.shared_evidence` object, then omit those fields from individual specimen `value_origin` objects.

**Default**: If unsure whether evidence is truly shared or if simplification might lose traceability, **keep the full `value_origin` for every specimen**. Redundancy is acceptable; loss of provenance is not.

For `fc_basis` decisions:

- cite the exact `Materials` / `Specimens` / `Concrete properties` paragraph, table header, or table footnote when available
- if you rely on code/notation context such as GB/T 50010 `C60`, Eurocode `C60/75`, ACI `f'c`, or Japanese `Fc`, name that context explicitly in `source_evidence`
- do not leave a context-inferred `fc_basis` unexplained in `source_evidence`

## 11. Validation Expectations

Validation outcomes fall into two classes:

- schema/data/evidence failures: repair once, overwrite the same temp JSON, and rerun validation once
- path/mount/sandbox failures such as missing JSON at the declared path: report the failure to the parent and stop; do not move the JSON or invent a second output path

Warnings alone are not validator failure. If the validator exits zero with warnings only, the worker may return success unless a warning reflects a clearly recoverable omission that it is already correcting during an error-driven repair pass.

Validation must reject:

- missing or blank `specimen_label`
- invalid `fc_basis`
- impossible dimensions or strengths
- `is_valid=false` with non-empty specimen groups
- axial rows with nonzero eccentricity
- eccentric rows with both eccentricities zero
- non-null `fcy150` values that are non-numeric or non-positive
- `is_ordinary=true` with shapes outside circular / square / rectangular / round-ended
- `is_ordinary=true` with non-carbon steel
- `is_ordinary=true` with concrete types outside normal / high-strength / recycled
- `is_ordinary=true` with `loading_pattern != monotonic`
- `is_ordinary=false` with empty `ordinary_exclusion_reasons`
- `is_ordinary_cfst=true` but no specimen has `is_ordinary=true`
- `is_ordinary_cfst=false` but some specimen has `is_ordinary=true`
- `ordinary_filter.ordinary_count` mismatch with actual count of `is_ordinary=true` specimens
- per-specimen `loading_pattern` not in allowed specimen-level values
- duplicate specimen labels

## 12. Final Output Goal

The single-paper JSON should be:

- traceable
- physically plausible
- ordinary-filter aware
- canonical for downstream project-specific processing
