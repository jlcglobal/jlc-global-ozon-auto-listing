---
name: full-product-pipeline
description: Fully process one queued 1688 product through Ozon while preserving checkpoints and preventing duplicate writes.
---

# Full Product Pipeline

Input: `product_id`.

The collection inbox may contain any number of products, but this skill handles exactly one product per invocation. That product must contain between 1 and 10 selected SKUs. Never interpret the 10-SKU limit as a batch product-count limit.

Read `products/<product_id>/status.json`. Require `task_authorized=true`, which is written only by the user-started batch command. Before every step, enforce the formal production guard: the product must be under `products/<product_id>`, must have `source_kind=workbench_collection`, and every registered input must match the current `product_id + collection_id` manifest and hash. Manual test data, another product, archived data and generated output are hard failures, never fallback sources. Validate every completed artifact before skipping it. Continue through as many pending steps as the current run permits. Persist every output atomically, then update `completed_steps`, `pending_steps`, `failed_step`, `retry_count_by_step`, `api_write_count`, `last_run_at`, and `next_action`.

Formal production IDs are limited to `P000001` through `P899999`. The
`P900000-P999999` range is permanently reserved for tests and audit samples;
those identities must never enter the workbench, a formal batch, an upload
draft or an Ozon queue. At batch creation freeze the current collection ID and
the SHA-256 of `source-manifest.json` in both the batch entry and product
status. Revalidate that exact frozen binding before every resume, design,
generation, confirmation and upload. A rewritten source plus rewritten
manifest is a new collection version, not an in-place repair.

Conversation attachments and hand-entered Codex test fixtures may exist only under `test-data/manual-input/<test_case_id>` with outputs under `test-data/manual-output/<test_case_id>`. They cannot enter the collection inbox, `products/`, upload drafts or Ozon queues. Never borrow facts or images from another product, similar filename/SKU/capacity, or an older archived product. Cross-product analysis cache reuse is forbidden.

When the batch runner asks for one step, execute only the named `next_action`. After validating that step's real output, run `python3 scripts/pipeline_checkpoint.py complete <product_id> <step>`. Never mark a step complete merely because a command exited successfully. The batch runner owns concurrency and retries.

Phase A must finish before any image planning or generation: source validation; fact analysis; live Ozon category/type and required attributes; official aspect-rule variant decision; measurement extraction or labelled estimation; RETS cost calculation and price; stable offer-id existence check; upload-feasibility precheck.

Fact analysis must treat explicit seller text in the 1688 title, structured attributes, selected SKU values, and readable detail-image specifications as source facts. For example, a title that explicitly says `硅胶` supports material `Силикон`; it must not remain `unknown`. Translate only through the project's verified fact dictionary and retain the exact source reference. Brand defaults to `Нет бренда`, origin defaults to China, and quantity defaults to 1 under the project business rule. Product and package weight/dimensions may be estimated only by the measurement module when absent and must retain `estimated` and confidence metadata. An estimated product dimension may appear only in the size image and must be labelled `Примерные размеры`; package dimensions must never be presented as product dimensions. Package weight and every package dimension must be strictly greater than the corresponding product measurement. Never estimate material, certification, load capacity, functions, or accessories.

Fact analysis should also record low-risk visible physical attributes that are clear in real 1688 source images, such as basic shape or an integral lid, under `product-analysis.inferences` with the exact image reference and confidence. These visible inferences may fill optional Ozon dictionary attributes as `AI_estimated`; they must never be promoted into material, certification, load-capacity, function, accessory, or precise-capacity claims.

When a selected seller SKU explicitly uses a nominal storage-capacity label such as `20斤装` or `40斤装`, and the chosen Ozon category exposes an official capacity attribute with `is_aspect=true`, treat the SKU difference as capacity rather than as an unrelated seller specification. Convert the nominal label only through the checked-in local conversion rule and the cached Ozon dictionary, retain the original SKU text, and mark the converted value as `estimated` with confidence and a conversion note. The current approved rice/grain-container rule is `1斤≈625毫升`, so `20斤装≈12500毫升` and `40斤装≈25000毫升`. This rule is for variant grouping and platform attributes; it must not be rewritten as a precise verified marketing claim. If the category has no matching official aspect field or the local dictionary has no matching value, leave the mapping unresolved instead of inventing one.

Phase A may pass category matching only when the live Ozon category match is `api_confirmed`, confidence is at least 0.90, the category/type pair is an enabled leaf in the same fetched tree, and the attribute schema has the same pair. A lower-confidence or cross-product mismatch is a product-level hard blocker before image generation. Existing offers must never be moved across category/type through an automatic UPDATE; record the conflict and do not write.

