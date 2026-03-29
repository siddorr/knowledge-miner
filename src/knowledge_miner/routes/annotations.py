from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..ai_filter import (
    assign_approved_structured_tags_to_paper,
    assign_approved_tags_to_paper,
    generate_paper_summary,
    generate_paper_tags,
    generate_structured_paper_tags,
)
from ..auth import require_api_key
from ..config import settings
from ..db import SessionLocal, get_db
from ..models import (
    PaperAnnotation,
    ParsedDocument,
    Run,
    SessionProfile,
    SessionSummarySettings,
    SessionTagCandidate,
    SessionTagCatalog,
    SessionTagSpec,
    Source,
)
from ..rate_limit import require_rate_limit
from ..schemas import (
    PaperAnnotationOut,
    PaperAnnotationsListResponse,
    PaperAnnotationUpdateRequest,
    SessionSummaryEditorConfig,
    SessionSummarySettingsOut,
    SessionSummarySettingsUpdateRequest,
    SessionTagCandidateOut,
    SessionTagCandidateGroupOut,
    SessionTagCatalogOut,
    SessionTagCatalogUpdateRequest,
    SessionTagSpecConfig,
    SessionTagSpecOut,
    SessionTagSpecUpdateRequest,
    SessionTagReviewOut,
    SessionTagWorkflowRequest,
    SessionTagWorkflowResponse,
    SuggestedTagDismissRequest,
    SuggestedTagPromoteRequest,
    SummaryGenerationBlockedOut,
    SummaryGenerationRequest,
    SummaryGenerationResponse,
    TagGenerationBlockedOut,
    TagGenerationRequest,
    TagGenerationResponse,
)

router = APIRouter(tags=["annotations"])
logger = logging.getLogger("knowledge_miner")

_LOCKED_EXTRACTION_RULES = [
    "Extract only facts explicitly stated in the provided text.",
    "Do not infer missing facts.",
    "Do not use outside knowledge.",
    "Keep values short, specific, and factual.",
    "Use null or [] for missing data.",
    "Return JSON only.",
]

_LOCKED_CRITICAL_CONSTRAINTS = [
    "Return exactly one valid JSON object and nothing else.",
    "The summary field is mandatory and must be non-empty.",
    "Do not add keys outside the generated schema.",
    "Do not wrap the answer in markdown fences.",
]

_DEFAULT_SUMMARY_SCHEMA_FIELDS = [
    {"id": "summary", "path": "summary", "label": "Summary", "description": "Concise human-readable summary.", "field_type": "string", "enabled": True, "object_item_fields": []},
    {"id": "fab_area", "path": "wastewater_source.fab_area", "label": "Fab Area", "description": "Fab section or wastewater origin area.", "field_type": "string", "enabled": True, "object_item_fields": []},
    {"id": "process_step", "path": "wastewater_source.process_step", "label": "Process Step", "description": "Process generating the wastewater.", "field_type": "string", "enabled": True, "object_item_fields": []},
    {"id": "tool_or_equipment", "path": "wastewater_source.tool_or_equipment", "label": "Tool or Equipment", "description": "Specific fab tool or equipment.", "field_type": "string", "enabled": True, "object_item_fields": []},
    {"id": "waste_stream_name", "path": "wastewater_source.waste_stream_name", "label": "Waste Stream Name", "description": "Named waste stream if given.", "field_type": "string", "enabled": True, "object_item_fields": []},
    {"id": "real_or_synthetic_water", "path": "wastewater_source.real_or_synthetic_water", "label": "Real or Synthetic Water", "description": "Water source type.", "field_type": "string", "enabled": True, "object_item_fields": []},
    {"id": "water_source_details", "path": "wastewater_source.water_source_details", "label": "Water Source Details", "description": "Short factual origin description.", "field_type": "string", "enabled": True, "object_item_fields": []},
    {"id": "components", "path": "water_composition.components", "label": "Components", "description": "Reported composition components.", "field_type": "object_list", "enabled": True, "object_item_fields": ["component", "value", "unit", "context"]},
    {"id": "water_quality_parameters", "path": "water_composition.water_quality_parameters", "label": "Water Quality Parameters", "description": "Reported water-quality parameters.", "field_type": "object_list", "enabled": True, "object_item_fields": ["parameter", "value", "unit", "context"]},
    {"id": "target_contaminants", "path": "treatment_target.target_contaminants_or_parameters", "label": "Target Contaminants or Parameters", "description": "Treatment targets.", "field_type": "string_list", "enabled": True, "object_item_fields": []},
    {"id": "technology_name", "path": "treatment_technology.technology_name", "label": "Technology Name", "description": "Named treatment process.", "field_type": "string", "enabled": True, "object_item_fields": []},
    {"id": "technology_category", "path": "treatment_technology.technology_category", "label": "Technology Category", "description": "Technology category.", "field_type": "string", "enabled": True, "object_item_fields": []},
    {"id": "used_real_wastewater", "path": "experiments.used_real_wastewater", "label": "Used Real Wastewater", "description": "Whether real wastewater was used.", "field_type": "boolean", "enabled": True, "object_item_fields": []},
    {"id": "used_synthetic_wastewater", "path": "experiments.used_synthetic_wastewater", "label": "Used Synthetic Wastewater", "description": "Whether synthetic wastewater was used.", "field_type": "boolean", "enabled": True, "object_item_fields": []},
    {"id": "experimental_scale", "path": "experiments.experimental_scale", "label": "Experimental Scale", "description": "Experimental scale.", "field_type": "string", "enabled": True, "object_item_fields": []},
    {"id": "removal_results", "path": "performance.removal_results", "label": "Removal Results", "description": "Numeric removal results.", "field_type": "object_list", "enabled": True, "object_item_fields": ["target", "metric", "value", "unit", "conditions"]},
    {"id": "key_findings", "path": "performance.key_findings", "label": "Key Findings", "description": "Short factual findings.", "field_type": "string_list", "enabled": True, "object_item_fields": []},
    {"id": "limitations", "path": "performance.limitations", "label": "Limitations", "description": "Explicit author-stated limitations.", "field_type": "string_list", "enabled": True, "object_item_fields": []},
]

_DEFAULT_SUMMARY_CONTROLLED_VALUES = [
    {"field_path": "wastewater_source.real_or_synthetic_water", "allowed_values": ["real", "synthetic", "both", "unclear"], "fallback_policy": "allow_free_text"},
    {"field_path": "treatment_technology.technology_category", "allowed_values": ["physical", "chemical", "physicochemical", "biological", "membrane", "electrochemical", "adsorption", "hybrid", "other", "unclear"], "fallback_policy": "allow_free_text"},
    {"field_path": "experiments.experimental_scale", "allowed_values": ["lab", "pilot", "full-scale", "unclear"], "fallback_policy": "allow_free_text"},
]

_DEFAULT_SUMMARY_EDITOR_CONFIG = {
    "summary_focus": (
        "Write a concise 1-3 sentence factual summary of the wastewater treatment topic, "
        "studied process, and explicit result using only paper-supported facts."
    ),
    "schema_fields": _DEFAULT_SUMMARY_SCHEMA_FIELDS,
    "controlled_values": _DEFAULT_SUMMARY_CONTROLLED_VALUES,
}

UNCATEGORIZED_TAGS_KEY = "uncategorized_tags"

_DEFAULT_TAG_SPEC_CONFIG = {
    "categories": [
        {
            "key": "material_or_product_tags",
            "label": "Material or Product Tags",
            "guidance": "Use for precipitates, products, or key media.",
            "allowed_tags": ["CaF2 precipitation", "calcium fluoride", "ion exchange resin", "struvite", "sulfuric acid concentration"],
            "allow_free_text": True,
        },
        {
            "key": "recovery_tags",
            "label": "Recovery Tags",
            "guidance": "Use for recovery, reuse, reclaim, or ZLD-related goals.",
            "allowed_tags": ["IPA recovery", "KI solution recovery", "cobalt recovery", "metal recovery", "phosphate recovery", "water reclamation", "wastewater reuse", "ultrapure water", "zero liquid discharge"],
            "allow_free_text": True,
        },
        {
            "key": "source_tags",
            "label": "Source Tags",
            "guidance": "Use for wastewater origin, fab area, or source stream.",
            "allowed_tags": ["semiconductor wastewater", "CMP wastewater", "hydrofluoric acid wastewater", "photolithography wastewater", "wafer cleaning wastewater", "plasma wet scrubber wastewater"],
            "allow_free_text": True,
        },
        {
            "key": "study_tags",
            "label": "Study Tags",
            "guidance": "Use for scale, modeling, or optimization study type.",
            "allowed_tags": ["pilot-scale study", "process simulation", "thermodynamic modeling", "treatment optimization"],
            "allow_free_text": True,
        },
        {
            "key": "target_tags",
            "label": "Target Tags",
            "guidance": "Use for contaminant, parameter, or removal objective.",
            "allowed_tags": ["fluoride removal", "PFOS removal", "SDS removal", "TOC removal", "TDS removal", "silica removal", "copper removal", "heavy metals removal", "nitrogen removal", "total nitrogen removal", "ammonium nitrogen removal", "orthophosphate removal", "organic removal", "photoresist removal", "contaminant degradation", "turbidity reduction"],
            "allow_free_text": True,
        },
        {
            "key": "technology_tags",
            "label": "Technology Tags",
            "guidance": "Use for treatment method or reactor/process used.",
            "allowed_tags": ["reverse osmosis", "ultrafiltration", "membrane bioreactor", "MBR-RO", "advanced oxidation process", "UV oxidation", "adsorption", "ion exchange", "electrochemical treatment", "electrocoagulation", "electrodeionization", "chemical precipitation", "coagulation", "flocculation", "dissolved air flotation", "air stripping", "vacuum evaporation", "crystallization", "sequence batch reactor", "fluidized bed reactor", "aerobic treatment", "aerobic denitrification", "biological nutrient removal", "biological treatment", "biosorption"],
            "allow_free_text": True,
        },
    ]
}


