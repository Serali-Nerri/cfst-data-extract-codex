# CFST Extraction Rules V2.2

Use this file as the extraction source of truth for one paper.

Section map:

- `## 1. Target Scope`: decide whether the paper is usable at all.
- `## 2. Ordinary-CFST Gate`: decide whether a valid paper belongs in the ordinary dataset.
- `## 3-6`: apply schema shape, group mapping, paper-level fields, ordinary-row fields, and excluded-bundle fields.
- `## 7-12`: apply evidence, loading, numeric, length, table-corruption, and invalid-output rules.

## 1. Target Scope

This workflow is for experimental CFST column papers that can support a unified ML/DL dataset for ultimate axial or eccentric compression resistance.

A paper is `is_valid=true` only when all are true:

- research object includes CFST columns or stub columns
- paper contains physical specimen test evidence
- paper includes usable specimen-level experimental capacity data
- loading mode is axial compression, single-direction eccentric compression, or a clearly separable mixture of those two modes

Grouped average measured capacities are still usable specimen-level experimental capacity data when the paper explicitly defines the repeated-specimen group membership, or gives enough specimen-count / parameter-set mapping to assign the same reported average to each member specimen row without fabricating group composition.
In that case, store the same `n_exp` on each member row, mark each affected specimen with `quality_flags += ["group_average_n_exp"]`, and make `source_evidence` state clearly that the value is a group average rather than an individually measured row value.
If a paper reports only grouped averages but the member-to-row mapping is not defensibly recoverable, those loads are not usable specimen-level capacity data.

### 1.1 Repeated-Specimen Group-Average Expansion

When a design/specimen table states that a reported group has `quantity = q`, but the results table reports only one average capacity row for that same group:

- treat the paper's printed identifier as the reported group label, not yet as the final unique specimen label
- expand the group into `q` specimen rows
- use the canonical naming rule `G-1`, `G-2`, ..., `G-q` where `G` is the paper's reported group label
- when available in the schema, set `reported_group_label = G` and `replicate_index = 1..q` on the expanded rows
- copy the shared design/material fields to each member row
- assign the same reported average `n_exp` to each member row
- mark every expanded row with `quality_flags += ["group_average_n_exp"]`
- make `source_evidence` explicitly say that the stored `n_exp` is a reported group average and cite both the member-count source and the result-table source
- compute `paper_level.expected_specimen_count` from the expanded member count, not from the number of reported result-table rows

Do not improvise alternative suffix styles such as `a/b`, `#1/#2`, or repeated identical labels. If defensible group membership still cannot be recovered, fail extraction instead of fabricating member rows.

## 2. Ordinary-CFST Gate

The ordinary gate uses a two-tier evaluation over the kept CFST specimen universe. Evaluate every kept CFST specimen as ordinary or non-ordinary before final JSON assembly. In schema-v2.2, ordinary CFST specimens stay as full rows in `Group_A`, `Group_B`, or `Group_C`; non-ordinary CFST specimens are represented through grouped top-level `excluded_specimens` bundles. The paper-level `is_ordinary_cfst` is derived: `true` when the paper contains at least one ordinary specimen row kept in `Group_A` / `Group_B` / `Group_C`, `false` otherwise.

### 2.1 Tier 1 — Paper-Level Preconditions

These conditions apply to all kept CFST specimens in the paper. If any fails, every kept CFST specimen becomes non-ordinary and must be summarized under `excluded_specimens` with the paper-level reason propagated into each bundle's `ordinary_exclusion_reasons`.

Tier 1 requires all of:

- `test_temperature = ambient`
- `loading_regime = static`
- no paper-wide durability conditioning (fire exposure, corrosion, freeze-thaw)

If Tier 1 fails, skip Tier 2 and treat the full kept CFST specimen universe as non-ordinary.

### 2.2 Tier 2 — Per-Specimen Evaluation

**Two-layer material classification**: use two separate fields to classify concrete.

- `concrete_type` records only the base concrete family: `normal`, `high_strength`, `recycled`, `lightweight`, `self_consolidating`, `uhpc`, `other`, `unknown`.
- `material_modifiers` records additional concrete modifications, admixture-driven functions, or non-ordinary material systems as a list of strings (empty list `[]` for plain concrete).

In the final published JSON, keep `material_modifiers` on ordinary specimen rows even when it is `[]`. That empty list is intentional evidence that the modifier scan was performed and no active modifier was found for that row.