Phase B runs only after Phase A passes and uses one analysis pass in this exact order: `$ozon-ecommerce-designer`; materialize Russian title, short title, complete description, selling points and keywords; marketplace draft; exactly 30 unique `#` tags; category-bound required attributes, SKU names, price and variant mapping; all per-slot art direction, overlay plans and image prompts; one SKU-bound main image for every selected SKU; exactly eight shared detail images; image QC; final upload check; production CREATE/UPDATE. The universal image contract is `N selected SKUs + 8 shared details`, where N is 1 through 10. Never generate a second detail/disclaimer set for another SKU in the same internal product group.

All text, price, SKU and category-bound attributes must be materialized before `image_plan`. `image_plan` must read those final artifacts and produce every slot prompt before `image_generation` starts. Do not rerun product analysis, category matching, copy generation or field completion inside image generation. After image QC, `final_upload_check` may only refresh image references/Rich Content and validate the completed draft; it must not reanalyse the product or rewrite facts.

During `russian_copy`, invoke `$keyword-research` in read-only advisory mode for the Ozon Russia market. It must not ask the user questions, write to global skill memory, or fabricate search volume or difficulty. Use only source-backed product facts, the live category name, and verifiable Ozon public-search/listing terms. Save the result to `output/keyword-research-ru.json`; every accepted keyword needs traceable evidence, unavailable metrics remain `unknown`, and excluded unverified claims must stay excluded. Use the accepted terms to refine the Russian title, marketplace-content input and exactly 30 unique hashtags, each no longer than 30 characters.

For `image_generation`, invoke the project `$image-generator` skill. Run the source-image preflight once before generation. Exact comparison and dimension images use deterministic real-image composition; main and lifestyle images may use built-in reference-image editing. Do not invoke external branding, marketing, photography, or image-generation Skills. Low-resolution thumbnails must never be enlarged or auto-cut.

Every prompt must describe one buyer-decision job using the final listing title, verified attributes, selected SKU difference, source-backed facts and product-specific visual direction. The ecommerce designer must decide each slot's scene, composition, product scale/position, palette, lighting, typography, icon logic, negative space and exact typography coordinates before generation. Each slot uses one built-in image-model call that returns the final scene, faithful product and all exact Russian text together; a text-free intermediate or later overlay executor is forbidden. The generator cannot introduce a default header, badge, benefit rail, palette or card layout. Natural negative space is allowed; blank rounded rectangles, empty text boxes, placeholder cards, bordered empty panels and decorative empty frames are hard failures. Do not use a generic poster or reusable category template.

If the seller entered an exact storytelling preference through the current product's workbench collection, materialize its commercial roles in `input/operator-guidance.json -> image_detail_roles` before building the creative brief and register it in the same collection manifest. Conversation attachments and test fixtures are never operator guidance for a formal product. The override must contain exactly eight shared-detail roles and only current-product registered references. Treat it as a storytelling contract, never as permission to copy another product's facts or to bypass truthfulness checks.

The image generator processes independent slots in bounded waves of at most three. It must generate and locally verify `image-qc-report.json` in the same invocation. The batch runner, not the agent, advances the separate `image_qc` checkpoint after independent validation; do not launch a second Codex task merely to recreate the same QC report.

Never regenerate QC-passed images or repeat a successful Ozon write. When image QC fails, regenerate only the failed slots once using `output/image-regeneration-request.json`; preserve every passed image. Use `<product_id>-<source_sku_id>` offer IDs. Every variant main must use only that SKU's explicitly associated image or an image generated from that exact reference and passed by QC; never assign another color, size, or quantity variant's main by array order. Shared details and disclaimers must avoid claims or visuals that apply to only one variant. Unknown optional data is a warning; estimates must be labelled and must not become selling claims.

AI candidates are written only below `output/generated-images`; rejected or failed images go to `output/rejected-generation`; explicit user acceptance creates the final copy below `output/accepted-images`. Never write generated output into `input`, never feed any output image back as a product reference, and never overwrite a collected source image. In manual mode, an empty or incomplete accepted tree blocks upload even when an older QC/PASS file exists.

Every explicit acceptance must also update
`output/accepted-images/manifest.json`. Each slot record freezes product ID,
collection ID, SKU/detail role, candidate and accepted paths, accepted file
SHA-256, confirming operator/time and current ecommerce-design hash. Upload
must validate the manifest, current collection/design versions, all hashes and
the exact `N+8` slot set. Replacement, regeneration, deletion or design change
revokes the affected acceptance; the manifest file itself is never counted as
an image.

Never include `stock`, `warehouse_id`, inventory availability, or an inventory request in the Ozon payload. Never call an inventory endpoint. The seller adds inventory later in Ozon.

Set `FAILED_HARD_BLOCKER` only for the project-defined hard failures. A missing intermediate file is not a blocker: generate it with the corresponding local module or Codex built-in capability. On an Ozon error, save the endpoint, HTTP status, task, offer results, attribute IDs, fields and raw response. End only at `UPLOADED`, `OZON_MODERATION`, `ACTIVE`, or `FAILED_HARD_BLOCKER`.