def _iso_or_none(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _normalize_tag(value: str) -> str:
    return " ".join(str(value or "").strip().split())


def _normalize_tag_list(values: list[str] | None) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in values or []:
        tag = _normalize_tag(raw)
        if not tag:
            continue
        tag = tag[:64]
        key = tag.lower()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(tag)
        if len(normalized) >= 30:
            break
    return normalized


def _default_tag_spec_config() -> dict:
    return json.loads(json.dumps(_DEFAULT_TAG_SPEC_CONFIG))


def _normalize_tag_category_key(value: str | None) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    normalized = []
    for ch in text:
        if ch.isalnum():
            normalized.append(ch)
        elif ch in {" ", "-", "."}:
            normalized.append("_")
        elif ch == "_":
            normalized.append(ch)
    key = "".join(normalized).strip("_")
    while "__" in key:
        key = key.replace("__", "_")
    if not key or not key[0].isalpha():
        return ""
    return key[:120]


def _normalize_tags_by_category(payload: dict | None, *, valid_category_keys: set[str] | None = None) -> dict[str, list[str]]:
    if not isinstance(payload, dict):
        return {}
    normalized: dict[str, list[str]] = {}
    for raw_key, raw_values in payload.items():
        key = _normalize_tag_category_key(raw_key)
        if not key:
            continue
        if valid_category_keys is not None and key not in valid_category_keys:
            continue
        values = _normalize_tag_list(raw_values if isinstance(raw_values, list) else [])
        if values:
            normalized[key] = values
    return normalized


def _flatten_tags_by_category(payload: dict | None) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    for tags in (_normalize_tags_by_category(payload).values()):
        for tag in tags:
            lowered = tag.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            ordered.append(tag)
    return ordered


def _normalize_tag_spec_config(payload: dict | None) -> dict:
    source = payload if isinstance(payload, dict) else _default_tag_spec_config()
    rows = source.get("categories")
    normalized_rows: list[dict] = []
    seen_keys: set[str] = set()
    if not isinstance(rows, list):
        rows = _default_tag_spec_config()["categories"]
    for row in rows:
        if not isinstance(row, dict):
            continue
        key = _normalize_tag_category_key(row.get("key"))
        if not key or key in seen_keys:
            continue
        seen_keys.add(key)
        label = str(row.get("label") or key.replace("_", " ").title()).strip()[:160] or key
        guidance = str(row.get("guidance") or "").strip()
        allowed_tags = _normalize_tag_list(row.get("allowed_tags") if isinstance(row.get("allowed_tags"), list) else [])
        normalized_rows.append(
            {
                "key": key,
                "label": label,
                "guidance": guidance,
                "allowed_tags": allowed_tags,
                "allow_free_text": bool(row.get("allow_free_text", False)),
            }
        )
    if not normalized_rows:
        normalized_rows = _default_tag_spec_config()["categories"]
    normalized_rows.sort(key=lambda item: (item["label"].lower(), item["key"].lower()))
    if not any(item["key"] == UNCATEGORIZED_TAGS_KEY for item in normalized_rows):
        normalized_rows.append(
            {
                "key": UNCATEGORIZED_TAGS_KEY,
                "label": "Uncategorized Tags",
                "guidance": "Fallback bucket for legacy flat tags or removed categories.",
                "allowed_tags": [],
                "allow_free_text": True,
            }
        )
        normalized_rows.sort(key=lambda item: (item["label"].lower(), item["key"].lower()))
    return {"categories": normalized_rows}


def _tag_spec_category_map(config: dict) -> dict[str, dict]:
    normalized = _normalize_tag_spec_config(config)
    return {row["key"]: row for row in normalized["categories"]}


def _build_tag_prompt_template_from_spec(config: dict) -> str:
    normalized = _normalize_tag_spec_config(config)
    schema = {"tags": {row["key"]: [] for row in normalized["categories"] if row["key"] != UNCATEGORIZED_TAGS_KEY}}
    category_lines = []
    for row in normalized["categories"]:
        if row["key"] == UNCATEGORIZED_TAGS_KEY:
            continue
        allowed = "\n".join(f'- "{tag}"' for tag in row["allowed_tags"]) if row["allowed_tags"] else "- No preferred tags configured."
        category_lines.append(
            f'"{row["key"]}":\n'
            f'- Label: {row["label"]}\n'
            f'- Guidance: {row["guidance"] or "No additional guidance."}\n'
            f'- Allow free text: {"yes" if row["allow_free_text"] else "no"}\n'
            f"- Allowed tags:\n{allowed}"
        )
    return (
        "You are a strict JSON tagging engine.\n\n"
        "Task:\n"
        "Assign structured paper tags from the provided scientific paper text.\n\n"
        "Return exactly one valid JSON object and nothing else.\n\n"
        "The output must follow this exact schema:\n\n"
        f"{json.dumps(schema, indent=2)}\n\n"
        "Tagging rules:\n"
        "- Assign tags only when explicitly supported by the paper text.\n"
        "- Prefer tags from the allowed lists below.\n"
        "- If no allowed tag fits and that category allows free text, you may add a short free-text tag.\n"
        "- Keep free-text tags short, factual, and close to the paper wording.\n"
        "- Do not invent tags.\n"
        "- Do not duplicate the same concept across categories unless clearly needed.\n"
        "- Use arrays.\n"
        "- If no tag applies in a category, return [].\n"
        "- Prefer 0 to 5 tags per category.\n"
        "- Tags are supplementary labels only and must not replace structured extraction fields.\n\n"
        "Categories:\n"
        f"{chr(10).join(category_lines)}\n"
    )


def _tag_spec_settings(db: Session, session_id: str) -> SessionTagSpecOut:
    row = db.get(SessionTagSpec, session_id)
    config = _normalize_tag_spec_config(row.category_config_json if row is not None else None)
    prompt_template = row.prompt_template if row is not None else _build_tag_prompt_template_from_spec(config)
    return SessionTagSpecOut(
        session_id=session_id,
        category_config=SessionTagSpecConfig(**config),
        prompt_template=prompt_template,
    )


def _deep_copy_json(value: dict) -> dict:
    return json.loads(json.dumps(value))


def _default_summary_editor_config() -> dict:
    return json.loads(json.dumps(_DEFAULT_SUMMARY_EDITOR_CONFIG))


def _is_valid_schema_path(value: str) -> bool:
    parts = [part for part in value.split(".") if part]
    if not parts or ".".join(parts) != value:
        return False
    return all(part.replace("_", "").isalnum() and not part[0].isdigit() for part in parts)


def _normalize_item_field_names(values: list[str] | None) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in values or []:
        name = str(raw or "").strip()
        if not name or not _is_valid_schema_path(name):
            continue
        if "." in name:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(name)
    return normalized


def _normalize_summary_field_configs(values: list[dict] | None) -> list[dict]:
    defaults = {row["path"]: row for row in _DEFAULT_SUMMARY_SCHEMA_FIELDS}
    normalized: list[dict] = []
    seen_paths: set[str] = set()
    source_rows = values if isinstance(values, list) else _DEFAULT_SUMMARY_SCHEMA_FIELDS
    for index, raw in enumerate(source_rows):
        row = raw if isinstance(raw, dict) else {}
        path = str(row.get("path") or "").strip()
        if not _is_valid_schema_path(path):
            continue
        if path.lower() in seen_paths:
            continue
        field_type = str(row.get("field_type") or "string").strip()
        if field_type not in {"string", "boolean", "string_list", "object_list"}:
            field_type = "string"
        default = defaults.get(path, {})
        item_fields = _normalize_item_field_names(row.get("object_item_fields") if isinstance(row.get("object_item_fields"), list) else default.get("object_item_fields", []))
        if field_type != "object_list":
            item_fields = []
        normalized.append({
            "id": str(row.get("id") or default.get("id") or f"field_{index + 1}").strip()[:120] or f"field_{index + 1}",
            "path": path,
            "label": str(row.get("label") or default.get("label") or path.split(".")[-1].replace("_", " ").title()).strip()[:160] or path,
            "description": str(row.get("description") or default.get("description") or "").strip(),
            "field_type": field_type,
            "enabled": bool(row.get("enabled", default.get("enabled", True))),
            "object_item_fields": item_fields,
        })
        seen_paths.add(path.lower())
    if not any(row["path"] == "summary" for row in normalized):
        normalized.insert(0, _deep_copy_json(_DEFAULT_SUMMARY_SCHEMA_FIELDS[0]))
    for row in normalized:
        if row["path"] == "summary":
            row["enabled"] = True
            row["field_type"] = "string"
            row["object_item_fields"] = []
    return normalized


def _normalize_controlled_value_configs(values: list[dict] | None, valid_paths: set[str]) -> list[dict]:
    normalized: list[dict] = []
    seen_paths: set[str] = set()
    source_rows = values if isinstance(values, list) else _DEFAULT_SUMMARY_CONTROLLED_VALUES
    for raw in source_rows:
        row = raw if isinstance(raw, dict) else {}
        field_path = str(row.get("field_path") or "").strip()
        if field_path not in valid_paths or field_path.lower() in seen_paths:
            continue
        allowed_values = []
        seen_values: set[str] = set()
        for value in row.get("allowed_values") or []:
            text = str(value or "").strip()
            if not text:
                continue
            key = text.lower()
            if key in seen_values:
                continue
            seen_values.add(key)
            allowed_values.append(text[:80])
        fallback_policy = str(row.get("fallback_policy") or "allow_free_text").strip()
        if fallback_policy not in {"allow_free_text", "prefer_enum_only"}:
            fallback_policy = "allow_free_text"
        normalized.append({
            "field_path": field_path,
            "allowed_values": allowed_values,
            "fallback_policy": fallback_policy,
        })
        seen_paths.add(field_path.lower())
    return normalized


def _normalize_summary_editor_config(payload: dict | None) -> dict:
    base = _default_summary_editor_config()
    if not isinstance(payload, dict):
        return base
    if "schema_fields" not in payload and ("field_guidance" in payload or "extraction_rules" in payload or "critical_constraints" in payload):
        summary_focus = str(payload.get("summary_focus") or base["summary_focus"]).strip() or base["summary_focus"]
        return {
            "summary_focus": summary_focus,
            "schema_fields": _normalize_summary_field_configs(base["schema_fields"]),
            "controlled_values": _normalize_controlled_value_configs(base["controlled_values"], {row["path"] for row in base["schema_fields"]}),
        }
    schema_fields = _normalize_summary_field_configs(payload.get("schema_fields"))
    valid_paths = {row["path"] for row in schema_fields}
    return {
        "summary_focus": str(payload.get("summary_focus") or base["summary_focus"]).strip() or base["summary_focus"],
        "schema_fields": schema_fields,
        "controlled_values": _normalize_controlled_value_configs(payload.get("controlled_values"), valid_paths),
    }


def _default_value_for_summary_field(field: dict):
    if field["path"] == "summary":
        return ""
    if field["field_type"] in {"string_list", "object_list"}:
        return []
    return None


def _set_nested_value(target: dict, path: str, value) -> None:
    parts = path.split(".")
    cursor = target
    for part in parts[:-1]:
        next_value = cursor.get(part)
        if not isinstance(next_value, dict):
            next_value = {}
            cursor[part] = next_value
        cursor = next_value
    cursor[parts[-1]] = value


def _get_nested_value(source: dict, path: str):
    cursor = source
    for part in path.split("."):
        if not isinstance(cursor, dict) or part not in cursor:
            return None
        cursor = cursor[part]
    return cursor


def _summary_schema_template_from_config(config: dict) -> dict:
    normalized = {"summary": "string"}
    for field in config["schema_fields"]:
        if not field.get("enabled") or field["path"] == "summary":
            continue
        _set_nested_value(normalized, field["path"], _default_value_for_summary_field(field))
    return normalized


def _normalize_object_list_value(value, field: dict) -> list[dict]:
    if not isinstance(value, list):
        return []
    item_fields = field.get("object_item_fields") or []
    rows: list[dict] = []
    for raw in value:
        if not isinstance(raw, dict):
            continue
        row: dict[str, object | None] = {}
        for key in item_fields:
            item_value = raw.get(key)
            if isinstance(item_value, (dict, list)):
                row[key] = None
            elif item_value is None:
                row[key] = None
            else:
                row[key] = str(item_value)
        if row and any(item_value not in {None, ""} for item_value in row.values()):
            rows.append(row)
    return rows


def _normalize_summary_artifact(payload: dict, editor_config: dict | None = None) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("paper_summary_invalid_schema")
    normalized_config = _normalize_summary_editor_config(editor_config)
    summary = str(payload.get("summary", "")).strip()
    if not summary:
        raise ValueError("paper_summary_empty")
    normalized: dict = {"summary": summary}
    for field in normalized_config["schema_fields"]:
        if not field.get("enabled") or field["path"] == "summary":
            continue
        value = _get_nested_value(payload, field["path"])
        if field["field_type"] == "string":
            normalized_value = None if value is None or isinstance(value, (dict, list)) else str(value)
        elif field["field_type"] == "boolean":
            normalized_value = value if isinstance(value, bool) else None
        elif field["field_type"] == "string_list":
            normalized_value = [str(item) for item in value if not isinstance(item, (dict, list))] if isinstance(value, list) else []
        else:
            normalized_value = _normalize_object_list_value(value, field)
        _set_nested_value(normalized, field["path"], normalized_value)
    return normalized


def _format_schema_fields_guidance(config: dict) -> str:
    lines: list[str] = []
    for field in config["schema_fields"]:
        if not field.get("enabled") or field["path"] == "summary":
            continue
        description = field.get("description") or ""
        suffix = f" - {description}" if description else ""
        if field["field_type"] == "object_list" and field.get("object_item_fields"):
            suffix += f" (object item fields: {', '.join(field['object_item_fields'])})"
        lines.append(f"- {field['path']}: {field['label']}{suffix}")
    return "\n".join(lines) if lines else "- No additional extracted fields."


def _format_controlled_values_guidance(config: dict) -> str:
    if not config["controlled_values"]:
        return "- No custom controlled values configured."
    lines: list[str] = []
    for row in config["controlled_values"]:
        values = ", ".join(row["allowed_values"]) if row["allowed_values"] else "(no preferred values)"
        policy = "allow free text" if row["fallback_policy"] == "allow_free_text" else "prefer enum only"
        lines.append(f"- {row['field_path']}: {values} | fallback: {policy}")
    return "\n".join(lines)


def _build_prompt_template_from_editor_config(config: dict) -> str:
    normalized = _normalize_summary_editor_config(config)
    schema_template = _summary_schema_template_from_config(normalized)
    return (
        "You are a strict JSON extraction engine.\n\n"
        "Task:\n"
        "Extract structured data from the provided full scientific paper text about\n"
        "wastewater treatment in semiconductor fabrication facilities.\n\n"
        "The output must follow this exact root schema:\n\n"
        f"{json.dumps(schema_template, indent=2)}\n\n"
        "Summary focus:\n"
        f"{normalized['summary_focus']}\n\n"
        "Fields to extract:\n"
        f"{_format_schema_fields_guidance(normalized)}\n\n"
        "Controlled values:\n"
        f"{_format_controlled_values_guidance(normalized)}\n\n"
        "Extraction rules:\n"
        f"{chr(10).join(f'- {rule}' for rule in _LOCKED_EXTRACTION_RULES)}\n\n"
        "Critical constraints:\n"
        f"{chr(10).join(f'- {rule}' for rule in _LOCKED_CRITICAL_CONSTRAINTS)}\n"
    )


def _session_exists(db: Session, session_id: str) -> bool:
    return db.get(SessionProfile, session_id) is not None or db.scalar(select(Run.id).where(Run.session_id == session_id).limit(1)) is not None


def _session_run_ids(db: Session, session_id: str) -> list[str]:
    return db.scalars(select(Run.id).where(Run.session_id == session_id)).all()


def _session_source_map(db: Session, session_id: str, source_ids: list[str]) -> dict[str, Source]:
    if not source_ids:
        return {}
    run_ids = _session_run_ids(db, session_id)
    if not run_ids:
        return {}
    rows = db.scalars(
        select(Source).where(Source.run_id.in_(run_ids), Source.id.in_(source_ids), Source.accepted.is_(True))
    ).all()
    return {row.id: row for row in rows}


def _ensure_session_profile(db: Session, session_id: str) -> SessionProfile:
    row = db.get(SessionProfile, session_id)
    if row is not None:
        return row
    row = SessionProfile(session_id=session_id)
    db.add(row)
    db.flush()
    return row


def _accepted_session_sources(db: Session, session_id: str) -> list[Source]:
    run_ids = _session_run_ids(db, session_id)
    if not run_ids:
        return []
    return db.scalars(
        select(Source)
        .where(Source.run_id.in_(run_ids), Source.accepted.is_(True))
        .order_by(Source.created_at.asc(), Source.id.asc())
    ).all()


def _tag_assignment_snapshot(spec_config: dict, approved_tags_by_category: dict[str, list[str]]) -> str:
    return (
        "approved_tag_assignment:"
        f"{json.dumps(_normalize_tag_spec_config(spec_config), separators=(',', ':'))}:"
        f"{json.dumps(_normalize_tags_by_category(approved_tags_by_category), separators=(',', ':'))}"
    )


def _prompt_snapshot_hash(value: str | None) -> str:
    text = str(value or "")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def _approved_tags_by_category(db: Session, session_id: str) -> dict[str, list[str]]:
    rows = db.scalars(
        select(SessionTagCatalog)
        .where(SessionTagCatalog.session_id == session_id)
        .order_by(SessionTagCatalog.category_key.asc(), SessionTagCatalog.tag.asc())
    ).all()
    grouped: dict[str, list[str]] = {}
    for row in rows:
        grouped.setdefault(row.category_key or UNCATEGORIZED_TAGS_KEY, []).append(row.tag)
    return grouped


def _tag_review_out(db: Session, session_id: str) -> SessionTagReviewOut:
    profile = db.get(SessionProfile, session_id)
    spec_out = _tag_spec_settings(db, session_id)
    category_map = {row.key: row for row in spec_out.category_config.categories}
    rows = db.scalars(
        select(SessionTagCandidate)
        .where(SessionTagCandidate.session_id == session_id)
        .order_by(
            SessionTagCandidate.category_key.asc(),
            SessionTagCandidate.status.asc(),
            SessionTagCandidate.source_count.desc(),
            SessionTagCandidate.tag.asc(),
        )
    ).all()
    groups_by_key: dict[str, dict] = {}
    pending_total = approved_total = rejected_total = 0
    flat_candidates: list[SessionTagCandidateOut] = []
    for row in rows:
        key = row.category_key or UNCATEGORIZED_TAGS_KEY
        label = category_map.get(key).label if key in category_map else key.replace("_", " ").title()
        group = groups_by_key.setdefault(
            key,
            {
                "category_key": key,
                "category_label": label,
                "pending_count": 0,
                "approved_count": 0,
                "rejected_count": 0,
                "candidates": [],
            },
        )
        out = SessionTagCandidateOut(
            id=row.id,
            session_id=row.session_id,
            category_key=key,
            category_label=label,
            tag=row.tag,
            status=row.status,
            source_count=int(row.source_count or 0),
            updated_at=_iso_or_none(row.updated_at),
        )
        if row.status == "candidate":
            group["pending_count"] += 1
            pending_total += 1
            group["candidates"].append(out)
            flat_candidates.append(out)
        elif row.status == "approved":
            group["approved_count"] += 1
            approved_total += 1
        elif row.status == "rejected":
            group["rejected_count"] += 1
            rejected_total += 1
    groups = [
        SessionTagCandidateGroupOut(
            category_key=group["category_key"],
            category_label=group["category_label"],
            pending_count=group["pending_count"],
            approved_count=group["approved_count"],
            rejected_count=group["rejected_count"],
            candidates=group["candidates"],
        )
        for group in sorted(groups_by_key.values(), key=lambda item: (item["category_label"].lower(), item["category_key"].lower()))
    ]
    return SessionTagReviewOut(
        session_id=session_id,
        candidate_generation_status=(profile.tag_candidate_status if profile is not None else "none") or "none",
        candidate_generation_generated_at=_iso_or_none(profile.tag_candidate_generated_at) if profile is not None else None,
        candidate_generation_error=profile.tag_candidate_error if profile is not None else None,
        tag_assignment_status=(profile.tag_assignment_status if profile is not None else "none") or "none",
        tag_assignment_generated_at=_iso_or_none(profile.tag_assignment_generated_at) if profile is not None else None,
        tag_assignment_error=profile.tag_assignment_error if profile is not None else None,
        pending_count=pending_total,
        approved_count=approved_total,
        rejected_count=rejected_total,
        candidates=flat_candidates,
        groups=groups,
    )


def _parsed_text_rows(db: Session, source_ids: list[str]) -> dict[str, ParsedDocument]:
    if not source_ids:
        return {}
    rows = db.scalars(
        select(ParsedDocument)
        .where(
            ParsedDocument.source_id.in_(source_ids),
            ParsedDocument.status == "parsed",
            ParsedDocument.body_text.is_not(None),
        )
        .order_by(ParsedDocument.updated_at.desc(), ParsedDocument.created_at.desc(), ParsedDocument.id.desc())
    ).all()
    latest: dict[str, ParsedDocument] = {}
    for row in rows:
        latest.setdefault(row.source_id, row)
    return latest


def _annotation_row(db: Session, session_id: str, source_id: str) -> PaperAnnotation | None:
    return db.scalars(
        select(PaperAnnotation).where(PaperAnnotation.session_id == session_id, PaperAnnotation.source_id == source_id).limit(1)
    ).first()


def _annotation_tags_by_category(annotation: PaperAnnotation | None, kind: str) -> dict[str, list[str]]:
    if annotation is None:
        return {}
    if kind == "approved":
        categorized = _normalize_tags_by_category(annotation.approved_tags_by_category_json)
        legacy = _normalize_tag_list(annotation.approved_tags_json or [])
    else:
        categorized = _normalize_tags_by_category(annotation.freeform_tags_by_category_json)
        legacy = _normalize_tag_list(annotation.freeform_tags_json or [])
    if legacy:
        existing = {item.lower() for values in categorized.values() for item in values}
        extras = [tag for tag in legacy if tag.lower() not in existing]
        if extras:
            categorized[UNCATEGORIZED_TAGS_KEY] = _normalize_tag_list((categorized.get(UNCATEGORIZED_TAGS_KEY) or []) + extras)
    return categorized


def _apply_annotation_tag_payloads(
    annotation: PaperAnnotation,
    *,
    approved_tags_by_category: dict[str, list[str]] | None = None,
    freeform_tags_by_category: dict[str, list[str]] | None = None,
) -> None:
    if approved_tags_by_category is not None:
        annotation.approved_tags_by_category_json = approved_tags_by_category
        annotation.approved_tags_json = _flatten_tags_by_category(approved_tags_by_category)
    if freeform_tags_by_category is not None:
        annotation.freeform_tags_by_category_json = freeform_tags_by_category
        annotation.freeform_tags_json = _flatten_tags_by_category(freeform_tags_by_category)


def _annotation_out(
    session_id: str,
    source_id: str,
    annotation: PaperAnnotation | None,
    *,
    parsed_ready: bool,
) -> PaperAnnotationOut:
    summary_status_value = annotation.summary_status if annotation is not None else "none"
    tag_status_value = annotation.tag_suggestion_status if annotation is not None else "none"
    block_reason = None if parsed_ready else "parsed_text_required"
    can_generate_summary = parsed_ready and summary_status_value not in {"queued", "running"}
    can_generate_tags = parsed_ready and tag_status_value not in {"queued", "running"}
    return PaperAnnotationOut(
        session_id=session_id,
        source_id=source_id,
        freeform_tags=list(annotation.freeform_tags_json or []) if annotation is not None else [],
        approved_tags=list(annotation.approved_tags_json or []) if annotation is not None else [],
        freeform_tags_by_category=_annotation_tags_by_category(annotation, "freeform"),
        approved_tags_by_category=_annotation_tags_by_category(annotation, "approved"),
        ai_suggested_tags=list(annotation.ai_suggested_tags_json or []) if annotation is not None else [],
        ai_summary=annotation.ai_summary if annotation is not None else None,
        ai_summary_json=annotation.ai_summary_json if annotation is not None else None,
        summary_prompt_snapshot=annotation.summary_prompt_snapshot if annotation is not None else None,
        summary_model=annotation.summary_model if annotation is not None else None,
        summary_status=summary_status_value,
        tag_suggestion_status=tag_status_value,
        summary_generated_at=_iso_or_none(annotation.summary_generated_at) if annotation is not None else None,
        tag_suggestion_generated_at=_iso_or_none(annotation.tag_suggestion_generated_at) if annotation is not None else None,
        summary_error=annotation.summary_error if annotation is not None else None,
        tag_suggestion_error=annotation.tag_suggestion_error if annotation is not None else None,
        can_generate_summary=can_generate_summary,
        summary_block_reason=block_reason,
        can_generate_tags=can_generate_tags,
        tag_suggestion_block_reason=block_reason,
    )


def _get_or_create_annotation(db: Session, session_id: str, source_id: str) -> PaperAnnotation:
    existing = _annotation_row(db, session_id, source_id)
    if existing is not None:
        return existing
    created = PaperAnnotation(
        id=f"annot_{uuid.uuid4().hex[:12]}",
        session_id=session_id,
        source_id=source_id,
        freeform_tags_json=[],
        approved_tags_json=[],
        freeform_tags_by_category_json={},
        approved_tags_by_category_json={},
        ai_suggested_tags_json=[],
        summary_status="none",
        tag_suggestion_status="none",
    )
    db.add(created)
    return created


def _summary_settings(db: Session, session_id: str) -> SessionSummarySettingsOut:
    row = db.get(SessionSummarySettings, session_id)
    prompt_template = row.prompt_template if row is not None else _build_prompt_template_from_editor_config(_default_summary_editor_config())
    editor_config = _normalize_summary_editor_config(row.editor_config_json if row is not None else None)
    return SessionSummarySettingsOut(
        session_id=session_id,
        prompt_template=prompt_template,
        editor_config=SessionSummaryEditorConfig(**editor_config),
        current_global_summary_model=settings.ai_model,
    )


def _generate_summaries_task(session_id: str, source_ids: list[str], force_regenerate: bool) -> None:
    with SessionLocal() as db:
        profile = db.get(SessionProfile, session_id)
        session_context = profile.session_context if profile and profile.session_context else ""
        settings_row = db.get(SessionSummarySettings, session_id)
        default_prompt = settings_row.prompt_template if settings_row is not None else _build_prompt_template_from_editor_config(_default_summary_editor_config())
        default_editor_config = _normalize_summary_editor_config(settings_row.editor_config_json if settings_row is not None else None)
        parsed_rows = _parsed_text_rows(db, source_ids)
        for source_id in source_ids:
            annotation = _annotation_row(db, session_id, source_id)
            source = db.get(Source, source_id)
            parsed = parsed_rows.get(source_id)
            if annotation is None or source is None or parsed is None or not parsed.body_text:
                continue
            if annotation.summary_status == "completed" and not force_regenerate and annotation.ai_summary:
                continue
            prompt_template = annotation.summary_prompt_snapshot or default_prompt
            editor_config = _normalize_summary_editor_config(annotation.summary_editor_snapshot_json or default_editor_config)
            annotation.summary_status = "running"
            annotation.summary_error = None
            db.commit()
            try:
                result = generate_paper_summary(
                    session_context=session_context,
                    prompt_template=prompt_template,
                    title=source.title,
                    body_text=parsed.body_text,
                    doi=source.doi,
                    year=source.year,
                )
                normalized_artifact = _normalize_summary_artifact(result.artifact_json, editor_config)
                annotation.ai_summary_json = normalized_artifact
                annotation.ai_summary = str(normalized_artifact.get("summary") or "").strip()
                annotation.summary_status = "completed"
                annotation.summary_model = settings.ai_model
                annotation.summary_generated_at = datetime.now(UTC)
                annotation.summary_error = None
                db.commit()
                logger.info("paper_summary_completed session_id=%s source_id=%s structured=true", session_id, source_id)
            except Exception as exc:
                db.rollback()
                annotation = _annotation_row(db, session_id, source_id)
                if annotation is None:
                    continue
                if not annotation.ai_summary:
                    annotation.summary_status = "failed"
                else:
                    annotation.summary_status = "completed"
                annotation.summary_error = str(exc)
                db.commit()
                logger.warning("paper_summary_failed session_id=%s source_id=%s error=%s", session_id, source_id, exc)


def _generate_tags_task(session_id: str, source_ids: list[str], force_regenerate: bool) -> None:
    with SessionLocal() as db:
        profile = db.get(SessionProfile, session_id)
        session_context = profile.session_context if profile and profile.session_context else ""
        parsed_rows = _parsed_text_rows(db, source_ids)
        for source_id in source_ids:
            annotation = _annotation_row(db, session_id, source_id)
            source = db.get(Source, source_id)
            parsed = parsed_rows.get(source_id)
            if annotation is None or source is None or parsed is None or not parsed.body_text:
                continue
            if annotation.tag_suggestion_status == "completed" and not force_regenerate and annotation.ai_suggested_tags_json:
                continue
            annotation.tag_suggestion_status = "running"
            annotation.tag_suggestion_error = None
            db.commit()
            try:
                result = generate_paper_tags(
                    session_context=session_context,
                    title=source.title,
                    body_text=parsed.body_text,
                    doi=source.doi,
                    year=source.year,
                )
                annotation.ai_suggested_tags_json = _normalize_tag_list(result.tags)
                annotation.tag_suggestion_status = "completed"
                annotation.tag_suggestion_model = settings.ai_model
                annotation.tag_suggestion_generated_at = datetime.now(UTC)
                annotation.tag_suggestion_error = None
                db.commit()
                logger.info("paper_tags_completed session_id=%s source_id=%s", session_id, source_id)
            except Exception as exc:
                db.rollback()
                annotation = _annotation_row(db, session_id, source_id)
                if annotation is None:
                    continue
                if not annotation.ai_suggested_tags_json:
                    annotation.tag_suggestion_status = "failed"
                else:
                    annotation.tag_suggestion_status = "completed"
                annotation.tag_suggestion_error = str(exc)
                db.commit()
                logger.warning("paper_tags_failed session_id=%s source_id=%s error=%s", session_id, source_id, exc)


def _generate_session_tag_candidates_task(session_id: str, force_regenerate: bool) -> None:
    with SessionLocal() as db:
        profile = _ensure_session_profile(db, session_id)
        profile.tag_candidate_status = "running"
        profile.tag_candidate_error = None
        db.commit()

        spec_out = _tag_spec_settings(db, session_id)
        spec_config = spec_out.category_config.model_dump()
        valid_category_keys = {
            row["key"] for row in spec_config["categories"] if row["key"] != UNCATEGORIZED_TAGS_KEY
        }
        accepted_sources = _accepted_session_sources(db, session_id)
        parsed_rows = _parsed_text_rows(db, [row.id for row in accepted_sources])
        session_context = profile.session_context or ""
        aggregated: dict[tuple[str, str], set[str]] = {}
        failure_count = 0
        for source in accepted_sources:
            parsed = parsed_rows.get(source.id)
            if parsed is None or not parsed.body_text:
                continue
            try:
                result = generate_structured_paper_tags(
                    session_context=session_context,
                    category_config=spec_config["categories"],
                    prompt_template=spec_out.prompt_template,
                    title=source.title,
                    body_text=parsed.body_text,
                    doi=source.doi,
                    year=source.year,
                )
            except Exception as exc:
                failure_count += 1
                logger.warning("session_tag_candidate_generation_failed session_id=%s source_id=%s error=%s", session_id, source.id, exc)
                continue
            for category_key, tags in result.tags_by_category.items():
                if category_key not in valid_category_keys:
                    continue
                for tag in _normalize_tag_list(tags):
                    aggregated.setdefault((category_key, tag), set()).add(source.id)

        existing_rows = db.scalars(select(SessionTagCandidate).where(SessionTagCandidate.session_id == session_id)).all()
        existing_by_key = {(row.category_key or UNCATEGORIZED_TAGS_KEY, row.tag.lower()): row for row in existing_rows}
        approved_catalog = {
            ((row.category_key or UNCATEGORIZED_TAGS_KEY), row.tag.lower()): row.tag
            for row in db.scalars(select(SessionTagCatalog).where(SessionTagCatalog.session_id == session_id)).all()
        }
        seen_keys = set()
        now = datetime.now(UTC)
        for (category_key, tag), source_ids in aggregated.items():
            lowered = tag.lower()
            seen_keys.add((category_key, lowered))
            row = existing_by_key.get((category_key, lowered))
            if row is None:
                row = SessionTagCandidate(
                    id=f"stagc_{uuid.uuid4().hex[:12]}",
                    session_id=session_id,
                    category_key=category_key,
                    tag=tag,
                    status="approved" if (category_key, lowered) in approved_catalog else "candidate",
                    source_count=len(source_ids),
                    source_ids_json=sorted(source_ids),
                )
                db.add(row)
                existing_by_key[(category_key, lowered)] = row
            else:
                row.source_count = len(source_ids)
                row.source_ids_json = sorted(source_ids)
                if row.status == "candidate" and (category_key, lowered) in approved_catalog:
                    row.status = "approved"
            row.updated_at = now

        for key, row in existing_by_key.items():
            if row.status == "candidate" and key not in seen_keys:
                db.delete(row)

        profile.tag_candidate_status = "completed"
        profile.tag_candidate_generated_at = now
        profile.tag_candidate_error = (
            f"{failure_count} paper tag generations failed during candidate generation."
            if failure_count
            else None
        )
        db.commit()
        logger.info(
            "session_tag_candidates_completed session_id=%s candidate_count=%s failure_count=%s",
            session_id,
            len(aggregated),
            failure_count,
        )


def _apply_approved_tags_task(session_id: str, force_regenerate: bool, source_ids: list[str] | None = None) -> None:
    with SessionLocal() as db:
        profile = _ensure_session_profile(db, session_id)
        profile.tag_assignment_status = "running"
        profile.tag_assignment_error = None
        db.commit()

        spec_out = _tag_spec_settings(db, session_id)
        spec_config = spec_out.category_config.model_dump()
        approved_tags = _approved_tags_by_category(db, session_id)
        allow_free_text = any(bool(row.get("allow_free_text", False)) for row in spec_config["categories"] if row["key"] != UNCATEGORIZED_TAGS_KEY)
        if not any(approved_tags.values()) and not allow_free_text:
            profile.tag_assignment_status = "failed"
            profile.tag_assignment_error = "approved_or_free_text_tags_required"
            db.commit()
            return

        snapshot = _tag_assignment_snapshot(spec_config, approved_tags)
        accepted_sources = _accepted_session_sources(db, session_id)
        if source_ids:
            wanted = {value for value in source_ids if value}
            accepted_sources = [row for row in accepted_sources if row.id in wanted]
        parsed_rows = _parsed_text_rows(db, [row.id for row in accepted_sources])
        session_context = profile.session_context or ""
        failure_count = 0
        valid_category_keys = {row["key"] for row in spec_config["categories"]}
        for source in accepted_sources:
            parsed = parsed_rows.get(source.id)
            if parsed is None or not parsed.body_text:
                continue
            annotation = _get_or_create_annotation(db, session_id, source.id)
            if not force_regenerate and annotation.tag_suggestion_status == "completed" and annotation.tag_suggestion_prompt_snapshot == snapshot:
                continue
            annotation.tag_suggestion_status = "running"
            annotation.tag_suggestion_error = None
            db.commit()
            logger.info(
                "approved_tag_assignment_paper_started session_id=%s source_id=%s prompt_hash=%s approved_category_count=%s approved_tag_count=%s",
                session_id,
                source.id,
                _prompt_snapshot_hash(snapshot),
                len([key for key, values in approved_tags.items() if values]),
                sum(len(values) for values in approved_tags.values()),
            )
            try:
                result = assign_approved_structured_tags_to_paper(
                    session_context=session_context,
                    category_config=spec_config["categories"],
                    approved_tags_by_category=approved_tags,
                    prompt_template=spec_out.prompt_template,
                    title=source.title,
                    body_text=parsed.body_text,
                    doi=source.doi,
                    year=source.year,
                )
                normalized_approved = _normalize_tags_by_category(
                    result.approved_tags_by_category,
                    valid_category_keys=valid_category_keys,
                )
                normalized_freeform = _normalize_tags_by_category(
                    result.freeform_tags_by_category,
                    valid_category_keys=valid_category_keys,
                )
                _apply_annotation_tag_payloads(
                    annotation,
                    approved_tags_by_category=normalized_approved,
                    freeform_tags_by_category=normalized_freeform,
                )
                annotation.tag_suggestion_status = "completed"
                annotation.tag_suggestion_model = settings.ai_model
                annotation.tag_suggestion_generated_at = datetime.now(UTC)
                annotation.tag_suggestion_error = None
                annotation.tag_suggestion_prompt_snapshot = snapshot
                db.commit()
                logger.info(
                    "approved_tag_assignment_paper_completed session_id=%s source_id=%s prompt_hash=%s approved_total=%s freeform_total=%s approved_by_category=%s freeform_by_category=%s raw_json=%s",
                    session_id,
                    source.id,
                    _prompt_snapshot_hash(snapshot),
                    sum(len(values) for values in normalized_approved.values()),
                    sum(len(values) for values in normalized_freeform.values()),
                    json.dumps(normalized_approved, ensure_ascii=True, separators=(",", ":")),
                    json.dumps(normalized_freeform, ensure_ascii=True, separators=(",", ":")),
                    json.dumps(getattr(result, "raw_response_json", {}), ensure_ascii=True, separators=(",", ":")),
                )
            except Exception as exc:
                db.rollback()
                annotation = _annotation_row(db, session_id, source.id)
                if annotation is None:
                    continue
                failure_count += 1
                if not annotation.approved_tags_json and not annotation.freeform_tags_json:
                    annotation.tag_suggestion_status = "failed"
                else:
                    annotation.tag_suggestion_status = "completed"
                annotation.tag_suggestion_error = str(exc)
                db.commit()
                logger.warning(
                    "approved_tag_assignment_failed session_id=%s source_id=%s prompt_hash=%s error=%s",
                    session_id,
                    source.id,
                    _prompt_snapshot_hash(snapshot),
                    exc,
                )

        profile.tag_assignment_status = "completed" if failure_count == 0 else "failed"
        profile.tag_assignment_generated_at = datetime.now(UTC)
        profile.tag_assignment_error = (
            f"{failure_count} paper assignments failed."
            if failure_count
            else None
        )
        db.commit()
        logger.info(
            "approved_tag_assignment_completed session_id=%s source_scope=%s approved_count=%s failure_count=%s",
            session_id,
            len(accepted_sources),
            sum(len(values) for values in approved_tags.values()),
            failure_count,
        )


def mark_interrupted_annotation_jobs_on_startup() -> dict[str, int]:
    summary_count = 0
    tag_count = 0
    now = datetime.now(UTC)
    with SessionLocal() as db:
        rows = db.scalars(
            select(PaperAnnotation).where(
                PaperAnnotation.summary_status.in_(("queued", "running"))
                | PaperAnnotation.tag_suggestion_status.in_(("queued", "running"))
            )
        ).all()
        for row in rows:
            changed = False
            if (row.summary_status or "").strip() in {"queued", "running"}:
                row.summary_status = "failed"
                row.summary_error = "Operation was not completed because the server restarted. Generate summary again manually."
                summary_count += 1
                changed = True
            if (row.tag_suggestion_status or "").strip() in {"queued", "running"}:
                row.tag_suggestion_status = "failed"
                row.tag_suggestion_error = "Operation was not completed because the server restarted. Generate tags again manually."
                tag_count += 1
                changed = True
            if changed:
                row.updated_at = now
        if summary_count or tag_count:
            db.commit()
            logger.info(
                "startup_interrupted_annotation_jobs summary_count=%s tag_count=%s",
                summary_count,
                tag_count,
            )
    return {"summary_count": summary_count, "tag_count": tag_count}


@router.get("/v1/sessions/{session_id}/annotations", response_model=PaperAnnotationsListResponse)
def list_annotations(
    session_id: str,
    source_id: list[str] | None = Query(default=None),
    limit: int = Query(default=500, ge=1, le=5000),
    offset: int = Query(default=0, ge=0),
    _: str = Depends(require_api_key),
    __: None = Depends(require_rate_limit),
    db: Session = Depends(get_db),
) -> PaperAnnotationsListResponse:
    if not _session_exists(db, session_id):
        return PaperAnnotationsListResponse(items=[], total=0, limit=limit, offset=offset)
    requested_source_ids = [value for value in (source_id or []) if value]
    if requested_source_ids:
        source_map = _session_source_map(db, session_id, requested_source_ids)
        rows = db.scalars(
            select(PaperAnnotation).where(
                PaperAnnotation.session_id == session_id,
                PaperAnnotation.source_id.in_(requested_source_ids),
            )
        ).all()
        annotation_by_source = {row.source_id: row for row in rows}
        parsed_rows = _parsed_text_rows(db, list(source_map.keys()))
        items = [
            _annotation_out(
                session_id,
                source_id_value,
                annotation_by_source.get(source_id_value),
                parsed_ready=source_id_value in parsed_rows,
            )
            for source_id_value in requested_source_ids
            if source_id_value in source_map
        ]
    else:
        rows = db.scalars(
            select(PaperAnnotation)
            .where(PaperAnnotation.session_id == session_id)
            .order_by(PaperAnnotation.updated_at.desc(), PaperAnnotation.id.desc())
        ).all()
        parsed_rows = _parsed_text_rows(db, [row.source_id for row in rows])
        items = [
            _annotation_out(session_id, row.source_id, row, parsed_ready=row.source_id in parsed_rows)
            for row in rows
        ]
    page = items[offset : offset + limit]
    return PaperAnnotationsListResponse(items=page, total=len(items), limit=limit, offset=offset)


@router.put("/v1/sessions/{session_id}/annotations/{source_id}", response_model=PaperAnnotationOut)
def upsert_annotation(
    session_id: str,
    source_id: str,
    payload: PaperAnnotationUpdateRequest,
    _: str = Depends(require_api_key),
    __: None = Depends(require_rate_limit),
    db: Session = Depends(get_db),
) -> PaperAnnotationOut:
    source_map = _session_source_map(db, session_id, [source_id])
    if source_id not in source_map:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="paper_not_annotatable_in_session")
    spec_config = _tag_spec_settings(db, session_id).category_config.model_dump()
    valid_category_keys = {row["key"] for row in spec_config["categories"]}
    approved_tags = _normalize_tag_list(payload.approved_tags)
    freeform_tags = _normalize_tag_list(payload.freeform_tags)
    approved_tags_by_category = _normalize_tags_by_category(payload.approved_tags_by_category, valid_category_keys=valid_category_keys)
    freeform_tags_by_category = _normalize_tags_by_category(payload.freeform_tags_by_category, valid_category_keys=valid_category_keys)
    if payload.approved_tags is not None and payload.approved_tags_by_category is None:
        approved_tags_by_category = {UNCATEGORIZED_TAGS_KEY: approved_tags} if approved_tags else {}
    if payload.freeform_tags is not None and payload.freeform_tags_by_category is None:
        freeform_tags_by_category = {UNCATEGORIZED_TAGS_KEY: freeform_tags} if freeform_tags else {}
    approved_catalog = {
        (row.category_key or UNCATEGORIZED_TAGS_KEY, row.tag.lower()): row.tag
        for row in db.scalars(select(SessionTagCatalog).where(SessionTagCatalog.session_id == session_id)).all()
    }
    for category_key, tags in approved_tags_by_category.items():
        for tag in tags:
            if (category_key, tag.lower()) not in approved_catalog:
                raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="approved_tag_not_in_catalog")
    annotation = _get_or_create_annotation(db, session_id, source_id)
    if payload.freeform_tags is not None or payload.freeform_tags_by_category is not None:
        _apply_annotation_tag_payloads(annotation, freeform_tags_by_category=freeform_tags_by_category)
    if payload.approved_tags is not None or payload.approved_tags_by_category is not None:
        _apply_annotation_tag_payloads(annotation, approved_tags_by_category=approved_tags_by_category)
    db.commit()
    db.refresh(annotation)
    parsed_rows = _parsed_text_rows(db, [source_id])
    return _annotation_out(session_id, source_id, annotation, parsed_ready=source_id in parsed_rows)


