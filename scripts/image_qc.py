#!/usr/bin/env python3
"""Score generated Ozon images without calling any model API.

Codex supplies semantic visual checks from the current session. This module
verifies image files, applies fixed weights, enforces critical failures, and
calculates the final decision deterministically.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[1]
RULES_PATH = ROOT / "rules" / "image_qc_rules.json"
SCHEMA_PATH = ROOT / "templates" / "image-qc-report.schema.json"


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temp_path = Path(handle.name)
    temp_path.replace(path)


def repo_path(path: str) -> Path:
    candidate = Path(path)
    return candidate if candidate.is_absolute() else ROOT / candidate


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_png_size(path: Path) -> Tuple[int, int]:
    with path.open("rb") as handle:
        signature = handle.read(24)
    if len(signature) < 24 or signature[:8] != b"\x89PNG\r\n\x1a\n" or signature[12:16] != b"IHDR":
        raise ValueError("not a readable PNG image")
    return struct.unpack(">II", signature[16:24])


def all_plan_items(plan: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    for key in ("main_images", "detail_images", "disclaimer_images"):
        yield from plan.get(key, [])


def decision_for(score: int, critical_failures: List[str], rules: Dict[str, Any]) -> str:
    if critical_failures or score < rules["thresholds"]["revise_min"]:
        return "reject"
    if score < rules["thresholds"]["pass_min"]:
        return "revise"
    return "pass"


def recommendation_for(decision: str) -> str:
    return {
        "pass": "推荐进入人工审核；不得自动上传或发布。",
        "revise": "允许人工修改后重新执行图片质检。",
        "reject": "要求重新规划或重新生成图片后再执行质检。",
    }[decision]


def dimension_status(score: int, max_score: int) -> str:
    ratio = score / max_score
    if ratio >= 0.9:
        return "pass"
    if ratio >= 0.75:
        return "revise"
    return "reject"


def score_dimensions(assessment: Dict[str, Any], rules: Dict[str, Any]) -> Dict[str, Any]:
    expected_dimensions = rules["criteria"]
    actual_dimensions = assessment.get("dimensions", {})
    if set(actual_dimensions) != set(expected_dimensions):
        raise ValueError("assessment dimensions must exactly match image_qc_rules.json")

    dimensions = {}
    for dimension, criteria in expected_dimensions.items():
        supplied_checks = actual_dimensions[dimension]
        by_name = {item["criterion"]: item for item in supplied_checks}
        if set(by_name) != set(criteria) or len(by_name) != len(supplied_checks):
            raise ValueError(f"{dimension} criteria must exactly match image_qc_rules.json")

        scored_checks = []
        for criterion, max_score in criteria.items():
            supplied = by_name[criterion]
            deduction = supplied["deduction"]
            if not isinstance(deduction, int) or not 0 <= deduction <= max_score:
                raise ValueError(f"invalid deduction for {dimension}.{criterion}")
            if supplied["status"] == "pass" and deduction != 0:
                raise ValueError(f"pass check cannot deduct points: {dimension}.{criterion}")
            if supplied["status"] != "pass" and deduction == 0:
                raise ValueError(f"non-pass check must deduct points: {dimension}.{criterion}")
            evidence = supplied.get("evidence", [])
            if not evidence:
                raise ValueError(f"missing evidence for {dimension}.{criterion}")
            scored_checks.append({
                "criterion": criterion,
                "max_score": max_score,
                "deduction": deduction,
                "score": max_score - deduction,
                "status": supplied["status"],
                "message": supplied["message"],
                "evidence": evidence,
            })

        max_dimension_score = rules["weights"][dimension]
        if sum(criteria.values()) != max_dimension_score:
            raise ValueError(f"criterion weights do not sum to {dimension} weight")
        score = sum(item["score"] for item in scored_checks)
        dimensions[dimension] = {
            "max_score": max_dimension_score,
            "score": score,
            "status": dimension_status(score, max_dimension_score),
            "checks": scored_checks,
        }
    return dimensions


def inspect_images(
    product_dir: Path,
    plan: Dict[str, Any],
    slots: List[str],
    rules: Dict[str, Any],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]], List[str]]:
    items_by_slot = {item["slot"]: item for item in all_plan_items(plan)}
    if not slots or len(set(slots)) != len(slots):
        raise ValueError("assessment image_slots must contain unique planned slots")

    images_checked = []
    technical_checks = []
    issues = []
    critical_failures = []
    requirements = rules["technical_requirements"]
    target_ratio = 3 / 4
    preflight_ref = str((plan.get("generator_contract") or {}).get("source_preflight_ref") or "")
    preflight_path = repo_path(preflight_ref) if preflight_ref else None
    preflight = load_json(preflight_path) if preflight_path and preflight_path.is_file() else {}
    blocked_skus = {str(value) for value in preflight.get("blocked_sku_ids") or []}

    for slot in slots:
        if slot not in items_by_slot:
            raise ValueError(f"unknown image slot: {slot}")
        item = items_by_slot[slot]
        path = repo_path(item["output_path"])
        relative_path = str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path)
        images_checked.append({
            "slot": slot,
            "image_type": item["image_type"],
            "path": relative_path,
            "reference_images": item["reference_images"],
        })

        source_sku_id = str(item.get("source_sku_id") or "all")
        preflight_blocked = (
            bool(preflight_ref) and not preflight
            or source_sku_id in blocked_skus
            or item.get("image_type") == "comparison" and bool(blocked_skus)
        )
        if preflight_blocked:
            code = "source_preflight_missing" if preflight_ref and not preflight else "source_reference_too_small"
            critical_failures.append(code)
            issues.append({
                "code": code, "severity": "critical", "image_slots": [slot],
                "message": "生图前原图检查未通过，禁止放大低清缩略图或把结果用于上传。",
            })

        if (plan.get("generator_contract") or {}).get("product_pixel_lock_required") is True:
            lock_manifest_path = product_dir / "output/product-lock" / f"{slot}.json"
            if not lock_manifest_path.is_file():
                critical_failures.append("product_pixel_lock_missing")
                issues.append({
                    "code": "product_pixel_lock_missing", "severity": "critical",
                    "image_slots": [slot],
                    "message": "最终图片没有真实产品像素锁定清单，禁止上传。",
                })
            else:
                lock = load_json(lock_manifest_path)
                lock_ok = (
                    lock.get("mode") == "locked_product_composite"
                    and (lock.get("audit") or {}).get("status") == "pass"
                    and path.is_file()
                    and lock.get("output_sha256") == file_sha256(path)
                )
                if not lock_ok:
                    critical_failures.append("product_pixel_lock_failed")
                    issues.append({
                        "code": "product_pixel_lock_failed", "severity": "critical",
                        "image_slots": [slot],
                        "message": "真实产品图层哈希或像素审计不一致，禁止上传。",
                    })

        try:
            width, height = read_png_size(path)
            ratio_ok = abs((width / height) - target_ratio) <= requirements["aspect_ratio_tolerance"]
            resolution_ok = width >= requirements["minimum_width"] and height >= requirements["minimum_height"]
            if not ratio_ok:
                status = "reject"
                message = f"图片比例不是要求的 {requirements['aspect_ratio']}。"
                critical_failures.append("aspect_ratio_mismatch")
                issues.append({
                    "code": "aspect_ratio_mismatch",
                    "severity": "critical",
                    "image_slots": [slot],
                    "message": message,
                })
            elif not resolution_ok:
                status = "revise"
                message = "图片比例正确，但分辨率低于当前质检下限。"
                issues.append({
                    "code": "resolution_below_minimum",
                    "severity": "medium",
                    "image_slots": [slot],
                    "message": message,
                })
            else:
                status = "pass"
                message = "PNG可读取，分辨率和3:4比例符合当前规则。"
            technical_checks.append({
                "slot": slot,
                "path": relative_path,
                "format": "png",
                "width": width,
                "height": height,
                "aspect_ratio": requirements["aspect_ratio"],
                "status": status,
                "message": message,
            })
        except (FileNotFoundError, OSError, ValueError) as error:
            critical_failures.append("image_file_unreadable")
            issues.append({
                "code": "image_file_unreadable",
                "severity": "critical",
                "image_slots": [slot],
                "message": f"图片无法读取：{error}",
            })
            technical_checks.append({
                "slot": slot,
                "path": relative_path,
                "format": path.suffix.lower().lstrip(".") or "unknown",
                "width": 1,
                "height": 1,
                "aspect_ratio": "unknown",
                "status": "reject",
                "message": f"图片无法读取：{error}",
            })

    return images_checked, technical_checks, issues, critical_failures


def build_report(
    product_dir: Path,
    assessment: Dict[str, Any],
    checked_at: str | None = None,
) -> Dict[str, Any]:
    product_dir = product_dir.resolve()
    rules = load_json(RULES_PATH)
    plan = load_json(product_dir / "output" / "image-plan.json")
    dimensions = score_dimensions(assessment, rules)
    images, technical, technical_issues, technical_failures = inspect_images(
        product_dir, plan, assessment["image_slots"], rules
    )

    allowed_failures = set(rules["critical_failures"])
    semantic_failures = assessment.get("critical_failures", [])
    unknown_failures = set(semantic_failures) - allowed_failures
    if unknown_failures:
        raise ValueError("unknown critical failure codes: " + ", ".join(sorted(unknown_failures)))
    critical_failures = list(dict.fromkeys([*technical_failures, *semantic_failures]))
    score = sum(item["score"] for item in dimensions.values())
    decision = decision_for(score, critical_failures, rules)
    timestamp = checked_at or datetime.now().astimezone().replace(microsecond=0).isoformat()

    return {
        "schema_version": "1.0.0",
        "product_id": product_dir.name,
        "checked_at": timestamp,
        "source_refs": [
            f"products/{product_dir.name}/input/source.json",
            f"products/{product_dir.name}/output/product-analysis.json",
            f"products/{product_dir.name}/output/product-positioning.json",
            f"products/{product_dir.name}/output/style-profile.json",
            f"products/{product_dir.name}/output/image-plan.json",
        ],
        "images_checked": images,
        "technical_checks": technical,
        "dimensions": dimensions,
        "score": score,
        "decision": decision,
        "recommendation": recommendation_for(decision),
        "issues": [*assessment.get("issues", []), *technical_issues],
        "suggestions": assessment.get("suggestions", []),
        "critical_failures": critical_failures,
        "regenerate_needed": decision == "reject",
    }


def assessment_from_hard_gate(product_dir: Path, hard_gate: Dict[str, Any]) -> Dict[str, Any]:
    """Adapt the six user-approved hard failures to the legacy report envelope."""
    plan = load_json(product_dir / "output/image-plan.json")
    planned_slots = [item["slot"] for item in all_plan_items(plan) if item.get("status") != "needs_review"]
    checked_slots = [str(value) for value in hard_gate.get("checked_slots") or []]
    if set(checked_slots) != set(planned_slots):
        missing = sorted(set(planned_slots) - set(checked_slots))
        raise ValueError("hard gate did not check every generated slot: " + ", ".join(missing))
    rules = load_json(RULES_PATH)
    dimensions = {
        dimension: [
            {
                "criterion": criterion,
                "status": "pass",
                "deduction": 0,
                "message": "硬错误快检未发现阻断问题；不做美学评分。",
                "evidence": checked_slots,
            }
            for criterion in criteria
        ]
        for dimension, criteria in rules["criteria"].items()
    }
    return {
        "image_slots": checked_slots,
        "dimensions": dimensions,
        "critical_failures": hard_gate.get("critical_failures") or [],
        "issues": hard_gate.get("issues") or [],
        "suggestions": [],
    }


def validate_report(report: Dict[str, Any]) -> List[str]:
    schema = load_json(SCHEMA_PATH)
    errors = [
        f"{'/'.join(str(part) for part in error.path) or '<root>'}: {error.message}"
        for error in Draft202012Validator(schema).iter_errors(report)
    ]
    if errors:
        return sorted(errors)

    rules = load_json(RULES_PATH)
    for dimension, expected_max in rules["weights"].items():
        item = report["dimensions"][dimension]
        calculated = sum(check["score"] for check in item["checks"])
        if item["max_score"] != expected_max or item["score"] != calculated:
            errors.append(f"{dimension}: inconsistent dimension score")
    calculated_total = sum(item["score"] for item in report["dimensions"].values())
    if report["score"] != calculated_total:
        errors.append("score: inconsistent total")
    expected_decision = decision_for(report["score"], report["critical_failures"], rules)
    if report["decision"] != expected_decision:
        errors.append("decision: does not match thresholds or critical failures")
    if report["regenerate_needed"] != (expected_decision == "reject"):
        errors.append("regenerate_needed: inconsistent with decision")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Score generated product images for Ozon review.")
    parser.add_argument("product_dir", help="Path to products/{product_id}")
    parser.add_argument("--assessment", help="Codex visual assessment JSON used to build the report")
    parser.add_argument("--write", action="store_true", help="Write output/image-qc-report.json atomically")
    parser.add_argument("--verify-report", action="store_true", help="Validate the existing image-qc-report.json")
    parser.add_argument("--hard-gate", action="store_true", help="Build from output/image-hard-gate.json without aesthetic scoring")
    args = parser.parse_args()
    product_dir = Path(args.product_dir).resolve()

    if args.verify_report:
        report = load_json(product_dir / "output" / "image-qc-report.json")
        errors = validate_report(report)
        if errors:
            print("FAILED")
            for error in errors:
                print(f"- {error}")
            return 1
        print(f"PASS {product_dir / 'output' / 'image-qc-report.json'}")
        return 0

    if args.hard_gate:
        hard_gate_path = product_dir / "output/image-hard-gate.json"
        if not hard_gate_path.is_file():
            raise ValueError("missing output/image-hard-gate.json")
        report = build_report(product_dir, assessment_from_hard_gate(product_dir, load_json(hard_gate_path)))
        errors = validate_report(report)
        if errors:
            raise ValueError("generated image hard-gate report is invalid: " + "; ".join(errors))
        if args.write:
            output = product_dir / "output/image-qc-report.json"
            write_json_atomic(output, report)
            print(output)
        else:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    if not args.assessment:
        parser.error("--assessment is required unless --verify-report is used")
    report = build_report(product_dir, load_json(Path(args.assessment)))
    errors = validate_report(report)
    if errors:
        raise ValueError("generated image QC report is invalid: " + "; ".join(errors))
    if args.write:
        output = product_dir / "output" / "image-qc-report.json"
        write_json_atomic(output, report)
        print(output)
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
