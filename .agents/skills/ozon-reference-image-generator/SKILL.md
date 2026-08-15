---
name: ozon-reference-image-generator
description: Generate real-photo product images for Ozon public listing reference drafts without using the formal 1688 production gate.
---

# Ozon Reference Image Generator

Use only when the product has `input/source.json -> source_kind=ozon_reference_draft`.
This is not the formal 1688 production pipeline.

## Input

Read only the current product directory:

- `products/<product_id>/input/source.json`
- `products/<product_id>/input/main-images/*`
- `products/<product_id>/input/detail-images/*`
- `products/<product_id>/output/image-plan.json`
- `products/<product_id>/output/ozon-reference-listing-design-draft.json`
- `products/<product_id>/output/copy-ru.json`

Do not call Ozon Seller API, Ozon readback, inventory, warehouse, activation, OpenAI API, or third-party hosted APIs.

## Purpose

Generate new JLC-owned product images that feel like real phone/camera seller photos.
The captured Ozon public-card images are only visual references for:

- product subject identity;
- visible pose, angle, color and structure;
- casual seller-photo camera feel;
- lighting, desk/background realism and depth of field.

They are not facts for inventory, brand, certification, dimensions, included accessories, or upload fields.

## Image Rules

For every slot in `output/image-plan.json`, generate exactly one final PNG to the slot's `output_path`.

Requirements:

- final image must be 3:4 portrait, at least 900x1200;
- use the built-in image generation/editing tool only;
- use current product input images as reference images;
- preserve the same product type, visible body, pose, color, structure, accessory count and major details;
- remove competitor watermark, store name, Ozon/platform logo, and copied overlay text;
- do not copy the original reference image as final canvas;
- do not invent accessories, certifications, claims, functions, materials, SKU variants or packaging;
- no Chinese text, no seller watermark, no platform logo;
- no poster style, no large black/yellow blocks, no cheap marketplace banner;
- if text is requested, use only validated Russian text from the plan.

For this reference flow, a clean real-photo image without marketing text is acceptable when the plan has empty `russian_text`.

## Output Report

After all possible slots finish, write:

`products/<product_id>/output/ozon-reference-image-generation-report.json`

with JSON:

```json
{
  "schema_version": "1.0.0",
  "status": "PASS",
  "product_id": "P000000",
  "generated_slots": ["products/P000000/output/generated-images/variant-main/main-001.png"],
  "failed_slots": [],
  "write_api_calls": 0,
  "inventory_api_calls": 0,
  "generated_at": "ISO-8601"
}
```

If some slot cannot be generated, put it in `failed_slots` with a Chinese reason. Never mark a copied source image or local-script image as passed.

End with: `DONE ozon_reference_image_generation`.