@router.get("/v1/sessions/{session_id}/tag-spec", response_model=SessionTagSpecOut)
def get_tag_spec(
    session_id: str,
    _: str = Depends(require_api_key),
    __: None = Depends(require_rate_limit),
    db: Session = Depends(get_db),
) -> SessionTagSpecOut:
    return _tag_spec_settings(db, session_id)


@router.put("/v1/sessions/{session_id}/tag-spec", response_model=SessionTagSpecOut)
def put_tag_spec(
    session_id: str,
    payload: SessionTagSpecUpdateRequest,
    _: str = Depends(require_api_key),
    __: None = Depends(require_rate_limit),
    db: Session = Depends(get_db),
) -> SessionTagSpecOut:
    config = _normalize_tag_spec_config(payload.category_config.model_dump())
    row = db.get(SessionTagSpec, session_id)
    prompt_template = _build_tag_prompt_template_from_spec(config)
    if row is None:
        row = SessionTagSpec(
            session_id=session_id,
            category_config_json=config,
            prompt_template=prompt_template,
        )
        db.add(row)
    else:
        previous_keys = {
            item["key"] for item in _normalize_tag_spec_config(row.category_config_json)["categories"]
        }
        next_keys = {item["key"] for item in config["categories"]}
        removed_keys = previous_keys - next_keys
        row.category_config_json = config
        row.prompt_template = prompt_template
        if removed_keys:
            annotations = db.scalars(select(PaperAnnotation).where(PaperAnnotation.session_id == session_id)).all()
            for annotation in annotations:
                approved = _annotation_tags_by_category(annotation, "approved")
                freeform = _annotation_tags_by_category(annotation, "freeform")
                moved = False
                for removed_key in removed_keys:
                    removed_approved = approved.pop(removed_key, [])
                    removed_freeform = freeform.pop(removed_key, [])
                    if removed_approved:
                        approved[UNCATEGORIZED_TAGS_KEY] = _normalize_tag_list((approved.get(UNCATEGORIZED_TAGS_KEY) or []) + removed_approved)
                        moved = True
                    if removed_freeform:
                        freeform[UNCATEGORIZED_TAGS_KEY] = _normalize_tag_list((freeform.get(UNCATEGORIZED_TAGS_KEY) or []) + removed_freeform)
                        moved = True
                if moved:
                    _apply_annotation_tag_payloads(annotation, approved_tags_by_category=approved, freeform_tags_by_category=freeform)
            catalog_rows = db.scalars(select(SessionTagCatalog).where(SessionTagCatalog.session_id == session_id)).all()
            for catalog_row in catalog_rows:
                if catalog_row.category_key in removed_keys:
                    catalog_row.category_key = UNCATEGORIZED_TAGS_KEY
            candidate_rows = db.scalars(select(SessionTagCandidate).where(SessionTagCandidate.session_id == session_id)).all()
            for candidate_row in candidate_rows:
                if candidate_row.category_key in removed_keys:
                    candidate_row.category_key = UNCATEGORIZED_TAGS_KEY
    db.commit()
    return _tag_spec_settings(db, session_id)


