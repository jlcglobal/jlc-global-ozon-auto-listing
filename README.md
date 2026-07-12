# crossborder-ai-factory

Stage 1 defines the local data contract for a single 1688-to-Ozon product workflow. It does not collect 1688 pages, generate images, call Ozon Seller API, or call any OpenAI API.

## Stage 1 Scope

- Product directory structure
- JSON Schema files
- Unified product validator
- Workflow status machine checks
- One structural example product directory
- Automated tests

## Product Directory

```text
products/{product_id}/
  input/
    source.json
    main-images/
    sku-images/
    detail-images/
  output/
    product-analysis.json
    product-positioning.json
    copy-ru.json
    title-ru.json
    description-ru.json
    keywords-ru.json
    attributes.json
    ozon-category.json
    ozon-attributes.json
    ozon-category-tree.json
    ozon-category-attributes.json
    ozon-preflight.json
    style-profile.json
    image-plan.json
    image-qc-report.json
    generated-images/
      main/
      detail/
      disclaimer/
    images/
      main/
      detail/
    qc-report.json
    ozon-draft.json
  status.json
  logs/
```

## File Responsibilities

- `input/source.json`: only real 1688 collected data and original facts. Do not write Codex analysis or AI guesses here.
- `output/product-analysis.json`: Codex product analysis, selling points, risks, missing information, and recommendations.
- `output/product-positioning.json`: target customer, purchase motivation, pain points, sales angle, emotional trigger, evidence, and price-position recommendation.
- `output/copy-ru.json`: Russian title, keywords, selling points, and description.
- `output/title-ru.json`: Ozon-oriented Russian title, core keyword, evidence, and excluded claims.
- `output/description-ru.json`: localized value, usage, advantage, method, and notice sections with evidence.
- `output/keywords-ru.json`: primary and secondary Russian keywords traced to product type, scenes, motivation, or SKU data.
- `output/attributes.json`: proposed category and semantic attribute mapping; unknown Ozon IDs and facts remain `unknown`.
- `output/ozon-category.json`: offline semantic category recommendation, confidence, alternatives, evidence, and unresolved live Ozon category ID.
- `output/ozon-attributes.json`: category-specific candidate attributes, reliable mappings, missing facts, and unknown fields awaiting live Ozon metadata.
- `output/ozon-category-tree.json`: normalized read-only snapshot of the live Ozon category tree.
- `output/ozon-category-attributes.json`: live attributes and allowed values for the selected Ozon category.
- `output/ozon-preflight.json`: read-only category/attribute validation; it never enables upload.
- `output/style-profile.json`: selected ecommerce visual family, buyer mode, evidence, generator constraints, and fixed image-set structure.
- `output/image-plan.json`: main/detail image plans, real source image references, and product features that must not change.
- `output/image-qc-report.json`: weighted Ozon image QC score, decision, issues, suggestions, and regeneration requirement.
- `output/qc-report.json`: copy, image, SKU, and data truthfulness checks.
- `output/ozon-draft.json`: normalized Ozon Seller API draft.
- `status.json`: product status, current step, retry counts, failure reasons, and Ozon returned IDs.

## API Boundary

Allowed in later stages:

- Ozon Seller API
- `OZON_CLIENT_ID`
- `OZON_API_KEY`

Forbidden in project code:

- `OPENAI_API_KEY`
- OpenAI API clients
- OpenAI text API calls
- OpenAI image API calls
- Paid third-party AI model or image APIs

Codex analysis and image generation must be performed directly through the current Codex session, not through project code.

## Pricing Engine

`pricing-engine/` runs before Ozon draft generation and writes:

- `output/cost-analysis.json`
- `output/pricing-result.json`
- `output/profit-analysis.json`

Only the `RETS` worksheet in `pricing-engine/shipping_rules.xlsx` is read. Shipping chooses the lowest-cost eligible RETS route after weight, volumetric weight, item value and dimension checks.

Default pricing assumptions are stored in `pricing-engine/pricing_rules.json`: 50% profit markup, unknown-category commission 18% (bounded to 12%-20%), logistics commission 2%, acquiring fee 2%, withdrawal fee 1.2%, and packing fee CNY 2.

