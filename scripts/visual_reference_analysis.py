#!/usr/bin/env python3
"""Create optional real-photo visual guidance from Ozon reference images.

This helper talks only to a local Florence2-compatible service such as
spgoodman/florence2-visionapi.  It does not call Ozon, inventory, upload, or
third-party hosted AI APIs.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import tempfile
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ENDPOINT = "http://127.0.0.1:54880/process_image"


def load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json_atomic(path: Path, value: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=str(path.parent), delete=False) as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temp_name = handle.name
    Path(temp_name).replace(path)


def _relative_or_absolute(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path.resolve())


def caption_image(endpoint: str, image_path: Path, prompt: str, timeout: int) -> str:
    payload = json.dumps({
        "image": base64.b64encode(image_path.read_bytes()).decode("ascii"),
        "prompt": prompt,
    }).encode("utf-8")
    request = urllib.request.Request(
        endpoint,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError) as exc:
        raise RuntimeError(f"local Florence2 visual service unavailable: {exc}") from exc
    result = str(data.get("result") or data.get("caption") or "").strip()
    if not result:
        raise RuntimeError(f"local Florence2 visual service returned no caption for {image_path}")
    return result


def build_visual_reference_analysis(
    product_id: str,
    image_paths: List[Path],
    captions: List[str],
    endpoint: str,
) -> Dict[str, Any]:
    combined = " ".join(captions).casefold()
    close_hint = "macro close-up / near handheld detail shot" if any(token in combined for token in ("close", "macro", "detail", "desk", "table")) else "near handheld product photo"
    background = "real seller environment, desk/table/shelf/room background adapted from reference captions without copying logos or text"
    if any(token in combined for token in ("monitor", "screen", "computer")):
        background = "real desk scene with a softly blurred monitor/screen background when product context supports it"
    elif any(token in combined for token in ("kitchen", "bathroom", "room", "shelf")):
        background = "real home/store room context with believable surfaces and background depth"

    return {
        "schema_version": "1.0.0",
        "product_id": product_id,
        "source_kind": "ozon_reference_images",
        "provider": {
            "name": "spgoodman/florence2-visionapi",
            "mode": "local_florence2_visionapi",
            "endpoint": endpoint,
        },
        "reference_images": [
            {
                "path": _relative_or_absolute(path),
                "caption": caption,
                "role": "ozon_competitor_reference",
            }
            for path, caption in zip(image_paths, captions)
        ],
        "real_photo_style": {
            "camera_feel": "real phone/camera seller photo, not a polished AI poster or studio-perfect render",
            "lighting": "natural indoor light, screen light or soft room light with believable shadows",
            "background": background,
            "depth_of_field": "shallow or moderate depth of field with slightly soft real background",
            "texture": "visible real material texture, lens softness, reflections and imperfect surface detail",
            "imperfections": "minor handheld framing, mild noise or softness, no over-smoothed AI plastic look",
        },
        "shot_recipes": [
            {
                "shot_type": close_hint,
                "composition": "product close to camera with real surface/background depth; keep current product identity from our source images",
                "purpose": "make the image feel like a real marketplace seller photo while proving material and scale",
                "avoid": ["watermark", "store name", "platform logo", "copied competitor text", "copied competitor brand"],
            },
            {
                "shot_type": "3/4 product view with real environment",
                "composition": "main product dominant, background visible but secondary, Russian text kept as restrained overlay",
                "purpose": "combine Ozon usability with real-photo trust",
                "avoid": ["AI poster layout", "giant title block", "fake props", "wrong accessory count"],
            },
            {
                "shot_type": "macro proof detail when the product has visible detail",
                "composition": "crop into verified material, edge, connector, surface or decorative detail from our product evidence",
                "purpose": "show quality proof without changing product structure",
                "avoid": ["invented materials", "extra functions", "unverified certification"],
            },
        ],
        "negative_style": [
            "watermark",
            "store name",
            "Ozon logo",
            "competitor brand",
            "copied model number",
            "AI-polished poster look",
            "perfect synthetic studio render",
            "large ad headline blocks",
            "copied product facts not present in our source",
        ],
        "fact_policy": {
            "reference_is_not_product_fact": True,
            "forbidden_fact_sources": [
                "competitor brand",
                "competitor store name",
                "competitor watermark",
                "competitor model number",
                "competitor packaging",
                "competitor accessories",
                "competitor certifications",
                "competitor exact text",
            ],
            "allowed_usage": [
                "camera feel",
                "lens distance",
                "lighting style",
                "background realism",
                "composition rhythm",
                "real seller-photo imperfections",
            ],
        },
        "processing": {
            "step": "visual_reference_analysis",
            "status": "completed",
            "generated_at": datetime.now().astimezone().replace(microsecond=0).isoformat(),
            "error": None,
        },
    }


def validate_analysis(value: Dict[str, Any]) -> None:
    schema = load_json(ROOT / "templates" / "visual-reference-analysis.schema.json")
    errors = list(Draft202012Validator(schema).iter_errors(value))
    if errors:
        raise ValueError("; ".join(error.message for error in errors))


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate visual-reference-analysis.json from Ozon reference images")
    parser.add_argument("product_dir", type=Path)
    parser.add_argument("--image", action="append", default=[], help="Reference image path; repeat for multiple images")
    parser.add_argument("--endpoint", default=os.environ.get("JLC_FLORENCE2_ENDPOINT", DEFAULT_ENDPOINT))
    parser.add_argument("--prompt", default="<MIXED_CAPTION_PLUS>")
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()

    product_dir = args.product_dir.resolve()
    product_id = product_dir.name
    image_paths = [Path(value).expanduser().resolve() for value in args.image]
    if not image_paths:
        raise SystemExit("至少需要传入一个 --image Ozon参考图路径")
    for image_path in image_paths:
        if not image_path.is_file():
            raise SystemExit(f"参考图不存在: {image_path}")

    captions = [caption_image(args.endpoint, image_path, args.prompt, args.timeout) for image_path in image_paths]
    analysis = build_visual_reference_analysis(product_id, image_paths, captions, args.endpoint)
    validate_analysis(analysis)
    if args.write:
        write_json_atomic(product_dir / "output" / "visual-reference-analysis.json", analysis)
    else:
        print(json.dumps(analysis, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
