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

try:
    from PIL import Image
except ImportError:  # pragma: no cover - the runtime venv provides Pillow
    Image = None


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


CN_COLOR_HSV = {
    "橙色": {"hue": (10, 50), "sat": (0.35, 1.0), "val": (0.35, 1.0)},
    "红色": {"hue": (345, 360), "sat": (0.40, 1.0), "val": (0.30, 1.0), "alt_hue": (0, 12)},
    "蓝色": {"hue": (190, 260), "sat": (0.30, 1.0), "val": (0.20, 1.0)},
    "绿色": {"hue": (70, 160), "sat": (0.30, 1.0), "val": (0.20, 1.0)},
    "黄色": {"hue": (45, 70), "sat": (0.35, 1.0), "val": (0.40, 1.0)},
    "紫色": {"hue": (260, 310), "sat": (0.30, 1.0), "val": (0.20, 1.0)},
    "粉色": {"hue": (310, 345), "sat": (0.25, 1.0), "val": (0.40, 1.0)},
    "金色": {"hue": (38, 55), "sat": (0.30, 1.0), "val": (0.45, 1.0)},
    "棕色": {"hue": (15, 38), "sat": (0.25, 1.0), "val": (0.20, 0.65)},
}


def hsv_fraction_matching(path: Path, color_name: str) -> float | None:
    """Return the fraction of pixels matching a Chinese color name in HSV space.

    Neutral colors (黑/白/灰/银/透明) return None: their presence cannot be
    distinguished from a scene background, so no check is performed.
    """
    spec = CN_COLOR_HSV.get(str(color_name or "").strip())
    if not spec or not path.is_file():
        return None
    try:
        from PIL import Image
    except ImportError:  # pragma: no cover
        return None
    try:
        with Image.open(path) as handle:
            small = handle.convert("RGB").resize((64, 64))
    except (OSError, ValueError):
        return None
    hue_lo, hue_hi = spec["hue"]
    alt = spec.get("alt_hue")
    sat_lo, sat_hi = spec["sat"]
    val_lo, val_hi = spec["val"]
    matched = 0
    total = 0
    for r, g, b in small.getdata():
        total += 1
        rn, gn, bn = r / 255.0, g / 255.0, b / 255.0
        mx, mn = max(rn, gn, bn), min(rn, gn, bn)
        val = mx
        sat = 0.0 if mx == 0 else (mx - mn) / mx
        if sat < sat_lo or sat > sat_hi or val < val_lo or val > val_hi:
            continue
        if mx == rn:
            hue = (gn - bn) / (mx - mn) * 60 % 360 if mx != mn else 0.0
        elif mx == gn:
            hue = ((bn - rn) / (mx - mn)) * 60 + 120 if mx != mn else 0.0
        else:
            hue = ((rn - gn) / (mx - mn)) * 60 + 240 if mx != mn else 0.0
        in_range = hue_lo <= hue < hue_hi
        if alt:
            in_range = in_range or alt[0] <= hue < alt[1]
        if in_range:
            matched += 1
    return matched / total if total else 0.0


def read_png_size(path: Path) -> Tuple[int, int]:
    with path.open("rb") as handle:
        signature = handle.read(24)
    if len(signature) < 24 or signature[:8] != b"\x89PNG\r\n\x1a\n" or signature[12:16] != b"IHDR":
        raise ValueError("not a readable PNG image")
    return struct.unpack(">II", signature[16:24])