@router.get("/v1/sessions/{session_id}/tag-catalog", response_model=SessionTagCatalogOut)
def get_tag_catalog(
    session_id: str,
    _: str = Depends(require_api_key),
    __: None = Depends(require_rate_limit),
    db: Session = Depends(get_db),
) -> SessionTagCatalogOut:
    tags = _flatten_tags_by_category(_approved_tags_by_category(db, session_id))
    return SessionTagCatalogOut(session_id=session_id, tags=tags)


@router.put("/v1/sessions/{session_id}/tag-catalog", response_model=SessionTagCatalogOut)
def put_tag_catalog(
    session_id: str,
    payload: SessionTagCatalogUpdateRequest,
    _: str = Depends(require_api_key),
    __: None = Depends(require_rate_limit),
    db: Session = Depends(get_db),
) -> SessionTagCatalogOut:
    tags = _normalize_tag_list(payload.tags)
    existing = db.scalars(select(SessionTagCatalog).where(SessionTagCatalog.session_id == session_id)).all()
    for row in existing:
        if row.category_key == UNCATEGORIZED_TAGS_KEY:
            db.delete(row)
    for tag in tags:
        db.add(SessionTagCatalog(id=f"stag_{uuid.uuid4().hex[:12]}", session_id=session_id, category_key=UNCATEGORIZED_TAGS_KEY, tag=tag))
    db.commit()
    return SessionTagCatalogOut(session_id=session_id, tags=_flatten_tags_by_category(_approved_tags_by_category(db, session_id)))