Examples:
- High-strength expansive concrete: `concrete_type=high_strength` + `material_modifiers=["expansive_concrete"]`
- Recycled rubber concrete: `concrete_type=recycled` + `material_modifiers=["rubber_concrete"]`
- Plain high-strength concrete: `concrete_type=high_strength` + `material_modifiers=[]`

When Tier 1 passes, evaluate each specimen individually. A specimen is `is_ordinary=true` only when all hold:

- `section_shape` is one of: circular, square, rectangular, round-ended
- `steel_type = carbon_steel`
- `concrete_type` is one of: `normal`, `high_strength`, `recycled`
- `material_modifiers` is empty or contains no non-ordinary modifier
- `loading_pattern = monotonic`
- compression mode is axial or single-direction eccentric
- no strengthening, no added confinement device, no stiffener that changes the basic member system
- recycled aggregate concrete has explicit `R%` recorded in `r_ratio`

Non-ordinary `material_modifiers` — any of these makes the specimen non-ordinary regardless of `concrete_type`:

- `expansive_concrete`
- `rubber_concrete`
- `self_stressing_concrete`
- `reactive_powder`
- `fiber_reinforced`
- `polymer_modified`
- `geopolymer`
- `foamed_concrete`
- `other_modified_concrete`

Do not infer ordinary status from `concrete_type=high_strength` or `concrete_type=recycled` alone. Always separately scan for modifier evidence.

Typical ordinary specimens:

- normal or high-strength concrete with carbon-steel tube and no modifiers (`material_modifiers=[]`)
- recycled aggregate concrete with explicit `R%`, carbon-steel tube, and no modifiers
- static monotonic axial or single-direction eccentric compression

### 2.2.1 Control Specimens And Strengthening Mapping

Before building `Group_A`, `Group_B`, or `Group_C`, first partition the paper's tested specimens into:

- CFST specimens that belong in the extraction output
- non-CFST controls that must be excluded from specimen output entirely

Exclude non-CFST controls before ordinary tagging. Typical exclusions:

- hollow steel tube
- bare steel tube
- empty steel tube
- plain steel control
- steel-only comparison specimens without concrete infill

For the kept CFST specimens, use these ordinary-exclusion mappings:

- external jackets, welded cover plates, bonded reinforcement, section-enlarging plates, and internal or welded stiffeners that materially change the member system: `strengthened_section`
- rings, clamps, ties, hoops, or other added confinement devices whose primary role is extra confinement rather than restoring the base CFST section: `confinement_device`
- internal U-shaped stiffeners,拉结件,加劲肋, or similar added steel details that materially alter the wall-restraint mechanism: default to `strengthened_section` unless the paper clearly defines them as a separate confinement device

When the paper's wording is ambiguous, prefer a conservative non-ordinary classification over silently treating the specimen as ordinary, and explain the decision in `source_evidence`.

### 2.2.2 Concrete Classification Priority

When classifying concrete for `concrete_type` and `material_modifiers`, follow this priority:

1. Determine the base class (`normal` / `high_strength` / `recycled` / `lightweight` / `self_consolidating` / `uhpc` / `other`).
2. Separately scan for modifier evidence: expansive agent, 膨胀剂, 补偿收缩, 橡胶颗粒, 橡胶混凝土, 自应力, 自应力混凝土, 活性粉末, RPC, 纤维, 聚合物改性, 地聚物, 泡沫混凝土, etc.
3. If any modifier evidence is present, record it in `material_modifiers`.
4. Do not keep such specimens as ordinary unless the rule explicitly allows that modifier.
5. For mixed papers (some rows plain, some modified), classify specimen by specimen — not at paper level.

### 2.2.3 Zero-Dosage Control Specimen Exception

If a paper contains modified mixes plus an explicit plain-control mix with zero modifier dosage, the control row may be ordinary provided its own row carries no active modifier in that row. Set `material_modifiers=[]` and `is_ordinary=true` for that control row, and explain the zero-dosage decision in `source_evidence`.

### 2.3 Specimen Exclusion Tagging

When a kept CFST specimen fails Tier 2, mark it non-ordinary and record each failing condition in `ordinary_exclusion_reasons`. Common reasons:

- `stainless_steel`
- `lightweight_concrete`
- `self_consolidating_concrete`
- `uhpc`
- `cyclic_loading`
- `repeated_loading`
- `non_ordinary_shape`
- `confinement_device`
- `strengthened_section`
- `expansive_concrete`
- `rubber_concrete`
- `self_stressing_concrete`
- `reactive_powder`
- `fiber_reinforced`
- `polymer_modified`
- `geopolymer`
- `foamed_concrete`
- `other_modified_concrete`

