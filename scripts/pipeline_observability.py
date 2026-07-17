"""Per-product pipeline cache and performance accounting."""
from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable


ROOT = Path(__file__).resolve().parents[1]
PIPELINE_CACHE_VERSION = "6"
EXTERNAL_IMAGE_ADVISORY_SKILLS = [
    Path.home() / ".codex/skills/ecommerce-branding/SKILL.md",
]
EXTERNAL_COPY_ADVISORY_SKILLS = [
    Path.home() / ".codex/skills/keyword-research/SKILL.md",
]

STEP_OUTPUTS = {
    "validate_source": ["input/source.json", "input/raw-snapshot.json"],
    "product_analysis": ["output/product-analysis.json"],
    "category_match": ["output/ozon-category.json", "output/ozon-category-attributes.json"],
    "variant_rules": ["output/platform-grouping-result.json"],
    "measurements": ["output/cost-analysis.json", "output/pricing-result.json", "output/profit-analysis.json"],
    "offer_exists_check": ["output/offer-id-precheck.json"],
    "upload_feasibility": ["output/upload-feasibility.json"],
    "product_positioning": ["output/product-positioning.json"],
    "russian_copy": [
        "output/copy-ru.json", "output/marketplace-content-input.json",
        "output/keyword-research-ru.json",
    ],
    "style_selector": ["output/style-profile.json"],
    "image_plan": ["output/image-plan.json"],
    "image_generation": [],
    "image_qc": ["output/image-qc-report.json"],
    "marketplace_content": ["output/ozon-draft.json", "output/title-ru.json", "output/description-ru.json", "output/keywords-ru.json"],
    "field_completion": [
        "output/ozon-tags.json", "output/ozon-attributes-final.json",
        "output/attribute-coverage-report.json",
    ],
    "final_upload_check": ["output/final-upload-check.json", "output/ozon-draft.json"],
    "ozon_upload": ["output/ozon-result.json"],
}

STEP_INPUTS = {
    "product_analysis": [],
    "category_match": ["output/product-analysis.json", "output/product-positioning.json"],
    "variant_rules": ["output/ozon-category.json", "output/ozon-category-attributes.json"],
    "measurements": ["output/product-analysis.json", "output/ozon-category.json"],
    "offer_exists_check": ["output/pricing-result.json", "output/ozon-category.json"],
    "upload_feasibility": [
        "output/cost-analysis.json", "output/pricing-result.json", "output/ozon-category.json",
        "output/ozon-category-attributes.json", "output/ozon-attributes.json", "output/offer-id-precheck.json",
    ],
    "product_positioning": ["output/product-analysis.json"],
    "russian_copy": ["output/product-analysis.json", "output/product-positioning.json"],
    "style_selector": ["output/product-analysis.json", "output/product-positioning.json", "input/visual-preference.json"],
    "image_plan": [
        "output/product-analysis.json", "output/product-positioning.json", "output/style-profile.json",
        "output/cost-analysis.json", "output/title-ru.json", "output/description-ru.json",
        "output/ozon-tags.json", "output/ozon-attributes-final.json", "output/pricing-result.json",
        "output/platform-grouping-result.json", "input/visual-preference.json",
    ],
    "image_generation": [
        "output/image-plan.json", "output/style-profile.json", "output/product-analysis.json",
        "output/product-positioning.json", "output/copy-ru.json", "output/image-regeneration-request.json",
    ],
    "image_qc": ["output/image-plan.json"],
    "marketplace_content": [
        "output/marketplace-content-input.json", "output/product-analysis.json",
        "output/cost-analysis.json", "output/pricing-result.json", "output/profit-analysis.json",
    ],
    "field_completion": [
        "output/ozon-draft.json", "output/ozon-category.json", "output/ozon-category-attributes.json",
        "output/title-ru.json", "output/description-ru.json", "output/keywords-ru.json",
        "output/pricing-result.json",
    ],
    "final_upload_check": [
        "output/marketplace-content-input.json", "output/ozon-draft.json",
        "output/ozon-tags.json", "output/ozon-attributes-final.json",
        "output/image-plan.json", "output/image-qc-report.json", "output/pricing-result.json",
    ],
    "ozon_upload": [
        "output/final-upload-check.json", "output/ozon-draft.json", "output/ozon-upload-config.json",
        "output/ozon-attributes-final.json", "output/rich-content.json", "output/color-variants.json",
    ],
}

