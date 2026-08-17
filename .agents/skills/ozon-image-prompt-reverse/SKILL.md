---
name: ozon-image-prompt-reverse
description: Reverse Ozon competitor/reference product images into real-photo camera, lighting, composition and negative-style guidance for JLC GLOBAL image planning.
---

# Ozon Image Prompt Reverse

Input: one current product id plus one or more Ozon competitor/reference product
images already captured for analysis.

This Skill is an advisory visual-analysis layer. It extracts how a reference
image feels photographed; it must not become product facts, listing facts,
attribute facts, SKU facts, upload data or inventory data.

## Allowed source

- Ozon public product-card screenshots or downloaded product-card images used
  only as competitor/reference material.
- Current product evidence remains the formal source of truth for structure,
  color, SKU differences, accessories, quantities, dimensions, functions,
  certifications, packaging and claims.

## Recommended local tool

Use a local Florence2-compatible service, preferably:

- `spgoodman/florence2-visionapi`

The adapter in this project is:

- `scripts/visual_reference_analysis.py`

It calls only a local endpoint such as
`http://127.0.0.1:54880/process_image`. It must not call Ozon Seller API,
inventory, warehouse, activation, OpenAI API or any third-party hosted AI API.

Example:

```bash
python scripts/visual_reference_analysis.py products/P000001 \
  --image /path/to/ozon-reference-1.jpg \
  --image /path/to/ozon-reference-2.jpg \
  --write
```

## Output

Write:

- `products/<product_id>/output/visual-reference-analysis.json`

The file must validate against:

- `templates/visual-reference-analysis.schema.json`

The output describes:

- `real_photo_style.camera_feel`
- `real_photo_style.lighting`
- `real_photo_style.background`
- `real_photo_style.depth_of_field`
- `real_photo_style.texture`
- `real_photo_style.imperfections`
- `shot_recipes`
- `negative_style`
- `fact_policy`

## Hard boundary

Reference images may guide only:

- camera feel;
- lens distance;
- lighting style;
- background realism;
- composition rhythm;
- real seller-photo imperfections.

Reference images must never supply:

- competitor brand;
- competitor store name;
- watermark;
- model number;
- certification;
- packaging;
- included accessories;
- exact title/description text;
- product dimensions, weight, material or function not present in our product
  evidence.

## Downstream behavior

`scripts/image_planner.py` reads
`output/visual-reference-analysis.json` only when the file exists and belongs
to the same product. It adds the guidance to executable image prompts while
keeping the existing `N SKU main + 8 shared detail` image contract.

Absence of this file must not stop the normal production line.
