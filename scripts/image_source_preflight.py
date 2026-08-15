#!/usr/bin/env python3
"""Check image references once and recover original 1688 SKU images safely."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import tempfile
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

from PIL import Image

try:
    from scripts.sku_image_bindings import load_sku_image_bindings, project_relative, selected_sku_ids
except ModuleNotFoundError:  # pragma: no cover - direct execution from scripts/
    from sku_image_bindings import load_sku_image_bindings, project_relative, selected_sku_ids


ROOT = Path(__file__).resolve().parents[1]
MIN_REFERENCE_SIDE = 600
# 512-599px references are usable for reference editing but receive a warning.
# They are not enlarged, cut out, or used for deterministic measurement/comparison
# compositions. Only references below this floor remain a hard blocker.
MIN_USABLE_REFERENCE_SIDE = 512
MAX_SOURCE_IMAGE_BYTES = 25 * 1024 * 1024
ALLOWED_SOURCE_IMAGE_HOST_SUFFIXES = ("1688.com", "alicdn.com")
ALLOWED_OZON_REFERENCE_IMAGE_HOST_SUFFIXES = (
    "ozone.ru",
    "ozon.ru",
    "ozonusercontent.com",
)


def _product_data_root(product_dir: Path) -> Path:
    resolved = product_dir.resolve()
    if resolved.parent.name == "products":
        return resolved.parent.parent
    return ROOT.resolve()


def _resolve_current_product_path(product_dir: Path, value: Any) -> Path:
    raw = Path(str(value or "").strip())
    if raw.is_absolute():
        return raw.resolve()
    data_root = _product_data_root(product_dir)
    return (data_root / raw).resolve()


def validate_source_image_url(url: str, allowed_host_suffixes: Tuple[str, ...] | None = None) -> str:
    """Allow only public 1688/Alibaba CDN image URLs.

    Source-image URLs originate in page content and therefore must not be able
    to redirect the local service to localhost, a LAN host, or an arbitrary
    external server.
    """
    value = str(url or "").strip()
    try:
        parsed = urllib.parse.urlparse(value)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("invalid source image URL") from exc
    hostname = str(parsed.hostname or "").lower().rstrip(".")
    suffixes = allowed_host_suffixes or ALLOWED_SOURCE_IMAGE_HOST_SUFFIXES
    allowed_host = any(
        hostname == suffix or hostname.endswith(f".{suffix}")
        for suffix in suffixes
    )
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 80, 443}
        or not allowed_host
    ):
        raise ValueError("source image URL is outside the trusted source CDN")
    return value


class _SourceImageRedirectHandler(urllib.request.HTTPRedirectHandler):
    def __init__(self, allowed_host_suffixes: Tuple[str, ...] | None = None):
        self.allowed_host_suffixes = allowed_host_suffixes
        super().__init__()

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_source_image_url(newurl, self.allowed_host_suffixes)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def open_source_image_url(
    request: urllib.request.Request,
    timeout: int = 20,
    allowed_host_suffixes: Tuple[str, ...] | None = None,
):
    validate_source_image_url(request.full_url, allowed_host_suffixes)
    return urllib.request.build_opener(_SourceImageRedirectHandler(allowed_host_suffixes)).open(request, timeout=timeout)


def read_source_image_response(response) -> Tuple[bytes, str]:
    content_type = str(response.headers.get("Content-Type") or "")
    if content_type and not content_type.lower().startswith("image/"):
        raise ValueError("source URL did not return an image")
    content = response.read(MAX_SOURCE_IMAGE_BYTES + 1)
    if len(content) > MAX_SOURCE_IMAGE_BYTES:
        raise ValueError("source image is larger than 25MB")
    return content, content_type


def load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json_atomic(path: Path, value: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temporary = Path(handle.name)
    temporary.replace(path)


def source_image_candidates(url: str) -> List[str]:
    """Prefer the original Alibaba image before its small thumbnail URL."""
    value = str(url or "").strip()
    candidates: List[str] = []
    if "alicdn.com/" in value or "1688.com/" in value:
        original = re.sub(r"_sum\.(?:jpe?g|png|webp)$", "", value, flags=re.IGNORECASE)
        original = re.sub(r"_(?:\d+x\d+|q\d+)\.(?:jpe?g|png|webp)$", "", original, flags=re.IGNORECASE)
        if original != value:
            candidates.append(original)
    candidates.append(value)
    return list(dict.fromkeys(item for item in candidates if item))


def source_image_key(url: str) -> str:
    """Return a stable Alibaba image key for thumbnail/gallery matching."""
    value = str(url or "").strip()
    if not value or value == "unknown":
        return ""
    parsed = urllib.parse.urlparse(value)
    name = urllib.parse.unquote(Path(parsed.path).name).lower()
    if not name:
        return ""
    name = re.sub(r"_sum(?=\.)", "", name)
    for _ in range(3):
        stem = re.sub(r"\.(?:jpe?g|png|webp|avif)$", "", name, flags=re.IGNORECASE)
        stem = stem.rstrip("_.")
        if stem == name:
            break
        name = stem
    return name


def image_dimensions(path: Path) -> Tuple[int, int]:
    try:
        with Image.open(path) as image:
            return int(image.width), int(image.height)
    except (FileNotFoundError, OSError, ValueError):
        return 0, 0


def _download(url: str, timeout: int = 20) -> Tuple[bytes, str]:
    request = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 crossborder-ai-factory-image-preflight/1.0",
        "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
    })
    with open_source_image_url(request, timeout=timeout) as response:
        return read_source_image_response(response)


def _extension(content_type: str, image_format: str) -> str:
    mapping = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp", "AVIF": ".avif"}
    if image_format in mapping:
        return mapping[image_format]
    if "png" in content_type:
        return ".png"
    if "webp" in content_type:
        return ".webp"
    return ".jpg"


def recover_source_image(url: str, destination_base: Path) -> Dict[str, Any] | None:
    best: Tuple[int, bytes, str, str, int, int] | None = None
    for candidate in source_image_candidates(url):
        try:
            content, content_type = _download(candidate)
            with Image.open(io.BytesIO(content)) as image:
                width, height, image_format = image.width, image.height, str(image.format or "JPEG")
                image.verify()
        except (OSError, ValueError):
            continue
        score = min(width, height)
        if best is None or score > best[0]:
            best = (score, content, content_type, image_format, width, height)
        if score >= MIN_REFERENCE_SIDE:
            break
    if best is None:
        return None
    _, content, content_type, image_format, width, height = best
    path = destination_base.with_suffix(_extension(content_type, image_format))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return {
        "path": project_relative(path),
        "width": width,
        "height": height,
        "sha256": hashlib.sha256(content).hexdigest(),
    }


def _sku_id(value: Dict[str, Any]) -> str:
    return str(value.get("sku_id") or value.get("skuId") or "").strip()


def _confirmed_reference_overrides(product_dir: Path) -> Dict[str, Dict[str, Any]]:
    """Return only explicit, narrowly-scoped manual image-sharing decisions."""
    path = product_dir / "input/manual-confirmation.json"
    if not path.is_file():
        return {}
    raw = load_json(path).get("sku_image_reference_overrides") or {}
    if not isinstance(raw, dict):
        return {}
    confirmed: Dict[str, Dict[str, Any]] = {}
    for target_sku_id, value in raw.items():
        if not isinstance(value, dict):
            continue
        if (
            value.get("decision") != "user_confirmed_same_appearance"
            or value.get("scope") != "reference_image_only"
            or value.get("must_preserve_target_sku_facts") is not True
            or not str(value.get("source_sku_id") or "").strip()
        ):
            continue
        confirmed[str(target_sku_id)] = value
    return confirmed


def _all_captured_skus(product_dir: Path, source: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Index selected and unselected captured SKUs without mutating source facts."""
    values: List[Dict[str, Any]] = []
    raw_path = product_dir / "input/raw-snapshot.json"
    if raw_path.is_file():
        raw = load_json(raw_path)
        for collection in (
            raw.get("sku_raw_data") or [],
            (raw.get("raw_snapshot") or {}).get("all_raw_skus") or [],
        ):
            values.extend(item for item in collection if isinstance(item, dict))
    # Selected source records are materialized last so their downloaded local
    # image paths take precedence over the raw capture copy of the same SKU.
    values.extend(item for item in source.get("skus") or [] if isinstance(item, dict))
    return {_sku_id(item): item for item in values if _sku_id(item)}