A paper with mixed ordinary and non-ordinary CFST specimens keeps all CFST specimens in the output, but uses two representations: ordinary specimens become full rows in `Group_A` / `Group_B` / `Group_C`, while non-ordinary CFST specimens are grouped into `excluded_specimens` bundles keyed by shared exclusion reason and locator evidence. Non-CFST control specimens are excluded before ordinary tagging and must not appear either as group rows or as excluded bundles.

### 2.4 Paper-Level Derivation

After the kept CFST specimen universe is tagged and split into ordinary rows plus excluded bundles:

- `is_ordinary_cfst = any(specimen.is_ordinary for specimen in ordinary_group_rows)`
- `ordinary_filter.include_in_dataset = is_ordinary_cfst`
- `ordinary_filter.ordinary_count` = count of ordinary rows written into `Group_A` / `Group_B` / `Group_C`
- `ordinary_filter.total_count` = total kept CFST specimen count = ordinary group rows + represented member count across `excluded_specimens[*].specimen_labels`
- `ordinary_filter.special_factors`: sorted unique paper-level base-concrete tags derived from the kept CFST specimen universe. Allowed values only:
  - `high_strength_concrete`
  - `recycled_aggregate`
  Do not store specimen-level modifiers such as `expansive_concrete` here; those belong in `material_modifiers` and exclusion evidence.
- `ordinary_filter.exclusion_reasons`: list of paper-level exclusion summaries

## 3. Top-Level JSON Shape

Required top-level keys:

- `schema_version`
- `paper_id`
- `is_valid`
- `is_ordinary_cfst`
- `reason`
- `ordinary_filter`
- `ref_info`
- `paper_level`
- `Group_A`
- `Group_B`
- `Group_C`
- `excluded_specimens`

Recommended `schema_version` value:

- `cfst-paper-extractor-v2.2`

Published `output/<paper_id>.json` files are the canonical dataset artifact. Any downstream tabular conversion is project-specific and outside this skill's canonical schema.

Use the schema description below as the worker's example source of truth. Do not inspect `runs/`, prior outputs, or unrelated papers to infer JSON shape.

### 3.1 Canonical Skeleton

```json
{
  "schema_version": "cfst-paper-extractor-v2.2",
  "paper_id": "A1-1",
  "is_valid": true,
  "is_ordinary_cfst": true,
  "reason": "One-line paper usability summary.",
  "ordinary_filter": {
    "include_in_dataset": true,
    "ordinary_count": 1,
    "total_count": 1,
    "special_factors": [],
    "exclusion_reasons": []
  },
  "ref_info": {
    "title": "Paper title",
    "authors": ["Author 1", "Author 2"],
    "journal": "Journal name",
    "year": 2005,
    "citation_tag": "[A1-1]",
    "doi": null,
    "language": "zh"
  },
  "paper_level": {
    "loading_mode": "axial",
    "boundary_condition": "unknown",
    "test_temperature": "ambient",
    "loading_regime": "static",
    "loading_pattern": "monotonic",
    "setup_figure": {
      "figure_id": null,
      "image_path": null,
      "page": null
    },
    "expected_specimen_count": 1,
    "notes": []
  },
  "Group_A": [],
  "Group_B": [
    {
      "ref_no": "",
      "specimen_label": "SC-1",
      "reported_group_label": null,
      "replicate_index": null,
      "section_shape": "circular",
      "loading_mode": "axial",
      "loading_pattern": "monotonic",
      "boundary_condition": "unknown",
      "fc_value": 30.5,
      "fc_type": "cube",
      "fc_basis": "cube",
      "fy": 345.0,
      "fcy150": null,
      "r_ratio": 0.0,
      "steel_type": "carbon_steel",
      "concrete_type": "normal",
      "material_modifiers": [],
      "is_ordinary": true,
      "ordinary_exclusion_reasons": [],
      "b": 165.0,
      "h": 165.0,
      "t": 4.0,
      "r0": 82.5,
      "L": 495.0,
      "e1": 0.0,
      "e2": 0.0,
      "n_exp": 1650.0,
      "source_evidence": "One-line row summary with table and page trace."
    }
  ],
  "Group_C": [],
  "excluded_specimens": []
}
```

## 4. Group Mapping

- `Group_A`: square / rectangular
  - `b`: outer width
  - `h`: outer depth
- `Group_B`: circular
  - `b = h = D`
  - `r0 = h / 2`
