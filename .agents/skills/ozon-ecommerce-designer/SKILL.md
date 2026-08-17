---
name: ozon-ecommerce-designer
description: Analyze one source-grounded product and make the complete Russian Ozon listing plus product-specific commercial art direction for every SKU main and shared detail image before generation. Use whenever AI Factory needs listing copy, selling points, image prompts, visual strategy or image regeneration; never fill a reusable visual template.
---

# Ozon Ecommerce Designer

Input: `product_id` for one product whose SKU selection, final Ozon category,
target stores, product analysis, measurements and pricing are already complete.

This Skill is the only commercial planning layer between product analysis and
image production. It uses the current connected Codex model. It must not use a
local fallback, generic template, another product's facts, or a credentialed
OpenAI/third-party API. If the connected Codex service is unavailable, preserve
the current checkpoint and wait for recovery.

Read `references/product-specific-image-standard.md` before making any image
decision. Treat it as the seller's quality and reasoning standard, not as a
palette or layout template.

The user-accepted P900002 image set defines the minimum commercial finish:
the product is visually dominant, the Russian hierarchy is immediately
readable, the contrast is scroll-stopping, and every visible element answers a
purchase question. Inherit only that level of commercial clarity and finish.
Never inherit P900002's refrigerator scene, green palette, left-right layout,
icons, wording, specifications or any other product fact.

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

The selected Ozon category is upload metadata and a field plan only; it is not
visual evidence for image content. Do not use category names or old visual
templates to invent concrete contents, accessories or scenes. Any visible
contents, props, included accessories, use scenes or product state in the image
plan must be supported by the current product title, main images, detail images,
SKU images, structured attributes, product analysis or visual evidence. Missing
capture means `unknown`, not proof that an item exists or does not exist. When
evidence is insufficient, design the image around the product body, a
closed/empty state, or a neutral believable use scene. Never add rice, nuts,
grains, snacks, feed, pet food or similar category/template filler unless the
current product evidence explicitly supports it; for example, a protein powder
divider can use protein powder, gym or travel context only when grounded, but it
must not become a rice box or nut container.

When `products/<product_id>/output/visual-reference-analysis.json` exists, read
it only as optional Ozon competitor/reference photography guidance produced by
`$ozon-image-prompt-reverse`. It may influence camera feel, lens distance,
lighting, background realism, shallow depth of field, seller-photo imperfections
and composition rhythm. It is not current-product evidence. Never copy or infer
competitor brand, store name, watermark, model number, packaging, accessories,
certifications, exact title/description text, dimensions, weight, material or
function from that reference file. Current product facts and Ozon upload fields
must still come from the formal current-product sources above.

When `output/attribute-fill-input.compact.json` exists, use it only as light
field context for the selected Ozon category, SKU/aspect fields and obvious
dictionary constraints. The ecommerce designer is not the upload field
compiler. Keep `attribute_decisions` minimal: preserve the current `input_hash`
and include only clear source-backed decisions that affect SKU identity,
visible color/material differences, buyer copy or image planning. Empty
`common_attributes` and `attributes_by_sku` are acceptable when no such
decision is needed.

Do not enumerate every required, optional or dictionary attribute inside the
visual design artifact. Complete required Ozon fields and exact dictionary
validation are handled later by deterministic `field_completion` and upload
preflight. Never open the full `output/attribute-fill-input.json` merely to
fill a table during design. If a value cannot be proven from current product
facts, leave it out of design or mark it unknown; never submit a raw source
word as an Ozon dictionary value.

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

If a selected SKU has no SKU-owned image, the current-product, current-collection
`input/sku-image-bindings.json` may bind one already collected image from this
same product's `input/main-images`, `input/detail-images` or `input/sku-images`
to that SKU. Treat this as `user_bound_reference_image` and
`scope=reference_image_only`. It is only a visual reference chosen by the
operator; it is never a 1688 SKU-owned image and never supplies the target SKU's
facts.

The legacy exception is a current-product, current-collection
`input/manual-confirmation.json -> sku_image_reference_overrides` entry with
`decision=user_confirmed_same_appearance`,
`scope=reference_image_only`, and `must_preserve_target_sku_facts=true`. In both
binding cases the named image may be used as visual reference for the target SKU
only. Capacity, dimensions, price, SKU name, Russian copy and comparison text
must still come from the target SKU; if the reference image text says `1500 ml`
and the target SKU is `1000 ml`, scale the visible product proportionally and
render the target `1000 ml` text, never inherit the reference capacity as a
fact.

## Single design artifact

Write `products/<product_id>/output/ozon-ecommerce-design.json` atomically. It
must validate against `templates/ozon-ecommerce-design.schema.json` and
`scripts/ozon_ecommerce_designer_contract.py` and contain, in one coherent
decision:

**How to write the file (mandatory).** Compose the complete design JSON in a
single shell step and write it with the project atomic writer — never create
an empty placeholder first, never use `apply patch` for this file, and never
finish while the file still holds `{}`:

```bash
$CAF_PYTHON_BIN - <<'PY'
import json, sys
from scripts.attribute_fill_input import write_json_atomic
from pathlib import Path
design = json.loads(sys.stdin.read())
write_json_atomic(Path("products/<product_id>/output/ozon-ecommerce-design.json"), design)
PY <<'JSON'
{ ...the complete design JSON, nothing left for a later step... }
JSON
```

After the write, verify the file starts with `{"schema_version"` and contains
both `main_images` and `detail_images`; if the verification reads `{}` or the
file is missing either array, rewrite the whole file again in one step. Only
after the full design is on disk may you run `--materialize`.

1. source-grounded product understanding;
2. Russian buyer profile, motivations and objections;
3. natural Russian SEO title and short title, not Chinese word order;
4. primary, long-tail, scene and excluded keywords with provenance, never
   fabricated search volume;
5. a complete multi-paragraph Russian description split into product value,
   usage scenarios, core advantages, usage method and purchase notices;
6. three to six traceable selling points;
7. up to 30 unique Russian hashtags using Russian letters only; no brands,
   numbers, underscores, Latin letters or capacity digits;
8. a category attribute plan separating facts, estimates and unknown high-risk
   fields;
9. Russian SKU names and exact SKU differences;
10. one SKU-bound main-image design for every selected SKU;
11. exactly eight shared-detail designs in one buyer-decision sequence;
12. a complete per-image art direction and exact model-native typography plan
    that the image model can execute in the same call as the final scene.

Ozon color-name fields have a stricter contract than ordinary text. For
`Название цвета`, attribute `10097`, or any equivalent color-name field, output
only one or more natural Russian color words. Do not include SKU capacity,
range, digits, units, Chinese, Latin letters, model names or specs. If a SKU
label combines color and capacity, split it: color goes only to the color field;
capacity goes only to capacity/size fields. Examples: `черный`, `зеленый`,
`хаки`, `прозрачный`; invalid: `601-800 мл`, `卡其色1.9L`, `black 1000 ml`.

Any AI-estimated weight that will be written as grams must be a positive
integer. Round up fractional grams and keep the original estimate only as
provenance/evidence, not as the Ozon-facing value.

The production image contract is always `N SKU main images + 8 shared detail
images`, where `N` is the current selected SKU count from 1 through 10. Each
selected SKU has exactly one main image bound only to its own real reference,
capacity/size, color, configuration and copy. The shared detail set is generated
once for the whole product group and may claim only facts shared by all selected
SKUs. SKU differences belong in one deterministic comparison image using the
real SKU references, never in duplicated per-SKU detail sets.

The eight shared details must form a product-specific buyer decision sequence,
without adding extra images, review gates or manual confirmation. Parameter,
instruction, SKU/style comparison, real-use context, macro/structure proof and
purchase-risk reminder are optional roles chosen from current product evidence.
Do not force an adult model, a fixed lifestyle scene, or a final
disclaimer/purchase-notice image when it does not help this product or the real
references do not support it. Estimates must be clear as approximate and
package dimensions must never be presented as product-body dimensions.

Plan the entire `N+8` image set in one connected-model execution and write one
atomic design artifact. Complete every slot's commercial purpose, art
direction, exact Russian copy, overlay plan and final prompt before returning.
Never alternate design and generation one image at a time. Image generation is
a later stage and cannot start while even one planned slot is incomplete.

Every image design must include: layout type, commercial purpose, buyer
question, image role, customer question, visual goal, shot type, composition,
must-show elements, avoid list, exact source references, exact Russian overlay
copy, complete visual prompt, immutable product features, design rationale,
per-image art direction and an exact overlay plan. `layout_type` names the
buyer-decision job only; it
never selects a visual template. Supported jobs are `sku_main`, `core_benefit`,
`structure_callout`, `usage_scene`, `sku_comparison`, and `purchase_notice`.
Multi-SKU products use all six. A single-SKU product uses the other five and
replaces comparison with another source-grounded detail role; it must never
fabricate a variant comparison.

`source_references` inside `main_images` and `detail_images` are image-generator
inputs, so every value must be a current-product image path below
`products/<product_id>/input/sku-images`, `products/<product_id>/input/main-images`
or `products/<product_id>/input/detail-images`. Never put JSON evidence files,
schemas, `output/product-analysis.json`, `output/merged-product-facts.json`,
category files, logs, URLs or non-image paths in image `source_references`.
Use those JSON files only in factual `source_refs`, attribute evidence,
description provenance or prompt wording.

