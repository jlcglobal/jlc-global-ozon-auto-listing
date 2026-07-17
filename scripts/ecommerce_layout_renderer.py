#!/usr/bin/env python3
"""Render legacy deterministic layouts for isolated P9 regression fixtures.

This fixed renderer is intentionally disabled for formal production. Current
products use one built-in image-model call for the faithful scene, product and
exact Russian typography; no reusable template or post-generation overlay may
own the layout.
"""
from __future__ import annotations

import argparse
import json
import math
import tempfile
from pathlib import Path
from typing import Any, Iterable, List, Sequence

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

try:
    from scripts.image_asset_boundaries import validate_generated_output, validate_product_reference
    from scripts.production_input_guard import validate_formal_product_input
except ModuleNotFoundError:
    from image_asset_boundaries import validate_generated_output, validate_product_reference
    from production_input_guard import validate_formal_product_input


ROOT = Path(__file__).resolve().parents[1]
CANVAS = (1080, 1440)
FONT_REGULAR = Path("/System/Library/Fonts/Supplemental/Arial.ttf")
FONT_BOLD = Path("/System/Library/Fonts/Supplemental/Arial Bold.ttf")
NAVY = (8, 31, 58, 255)
BLUE = (0, 119, 255, 255)
ICE = (229, 244, 252, 238)
WHITE = (255, 255, 255, 245)
GREEN = (51, 150, 115, 255)
INK = (18, 34, 50, 255)
AMBER = (255, 176, 0, 255)
WARM_WHITE = (255, 250, 242, 238)
CHARCOAL = (24, 30, 35, 255)
COPPER = (184, 105, 43, 255)


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    candidate = FONT_BOLD if bold and FONT_BOLD.is_file() else FONT_REGULAR
    return ImageFont.truetype(str(candidate), size)


def wrap(draw: ImageDraw.ImageDraw, text: str, face: ImageFont.FreeTypeFont, width: int) -> List[str]:
    words = text.split()
    lines: List[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if not current or draw.textbbox((0, 0), candidate, font=face)[2] <= width:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def fit_cover(image: Image.Image, size: tuple[int, int] = CANVAS) -> Image.Image:
    return ImageOps.fit(image.convert("RGB"), size, Image.Resampling.LANCZOS, centering=(0.5, 0.5))


def soft_base(image: Image.Image) -> Image.Image:
    base = fit_cover(image)
    base = ImageEnhance.Contrast(base).enhance(1.08)
    base = ImageEnhance.Color(base).enhance(1.03)
    return base.convert("RGBA")


def rounded(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], fill: tuple[int, int, int, int], radius: int = 24, outline=None, width: int = 1) -> None:
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


def draw_lines(draw: ImageDraw.ImageDraw, lines: Sequence[str], xy: tuple[int, int], face: ImageFont.FreeTypeFont, fill, spacing: int = 8) -> int:
    x, y = xy
    for line in lines:
        draw.text((x, y), line, font=face, fill=fill)
        y += face.size + spacing
    return y


def header(draw: ImageDraw.ImageDraw, text: str, eyebrow: str = "ОРГАНИЗАЦИЯ ХРАНЕНИЯ") -> None:
    # A quiet editorial masthead keeps the product looking premium.  Contrast
    # comes from typography and whitespace instead of dashboard-like cards.
    draw.rectangle((0, 0, 1080, 304), fill=WARM_WHITE)
    draw.rounded_rectangle((58, 54, 154, 61), radius=3, fill=COPPER)
    draw.text((58, 78), eyebrow, font=font(22, True), fill=COPPER)
    face = font(49, True)
    lines = wrap(draw, text, face, 680)[:2]
    draw_lines(draw, lines, (56, 119), face, CHARCOAL, 2)


