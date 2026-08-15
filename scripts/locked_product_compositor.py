#!/usr/bin/env python3
"""Composite an unchanged real product layer over an AI-generated background."""

from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Tuple

try:
    import numpy as np
    from PIL import Image
except ImportError as exc:  # pragma: no cover - environment diagnostic
    raise SystemExit(
        "locked_product_compositor requires Pillow and numpy; use the bundled Codex Python runtime"
    ) from exc


ROOT = Path(__file__).resolve().parents[1]
CANVAS = (1080, 1440)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json_atomic(path: Path, value: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temporary = Path(handle.name)
    temporary.replace(path)


def border_background_mask(rgb: np.ndarray) -> Tuple[np.ndarray, float]:
    height, width, _ = rgb.shape
    border = np.concatenate((rgb[0], rgb[-1], rgb[:, 0], rgb[:, -1]), axis=0).astype(np.float32)
    background = np.median(border, axis=0)
    distance = np.linalg.norm(rgb.astype(np.float32) - background, axis=2)
    threshold = max(18.0, float(np.percentile(np.linalg.norm(border - background, axis=1), 90)) + 12.0)
    candidate = distance <= threshold
    connected = np.zeros((height, width), dtype=bool)
    queue: deque[Tuple[int, int]] = deque()
    for x in range(width):
        if candidate[0, x]: queue.append((0, x))
        if candidate[height - 1, x]: queue.append((height - 1, x))
    for y in range(height):
        if candidate[y, 0]: queue.append((y, 0))
        if candidate[y, width - 1]: queue.append((y, width - 1))
    while queue:
        y, x = queue.popleft()
        if connected[y, x] or not candidate[y, x]:
            continue
        connected[y, x] = True
        if y: queue.append((y - 1, x))
        if y + 1 < height: queue.append((y + 1, x))
        if x: queue.append((y, x - 1))
        if x + 1 < width: queue.append((y, x + 1))
    border_uniformity = float(np.mean(np.linalg.norm(border - background, axis=1) <= threshold))
    removed_ratio = float(connected.mean())
    confidence = min(border_uniformity, min(1.0, removed_ratio / 0.20))
    return connected, round(confidence, 4)


def build_locked_layer(reference: Path) -> Tuple[Image.Image, str, float]:
    source = Image.open(reference).convert("RGBA")
    rgb = np.asarray(source.convert("RGB"))
    background_mask, confidence = border_background_mask(rgb)
    if confidence < 0.65:
        return source, "full_reference_rectangle", confidence
    rgba = np.asarray(source).copy()
    rgba[background_mask, 3] = 0
    return Image.fromarray(rgba, "RGBA"), "border_connected_cutout", confidence


def fit_without_upscale(layer: Image.Image, canvas: Tuple[int, int]) -> Image.Image:
    max_width = int(canvas[0] * 0.78)
    max_height = int(canvas[1] * 0.68)
    scale = min(1.0, max_width / layer.width, max_height / layer.height)
    if scale == 1.0:
        return layer
    return layer.resize((max(1, round(layer.width * scale)), max(1, round(layer.height * scale))), Image.Resampling.LANCZOS)


def compose(reference: Path, background: Path, output: Path, slot: str, manifest: Path) -> Dict[str, Any]:
    background_image = Image.open(background).convert("RGB").resize(CANVAS, Image.Resampling.LANCZOS)
    layer, extraction_mode, confidence = build_locked_layer(reference)
    layer = fit_without_upscale(layer, CANVAS)
    x = (CANVAS[0] - layer.width) // 2
    y = (CANVAS[1] - layer.height) // 2
    final = background_image.convert("RGBA")
    final.alpha_composite(layer, (x, y))
    output.parent.mkdir(parents=True, exist_ok=True)
    final.convert("RGB").save(output, "PNG")

    # Recreate the expected composite independently and compare exact output pixels.
    expected = background_image.convert("RGBA")
    expected.alpha_composite(layer, (x, y))
    actual = Image.open(output).convert("RGBA")
    exact = np.array_equal(np.asarray(expected), np.asarray(actual))
    opaque = np.asarray(layer)[:, :, 3] == 255
    locked_pixels = int(opaque.sum())
    value = {
        "schema_version": "1.0.0",
        "slot": slot,
        "mode": "locked_product_composite",
        "reference_path": str(reference.relative_to(ROOT)),
        "reference_sha256": sha256(reference),
        "background_path": str(background.relative_to(ROOT)),
        "background_sha256": sha256(background),
        "output_path": str(output.relative_to(ROOT)),
        "output_sha256": sha256(output),
        "extraction_mode": extraction_mode,
        "extraction_confidence": confidence,
        "placement": {"x": x, "y": y, "width": layer.width, "height": layer.height},
        "audit": {
            "status": "pass" if exact and locked_pixels > 0 else "fail",
            "exact_composite_match": exact,
            "locked_opaque_pixel_count": locked_pixels,
            "product_redrawn": False,
        },
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    write_json_atomic(manifest, value)
    if value["audit"]["status"] != "pass":
        raise RuntimeError("locked product pixel audit failed")
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reference", required=True)
    parser.add_argument("--background", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--slot", required=True)
    parser.add_argument("--manifest", required=True)
    args = parser.parse_args()
    value = compose(
        (ROOT / args.reference).resolve(), (ROOT / args.background).resolve(),
        (ROOT / args.output).resolve(), args.slot, (ROOT / args.manifest).resolve(),
    )
    print(json.dumps(value, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