Generate pricing outputs without any network or Seller API call:

```bash
python3 pricing-engine/cli.py products/P000004 --write
```

Apply prices to an existing, not-yet-uploaded Ozon draft:

```bash
python3 pricing-engine/cli.py products/P000005 --write --update-draft
```

## Status Machine

Supported states:

```text
COLLECTED
QUEUED
PROCESSING
CATEGORY_MATCHED
CONTENT_GENERATED
IMAGES_GENERATED
PRICED
OZON_READY
UPLOADING
UPLOADED
OZON_MODERATION
ACTIVE
FAILED_HARD_BLOCKER
```

Rules:

- Products remain in `COLLECTED` until the user clicks `运行任务`.
- That one click authorizes the current collection-inbox snapshot; no per-product or intermediate review state is used.
- The inbox can contain any number of products. Each product must contain 1-10 selected SKUs.
- `FAILED_HARD_BLOCKER` must include a failed step or Ozon error with a concrete reason; the batch continues with the next product.
- Each step records `retry_count` and `retryable`.
- Completed files and successful API writes are checkpointed and are not repeated after restart.

## Manual Verification

Install validation dependencies:

```bash
python3 -m pip install -r requirements.txt
```

Validate the example product:

```bash
python3 scripts/validate_product.py products/P000001 --check-upload-gate
```

Expected result:

```text
PASS products/P000001
upload_allowed=false
```

Run automated tests:

```bash
python3 -m unittest discover -s tests
```

## Stage 3 Style Selection

The style layer is local and deterministic. It does not call an external model API.

Stage 3 processing order is:

```text
source.json -> product-analysis.json -> product-positioning.json -> style-profile.json -> image-plan.json
```

```bash
python3 scripts/style_selector.py products/P000004
python3 scripts/image_planner.py products/P000004 --write
python3 scripts/image_generator_contract.py products/P000004 main-001
```

`image_generator_contract.py` only creates a constrained prompt packet for the current Codex session. It never calls an image API. Image generation is blocked when the plan does not match `style-profile.json`, the image type is outside the selected structure, source images are missing, or required facts are unknown.

## Stage 3.4 Image Quality Control

Image QC uses deterministic local scoring after Codex inspects the generated images in the current session. Project code checks image files, 3:4 ratio, resolution, score integrity, thresholds, and critical-failure overrides. It does not call a model API.

```bash
python3 scripts/image_qc.py products/P000004 \
  --assessment products/P000004/logs/image-qc-assessment.json \
  --write
python3 scripts/image_qc.py products/P000004 --verify-report
```

Decision rules:

- `90-100`: `pass`, recommended for human review only.
- `75-89`: `revise`, allow manual edits and rerun QC.
- `0-74`: `reject`, replan or regenerate images.
- A critical product-identity, structure, accessory, false-parameter, false-certification, unreadable-file, or aspect-ratio failure always forces `reject`.

## Stage 3.5 Marketplace Content

Marketplace Content Generator converts approved product facts and positioning into separate Russian content files and a blocked Ozon draft. Codex supplies localized language through the current session; project code validates evidence boundaries, preserves source SKU data, assembles files, and never calls a text API.

```bash
python3 scripts/marketplace_content_generator.py products/P000004 \
  --content-input products/P000004/logs/marketplace-content-input.json \
  --write
python3 scripts/marketplace_content_generator.py products/P000004 --verify
```

Stage 3.5 rules:

- Core search terms must appear near the start of the Russian title.
- Every keyword must trace to product type, usage scene, purchase motivation, or selected SKU data.
- Source SKU IDs, Chinese names, option values, purchase prices, price sources, and image associations are copied without alteration.
- CNY purchase price is never used as RUB sale price.
- Ozon category IDs, attribute IDs, RUB price, stock, and warehouse remain unknown until separately mapped and confirmed.
- Every generated draft has `upload_allowed=false` and failed preflight.

## Stage 3.6 Ozon Metadata Matching

Stage 3.6 performs deterministic offline semantic matching. It reads the collected facts, product analysis, product positioning, and local Ozon rules. It does not call Ozon Seller API and therefore never invents live category or attribute IDs.