@router.get("/v1/sessions/{session_id}/tag-review", response_model=SessionTagReviewOut)
def get_tag_review(
    session_id: str,
    _: str = Depends(require_api_key),
    __: None = Depends(require_rate_limit),
    db: Session = Depends(get_db),
) -> SessionTagReviewOut:
    return _tag_review_out(db, session_id)


@router.post("/v1/sessions/{session_id}/tag-review/generate", response_model=SessionTagWorkflowResponse, status_code=status.HTTP_202_ACCEPTED)
def generate_session_tag_candidates(
    session_id: str,
    payload: SessionTagWorkflowRequest,
    background_tasks: BackgroundTasks,
    _: str = Depends(require_api_key),
    __: None = Depends(require_rate_limit),
    db: Session = Depends(get_db),
) -> SessionTagWorkflowResponse:
    profile = _ensure_session_profile(db, session_id)
    profile.tag_candidate_status = "queued"
    profile.tag_candidate_error = None
    db.commit()
    background_tasks.add_task(_generate_session_tag_candidates_task, session_id, payload.force_regenerate)
    logger.info("session_tag_candidates_queued session_id=%s force_regenerate=%s", session_id, payload.force_regenerate)
    return SessionTagWorkflowResponse(session_id=session_id, status="queued")


@router.post("/v1/sessions/{session_id}/tags/apply-approved", response_model=SessionTagWorkflowResponse, status_code=status.HTTP_202_ACCEPTED)
def apply_approved_tags(
    session_id: str,
    payload: SessionTagWorkflowRequest,
    background_tasks: BackgroundTasks,
    _: str = Depends(require_api_key),
    __: None = Depends(require_rate_limit),
    db: Session = Depends(get_db),
) -> SessionTagWorkflowResponse:
    spec = _tag_spec_settings(db, session_id)
    approved_tags = _approved_tags_by_category(db, session_id)
    allow_free_text = any(row.allow_free_text for row in spec.category_config.categories if row.key != UNCATEGORIZED_TAGS_KEY)
    if not any(approved_tags.values()) and not allow_free_text:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="approved_tags_required")
    profile = _ensure_session_profile(db, session_id)
    profile.tag_assignment_status = "queued"
    profile.tag_assignment_error = None
    db.commit()
    background_tasks.add_task(_apply_approved_tags_task, session_id, payload.force_regenerate)
    logger.info("approved_tag_assignment_queued session_id=%s force_regenerate=%s", session_id, payload.force_regenerate)
    return SessionTagWorkflowResponse(session_id=session_id, status="queued")