Default `operation` is `generate_from_reference` for every slot: the reference
images are a FACT LOCK (body structure, magnetic ring vs clamp, colour,
proportions, SKU difference), and the image model generates a new photoreal
image plus clean infographic typography — it must not paste the supplier
poster's pixels, its 3D-render look, its Chinese text or watermark, and it must
not copy a different variant's structure from a mixed gallery. Use
`compose_from_real_images` or `edit_real_image` only when the operator has a
clean real product photo that must be kept pixel-faithful; supplier PROMO
posters must never be the composed canvas.

**SKU main structure is pinned (2026-08-15).** Every SKU main image must use
`generate_from_reference` with the SKU's own reference image as the structure
and colour fact-lock: the visible body shape, mounting/attachment structure,
proportions and accessories are described from that reference and reproduced
faithfully in the generated photo. Never invent a clamp arm, bracket, ring
shape or any structural element the SKU reference does not show. The scene may
change around the product; the product itself may not.

**Attachment mechanism is source-text-authoritative (global).** Two separate
facts must never be confused: (1) how the phone attaches — the SKU name/title
is authoritative here, so "磁吸/magsafe" means magnetic, never side claws or a
phone clamp; (2) how the mount itself attaches to the car — this is a DIFFERENT
fact, and if neither the source text nor a clear, unambiguous current-product
image confirms it (screen clip / adhesive / suction / vent), write it as
UNKNOWN and use neutral wording such as "крепление на панели / фиксируется на
панели / основание". Do not invent "зажим/экранный зажим", a silicone clamp,
adhesive tape, a suction cup or any specific mechanism. Read
`output/image-reference-exclusions.json` when it exists and treat every listed
image as NOT product evidence for any slot; a mixed gallery may show a
different variant's mechanism, which is not permission to copy it.

Each slot must translate product evidence into a concrete visual shooting task,
not just restate a selling point. Use this chain for every image:
`feature or buyer doubt -> customer_question -> visual_goal -> shot_type ->
composition -> must_show -> avoid -> final prompt`. The answer must be
product-specific and generated fresh for the current item; do not use fixed
templates, fixed shot lists or copied wording from another product.

Before writing any slot, define the product's own photographic world in
`visual_system`. This is not a house style and not a palette preset. It must
include these string fields:

- `photography_world`: the product-specific visual world derived from this
  item's use, material appearance, structure, buyer context and source images;
- `lens_plan`: how SKU mains and the eight shared details vary lens distance,
  crop, camera angle and proof type;
- `reference_editing_rule`: how reference images lock product identity while
  forbidding direct supplier-photo reuse as the final canvas;
- `material_value_signal`: what visible surface, edge, transparency, finish,
  texture, scale, shadow or construction detail makes this product look more
  valuable;
- `scene_variety_rule`: how the set avoids repeating one counter, shelf,
  fabric, desk, white studio, product-plus-text setup or side-info layout.

For a simple household product, this still matters: do not default every image
to a clean white kitchen counter with a product and side text. A plastic
storage container can use transparency, rim/handle/press-rod detail, top-down
inspection, shelf-placement context, adult model scale, size checking and
variant comparison as separate camera jobs. A brooch, electronics item,
tool, textile or bathroom item must each get its own visual world. The goal is
not luxury decoration; it is believable commercial photography with depth,
material evidence and a buyer moment.

In every slot, the `must_show`, `composition` and final prompt must separate
category context from observed product facts. Category can explain what Ozon
field set is being filled; it cannot authorize unrelated contents, scene props
or accessories.

Every non-technical sales image must also carry a buyer moment, not just a
product proof. Express the story inside the existing fields and final prompt;
do not add a new schema field. The story layer answers: who is the Russian
buyer or recipient, where the product naturally appears, what is happening in
the scene, what changed after the product is used/worn/placed, and why that
moment makes the product worth buying. This story must be a low-risk visual
context derived from the product type, usage scenes, buyer profile and source
facts. It must not invent included accessories, performance, certification,
medical/safety claims, brand status, exact event promises, or unsupported
materials.

For `customer_question`, avoid generic wording like "what fact helps decide".
Write a scene-aware shopping doubt: "Will this brooch make a plain blazer look
more formal?", "Will this organizer keep the bathroom counter calm in daily
use?", or "Will this device look clean on a work desk?" For `visual_goal`,
describe the visible before/after feeling or purchase emotion, such as a plain
outfit becoming a finished evening look, scattered items becoming orderly, or a
small device making the workspace look controlled. For `shot_type`, combine
camera distance with story task, not only lens type: mirror-before-leaving
upper-body scene, gift-table close lifestyle, morning desk setup, family
bathroom storage moment, travel packing flat-lay, macro inspection of material
before purchase. For `composition`, state the action/environment line: model
adjusting a lapel near a mirror, product on the dresser before going out,
object installed in a believable room, hand-scale context only when hands are
allowed, or a close material crop framed as buyer inspection. Keep the product
as the clear hero and text compact.