```bash
python3 scripts/ozon_metadata_matcher.py products/P000004 --write
python3 scripts/ozon_metadata_matcher.py products/P000004 --verify
```

Stage 3.6 rules for legacy products collected before the category-lock flow:

- Category matching combines product type, category facts, usage scenarios, purchase motivation, and image-derived evidence already recorded in product analysis.
- The recommended Russian category name is semantic guidance only; `category_id` remains `unknown` until live Ozon metadata is queried in a later approved stage.
- Candidate required attributes are explicitly marked as needing Ozon confirmation.
- Unknown material, dimensions, weight, load, certification, brand, function, and package quantity are never inferred.
- The draft remains blocked with `upload_allowed=false` and failed preflight.

For newly collected products, Stage 3.6 must use `input/category-selection.json` and the exact user-selected `category_id`/`type_id`. It must not guess, replace, or rematch the category at runtime.

## Ozon Upload

The uploader creates or updates each queued Ozon product and polls its import task. It never submits stock or warehouse fields and never calls an inventory endpoint.

```bash
python3 ozon-uploader/cli.py products/P000004 --prepare
python3 ozon-uploader/cli.py products/P000004 --shop zhonglian1 --execute
```

The execute command is blocked unless the product belongs to a user-started batch, all required marketplace fields are valid, temporary HTTPS image URLs are available, and the final upload preflight passes.

## Stage 2 Collector

Start the local ingest service:

```bash
python3 -m uvicorn app:app --app-dir collector/local-ingest --host 127.0.0.1 --port 8765
```

Check service health:

```bash
python3 - <<'PY'
import urllib.request
print(urllib.request.urlopen('http://127.0.0.1:8765/health', timeout=5).read().decode())
PY
```

Load the Edge or Chrome extension:

1. Open `edge://extensions` or `chrome://extensions`.
2. Turn on developer mode.
3. Click `Load unpacked`.
4. Select `collector/edge-extension`.
5. Open a real `https://detail.1688.com/offer/...html` product page.
6. Click the extension icon.
7. Confirm the popup shows title preview, main image count, SKU count, and detail image count.
8. Click `采集当前商品`.
9. Select 1-10 SKUs.
10. Search the bundled local Chinese Ozon category cache by Chinese keyword, Russian text, category path, `category_id`, or `type_id`, or browse the Chinese tree level by level.
11. Choose the final Ozon category and wait for its official required attributes, dictionary values, and `is_aspect` rules to load.
12. Confirm collection. Collection is blocked until the final category and rule snapshot are present.

The full tree, including every final leaf type, is displayed in Chinese. Russian names remain in the versioned local cache for search and traceability but are not the tree's primary UI labels. The extension reads `category-tree.zh-CN.json` from its own package and keeps it in memory for the page; it does not reload the tree from Ozon or the local service on each expansion. The local service also uses a file-version-aware in-process cache. The collector can show at most three deterministic category recommendations, but the user always makes the final choice. Recent and favorite categories are stored locally. The collection inbox can hold any number of products. After collecting all desired products, click `运行任务` once; the batch then runs every queued product without further confirmation.

The service writes a new product directory:

```text
products/Pxxxxxx/
  input/source.json
  input/raw-snapshot.json
  input/category-selection.json
  input/main-images/
  input/sku-images/
  input/detail-images/
  output/
  status.json
  logs/
```

Validate the captured product:

```bash
python3 scripts/validate_product.py products/Pxxxxxx --collector-only --check-upload-gate
```

The important collection files are `input/source.json`, `input/raw-snapshot.json`, `input/category-selection.json`, downloaded original images, `status.json`, and `logs/collector.log.jsonl`. `source.json` remains limited to 1688 facts; the separate category-selection file records the user's Ozon choice and immutable rule snapshot. Changing the category before any Ozon write invalidates old attributes, image strategy, and upload payloads. The final collection state is `COLLECTED`; processing starts only when `运行任务` creates the batch.

## Example Product

The structural example is:

```text
products/P000001
```

It is not a real product listing. Unknown fields are intentionally written as `unknown` or `null` until a real 1688 product page is collected in a later stage.