@router.post(
    "/v1/sessions/{session_id}/annotations/{source_id}/tags/apply-approved",
    response_model=SessionTagWorkflowResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def apply_approved_tags_for_source(
    session_id: str,
    source_id: str,
    payload: SessionTagWorkflowRequest,
    background_tasks: BackgroundTasks,
    _: str = Depends(require_api_key),
    __: None = Depends(require_rate_limit),
    db: Session = Depends(get_db),
) -> SessionTagWorkflowResponse:
    source_map = _session_source_map(db, session_id, [source_id])
    if source_id not in source_map:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="paper_not_annotatable_in_session")
    parsed_rows = _parsed_text_rows(db, [source_id])
    parsed = parsed_rows.get(source_id)
    if parsed is None or not parsed.body_text:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="parsed_text_required")
    spec = _tag_spec_settings(db, session_id)
    approved_tags = _approved_tags_by_category(db, session_id)
    allow_free_text = any(row.allow_free_text for row in spec.category_config.categories if row.key != UNCATEGORIZED_TAGS_KEY)
    if not any(approved_tags.values()) and not allow_free_text:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="approved_tags_required")
    annotation = _get_or_create_annotation(db, session_id, source_id)
    annotation.tag_suggestion_status = "queued"
    annotation.tag_suggestion_error = None
    db.commit()
    background_tasks.add_task(_apply_approved_tags_task, session_id, payload.force_regenerate, [source_id])
    logger.info(
        "approved_tag_assignment_single_queued session_id=%s source_id=%s force_regenerate=%s",
        session_id,
        source_id,
        payload.force_regenerate,
    )
    return SessionTagWorkflowResponse(session_id=session_id, status="queued")