Do not force story into slots whose job would be damaged by it. A parameter
image may stay technical, but can still use a quiet "before buying, check the
size/facts" purchase context. A macro material image may be a buyer inspection
moment rather than a full lifestyle scene. A SKU comparison image should remain
deterministic and clear. The final purchase-notice image should feel like a
calm product-based reminder, not a lifestyle poster.

The new per-image fields mean:

- `image_role`: the exact role this image plays in the buyer decision, not just
  the schema `layout_type`;
- `customer_question`: the buyer doubt this picture answers, written as a
  concrete shopping question with a product-specific use or purchase moment
  whenever the slot is not purely technical;
- `visual_goal`: what the viewer must visually believe after seeing the image,
  including the practical or emotional change the product creates for the buyer
  when the slot is a sales or lifestyle image;
- `shot_type`: the camera and distance task, with lens/view language when useful
  such as full product hero, macro close-up, 85mm macro detail, angled close
  crop, top-down layout, medium lifestyle scene, wide room context or partial
  structure view, plus the story task when relevant;
- `composition`: where the product, crop area, environment and text live in the
  3:4 frame, including scene action, buyer context or environmental clue when
  the slot is not purely technical;
- `must_show`: concrete visible product elements that must appear in this slot;
- `avoid`: product-specific visual mistakes, factual inventions and composition
  failures forbidden in this slot.

Choose the shot from the product type and the proof needed by that slot. A main
image normally shows the complete product and SKU difference. A benefit image
may show the full product if the proof is overall usefulness. A material image
must be allowed to use macro close-up when the selling point is rhinestones,
beads, coating, fabric texture, surface finish, stitching, connector detail or
other small visible material evidence. A structure image must be allowed to use
partial detail, exploded/section-like composition, callout crop or side/angled
view when it proves visible construction. A scene image may increase the
environment share when scale, use context or room fit is the proof, while still
keeping the product understandable. The final purchase-notice image remains
grounded in product facts and buyer reminders.

The eight shared detail images should form a simple buyer journey instead of
eight isolated demonstrations: first attraction, practical use or wearing
moment, material/structure proof, scale or parameters, variant choice when
needed, fitting/placement context, close inspection, and final purchase notice.
The order may change by product, but the set must feel like a coherent shopping
story. Reusing the same "product on fabric plus text" setup with different
labels is not enough, even when the lens distance changes.

Examples of acceptable translation logic: rhinestones become a macro close-up
of the rhinestone area to prove sparkle and decorative quality; bead rows become
a close detail showing bead spacing and color contrast; a metal frame becomes
an angled edge or side-detail shot showing finish and structure. If a material
or claim is not proven, phrase the visual task as a visible appearance, such as
white bead detail or pearl-like decorative beads, never as a confirmed material.

Examples of story-aware translation logic: a brooch is not only "on a blazer";
it can be the final accent as an adult Russian buyer adjusts a coat or blazer
near a mirror before going out, with the brooch still dominant. A bathroom rack
is not only "storage levels"; it can show the morning routine with towels and
cosmetics orderly in reach, without inventing capacity or included items.
Electronics are not only "ports and specs"; they can sit in a clean work-desk,
travel or charging context that shows the buyer's problem becoming simpler,
while unsupported performance claims remain forbidden.

The final `prompt` for every slot is the complete production instruction, not
a short summary and not a handoff that asks the image model to make creative
choices. It must be self-contained enough to execute with only the listed real
references, but it must not repeat the whole project rulebook in every slot.
Keep each slot prompt concise, normally around 1200-1800 characters. Put only
the slot-specific product facts, reference priority, composition, exact Russian
text, and the few forbidden changes that matter for that slot.

Every prompt must explicitly include these decisions:

- the exact commercial message and purchase question answered by this image;
- the selected product-specific buyer moment, scene action or inspection moment
  and why it proves the selling point;
- canvas ratio, shot type, camera/view, composition, product position,
  crop/detail area when relevant and approximate product scale;
- high-contrast product-specific palette, lighting, material treatment and the
  concrete visual signal that makes the item look valuable rather than cheap;

**Understand the SKU colour scope before writing any prompt (mandatory, global).**
A 1688 SKU colour word does NOT always mean the body colour. It often names the
one distinguishing PART — a magnetic ring/star ring, a lid, a handle, a trim
strip — while the body is a neutral colour. Before writing the main-image
prompt for each SKU, the visual director must read the SKU title, that SKU's own
reference image and the current-product main/detail images, then decide and
record the colour scope:

- `body` : the colour word is the product-body colour. The prompt must render
  the body in that colour over most of its visible area, and make it the first
  palette entry. Example: a red kettle — "корпус целиком красный".
- `accent`: the colour word is a distinguishing part, not the body. The body
  stays its own neutral/material colour (black aluminium, silver steel, etc.);
  the named colour applies ONLY to that part (the magnetic ring, lid, handle,
  trim) and must be prominent. Example: a magnetic phone mount whose SKU is
  "оранжевый" — "чёрный алюминиевый корпус + ярко-оранжевое магнитное кольцо",
  never "корпус целиком оранжевый".

