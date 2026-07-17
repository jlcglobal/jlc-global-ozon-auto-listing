---
name: ozon-ecommerce-designer
description: Turn one source-grounded product analysis into the complete Russian Ozon listing and ecommerce image sales design before any image is generated.
---

# Ozon Ecommerce Designer

Input: `product_id` for one product whose SKU selection, final Ozon category,
target stores, product analysis, measurements and pricing are already complete.

This Skill is the only commercial planning layer between product analysis and
image production. It uses the current connected Codex model. It must not use a
local fallback, generic template, another product's facts, or a credentialed
OpenAI/third-party API. If the connected Codex service is unavailable, preserve
the current checkpoint and wait for recovery.

## Required evidence

Formal production has one and only one product-data source: the current AI
Factory workbench collection. Before reading any commercial content, validate
`products/<product_id>/input/source-manifest.json` and require the exact
`product_id + collection_id + source_kind=workbench_collection` recorded there.
Read only the current product's registered `input/source.json`, selected SKU
images, `input/category-selection.json`, operator confirmation saved by this
same workbench collection when present,
`output/product-analysis.json`, product measurements and pricing, category-bound
Ozon attributes/dictionaries, and available Ozon Russia keyword provenance.
Every factual claim must point to one of these inputs. Low-risk ordinary fields
may be explicitly marked `estimated` with confidence. Do not invent brand,
certification, warranty, customs code, safety/load rating, material, function,
accessory, quantity or other high-risk facts.

Use `references/manual-ozon-flow-2026-07-12/` only as an information-design
quality baseline. Learn the complete main-plus-detail sales structure; never
copy that reference product's facts, palette or copy into another product.

Images or specifications sent in a Codex conversation are `manual_test`, not
formal product data. They belong only below
`test-data/manual-input/<test_case_id>/`; their results belong only below
`test-data/manual-output/<test_case_id>/`. Never copy them into `products/`, the
collection inbox, a product master, a payload or an Ozon queue. A manual-test
sample such as P900002 must stay a test identity and can never be uploaded.
All `P900000-P999999` identities are reserved for offline test/audit use and
are rejected by the formal production guard regardless of their directory.
The current batch-frozen `collection_id` and `source-manifest.json` SHA-256
must still match before the design is written or materialized.

Never borrow or match an image, specification, attribute or historical value
from another product, collection, archived product or test case, even when the
filename, appearance, SKU label or capacity is similar. A local product
reference is valid only when it is registered in this collection's manifest
and physically below the current product's `input/sku-images`,
`input/main-images` or `input/detail-images`.

## Single design artifact

Write `products/<product_id>/output/ozon-ecommerce-design.json` atomically. It
must validate against `templates/ozon-ecommerce-design.schema.json` and
`scripts/ozon_ecommerce_designer_contract.py` and contain, in one coherent
decision:

1. source-grounded product understanding;
2. Russian buyer profile, motivations and objections;
3. natural Russian SEO title and short title, not Chinese word order;
4. primary, long-tail, scene and excluded keywords with provenance, never
   fabricated search volume;
5. a complete multi-paragraph Russian description split into product value,
   usage scenarios, core advantages, usage method and purchase notices;
6. three to six traceable selling points;
7. exactly 30 unique Russian hashtags;
8. a category attribute plan separating facts, estimates and unknown high-risk
   fields;
9. Russian SKU names and exact SKU differences;
10. one SKU-bound main-image design for every selected SKU;
11. exactly eight shared-detail designs in one buyer-decision sequence.

The production image contract is always `N SKU main images + 8 shared detail
images`, where `N` is the current selected SKU count from 1 through 10. Each
selected SKU has exactly one main image bound only to its own real reference,
capacity/size, color, configuration and copy. The shared detail set is generated
once for the whole product group and may claim only facts shared by all selected
SKUs. SKU differences belong in one deterministic comparison image using the
real SKU references, never in duplicated per-SKU detail sets.

Every image design must include: layout type, commercial purpose, buyer
question, exact source references, exact Russian overlay copy, complete visual
prompt, deterministic overlay modules and immutable product features. Supported
layout types are `sku_main`, `core_benefit`, `structure_callout`, `usage_scene`,
`sku_comparison`, and `purchase_notice`. Multi-SKU products use all six. A
single-SKU product uses the other five and replaces comparison with another
source-grounded detail role; it must never fabricate a variant comparison.

## Image design rules

The filesystem is authoritative:

- real workbench assets: `input/{sku-images,main-images,detail-images}`;
- unreviewed AI candidates: `output/generated-images/{variant-main,detail}`;
- rejected/failed AI images: `output/rejected-generation`;
- explicitly confirmed images: `output/accepted-images`.

Never scan any `output` tree for a product reference and never write an
AI-generated image into `input`. A style/layout baseline is not a product
reference. Regeneration or replacement invalidates the corresponding accepted
copy without overwriting the source asset.

The final visual is not a photo with two giant lines at the top. Design a real
ecommerce information hierarchy: product name, SKU/capacity badge, parameter or
dimension callouts where relevant, benefit sections, icons/arrows and clear
Russian copy. Choose palette, light, composition and mood for the exact product.

AI reference editing is permitted only for faithful scene/base visuals. Exact
Russian text, badges, dimension lines, icons and information modules are added
by the deterministic ecommerce layout renderer. SKU comparison, dimensions,
structure and package contents use real-image deterministic composition; AI
must not redraw the product. Preserve shape, color, transparency/material
appearance, structure, proportions, SKU differences and accessory quantity.

Forbid plain white catalog defaults, generic/repeated templates, Chinese or
garbled text, supplier labels/watermarks, browser controls, incorrect Russian,
invented accessories and changed SKU proportions.

## Materialization and handoff

After writing the design, run:

`$CAF_PYTHON_BIN scripts/ozon_ecommerce_designer_contract.py products/<product_id> --materialize`

This validation-only projector writes the existing compatibility artifacts
(`copy-ru.json`, title/description/tags, ecommerce creative brief) from the
already completed design. It never invents content and never calls Ozon.

`image_plan` consumes the design and its projected artifacts. `image_generation`
must not reanalyse the product or rewrite prompts. In manual mode, successful
generation and hard QC end at `WAITING_MANUAL_REVIEW` with workbench text
`等待人工检查`; do not open a preview automatically and do not upload. Automatic
mode follows the existing explicit global switch.

In manual mode, candidates are not uploadable. Upload is blocked until the
accepted tree contains exactly one confirmed main for every selected SKU plus
exactly eight confirmed shared details, with no missing or extra planned image.
Every accepted file must also match the immutable accepted-image manifest for
the current collection and ecommerce-design hash. Regeneration, replacement,
deletion or a design change revokes the affected confirmation.

Never submit inventory fields or call inventory endpoints. During development
or offline acceptance, never call Ozon CREATE, UPDATE or read-status endpoints.
