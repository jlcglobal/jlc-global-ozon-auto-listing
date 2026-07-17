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


ROOT = Path(__file__).resolve().parents[1]
MIN_REFERENCE_SIDE = 600
# 512-599px references are usable for reference editing but receive a warning.
# They are not enlarged, cut out, or used for deterministic measurement/comparison
# compositions. Only references below this floor remain a hard blocker.
MIN_USABLE_REFERENCE_SIDE = 512
MAX_SOURCE_IMAGE_BYTES = 25 * 1024 * 1024
ALLOWED_SOURCE_IMAGE_HOST_SUFFIXES = ("1688.com", "alicdn.com")


def validate_source_image_url(url: str) -> str:
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
    allowed_host = any(
        hostname == suffix or hostname.endswith(f".{suffix}")
        for suffix in ALLOWED_SOURCE_IMAGE_HOST_SUFFIXES
    )
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 80, 443}
        or not allowed_host
    ):
        raise ValueError("source image URL is outside the trusted 1688 CDN")
    return value


class _SourceImageRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_source_image_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def open_source_image_url(request: urllib.request.Request, timeout: int = 20):
    validate_source_image_url(request.full_url)
    return urllib.request.build_opener(_SourceImageRedirectHandler()).open(request, timeout=timeout)


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
        "path": str(path.relative_to(ROOT)),
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


def _existing_reference(item: Dict[str, Any]) -> Dict[str, Any] | None:
    for field in ("variant_local_image_path", "local_image_path"):
        relative = str(item.get(field) or "unknown")
        if relative == "unknown":
            continue
        path = ROOT / relative
        width, height = image_dimensions(path)
        if path.is_file() and min(width, height) > 0:
            return {
                "path": relative,
                "width": width,
                "height": height,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
    return None


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
    recovered = _existing_reference(source_sku)
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
    sku_results = []
    for index, sku in enumerate(source.get("skus") or [], start=1):
        original_path = str(sku.get("variant_local_image_path") or sku.get("local_image_path") or "unknown")
        original_file = ROOT / original_path if original_path != "unknown" else Path("/missing")
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
        if min(preferred["width"], preferred["height"]) < MIN_USABLE_REFERENCE_SIDE and override:
            recovered_override = recover_confirmed_reference_override(
                product_dir, source, sku, override, allow_download,
            )
            if recovered_override and min(recovered_override["width"], recovered_override["height"]) > min(preferred["width"], preferred["height"]):
                preferred = recovered_override
        preferred_short_side = min(preferred["width"], preferred["height"])
        ready = preferred_short_side >= MIN_REFERENCE_SIDE
        usable_with_warning = MIN_USABLE_REFERENCE_SIDE <= preferred_short_side < MIN_REFERENCE_SIDE
        reference_status = "ready" if ready else "ready_with_warning" if usable_with_warning else "blocked"
        reference_override = preferred.get("reference_override")
        if ready and reference_override:
            reason = (
                f"已按人工确认使用同外观SKU {reference_override['source_sku_id']} 的真实图片；"
                "仅共用参考图，目标SKU规格、重量、价格和文案保持不变。"
            )
        elif ready:
            reason = "原始商品图清晰度可用于生图参考。"
        elif usable_with_warning:
            reason = (
                f"SKU参考图短边为{preferred_short_side}px，低于推荐{MIN_REFERENCE_SIDE}px但高于可用下限"
                f"{MIN_USABLE_REFERENCE_SIDE}px；仅用于参考编辑，不放大、不抠图。"
            )
        else:
            reason = f"SKU参考图短边小于{MIN_USABLE_REFERENCE_SIDE}px，禁止放大抠图或继续生成。"
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
            "status": "ready" if min(width, height) >= MIN_REFERENCE_SIDE else "low_resolution",
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
