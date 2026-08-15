---
name: image-generator
description: Generate one authorized Ozon image slot from the current product's compiled image plan and real references.
---

# Image Generator

Process only the `product_id` and `slot` named by the batch runner. The batch is already authorized and unattended: do not ask questions, wait for confirmation, analyze another product, or change another slot.

## Source of truth

Read the named slot from `products/<product_id>/output/image-plan.json`. Its `prompt`, `sku_identity`, `russian_text`, `art_direction`, `overlay_plan`, `operation`, `reference_product_images`, and `output_path` are the complete image contract. Do not append a global house style or rerun product analysis, category matching, copywriting, pricing, attributes, or image planning.

Use only manifest-registered real files from this product's `input/main-images`, `input/sku-images`, and `input/detail-images`. Resolve references to absolute paths before the built-in image call. Generated candidates and files from another product, archive, manual fixture, or output directory are never references.

The SKU identity is compiled from the exact SKU title, every structured option, and that SKU's own reference image. It may be distinguished by color, dimensions, capacity, quantity, set composition, style, structure, material, accessories, or another real option. Never infer the SKU from image order or dimensions alone.

Reference priority is simple: for a SKU main image, the SKU's own reference is
the identity lock. Current-product main and detail images are supporting
references for visible structure, installation/use, dimensions shown in source
text, and scene logic. They are useful evidence, not permission to redesign the
product. If references are weak, generate a simpler faithful image rather than
adding a new mechanism, material, accessory or exact number.

When the SKU's own reference is below 600 px, it locks variant identity and
colour scope but cannot carry photoreal texture alone. In that case pass it
together with the best current-product main images that show the same product
(up to the five-reference limit): the SKU reference locks the variant, the
larger real photos supply structure and material texture. Follow the colour
scope named in the slot prompt: a `body` colour stays the dominant body colour;
an `accent` colour (magnetic ring, lid, handle, trim) applies only to that
part and stays prominent while the body keeps its own neutral/material colour.
Never invent structure because a reference is small.

If preflight is missing or stale, run the project Python (`.venv/bin/python3` when present, otherwise the current `sys.executable`) for `scripts/image_source_preflight.py products/<product_id>` once. Do not use bare system `python3`, because it may not have project dependencies such as Pillow. If the required SKU reference is missing, fail this slot. A reference below 600 pixels may identify the visible product and SKU, but it cannot prove small details, dimensions, capacity, certification, or comparison facts. Never rewrite `input`.

## Generate the slot

- `generate_from_reference` (default): the reference images are a FACT LOCK
  only — they tell you WHAT the product is: its body structure, the magnetic
  ring vs clamp distinction, colour, proportions and the SKU difference. The
  built-in image tool generates a brand-new photoreal Ozon image plus clean
  infographic typography from that fact lock. It must NOT copy the reference's
  pixels, its 3D-render/CGI look, its Chinese text, its supplier brand or
  watermark, and it must NOT reproduce a different variant's structure that
  appears in a mixed gallery (for example a clamp arm on a magnetic mount).
- `edit_real_image`: edit from the slot's real SKU/product references while keeping the same physical product (only when the plan explicitly names it).
- `compose_from_real_images`: compose only source-backed comparison, dimensions, set composition, or other exact-evidence content (only when the plan explicitly names it).
- `needs_human_input`: stop this slot and record the missing fact or reference.

**Photoreal or FAIL (hard rule).** The saved image must read as a real
photograph taken by a seller camera: real material texture, lens depth,
environment light, soft shadows, believable reflections, photographic contrast.
It must never look like a 3D render, CGI mockup, vector graphic, flat
illustration, or a poster with a slogan headline. For `generate_from_reference`,
the product body, structure and colours must still match the fact lock from the
references exactly — generate the photo, do not copy the supplier poster. If
the built-in tool cannot produce a photoreal result, or the saved image looks
like a render/illustration/poster or copies a mixed-gallery variant, write a
FAIL receipt for this slot and stop; do not keep correcting inside the same
child process.

Use only Codex's built-in image tool — no OpenAI API, no third-party image API,
no local script. Generate by IMAGE-TO-IMAGE: attach the reference images to the
image tool so it locks the product's real structure, colour and proportions.
Keep the product exactly as the references show — do not invent, add or remove
any part, mechanism or accessory, and do not copy a different variant's
structure from a mixed gallery. Clean away everything that is NOT the product:
the supplier promo background, Chinese text, seller logo/watermark, 3D-render
look and any frame/banner, then place the unchanged product into a clean
ecommerce scene and infographic layout per the slot prompt.

Follow the designer's slot-specific scene. Background, camera, light, text position, accent colors, and composition must be chosen dynamically for this product, SKU, buyer question, and available space. Do not impose a fixed background, palette, reusable card template, or left-text/right-product layout. Keep only typography quality, hierarchy, alignment, readability, and visual continuity consistent across the set.

Visible text is a whitelist, not an inspiration list. Render only the exact strings in the slot's `russian_text` or explicit `overlay_plan` text, once each if used. For size diagrams only, one combined source-backed dimension such as `101 × 68 × 146 мм` may be split into exact component line labels such as `101 мм`, `68 мм`, and `146 мм`; do not add any other words around them. The slot `prompt`, `strategy`, `purchase_reason`, `buyer_question`, `selling_goal`, `visual_goal`, SKU identity, verified facts, category names, prompt labels and hard rules are internal instructions and must never appear as visible text. If the slot is being retried after `unexpected_russian_text`, use a cleaner layout with fewer words and no badges, captions, paraphrases or decorative labels beyond the whitelist.