@router.post("/v1/sessions/{session_id}/tag-candidates/{candidate_id}/approve", response_model=SessionTagReviewOut)
def approve_tag_candidate(
    session_id: str,
    candidate_id: str,
    _: str = Depends(require_api_key),
    __: None = Depends(require_rate_limit),
    db: Session = Depends(get_db),
) -> SessionTagReviewOut:
    row = db.get(SessionTagCandidate, candidate_id)
    if row is None or row.session_id != session_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="tag_candidate_not_found")
    row.status = "approved"
    if not db.scalars(
        select(SessionTagCatalog).where(
            SessionTagCatalog.session_id == session_id,
            SessionTagCatalog.category_key == row.category_key,
            SessionTagCatalog.tag == row.tag,
        ).limit(1)
    ).first():
        db.add(
            SessionTagCatalog(
                id=f"stag_{uuid.uuid4().hex[:12]}",
                session_id=session_id,
                category_key=row.category_key,
                tag=row.tag,
            )
        )
    db.commit()
    return _tag_review_out(db, session_id)


@router.post("/v1/sessions/{session_id}/tag-candidates/{candidate_id}/reject", response_model=SessionTagReviewOut)
def reject_tag_candidate(
    session_id: str,
    candidate_id: str,
    _: str = Depends(require_api_key),
    __: None = Depends(require_rate_limit),
    db: Session = Depends(get_db),
) -> SessionTagReviewOut:
    row = db.get(SessionTagCandidate, candidate_id)
    if row is None or row.session_id != session_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="tag_candidate_not_found")
    row.status = "rejected"
    db.commit()
    return _tag_review_out(db, session_id)


@router.post("/v1/sessions/{session_id}/tag-candidates/reset-rejections", response_model=SessionTagReviewOut)
def reset_rejected_tag_candidates(
    session_id: str,
    _: str = Depends(require_api_key),
    __: None = Depends(require_rate_limit),
    db: Session = Depends(get_db),
) -> SessionTagReviewOut:
    rows = db.scalars(
        select(SessionTagCandidate).where(SessionTagCandidate.session_id == session_id, SessionTagCandidate.status == "rejected")
    ).all()
    for row in rows:
        db.delete(row)
    db.commit()
    return _tag_review_out(db, session_id)


