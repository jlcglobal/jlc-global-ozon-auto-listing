#!/usr/bin/env python3
"""Legacy overlay renderer retained only for regression tests and old audits.

Formal production uses one built-in image-model call for scene, product and
exact Russian typography.  The CLI below is deliberately disabled so a formal
task can never fall back to post-generation local lettering.
"""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path
from typing import Any, Iterable

from PIL import Image, ImageDraw, ImageFont

try:
    from scripts.image_asset_boundaries import validate_generated_output
    from scripts.production_input_guard import validate_formal_product_input
except ModuleNotFoundError:  # Allows direct execution as scripts/designer_directed_overlay.py.
    from image_asset_boundaries import validate_generated_output
    from production_input_guard import validate_formal_product_input


FONT_REGULAR = Path("/System/Library/Fonts/Supplemental/Arial.ttf")
FONT_BOLD = Path("/System/Library/Fonts/Supplemental/Arial Bold.ttf")


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def find_slot(plan: dict[str, Any], slot: str) -> dict[str, Any]:
    for key in ("main_images", "detail_images", "disclaimer_images"):
        for item in plan.get(key) or []:
            if str(item.get("slot") or "") == slot:
                return item
    raise ValueError(f"unknown image slot: {slot}")


def rgba(value: str, alpha: int = 255) -> tuple[int, int, int, int]:
    text = str(value).strip().lstrip("#")
    if len(text) != 6:
        raise ValueError(f"invalid RGB color: {value}")
    return int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16), alpha


def wrap(draw: ImageDraw.ImageDraw, text: str, face: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    lines: list[str] = []
    current = ""
    for word in text.split():
        candidate = f"{current} {word}".strip()
        if not current or draw.textbbox((0, 0), candidate, font=face)[2] <= max_width:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def fitted_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font_path: Path,
    requested_size: int,
    max_width: int,
    max_height: int,
) -> tuple[ImageFont.FreeTypeFont, list[str], int]:
    size = requested_size
    while size >= 16:
        face = ImageFont.truetype(str(font_path), size)
        lines = wrap(draw, text, face, max_width)
        spacing = max(3, round(size * 0.16))
        total_height = len(lines) * size + max(0, len(lines) - 1) * spacing
        if total_height <= max_height:
            return face, lines, spacing
        size -= 2
    raise ValueError(f"overlay text does not fit its designer box: {text}")


