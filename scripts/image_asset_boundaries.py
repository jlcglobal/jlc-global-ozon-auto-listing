#!/usr/bin/env python3
"""Hard filesystem boundaries for source, generated, rejected and accepted images.

The directories are the authority.  A status string or a coincidentally equal
filename can never turn an AI candidate into a source image or an accepted
upload asset.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List

try:
    from scripts.production_input_guard import ProductionInputError, validate_registered_input_file
except ModuleNotFoundError:
    from production_input_guard import ProductionInputError, validate_registered_input_file


IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".avif"}
INPUT_IMAGE_DIRS = ("sku-images", "main-images", "detail-images")
OUTPUT_IMAGE_DIRS = ("generated-images", "rejected-generation", "accepted-images")


def project_root_for(product_dir: Path) -> Path:
    product_dir = product_dir.resolve()
    return product_dir.parent.parent if product_dir.parent.name == "products" else product_dir


def resolve_product_path(product_dir: Path, value: Any) -> Path:
    raw = Path(str(value or "").strip())
    root = project_root_for(product_dir)
    return raw.resolve() if raw.is_absolute() else (root / raw).resolve()


def _inside(path: Path, root: Path) -> bool:
    root = root.resolve()
    path = path.resolve()
    return path == root or root in path.parents


def source_roots(product_dir: Path) -> List[Path]:
    return [(product_dir / "input" / name).resolve() for name in INPUT_IMAGE_DIRS]


def ensure_asset_directories(product_dir: Path) -> Dict[str, Path]:
    paths: Dict[str, Path] = {}
    for name in INPUT_IMAGE_DIRS:
        path = product_dir / "input" / name
        path.mkdir(parents=True, exist_ok=True)
        paths[f"input_{name.replace('-', '_')}"] = path
    for name in OUTPUT_IMAGE_DIRS:
        path = product_dir / "output" / name
        path.mkdir(parents=True, exist_ok=True)
        paths[name.replace('-', '_')] = path
    for name in ("variant-main", "detail"):
        (product_dir / "output/generated-images" / name).mkdir(parents=True, exist_ok=True)
    return paths


def asset_contract_path(product_dir: Path) -> Path:
    return product_dir / "output/image-asset-contract.json"


def contract_enabled(product_dir: Path) -> bool:
    return asset_contract_path(product_dir).is_file()


def write_asset_contract(
    product_dir: Path,
    *,
    collection_id: str,
    manual_confirmation_required: bool = True,
) -> Dict[str, Any]:
    """Create the versioned marker used by new products and upload gating."""
    import json

    ensure_asset_directories(product_dir)
    value: Dict[str, Any] = {
        "schema_version": "1.0.0",
        "product_id": product_dir.name,
        "collection_id": collection_id,
        "source_kind": "workbench_collection",
        "manual_confirmation_required": bool(manual_confirmation_required),
        "candidate_root": f"products/{product_dir.name}/output/generated-images",
        "accepted_root": f"products/{product_dir.name}/output/accepted-images",
        "rejected_root": f"products/{product_dir.name}/output/rejected-generation",
    }
    path = asset_contract_path(product_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temporary = Path(handle.name)
    temporary.replace(path)
    return value


def accepted_manifest_path(product_dir: Path) -> Path:
    return product_dir / "output/accepted-images/manifest.json"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_json(path: Path, value: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        temporary = Path(handle.name)
    temporary.replace(path)


def _load_json(path: Path) -> Dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def design_version_hash(product_dir: Path) -> str:
    """Hash the immutable design inputs, excluding review bookkeeping."""
    design = product_dir / "output/ozon-ecommerce-design.json"
    if design.is_file():
        return _sha256(design)
    plan = _load_json(product_dir / "output/image-plan.json")
    normalized = []
    for group in ("main_images", "detail_images"):
        for item in plan.get(group) or []:
            normalized.append({
                "group": group,
                "slot": item.get("slot"),
                "source_sku_id": item.get("source_sku_id"),
                "commercial_role": item.get("commercial_role") or item.get("role") or item.get("image_type"),
                "output_path": item.get("output_path"),
                "prompt": item.get("prompt") or item.get("generation_prompt"),
                "source_refs": item.get("source_refs") or item.get("reference_product_images") or [],
            })
    payload = json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _plan_item_for_candidate(product_dir: Path, candidate: Path) -> tuple[str, Dict[str, Any]]:
    plan = _load_json(product_dir / "output/image-plan.json")
    matches: List[tuple[str, Dict[str, Any]]] = []
    for group in ("main_images", "detail_images"):
        for item in plan.get(group) or []:
            raw = item.get("output_path") or item.get("path") or item.get("local_path")
            if raw and resolve_product_path(product_dir, raw) == candidate.resolve():
                matches.append((group, item))
    if len(matches) != 1:
        raise ValueError("候选图片必须且只能对应当前image plan中的一个槽位")
    return matches[0]


def _manifest_base(product_dir: Path) -> Dict[str, Any]:
    contract = _load_json(asset_contract_path(product_dir))
    return {
        "schema_version": "1.0.0",
        "product_id": product_dir.name,
        "collection_id": str(contract.get("collection_id") or "unknown"),
        "design_version_hash": design_version_hash(product_dir),
        "entries": [],
    }


def invalidate_all_accepted(product_dir: Path, reason: str) -> None:
    root = product_dir / "output/accepted-images"
    for path in _images_below(root):
        path.unlink(missing_ok=True)
    value = _manifest_base(product_dir)
    value.update({"invalidated_at": datetime.now(timezone.utc).isoformat(), "invalidation_reason": reason})
    _atomic_json(accepted_manifest_path(product_dir), value)


def revoke_stale_acceptances(product_dir: Path) -> bool:
    manifest = _load_json(accepted_manifest_path(product_dir))
    if not manifest:
        return False
    current = _manifest_base(product_dir)
    if (
        manifest.get("product_id") != current["product_id"]
        or manifest.get("collection_id") != current["collection_id"]
        or manifest.get("design_version_hash") != current["design_version_hash"]
    ):
        invalidate_all_accepted(product_dir, "collection_or_design_changed")
        return True
    return False


def accepted_counterpart(product_dir: Path, candidate: Path) -> Path:
    candidate = candidate.resolve()
    generated = (product_dir / "output/generated-images").resolve()
    if not _inside(candidate, generated):
        raise ValueError("only a generated candidate can be accepted")
    return (product_dir / "output/accepted-images" / candidate.relative_to(generated)).resolve()


def rejected_counterpart(product_dir: Path, candidate: Path, *, group: str | None = None) -> Path:
    candidate = candidate.resolve()
    generated = (product_dir / "output/generated-images").resolve()
    if not _inside(candidate, generated):
        raise ValueError("only a generated candidate can be rejected")
    safe_group = group or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return (product_dir / "output/rejected-generation" / safe_group / candidate.relative_to(generated)).resolve()


def accept_candidate(
    product_dir: Path,
    value: Any,
    *,
    confirmed_by: str = "unknown",
    confirmed_at: str | None = None,
) -> Path:
    """Copy one reviewed candidate into the accepted tree without touching input."""
    candidate = validate_generated_output(product_dir, value)
    if not candidate.is_file():
        raise ValueError(f"candidate does not exist: {value}")
    revoke_stale_acceptances(product_dir)
    target = accepted_counterpart(product_dir, candidate)
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("wb", dir=target.parent, delete=False) as handle:
        with candidate.open("rb") as source:
            shutil.copyfileobj(source, handle)
        temporary = Path(handle.name)
    temporary.replace(target)
    shutil.copystat(candidate, target)
    group, item = _plan_item_for_candidate(product_dir, candidate)
    manifest = _load_json(accepted_manifest_path(product_dir)) or _manifest_base(product_dir)
    current_base = _manifest_base(product_dir)
    for key in ("schema_version", "product_id", "collection_id", "design_version_hash"):
        manifest[key] = current_base[key]
    entries = [entry for entry in manifest.get("entries") or [] if entry.get("slot") != item.get("slot")]
    root = project_root_for(product_dir)
    entries.append({
        "product_id": product_dir.name,
        "collection_id": manifest["collection_id"],
        "slot": str(item.get("slot") or "unknown"),
        "sku_id": str(item.get("source_sku_id") or "shared"),
        "shared_detail_role": (
            str(item.get("commercial_role") or item.get("role") or item.get("image_type") or "detail")
            if group == "detail_images" else None
        ),
        "candidate_path": str(candidate.resolve().relative_to(root.resolve())),
        "accepted_path": str(target.resolve().relative_to(root.resolve())),
        "sha256": _sha256(target),
        "confirmed_by": confirmed_by,
        "confirmed_at": confirmed_at or datetime.now(timezone.utc).isoformat(),
        "design_version_hash": manifest["design_version_hash"],
    })
    manifest["entries"] = sorted(entries, key=lambda entry: str(entry.get("slot") or ""))
    _atomic_json(accepted_manifest_path(product_dir), manifest)
    return target


def invalidate_accepted_candidate(product_dir: Path, value: Any) -> None:
    """A replacement/regeneration invalidates only its previous accepted copy."""
    candidate = validate_generated_output(product_dir, value)
    target = accepted_counterpart(product_dir, candidate)
    target.unlink(missing_ok=True)
    manifest_path = accepted_manifest_path(product_dir)
    manifest = _load_json(manifest_path)
    if manifest:
        relative = str(target.resolve().relative_to(project_root_for(product_dir).resolve()))
        manifest["entries"] = [
            entry for entry in manifest.get("entries") or []
            if str(entry.get("accepted_path") or "") != relative
        ]
        manifest["updated_at"] = datetime.now(timezone.utc).isoformat()
        _atomic_json(manifest_path, manifest)


def validate_accepted_manifest(
    product_dir: Path,
    planned_items: Iterable[Dict[str, Any]],
    *,
    expected_count: int,
    revoke_stale: bool = True,
) -> List[str]:
    """Verify the user's exact N+8 confirmation set before any upload call."""
    errors: List[str] = []
    if revoke_stale:
        revoke_stale_acceptances(product_dir)
    manifest = _load_json(accepted_manifest_path(product_dir))
    current = _manifest_base(product_dir)
    if not manifest:
        return ["缺少人工确认图片清单 manifest.json"]
    for field in ("product_id", "collection_id", "design_version_hash"):
        if manifest.get(field) != current.get(field):
            errors.append("人工确认清单与当前商品、采集版本或设计版本不一致")
            break
    items = list(planned_items)
    expected_slots = {str(item.get("slot") or "") for item in items}
    entries = list(manifest.get("entries") or [])
    if len(entries) != expected_count or {str(item.get("slot") or "") for item in entries} != expected_slots:
        errors.append(f"人工确认清单必须覆盖当前N+8全部槽位；期望{expected_count}张，实际{len(entries)}张")
    for entry in entries:
        try:
            accepted = validate_accepted_output(product_dir, entry.get("accepted_path"))
        except ValueError as exc:
            errors.append(str(exc))
            continue
        if _sha256(accepted) != str(entry.get("sha256") or ""):
            errors.append(f"已确认图片在确认后发生变化：{entry.get('slot') or accepted.name}")
        if entry.get("design_version_hash") != current["design_version_hash"]:
            errors.append(f"图片确认对应旧设计版本：{entry.get('slot') or accepted.name}")
    return list(dict.fromkeys(errors))


