---
name: image-generator
description: Generate the current product's real 3:4 Ozon image package from validated source references.
---

# Image Generator

Read `products/<product_id>/output/image-source-preflight.json`, `image-plan.json`, `style-profile.json`, `product-analysis.json`, `product-positioning.json`, and the real files under `input/main-images`, `input/sku-images`, and `input/detail-images`.

Do not invoke any external marketing, branding, photography, or image-generation Skill. Do not call OpenAI API, third-party AI API, or any credentialed image service. Use only the current Codex session's built-in image editing capability when the image plan explicitly permits AI reference editing.

Before every built-in image tool call, resolve every `reference_product_images` entry against the project root and pass only absolute filesystem paths such as `/Users/apple/Documents/crossborder-ai-factory/products/...`. Never pass a relative `products/...` path to the image tool. Output files must still be copied or saved to the relative `output_path` declared by `image-plan.json`.

The built-in image editor accepts at most five reference paths per call. A SKU main uses exactly its own SKU reference. A shared image uses at most five references, prioritizing the selected SKU references and then the clearest real main images.

## One pre-generation check

Before generating any slot, inspect `output/image-source-preflight.json`. If it already says `PASS` and its preferred reference files still match the recorded dimensions and hashes, reuse it and do not run another check. Only when the report is missing or stale, run `python3 scripts/image_source_preflight.py products/<product_id>` once. The preflight may recover the original-resolution 1688 image from a thumbnail URL into `input/sku-images/source-upgrades/`; it must never overwrite the originally collected file or `input/source.json`.

An authorized batch is unattended. When `status.json` has `task_authorized=true`, do not ask the user any question and do not wait for confirmation. Execute only the requested image-generation step and save the checkpoint.

If any required SKU reference remains below 600 pixels, stop before generation and record the blocked SKU. Never enlarge, nearest-neighbour scale, pixel-replicate, or auto-cut a low-resolution thumbnail. Never treat a clean generated background as a completed product image.

Compare all selected SKU references once and record the confirmed differences in size, color, structure, quantity/configuration and accessories. Source facts and confirmed manual values take precedence; unknowns stay `unknown`. Do not repeat this full check for every image.

## Image-type routing

Follow each slot's `operation` in `image-plan.json`:

- `compose_from_real_images`: required for SKU comparison, dimensions, color-accuracy, package-content and other exact-evidence images. Use deterministic crop, mask and layout from the real source images. AI must not redraw the product. Labels and Russian text may be added separately.
- `edit_real_image`: allowed for SKU main images and lifestyle/benefit/scene/detail images. Pass the actual local reference images to the built-in image editor. Preserve product identity, proportions, color, transparency, structure, openings, hardware, markings and accessory count. The scene may change; the product may not become a different model.
- `needs_human_input`: stop that slot. Do not substitute another SKU, generic product or invented reference.

Do not force every slot through `locked_product_compositor.py`. It may be used only when a clean, sufficiently large product-only source really supports deterministic compositing. A pixel hash match alone is not semantic QC and must never pass a fragmented, incomplete or unreadable product cutout.

Generate exactly one SKU-specific main image for every selected SKU, then generate the product-specific shared detail sequence declared by `image-plan.json`. The shared detail sequence contains six to eight images chosen for this exact product; it is not a fixed category template and it does not require a disclaimer image. A shared image must use facts common to every selected SKU and must not imply that all variants are included in one order.

Generate all SKU main images before any detail image. Save each image immediately so it appears progressively in the workbench. The whole set targets five minutes; do not spend time producing alternative main candidates or a second full-set review.

Every image has one different buyer-decision job. Do not reuse the same composition with only a background change. Main images must use a distinctive, truthful atmosphere and exactly one short, large Russian sales message. Detail images may use more copy and information. Plain white-background product images are forbidden.

The `size_spec` image reads only `output/cost-analysis.json -> product_dimensions`. Confirmed measurements use `Размеры`; estimates use `Примерные размеры`. Package measurements must never be presented as product measurements.

All final images are portrait 3:4 PNG, at least 900×1200. Reject Chinese text, seller watermarks, 1688 decorations, false parameters, false certification, invented accessories, wrong SKU colors and visibly deformed product structures.

Build the visual around an intentional typography area and the exact `russian_text` from the plan. For text accuracy, generate the visual without lettering and then render the exact copy with `python3 scripts/image_text_overlay.py --input <final.png> --output <final.png> --text '<exact text>' --kind <main|detail>`. The overlay is adaptive to the image and must not use a fixed black panel. A non-size detail image with `russian_text=unknown` is blocked before generation.

## Per-image hard gate only

As soon as one image is saved, inspect only these hard failures: wrong product/SKU, wrong color, invented accessory or function, obvious deformation, Chinese/garbage text, unreadable Russian, unreadable file or wrong 3:4 ratio. If one occurs, retry only that slot once and continue the remaining product set. Do not score aesthetics, do not wait for a full-set inspection, and do not rerun passed images. Never upload or display a hard-failed image as a completed result.

Update `output/image-hard-gate.json` after each saved image. It contains `mode: hard_failures_only`, `checked_slots`, `critical_failures`, and `issues`. When every planned slot has been checked, run `python3 scripts/image_qc.py products/<product_id> --hard-gate --write` once to produce the compatibility report used by the existing pipeline.
