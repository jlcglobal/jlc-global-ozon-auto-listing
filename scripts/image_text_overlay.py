#!/usr/bin/env python3
"""Render exact Cyrillic copy over a finished marketplace image."""

from __future__ import annotations

import argparse
import tempfile
from pathlib import Path
from typing import List

from PIL import Image, ImageDraw, ImageFont, ImageStat


FONT_REGULAR = Path("/System/Library/Fonts/Supplemental/Arial.ttf")
FONT_BOLD = Path("/System/Library/Fonts/Supplemental/Arial Bold.ttf")


def wrap_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_width: int) -> List[str]:
    words = text.split()
    lines: List[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if draw.textbbox((0, 0), candidate, font=font)[2] <= max_width or not current:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def _region_score(image: Image.Image, box: tuple[int, int, int, int]) -> tuple[float, float]:
    gray = image.convert("L").crop(box)
    stat = ImageStat.Stat(gray)
    return float(stat.var[0]), float(stat.mean[0])


def overlay(
    input_path: Path,
    output_path: Path,
    text: str,
    kind: str = "detail",
    placement: str = "auto",
) -> None:
    image = Image.open(input_path).convert("RGBA")
    width, height = image.size
    font_path = FONT_BOLD if FONT_BOLD.is_file() else FONT_REGULAR
    is_main = kind == "main"
    layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    margin = round(width * 0.065)
    parts = [part.strip() for part in text.split("||") if part.strip()]
    if not parts:
        raise ValueError("overlay text must not be empty")

    headline_size = max(44 if is_main else 38, round(width * (0.072 if is_main else 0.058)))
    minimum_headline_size = max(32, round(width * 0.038))
    while True:
        headline_font = ImageFont.truetype(str(font_path), headline_size)
        headline_lines = wrap_text(draw, parts[0], headline_font, width - margin * 2)
        if len(headline_lines) <= (2 if is_main else 3) or headline_size <= minimum_headline_size:
            break
        headline_size -= 2

    subline_size = max(28, round(headline_size * 0.55))
    subline_font = ImageFont.truetype(str(font_path), subline_size)
    subline_lines: List[str] = []
    for part in parts[1:]:
        subline_lines.extend(wrap_text(draw, part, subline_font, width - margin * 2))

    headline_spacing = round(headline_size * 0.18)
    subline_spacing = round(subline_size * 0.28)
    headline_height = (headline_size + headline_spacing) * len(headline_lines)
    subline_height = (subline_size + subline_spacing) * len(subline_lines)
    group_gap = round(headline_size * 0.30) if subline_lines else 0
    text_height = headline_height + group_gap + subline_height
    region_height = min(round(height * (0.45 if not is_main else 0.34)), text_height + margin * 2)
    top_score = _region_score(image, (0, 0, width, region_height))
    bottom_score = _region_score(image, (0, height - region_height, width, height))
    use_top = placement == "top" or (placement == "auto" and top_score[0] <= bottom_score[0])
    mean = top_score[1] if use_top else bottom_score[1]
    light_text = mean < 155
    text_fill = (255, 255, 255, 255) if light_text else (26, 29, 34, 255)
    shadow_fill = (0, 0, 0, 150) if light_text else (255, 255, 255, 180)
    gradient_rgb = (0, 0, 0) if light_text else (255, 255, 255)
    gradient = Image.new("RGBA", (width, region_height), (0, 0, 0, 0))
    gradient_draw = ImageDraw.Draw(gradient)
    for row in range(region_height):
        distance = row / max(region_height - 1, 1)
        # Keep the caption integrated into natural negative space.  A subtle
        # edge fade improves contrast without turning the typography area into
        # a fixed black/white panel or an empty placeholder card.
        max_alpha = 150 if is_main else 135
        alpha = int(max_alpha * ((1 - distance) ** 1.35))
        gradient_draw.line((0, row, width, row), fill=(*gradient_rgb, alpha))
    if use_top:
        layer.alpha_composite(gradient, (0, 0))
        y = margin
    else:
        gradient = gradient.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
        layer.alpha_composite(gradient, (0, height - region_height))
        y = height - region_height + margin
    accent_y = y - max(8, round(headline_size * 0.18))
    draw.rounded_rectangle(
        (margin, accent_y, margin + round(width * 0.09), accent_y + max(4, round(width * 0.007))),
        radius=3,
        fill=(218, 170, 55, 235),
    )
    for line in headline_lines:
        draw.text(
            (margin, y), line, font=headline_font, fill=text_fill,
            stroke_width=max(1, round(width * 0.0015)), stroke_fill=shadow_fill,
        )
        y += headline_size + headline_spacing
    if subline_lines:
        y += group_gap
        for line in subline_lines:
            draw.text(
                (margin, y), line, font=subline_font,
                fill=(231, 186, 66, 255) if light_text else (128, 86, 0, 255),
                stroke_width=max(1, round(width * 0.001)), stroke_fill=shadow_fill,
            )
            y += subline_size + subline_spacing
    final = Image.alpha_composite(image, layer).convert("RGB")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(suffix=".png", dir=output_path.parent, delete=False) as handle:
        temporary = Path(handle.name)
    final.save(temporary, "PNG")
    temporary.replace(output_path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--text", required=True)
    parser.add_argument("--kind", choices=["main", "detail"], default="detail")
    parser.add_argument("--placement", choices=["auto", "top", "bottom"], default="auto")
    args = parser.parse_args()
    overlay(Path(args.input), Path(args.output), args.text, args.kind, args.placement)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
