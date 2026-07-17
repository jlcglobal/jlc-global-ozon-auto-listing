#!/usr/bin/env python3
"""Validate a stage-1 product directory."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from jsonschema import Draft202012Validator

try:
    from scripts.image_qc import validate_report as validate_image_qc_report
except ModuleNotFoundError:  # Allows direct execution as scripts/validate_product.py.
    from image_qc import validate_report as validate_image_qc_report


ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "templates"

FILE_SCHEMAS = {
    "input/source.json": "source.schema.json",
    "output/product-analysis.json": "product-analysis.schema.json",
    "output/product-positioning.json": "product-positioning.schema.json",
    "output/copy-ru.json": "copy-ru.schema.json",
    "output/style-profile.json": "style-profile.schema.json",
    "output/image-plan.json": "image-plan.schema.json",
    "output/ozon-draft.json": "ozon-draft.schema.json",
    "status.json": "status.schema.json"
}

OPTIONAL_FILE_SCHEMAS = {
    "input/category-selection.json": "category-selection.schema.json",
    "output/keyword-research-ru.json": "keyword-research-ru.schema.json",
    "output/image-qc-report.json": "image-qc-report.schema.json",
    "output/title-ru.json": "title-ru.schema.json",
    "output/description-ru.json": "description-ru.schema.json",
    "output/keywords-ru.json": "keywords-ru.schema.json",
    "output/attributes.json": "attributes.schema.json",
    "output/ozon-category.json": "ozon-category.schema.json",
    "output/ozon-attributes.json": "ozon-attributes.schema.json",
    "output/ozon-category-tree.json": "ozon-category-tree.schema.json",
    "output/ozon-category-attributes.json": "ozon-category-attributes.schema.json",
    "output/ozon-preflight.json": "ozon-preflight.schema.json",
    "output/ozon-upload-config.json": "ozon-upload-config.schema.json",
    "output/ozon-images.json": "ozon-images.schema.json",
    "output/ozon-upload-preflight.json": "ozon-upload-preflight.schema.json",
    "output/ozon-result.json": "ozon-result.schema.json"
    ,"output/ozon-tags.json": "ozon-tags.schema.json"
    ,"output/ozon-attributes-final.json": "ozon-attributes-final.schema.json"
    ,"output/rich-content.json": "rich-content.schema.json"
    ,"output/color-variants.json": "color-variants.schema.json"
    ,"output/color-variant-policy.json": "color-variant-policy.schema.json"
    ,"output/color-variant-qc.json": "color-variant-qc.schema.json"
    ,"output/final-upload-check.json": "final-upload-check.schema.json"
    ,"output/ozon-upload-payload.json": "ozon-upload-payload.schema.json"
    ,"output/product-exists-check.json": "product-exists-check.schema.json"
    ,"output/cost-analysis.json": "cost-analysis.schema.json"
    ,"output/pricing-result.json": "pricing-result.schema.json"
    ,"output/profit-analysis.json": "profit-analysis.schema.json"
    ,"output/variant-decision.json": "variant-decision.schema.json"
    ,"output/variant-grouping-result.json": "variant-grouping-result.schema.json"
    ,"output/platform-grouping-result.json": "platform-grouping-result.schema.json"
    ,"output/grouping-verification.json": "grouping-verification.schema.json"
    ,"output/merge-field-diff.json": "merge-field-diff.schema.json"
    ,"output/merge-repair-payload.json": "merge-repair-payload.schema.json"
    ,"output/category-variant-rule-audit.json": "category-variant-rule-audit.schema.json"
    ,"output/corrected-variant-decision.json": "corrected-variant-decision.schema.json"
    ,"output/category-remap-proposal.json": "category-remap-proposal.schema.json"
    ,"output/local-rule-fix-report.json": "local-rule-fix-report.schema.json"
}

REQUIRED_DIRS = [
    "input",
    "input/main-images",
    "input/sku-images",
    "input/detail-images",
    "output",
    "output/images",
    "output/images/main",
    "output/images/detail",
    "logs"
]

COLLECTOR_REQUIRED_DIRS = [
    "input",
    "input/main-images",
    "input/sku-images",
    "input/detail-images",
    "output",
    "logs"
]

COLLECTOR_FILE_SCHEMAS = {
    "input/source.json": "source.schema.json",
    "input/category-selection.json": "category-selection.schema.json",
    "status.json": "status.schema.json"
}

STATUS_TRANSITIONS = {
    None: {"COLLECTING", "COLLECTED"},
    "COLLECTING": {"COLLECTED", "FAILED_HARD_BLOCKER"},
    # Batch source validation runs before queue_product(). Invalid captures can
    # therefore be checkpointed directly from COLLECTED as a hard blocker.
    "COLLECTED": {"QUEUED", "FAILED_HARD_BLOCKER"},
    "QUEUED": {"PROCESSING", "STOPPED", "FAILED_HARD_BLOCKER"},
    # A recovered batch may finish the final local checks in one pass after
    # earlier checkpoints were already persisted.  PROCESSING -> OZON_READY
    # is therefore a valid audited transition, not a corrupt history.
    "PROCESSING": {"CATEGORY_MATCHED", "CONTENT_GENERATED", "IMAGES_GENERATED", "OZON_READY", "STOPPED", "FAILED_HARD_BLOCKER"},
    "CATEGORY_MATCHED": {"PRICED", "CONTENT_GENERATED", "STOPPED", "FAILED_HARD_BLOCKER"},
    "CONTENT_GENERATED": {"IMAGES_GENERATED", "STOPPED", "FAILED_HARD_BLOCKER"},
    "IMAGES_GENERATED": {"PRICED", "OZON_READY", "STOPPED", "FAILED_HARD_BLOCKER"},
    "PRICED": {"PROCESSING", "OZON_READY", "STOPPED", "FAILED_HARD_BLOCKER"},
    "STOPPED": {"QUEUED", "FAILED_HARD_BLOCKER"},
    "OZON_READY": {"UPLOADING", "FAILED_HARD_BLOCKER"},
    "UPLOADING": {"OZON_MODERATION", "PENDING_REMOTE", "HANDED_OFF_TO_OZON", "FAILED_HARD_BLOCKER", "UPLOADED"},
    "OZON_MODERATION": {"PENDING_REMOTE", "UPLOADED", "ACTIVE", "FAILED_HARD_BLOCKER"},
    "PENDING_REMOTE": {"OZON_MODERATION", "UPLOADED", "ACTIVE", "FAILED_HARD_BLOCKER"},
    "UPLOADED": {"UPLOADING", "PENDING_REMOTE", "OZON_MODERATION", "FAILED_HARD_BLOCKER"},
    # A failed checkpoint is recoverable.  Older batch runs resumed directly
    # at the next validated checkpoint instead of writing an extra QUEUED /
    # PROCESSING event, so accept every forward checkpoint here.  This keeps
    # the audit history truthful without making a recovered product appear
    # permanently broken.
    "FAILED_HARD_BLOCKER": {
        "QUEUED", "PROCESSING", "CATEGORY_MATCHED", "PRICED",
        "CONTENT_GENERATED", "IMAGES_GENERATED", "OZON_READY", "STOPPED",
        "FAILED_HARD_BLOCKER", "PENDING_REMOTE", "HANDED_OFF_TO_OZON", "OZON_MODERATION", "UPLOADED",
    },
    # Read-only compatibility for audit histories created before batch authorization.
    "CODEX_PROCESSING": {"WAITING_REVIEW", "FAILED"},
    "WAITING_REVIEW": {"APPROVED", "REJECTED", "CODEX_PROCESSING", "FAILED"},
    "APPROVED": {"UPLOADING", "REJECTED", "FAILED"},
    "REJECTED": {"CODEX_PROCESSING"},
    "UPDATING": {"UPLOADED", "FAILED"},
    "FAILED": {"CODEX_PROCESSING", "COLLECTED"},
    "ARCHIVED": {"ARCHIVED"},
}

STATUS_TRANSITIONS["COLLECTED"].add("CODEX_PROCESSING")
# Local archival is a terminal lifecycle action reachable from any prior
# state; it never represents a new Ozon workflow state.
for _status_name in list(STATUS_TRANSITIONS):
    STATUS_TRANSITIONS[_status_name].add("ARCHIVED")

SOURCE_FORBIDDEN_KEYS = {
    "inference",
    "inferences",
    "ai_inference",
    "ai_guess",
    "codex_analysis",
    "generated_copy",
    "selling_points",
    "risks",
    "unknowns"
}


class ValidationFailure(Exception):
    pass


def display_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path)


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def iter_keys(value: Any) -> Iterable[str]:
    if isinstance(value, dict):
        for key, child in value.items():
            yield key
            yield from iter_keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from iter_keys(child)


def validate_schema(instance_path: Path, schema_path: Path) -> List[str]:
    instance = load_json(instance_path)
    schema = load_json(schema_path)
    validator = Draft202012Validator(schema)
    errors = []
    for error in sorted(validator.iter_errors(instance), key=lambda item: list(item.path)):
        path = "/".join(str(part) for part in error.path) or "<root>"
        errors.append(f"{display_path(instance_path)}:{path}: {error.message}")
    return errors


def validate_directory(product_dir: Path) -> List[str]:
    errors = []
    for rel_dir in REQUIRED_DIRS:
        path = product_dir / rel_dir
        if not path.is_dir():
            errors.append(f"{display_path(product_dir)}: missing directory {rel_dir}")
    for rel_file in FILE_SCHEMAS:
        path = product_dir / rel_file
        if not path.is_file():
            errors.append(f"{display_path(product_dir)}: missing file {rel_file}")
    # A product can legitimately be stopped or failed before image generation
    # (for example after a single-image retry budget is exhausted).  Missing
    # image QC is then a checkpoint fact, not a malformed product directory;
    # keep the hard requirement for products that claim to be upload-ready or
    # already uploaded.
    status_path = product_dir / "status.json"
    current_status = load_json(status_path).get("status") if status_path.is_file() else None
    qc_required_statuses = {"OZON_READY", "UPLOADING", "OZON_MODERATION", "PENDING_REMOTE", "UPLOADED", "ACTIVE"}
    if current_status in qc_required_statuses and not any(
        (product_dir / candidate).is_file() for candidate in ("output/qc-report.json", "output/image-qc-report.json")
    ):
        errors.append(f"{display_path(product_dir)}: missing image QC report")
    return errors


def validate_required_paths(product_dir: Path, dirs: List[str], files: Dict[str, str]) -> List[str]:
    errors = []
    for rel_dir in dirs:
        path = product_dir / rel_dir
        if not path.is_dir():
            errors.append(f"{display_path(product_dir)}: missing directory {rel_dir}")
    for rel_file in files:
        path = product_dir / rel_file
        if not path.is_file():
            errors.append(f"{display_path(product_dir)}: missing file {rel_file}")
    return errors


def validate_source_truthfulness(source: Dict[str, Any], product_dir: Path) -> List[str]:
    errors = []
    forbidden = SOURCE_FORBIDDEN_KEYS.intersection({key.lower() for key in iter_keys(source)})
    if forbidden:
        errors.append(
            f"{display_path(product_dir)}/input/source.json: source data contains analysis-only keys: "
            + ", ".join(sorted(forbidden))
        )
    return errors


def latest_step_by_name(steps: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    latest: Dict[str, Dict[str, Any]] = {}
    for step in steps:
        latest[step["name"]] = step
    return latest


def has_history_to(status: Dict[str, Any], target: str) -> bool:
    return any(item.get("to") == target for item in status.get("history", []))


def validate_history(status: Dict[str, Any], product_dir: Path) -> List[str]:
    errors = []
    previous: Optional[str] = None
    for index, item in enumerate(status.get("history", [])):
        current = item["to"]
        allowed = STATUS_TRANSITIONS.get(previous, set())
        if current not in allowed:
            errors.append(
                f"{display_path(product_dir)}/status.json: invalid transition at history[{index}] "
                f"from {previous} to {current}"
            )
        previous = current
    if previous and previous != status["status"]:
        errors.append(
            f"{display_path(product_dir)}/status.json: current status {status['status']} does not match "
            f"last history status {previous}"
        )
    return errors


def validate_status_integrity(status: Dict[str, Any], product_dir: Path) -> List[str]:
    errors = []
    current_status = status["status"]
    steps = status.get("steps", [])
    latest_steps = latest_step_by_name(steps)
    ozon = status["ozon"]

    if current_status == "FAILED_HARD_BLOCKER":
        failed_steps = [step for step in steps if step["status"] == "failed"]
        status_errors = ozon.get("errors", [])
        if not failed_steps and not status_errors:
            errors.append(f"{display_path(product_dir)}/status.json: FAILED_HARD_BLOCKER requires a failed step or Ozon error")
        for step in failed_steps:
            if not step.get("error") or not step["error"].get("reason"):
                errors.append(
                    f"{display_path(product_dir)}/status.json: failed step {step['name']} requires concrete reason"
                )

    upload_completed = (
        current_status in {"OZON_READY", "UPLOADING", "PENDING_REMOTE", "OZON_MODERATION", "UPLOADED", "ACTIVE"}
        and latest_steps.get("ozon_upload", {}).get("status") == "completed"
    )
    for name, step in latest_steps.items():
        # A product can have a stale failed checkpoint from an earlier retry.
        # Once a later upload checkpoint completed, that failure is historical
        # rather than a reason to hide an already usable product.
        if step["status"] == "failed" and current_status != "FAILED_HARD_BLOCKER" and not upload_completed:
            errors.append(
                f"{display_path(product_dir)}/status.json: latest step {name} failed but product status is "
                f"{current_status}"
            )

    if current_status == "UPLOADED":
        upload_step = latest_steps.get("ozon_upload")
        if not upload_step or upload_step["status"] != "completed":
            errors.append(f"{display_path(product_dir)}/status.json: UPLOADED requires completed ozon_upload step")
        if ozon.get("upload_status") != "uploaded":
            errors.append(f"{display_path(product_dir)}/status.json: UPLOADED requires ozon.upload_status=uploaded")
        if ozon.get("product_id") in {"unknown", None} or ozon.get("offer_id") in {"unknown", None}:
            errors.append(f"{display_path(product_dir)}/status.json: UPLOADED requires Ozon product_id and offer_id")

    return errors


def can_start_upload(status: Dict[str, Any]) -> bool:
    return status.get("task_authorized") is True and status["status"] == "OZON_READY"


def validate_positioning_integrity(product_dir: Path) -> List[str]:
    errors = []
    positioning = load_json(product_dir / "output/product-positioning.json")
    evidence_fields = {
        item["field"]
        for item in positioning.get("positioning_evidence", [])
        if item.get("claim_type") != "unknown"
    }
    scalar_fields = (
        "market_positioning",
        "target_customer",
        "purchase_motivation",
        "core_sales_angle",
        "emotional_trigger",
        "competitive_advantage",
        "recommended_visual_direction",
    )
    for field in scalar_fields:
        value = positioning.get(field)
        if value not in (None, "unknown") and field not in evidence_fields:
            errors.append(
                f"{display_path(product_dir)}/output/product-positioning.json: {field} requires positioning evidence"
            )
    pain_points = [value for value in positioning.get("customer_pain_points", []) if value != "unknown"]
    if pain_points and "customer_pain_points" not in evidence_fields:
        errors.append(
            f"{display_path(product_dir)}/output/product-positioning.json: customer_pain_points require positioning evidence"
        )
    buyer_selling_points = positioning.get("buyer_selling_points") or []
    if (positioning.get("processing") or {}).get("status") == "completed" and len(buyer_selling_points) < 3:
        errors.append(
            f"{display_path(product_dir)}/output/product-positioning.json: completed positioning requires at least 3 buyer selling points"
        )
    for index, item in enumerate(buyer_selling_points):
        if not isinstance(item, dict) or not item.get("text") or not item.get("source_refs"):
            errors.append(
                f"{display_path(product_dir)}/output/product-positioning.json: buyer_selling_points[{index}] must be traceable"
            )
    for index, item in enumerate(positioning.get("usage_scenarios") or []):
        if not isinstance(item, dict) or not item.get("text") or not item.get("source_refs"):
            errors.append(
                f"{display_path(product_dir)}/output/product-positioning.json: usage_scenarios[{index}] must be traceable"
            )
    if positioning.get("recommended_price_position") != "unknown":
        errors.append(
            f"{display_path(product_dir)}/output/product-positioning.json: price position must remain unknown without market evidence"
        )
    return errors


def validate_style_integrity(product_dir: Path) -> List[str]:
    errors = []
    style_profile = load_json(product_dir / "output/style-profile.json")
    image_plan = load_json(product_dir / "output/image-plan.json")
    legacy_qc_path = product_dir / "output/qc-report.json"
    qc_report = load_json(legacy_qc_path) if legacy_qc_path.is_file() else None
    expected_ref = f"products/{product_dir.name}/output/style-profile.json"
    expected_positioning_ref = f"products/{product_dir.name}/output/product-positioning.json"

    if style_profile.get("positioning_ref") != expected_positioning_ref:
        errors.append(
            f"{display_path(product_dir)}/output/style-profile.json: positioning_ref must be {expected_positioning_ref}"
        )
    if expected_positioning_ref not in image_plan["source_refs"]:
        errors.append(
            f"{display_path(product_dir)}/output/image-plan.json: source_refs must include product-positioning.json"
        )

    if image_plan["style_profile_ref"] != expected_ref:
        errors.append(f"{display_path(product_dir)}/output/image-plan.json: style_profile_ref must be {expected_ref}")
    if image_plan["style_family"] != style_profile["style_family"]:
        errors.append(f"{display_path(product_dir)}/output/image-plan.json: style_family does not match style-profile.json")
    creative_ref = image_plan.get("creative_brief_ref")
    modern_plan = bool(creative_ref)
    expected_structure = list(style_profile["image_set_structure"])
    if modern_plan and len(expected_structure) - 1 < 8:
        expected_structure.append("disclaimer")
    if image_plan["image_set_structure"] != expected_structure:
        errors.append(f"{display_path(product_dir)}/output/image-plan.json: image_set_structure does not match selected style structure")
    if modern_plan:
        brief_path = product_dir / "output/ecommerce-creative-brief.json"
        if not brief_path.is_file():
            errors.append(f"{display_path(product_dir)}/output/ecommerce-creative-brief.json: missing creative hand-off")
        if len(image_plan.get("detail_images") or []) != 8:
            errors.append(f"{display_path(product_dir)}/output/image-plan.json: modern plan requires exactly 8 detail images")
    contract = image_plan["generator_contract"]
    if contract["style_profile_ref"] != expected_ref:
        errors.append(f"{display_path(product_dir)}/output/image-plan.json: generator contract has wrong style profile")
    if contract["allowed_structure"] != image_plan["image_set_structure"]:
        errors.append(f"{display_path(product_dir)}/output/image-plan.json: generator contract structure mismatch")
    # New image production writes image-qc-report.json. Legacy style checks
    # remain applicable only when the earlier qc-report.json exists.
    if qc_report is None:
        return errors
    if qc_report["style_profile_ref"] != expected_ref:
        errors.append(f"{display_path(product_dir)}/output/qc-report.json: style_profile_ref must be {expected_ref}")
    if qc_report["selected_style_family"] != style_profile["style_family"]:
        errors.append(f"{display_path(product_dir)}/output/qc-report.json: selected style does not match style-profile.json")
    if qc_report["style_alignment"]["style_family"] != style_profile["style_family"]:
        errors.append(f"{display_path(product_dir)}/output/qc-report.json: style alignment family mismatch")

    required_style_checks = {
        "selected_style_match",
        "electronics_not_overly_home",
        "outdoor_has_outdoor_scene",
        "kitchen_has_kitchen_home_feel",
        "style_product_conflict",
    }
    actual_checks = {item["check"] for item in qc_report["style_alignment"]["checks"]}
    missing_checks = required_style_checks - actual_checks
    if missing_checks:
        errors.append(
            f"{display_path(product_dir)}/output/qc-report.json: missing style checks: "
            + ", ".join(sorted(missing_checks))
        )
    return errors


def validate_product(product_dir: Path) -> List[str]:
    product_dir = product_dir.resolve()
    errors = validate_directory(product_dir)
    if errors:
        return errors

    for rel_file, schema_file in FILE_SCHEMAS.items():
        errors.extend(validate_schema(product_dir / rel_file, TEMPLATES / schema_file))
    for rel_file, schema_file in OPTIONAL_FILE_SCHEMAS.items():
        path = product_dir / rel_file
        if path.is_file():
            errors.extend(validate_schema(path, TEMPLATES / schema_file))
            if rel_file == "output/image-qc-report.json":
                errors.extend(
                    f"{display_path(path)}:{error}"
                    for error in validate_image_qc_report(load_json(path))
                )

    if errors:
        return errors

    source = load_json(product_dir / "input/source.json")
    status = load_json(product_dir / "status.json")
    errors.extend(validate_source_truthfulness(source, product_dir))
    errors.extend(validate_positioning_integrity(product_dir))
    errors.extend(validate_history(status, product_dir))
    errors.extend(validate_status_integrity(status, product_dir))
    errors.extend(validate_style_integrity(product_dir))
    return errors


def validate_collector_product(product_dir: Path) -> List[str]:
    product_dir = product_dir.resolve()
    errors = validate_required_paths(product_dir, COLLECTOR_REQUIRED_DIRS, COLLECTOR_FILE_SCHEMAS)
    if not (product_dir / "input/raw-snapshot.json").is_file():
        errors.append(f"{display_path(product_dir)}: missing file input/raw-snapshot.json")
    if errors:
        return errors

    for rel_file, schema_file in COLLECTOR_FILE_SCHEMAS.items():
        errors.extend(validate_schema(product_dir / rel_file, TEMPLATES / schema_file))

    if errors:
        return errors

    source = load_json(product_dir / "input/source.json")
    status = load_json(product_dir / "status.json")
    errors.extend(validate_source_truthfulness(source, product_dir))
    errors.extend(validate_history(status, product_dir))
    errors.extend(validate_status_integrity(status, product_dir))
    if status["status"] not in {"COLLECTED", "FAILED_HARD_BLOCKER", "COLLECTING"}:
        errors.append(f"{display_path(product_dir)}/status.json: collector stage cannot finish with {status['status']}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate one stage-1 product directory.")
    parser.add_argument("product_dir", help="Path to products/{product_id}")
    parser.add_argument("--check-upload-gate", action="store_true", help="Also print whether upload is allowed.")
    parser.add_argument("--collector-only", action="store_true", help="Validate only phase-2 collector files.")
    args = parser.parse_args()

    product_dir = Path(args.product_dir)
    errors = validate_collector_product(product_dir) if args.collector_only else validate_product(product_dir)
    if errors:
        print("FAILED")
        for error in errors:
            print(f"- {error}")
        return 1

    print(f"PASS {product_dir}")
    if args.check_upload_gate:
        status = load_json(product_dir / "status.json")
        print(f"upload_allowed={str(can_start_upload(status)).lower()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
