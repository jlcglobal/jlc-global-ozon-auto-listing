---
name: full-product-pipeline
description: Fully process one queued 1688 product through Ozon while preserving checkpoints and preventing duplicate writes.
---

# Full Product Pipeline

Input: `product_id`.

The collection inbox may contain any number of products, but this skill handles exactly one product per invocation. That product must contain between 1 and 10 selected SKUs. Never interpret the 10-SKU limit as a batch product-count limit.

Read `products/<product_id>/status.json`. Require `task_authorized=true`, which is written only by the user-started batch command. Before every step, enforce the formal production guard: the product must be under `products/<product_id>`, must have `source_kind=workbench_collection`, and every registered input must match the current `product_id + collection_id` manifest and hash. Manual test data, another product, archived data and generated output are hard failures, never fallback sources. Validate every completed artifact before skipping it. Continue through as many pending steps as the current run permits. Persist every output atomically, then update `completed_steps`, `pending_steps`, `failed_step`, `retry_count_by_step`, `api_write_count`, `last_run_at`, and `next_action`.

After `task_authorized=true`, automatically apply every ordinary, low-risk AI
suggestion and record it as auto-applied; never create a suggestion-confirmation
pause or an operator question. Truth and platform safety gates are not AI
suggestions: unsupported brand, certification, load, safety, customs or product
identity claims remain unknown or fail visibly instead of being fabricated.

Formal production IDs are limited to `P000001` through `P899999`. The
`P900000-P999999` range is permanently reserved for tests and audit samples;
those identities must never enter the workbench, a formal batch, an upload
draft or an Ozon queue. At batch creation freeze the current collection ID and
the SHA-256 of `source-manifest.json` in both the batch entry and product
status. Revalidate that exact frozen binding before every resume, design,
generation, confirmation and upload. A rewritten source plus rewritten
manifest is a new collection version, not an in-place repair.

Conversation attachments and hand-entered Codex test fixtures may exist only under `test-data/manual-input/<test_case_id>` with outputs under `test-data/manual-output/<test_case_id>`. They cannot enter the collection inbox, `products/`, upload drafts or Ozon queues. Never borrow facts or images from another product, similar filename/SKU/capacity, or an older archived product. Cross-product analysis cache reuse is forbidden. A narrow current-product exception is allowed only when `input/manual-confirmation.json -> sku_image_reference_overrides` explicitly says `decision=user_confirmed_same_appearance`, `scope=reference_image_only`, and `must_preserve_target_sku_facts=true`; then the source SKU image is visual reference only, while target SKU capacity, dimensions, price and Russian text remain authoritative.

When the batch runner asks for one step, execute only the named `next_action`. After validating that step's real output, run `python3 scripts/pipeline_checkpoint.py complete <product_id> <step>`. Never mark a step complete merely because a command exited successfully. The batch runner owns concurrency and retries.

Phase A must finish before any image planning or generation: source validation; fact analysis; live Ozon category/type and required attributes; official aspect-rule variant decision; measurement extraction or labelled estimation; RETS cost calculation and price; stable offer-id existence check; upload-feasibility precheck.

The required Ozon model-name field is a low-risk internal grouping value. When
the current product has no existing value, generate one random-looking 12-digit
numeric model name from the immutable product/collection identity. Persist it
once and reuse the exact same value for every selected SKU, separate card,
target store, retry and resume of that product. Different products get
different values. Never append a SKU ID or store ID, and never overwrite an
existing non-unknown model value during category refresh.

Fact analysis must treat explicit seller text in the 1688 title, structured attributes, selected SKU values, and readable detail-image specifications as source facts. For example, a title that explicitly says `硅胶` supports material `Силикон`; it must not remain `unknown`. Translate only through the project's verified fact dictionary and retain the exact source reference. Brand defaults to `Нет бренда`, origin defaults to China, and quantity defaults to 1 under the project business rule. Visual analysis has higher priority than estimation: if clear current-product main, SKU, or detail images show product-body dimensions, capacity, weight, or SKU specifications, record them in `product-analysis.facts.dimensions` or `product-analysis.facts.weight` with `status=source_image_text`, numeric values, unit, evidence image path, confidence, and the matching SKU/variant label when available. SKU-image or SKU-text matches are SKU-specific; main/detail-image facts without a variant match are common product facts only and must not be copied across visibly different variants. Product and package weight/dimensions may be estimated only by the measurement module when structured text and readable current images are absent, and must retain `estimated` and confidence metadata. An estimated product dimension may appear only in the size image and must be labelled `Примерные размеры`; package dimensions must never be presented as product dimensions. Package weight and every package dimension must be strictly greater than the corresponding product measurement. Never estimate material, certification, load capacity, functions, or accessories.