def capacity_badge(draw: ImageDraw.ImageDraw, text: str, xy: tuple[int, int] = (812, 92)) -> None:
    rounded(draw, (xy[0], xy[1], xy[0] + 208, xy[1] + 104), AMBER, 22)
    face = font(51, True)
    bbox = draw.textbbox((0, 0), text, font=face)
    text_width = bbox[2] - bbox[0]
    draw.text((xy[0] + (208 - text_width) // 2, xy[1] + 23), text, font=face, fill=CHARCOAL)


def specification_badge(draw: ImageDraw.ImageDraw, text: str, xy: tuple[int, int] = (746, 216)) -> None:
    """Place a verified SKU specification below the capacity badge."""
    rounded(draw, (xy[0], xy[1], 1020, xy[1] + 60), (255, 255, 255, 205), 16, outline=COPPER, width=2)
    face = font(24, True)
    lines = wrap(draw, text, face, 238)[:1]
    draw_lines(draw, lines, (xy[0] + 19, xy[1] + 17), face, CHARCOAL, 0)


def benefit_chips(draw: ImageDraw.ImageDraw, values: Sequence[str]) -> None:
    values = [value for value in values if value][:3]
    if not values:
        return
    draw.rectangle((0, 1236, 1080, 1440), fill=WARM_WHITE)
    draw.rectangle((0, 1236, 1080, 1244), fill=AMBER)
    total_width = 984
    chip_width = total_width // len(values)
    x = 48
    y = 1244
    for index, value in enumerate(values):
        if index:
            draw.line((x, y + 26, x, 1410), fill=(184, 105, 43, 80), width=2)
        rounded(draw, (x + 18, y + 34, x + 64, y + 80), CHARCOAL, 23)
        # Draw the tick as geometry instead of a font glyph so Cyrillic-only
        # system fonts can never render it as a missing-character square.
        draw.line((x + 29, y + 57, x + 38, y + 67), fill=AMBER, width=4)
        draw.line((x + 38, y + 67, x + 54, y + 46), fill=AMBER, width=4)
        face_size = 25
        while True:
            face = font(face_size, True)
            lines = wrap(draw, value, face, chip_width - 94)
            if len(lines) <= 3 or face_size <= 20:
                break
            face_size -= 1
        lines = lines[:3]
        draw_lines(draw, lines, (x + 80, y + 35), face, CHARCOAL, 6)
        x += chip_width


def callout(draw: ImageDraw.ImageDraw, anchor: tuple[int, int], box: tuple[int, int, int, int], title: str, value: str = "") -> None:
    x1, y1, x2, y2 = box
    center = (x1 + x2) // 2, (y1 + y2) // 2
    draw.line((anchor[0], anchor[1], center[0], center[1]), fill=BLUE, width=6)
    draw.ellipse((anchor[0] - 10, anchor[1] - 10, anchor[0] + 10, anchor[1] + 10), fill=BLUE)
    rounded(draw, box, WHITE, 22, outline=(42, 126, 201, 160), width=3)
    draw.text((x1 + 22, y1 + 16), title, font=font(28, True), fill=NAVY)
    if value:
        lines = wrap(draw, value, font(24), x2 - x1 - 44)[:2]
        draw_lines(draw, lines, (x1 + 22, y1 + 54), font(24), INK, 3)


def dimension_line(draw: ImageDraw.ImageDraw, start: tuple[int, int], end: tuple[int, int], label: str) -> None:
    draw.line((*start, *end), fill=NAVY, width=5)
    angle = math.atan2(end[1] - start[1], end[0] - start[0])
    for point, direction in ((start, angle), (end, angle + math.pi)):
        wing = 18
        draw.line((point[0], point[1], point[0] + wing * math.cos(direction + .55), point[1] + wing * math.sin(direction + .55)), fill=NAVY, width=5)
        draw.line((point[0], point[1], point[0] + wing * math.cos(direction - .55), point[1] + wing * math.sin(direction - .55)), fill=NAVY, width=5)
    midpoint = ((start[0] + end[0]) // 2, (start[1] + end[1]) // 2)
    bbox = draw.textbbox((0, 0), label, font=font(27, True))
    rounded(draw, (midpoint[0] - (bbox[2] // 2) - 12, midpoint[1] - 42, midpoint[0] + (bbox[2] // 2) + 12, midpoint[1] + 2), WHITE, 12)
    draw.text((midpoint[0] - bbox[2] // 2, midpoint[1] - 37), label, font=font(27, True), fill=NAVY)


def product_card(reference: Image.Image, label: str, size_text: str, box: tuple[int, int, int, int]) -> Image.Image:
    x1, y1, x2, y2 = box
    card = Image.new("RGBA", (x2 - x1, y2 - y1), WHITE)
    # Source images may include a supplier footer.  The deterministic comparison
    # uses only the upper evidence region; no source text is copied into captions.
    crop = reference.convert("RGB").crop((0, 0, reference.width, int(reference.height * .76)))
    crop = ImageOps.contain(crop, (card.width - 28, card.height - 148), Image.Resampling.LANCZOS)
    card.alpha_composite(crop.convert("RGBA"), ((card.width - crop.width) // 2, 14))
    d = ImageDraw.Draw(card)
    d.text((20, card.height - 118), label, font=font(34, True), fill=NAVY)
    d.text((20, card.height - 68), size_text, font=font(22), fill=INK)
    return card


def render_comparison(references: Sequence[Image.Image], labels: Sequence[str], specs: Sequence[str]) -> Image.Image:
    canvas = Image.new("RGBA", CANVAS, (232, 245, 252, 255))
    draw = ImageDraw.Draw(canvas)
    header(draw, "ВЫБЕРИТЕ ПОДХОДЯЩИЙ ОБЪЁМ", "СРАВНЕНИЕ SKU")
    count = len(references)
    columns = min(4, count)
    rows = math.ceil(count / columns)
    gap = 18
    card_width = (984 - gap * (columns - 1)) // columns
    card_height = 790 // rows
    for index, reference in enumerate(references):
        column = index % columns
        row = index // columns
        x = 48 + column * (card_width + gap)
        y = 320 + row * (card_height + gap)
        card = product_card(reference, labels[index], specs[index], (x, y, x + card_width, y + card_height))
        canvas.alpha_composite(card, (x, y))
    rounded(draw, (48, 1160, 1032, 1388), NAVY, 30)
    draw.text((78, 1192), "ОДИН ОРГАНАЙЗЕР ВЫБРАННОГО РАЗМЕРА", font=font(37, True), fill=(255, 255, 255, 255))
    draw.text((78, 1258), "Перед покупкой измерьте свободное место на полке", font=font(28), fill=(220, 239, 252, 255))
    return canvas


def render_layout(
    role: dict[str, Any], base: Image.Image, references: Sequence[Image.Image] = (),
    labels: Sequence[str] = (), specs: Sequence[str] = (),
) -> Image.Image:
    layout = str(role.get("layout_type") or "")
    text = [str(value) for value in role.get("russian_text") or []]
    if layout == "sku_comparison":
        return render_comparison(references or [base], labels or ["SKU"], specs or [""])
    canvas = soft_base(base)
    overlay_layer = Image.new("RGBA", CANVAS, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay_layer)
    title = text[0] if text else "ТОВАР ДЛЯ ХРАНЕНИЯ"
    eyebrow = {
        "sku_main": "ОРГАНИЗАЦИЯ ХРАНЕНИЯ",
        "core_benefit": "ГЛАВНОЕ ПРЕИМУЩЕСТВО",
        "structure_callout": "ДЕТАЛИ КОНСТРУКЦИИ",
        "usage_scene": "ИДЕЯ ДЛЯ ИСПОЛЬЗОВАНИЯ",
        "purchase_notice": "ПЕРЕД ПОКУПКОЙ",
    }.get(layout, "ПОЛЕЗНАЯ ИНФОРМАЦИЯ")
    header(draw, title, eyebrow)
    if layout == "sku_main":
        capacity_badge(draw, text[1] if len(text) > 1 else "ВЫБЕРИТЕ ОБЪЁМ")
        if len(text) > 2:
            specification_badge(draw, text[2])
        benefit_chips(draw, text[3:] or ["ПРОЗРАЧНЫЙ КОРПУС", "КРЫШКА И РУЧКА", "1 ШТ."])
    elif layout == "structure_callout":
        callout(draw, (390, 600), (48, 925, 470, 1065), text[1] if len(text) > 1 else "ПРОЗРАЧНЫЙ КОРПУС", text[2] if len(text) > 2 else "Содержимое видно сразу")
        callout(draw, (705, 505), (610, 1010, 1032, 1150), text[3] if len(text) > 3 else "КРЫШКА", text[4] if len(text) > 4 else "Для аккуратного хранения")
        benefit_chips(draw, ["КРЫШКА", "ПЕРЕДНЯЯ РУЧКА", "ПРОЗРАЧНЫЕ СТЕНКИ"])
    elif layout == "purchase_notice":
        rounded(draw, (48, 940, 1032, 1388), NAVY, 32)
        y = 988
        for index, value in enumerate(text[1:5] or ["1 ОРГАНАЙЗЕР В КОМПЛЕКТЕ", "ПРОВЕРЬТЕ РАЗМЕР ПЕРЕД ПОКУПКОЙ"]):
            draw.ellipse((78, y + 4, 116, y + 42), fill=BLUE)
            draw.text((89, y + 4), "✓", font=font(25, True), fill=(255, 255, 255, 255))
            lines = wrap(draw, value, font(31, True), 820)[:2]
            y = draw_lines(draw, lines, (138, y), font(31, True), (255, 255, 255, 255), 5) + 28
    else:
        benefit_chips(draw, text[1:] or ["ПОРЯДОК", "УДОБНО ВИДЕТЬ", "ЛЕГКО ДОСТАТЬ"])
    return Image.alpha_composite(canvas, overlay_layer)


def save_atomic(image: Image.Image, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(suffix=".png", dir=output.parent, delete=False) as handle:
        temporary = Path(handle.name)
    image.convert("RGB").save(temporary, "PNG")
    temporary.replace(output)


def find_role(design: dict[str, Any], slot: str) -> dict[str, Any]:
    for role in [*(design.get("main_images") or []), *(design.get("detail_images") or [])]:
        if str(role.get("slot") or "") == slot:
            return role
    raise ValueError(f"unknown ecommerce design slot: {slot}")


def project_root_for(product_dir: Path) -> Path:
    resolved = product_dir.resolve()
    if resolved.parent.name == "products":
        return resolved.parent.parent
    return ROOT


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("product_dir")
    parser.add_argument("--slot", required=True)
    parser.add_argument("--base", required=True, help="Faithful AI scene/base or real-image composition")
    parser.add_argument("--output", required=True)
    parser.add_argument("--manual-test", action="store_true", help="Render an isolated P9 manual-test fixture")
    args = parser.parse_args()
    product_dir = Path(args.product_dir).resolve()
    if args.manual_test:
        if product_dir.parent != (ROOT / "test-data/manual-output").resolve():
            raise ValueError("manual-test renderer requires test-data/manual-output/P9xxxxx")
        if not product_dir.name.startswith("P9"):
            raise ValueError("manual-test renderer requires a reserved P9xxxxx identity")
        manual_input = ROOT / "test-data/manual-input" / product_dir.name
        design = load_json(product_dir / "ozon-ecommerce-design.json")
        source = load_json(manual_input / "source.json")
        if design.get("source_kind") != "manual_test" or source.get("source_kind") != "manual_test":
            raise ValueError("manual-test design and source must both use source_kind=manual_test")
        if design.get("collection_id") != source.get("collection_id"):
            raise ValueError("manual-test design collection does not match source")
        reference_root = manual_input.resolve()
    else:
        raise ValueError(
            "fixed ecommerce layout renderer is disabled for formal production; "
            "use single-pass built-in generation with the validated overlay_plan"
        )
    role = find_role(design, args.slot)
    refs = []
    for value in role.get("source_references") or []:
        if args.manual_test:
            candidate = (ROOT / value).resolve()
            if reference_root not in candidate.parents or candidate.parent.name != "sku-images":
                raise ValueError(f"manual-test product reference escapes its SKU input: {value}")
            if not candidate.is_file():
                raise ValueError(f"manual-test product reference is missing: {value}")
        else:
            candidate = validate_product_reference(product_dir, value)
        refs.append(Image.open(candidate).convert("RGB"))
    sku_by_id = {str(item.get("sku_id")): item for item in source.get("skus") or []}
    labels: List[str] = []
    specs: List[str] = []
    if role.get("layout_type") == "sku_comparison":
        for value in design.get("sku_plan") or []:
            sku = sku_by_id.get(str(value.get("sku_id"))) or {}
            labels.append(str(value.get("name_ru") or value.get("sku_id") or "SKU"))
            dimensions = ((sku.get("source_data") or {}).get("external_dimensions_cm") or {})
            specs.append(
                f"{value.get('difference_ru', '')} · "
                f"{dimensions.get('length', '?')} × {dimensions.get('width', '?')} × {dimensions.get('height', '?')} см"
            )
    base = Image.open(Path(args.base)).convert("RGB")
    result = render_layout(role, base, refs, labels, specs)
    if args.manual_test:
        output = Path(args.output).resolve()
        output_root = (product_dir / "generated-images").resolve()
        if output_root not in output.parents:
            raise ValueError("manual-test output must stay below its generated-images directory")
    else:
        output = validate_generated_output(product_dir, args.output)
    save_atomic(result, output)
    print(json.dumps({
        "slot": args.slot, "layout_type": role.get("layout_type"),
        "requested_operation": role.get("operation"),
        # The faithful base must already have been produced by the requested
        # operation.  This process adds only deterministic ecommerce modules.
        "executed_operation": role.get("operation"),
        "overlay_operation": "legacy_manual_test_layout_only",
        "output": str(output), "model_calls": 0, "ozon_calls": 0,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