- `Group_C`: elliptical / round-ended / obround
  - `b`: major axis
  - `h`: minor axis
  - `b >= h`
  - `r0 = h / 2`

Unlike v1, `Group_A.r0` is not forced to zero. Keep a nonzero corner radius when the paper provides it or when the section is clearly rounded-corner rectangular.

## 5. Required Paper-Level Fields

### 5.1 `ordinary_filter`

Required keys:

- `include_in_dataset`: boolean (true when at least one specimen is ordinary)
- `ordinary_count`: integer (count of specimens with `is_ordinary=true`)
- `total_count`: integer (total kept CFST specimen count = ordinary group rows + represented excluded bundle members)
- `special_factors`: sorted unique list drawn only from `high_strength_concrete` and `recycled_aggregate`
- `exclusion_reasons`: list of strings

### 5.2 `ref_info`

Required keys:

- `title`
- `authors`
- `journal`
- `year`
- `citation_tag`

Optional:

- `doi`
- `language`

### 5.3 `paper_level`

Required keys:

- `loading_mode`
- `boundary_condition`
- `test_temperature`
- `loading_regime`
- `loading_pattern`
- `setup_figure`
- `expected_specimen_count`
- `notes`

`paper_level.expected_specimen_count` must count the full kept CFST specimen universe represented in the final JSON: ordinary rows plus the member count carried by `excluded_specimens[*].specimen_labels`.

`loading_mode` allowed values:

- `axial`
- `eccentric`
- `mixed`
- `unknown`

`test_temperature` allowed values:

- `ambient`
- `elevated`
- `post_fire`
- `unknown`

`loading_regime` allowed values:

- `static`
- `dynamic`
- `impact`
- `unknown`

`loading_pattern` allowed values (paper level):

- `monotonic`
- `cyclic`
- `repeated`
- `mixed`
- `unknown`

`setup_figure` keys:

- `figure_id`
- `image_path`
- `page`

`boundary_condition` is trace metadata. Keep any defensible text the paper provides, but `null` or `unknown` is acceptable when the support condition cannot be recovered confidently. Do not derive `L` from boundary condition alone.

## 6. Required Output Row Fields

Every ordinary specimen row in `Group_A`, `Group_B`, or `Group_C` must contain:

- `ref_no`
- `specimen_label`
- `section_shape`
- `loading_mode`
- `loading_pattern`
- `boundary_condition`
- `fc_value`
- `fc_type`
- `fc_basis`
- `fy`
- `fcy150`
- `r_ratio`
- `steel_type`
- `concrete_type`
- `material_modifiers`
- `is_ordinary`
- `ordinary_exclusion_reasons`
- `b`
- `h`
- `t`
- `r0`
- `L`
- `e1`
- `e2`
- `n_exp`
- `source_evidence`

Optional specimen trace fields:

- `reported_group_label`
- `replicate_index`
- `quality_flags`: omit when empty; include when non-empty (e.g., `["group_average_n_exp"]`)

### 6.1 Required Excluded Bundle Fields

Every bundle in top-level `excluded_specimens` must contain:

- `ordinary_exclusion_reasons`
- `specimen_labels`
- `source_evidence`
- `reason_evidence`

`ordinary_exclusion_reasons` must be a non-empty list of unique strings.

`specimen_labels` must be a non-empty, sorted, de-duplicated list of the excluded CFST specimen labels represented by that bundle.

`source_evidence` must be a concise single-line explanation of why the bundle's members are non-ordinary, including page localization plus a table / figure / text locator.

Accepted page and locator wording may be English or Chinese, for example `Page 3 Table 4`, `Page 2 materials text`, `第3页表4`, `第2页正文`, or `Section 2.3 text`.

`reason_evidence` must contain:

- `page`
- `table_id`
- `figure_id`
- `table_image`
- `setup_image`
- `source`
- `raw_texts`

`reason_evidence.raw_texts` must be a non-empty list of unique source strings that justify the exclusion.

### 6.2 Ordinary Row Enumerations

`section_shape`:

- `square`
- `rectangular`
- `circular`
- `elliptical`
- `round-ended`
- `obround`

`loading_mode`:

- `axial`
- `eccentric`

`loading_pattern` (specimen level):

- `monotonic`
- `cyclic`
- `repeated`
- `unknown`

`fc_basis`:

- `cube`
- `cylinder`
- `prism`
- `unknown`

`steel_type`:

- `carbon_steel`
- `stainless_steel`
- `other`
- `unknown`

`concrete_type`:

- `normal`
- `high_strength`
- `lightweight`
- `recycled`
- `self_consolidating`
- `uhpc`
- `other`
- `unknown`

`material_modifiers`: a list of strings (may be empty). Non-ordinary modifier values:

- `expansive_concrete`
- `rubber_concrete`
- `self_stressing_concrete`
- `reactive_powder`
- `fiber_reinforced`
- `polymer_modified`
- `geopolymer`
- `foamed_concrete`
- `other_modified_concrete`

Use an empty list `[]` for unmodified plain concrete. Never use `null`.
For ordinary specimen rows, keep the field present with `[]` rather than omitting it; the empty list is the explicit checked-empty outcome of the modifier scan.

### 6.3 Ordinary Row Field Semantics

- `ref_no`: fixed empty string `""`
- `specimen_label`: unique, non-empty specimen ID; when expanding a repeated-specimen group average, use the canonical form `reported_group_label-1 ... reported_group_label-q`
- `reported_group_label`: optional original paper label for a repeated-specimen group or original paper row label; omit or set `null` for simple one-to-one rows when no separate group label needs preserving
- `replicate_index`: optional positive integer replicate index used when one reported group label expands into multiple specimen rows; omit or set `null` for simple one-to-one rows
- `boundary_condition`: trace metadata for the specimen support/end condition; may be `null` or `unknown`
- `fc_value`: source concrete strength value in MPa
- `fc_type`: source concrete specimen description, for example `Cube 150`, `Cylinder 100x200`, or `Prism 150x150x300`
- `fc_basis`: basis category of `fc_value`; use `prism` for prism / axial-compression concrete-strength systems, not for CFST member loading mode

`fc_value` and `fc_type` must describe the same source measurement. If the paper reports a strength of 45.0 MPa measured on a 100 mm cube, store `fc_type = "Cube 100"` and `fc_value = 45.0`. If the paper has already converted that value to a 150 mm standard cube equivalent and states 42.75 MPa, store `fc_type = "Cube 150"` and `fc_value = 42.75`. Never pair an `fc_value` from one specimen basis with an `fc_type` from another.
Do not store shorthand notation or explanatory prose in `fc_type`. Values such as `fck`, `fcu`, `f'c`, `fc`, or `Prism-equivalent fck converted from Cube 150` are invalid `fc_type` strings. Put notation/basis explanation in `fc_basis` and `source_evidence` instead.
In Chinese GB/T 50010-type context, if the nearby grade notation and the reported measured strength clearly indicate the standard-cube system, store a cube-form `fc_type` consistent with that measured value even when the local symbol usage is sloppy. Record the notation mismatch in `source_evidence`, not in `fc_type`.

Concrete examples:

- source says `150 mm cube = 45.0 MPa`:
  `fc_basis = cube`, `fc_type = Cube 150`
- source says `100x200 cylinder = 60.0 MPa`:
  `fc_basis = cylinder`, `fc_type = Cylinder 100x200`
- in Chinese GB/T 50010 context, the stored value is `fck = 53.4 MPa` and the paper makes clear this is an axial/prism-system strength:
  `fc_basis = prism`, `fc_type = Prism`
  put the `fcu -> fck` or code-context explanation in `source_evidence`
- in Chinese GB/T 50010 context, the paper states the concrete was proportioned to `C30` and reports a measured compressive strength of `30.5 MPa`; the local grade context and value magnitude align with the standard-cube system rather than the GB/T prism/axial meaning of `fck`:
  `fc_basis = cube`, `fc_type = Cube 150`
  explain in `source_evidence` that the stored value is treated as a measured cube-strength value under the nearby `C30` grade context; if the paper locally labels that value with `fck`, record that notation issue in `source_evidence` rather than mirroring it in `fc_type`
- basis cannot be resolved defensibly:
  `fc_basis = unknown`, `fc_type = Unknown`

Invalid examples:

- `fc_type = fck`
- `fc_type = Prism-equivalent fck converted from Cube 150`

