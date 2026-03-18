#!/usr/bin/env python3
"""Validate one CFST extraction JSON against schema-v2.2 rules.

This strict skill variant requires the validator to run inside worker_sandbox.py.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any


def _assert_sandbox() -> None:
    if os.environ.get("CFST_SANDBOX") != "1":
        print("[FAIL] This script must run inside worker_sandbox.py (CFST_SANDBOX=1 not set).", file=sys.stderr)
        raise SystemExit(1)

EPS = 1e-3
SCHEMA_VERSION = "cfst-paper-extractor-v2.2"

TOP_LEVEL_KEYS = {
    "schema_version",
    "paper_id",
    "is_valid",
    "is_ordinary_cfst",
    "reason",
    "ordinary_filter",
    "ref_info",
    "paper_level",
    "Group_A",
    "Group_B",
    "Group_C",
    "excluded_specimens",
}

EXCLUDED_BUNDLE_KEYS = {
    "ordinary_exclusion_reasons",
    "specimen_labels",
    "source_evidence",
    "reason_evidence",
}

REASON_EVIDENCE_KEYS = {
    "page",
    "table_id",
    "figure_id",
    "table_image",
    "setup_image",
    "source",
    "raw_texts",
}

SPECIMEN_KEYS = {
    "ref_no",
    "specimen_label",
    "section_shape",
    "loading_mode",
    "loading_pattern",
    "boundary_condition",
    "fc_value",
    "fc_type",
    "fc_basis",
    "fy",
    "fcy150",
    "r_ratio",
    "steel_type",
    "concrete_type",
    "is_ordinary",
    "ordinary_exclusion_reasons",
    "b",
    "h",
    "t",
    "r0",
    "L",
    "e1",
    "e2",
    "n_exp",
    "source_evidence",
    "material_modifiers",
}

NUMERIC_FIELDS = {"fc_value", "fy", "r_ratio", "b", "h", "t", "r0", "L", "e1", "e2", "n_exp"}
NULLABLE_NUMERIC_FIELDS = {"fcy150"}

SECTION_SHAPES = {
    "square",
    "rectangular",
    "circular",
    "elliptical",
    "round-ended",
    "obround",
}
PAPER_LOADING_MODES = {"axial", "eccentric", "mixed", "unknown"}
ROW_LOADING_MODES = {"axial", "eccentric"}
TEST_TEMPERATURES = {"ambient", "elevated", "post_fire", "unknown"}
LOADING_REGIMES = {"static", "dynamic", "impact", "unknown"}
LOADING_PATTERNS = {"monotonic", "cyclic", "repeated", "mixed", "unknown"}
ROW_LOADING_PATTERNS = {"monotonic", "cyclic", "repeated", "unknown"}
FC_BASIS_ALLOWED = {"cube", "cylinder", "prism", "unknown"}
STEEL_TYPES = {"carbon_steel", "stainless_steel", "other", "unknown"}
CONCRETE_TYPES = {
    "normal",
    "high_strength",
    "lightweight",
    "recycled",
    "self_consolidating",
    "uhpc",
    "other",
    "unknown",
}
GROUP_TO_SHAPES = {
    "Group_A": {"square", "rectangular"},
    "Group_B": {"circular"},
    "Group_C": {"elliptical", "round-ended", "obround"},
}
ORDINARY_ALLOWED_SHAPES = {"square", "rectangular", "circular", "round-ended"}
ORDINARY_ALLOWED_CONCRETE_TYPES = {"normal", "high_strength", "recycled"}
ORDINARY_ALLOWED_SPECIAL_FACTORS = {"high_strength_concrete", "recycled_aggregate"}

NON_ORDINARY_MATERIAL_MODIFIERS = {
    "expansive_concrete",
    "rubber_concrete",
    "self_stressing_concrete",
    "reactive_powder",
    "fiber_reinforced",
    "polymer_modified",
    "geopolymer",
    "foamed_concrete",
    "other_modified_concrete",
}

FC_TYPE_ALLOWED_SHAPE_ONLY = {"cube", "cylinder", "prism", "unknown"}
FC_TYPE_SIZED_PATTERN = re.compile(
    r"^(cube|cylinder|prism)\s+\d+(\.\d+)?(?:\s*[x×*]\s*\d+(\.\d+)?){0,2}\s*(mm)?$",
    re.IGNORECASE,
)
FC_TYPE_DISALLOWED_SYMBOL_PATTERN = re.compile(r"\b(f'?c|fc'|fcu|fck|fcm|fcd)\b", re.IGNORECASE)
GROUP_AVERAGE_HINT_PATTERN = re.compile(r"(group\s*average|average|avg|mean|平均|均值)", re.IGNORECASE)


def _as_bool(value: str) -> bool:
    lowered = value.strip().lower()
    if lowered in {"1", "true", "yes", "y"}:
        return True
    if lowered in {"0", "false", "no", "n"}:
        return False
    raise argparse.ArgumentTypeError(f"Invalid boolean value: {value}")


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _roughly_equal(a: float, b: float, tol: float = EPS) -> bool:
    return abs(float(a) - float(b)) <= tol


def _has_3dp(value: float) -> bool:
    return abs(round(float(value), 3) - float(value)) <= 1e-6


def _has_control_chars(value: str) -> bool:
    return any(ord(ch) < 32 for ch in value)


def _validate_string_list(value: Any, tag: str, errors: list[str]) -> None:
    if not isinstance(value, list):
        errors.append(f"`{tag}` must be list.")
        return
    for idx, item in enumerate(value):
        if not isinstance(item, str):
            errors.append(f"`{tag}[{idx}]` must be string.")


def _validate_nonempty_line(value: Any, tag: str, errors: list[str]) -> None:
    if not isinstance(value, str):
        errors.append(f"`{tag}` must be string.")
        return
    if not value.strip():
        errors.append(f"`{tag}` must be non-empty.")
    if "\n" in value or "\r" in value:
        errors.append(f"`{tag}` must be single-line.")
    if _has_control_chars(value):
        errors.append(f"`{tag}` must not contain control characters.")


def _validate_nonempty_string_list(
    value: Any,
    tag: str,
    errors: list[str],
    *,
    require_unique: bool = False,
    require_sorted: bool = False,
) -> None:
    if not isinstance(value, list):
        errors.append(f"`{tag}` must be list.")
        return
    normalized: list[str] = []
    for idx, item in enumerate(value):
        if not isinstance(item, str):
            errors.append(f"`{tag}[{idx}]` must be string.")
            continue
        if not item.strip():
            errors.append(f"`{tag}[{idx}]` must be non-empty.")
            continue
        normalized.append(item)
    if require_unique and len(set(normalized)) != len(normalized):
        errors.append(f"`{tag}` must not contain duplicates.")
    if require_sorted and normalized != sorted(normalized):
        errors.append(f"`{tag}` must be sorted in ascending order.")


def _is_valid_fc_type(value: str) -> bool:
    text = value.strip()
    if not text:
        return False
    lowered = text.lower()
    if lowered in FC_TYPE_ALLOWED_SHAPE_ONLY:
        return True
    return FC_TYPE_SIZED_PATTERN.fullmatch(text) is not None


def _fc_type_implied_basis(fc_type_str: str) -> str | None:
    """Return the basis implied by fc_type, or None if ambiguous/unknown."""
    lowered = fc_type_str.strip().lower()
    if not lowered or lowered == "unknown":
        return None
    for basis in ("cube", "cylinder", "prism"):
        if lowered.startswith(basis):
            return basis
    return None


def _validate_ref_info(obj: Any, errors: list[str]) -> None:
    if not isinstance(obj, dict):
        errors.append("`ref_info` must be an object.")
        return

    required = ("title", "authors", "journal", "year", "citation_tag")
    for key in required:
        if key not in obj:
            errors.append(f"`ref_info.{key}` is required.")

    if "title" in obj and not isinstance(obj["title"], str):
        errors.append("`ref_info.title` must be string.")
    if "authors" in obj:
        if not isinstance(obj["authors"], list):
            errors.append("`ref_info.authors` must be list.")
        else:
            for idx, author in enumerate(obj["authors"]):
                if not isinstance(author, str):
                    errors.append(f"`ref_info.authors[{idx}]` must be string.")
    if "journal" in obj and not isinstance(obj["journal"], str):
        errors.append("`ref_info.journal` must be string.")
    if "year" in obj and not isinstance(obj["year"], int):
        errors.append("`ref_info.year` must be integer.")
    if "citation_tag" in obj and not isinstance(obj["citation_tag"], str):
        errors.append("`ref_info.citation_tag` must be string.")
    if "doi" in obj and obj["doi"] is not None and not isinstance(obj["doi"], str):
        errors.append("`ref_info.doi` must be string or null.")
    if "language" in obj and obj["language"] is not None and not isinstance(obj["language"], str):
        errors.append("`ref_info.language` must be string or null.")


def _validate_ordinary_filter(
    obj: Any,
    is_valid: bool | None,
    is_ordinary_cfst: bool | None,
    errors: list[str],
) -> None:
    if not isinstance(obj, dict):
        errors.append("`ordinary_filter` must be an object.")
        return

    for key in ("include_in_dataset", "ordinary_count", "total_count", "special_factors", "exclusion_reasons"):
        if key not in obj:
            errors.append(f"`ordinary_filter.{key}` is required.")

    include = obj.get("include_in_dataset")
    if include is not None and not isinstance(include, bool):
        errors.append("`ordinary_filter.include_in_dataset` must be boolean.")

    ordinary_count = obj.get("ordinary_count")
    if ordinary_count is not None and not isinstance(ordinary_count, int):
        errors.append("`ordinary_filter.ordinary_count` must be integer.")
    total_count = obj.get("total_count")
    if total_count is not None and not isinstance(total_count, int):
        errors.append("`ordinary_filter.total_count` must be integer.")

    if isinstance(ordinary_count, int) and isinstance(total_count, int):
        if ordinary_count < 0:
            errors.append("`ordinary_filter.ordinary_count` must be >= 0.")
        if total_count < 0:
            errors.append("`ordinary_filter.total_count` must be >= 0.")
        if ordinary_count > total_count:
            errors.append("`ordinary_filter.ordinary_count` cannot exceed `total_count`.")

    if "special_factors" in obj:
        _validate_string_list(obj["special_factors"], "ordinary_filter.special_factors", errors)
    if "exclusion_reasons" in obj:
        _validate_string_list(obj["exclusion_reasons"], "ordinary_filter.exclusion_reasons", errors)

    if isinstance(is_ordinary_cfst, bool) and isinstance(include, bool):
        if is_ordinary_cfst and not include:
            errors.append("`is_ordinary_cfst=true` requires `ordinary_filter.include_in_dataset=true`.")
        if not is_ordinary_cfst and include:
            errors.append("`ordinary_filter.include_in_dataset=true` requires `is_ordinary_cfst=true`.")
    if is_valid is False and include is True:
        errors.append("Invalid paper cannot be included in dataset.")


def _validate_setup_figure(obj: Any, errors: list[str]) -> None:
    if not isinstance(obj, dict):
        errors.append("`paper_level.setup_figure` must be an object.")
        return
    for key in ("figure_id", "image_path", "page"):
        if key not in obj:
            errors.append(f"`paper_level.setup_figure.{key}` is required.")
    if "figure_id" in obj and obj["figure_id"] is not None and not isinstance(obj["figure_id"], str):
        errors.append("`paper_level.setup_figure.figure_id` must be string or null.")
    if "image_path" in obj and obj["image_path"] is not None and not isinstance(obj["image_path"], str):
        errors.append("`paper_level.setup_figure.image_path` must be string or null.")
    if "page" in obj and obj["page"] is not None and not isinstance(obj["page"], int):
        errors.append("`paper_level.setup_figure.page` must be integer or null.")


def _validate_paper_level(obj: Any, errors: list[str]) -> None:
    if not isinstance(obj, dict):
        errors.append("`paper_level` must be an object.")
        return

    for key in (
        "loading_mode",
        "boundary_condition",
        "test_temperature",
        "loading_regime",
        "loading_pattern",
        "setup_figure",
        "expected_specimen_count",
        "notes",
    ):
        if key not in obj:
            errors.append(f"`paper_level.{key}` is required.")

    loading_mode = obj.get("loading_mode")
    if loading_mode is not None and loading_mode not in PAPER_LOADING_MODES:
        errors.append(f"`paper_level.loading_mode` invalid: {loading_mode}")
    test_temperature = obj.get("test_temperature")
    if test_temperature is not None and test_temperature not in TEST_TEMPERATURES:
        errors.append(f"`paper_level.test_temperature` invalid: {test_temperature}")
    loading_regime = obj.get("loading_regime")
    if loading_regime is not None and loading_regime not in LOADING_REGIMES:
        errors.append(f"`paper_level.loading_regime` invalid: {loading_regime}")
    loading_pattern = obj.get("loading_pattern")
    if loading_pattern is not None and loading_pattern not in LOADING_PATTERNS:
        errors.append(f"`paper_level.loading_pattern` invalid: {loading_pattern}")
    if "boundary_condition" in obj and obj["boundary_condition"] is not None and not isinstance(obj["boundary_condition"], str):
        errors.append("`paper_level.boundary_condition` must be string or null.")
    if "notes" in obj:
        _validate_string_list(obj["notes"], "paper_level.notes", errors)
    if "expected_specimen_count" in obj and obj["expected_specimen_count"] is not None:
        if not isinstance(obj["expected_specimen_count"], int):
            errors.append("`paper_level.expected_specimen_count` must be integer or null.")
        elif obj["expected_specimen_count"] < 0:
            errors.append("`paper_level.expected_specimen_count` must be >= 0.")
    if "setup_figure" in obj:
        _validate_setup_figure(obj["setup_figure"], errors)


def _validate_reason_evidence(tag: str, evidence: Any, errors: list[str]) -> None:
    if not isinstance(evidence, dict):
        errors.append(f"`{tag}.reason_evidence` must be an object.")
        return
    missing = REASON_EVIDENCE_KEYS - set(evidence.keys())
    if missing:
        errors.append(f"`{tag}.reason_evidence` missing keys: {sorted(missing)}")
    if "page" in evidence and evidence["page"] is not None and not isinstance(evidence["page"], int):
        errors.append(f"`{tag}.reason_evidence.page` must be integer or null.")
    for key in ("table_id", "figure_id", "table_image", "setup_image"):
        if key in evidence and evidence[key] is not None and not isinstance(evidence[key], str):
            errors.append(f"`{tag}.reason_evidence.{key}` must be string or null.")
    if "source" in evidence:
        _validate_nonempty_line(evidence["source"], f"{tag}.reason_evidence.source", errors)
    if "raw_texts" in evidence:
        _validate_nonempty_string_list(
            evidence["raw_texts"],
            f"{tag}.reason_evidence.raw_texts",
            errors,
            require_unique=True,
        )
        if isinstance(evidence["raw_texts"], list) and len(evidence["raw_texts"]) == 0:
            errors.append(f"`{tag}.reason_evidence.raw_texts` must be non-empty.")


def _validate_excluded_bundle(idx: int, bundle: Any, errors: list[str], warnings: list[str]) -> None:
    tag = f"excluded_specimens[{idx}]"
    if not isinstance(bundle, dict):
        errors.append(f"`{tag}` must be object.")
        return

    missing = EXCLUDED_BUNDLE_KEYS - set(bundle.keys())
    if missing:
        errors.append(f"`{tag}` missing keys: {sorted(missing)}")

    if "ordinary_exclusion_reasons" in bundle:
        _validate_nonempty_string_list(
            bundle["ordinary_exclusion_reasons"],
            f"{tag}.ordinary_exclusion_reasons",
            errors,
            require_unique=True,
        )
        if isinstance(bundle["ordinary_exclusion_reasons"], list) and len(bundle["ordinary_exclusion_reasons"]) == 0:
            errors.append(f"`{tag}.ordinary_exclusion_reasons` must be non-empty.")

    if "specimen_labels" in bundle:
        _validate_nonempty_string_list(
            bundle["specimen_labels"],
            f"{tag}.specimen_labels",
            errors,
            require_unique=True,
            require_sorted=True,
        )
        if isinstance(bundle["specimen_labels"], list) and len(bundle["specimen_labels"]) == 0:
            errors.append(f"`{tag}.specimen_labels` must be non-empty.")

    if "source_evidence" in bundle:
        _validate_nonempty_line(bundle["source_evidence"], f"{tag}.source_evidence", errors)
        if isinstance(bundle["source_evidence"], str):
            lowered = bundle["source_evidence"].lower()
            if "page" not in lowered:
                warnings.append(f"`{tag}.source_evidence` should include page localization.")
            if all(token not in lowered for token in ("table", "fig", "figure", "text section")):
                warnings.append(f"`{tag}.source_evidence` should include table/figure/text locator.")

    if "reason_evidence" in bundle:
        _validate_reason_evidence(tag, bundle["reason_evidence"], errors)


def _validate_specimen(
    group_name: str,
    idx: int,
    specimen: Any,
    errors: list[str],
    warnings: list[str],
    strict_rounding: bool,
) -> None:
    tag = f"{group_name}[{idx}]"
    if not isinstance(specimen, dict):
        errors.append(f"`{tag}` must be object.")
        return

    missing = SPECIMEN_KEYS - set(specimen.keys())
    if missing:
        errors.append(f"`{tag}` missing keys: {sorted(missing)}")

    for key in NUMERIC_FIELDS:
        if key in specimen and not _is_number(specimen[key]):
            errors.append(f"`{tag}.{key}` must be numeric.")
    for key in NULLABLE_NUMERIC_FIELDS:
        if key in specimen and specimen[key] is not None and not _is_number(specimen[key]):
            errors.append(f"`{tag}.{key}` must be numeric or null.")

    if "ref_no" in specimen:
        if not isinstance(specimen["ref_no"], str):
            errors.append(f"`{tag}.ref_no` must be string.")
        elif specimen["ref_no"] != "":
            errors.append(f"`{tag}.ref_no` must be empty string.")

    if "specimen_label" in specimen:
        if not isinstance(specimen["specimen_label"], str):
            errors.append(f"`{tag}.specimen_label` must be string.")
        elif not specimen["specimen_label"].strip():
            errors.append(f"`{tag}.specimen_label` must be non-empty.")

    if "reported_group_label" in specimen:
        value = specimen["reported_group_label"]
        if value is not None and not isinstance(value, str):
            errors.append(f"`{tag}.reported_group_label` must be string or null.")
        elif isinstance(value, str) and not value.strip():
            errors.append(f"`{tag}.reported_group_label` must be non-empty when provided.")

    if "replicate_index" in specimen:
        value = specimen["replicate_index"]
        if value is not None and not isinstance(value, int):
            errors.append(f"`{tag}.replicate_index` must be integer or null.")
        elif isinstance(value, int) and value <= 0:
            errors.append(f"`{tag}.replicate_index` must be >= 1 when provided.")

    if (
        isinstance(specimen.get("specimen_label"), str)
        and isinstance(specimen.get("reported_group_label"), str)
        and isinstance(specimen.get("replicate_index"), int)
    ):
        expected_label = f"{specimen['reported_group_label'].strip()}-{specimen['replicate_index']}"
        if specimen["specimen_label"].strip() != expected_label:
            warnings.append(
                f"`{tag}` has `reported_group_label`/`replicate_index`, but `specimen_label` "
                f"is not the canonical `{expected_label}` form."
            )

    if "section_shape" in specimen:
        shape = specimen["section_shape"]
        if not isinstance(shape, str):
            errors.append(f"`{tag}.section_shape` must be string.")
        elif shape not in SECTION_SHAPES:
            errors.append(f"`{tag}.section_shape` invalid: {shape}")
        elif shape not in GROUP_TO_SHAPES[group_name]:
            errors.append(f"`{tag}.section_shape` incompatible with {group_name}.")

    if "loading_mode" in specimen:
        mode = specimen["loading_mode"]
        if not isinstance(mode, str):
            errors.append(f"`{tag}.loading_mode` must be string.")
        elif mode not in ROW_LOADING_MODES:
            errors.append(f"`{tag}.loading_mode` invalid: {mode}")

    if "loading_pattern" in specimen:
        lp = specimen["loading_pattern"]
        if not isinstance(lp, str):
            errors.append(f"`{tag}.loading_pattern` must be string.")
        elif lp not in ROW_LOADING_PATTERNS:
            errors.append(f"`{tag}.loading_pattern` invalid: {lp}")

    if "boundary_condition" in specimen and specimen["boundary_condition"] is not None and not isinstance(specimen["boundary_condition"], str):
        errors.append(f"`{tag}.boundary_condition` must be string or null.")

    if "is_ordinary" in specimen and not isinstance(specimen["is_ordinary"], bool):
        errors.append(f"`{tag}.is_ordinary` must be boolean.")
    if "ordinary_exclusion_reasons" in specimen:
        _validate_string_list(specimen["ordinary_exclusion_reasons"], f"{tag}.ordinary_exclusion_reasons", errors)
        is_ord = specimen.get("is_ordinary")
        reasons = specimen["ordinary_exclusion_reasons"]
        if is_ord is True and isinstance(reasons, list) and len(reasons) > 0:
            errors.append(f"`{tag}.is_ordinary=true` must have empty `ordinary_exclusion_reasons`.")
        if is_ord is False and isinstance(reasons, list) and len(reasons) == 0:
            errors.append(f"`{tag}.is_ordinary=false` must have non-empty `ordinary_exclusion_reasons`.")
        if is_ord is False:
            errors.append(
                f"`{tag}.is_ordinary=false` rows must not remain in {group_name}; "
                "move them into `excluded_specimens`."
            )

    if "fc_type" in specimen:
        if not isinstance(specimen["fc_type"], str):
            errors.append(f"`{tag}.fc_type` must be string.")
        else:
            fc_type = specimen["fc_type"].strip()
            if not fc_type:
                errors.append(f"`{tag}.fc_type` must be non-empty.")
            elif FC_TYPE_DISALLOWED_SYMBOL_PATTERN.search(fc_type):
                errors.append(
                    f"`{tag}.fc_type` must not use symbolic notation like f'c/fcu/fck. "
                    "Use cube/cylinder/prism (with optional size) or Unknown."
                )
            elif not _is_valid_fc_type(fc_type):
                errors.append(
                    f"`{tag}.fc_type` invalid. Allowed forms: cube/cylinder/prism/Unknown "
                    "or sized forms like `Cylinder 100x200`."
                )

    if "fc_basis" in specimen:
        if not isinstance(specimen["fc_basis"], str):
            errors.append(f"`{tag}.fc_basis` must be string.")
        elif specimen["fc_basis"] not in FC_BASIS_ALLOWED:
            errors.append(f"`{tag}.fc_basis` invalid: {specimen['fc_basis']}")

    if "fc_type" in specimen and "fc_basis" in specimen:
        fc_type_str = specimen["fc_type"] if isinstance(specimen["fc_type"], str) else ""
        fc_basis_str = specimen["fc_basis"] if isinstance(specimen["fc_basis"], str) else ""
        implied = _fc_type_implied_basis(fc_type_str)
        if implied is not None and fc_basis_str != "unknown" and implied != fc_basis_str:
            errors.append(
                f"`{tag}.fc_type` '{specimen['fc_type']}' implies basis '{implied}' "
                f"but `fc_basis` is '{fc_basis_str}'. These must be consistent."
            )

    for key, allowed in (("steel_type", STEEL_TYPES), ("concrete_type", CONCRETE_TYPES)):
        if key in specimen:
            if not isinstance(specimen[key], str):
                errors.append(f"`{tag}.{key}` must be string.")
            elif specimen[key] not in allowed:
                errors.append(f"`{tag}.{key}` invalid: {specimen[key]}")

    if "source_evidence" in specimen:
        _validate_nonempty_line(specimen["source_evidence"], f"{tag}.source_evidence", errors)
        if isinstance(specimen["source_evidence"], str):
            lowered = specimen["source_evidence"].lower()
            if "page" not in lowered:
                warnings.append(f"`{tag}.source_evidence` should include page localization.")
            if all(token not in lowered for token in ("table", "fig", "figure", "text section")):
                warnings.append(f"`{tag}.source_evidence` should include table/figure/text locator.")

    if "quality_flags" in specimen:
        _validate_string_list(specimen["quality_flags"], f"{tag}.quality_flags", errors)

    if "material_modifiers" in specimen:
        _validate_string_list(specimen["material_modifiers"], f"{tag}.material_modifiers", errors)

    flags = specimen.get("quality_flags") if isinstance(specimen.get("quality_flags"), list) else []
    if "group_average_n_exp" in flags:
        source_evidence = specimen.get("source_evidence")
        if isinstance(source_evidence, str) and GROUP_AVERAGE_HINT_PATTERN.search(source_evidence) is None:
            warnings.append(
                f"`{tag}.source_evidence` should state that `n_exp` is a reported group average."
            )

    for key in ("fc_value", "fy", "b", "h", "t", "L", "n_exp"):
        if key in specimen and _is_number(specimen[key]) and specimen[key] <= 0:
            errors.append(f"`{tag}.{key}` must be > 0.")
    if "fcy150" in specimen and _is_number(specimen["fcy150"]) and specimen["fcy150"] <= 0:
        errors.append(f"`{tag}.fcy150` must be > 0 when populated.")
    if "r_ratio" in specimen and _is_number(specimen["r_ratio"]):
        if specimen["r_ratio"] < 0 or specimen["r_ratio"] > 100:
            errors.append(f"`{tag}.r_ratio` must be between 0 and 100.")
    if all(k in specimen and _is_number(specimen[k]) for k in ("b", "h", "t")):
        if specimen["t"] >= min(specimen["b"], specimen["h"]) / 2.0:
            errors.append(f"`{tag}.t` must be smaller than min(b, h)/2.")
    if "r0" in specimen and _is_number(specimen["r0"]) and specimen["r0"] < 0:
        errors.append(f"`{tag}.r0` must be >= 0.")

    if group_name == "Group_B":
        if all(k in specimen and _is_number(specimen[k]) for k in ("b", "h")):
            if not _roughly_equal(specimen["b"], specimen["h"]):
                errors.append(f"`{tag}` must satisfy b == h for Group_B.")
        if all(k in specimen and _is_number(specimen[k]) for k in ("h", "r0")):
            if not _roughly_equal(specimen["r0"], specimen["h"] / 2.0):
                errors.append(f"`{tag}.r0` must equal h/2 for Group_B.")

    if group_name == "Group_C":
        if all(k in specimen and _is_number(specimen[k]) for k in ("b", "h")):
            if specimen["b"] + EPS < specimen["h"]:
                errors.append(f"`{tag}` must satisfy b >= h for Group_C.")
        if all(k in specimen and _is_number(specimen[k]) for k in ("h", "r0")):
            if not _roughly_equal(specimen["r0"], specimen["h"] / 2.0):
                errors.append(f"`{tag}.r0` must equal h/2 for Group_C.")

    if "loading_mode" in specimen and all(k in specimen and _is_number(specimen[k]) for k in ("e1", "e2")):
        if specimen["loading_mode"] == "axial":
            if not (_roughly_equal(specimen["e1"], 0.0) and _roughly_equal(specimen["e2"], 0.0)):
                errors.append(f"`{tag}` axial row must have e1=e2=0.")
        elif specimen["loading_mode"] == "eccentric":
            if _roughly_equal(specimen["e1"], 0.0) and _roughly_equal(specimen["e2"], 0.0):
                errors.append(f"`{tag}` eccentric row cannot have both e1 and e2 equal to 0.")

    for key in NUMERIC_FIELDS | NULLABLE_NUMERIC_FIELDS:
        if key in specimen and _is_number(specimen[key]) and not _has_3dp(specimen[key]):
            msg = f"`{tag}.{key}` is not rounded to 0.001: {specimen[key]}"
            if strict_rounding:
                errors.append(msg)
            else:
                warnings.append(msg)


def _iter_specimens(payload: dict[str, Any]):
    for group_name in ("Group_A", "Group_B", "Group_C"):
        group = payload.get(group_name, [])
        if isinstance(group, list):
            for idx, specimen in enumerate(group):
                if isinstance(specimen, dict):
                    yield group_name, idx, specimen


def _count_excluded_members(payload: dict[str, Any]) -> int:
    total = 0
    bundles = payload.get("excluded_specimens", [])
    if isinstance(bundles, list):
        for bundle in bundles:
            if isinstance(bundle, dict):
                labels = bundle.get("specimen_labels")
                if isinstance(labels, list):
                    total += len(labels)
    return total


def _validate_specimen_ordinary(
    tag: str, specimen: dict[str, Any], tier1_pass: bool, errors: list[str], warnings: list[str]
) -> None:
    """Validate per-specimen ordinary flag consistency."""
    is_ord = specimen.get("is_ordinary")
    if not isinstance(is_ord, bool):
        return

    if not tier1_pass and is_ord is True:
        errors.append(
            f"`{tag}.is_ordinary=true` but paper-level Tier 1 preconditions failed."
        )

    if is_ord is True:
        shape = specimen.get("section_shape")
        if shape not in ORDINARY_ALLOWED_SHAPES:
            errors.append(f"`{tag}.is_ordinary=true` but section_shape '{shape}' not allowed.")
        if specimen.get("steel_type") != "carbon_steel":
            errors.append(f"`{tag}.is_ordinary=true` but steel_type is not carbon_steel.")
        concrete_type = specimen.get("concrete_type")
        if concrete_type not in ORDINARY_ALLOWED_CONCRETE_TYPES:
            errors.append(f"`{tag}.is_ordinary=true` but concrete_type '{concrete_type}' not allowed.")
        if specimen.get("loading_pattern") != "monotonic":
            errors.append(f"`{tag}.is_ordinary=true` but loading_pattern is not monotonic.")
        r_ratio = specimen.get("r_ratio")
        if concrete_type == "recycled":
            if not _is_number(r_ratio) or (isinstance(r_ratio, (int, float)) and r_ratio <= 0):
                errors.append(f"`{tag}.is_ordinary=true` recycled concrete must have r_ratio > 0.")
        modifiers = specimen.get("material_modifiers", [])
        if isinstance(modifiers, list):
            bad = [m for m in modifiers if m in NON_ORDINARY_MATERIAL_MODIFIERS]
            if bad:
                errors.append(
                    f"`{tag}.is_ordinary=true` but material_modifiers contains non-ordinary factors: {bad}."
                )

    r_ratio = specimen.get("r_ratio")
    concrete_type = specimen.get("concrete_type")
    if concrete_type != "recycled" and _is_number(r_ratio) and isinstance(r_ratio, (int, float)) and r_ratio > 0:
        warnings.append(
            f"`{tag}.r_ratio` > 0 but `concrete_type` is {concrete_type}; "
            "use `recycled` when recycled aggregate is the primary type."
        )


def _validate_ordinary_scope(payload: dict[str, Any], errors: list[str], warnings: list[str]) -> None:
    """Two-tier specimen-level ordinary validation."""
    paper_level = payload.get("paper_level")
    if not isinstance(paper_level, dict):
        return

    # Tier 1: paper-level preconditions
    tier1_pass = True
    if paper_level.get("test_temperature") != "ambient":
        tier1_pass = False
    if paper_level.get("loading_regime") != "static":
        tier1_pass = False

    # Per-specimen ordinary checks
    actual_ordinary_count = 0
    ordinary_group_count = 0
    for group_name, idx, specimen in _iter_specimens(payload):
        tag = f"{group_name}[{idx}]"
        ordinary_group_count += 1
        _validate_specimen_ordinary(tag, specimen, tier1_pass, errors, warnings)
        if specimen.get("is_ordinary") is True:
            actual_ordinary_count += 1
    total_count = ordinary_group_count + _count_excluded_members(payload)

    # Cross-check is_ordinary_cfst against specimen flags
    has_ordinary = actual_ordinary_count > 0
    is_ordinary_cfst = payload.get("is_ordinary_cfst")
    if isinstance(is_ordinary_cfst, bool):
        if is_ordinary_cfst and not has_ordinary:
            errors.append("`is_ordinary_cfst=true` but no specimen has `is_ordinary=true`.")
        if not is_ordinary_cfst and has_ordinary:
            errors.append("`is_ordinary_cfst=false` but some specimens have `is_ordinary=true`.")

    # Cross-check ordinary_filter counts
    ordinary_filter = payload.get("ordinary_filter")
    if isinstance(ordinary_filter, dict):
        of_count = ordinary_filter.get("ordinary_count")
        if isinstance(of_count, int) and of_count != actual_ordinary_count:
            errors.append(
                f"`ordinary_filter.ordinary_count` is {of_count} but actual count "
                f"of `is_ordinary=true` specimens is {actual_ordinary_count}."
            )
        of_total = ordinary_filter.get("total_count")
        if isinstance(of_total, int) and of_total != total_count:
            errors.append(
                f"`ordinary_filter.total_count` is {of_total} but actual specimen "
                f"count is {total_count}."
            )


def validate_payload(
    payload: Any,
    expect_valid: bool | None,
    strict_rounding: bool,
    expect_count: int | None,
) -> tuple[list[str], list[str], int]:
    errors: list[str] = []
    warnings: list[str] = []

    if not isinstance(payload, dict):
        return ["Top-level JSON must be object."], warnings, 0

    missing_top = TOP_LEVEL_KEYS - set(payload.keys())
    if missing_top:
        errors.append(f"Missing top-level keys: {sorted(missing_top)}")

    if "schema_version" in payload:
        if not isinstance(payload["schema_version"], str):
            errors.append("`schema_version` must be string.")
        elif payload["schema_version"] != SCHEMA_VERSION:
            errors.append(
                f"`schema_version` must be `{SCHEMA_VERSION}`, got `{payload['schema_version']}`."
            )
    if "paper_id" in payload:
        if not isinstance(payload["paper_id"], str):
            errors.append("`paper_id` must be string.")
        elif not payload["paper_id"].strip():
            errors.append("`paper_id` must be non-empty.")

    if "is_valid" in payload and not isinstance(payload["is_valid"], bool):
        errors.append("`is_valid` must be boolean.")
    if "is_ordinary_cfst" in payload and not isinstance(payload["is_ordinary_cfst"], bool):
        errors.append("`is_ordinary_cfst` must be boolean.")
    if "reason" in payload:
        _validate_nonempty_line(payload["reason"], "reason", errors)

    for group_name in ("Group_A", "Group_B", "Group_C"):
        if group_name in payload and not isinstance(payload[group_name], list):
            errors.append(f"`{group_name}` must be list.")
    if "excluded_specimens" in payload and not isinstance(payload["excluded_specimens"], list):
        errors.append("`excluded_specimens` must be list.")

    if "ordinary_filter" in payload:
        _validate_ordinary_filter(
            payload["ordinary_filter"],
            payload.get("is_valid"),
            payload.get("is_ordinary_cfst"),
            errors,
        )
    if "ref_info" in payload:
        _validate_ref_info(payload["ref_info"], errors)
    if "paper_level" in payload:
        _validate_paper_level(payload["paper_level"], errors)

    if expect_valid is not None and "is_valid" in payload and payload["is_valid"] != expect_valid:
        errors.append(f"`is_valid` expected {expect_valid}, got {payload['is_valid']}.")

    total = 0
    label_index: dict[str, list[str]] = defaultdict(list)
    for group_name in ("Group_A", "Group_B", "Group_C"):
        group = payload.get(group_name, [])
        if isinstance(group, list):
            total += len(group)
            for idx, specimen in enumerate(group):
                _validate_specimen(group_name, idx, specimen, errors, warnings, strict_rounding)
                tag = f"{group_name}[{idx}]"
                if isinstance(specimen, dict) and isinstance(specimen.get("specimen_label"), str):
                    label = specimen["specimen_label"].strip()
                    if label:
                        label_index[label].append(tag)

    bundles = payload.get("excluded_specimens", [])
    if isinstance(bundles, list):
        for idx, bundle in enumerate(bundles):
            _validate_excluded_bundle(idx, bundle, errors, warnings)
            if isinstance(bundle, dict):
                labels = bundle.get("specimen_labels")
                if isinstance(labels, list):
                    total += len(labels)
                    for label_idx, label in enumerate(labels):
                        if isinstance(label, str) and label.strip():
                            tag = f"excluded_specimens[{idx}].specimen_labels[{label_idx}]"
                            label_index[label.strip()].append(tag)

    for label, tags in label_index.items():
        if len(tags) > 1:
            errors.append(f"`specimen_label` duplicated across rows: '{label}' in {tags}.")

    expected_from_payload = None
    paper_level = payload.get("paper_level")
    if isinstance(paper_level, dict):
        expected_from_payload = paper_level.get("expected_specimen_count")
        if isinstance(expected_from_payload, int) and expected_from_payload != total:
            errors.append(
                f"`paper_level.expected_specimen_count` expected {expected_from_payload}, got {total}."
            )

    if expect_count is not None and total != expect_count:
        errors.append(f"`specimen` total expected {expect_count}, got {total}.")

    if payload.get("is_valid") is True and total == 0:
        errors.append("`is_valid=true` but kept CFST specimen count is 0.")
    if payload.get("is_valid") is False and total > 0:
        errors.append("`is_valid=false` requires `Group_A`/`Group_B`/`Group_C` and `excluded_specimens` to be empty.")

    if payload.get("is_valid") is False and payload.get("is_ordinary_cfst") is True:
        errors.append("Invalid paper cannot be marked as ordinary CFST.")

    _validate_ordinary_scope(payload, errors, warnings)

    return errors, warnings, total


def main() -> int:
    _assert_sandbox()
    parser = argparse.ArgumentParser(
        description="Validate single-paper CFST extraction JSON v2.2. Requires CFST_SANDBOX=1."
    )
    parser.add_argument("--json-path", required=True, help="Path to extraction JSON file.")
    parser.add_argument(
        "--expect-valid",
        default=None,
        type=_as_bool,
        help="Optional expected value for `is_valid` (true/false).",
    )
    parser.add_argument(
        "--strict-rounding",
        action="store_true",
        help="Fail when numeric fields are not rounded to 0.001.",
    )
    parser.add_argument(
        "--expect-count",
        type=int,
        default=None,
        help="Optional expected total kept CFST count across Group_A/B/C plus excluded_specimens.",
    )
    args = parser.parse_args()

    json_path = Path(args.json_path)
    if not json_path.exists():
        print(f"[FAIL] JSON file not found: {json_path}")
        return 1

    try:
        payload = json.loads(json_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"[FAIL] Invalid JSON: {exc}")
        return 1

    errors, warnings, total = validate_payload(
        payload,
        args.expect_valid,
        args.strict_rounding,
        args.expect_count,
    )

    print(f"[INFO] Kept CFST specimen count: {total}")
    if warnings:
        print("[WARN] Validation warnings:")
        for msg in warnings:
            print(f"- {msg}")

    if errors:
        print("[FAIL] Validation errors:")
        for msg in errors:
            print(f"- {msg}")
        return 1

    print("[OK] Validation passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