def reject_candidate(product_dir: Path, value: Any, *, group: str | None = None) -> Path:
    """Move a rejected candidate out of the active set; never delete the source input."""
    candidate = validate_generated_output(product_dir, value)
    if not candidate.is_file():
        raise ValueError(f"candidate does not exist: {value}")
    invalidate_accepted_candidate(product_dir, candidate)
    target = rejected_counterpart(product_dir, candidate, group=group)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        target = target.with_name(f"{target.stem}-{datetime.now(timezone.utc).strftime('%H%M%S%f')}{target.suffix}")
    shutil.move(str(candidate), str(target))
    return target


def validate_product_reference(product_dir: Path, value: Any, *, require_exists: bool = True) -> Path:
    path = resolve_product_path(product_dir, value)
    if not any(_inside(path, root) for root in source_roots(product_dir)):
        raise ValueError(
            "product reference must be under input/sku-images, input/main-images, "
            f"or input/detail-images; output and layout baselines are forbidden: {value}"
        )
    if require_exists and not path.is_file():
        raise ValueError(f"product reference does not exist: {value}")
    try:
        validate_registered_input_file(product_dir, path)
    except ProductionInputError as exc:
        raise ValueError(str(exc)) from exc
    return path


def validate_generated_output(product_dir: Path, value: Any) -> Path:
    path = resolve_product_path(product_dir, value)
    root = (product_dir / "output/generated-images").resolve()
    if not _inside(path, root):
        raise ValueError(f"generated image output must stay under output/generated-images: {value}")
    if any(_inside(path, source) for source in source_roots(product_dir)):
        raise ValueError(f"generated image output must never overwrite input: {value}")
    return path


