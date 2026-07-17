---
name: image-generator
description: Generate the current product's real 3:4 Ozon image package from validated source references.
---

# Image Generator

Read `products/<product_id>/output/image-source-preflight.json`, `ecommerce-creative-brief.json`, `image-plan.json`, `style-profile.json`, `product-analysis.json`, `product-positioning.json`, `title-ru.json`, `description-ru.json`, `ozon-tags.json`, `ozon-attributes-final.json`, `pricing-result.json`, `platform-grouping-result.json`, and only the current collection's manifest-registered real files under `input/main-images`, `input/sku-images`, and `input/detail-images`.

Formal production must reject manual-test paths, another product/collection,
archived data and every output path as an input reference. Never infer a match
from filename, image similarity, SKU name or capacity. `test-data/manual-input`
and `test-data/manual-output` are isolated acceptance fixtures, not production
sources.

Before planning or regenerating, revalidate the batch-frozen collection ID and
`source-manifest.json` SHA-256. A changed collection snapshot requires a new
batch; never silently accept an in-place rewrite of both source and manifest.

Before the first image call, verify that `image-plan.json -> listing_context.ready=true` and that every planned slot has a non-generic `prompt`. The pipeline has already analyzed the product and completed text/attributes; do not repeat product analysis, category matching, copy generation, price calculation or field completion. Generate all prompts first, then generate images.

Do not invoke any external marketing, branding, photography, or image-generation Skill. Do not call OpenAI API, third-party AI API, or any credentialed image service. Use only the current Codex session's built-in image editing capability when the image plan explicitly permits AI reference editing.

Before every built-in image tool call, resolve every `reference_product_images` entry against the project root and pass only absolute filesystem paths such as `/Users/apple/Documents/crossborder-ai-factory/products/...`. Never pass a relative `products/...` path to the image tool. Output files must still be copied or saved to the relative `output_path` declared by `image-plan.json`, always below `output/generated-images/variant-main` or `output/generated-images/detail`.

The built-in image editor accepts at most five reference paths per call. A SKU main uses exactly its own SKU reference. A shared image uses at most five references, prioritizing the selected SKU references and then the clearest real main images.

## One pre-generation check

Before generating any slot, inspect `output/image-source-preflight.json`. If it already says `PASS` and its preferred reference files still match the recorded dimensions and hashes, reuse it and do not run another check. Only when the report is missing or stale, run `python3 scripts/image_source_preflight.py products/<product_id>` once. Image generation is read-only with respect to `input`: preflight must not download, upgrade or rewrite a source image. A source-quality problem returns to the workbench collection step and is recorded in a new collection snapshot.

An authorized batch is unattended. When `status.json` has `task_authorized=true`, do not ask the user any question and do not wait for confirmation. Execute only the requested image-generation step and save the checkpoint.

If any required SKU reference remains below 600 pixels, stop before generation and record the blocked SKU. Never enlarge, nearest-neighbour scale, pixel-replicate, or auto-cut a low-resolution thumbnail. Never treat a clean generated background as a completed product image.

Compare all selected SKU references once and record the confirmed differences in size, color, structure, quantity/configuration and accessories. Source facts and confirmed manual values take precedence; unknowns stay `unknown`. Do not repeat this full check for every image.

## Image-type routing

Follow each slot's `operation` in `image-plan.json`:

- `compose_from_real_images`: required for SKU comparison, dimensions, color-accuracy, package-content and other exact-evidence images. Use deterministic crop, mask and layout from the real source images. AI must not redraw the product. Labels and Russian text may be added separately.
- `edit_real_image`: allowed for SKU main images and lifestyle/benefit/scene/detail images. Pass the actual local reference images to the built-in image editor. Preserve product identity, proportions, color, transparency, structure, openings, hardware, markings and accessory count. The scene may change; the product may not become a different model.
- `needs_human_input`: stop that slot. Do not substitute another SKU, generic product or invented reference.

Do not force every slot through `locked_product_compositor.py`. It may be used only when a clean, sufficiently large product-only source really supports deterministic compositing. A pixel hash match alone is not semantic QC and must never pass a fragmented, incomplete or unreadable product cutout.

Generate exactly one SKU-specific main image for every selected SKU, then generate exactly eight product-specific shared detail images declared by `ecommerce-creative-brief.json` and `image-plan.json`. The eight commercial purposes are fixed only as buyer questions; scenes, palette, composition and copy are selected for this exact product. A shared image must use facts common to every selected SKU and must not imply that all variants are included in one order.

Generate all SKU main images before any detail image. Save each image immediately so it appears progressively in the workbench. The whole set targets five minutes; do not spend time producing alternative main candidates or a second full-set review.

Every image has one different buyer-decision job. Do not reuse the same composition with only a background change. Main images must use a distinctive, truthful atmosphere and exactly one short, large Russian sales message. Detail images may use more copy and information. Plain white-background product images, generic posters and reusable product templates are forbidden.

Use `references/manual-ozon-flow-2026-07-12/` only as the seller's ecommerce-quality baseline: a coherent Russian-language main-plus-detail set with a prominent product, real usage context, SKU choice, structure and purchase guidance. Never copy that reference product's facts into the current product. A technical preview, white-background cutout, isolated product rendering, or repeated card layout is not a completed ecommerce image.

When `input/operator-guidance.json -> image_detail_roles` is present, follow its exact eight product-specific roles after validating that every claim and reference is grounded in the current product. This override controls commercial storytelling only; it cannot bypass source-image checks or change product facts.

The `size_spec` image reads only `output/cost-analysis.json -> product_dimensions`. Confirmed measurements use `Размеры`; estimates use `Примерные размеры`. Package measurements must never be presented as product measurements.

All final images are portrait 3:4 PNG, at least 900×1200. Reject Chinese text, seller watermarks, 1688 decorations, false parameters, false certification, invented accessories, wrong SKU colors and visibly deformed product structures.

Use the exact `russian_text` from the plan. For text accuracy, generate the visual without lettering and then render the exact copy with `python3 scripts/image_text_overlay.py --input <final.png> --output <final.png> --text '<exact text>' --kind <main|detail>`. Reserve only natural negative space integrated into the scene. Never ask the image model to draw an empty typography container: blank rounded rectangles, empty text boxes, placeholder cards, bordered empty panels and decorative empty frames are forbidden. The overlay is adaptive to the image and must not use a fixed black panel. A non-size detail image with `russian_text=unknown` is blocked before generation.

## Per-image hard gate only

As soon as one image is saved, inspect only these hard failures: wrong product/SKU, wrong color, invented accessory or function, obvious deformation, Chinese/garbage text, unreadable Russian, large blank placeholder box or empty bordered panel, unreadable file or wrong 3:4 ratio. If one occurs, retry only that slot once and continue the remaining product set. Do not wait for a full-set inspection, and do not rerun passed images. Never upload or display a hard-failed image as a completed result.

Update `output/image-hard-gate.json` after each saved image. It contains `mode: hard_failures_only`, `checked_slots`, `critical_failures`, and `issues`. When every planned slot has been checked, run `python3 scripts/image_qc.py products/<product_id> --hard-gate --write` once to produce the compatibility report used by the existing pipeline.

Generated candidates never become sources. Rejected, interrupted or failed
candidates move below `output/rejected-generation`. Only an explicit workbench
confirmation copies a candidate below `output/accepted-images`. Manual upload
is forbidden until the accepted tree contains exactly the planned N SKU mains
and eight shared details recorded with current hashes in
`output/accepted-images/manifest.json`. Regeneration invalidates its previous
accepted copy and never overwrites `input`.