def _detect_empty_placeholder_panel_with_pillow(image: Any) -> Dict[str, Any] | None:
    try:
        from PIL import ImageStat
    except ImportError:  # pragma: no cover
        return None
    gray = image.convert("L")
    width, height = gray.size
    if width < 120 or height < 160:
        return None
    y_ranges = [(0, int(height * 0.45)), (int(height * 0.55), height)]
    width_fracs = (0.50, 0.62, 0.74, 0.86)
    height_fracs = (0.07, 0.10, 0.14, 0.18, 0.22)
    best: Dict[str, Any] | None = None

    def stats(box: tuple[int, int, int, int]) -> tuple[float, float] | None:
        if box[0] >= box[2] or box[1] >= box[3]:
            return None
        stat = ImageStat.Stat(gray.crop(box))
        return float(stat.mean[0]), float(stat.stddev[0])

    for y_start, y_end in y_ranges:
        for height_frac in height_fracs:
            box_h = max(12, int(height * height_frac))
            if box_h >= y_end - y_start:
                continue
            for width_frac in width_fracs:
                box_w = max(60, int(width * width_frac))
                if box_w >= width:
                    continue
                for x in sorted({(width - box_w) // 2, int(width * 0.06), width - box_w - int(width * 0.06)}):
                    if x < 2 or x + box_w + 2 >= width:
                        continue
                    for y in range(y_start + 2, y_end - box_h - 1, max(3, box_h // 12)):
                        pad_x = max(3, int(box_w * 0.06))
                        pad_y = max(2, int(box_h * 0.12))
                        interior_stats = stats((x + pad_x, y + pad_y, x + box_w - pad_x, y + box_h - pad_y))
                        if not interior_stats:
                            continue
                        interior_mean, variance = interior_stats
                        if variance > 3.0 or (box_w / width) < 0.58:
                            continue
                        outside_boxes = [
                            (x + pad_x, max(0, y - 3), x + box_w - pad_x, y),
                            (x + pad_x, y + box_h, x + box_w - pad_x, min(height, y + box_h + 3)),
                            (max(0, x - 3), y + pad_y, x, y + box_h - pad_y),
                            (x + box_w, y + pad_y, min(width, x + box_w + 3), y + box_h - pad_y),
                        ]
                        edge_contrast = []
                        for box in outside_boxes:
                            outside_stats = stats(box)
                            if not outside_stats:
                                edge_contrast = []
                                break
                            edge_contrast.append(abs(interior_mean - outside_stats[0]))
                        if len(edge_contrast) != 4 or min(edge_contrast) < 10.0:
                            continue
                        score = (box_w / width) * (box_h / height) * (min(edge_contrast) / 255.0)
                        candidate = {
                            "x": round(x / width, 4),
                            "y": round(y / height, 4),
                            "width": round(box_w / width, 4),
                            "height": round(box_h / height, 4),
                            "interior_stddev": round(variance, 2),
                            "edge_contrast": [round(value, 2) for value in edge_contrast],
                            "score": round(score, 6),
                        }
                        if best is None or candidate["score"] > best["score"]:
                            best = candidate
    return best


def detect_empty_placeholder_panel(path: Path) -> Dict[str, Any] | None:
    """Detect a large, nearly empty rounded/rectangular text panel.

    Image models often leave a dark or light rounded rectangle where a sales
    message was requested.  It is not enough to check that the PNG exists: a
    panel with no useful text must be rejected before the product can continue.
    This intentionally conservative detector only considers broad bands near
    the top or bottom, requires a low-variance interior and a visible boundary
    on all four sides, and returns evidence for the UI error message.
    """
    if Image is None or not path.is_file():
        return None
    try:
        with Image.open(path) as source:
            image = source.convert("RGB")
            # Keep the scan deterministic and cheap for 3:4 images.
            width, height = image.size
            scale = min(1.0, 240.0 / max(width, 1))
            if scale < 1.0:
                image = image.resize((max(1, int(width * scale)), max(1, int(height * scale))))
            try:
                pixels = __import__("numpy").asarray(image, dtype="float32")
            except ImportError:
                return _detect_empty_placeholder_panel_with_pillow(image)
    except (OSError, ValueError):
        return None

    height, width = pixels.shape[:2]
    if width < 120 or height < 160:
        return None
    gray = pixels.mean(axis=2)
    # Panels are normally either in the lower information area or at the top.
    y_ranges = [(0, int(height * 0.45)), (int(height * 0.55), height)]
    width_fracs = (0.50, 0.62, 0.74, 0.86)
    height_fracs = (0.07, 0.10, 0.14, 0.18, 0.22)
    best: Dict[str, Any] | None = None

    for y_start, y_end in y_ranges:
        for height_frac in height_fracs:
            box_h = max(12, int(height * height_frac))
            if box_h >= y_end - y_start:
                continue
            for width_frac in width_fracs:
                box_w = max(60, int(width * width_frac))
                if box_w >= width:
                    continue
                # A few deterministic positions cover centered and slightly
                # offset panels without mistaking a full-width wall for one.
                for x in sorted({(width - box_w) // 2, int(width * 0.06), width - box_w - int(width * 0.06)}):
                    if x < 2 or x + box_w + 2 >= width:
                        continue
                    for y in range(y_start + 2, y_end - box_h - 1, max(3, box_h // 12)):
                        pad_x = max(3, int(box_w * 0.06))
                        pad_y = max(2, int(box_h * 0.12))
                        interior = gray[y + pad_y:y + box_h - pad_y, x + pad_x:x + box_w - pad_x]
                        if interior.size == 0:
                            continue
                        variance = float(interior.std())
                        # A blank panel is much more uniform than a product
                        # scene. Keep this threshold deliberately strict so a
                        # smooth wall, floor, or curtain is not misclassified.
                        # Text or a real product normally pushes variance well
                        # above this value.
                        if variance > 3.0 or (box_w / width) < 0.58:
                            continue
                        top_out = gray[max(0, y - 3):y, x + pad_x:x + box_w - pad_x]
                        bottom_out = gray[y + box_h:min(height, y + box_h + 3), x + pad_x:x + box_w - pad_x]
                        left_out = gray[y + pad_y:y + box_h - pad_y, max(0, x - 3):x]
                        right_out = gray[y + pad_y:y + box_h - pad_y, x + box_w:min(width, x + box_w + 3)]
                        if min(top_out.size, bottom_out.size, left_out.size, right_out.size) == 0:
                            continue
                        edge_contrast = [
                            abs(float(interior.mean()) - float(top_out.mean())),
                            abs(float(interior.mean()) - float(bottom_out.mean())),
                            abs(float(interior.mean()) - float(left_out.mean())),
                            abs(float(interior.mean()) - float(right_out.mean())),
                        ]
                        if min(edge_contrast) < 10.0:
                            continue
                        score = (box_w / width) * (box_h / height) * (min(edge_contrast) / 255.0)
                        candidate = {
                            "x": round(x / width, 4),
                            "y": round(y / height, 4),
                            "width": round(box_w / width, 4),
                            "height": round(box_h / height, 4),
                            "interior_stddev": round(variance, 2),
                            "edge_contrast": [round(value, 2) for value in edge_contrast],
                            "score": round(score, 6),
                        }
                        if best is None or candidate["score"] > best["score"]:
                            best = candidate
    return best


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
        "pass": "商品与SKU硬检查通过，可继续当前已授权批次；不增加人工门禁。",
        "revise": "只重试未通过的图片图位，已通过图片保持不变。",
        "reject": "按失败图位返回图片计划，禁止整套重新生成。",
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
            f"products/{product_dir.name}/output/ozon-ecommerce-design.json",
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
    """Project product-fidelity checks into the legacy report envelope."""
    plan = load_json(product_dir / "output/image-plan.json")
    planned_slots = [item["slot"] for item in all_plan_items(plan) if item.get("status") != "needs_review"]
    checked_slots = [
        str(value.get("slot") if isinstance(value, dict) else value)
        for value in hard_gate.get("checked_slots") or []
    ]
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
                "message": "硬错误快检未发现必须处理的问题；不做美学评分。",
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