def validate_accepted_output(product_dir: Path, value: Any, *, require_exists: bool = True) -> Path:
    path = resolve_product_path(product_dir, value)
    root = (product_dir / "output/accepted-images").resolve()
    if not _inside(path, root):
        raise ValueError(f"accepted image must stay under output/accepted-images: {value}")
    if require_exists and not path.is_file():
        raise ValueError(f"accepted image does not exist: {value}")
    return path


def classify_path(product_dir: Path, value: Any) -> str:
    path = resolve_product_path(product_dir, value)
    for name in INPUT_IMAGE_DIRS:
        if _inside(path, product_dir / "input" / name):
            return "original"
    for name, label in (
        ("generated-images", "candidate"),
        ("rejected-generation", "rejected"),
        ("accepted-images", "accepted"),
    ):
        if _inside(path, product_dir / "output" / name):
            return label
    return "outside"


def _images_below(path: Path) -> Iterable[Path]:
    if not path.is_dir():
        return []
    return sorted(
        item for item in path.rglob("*")
        if item.is_file() and item.suffix.lower() in IMAGE_SUFFIXES
    )


def asset_inventory(product_dir: Path) -> Dict[str, List[Dict[str, str]]]:
    root = project_root_for(product_dir)
    groups = {
        "original": [product_dir / "input" / name for name in INPUT_IMAGE_DIRS],
        "candidate": [product_dir / "output/generated-images"],
        "rejected": [product_dir / "output/rejected-generation"],
        "accepted": [product_dir / "output/accepted-images"],
    }
    result: Dict[str, List[Dict[str, str]]] = {}
    for label, directories in groups.items():
        files = [item for directory in directories for item in _images_below(directory)]
        result[label] = [{
            # macOS may spell the same temporary directory as /var or
            # /private/var. Resolve both sides before making the project path.
            "path": str(item.resolve().relative_to(root.resolve())),
            "name": item.name,
            "bucket": label,
        } for item in files]
    return result