Decide this per SKU from evidence, never assume body by default, and never
apply one SKU's scope to another. Record the decision in `sku_plan` /
`art_direction.palette` (first entry = the dominant colour, body or accent
ring) and state it verbatim in the image prompt. The scene background is only
a contrast bed and must never flip a coloured body into a dark body with a
small coloured dot, nor a coloured ring into a full-body repaint.

- the product or real product-photo area must be the largest visual zone in the
  image; text modules support the product and must not become the dominant
  area;
- every Russian text module must have explicit high-contrast treatment chosen
  for this product and slot: compact product information on natural negative
  space, small supporting copy, refined line icons, thin dividers, measurement
  arrows, a calm side information column, or a subtle plate only when that fits
  the product. Pale text on a similar background is not acceptable, but one
  fixed headline/badge/card/chip/plate style is also not acceptable;
- text modules must look like designed product information, not temporary
  captions. Do not use a default upper-left vertical-line title stack, isolated
  corner label, tiny three-line specification pile, or white text floating on a
  plain shadow area. Bind every text group to the product proof: dimension
  lines, structure callouts, SKU labels, step numbers, compact information
  groups, natural negative-space title groups, or labels aligned to the product
  edge, shadow, tabletop perspective or scene geometry;
- every final Russian text line verbatim, with hierarchy, placement,
  typography, color, emphasis and relationship to the product;
- the exact real-image references, immutable product/SKU features and allowed
  editing operation;
- a clear reference-image rule: the real reference locks product identity,
  color, structure, proportions and SKU facts, but the final image must be a
  newly composed ecommerce photograph/scene following this slot's camera task;
  it must not paste the supplier/source image as the canvas and simply add
  Russian text;
- forbidden inventions, generic template treatments, garbled/Chinese text,
  watermarks, extra accessories and any change to product identity.

Reference discipline is part of the creative decision, not a separate hard
template. The SKU image locks SKU identity for a main image. Current-product
main and detail images may support visible structure, installation/use,
dimensions shown in source text, and scene logic. If a detail or SKU image shows
clear readable dimensions, capacity, folding steps, installation position, or
configuration, treat it as source-image evidence for this product and preserve
that evidence in the prompt. If the reference is weak or unreadable, choose a
simpler truthful composition instead of inventing product structure, material,
accessories, or exact numbers.

The complete prompts for all `N+8` slots must be present in the single atomic
design before image generation starts. The generator is an executor: it must
not pick a new scene, rewrite copy, weaken the hierarchy, add a house style or
finish an incomplete prompt by itself.

## Prompt-first recovery policy

Do not turn ordinary prompt-quality problems into product-stopping contract
failures. If the first design draft has weak art-direction wording, missing
main-image modules, incomplete prompt text, overlay text in the wrong order,
too few description paragraphs, duplicated/dirty tags, or similar defects that
can be repaired by writing a better prompt, fix the design artifact immediately
and continue. The product should stop only for facts, assets, source boundaries,
SKU binding, image count, high-risk claims, or upload-safety problems.

For every image slot, the final prompt must be self-contained and must include
the exact Russian text lines. If an earlier draft says “text-free”, “generate no
text”, “post overlay later” or similar, rewrite that wording inside the design:
the actual prompt must request a finished Ozon ecommerce image with integrated,
readable Russian sales text in the same image call.

Russian hashtags are generated and normalized by the designer, not by the user.
Output up to 30 unique hashtags using Russian letters only. Never use
brands, Latin text, numbers, underscores or capacity digits in hashtags. If the
model proposes invalid tags, replace them from the product type, usage scenes,
buyer intent and safe generic Ozon search phrases before validation.

Search keywords are different from title and description copy. Think in natural
Russian buyer phrases, but every Ozon-facing search keyword artifact must use
lowercase Russian words joined with underscores only, not spaces or hyphens:
valid `сумка_органайзер_для_барбекю`; invalid `сумка-органайзер для барбекю`.
This underscore rule does not apply to the SEO title, product description or
image text, where normal Russian punctuation remains allowed.

For Ozon description-like attributes such as `Аннотация`, never copy a weak
first-sentence summary or system wording like "current card" or "selected
option format". Write a concise SEO annotation from this product's title, core
buyer phrase, verified usage scenes, visible facts and selling points. It should
be a real Russian product-card summary, not one sentence, and must not invent
brand, certification, warranty, load, functions or accessories.

Visual style quality is handled by this Skill, but three look-and-feel failures
are hard design defects, not mere prompt polish (2026-08-15):

1. **3D-render / CGI look.** Every slot's final image must read as a real
   seller photograph: real material texture, lens depth, environment light,
   soft shadows, believable reflections. A rendered, plastic, glossy-CGI,
   vector or flat-illustration look is forbidden in `art_direction` and in
   every final prompt. State explicitly in each prompt: "фотореалистичная
   продающая фотография, снятая камерой; не 3D-рендер, не CGI, не иллюстрация".