- `fy`: steel yield strength in MPa
- `fcy150`: normalized 150 mm cylinder compressive strength in MPa; keep the key present, but `null` is allowed during extraction when project-level conversion is deferred
- `material_modifiers`: list of additional concrete modification tags; use empty list `[]` for plain ordinary concrete; never `null`; any non-ordinary modifier (see section 2.2) makes the specimen non-ordinary regardless of `concrete_type`
- `r_ratio`: recycled aggregate ratio in percent, use `0` for normal concrete
- `b`, `h`, `t`, `r0`, `L`, `e1`, `e2`: numbers stored in mm
- `L`: project geometric specimen length in mm; do not reinterpret it as effective length
- `n_exp`: experimental ultimate load in kN
- when `n_exp` comes from an explicitly reported group average for repeated specimens, assign that same average to each defensibly identified member row, name those rows using the canonical `G-1 ... G-q` rule, mark `quality_flags += ["group_average_n_exp"]`, and make `source_evidence` say that the stored value is a group average
- when `reported_group_label` and `replicate_index` are present, they are traceability helpers; they do not replace the requirement that `specimen_label` stay unique and validator-safe
- `source_evidence`: concise human-readable trace string
- `loading_pattern`: the loading pattern for this specific ordinary specimen row (`monotonic`, `cyclic`, `repeated`, or `unknown`); when the paper uses a single loading pattern for all ordinary rows, every ordinary row receives the same value; when the paper mixes patterns, each ordinary row records its own
- `is_ordinary`: boolean indicating whether this kept row qualifies for the ordinary CFST dataset; for rows kept in `Group_A` / `Group_B` / `Group_C`, this must be `true`
- `ordinary_exclusion_reasons`: list of strings identifying why the specimen is non-ordinary; for rows kept in `Group_A` / `Group_B` / `Group_C`, this must be empty
- `quality_flags`: optional list of extraction-risk flags such as `group_average_n_exp`, `derived_L`, `unit_converted`, `context_inferred_fc_basis`; omit when empty

For recycled aggregate concrete, `r_ratio` must record the recycled aggregate replacement ratio `R%`.

Excluded bundle field semantics:

- `ordinary_exclusion_reasons`: shared exclusion reasons for all labels represented by the bundle
- `specimen_labels`: the exact excluded CFST labels represented by the bundle
- `source_evidence`: concise grouped explanation of why those labels are excluded
- `reason_evidence`: compact locator + raw exclusion text

### 6.3.1 Concrete-Strength Basis Resolution

Resolve `fc_basis` using the following priority order:

1. explicit statements in `Materials`, `Specimens`, `Concrete properties`, notation sections, specimen tables, and table footnotes
2. explicit specimen/test descriptions such as `150 mm cube`, `100x200 cylinder`, `ASTM C39 cylinder`, `JIS A 1108`, `JIS A 1132`, or prism / axial-compression concrete-strength wording
3. cited design-code or test-standard context, including code-defined grade notation such as Chinese GB/T 50010 `C30` / `C40` / `C50` / `C60` and Eurocode / EN 206 `C60/75`
4. shorthand strength symbols such as `f'c`, `Fc`, `fck`, `fc`, or bare notation whose governing code context is still unresolved

Apply these rules:

- before relying on symbols such as `fck`, `fc`, `f'c`, or `Fc`, first search the same sentence, paragraph, table header, and table footnote for nearby concrete-strength-grade signals such as `C30`, `C40`, `C50`, `C60`, or `C60/75`
- if the source explicitly says `cube`, `150 mm cube`, or equivalent standard cube wording, use `fc_basis = cube`
- if the source explicitly says `cylinder`, gives cylinder dimensions, or cites cylinder-based test standards, use `fc_basis = cylinder`
- if the source explicitly says prism strength, axial compressive strength, or uses the Chinese GB/T 50010 `fck` / `fc` axial-compression system, use `fc_basis = prism`
- treat explicit material/test descriptions as higher priority than shorthand grades in titles, abstracts, or specimen labels
- in Chinese GB/T 50010-type context, a nearby single-grade `C30` / `C40` / `C50` / `C60`-style notation is a code-defined cube-strength cue at the design-code layer and must be checked before a nearby bare `fck` / `fc` symbol is allowed to lock `fc_basis = prism`
- in the same Chinese GB/T 50010-type context, when a reported measured strength value is numerically consistent with the nearby cube-grade system and clearly inconsistent with the prism/axial reading of a nearby `fck` / `fc` symbol, you may resolve `fc_basis = cube`; explain the local notation mismatch explicitly in `source_evidence`
- when both cube and cylinder strengths are reported, store the basis/value that the paper explicitly uses in material parameters, constitutive calculations, or specimen-property tables; cite that decision in `source_evidence`

Country/context rules:

- China / GB/T 50010 context:
  - bare `C30`, `C40`, `C50`, `C60`, `C70`, and similar `C` grades are code-defined concrete strength grades in the 150 mm standard cube-strength system
  - these Chinese `Cxx` grades belong to the code-context layer and outrank nearby bare `fck` / `fc` symbol usage when the two conflict
  - `fck` and `fc` are axial/prism-system values in this context, not cylinder strengths