def _existing_reference(product_dir: Path, item: Dict[str, Any]) -> Dict[str, Any] | None:
    for field in ("variant_local_image_path", "local_image_path", "image_path", "sku_image_path", "image_local_path"):
        relative = str(item.get(field) or "unknown")
        if relative == "unknown":
            continue
        path = _resolve_current_product_path(product_dir, relative)
        width, height = image_dimensions(path)
        if path.is_file() and min(width, height) > 0:
            return {
                "path": project_relative(path),
                "width": width,
                "height": height,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
    return None


def _sku_image_keys(sku: Dict[str, Any]) -> List[str]:
    keys = [
        source_image_key(sku.get("variant_image_url")),
        source_image_key(sku.get("image_url")),
        source_image_key((sku.get("source_data") or {}).get("variant_image_url")),
        source_image_key((sku.get("source_data") or {}).get("image_url")),
    ]
    return list(dict.fromkeys(item for item in keys if item))


def _inside(path: Path, root: Path) -> bool:
    resolved = path.resolve()
    allowed = root.resolve()
    return resolved == allowed or allowed in resolved.parents


def _gallery_local_path(item: Dict[str, Any]) -> str:
    return str(
        item.get("local_path")
        or item.get("variant_local_image_path")
        or item.get("image_path")
        or ""
    ).strip()


def _auto_single_sku_gallery_reference(
    product_dir: Path,
    source: Dict[str, Any],
    target_sku: Dict[str, Any],
) -> Dict[str, Any] | None:
    """Use a current-product gallery/detail image when a single SKU thumbnail is unusable.

    This is intentionally narrower than manual SKU image sharing: it only runs
    for one selected SKU, never crosses products, and excludes gallery images
    that can be tied to an unselected 1688 SKU image URL.
    """
    selected_skus = [item for item in source.get("skus") or [] if isinstance(item, dict)]
    if len(selected_skus) != 1:
        return None
    selected_id = _sku_id(target_sku)
    selected_keys = set(_sku_image_keys(target_sku))
    all_skus = _all_captured_skus(product_dir, source)
    unselected_keys = {
        key
        for sku_id, sku in all_skus.items()
        if sku_id != selected_id
        for key in _sku_image_keys(sku)
    }
    candidates: List[Tuple[Tuple[int, int, int, int], Dict[str, Any]]] = []
    for role, collection in (("main", source.get("main_images") or []), ("detail", source.get("detail_images") or [])):
        for item in collection:
            if not isinstance(item, dict):
                continue
            if item.get("download_status") not in {None, "", "downloaded", "unknown"}:
                continue
            relative = _gallery_local_path(item)
            if not relative or relative == "unknown":
                continue
            path = _resolve_current_product_path(product_dir, relative)
            if not (
                _inside(path, product_dir / "input/main-images")
                or _inside(path, product_dir / "input/detail-images")
            ):
                continue
            width, height = image_dimensions(path)
            if not path.is_file() or min(width, height) < MIN_USABLE_REFERENCE_SIDE:
                continue
            key = source_image_key(
                item.get("original_url")
                or item.get("source_url")
                or item.get("url")
            )
            if key and key in unselected_keys and key not in selected_keys:
                continue
            exact_match = int(bool(key and key in selected_keys))
            role_bonus = 1 if role == "detail" else 0
            score = (exact_match, role_bonus, min(width, height), width * height)
            candidates.append((score, {
                "path": project_relative(path),
                "width": width,
                "height": height,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "reference_override": {
                    "decision": "auto_single_sku_gallery_reference",
                    "scope": "reference_image_only",
                    "target_sku_id": selected_id,
                    "target_sku_name": str(target_sku.get("sku_name") or "unknown"),
                    "selected_image_path": project_relative(path),
                    "selected_image_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    "source_type": f"{role}_gallery_reference",
                    "match_kind": "sku_url_exact" if exact_match else "single_sku_current_product_gallery",
                    "must_preserve_target_sku_facts": True,
                    "source_ref": f"products/{product_dir.name}/input/source.json",
                },
            }))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


def recover_confirmed_reference_override(
    product_dir: Path,
    source: Dict[str, Any],
    target_sku: Dict[str, Any],
    override: Dict[str, Any],
    allow_download: bool,
) -> Dict[str, Any] | None:
    """Resolve a user-confirmed same-appearance source image for one target SKU."""
    target_sku_id = _sku_id(target_sku)
    source_sku_id = str(override.get("source_sku_id") or "").strip()
    if not source_sku_id or source_sku_id == target_sku_id:
        return None
    source_sku = _all_captured_skus(product_dir, source).get(source_sku_id)
    if not source_sku:
        return None
    recovered = _existing_reference(product_dir, source_sku)
    if recovered is None and allow_download:
        source_url = str(source_sku.get("variant_image_url") or source_sku.get("image_url") or "")
        safe_target = re.sub(r"[^A-Za-z0-9._-]+", "-", target_sku_id).strip("-") or "target"
        safe_source = re.sub(r"[^A-Za-z0-9._-]+", "-", source_sku_id).strip("-") or "source"
        recovered = recover_source_image(
            source_url,
            product_dir / "input/sku-images/manual-overrides" / f"{safe_target}-from-{safe_source}",
        )
    if recovered is None:
        return None
    return {
        **recovered,
        "reference_override": {
            "decision": "user_confirmed_same_appearance",
            "scope": "reference_image_only",
            "target_sku_id": target_sku_id,
            "target_sku_name": str(target_sku.get("sku_name") or "unknown"),
            "source_sku_id": source_sku_id,
            "source_sku_name": str(source_sku.get("sku_name") or override.get("source_sku_name") or "unknown"),
            "confirmed_at": str(override.get("confirmed_at") or "unknown"),
            "must_preserve_target_sku_facts": True,
            "source_ref": f"products/{product_dir.name}/input/manual-confirmation.json",
        },
    }


def build_preflight(product_dir: Path, allow_download: bool = False) -> Dict[str, Any]:
    product_dir = product_dir.resolve()
    source = load_json(product_dir / "input/source.json")
    confirmed_overrides = _confirmed_reference_overrides(product_dir)
    user_bindings = load_sku_image_bindings(product_dir, selected_sku_ids(source), strict=False)
    sku_results = []
    for index, sku in enumerate(source.get("skus") or [], start=1):
        original_path = str(
            sku.get("variant_local_image_path")
            or sku.get("local_image_path")
            or sku.get("image_path")
            or sku.get("sku_image_path")
            or sku.get("image_local_path")
            or "unknown"
        )
        original_file = _resolve_current_product_path(product_dir, original_path) if original_path != "unknown" else Path("/missing")
        original_width, original_height = image_dimensions(original_file)
        preferred = {
            "path": original_path,
            "width": original_width,
            "height": original_height,
            "sha256": hashlib.sha256(original_file.read_bytes()).hexdigest() if original_file.is_file() else "unknown",
        }
        if min(original_width, original_height) < MIN_REFERENCE_SIDE and allow_download:
            safe_id = re.sub(r"[^A-Za-z0-9._-]+", "-", str(sku.get("sku_id") or index)).strip("-") or str(index)
            recovered = recover_source_image(
                str(sku.get("variant_image_url") or sku.get("image_url") or ""),
                product_dir / "input/sku-images/source-upgrades" / safe_id,
            )
            if recovered and min(recovered["width"], recovered["height"]) > min(preferred["width"], preferred["height"]):
                preferred = recovered
        override = confirmed_overrides.get(_sku_id(sku))
        if override and (
            min(preferred["width"], preferred["height"]) < MIN_USABLE_REFERENCE_SIDE
            or override.get("force_reference_override") is True
        ):
            recovered_override = recover_confirmed_reference_override(
                product_dir, source, sku, override, allow_download,
            )
            if recovered_override and override.get("force_reference_override") is True:
                preferred = recovered_override
            elif recovered_override and min(recovered_override["width"], recovered_override["height"]) > min(preferred["width"], preferred["height"]):
                preferred = recovered_override
        binding = user_bindings.get(_sku_id(sku))
        if binding and min(preferred["width"], preferred["height"]) < MIN_USABLE_REFERENCE_SIDE:
            bound_path = _resolve_current_product_path(product_dir, binding["selected_image_path"])
            bound_width, bound_height = image_dimensions(bound_path)
            if bound_path.is_file() and min(bound_width, bound_height) > 0:
                preferred = {
                    "path": binding["selected_image_path"],
                    "width": bound_width,
                    "height": bound_height,
                    "sha256": binding["selected_image_sha256"],
                    "reference_override": {
                        "decision": "user_bound_reference_image",
                        "scope": "reference_image_only",
                        "target_sku_id": _sku_id(sku),
                        "target_sku_name": str(sku.get("sku_name") or "unknown"),
                        "selected_image_path": binding["selected_image_path"],
                        "selected_image_sha256": binding["selected_image_sha256"],
                        "source_type": binding["source_type"],
                        "bound_by": binding.get("bound_by") or "user",
                        "bound_at": binding.get("bound_at") or "unknown",
                        "must_preserve_target_sku_facts": True,
                        "source_ref": f"products/{product_dir.name}/input/sku-image-bindings.json",
                    },
                }
        if min(preferred["width"], preferred["height"]) < MIN_USABLE_REFERENCE_SIDE:
            gallery_reference = _auto_single_sku_gallery_reference(product_dir, source, sku)
            if gallery_reference:
                preferred = gallery_reference
        preferred_short_side = min(preferred["width"], preferred["height"])
        ready = preferred_short_side >= MIN_REFERENCE_SIDE
        usable_with_warning = 0 < preferred_short_side < MIN_REFERENCE_SIDE
        reference_status = "ready" if ready else "ready_with_warning" if usable_with_warning else "blocked"
        reference_override = preferred.get("reference_override")
        if reference_status != "blocked" and reference_override and reference_override.get("decision") == "user_bound_reference_image":
            reason = (
                "已使用你绑定的本商品采集图作为该SKU参考图；"
                "仅作为产品主体参考，SKU规格、颜色、重量、尺寸和文案仍使用当前SKU资料；"
                "低清图不用于证明尺寸、结构细节或SKU对比。"
            )
        elif reference_status != "blocked" and reference_override:
            if reference_override.get("decision") == "auto_single_sku_gallery_reference":
                reason = (
                    "单SKU的SKU小图清晰度不足，已自动匹配本商品已采集主图/详情图作为产品主体参考；"
                    "SKU规格、重量、价格、尺寸和文案保持不变；低清图不用于证明新事实。"
                )
            else:
                reason = (
                    f"已按人工确认使用同外观SKU {reference_override['source_sku_id']} 的真实图片；"
                    "仅作为产品主体参考，目标SKU规格、重量、价格、尺寸和文案保持不变。"
                )
        elif ready:
            reason = "原始商品图清晰度可用于生图参考。"
        elif usable_with_warning:
            reason = (
                f"SKU参考图短边为{preferred_short_side}px，低于推荐{MIN_REFERENCE_SIDE}px；"
                "仅作为产品主体参考用于图生图，不放大、不抠图、不从低清图推断尺寸、结构细节或SKU对比事实。"
            )
        else:
            reason = (
                "该SKU缺少参考图，请从本商品已采集图片中选择一张绑定后继续。"
                if original_path == "unknown" or sku.get("sku_image_missing")
                else "SKU参考图文件不可读，无法作为产品主体参考。"
            )
        result = {
            "source_sku_id": str(sku.get("sku_id") or "unknown"),
            "sku_name": str(sku.get("sku_name") or "unknown"),
            "original_reference_path": original_path,
            "original_dimensions": {"width": original_width, "height": original_height},
            "preferred_reference_path": preferred["path"],
            "preferred_dimensions": {"width": preferred["width"], "height": preferred["height"]},
            "preferred_sha256": preferred["sha256"],
            "status": reference_status,
            "reason": reason,
            "subject_reference_only": bool(usable_with_warning),
            "proof_ready": bool(ready),
        }
        if reference_override:
            result["reference_override"] = reference_override
        sku_results.append(result)
    main_results = []
    for item in source.get("main_images") or []:
        path = str(item.get("local_path") or "unknown")
        width, height = image_dimensions(ROOT / path) if path != "unknown" else (0, 0)
        main_results.append({
            "id": str(item.get("id") or "unknown"), "path": path,
            "width": width, "height": height,
            "status": "ready" if min(width, height) >= MIN_REFERENCE_SIDE else "ready_with_warning" if min(width, height) > 0 else "missing",
            "subject_reference_only": bool(0 < min(width, height) < MIN_REFERENCE_SIDE),
            "proof_ready": bool(min(width, height) >= MIN_REFERENCE_SIDE),
        })
    blocked = [item["source_sku_id"] for item in sku_results if item["status"] == "blocked"]
    value = {
        "schema_version": "1.0.0",
        "product_id": product_dir.name,
        "checked_at": datetime.now().astimezone().replace(microsecond=0).isoformat(),
        "minimum_reference_side_px": MIN_REFERENCE_SIDE,
        "status": "PASS" if not blocked else "BLOCKED",
        "check_scope": "once_before_image_generation",
        "sku_references": sku_results,
        "main_references": main_results,
        "blocked_sku_ids": blocked,
        "rules": {
            "low_resolution_upscale_forbidden": True,
            "thumbnail_pixel_cutout_forbidden": True,
            "warning_reference_side_px": MIN_REFERENCE_SIDE,
            "minimum_usable_reference_side_px": MIN_USABLE_REFERENCE_SIDE,
            "low_resolution_current_product_images_are_subject_references": True,
            "comparison_and_measurement_images_require_deterministic_real_image_composition": True,
            "lifestyle_images_allow_ai_reference_edit": True,
        },
    }
    write_json_atomic(product_dir / "output/image-source-preflight.json", value)
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("product_dir")
    parser.add_argument(
        "--allow-download",
        action="store_true",
        help="collector-only compatibility option; formal image generation must not mutate input",
    )
    args = parser.parse_args()
    value = build_preflight(Path(args.product_dir), allow_download=args.allow_download)
    print(json.dumps(value, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