@router.get("/v1/sessions/{session_id}/summary-settings", response_model=SessionSummarySettingsOut)
def get_summary_settings(
    session_id: str,
    _: str = Depends(require_api_key),
    __: None = Depends(require_rate_limit),
    db: Session = Depends(get_db),
) -> SessionSummarySettingsOut:
    return _summary_settings(db, session_id)


@router.put("/v1/sessions/{session_id}/summary-settings", response_model=SessionSummarySettingsOut)
def put_summary_settings(
    session_id: str,
    payload: SessionSummarySettingsUpdateRequest,
    _: str = Depends(require_api_key),
    __: None = Depends(require_rate_limit),
    db: Session = Depends(get_db),
) -> SessionSummarySettingsOut:
    prompt_template = (payload.prompt_template or "").strip()
    editor_config = _normalize_summary_editor_config(
        payload.editor_config.model_dump() if payload.editor_config is not None else None
    )
    if not prompt_template and payload.editor_config is None:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="summary_settings_required")
    if payload.editor_config is not None and not prompt_template:
        prompt_template = _build_prompt_template_from_editor_config(editor_config)
    row = db.get(SessionSummarySettings, session_id)
    if row is None:
        row = SessionSummarySettings(
            session_id=session_id,
            prompt_template=prompt_template,
            editor_config_json=editor_config,
        )
        db.add(row)
    else:
        row.prompt_template = prompt_template
        if payload.editor_config is not None:
            row.editor_config_json = editor_config
    db.commit()
    return _summary_settings(db, session_id)


@router.post("/v1/sessions/{session_id}/summaries/generate", response_model=SummaryGenerationResponse, status_code=status.HTTP_202_ACCEPTED)
def generate_summaries(
    session_id: str,
    payload: SummaryGenerationRequest,
    background_tasks: BackgroundTasks,
    _: str = Depends(require_api_key),
    __: None = Depends(require_rate_limit),
    db: Session = Depends(get_db),
) -> SummaryGenerationResponse:
    source_ids = [value for value in payload.source_ids if value]
    if not source_ids:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="source_ids_required")
    source_map = _session_source_map(db, session_id, source_ids)
    parsed_rows = _parsed_text_rows(db, list(source_map.keys()))
    summary_settings = _summary_settings(db, session_id)
    blocked: list[SummaryGenerationBlockedOut] = []
    queued: list[str] = []
    for source_id in source_ids:
        if source_id not in source_map:
            blocked.append(SummaryGenerationBlockedOut(source_id=source_id, reason="paper_not_annotatable_in_session"))
            continue
        parsed = parsed_rows.get(source_id)
        if parsed is None or not parsed.body_text:
            blocked.append(SummaryGenerationBlockedOut(source_id=source_id, reason="parsed_text_required"))
            continue
        annotation = _get_or_create_annotation(db, session_id, source_id)
        if annotation.summary_status == "completed" and annotation.ai_summary and not payload.force_regenerate:
            blocked.append(SummaryGenerationBlockedOut(source_id=source_id, reason="already_generated"))
            continue
        annotation.summary_status = "queued"
        annotation.summary_error = None
        annotation.summary_prompt_snapshot = summary_settings.prompt_template
        annotation.summary_editor_snapshot_json = summary_settings.editor_config.model_dump()
        queued.append(source_id)
    db.commit()
    if queued:
        background_tasks.add_task(_generate_summaries_task, session_id, queued, payload.force_regenerate)
        logger.info("paper_summary_queued session_id=%s queued_count=%s", session_id, len(queued))
    return SummaryGenerationResponse(
        session_id=session_id,
        queued_count=len(queued),
        blocked_count=len(blocked),
        blocked=blocked,
    )


@router.post("/v1/sessions/{session_id}/tags/generate", response_model=TagGenerationResponse, status_code=status.HTTP_202_ACCEPTED)
def generate_tags(
    session_id: str,
    payload: TagGenerationRequest,
    background_tasks: BackgroundTasks,
    _: str = Depends(require_api_key),
    __: None = Depends(require_rate_limit),
    db: Session = Depends(get_db),
) -> TagGenerationResponse:
    source_ids = [value for value in payload.source_ids if value]
    if not source_ids:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="source_ids_required")
    source_map = _session_source_map(db, session_id, source_ids)
    parsed_rows = _parsed_text_rows(db, list(source_map.keys()))
    blocked: list[TagGenerationBlockedOut] = []
    queued: list[str] = []
    for source_id in source_ids:
        if source_id not in source_map:
            blocked.append(TagGenerationBlockedOut(source_id=source_id, reason="paper_not_annotatable_in_session"))
            continue
        parsed = parsed_rows.get(source_id)
        if parsed is None or not parsed.body_text:
            blocked.append(TagGenerationBlockedOut(source_id=source_id, reason="parsed_text_required"))
            continue
        annotation = _get_or_create_annotation(db, session_id, source_id)
        if annotation.tag_suggestion_status == "completed" and annotation.ai_suggested_tags_json and not payload.force_regenerate:
            blocked.append(TagGenerationBlockedOut(source_id=source_id, reason="already_generated"))
            continue
        annotation.tag_suggestion_status = "queued"
        annotation.tag_suggestion_error = None
        queued.append(source_id)
    db.commit()
    if queued:
        background_tasks.add_task(_generate_tags_task, session_id, queued, payload.force_regenerate)
        logger.info("paper_tags_queued session_id=%s queued_count=%s", session_id, len(queued))
    return TagGenerationResponse(
        session_id=session_id,
        queued_count=len(queued),
        blocked_count=len(blocked),
        blocked=blocked,
    )


@router.post("/v1/sessions/{session_id}/annotations/{source_id}/suggested-tags/promote", response_model=PaperAnnotationOut)
def promote_suggested_tag(
    session_id: str,
    source_id: str,
    payload: SuggestedTagPromoteRequest,
    _: str = Depends(require_api_key),
    __: None = Depends(require_rate_limit),
    db: Session = Depends(get_db),
) -> PaperAnnotationOut:
    source_map = _session_source_map(db, session_id, [source_id])
    if source_id not in source_map:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="paper_not_annotatable_in_session")
    annotation = _get_or_create_annotation(db, session_id, source_id)
    normalized_tag = _normalize_tag(payload.tag)
    if not normalized_tag:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="tag_required")
    suggested = list(annotation.ai_suggested_tags_json or [])
    if normalized_tag.lower() not in {item.lower() for item in suggested}:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="suggested_tag_not_found")
    if payload.target == "approved":
        approved_catalog = {
            (row.category_key or UNCATEGORIZED_TAGS_KEY, row.tag.lower()): row.tag
            for row in db.scalars(select(SessionTagCatalog).where(SessionTagCatalog.session_id == session_id)).all()
        }
        canonical = approved_catalog.get((UNCATEGORIZED_TAGS_KEY, normalized_tag.lower()))
        if canonical is None:
            canonical = normalized_tag
            db.add(SessionTagCatalog(id=f"stag_{uuid.uuid4().hex[:12]}", session_id=session_id, category_key=UNCATEGORIZED_TAGS_KEY, tag=canonical))
        approved = _annotation_tags_by_category(annotation, "approved")
        approved[UNCATEGORIZED_TAGS_KEY] = _normalize_tag_list((approved.get(UNCATEGORIZED_TAGS_KEY) or []) + [canonical])
        _apply_annotation_tag_payloads(annotation, approved_tags_by_category=approved)
    else:
        freeform = _annotation_tags_by_category(annotation, "freeform")
        freeform[UNCATEGORIZED_TAGS_KEY] = _normalize_tag_list((freeform.get(UNCATEGORIZED_TAGS_KEY) or []) + [normalized_tag])
        _apply_annotation_tag_payloads(annotation, freeform_tags_by_category=freeform)
    annotation.ai_suggested_tags_json = [item for item in suggested if item.lower() != normalized_tag.lower()]
    db.commit()
    db.refresh(annotation)
    parsed_rows = _parsed_text_rows(db, [source_id])
    return _annotation_out(session_id, source_id, annotation, parsed_ready=source_id in parsed_rows)


@router.post("/v1/sessions/{session_id}/annotations/{source_id}/suggested-tags/dismiss", response_model=PaperAnnotationOut)
def dismiss_suggested_tag(
    session_id: str,
    source_id: str,
    payload: SuggestedTagDismissRequest,
    _: str = Depends(require_api_key),
    __: None = Depends(require_rate_limit),
    db: Session = Depends(get_db),
) -> PaperAnnotationOut:
    source_map = _session_source_map(db, session_id, [source_id])
    if source_id not in source_map:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="paper_not_annotatable_in_session")
    annotation = _get_or_create_annotation(db, session_id, source_id)
    normalized_tag = _normalize_tag(payload.tag)
    if not normalized_tag:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="tag_required")
    suggested = list(annotation.ai_suggested_tags_json or [])
    if normalized_tag.lower() not in {item.lower() for item in suggested}:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="suggested_tag_not_found")
    annotation.ai_suggested_tags_json = [item for item in suggested if item.lower() != normalized_tag.lower()]
    db.commit()
    db.refresh(annotation)
    parsed_rows = _parsed_text_rows(db, [source_id])
    return _annotation_out(session_id, source_id, annotation, parsed_ready=source_id in parsed_rows)