Do not make poster text-pasting. Large readable commerce text is allowed when it labels real product proof: dimensions, folding/usage steps, color, SKU variant, visible structure, or a source-backed buyer benefit. The failure is generic background plus unrelated words. Visible Russian text must help explain the product photo or infographic, not replace it. The SKU reference locks the current variant; current-product main/detail references may support structure and usage when present. Do not invent a new product because a single reference is weak.

**Layout like a mature Ozon infographic, not a block of small text.** Every
detail image must be structured as clear proof modules, never a paragraph of
small print squeezed into a corner. Use: one clear large title; part callouts
where each callout is a part name plus one short explanation with a thin leader
line to the REAL part (structure/installation images); a comparison table with
labelled rows (SKU comparison images); numbered step rows (usage/instruction);
or icon-plus-short-label rows (benefit/scene images). Hierarchy must read
title > section label > explanation at a glance. Each text module must sit on
high-contrast space and align to the product or proof element.

For SKU main images, the product photo must sell first, but the main image must
still name what the product is: a short product-type/name line, the SKU
difference (colour/spec) and one core source-backed benefit note, then the
subtle JLC GLOBAL watermark. Do not render the full listing title, SKU name or
model as a huge headline block, and do not strip the image down to one floating
label either. The visual quality target is premium Ozon product photography:
believable lens depth, material texture, soft shadows, clean reflections, real
environment light and restrained color grading before any text treatment.

Do not add Chinese, garbled text, unsupported claims, seller marks, supplier decoration, or invented accessories. The product remains visually primary.

Render text modules as polished ecommerce information design. Use compact
Russian typography with clear hierarchy, strong contrast, disciplined spacing
and alignment to a real proof element: product edge, measurement line, SKU
tile, step panel, callout path or natural negative space. Do not repeat a
default upper-left vertical-line title stack, random corner label, tiny spec
pile, decorative badge strip, or large empty text panel. When the slot's text
cannot be tied to product proof, use less text and make the product photograph
carry the message.

Preserve the current SKU's product type, product-body structure, color, visible proportions and dimensions, specification, openings/interfaces, confirmed included accessory count, and exact set composition. Reference-scene props, food, plants, tableware, stands, cleaning tools, decoration, or unconfirmed display items are not hard accessories unless current product facts explicitly say they are included. The scene may change; the physical product and SKU may not.

Save one 3:4 PNG of at least 900x1200 to the slot's declared `output_path`, without overwriting input. Never create an alternative or touch a passed slot.

Use at most one built-in image generation/edit call per slot invocation. If that
call returns no usable saved image, or the generated image has a factual hard
failure such as wrong product, wrong SKU, invented accessory, changed structure,
wrong SKU colour (a `body` colour painted only as a dot, or an `accent` colour
missing from its part / wrongly repainting the whole body), unreadable Russian,
unrelated poster text, a slogan headline block, a 3D-render/CGI/illustration
look instead of a real photograph, or invented structure (for example a clamp
arm or bracket on a magnetic mount), write a FAIL receipt
for this slot and stop. Do not keep correcting inside the same child process.
The parent batch runner owns targeted retries and will preserve passed slots.

## Slot receipt

After the image is saved, check only:

- the file is readable and 3:4;
- the current product/SKU is correct;
- product-body structure, color, visible dimensions/proportions, specification, confirmed included accessories, quantity, and set composition are unchanged;
- the image reads as a real seller photograph (photoreal texture, lens depth, environment light), not a 3D render, CGI mockup, vector or flat illustration;
- Russian text is readable and there is no Chinese or garbling.
- there is no unrelated poster text-pasting and no slogan headline block: large text is acceptable only when it is integrated with product proof, dimensions, steps, SKU choice or usage explanation.
- for `main-` SKU images only, record whether the product is the visual lead and whether the text works as ecommerce information. Treat this as quality telemetry unless the product is wrong, hidden, unreadable, or factually changed.

Do not fail on subjective aesthetics, text size, a chosen background, layout, text position, palette, or an empty decorative area unless it hides/misrepresents the product, makes Russian unreadable, or creates unrelated poster text with no product proof.

Write only `output/image-slot-results/<slot>.json` with `product_id`, `slot`, `output_path`, `status`, `attempt`, `sha256`, `dimensions`, `hard_failures`, `checked_at`, `generation_source`, `designer_prompt_followed`, `visual_acceptance`, and `local_script_generation`.

For non-main detail slots, `visual_acceptance` may be omitted. For any `main-` SKU image, include it when practical:

```json
{
  "visual_acceptance": {
    "status": "PASS",
    "checks": {
      "product_visually_dominant": true,
      "text_integrated_not_poster": true,
      "title_not_dominating_product": true,
      "main_three_second_click": true
    },
    "failures": []
  }
}
```

If any visual-quality check is weak but the product/SKU facts, structure,
color, readable Russian and technical image requirements are correct, keep
`status="PASS"` and record the weakness in `visual_acceptance.quality_notes`.
Write `status="FAIL"` only for factual identity errors, hidden/wrong product,
unreadable/garbled text, or technical image failures.

A PASS receipt must contain `generation_source="built_in_image_tool"`, `designer_prompt_followed=true`, and `local_script_generation=false`. If the built-in image tool is unavailable, write FAIL with `built_in_image_tool_unavailable`; never substitute a local script.

Do not edit shared checkpoints, `image-plan.json`, `status.json`, Ozon data, inventory, or any other slot. Finish with only `DONE <slot>`.