- Europe / EN 206 / Eurocode context:
  - `Cx/y` means `x = characteristic cylinder strength`, `y = cube strength`
  - `Cx/y` is a code-defined grade pair and belongs to the code-context layer, not the bare-symbol fallback layer
  - a bare single-value `C60` in a European paper is shorthand and remains ambiguous until the paper confirms which basis is being used
  - `fck` in Eurocode is the characteristic cylinder compressive strength; when a European paper writes `fck = 60 MPa` without a `Cx/y` grade, treat it as `fc_basis = cylinder`
- United States / ACI / ASTM C39 context:
  - `f'c` is cylinder-based specified compressive strength
  - a bare `C60` is not enough by itself to justify `cube` or `cylinder`
- Japan / `Fc` / JIS A 1108 / JIS A 1132 context:
  - `Fc` is normally tied to the Japanese cylinder-based concrete-strength system
  - a bare `C60` is not enough by itself to justify `cube` or `cylinder`

Cross-code symbol disambiguation:

The same symbol can carry completely different meanings across national codes. Do not interpret a symbol by its letters alone; always check which code system the paper is operating in.

- China `fck` is NOT Eurocode `fck`. In GB/T 50010, `fck` is the characteristic axial/prism compressive strength converted from the cube grade (e.g., C60 → fck = 38.5 MPa). In Eurocode, `fck` is the characteristic cylinder compressive strength (e.g., C60/75 → fck = 60 MPa).
- China `fc` is NOT US `f'c`. In GB/T 50010, `fc` is the axial compressive design value (lower than fck). In ACI, `f'c` is the specified compressive strength defaulting to cylinder tests.
- Japan `Fc` is a separate notation. It is the Japanese design standard strength tied to cylinder-based JIS testing, not interchangeable with Chinese `fc` or US `f'c`.

When a paper cites multiple national codes or compares specimens across code systems, resolve each specimen's `fc_basis` against the code that governs that specific specimen, not against a single assumed code for the whole paper.

Ambiguity rules:

- do not infer `cube` from a bare `C60`-style notation unless the paper is clearly operating in a Chinese GB/T 50010-type context or explicitly says cube
- do not infer `cylinder` from a bare `C60`-style notation unless the paper explicitly ties that notation to cylinder-based testing or code context
- if a nearby `Cxx` grade signal and a nearby `fck` / `fc` symbol point to different bases, and no explicit cube / cylinder / prism test description resolves the conflict, do not force the symbol-based basis; in Chinese GB/T 50010-type context, if the nearby grade context plus the reported measured value clearly support the cube-strength system, you may resolve `fc_basis = cube`; otherwise set `fc_basis = unknown`
- if the basis remains unresolved after checking the paper text, cited standards, and table notes, set `fc_basis = unknown`
- when `fc_basis = unknown`, keep `fcy150 = null` unless the paper itself provides a defensible normalized cylinder value
- for context-inferred decisions, make `source_evidence` cite the specific section/table/note and the standard or notation that justified the choice

### 6.4 Canonical Units

Store numeric values in the published JSON using these canonical units:

- `fc_value`, `fy`, `fcy150`: `MPa`
- `r_ratio`: `%`
- `b`, `h`, `t`, `r0`, `L`, `e1`, `e2`: `mm`
- `n_exp`: `kN`

## 7. Evidence Contract

### 7.1 Ordinary Specimen Evidence

Each ordinary specimen row requires a concise `source_evidence` string. No `evidence` object is required.

`source_evidence` must:

- be a non-empty single-line string
- identify the PDF page(s) and table/figure/text locator(s) for each stored value
- state explicitly when `n_exp` is a reported group average rather than an individually measured value
- explain derivations or notation resolutions inline (e.g., unit conversion, `r0 = D/2`, `fck` notation resolved to cube basis)

Accepted page/locator wording may be English or Chinese. `Page`, `页`, `Table`, `Fig.`, `Figure`, `text`, `section`, `表`, `图`, `正文`, and explicit section forms such as `第2.3节` are all valid locator styles.

Example:

```
"Page 9 Table 3 row SC-1 gives axial peak load 816 kN; Page 4 Table 2 gives SHS89×3.5 geometry; Page 3 Table 1 gives 28-day cylinder strength 40.8 MPa; Page 2 gives L=1000 mm and Page 5 Fig. 5 shows pinned-pinned axial column setup."
```

