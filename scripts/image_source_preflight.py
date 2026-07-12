#!/usr/bin/env python3
"""Check image references once and recover original 1688 SKU images safely."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import tempfile
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
MIN_REFERENCE_SIDE = 600


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
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read(), str(response.headers.get("Content-Type") or "")


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


def build_preflight(product_dir: Path, allow_download: bool = True) -> Dict[str, Any]:
    product_dir = product_dir.resolve()
    source = load_json(product_dir / "input/source.json")
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
        ready = min(preferred["width"], preferred["height"]) >= MIN_REFERENCE_SIDE
        sku_results.append({
            "source_sku_id": str(sku.get("sku_id") or "unknown"),
            "sku_name": str(sku.get("sku_name") or "unknown"),
            "original_reference_path": original_path,
            "original_dimensions": {"width": original_width, "height": original_height},
            "preferred_reference_path": preferred["path"],
            "preferred_dimensions": {"width": preferred["width"], "height": preferred["height"]},
            "preferred_sha256": preferred["sha256"],
            "status": "ready" if ready else "blocked",
            "reason": "原始商品图清晰度可用于生图参考。" if ready else f"SKU参考图短边小于{MIN_REFERENCE_SIDE}px，禁止放大抠图或继续生成。",
        })
    main_results = []
    for item in source.get("main_images") or []:
        path = str(item.get("local_path") or "unknown")
        width, height = image_dimensions(ROOT / path) if path != "unknown" else (0, 0)
        main_results.append({
            "id": str(item.get("id") or "unknown"), "path": path,
            "width": width, "height": height,
            "status": "ready" if min(width, height) >= MIN_REFERENCE_SIDE else "low_resolution",
        })
    blocked = [item["source_sku_id"] for item in sku_results if item["status"] != "ready"]
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
            "comparison_and_measurement_images_require_deterministic_real_image_composition": True,
            "lifestyle_images_allow_ai_reference_edit": True,
        },
    }
    write_json_atomic(product_dir / "output/image-source-preflight.json", value)
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("product_dir")
    parser.add_argument("--no-download", action="store_true")
    args = parser.parse_args()
    value = build_preflight(Path(args.product_dir), allow_download=not args.no_download)
    print(json.dumps(value, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