ROOT_RULE_INPUTS = {
    "russian_copy": [
        ".agents/skills/full-product-pipeline/SKILL.md",
        "rules/russian-copy-rules.md",
        *EXTERNAL_COPY_ADVISORY_SKILLS,
    ],
    "category_match": ["rules/ozon_category_profiles.json", "rules/ozon-rules.md"],
    "variant_rules": ["ozon-adapter/metadata/live-aspect-rules"],
    "measurements": ["pricing-engine/pricing_rules.json", "pricing-engine/shipping_rules.xlsx"],
    "style_selector": ["rules/style_selector_rules.json", "rules/image_structure_rules.json"],
    "image_plan": [
        "rules/image_structure_rules.json", "scripts/image_planner.py",
        "templates/image-plan.schema.json", "config/pipeline-settings.json",
    ],
    "image_generation": [
        ".agents/skills/image-generator/SKILL.md", "scripts/image_generator_contract.py",
        "scripts/locked_product_compositor.py", "scripts/image_slot_scheduler.py",
        "config/pipeline-settings.json", *EXTERNAL_IMAGE_ADVISORY_SKILLS,
    ],
    "image_qc": ["rules/image_qc_rules.json", "scripts/image_qc.py"],
    "marketplace_content": ["rules/marketplace_content_rules.json"],
    "field_completion": ["ozon-field-completion/ozon_field_completion/service.py"],
    "final_upload_check": [
        "rules/marketplace_content_rules.json",
        "ozon-field-completion/ozon_field_completion/service.py",
    ],
}


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _write(path: Path, value: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temporary = Path(handle.name)
    temporary.replace(path)


def _load(path: Path, default: Dict[str, Any]) -> Dict[str, Any]:
    if not path.is_file():
        return default
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _expand_path(path: Path) -> list[Path]:
    if path.is_dir():
        return sorted(item for item in path.rglob("*") if item.is_file())
    return [path] if path.is_file() else []


def _image_output_paths(product_dir: Path) -> list[Path]:
    plan_path = product_dir / "output/image-plan.json"
    if not plan_path.is_file():
        return []
    plan = _load(plan_path, {})
    paths = []
    for key in ("main_images", "detail_images", "disclaimer_images"):
        for item in plan.get(key) or []:
            output = item.get("output_path")
            slot = item.get("slot")
            if output:
                paths.append(ROOT / str(output))
            if slot:
                paths.append(product_dir / "output/product-lock" / f"{slot}.json")
    return paths


def input_hash(product_dir: Path, step: str, extra_paths: Iterable[Path] = ()) -> str:
    paths = [product_dir / "input/source.json", product_dir / "input/raw-snapshot.json"]
    paths.extend(product_dir / relative for relative in STEP_INPUTS.get(step, []))
    paths.extend(ROOT / relative for relative in ROOT_RULE_INPUTS.get(step, []))
    paths.extend(extra_paths)
    if step in {"product_analysis", "image_plan", "image_generation", "image_qc"}:
        paths.append(product_dir / "input")
    if step == "image_qc":
        paths.append(product_dir / "output/generated-images")
        paths.append(product_dir / "output/product-lock")
    digest = hashlib.sha256(f"{PIPELINE_CACHE_VERSION}:{step}".encode())
    expanded = [item for path in paths for item in _expand_path(path)]
    for path in sorted({path.resolve() for path in expanded}):
        digest.update(str(path).encode())
        digest.update(_digest(path).encode())
    return digest.hexdigest()


def outputs_exist(product_dir: Path, step: str) -> bool:
    if step == "image_generation":
        paths = _image_output_paths(product_dir)
        return bool(paths) and all(path.is_file() for path in paths)
    return all((product_dir / relative).is_file() for relative in STEP_OUTPUTS.get(step, []))


def output_hash(product_dir: Path, step: str) -> str:
    digest = hashlib.sha256()
    if step == "image_generation":
        for path in _image_output_paths(product_dir):
            if path.is_file():
                digest.update(str(path.relative_to(ROOT)).encode())
                digest.update(_digest(path).encode())
        return digest.hexdigest()
    for relative in STEP_OUTPUTS.get(step, []):
        path = product_dir / relative
        if path.is_file():
            digest.update(relative.encode())
            digest.update(_digest(path).encode())
    return digest.hexdigest()


def cache_hit(product_dir: Path, step: str, value: str) -> bool:
    cache = _load(product_dir / "output/pipeline-cache.json", {"schema_version": "1.0.0", "steps": {}})
    entry = cache.get("steps", {}).get(step, {})
    return (
        entry.get("input_hash") == value
        and outputs_exist(product_dir, step)
        and entry.get("output_hash") == output_hash(product_dir, step)
    )


def cache_store(product_dir: Path, step: str, value: str) -> None:
    path = product_dir / "output/pipeline-cache.json"
    cache = _load(path, {"schema_version": "1.0.0", "product_id": product_dir.name, "steps": {}})
    cache.setdefault("steps", {})[step] = {
        "input_hash": value,
        "output_hash": output_hash(product_dir, step),
        "stored_at": timestamp(),
    }
    _write(path, cache)


def shared_analysis_input_hash(product_dir: Path) -> str:
    """Hash stable source facts and actual image bytes, excluding capture-local paths."""
    source = _load(product_dir / "input/source.json", {})
    stable_source = {
        "source_url": source.get("source_url"),
        "title_cn": source.get("title_cn"),
        "product_attributes": source.get("product_attributes") or [],
        "skus": [
            {
                "sku_id": item.get("sku_id"),
                "sku_name": item.get("sku_name"),
                "option_values": item.get("option_values") or [],
                "purchase_price": item.get("purchase_price"),
                "original_image_url": item.get("original_image_url"),
            }
            for item in source.get("skus") or []
        ],
    }
    digest = hashlib.sha256(
        json.dumps(stable_source, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    )
    for path in sorted((product_dir / "input").rglob("*")):
        if path.is_file() and path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}:
            digest.update(_digest(path).encode())
    for path in (
        ROOT / "AGENTS.md",
        ROOT / ".agents/skills/full-product-pipeline/SKILL.md",
        ROOT / "templates/product-analysis.schema.json",
    ):
        if path.is_file():
            digest.update(_digest(path).encode())
    return digest.hexdigest()


def _replace_product_refs(value: Any, old_product_id: str, new_product_id: str) -> Any:
    if isinstance(value, dict):
        return {
            key: _replace_product_refs(item, old_product_id, new_product_id)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_replace_product_refs(item, old_product_id, new_product_id) for item in value]
    if isinstance(value, str):
        return value.replace(f"products/{old_product_id}/", f"products/{new_product_id}/")
    return value


def shared_analysis_cache_restore(product_dir: Path, cache_key: str) -> bool:
    path = ROOT / "cache/image-recognition" / f"{cache_key}.json"
    if not path.is_file():
        return False
    entry = _load(path, {})
    if entry.get("input_hash") != cache_key or not isinstance(entry.get("analysis"), dict):
        return False
    old_product_id = str(entry.get("source_product_id") or product_dir.name)
    analysis = _replace_product_refs(copy.deepcopy(entry["analysis"]), old_product_id, product_dir.name)
    analysis["product_id"] = product_dir.name
    _write(product_dir / "output/product-analysis.json", analysis)
    return True


def shared_analysis_cache_store(product_dir: Path, cache_key: str) -> None:
    analysis_path = product_dir / "output/product-analysis.json"
    if not analysis_path.is_file():
        return
    _write(ROOT / "cache/image-recognition" / f"{cache_key}.json", {
        "schema_version": "1.0.0",
        "input_hash": cache_key,
        "source_product_id": product_dir.name,
        "stored_at": timestamp(),
        "analysis": _load(analysis_path, {}),
    })


def prune_shared_analysis_cache(retention_days: int) -> int:
    root = ROOT / "cache/image-recognition"
    if not root.is_dir():
        return 0
    cutoff = time.time() - max(1, int(retention_days)) * 86400
    removed = 0
    for path in root.glob("*.json"):
        if path.stat().st_mtime < cutoff:
            path.unlink(missing_ok=True)
            removed += 1
    return removed


def performance_start(product_dir: Path, step: str, cache_hit_value: bool) -> float:
    return time.monotonic()


def performance_finish(product_dir: Path, step: str, started: float, cache_hit_value: bool, status: str, retries: int = 0, network_wait: float = 0.0) -> None:
    path = product_dir / "output/performance-report.json"
    report = _load(path, {"schema_version": "1.0.0", "product_id": product_dir.name, "steps": [], "total_seconds": 0.0})
    duration = round(time.monotonic() - started, 3)
    finished = datetime.now(timezone.utc)
    status_path = product_dir / "status.json"
    batch_id = _load(status_path, {}).get("batch_id", "unknown")
    report.setdefault("steps", []).append({
        "step": step,
        "batch_id": batch_id,
        "started_at": (finished - timedelta(seconds=duration)).isoformat(timespec="seconds"),
        "finished_at": finished.isoformat(timespec="seconds"),
        "duration_seconds": duration,
        "retry_count": retries,
        "cache_hit": cache_hit_value,
        "network_wait_seconds": round(network_wait, 3),
        "image_generation_seconds": duration if step == "image_generation" else 0.0,
        "status": status,
    })
    report["total_seconds"] = round(sum(item["duration_seconds"] for item in report["steps"]), 3)
    report.setdefault("batch_totals", {})[str(batch_id)] = round(sum(
        float(item.get("duration_seconds") or 0)
        for item in report["steps"]
        if item.get("batch_id") == batch_id
    ), 3)
    _write(path, report)