2. **Slogan headline blocks vs real information.** Do not confuse "no poster
   slogans" with "little text". Mature Ozon images carry a lot of Russian text
   — a product/type name, structured part callouts each with a short
   explanation, dimension numbers, comparison tables, spec lines, package
   lists, buyer-benefit sentences — as long as every text block ties to real
   product proof. Forbid only the empty marketing slogan (freedom/comfort with
   no product proof) and decorative text pasted onto a generic background. The
   buyer must be able to read WHAT the product is, WHAT proves the claim, and
   HOW to choose, without opening the card. If a draft has almost no text or
   only 2-4 floating labels, that is a design defect: write the missing product
   name, part callouts, spec or buyer-benefit lines before `image_plan`.
   Every image's Russian text must be BENEFIT-led, not scene-led: the title and
   labels state a buyer benefit ("Мягкое светодиодное освещение", "Сенсорное
   управление", "Быстрая зарядка", "Безопасный для детей"), and a one-line
   explanation says how that benefit helps the buyer. Do not use the scene
   description or an operating instruction as the headline ("НАВИГАЦИЯ В ПОЛЕ
   ЗРЕНИЯ", "Настройте положение") — the benefit goes first, the scene or
   usage note supports it underneath.
3. **Poster composition.** A generic background plus stacked title/benefit
   lines with no visible product proof is forbidden at design time. If a draft
   reads like "background + title + benefits", rewrite it into a concrete
   buyer moment, size/step diagram or structure-proof composition before
   `image_plan`, instead of shipping it to generation.

Other background choice, negative space, text position, chip/card shape and
image taste remain prompt-quality issues fixed by rewriting the slot prompt and
art direction, not contract stops.

## Visual director boundary

The visual-director role lives inside this Skill and acts before image
generation, not after it. Its job is to raise the commercial quality of the
`N+8` image plan by improving buyer sequence, scene choice, camera task,
product scale, material/value signal, Russian hierarchy, palette, typography
and prompt clarity. It may rewrite the design artifact and final prompts before
the image slots are sent to generation. This is where "not poster text-pasting"
is solved: design product proof, buyer context, callouts, dimensions, steps or
SKU choice into the scene before the image model runs.

It must not create a second subjective approval gate after images exist. Once a
slot preserves the correct product, SKU, color, structure, proportions,
accessory quantity and exact readable Russian text, the visual director may
record layout/background/palette/aesthetic concerns as quality notes only.
Those notes can guide the next fresh design or an explicit user-requested
regeneration, but they must not automatically delete accepted images, loop the
same paid generation, or stop the product.

Visual-director hard stops are limited to source or upload safety: wrong
product, wrong SKU, changed color/structure/proportions, invented accessory or
function, missing required image count, unreadable/garbled Russian, forbidden
Chinese/supplier watermark/browser chrome, invalid source reference, or an
Ozon-facing image problem that would make upload fail. Background choice,
negative space, text position, visual style, chip/card shape, image taste and
overall prettiness are prompt-quality issues, not hard blockers.

The visual director must design the sales story before any prompt is written.
The story is not a slogan list and not eight unrelated posters. It must define
one buyer-decision arc across the SKU mains plus eight shared details. Model it
on a mature Ozon infographic set: each of the eight details has ONE role and a
MATCHING infographic layout, never "a photo plus a few floating lines":

1. structure/principle proof — real product + leader lines to every REAL part,
   each part name + one short explanation (the "how it works" diagram);
2. problem → solution — a visible buyer problem answered by this product;
3. trust / quality / spec — only source-confirmed specs, materials, limits or
   safety (mark estimates, write UNKNOWN instead of inventing);
4. SKU comparison — two variants side by side + a labelled comparison table;
5. package / contents — a numbered contents list of confirmed included items;
6. size / parameter — dimension numbers with measurement lines (only real
   product-body numbers; skip this role if no measurements exist);
7. real-use scene — a believable scene with icon-plus-short-label benefits;
8. material / macro detail — a close-up with part callouts.

Do not repeat one benefit three times. If evidence for a role is missing (no
dimensions, no confirmed accessories, no material proof), mark that fact
UNKNOWN in the design and use the next useful role instead of inventing the
missing proof or padding with the same selling point. Every slot's
`composition` must NAME the infographic layout it uses (leader lines, a table,
numbered rows, icon row, dimension lines) so the generator has a concrete
layout to build, not a poster frame.

Write this arc into `visual_system.scene_logic` and reflect it in every slot's
`buyer_question`, `visual_goal`, `composition`, `design_rationale` and final
prompt. A valid plan must read as one product-card story when viewed in order:
the buyer should move from "what is it" to "why choose it", "what proves it",
"which SKU is mine", and "what should I verify before buying". Do not generate
eight independent advertising cards with interchangeable backgrounds and
headlines.

Poster text-pasting is forbidden at the design stage, but large readable
commerce text is allowed when it is part of a real product proof, size diagram,
step diagram, variant explanation or buyer scene. The real failure is a
generic background plus unrelated words. Each final prompt must describe a
finished ecommerce photograph or infographic where the product, scene, proof
element and Russian text belong together. Text may be prominent when it labels
source-backed dimensions, steps, color, variant, structure or buyer benefit,
as long as the product structure stays accurate and visually primary. If a
draft reads like "background + title + benefits" with no product proof or
scene logic, rewrite it into a concrete buyer moment, size/step diagram or
structure-proof composition before `image_plan`.

For SKU main images, do not paste the full listing title, SKU name or model as
a huge headline block, but the main image must still name WHAT the product is
at a glance: a short product-type/name line (e.g. "Автомобильный магнитный
держатель"), the SKU difference (colour/spec), and one core source-backed
buyer-benefit note, then the subtle JLC GLOBAL watermark. The main image should
be a premium real product photograph first: believable camera distance, lens
depth, material texture, soft shadow, clean reflection, environment light and
restrained color grading — never a 3D render, CGI mockup or flat illustration.
Shared detail images carry even richer infographics when they explain
dimensions, steps, structure, SKU choice or a real buyer objection — with part
callouts, numbers, tables and short explanations — but they must stay
photorealistic photographs, never slogan posters.

Acceptable infographic patterns include: a three-second recognition card with a
large real product and core name/spec labels; a dimension/proof card with
expanded vs folded state or measured length/width/height lines; and a step card
showing real operation stages. These are information patterns only, not fixed
backgrounds, colors, layouts or category templates, and they must never import
facts from a reference/example product.

## Mandatory creative sequence

Do not choose a layout before completing these decisions for the current
product:

1. identify the exact product, use situation, visible structure, SKU
   differences and factual boundaries;
2. identify the Russian buyer, search intent, purchase motivation, objections
   and return risks;
3. choose three to six concrete source-backed selling points and rank them;
4. define the buyer-decision sales story for the SKU mains plus eight shared
   details, with each shared detail answering a different buyer question and
   no poster text-pasting;
5. for every slot independently decide the scene, composition, product scale
   and position, background, palette, lighting, typography, icon logic,
   information hierarchy, natural negative space and premium/value signal;
6. map every exact Russian text item to one model-native typography instruction
   with its own normalized box, hierarchy, colors, alignment and background
   treatment for the same final-image call;
7. write the final image prompt from those decisions, then validate the whole
   design before any image is generated.

Record those seven stages, in order, in `decision_trace`: `product_evidence`,
`buyer_analysis`, `selling_point_ranking`, `image_sequence`,
`per_slot_art_direction`, `prompt_completion`, and
`pre_generation_validation`. Each stage must be `completed` with evidence.
Only `compliance_status=PASS` with no violations may leave this Skill.

If the contract reports any missing, reordered or non-compliant stage or slot,
do not patch in a template and do not continue to `image_plan`. Mark the whole
current `ecommerce_design` attempt retryable and regenerate the complete `N+8`
design artifact from product evidence in one pass. Do not replay already
validated upstream collection, category, measurement or pricing work.

The exception is prompt-repairable defects described above: normalize them in
place inside the design artifact and proceed. Do not ask the user and do not
leave the task stuck because a text block, overlay order or image-role wording
was imperfect.

The designer must explain in `design_rationale` why the visual treatment fits
this product and buyer question. Eight shared details must not repeat the same
composition with different copy or background. SKU mains may share one visual
language for consistency, but remain bound to their own SKU facts and images.

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

The final visual must not be a generic photo with empty advertising lines
placed on top. Design a real Ozon ecommerce information hierarchy for this
exact product: the buyer should understand the product type, this SKU's
variant/specification, one source-backed reason to click, and one concrete
proof point or usage cue without opening the card.

For every `sku_main` image:

- the real product remains the dominant visual object;
- the SKU product/photo area is the largest visual area; keep text compact and
  readable instead of letting title panels or text blocks take over the card;
- readable commerce text, headings, size diagrams and step labels are allowed
  when they explain the actual SKU, source-backed dimensions, structure, usage
  or buyer choice; do not ban text size by itself;
- the main risk is insufficient reference grounding. Use the SKU reference to
  lock the current variant, and use current-product main/detail images as
  supporting structure, scene and usage references when available; never let a
  weak single reference make the model invent a different product;
- text and background must form strong contrast on every Russian text module;
  do not rely on a busy scene background to make text readable;
- commercial hierarchy is allowed when it accurately expresses this SKU, but
  oversized headlines, black/yellow bars and text plates must never become
  larger or louder than the product;
- include grounded ecommerce information: product name, SKU/variant label, at
  least one source-backed benefit, and either visible structure, usage cue,
  size/specification, capacity, configuration or other proof relevant to this
  SKU;
- no line may claim fuel type, charging method, capacity, protection,
  certification, warranty, material, load, accessory or function unless it is
  traceable to this product's current workbench collection, analysis or Ozon
  attribute plan.

Product name, SKU/capacity, dimensions, benefits, icons and notices are selected
by the designer for the current buyer question, not copied as mandatory fixed
boxes. Choose palette, light, composition, product scale, typography and
iconography from this product's value proposition while maintaining strong
commercial contrast and a premium, scroll-stopping finish.

Do not use the old module template names as visual instructions. Avoid
`capacity_badge`, `benefit_section`, `icon_chips`, headline bars, fixed badge
rails and repeated card stacks; express the same verified facts as compact
product-specific information integrated into the scene.

Also avoid the weak fallback module: an upper-left vertical accent line plus
large product name, small SKU size and tiny color line. That composition is too
generic unless the text is visually connected to the product through a
dimension/proof/callout/scene relationship. Rewrite it before `image_plan`.

Text modules must be designed like mature Ozon information graphics, not raw
captions. Use two or three strong hierarchy levels at most, clear Russian
grotesk typography, strong contrast, disciplined spacing, and alignment to the
product edge, measurement line, step panel, SKU tile, callout path or natural
negative space. Avoid repeating a lonely upper-left title stack, skinny accent
line, random corner label, tiny specification pile, decorative badge strip, or
large empty text panel. If a text block does not help the buyer understand a
source-backed product proof, remove or rewrite it before generation.

Choose the visual language from the product itself, not from a global reference
style. The same information structure can look different by category:

- a gold home or bathroom rack may use warm interior lighting, marble/tile
  surroundings, black/white/gold typography, elegant line icons and luxury
  catalog composition because the product color/material supports it;
- electronics should usually use clean technical studio or desk context,
  graphite/white/cool accent colors, precise specification callouts, cable or
  interface details, screen/device close-ups and restrained modern typography;
- outdoor, tool, storage, textile or car products should use their own practical
  context, material texture, proof angle and category-appropriate accent color.

Never force the bathroom/gold editorial look, a technology look, or any other
single house style across all products. A reference image is only a quality
example for composition maturity and integrated typography, not a palette,
scene, object, model or layout to copy.

The built-in image model must create the faithful product scene and every exact
Russian text line in one final-image call. `overlay_plan` is a model-native
typography instruction set for that call, not a request for later local
rendering. Never create or expose a text-free intermediate and never call a
post-generation overlay executor. SKU comparison, dimensions, structure and
package contents use the real SKU references in the same final composition;
AI must not turn the product into another model. The result must look like a
real photograph taken by a seller camera (photoreal texture, lens depth,
environment light, soft shadows) — never a 3D render, CGI mockup, vector or
flat illustration. Preserve product type, color,
transparency/material appearance, visible structure, believable overall
proportions, SKU differences and accessory quantity; pixel-for-pixel identity
is not required.

Forbid plain white catalog defaults, generic/repeated templates, fixed
dashboard/card layouts, 3D-render/CGI looks, flat poster illustrations, slogan
headline blocks, Chinese or garbled text, supplier labels/watermarks,
browser controls, incorrect Russian, invented accessories and changed SKU
proportions. If Russian is missing, garbled, misspelled or unreadable, retry
only that image slot once. If per-image art direction or typography instructions
are missing, fail the design step; never let a legacy renderer fill the gap.

## Materialization and handoff

After writing the design, run:

`$CAF_PYTHON_BIN scripts/ozon_ecommerce_designer_contract.py products/<product_id> --materialize`

This validation-only projector writes the compiled listing artifacts
(`copy-ru.json`, title/description/tags and Ozon draft inputs) from the already
completed design. It never invents content, never creates a second creative
brief, and never calls Ozon.

`image_plan` consumes the design and its projected artifacts without adding a
fallback prompt or visual choice. `image_generation` must not reanalyse the
product, rewrite prompts or replace `overlay_plan`. In manual mode, successful
generation and hard QC end at `WAITING_MANUAL_REVIEW` with workbench text
`等待手动上传`; do not open a preview automatically and do not upload. Automatic
mode follows the existing explicit global switch.

The operator no longer confirms images one by one. The current generated
candidate set becomes uploadable only after hard technical QC verifies exactly
one main for every selected SKU plus exactly eight shared details, with no
missing or extra planned image. Legacy contracts that explicitly require manual
image confirmation may still validate `output/accepted-images`; every accepted
file must match the immutable accepted-image manifest for the current
collection and ecommerce-design hash. Regeneration, replacement, deletion or a
design change revokes the affected confirmation.

Never submit inventory fields or call inventory endpoints. During development
or offline acceptance, never call Ozon CREATE, UPDATE or read-status endpoints.