def draw_background(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    style: str,
    color: str,
) -> None:
    if style == "none":
        return
    alpha = 190 if style == "translucent" else 255
    fill = rgba(color, alpha)
    x1, y1, x2, y2 = box
    if style == "circle":
        draw.ellipse(box, fill=fill)
    elif style == "pill":
        draw.rounded_rectangle(box, radius=max(1, (y2 - y1) // 2), fill=fill)
    else:
        draw.rounded_rectangle(box, radius=max(8, round(min(x2 - x1, y2 - y1) * 0.08)), fill=fill)


def draw_accent(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    style: str,
    color: str,
) -> int:
    """Draw only the accent selected by the designer; return text left inset."""
    x1, y1, x2, y2 = box
    width, height = x2 - x1, y2 - y1
    fill = rgba(color)
    if style == "none":
        return 0
    if style == "top_line":
        draw.rounded_rectangle((x1, y1, x1 + round(width * 0.28), y1 + max(4, round(height * 0.035))), radius=3, fill=fill)
        return 0
    if style == "left_line":
        draw.rounded_rectangle((x1, y1, x1 + max(4, round(width * 0.018)), y2), radius=3, fill=fill)
        return max(12, round(width * 0.055))
    if style == "underline":
        draw.rounded_rectangle((x1, y2 - max(4, round(height * 0.035)), x1 + round(width * 0.62), y2), radius=3, fill=fill)
        return 0
    if style == "check_icon":
        diameter = max(26, min(round(height * 0.58), round(width * 0.18)))
        cy = y1 + height // 2
        icon_box = (x1, cy - diameter // 2, x1 + diameter, cy + diameter // 2)
        draw.ellipse(icon_box, fill=fill)
        draw.line((x1 + round(diameter * .25), cy, x1 + round(diameter * .43), cy + round(diameter * .18)), fill=(255, 255, 255, 255), width=max(2, diameter // 11))
        draw.line((x1 + round(diameter * .43), cy + round(diameter * .18), x1 + round(diameter * .76), cy - round(diameter * .22)), fill=(255, 255, 255, 255), width=max(2, diameter // 11))
        return diameter + max(10, round(width * 0.04))
    raise ValueError(f"unsupported designer accent style: {style}")


def render_overlay_plan(
    image: Image.Image,
    russian_text: Iterable[str],
    overlay_plan: list[dict[str, Any]],
) -> Image.Image:
    expected = [str(value).strip() for value in russian_text]
    actual = [str(value.get("text") or "").strip() for value in overlay_plan]
    if actual != expected:
        raise ValueError("overlay_plan must match every exact Russian text item in order")
    width, height = image.size
    if width * 4 != height * 3:
        raise ValueError("designer-directed overlay requires an exact 3:4 image")
    layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)

    for instruction in sorted(overlay_plan, key=lambda value: int(value["priority"])):
        x, y, box_width, box_height = [float(value) for value in instruction["box"]]
        box = (
            round(x * width), round(y * height),
            round((x + box_width) * width), round((y + box_height) * height),
        )
        if box[0] < 0 or box[1] < 0 or box[2] > width or box[3] > height or box[2] <= box[0] or box[3] <= box[1]:
            raise ValueError(f"overlay box is outside the canvas: {instruction['box']}")
        draw_background(draw, box, str(instruction["background_style"]), str(instruction["background_color"]))

        padding = max(6, round(min(box[2] - box[0], box[3] - box[1]) * 0.07))
        accent_inset = draw_accent(draw, box, str(instruction["accent_style"]), str(instruction["accent_color"]))
        text_box = (
            box[0] + padding + accent_inset,
            box[1] + padding,
            box[2] - padding,
            box[3] - padding,
        )
        font_path = FONT_BOLD if instruction["font_weight"] == "bold" and FONT_BOLD.is_file() else FONT_REGULAR
        requested_size = round(width * float(instruction["font_size_ratio"]))
        face, lines, spacing = fitted_text(
            draw, str(instruction["text"]), font_path, requested_size,
            text_box[2] - text_box[0], text_box[3] - text_box[1],
        )
        line_widths = [draw.textbbox((0, 0), line, font=face)[2] for line in lines]
        total_height = len(lines) * face.size + max(0, len(lines) - 1) * spacing
        vertical = str(instruction["vertical_align"])
        if vertical == "middle":
            text_y = text_box[1] + max(0, (text_box[3] - text_box[1] - total_height) // 2)
        elif vertical == "bottom":
            text_y = text_box[3] - total_height
        else:
            text_y = text_box[1]
        for line, line_width in zip(lines, line_widths):
            align = str(instruction["align"])
            if align == "center":
                text_x = text_box[0] + (text_box[2] - text_box[0] - line_width) // 2
            elif align == "right":
                text_x = text_box[2] - line_width
            else:
                text_x = text_box[0]
            draw.text((text_x, text_y), line, font=face, fill=rgba(str(instruction["text_color"])))
            text_y += face.size + spacing
    return Image.alpha_composite(image.convert("RGBA"), layer).convert("RGB")


def save_atomic(image: Image.Image, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(suffix=".png", dir=output.parent, delete=False) as handle:
        temporary = Path(handle.name)
    image.save(temporary, "PNG")
    temporary.replace(output)


def project_root_for(product_dir: Path) -> Path:
    resolved = product_dir.resolve()
    if resolved.parent.name == "products":
        return resolved.parent.parent
    raise ValueError("designer-directed overlay requires a formal products/<product_id> directory")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("product_dir")
    parser.add_argument("--slot", required=True)
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    raise ValueError(
        "post-generation overlay is disabled; use one built-in image-model call "
        "for the final scene, product and exact Russian typography"
    )


if __name__ == "__main__":
    raise SystemExit(main())
