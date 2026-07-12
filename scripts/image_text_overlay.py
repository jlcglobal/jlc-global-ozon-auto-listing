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


def wrap_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_width: int, max_lines: int = 4) -> List[str]:
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
    return lines[:max_lines]


def _region_score(image: Image.Image, box: tuple[int, int, int, int]) -> tuple[float, float]:
    gray = image.convert("L").crop(box)
    stat = ImageStat.Stat(gray)
    return float(stat.var[0]), float(stat.mean[0])


def overlay(input_path: Path, output_path: Path, text: str, kind: str = "detail") -> None:
    image = Image.open(input_path).convert("RGBA")
    width, height = image.size
    font_path = FONT_BOLD if FONT_BOLD.is_file() else FONT_REGULAR
    is_main = kind == "main"
    font_size = max(42 if is_main else 34, round(width * (0.075 if is_main else 0.052)))
    font = ImageFont.truetype(str(font_path), font_size)
    layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    margin = round(width * 0.065)
    lines = wrap_text(draw, text.strip(), font, width - margin * 2, 2 if is_main else 4)
    spacing = round(font_size * 0.20)
    line_height = font_size + spacing
    text_height = line_height * len(lines)
    region_height = min(height // 3, text_height + margin * 2)
    top_score = _region_score(image, (0, 0, width, region_height))
    bottom_score = _region_score(image, (0, height - region_height, width, height))
    use_top = top_score[0] <= bottom_score[0]
    mean = top_score[1] if use_top else bottom_score[1]
    light_text = mean < 155
    text_fill = (255, 255, 255, 255) if light_text else (26, 29, 34, 255)
    shadow_fill = (0, 0, 0, 150) if light_text else (255, 255, 255, 180)
    gradient_rgb = (0, 0, 0) if light_text else (255, 255, 255)
    gradient = Image.new("RGBA", (width, region_height), (0, 0, 0, 0))
    gradient_draw = ImageDraw.Draw(gradient)
    for row in range(region_height):
        distance = row / max(region_height - 1, 1)
        alpha = int((145 if is_main else 115) * (1 - distance))
        gradient_draw.line((0, row, width, row), fill=(*gradient_rgb, alpha))
    if use_top:
        layer.alpha_composite(gradient, (0, 0))
        y = margin
    else:
        gradient = gradient.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
        layer.alpha_composite(gradient, (0, height - region_height))
        y = height - region_height + margin
    accent_y = y - max(8, round(font_size * 0.18))
    draw.rounded_rectangle(
        (margin, accent_y, margin + round(width * 0.09), accent_y + max(4, round(width * 0.007))),
        radius=3,
        fill=(218, 170, 55, 235),
    )
    for line in lines:
        draw.text(
            (margin, y), line, font=font, fill=text_fill,
            stroke_width=max(1, round(width * 0.0015)), stroke_fill=shadow_fill,
        )
        y += line_height
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
    args = parser.parse_args()
    overlay(Path(args.input), Path(args.output), args.text, args.kind)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