Fact analysis should also record low-risk visible physical attributes that are clear in real 1688 source images, such as basic shape or an integral lid, under `product-analysis.inferences` with the exact image reference and confidence. These visible inferences may fill optional Ozon dictionary attributes as `AI_estimated`; they must never be promoted into material, certification, load-capacity, function, accessory, or precise-capacity claims.

When a selected seller SKU explicitly uses a nominal storage-capacity label such as `20斤装` or `40斤装`, and the chosen Ozon category exposes an official capacity attribute with `is_aspect=true`, treat the SKU difference as capacity rather than as an unrelated seller specification. Convert the nominal label only through the checked-in local conversion rule and the cached Ozon dictionary, retain the original SKU text, and mark the converted value as `estimated` with confidence and a conversion note. The current approved rice/grain-container rule is `1斤≈625毫升`, so `20斤装≈12500毫升` and `40斤装≈25000毫升`. This rule is for variant grouping and platform attributes; it must not be rewritten as a precise verified marketing claim. If the category has no matching official aspect field or the local dictionary has no matching value, leave the mapping unresolved instead of inventing one.

Phase A may pass category matching only when the live Ozon category match is `api_confirmed`, confidence is at least 0.90, the category/type pair is an enabled leaf in the same fetched tree, and the attribute schema has the same pair. A lower-confidence or cross-product mismatch is a product-level hard blocker before image generation. Existing offers must never be moved across category/type through an automatic UPDATE; record the conflict and do not write.

Phase B runs only after Phase A passes and uses one analysis pass in this exact order: `$ozon-ecommerce-designer`; materialize Russian title, short title, complete description, selling points and keywords; marketplace draft; up to 30 unique Russian-only `#` search tags (omit the optional tag attribute when no valid tag remains); category-bound required attributes, SKU names, price and variant mapping; all per-slot art direction, overlay plans and image prompts; one SKU-bound main image for every selected SKU; exactly eight shared detail images; image QC; automatically establish the fixed-TTL public image channel and submit to the batch's selected Ozon stores. The universal image contract is `N selected SKUs + 8 shared details`, where N is 1 through 10. Never generate a second detail/disclaimer set for another SKU in the same internal product group.

All text, price, SKU and category-bound attributes must be materialized before `image_plan`. `image_plan` must read those final artifacts and produce every slot prompt before `image_generation` starts. Do not rerun product analysis, category matching, copy generation or field completion inside image generation. There is no separate "final upload check" stage after image QC. The upload action itself may refresh image references/Rich Content and run write-safety gates immediately before sending to Ozon; it must not reanalyse the product or rewrite facts.

During `russian_copy`, do not invoke external keyword research, Seerfar,
Yandex, browser-visible keyword pages, or any market-intelligence queue. The
production chain uses only the unified ecommerce design, current product facts,
the live Ozon category/type metadata, and local deterministic Russian SEO
helpers. Materialize `output/keyword-research-ru.json` only as a compatibility
artifact projected from the same ecommerce design; it must not wait for or
consume external search-volume data. Hashtags must be Russian-letter-only: no
brands, numbers, underscores, Latin letters or capacity digits; do not invent
filler just to reach 30.

For `image_generation`, invoke the project `$image-generator` skill. Run the source-image preflight once before generation. Exact comparison and dimension images use deterministic real-image composition; main and lifestyle images may use built-in reference-image editing. Do not invoke external branding, marketing, photography, or image-generation Skills. Low-resolution thumbnails must never be enlarged or auto-cut.

