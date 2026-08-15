#!/usr/bin/env python3
"""User-bound SKU reference images for current workbench products.

Bindings are deliberately separate from ``input/source.json``.  A bound image is
only a current-product visual reference chosen by the operator from already
collected input images; it never becomes a 1688 SKU-owned image.
"""
from __future__ import annotations

import hashlib
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List

try:
    from scripts.image_asset_boundaries import validate_product_reference
except ModuleNotFoundError:  # pragma: no cover - script execution from scripts/
    from image_asset_boundaries import validate_product_reference


ROOT = Path(__file__).resolve().parents[1]
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".avif", ".gif", ".bmp"}


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().replace(microsecond=0).isoformat()


def project_relative(path: Path) -> str:
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(ROOT.resolve()))
    except ValueError:
        parts = resolved.parts
        try:
            products_index = parts.index("products")
        except ValueError:
            return str(resolved)
        return str(Path(*parts[products_index:]))


def load_json(path: Path, default: Dict[str, Any] | None = None) -> Dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return dict(default or {})


def write_json_atomic(path: Path, value: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        temporary = Path(handle.name)
    temporary.replace(path)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def binding_path(product_dir: Path) -> Path:
    return product_dir / "input/sku-image-bindings.json"


def resolve_product_path(product_dir: Path, value: Any) -> Path:
    raw = Path(str(value or "").strip())
    return raw.resolve() if raw.is_absolute() else (ROOT / raw).resolve()


def source_type_for_path(product_dir: Path, value: Any) -> str:
    path = validate_product_reference(product_dir, value)
    input_dir = (product_dir / "input").resolve()
    try:
        relative = path.resolve().relative_to(input_dir)
    except ValueError as exc:
        raise ValueError("绑定图片必须来自当前商品input目录") from exc
    bucket = relative.parts[0] if relative.parts else ""
    if bucket == "main-images":
        return "main_gallery_reference"
    if bucket == "detail-images":
        return "detail_reference"
    if bucket == "sku-images":
        return "sku_image"
    raise ValueError("绑定图片必须来自当前商品 main-images/detail-images/sku-images")


def _candidate_from_source(
    product_dir: Path,
    *,
    image_type: str,
    index: int,
    label: str,
    value: Dict[str, Any],
) -> Dict[str, Any] | None:
    local_path = str(value.get("local_path") or value.get("variant_local_image_path") or value.get("image_path") or "").strip()
    if not local_path or local_path == "unknown":
        return None
    try:
        path = validate_product_reference(product_dir, local_path)
    except ValueError:
        return None
    return {
        "id": f"{image_type}-{index}",
        "path": project_relative(path),
        "sha256": sha256_file(path),
        "source_type": source_type_for_path(product_dir, path),
        "image_type": image_type,
        "source_index": index,
        "label": label,
    }


def available_binding_candidates(product_dir: Path, source: Dict[str, Any] | None = None) -> List[Dict[str, Any]]:
    """Return current-product collected images that can be bound to a missing SKU."""
    source = source or load_json(product_dir / "input/source.json")
    candidates: List[Dict[str, Any]] = []
    for image_type, values in (
        ("main", source.get("main_images") or []),
        ("detail", source.get("detail_images") or []),
    ):
        for index, item in enumerate(values):
            if not isinstance(item, dict):
                continue
            candidate = _candidate_from_source(
                product_dir,
                image_type=image_type,
                index=index,
                label=f"1688{'主图' if image_type == 'main' else '详情图'} {index + 1}",
                value=item,
            )
            if candidate:
                candidates.append(candidate)
    for index, sku in enumerate(source.get("skus") or []):
        if not isinstance(sku, dict):
            continue
        candidate = _candidate_from_source(
            product_dir,
            image_type="sku",
            index=index,
            label=f"SKU图 {sku.get('sku_name') or sku.get('sku_id') or index + 1}",
            value={
                "local_path": sku.get("variant_local_image_path")
                or sku.get("local_image_path")
                or sku.get("sku_image_path")
                or sku.get("image_local_path"),
            },
        )
        if candidate:
            candidates.append(candidate)
    unique: Dict[str, Dict[str, Any]] = {}
    for item in candidates:
        unique.setdefault(str(item["path"]), item)
    return list(unique.values())


def selected_sku_ids(source: Dict[str, Any]) -> List[str]:
    return [
        str(item.get("sku_id") or "")
        for item in source.get("skus") or []
        if str(item.get("sku_id") or "")
    ]


def _normalize_binding(product_dir: Path, sku_id: str, value: Dict[str, Any]) -> Dict[str, Any]:
    selected_path = str(value.get("selected_image_path") or "").strip()
    if not selected_path:
        raise ValueError("绑定图片路径为空")
    path = validate_product_reference(product_dir, selected_path)
    sha = sha256_file(path)
    recorded_sha = str(value.get("selected_image_sha256") or sha).strip()
    if recorded_sha and recorded_sha != sha:
        raise ValueError("绑定图片哈希已变化，请重新选择")
    return {
        "product_id": product_dir.name,
        "collection_id": str(value.get("collection_id") or load_json(product_dir / "input/source.json").get("collection_id") or ""),
        "sku_id": str(value.get("sku_id") or sku_id),
        "selected_image_path": project_relative(path),
        "selected_image_sha256": sha,
        "source_type": source_type_for_path(product_dir, path),
        "bound_by": str(value.get("bound_by") or "user"),
        "bound_at": str(value.get("bound_at") or now_iso()),
        "binding_kind": "user_bound_reference_image",
        "scope": "reference_image_only",
        "must_preserve_target_sku_facts": True,
    }


def load_sku_image_bindings(
    product_dir: Path,
    selected_sku_ids_filter: Iterable[str] | None = None,
    *,
    strict: bool = True,
) -> Dict[str, Dict[str, Any]]:
    raw = load_json(binding_path(product_dir))
    bindings = raw.get("bindings") or {}
    if not isinstance(bindings, dict):
        return {}
    allowed = set(str(item) for item in selected_sku_ids_filter or [])
    result: Dict[str, Dict[str, Any]] = {}
    for sku_id, value in bindings.items():
        if allowed and str(sku_id) not in allowed:
            continue
        if not isinstance(value, dict):
            if strict:
                raise ValueError("SKU图片绑定格式错误")
            continue
        try:
            normalized = _normalize_binding(product_dir, str(sku_id), value)
        except ValueError:
            if strict:
                raise
            continue
        result[str(sku_id)] = normalized
    return result


def save_sku_image_binding(
    product_dir: Path,
    sku_id: str,
    selected_image_path: str,
    *,
    bound_by: str = "user",
) -> Dict[str, Any]:
    source = load_json(product_dir / "input/source.json")
    valid_skus = set(selected_sku_ids(source))
    if str(sku_id) not in valid_skus:
        raise ValueError("SKU不属于当前商品")
    path = validate_product_reference(product_dir, selected_image_path)
    record = _normalize_binding(product_dir, str(sku_id), {
        "product_id": product_dir.name,
        "collection_id": source.get("collection_id"),
        "sku_id": str(sku_id),
        "selected_image_path": project_relative(path),
        "selected_image_sha256": sha256_file(path),
        "bound_by": bound_by,
        "bound_at": now_iso(),
    })
    current = load_json(binding_path(product_dir), {
        "schema_version": "1.0.0",
        "product_id": product_dir.name,
        "collection_id": str(source.get("collection_id") or ""),
        "bindings": {},
    })
    current.update({
        "schema_version": "1.0.0",
        "product_id": product_dir.name,
        "collection_id": str(source.get("collection_id") or ""),
        "updated_at": now_iso(),
    })
    current.setdefault("bindings", {})[str(sku_id)] = record
    write_json_atomic(binding_path(product_dir), current)
    return record


def sku_owned_image_path(product_dir: Path, sku: Dict[str, Any]) -> str:
    for field in ("variant_local_image_path", "local_image_path", "image_path", "sku_image_path", "image_local_path"):
        value = str(sku.get(field) or "").strip()
        if not value or value == "unknown":
            continue
        try:
            path = validate_product_reference(product_dir, value)
        except ValueError:
            continue
        if path.is_file():
            return project_relative(path)
    return ""


def first_current_product_main_image(product_dir: Path) -> Dict[str, Any] | None:
    source = load_json(product_dir / "input/source.json")
    for index, item in enumerate(source.get("main_images") or []):
        if not isinstance(item, dict):
            continue
        candidate = _candidate_from_source(
            product_dir,
            image_type="main",
            index=index,
            label=f"1688主图 {index + 1}",
            value=item,
        )
        if candidate:
            return candidate
    return None


def is_single_spec_sku(product_dir: Path, sku: Dict[str, Any]) -> bool:
    source = load_json(product_dir / "input/source.json")
    skus = [
        item for item in source.get("skus") or []
        if not item.get("excluded") and str(item.get("sku_id") or "")
    ]
    if len(skus) != 1:
        return False
    identity = str(sku.get("sku_identity_type") or skus[0].get("sku_identity_type") or "").casefold()
    name = str(sku.get("sku_name") or skus[0].get("sku_name") or "").strip()
    options = sku.get("option_values")
    if not isinstance(options, list):
        options = skus[0].get("option_values")
    return (
        identity == "single_specification"
        or name in {"单规格", "默认", "default"}
        or options == []
    )


def effective_sku_reference(
    product_dir: Path,
    sku: Dict[str, Any],
    bindings: Dict[str, Dict[str, Any]] | None = None,
) -> Dict[str, Any] | None:
    sku_id = str(sku.get("sku_id") or "")
    owned = sku_owned_image_path(product_dir, sku)
    if owned and not sku.get("sku_image_missing"):
        path = validate_product_reference(product_dir, owned)
        return {
            "sku_id": sku_id,
            "reference_kind": "sku_owned_image",
            "path": project_relative(path),
            "sha256": sha256_file(path),
            "source_type": "sku_image",
        }
    binding = (bindings or {}).get(sku_id)
    if binding:
        return {
            "sku_id": sku_id,
            "reference_kind": "user_bound_reference_image",
            "path": binding["selected_image_path"],
            "sha256": binding["selected_image_sha256"],
            "source_type": binding["source_type"],
            "binding": binding,
        }
    if sku.get("sku_image_missing") and is_single_spec_sku(product_dir, sku):
        candidate = first_current_product_main_image(product_dir)
        if candidate:
            return {
                "sku_id": sku_id,
                "reference_kind": "single_spec_main_gallery_reference",
                "path": candidate["path"],
                "sha256": candidate["sha256"],
                "source_type": candidate["source_type"],
            }
    return None