When page localization cannot be determined, state the best available locator rather than inventing a page number.

When a value is derived or the basis is inferred from code/notation context, explain the derivation or resolution in `source_evidence`.

### 7.2 Excluded Bundle Evidence

Every bundle in `excluded_specimens` must preserve:

- concise `source_evidence`
- structured `reason_evidence` containing:
  - `page`
  - `table_id`
  - `figure_id`
  - `table_image`
  - `setup_image`
  - `source`
  - `raw_texts`

`reason_evidence.raw_texts` must be a non-empty list of unique source strings that justify the exclusion.

### 7.3 Field-Level Reading Priority

Use these field-level source priorities when locating values in the PDF:

- `n_exp`: results table first; cite the averaging rule paragraph when the stored value is a group average
- `fc_value` / `fc_basis`: material-properties section, concrete-properties table, notation section, and nearby table footnotes before shorthand symbols
- `fy`: steel material-property table or specimen-property table before back-solving from stress ratios
- `L`: explicit specimen table/text first; explicit ratio/formula second; figure-based derivation only when the geometry labels make it unambiguous
- `loading_mode` / `loading_pattern`: setup figure plus loading-program section before abstract-level wording
- control-versus-CFST classification: specimen/design table plus nearby material/section description before ordinary-filter logic

When two sources disagree, prefer the higher-priority source and record the conflict in `source_evidence`.

## 8. Loading-Mode Rules

- determine paper-level loading mode from setup-figure evidence when available
- preserve specimen-level loading mode in every row
- if specimen `loading_mode = axial`, enforce `e1 = 0` and `e2 = 0`
- if specimen `loading_mode = eccentric`, at least one of `e1`, `e2` must be nonzero
- preserve the original signs of `e1` and `e2`
- `e1` and `e2` may have the same sign or opposite signs; sign alone must not be used to exclude an otherwise ordinary specimen
- mixed papers must still store each specimen row with its own loading mode

## 9. Numerical Rules

- use `scripts/safe_calc.py` for every conversion and derived value
- convert stored values to the canonical units defined above before writing JSON
- round numeric outputs to `0.001`
- enforce:
  - `fc_value > 0`
  - `fy > 0`
  - `fcy150 > 0` when `fcy150` is populated
  - `b > 0`
  - `h > 0`
  - `t > 0`
  - `L > 0`
  - `n_exp > 0`
  - `0 <= r_ratio <= 100`
- `t` must be strictly smaller than `min(b, h) / 2`
- keep `fcy150 = null` when the project defers strength-basis conversion; do not fabricate it during extraction

A specimen with `is_ordinary=true` must satisfy all of:

- `section_shape in {square, rectangular, circular, round-ended}`
- `steel_type = carbon_steel`
- `concrete_type in {normal, high_strength, recycled}`
- `material_modifiers` contains no non-ordinary modifier (see section 2.2)
- `loading_pattern = monotonic`

Paper-level Tier 1 preconditions (checked once, applied to all kept CFST specimens):

- `test_temperature = ambient`
- `loading_regime = static`

## 10. Length Rule

Determine `L` as the project geometric specimen length with this priority:

1. explicit specimen length in paper text/table/note
2. explicit formula or ratio with clear variable meaning
3. figure-based derivation with explicit geometry evidence, including steel-tube net height when the figure makes that geometry unambiguous

If the paper does not name `L` directly but the specimen/setup figure makes the steel-tube net height derivable, use that geometric length and record the basis in `source_evidence`.

Do not populate `L` when the geometry basis is ambiguous. Do not infer `L` from boundary-condition assumptions or effective-length formulas.

## 11. Direct PDF Reading

The worker reads the paper through the `pdf_pages` MCP tool. Each PDF page is rendered as a high-resolution image. The page image is the single source of truth for all specimen values including row boundaries, merged cells, units, symbols, and signs.

Do not extract specimen values from any text layer or OCR output. Read values directly from the rendered PDF page images.

## 12. Invalid And Failed Outputs

### 12.1 Invalid Paper

If paper is outside the experimental CFST-column scope:

- `is_valid=false`
- `is_ordinary_cfst=false`
- `ordinary_filter.include_in_dataset=false`
- `ref_info` may still contain bibliographic metadata when available
- `Group_A=[]`, `Group_B=[]`, `Group_C=[]`, `excluded_specimens=[]`

### 12.2 Processing Failure

When evidence is insufficient for a defensible extraction:

- stop with a clear failure reason
- do not fabricate row values
- keep intermediate output outside final published output