Every prompt must describe one buyer-decision job using the final listing title, verified attributes, selected SKU difference, source-backed facts and product-specific visual direction. The ecommerce designer must decide each slot's scene, composition, product scale/position, palette, lighting, typography, icon logic, negative space and exact typography coordinates before generation. Each slot uses one built-in image-model call that returns the final scene, faithful product and all exact Russian text together; a text-free intermediate or later overlay executor is forbidden. The generator cannot introduce a default header, badge, benefit rail, palette or card layout. Natural negative space is allowed. Blank rounded rectangles, empty text boxes, placeholder cards, bordered empty panels and decorative empty frames are quality warnings only unless they hide the product, make required Russian text unreadable, or cause Ozon image upload rejection. Do not use a reusable category template; advertising-style hierarchy is allowed when it remains source-grounded and useful.

If the seller entered an exact storytelling preference through the current product's workbench collection, materialize its commercial roles in `input/operator-guidance.json -> image_detail_roles` before building the creative brief and register it in the same collection manifest. Conversation attachments and test fixtures are never operator guidance for a formal product. The override must contain exactly eight shared-detail roles and only current-product registered references. Treat it as a storytelling contract, never as permission to copy another product's facts or to bypass truthfulness checks.

The image generator processes independent slots in true bounded waves of at
most three: the batch runner starts up to three separate one-slot Codex workers
at the same time, waits for all workers in that wave, and only then starts the
next wave. A single Codex process iterating over a three-item list is not
concurrency. Each child writes an isolated slot receipt; the parent alone
merges the hard gate and generates/verifies `image-qc-report.json`. Only failed
slots may receive targeted retries up to the configured image-QC revision
limit; every passed image and hash remain untouched. All SKU-main waves finish
before shared-detail waves. The batch runner, not an image child, advances the
separate `image_qc` checkpoint; do not launch another Codex task merely to
recreate the same QC report.

Never regenerate QC-passed images or repeat a successful Ozon write. When image
QC fails, revise only the failed slot prompts and regenerate only those failed
slots using `output/image-regeneration-request.json`; preserve every passed
image. Use `<product_id>-<source_sku_id>` offer IDs. Every variant main must use
only that SKU's explicitly associated image or an image generated from that
exact reference and passed by QC; never assign another color, size, or quantity
variant's main by array order. Shared details and disclaimers must avoid claims
or visuals that apply to only one variant. Unknown optional data is a warning;
estimates must be labelled and must not become selling claims.

AI candidates are written only below `output/generated-images`; rejected or failed images go to `output/rejected-generation`; optional explicit user acceptance creates an audit copy below `output/accepted-images`. Never write generated output into `input`, never feed any output image back as a product reference, and never overwrite a collected source image. After hard image QC passes, the generated candidate set is the upload candidate set. A user-authorized production batch does not stop for a second upload click: it automatically keeps the image channel alive for its fixed 24-hour TTL and submits only the selected stores.

Every optional explicit acceptance must also update
`output/accepted-images/manifest.json`. Each slot record freezes product ID,
collection ID, SKU/detail role, candidate and accepted paths, accepted file
SHA-256, confirming operator/time and current ecommerce-design hash. Contracts
that explicitly require manual image confirmation must validate that manifest,
current collection/design versions, all hashes and the exact `N+8` slot set.
Current auto image-review contracts validate the generated candidate paths and
current hard QC instead. Replacement, regeneration, deletion or design change
revokes the affected acceptance; the manifest file itself is never counted as
an image.

Never include `stock`, `warehouse_id`, inventory availability, or an inventory request in the Ozon payload. Never call an inventory endpoint. The seller adds inventory later in Ozon.

Set `NEEDS_ATTENTION` only for real problems that require the product to resume from a failed step. A missing intermediate file is not a stop reason: generate it with the corresponding local module or Codex built-in capability. On an Ozon error, save the endpoint, HTTP status, task, offer results, attribute IDs, fields and raw response. End only at `WAITING_MANUAL_REVIEW`, `HANDED_OFF_TO_OZON`, `UPLOADED`, `OZON_MODERATION`, `ACTIVE`, or `NEEDS_ATTENTION`.
