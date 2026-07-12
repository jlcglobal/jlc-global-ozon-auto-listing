---
name: full-product-pipeline
description: Fully process one queued 1688 product through Ozon while preserving checkpoints and preventing duplicate writes.
---

# Full Product Pipeline

Input: `product_id`.

The collection inbox may contain any number of products, but this skill handles exactly one product per invocation. That product must contain between 1 and 10 selected SKUs. Never interpret the 10-SKU limit as a batch product-count limit.

Read `products/<product_id>/status.json`. Require `task_authorized=true`, which is written only by the user-started batch command. Validate every completed artifact before skipping it. Continue through as many pending steps as the current run permits. Persist every output atomically, then update `completed_steps`, `pending_steps`, `failed_step`, `retry_count_by_step`, `api_write_count`, `last_run_at`, and `next_action`.

When the batch runner asks for one step, execute only the named `next_action`. After validating that step's real output, run `python3 scripts/pipeline_checkpoint.py complete <product_id> <step>`. Never mark a step complete merely because a command exited successfully. The batch runner owns concurrency and retries.

Phase A must finish before any image planning or generation: source validation; fact analysis; live Ozon category/type and required attributes; official aspect-rule variant decision; measurement extraction or labelled estimation; RETS cost calculation and price; stable offer-id existence check; upload-feasibility precheck.

Fact analysis must treat explicit seller text in the 1688 title, structured attributes, selected SKU values, and readable detail-image specifications as source facts. For example, a title that explicitly says `硅胶` supports material `Силикон`; it must not remain `unknown`. Translate only through the project's verified fact dictionary and retain the exact source reference. Brand defaults to `Нет бренда`, origin defaults to China, and quantity defaults to 1 under the project business rule. Product and package weight/dimensions may be estimated only by the measurement module when absent and must retain `estimated` and confidence metadata. An estimated product dimension may appear only in the size image and must be labelled `Примерные размеры`; package dimensions must never be presented as product dimensions. Package weight and every package dimension must be strictly greater than the corresponding product measurement. Never estimate material, certification, load capacity, functions, or accessories.

Fact analysis should also record low-risk visible physical attributes that are clear in real 1688 source images, such as basic shape or an integral lid, under `product-analysis.inferences` with the exact image reference and confidence. These visible inferences may fill optional Ozon dictionary attributes as `AI_estimated`; they must never be promoted into material, certification, load-capacity, function, accessory, or precise-capacity claims.

When a selected seller SKU explicitly uses a nominal storage-capacity label such as `20斤装` or `40斤装`, and the chosen Ozon category exposes an official capacity attribute with `is_aspect=true`, treat the SKU difference as capacity rather than as an unrelated seller specification. Convert the nominal label only through the checked-in local conversion rule and the cached Ozon dictionary, retain the original SKU text, and mark the converted value as `estimated` with confidence and a conversion note. The current approved rice/grain-container rule is `1斤≈625毫升`, so `20斤装≈12500毫升` and `40斤装≈25000毫升`. This rule is for variant grouping and platform attributes; it must not be rewritten as a precise verified marketing claim. If the category has no matching official aspect field or the local dictionary has no matching value, leave the mapping unresolved instead of inventing one.

Phase A may pass category matching only when the live Ozon category match is `api_confirmed`, confidence is at least 0.90, the category/type pair is an enabled leaf in the same fetched tree, and the attribute schema has the same pair. A lower-confidence or cross-product mismatch is a product-level hard blocker before image generation. Existing offers must never be moved across category/type through an automatic UPDATE; record the conflict and do not write.

Phase B runs only after Phase A passes: product positioning; Russian title and copy; style selection; image plan; one shared detail set (6 details plus 1 disclaimer) and one SKU-specific main image for each supported color, size, or quantity/configuration variant; image QC; marketplace draft; exactly 30 unique `#` tags; Rich Content JSON and variant mapping; final check; production CREATE/UPDATE; import, moderation and grouping polling. Never generate a second detail/disclaimer set for another SKU in the same internal product group.

During `russian_copy`, invoke `$keyword-research` in read-only advisory mode for the Ozon Russia market. It must not ask the user questions, write to global skill memory, or fabricate search volume or difficulty. Use only source-backed product facts, the live category name, and verifiable Ozon public-search/listing terms. Save the result to `output/keyword-research-ru.json`; every accepted keyword needs traceable evidence, unavailable metrics remain `unknown`, and excluded unverified claims must stay excluded. Use the accepted terms to refine the Russian title, marketplace-content input and exactly 30 unique hashtags, each no longer than 30 characters.

For `image_generation`, invoke the project `$image-generator` skill. Run the source-image preflight once before generation. Exact comparison and dimension images use deterministic real-image composition; main and lifestyle images may use built-in reference-image editing. Do not invoke external branding, marketing, photography, or image-generation Skills. Low-resolution thumbnails must never be enlarged or auto-cut.

The image generator processes independent slots in bounded waves of at most three. It must generate and locally verify `image-qc-report.json` in the same invocation. The batch runner, not the agent, advances the separate `image_qc` checkpoint after independent validation; do not launch a second Codex task merely to recreate the same QC report.

Never regenerate QC-passed images or repeat a successful Ozon write. When image QC fails, regenerate only the failed slots once using `output/image-regeneration-request.json`; preserve every passed image. Use `<product_id>-<source_sku_id>` offer IDs. Every variant main must use only that SKU's explicitly associated image or an image generated from that exact reference and passed by QC; never assign another color, size, or quantity variant's main by array order. Shared details and disclaimers must avoid claims or visuals that apply to only one variant. Unknown optional data is a warning; estimates must be labelled and must not become selling claims.

Never include `stock`, `warehouse_id`, inventory availability, or an inventory request in the Ozon payload. Never call an inventory endpoint. The seller adds inventory later in Ozon.

Set `FAILED_HARD_BLOCKER` only for the project-defined hard failures. A missing intermediate file is not a blocker: generate it with the corresponding local module or Codex built-in capability. On an Ozon error, save the endpoint, HTTP status, task, offer results, attribute IDs, fields and raw response. End only at `UPLOADED`, `OZON_MODERATION`, `ACTIVE`, or `FAILED_HARD_BLOCKER`.
