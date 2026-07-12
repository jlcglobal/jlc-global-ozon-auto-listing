# Ozon Uploader Stage 4.3

This module creates or updates one product within a user-started batch. It does not publish inventory.

Safety rules:

- `UPLOAD_MODE` defaults to `dry-run`; this mode never creates an Ozon client or sends an API request.
- API writes require the exact value `UPLOAD_MODE=production`.
- Production requires `final-upload-check.json.status=PASS`; existing products enter the update path.
- Offer existence is checked before a write and saved to `product-exists-check.json`.
- Missing offers use `create`; existing offer IDs use the import endpoint as `update`; unchanged uploaded hashes use `skip`.
- Existing products are not an error and never receive a new offer ID.
- Color images follow `SKU image -> visually verified main image -> QC-approved Codex image -> missing`; `missing` keeps production blocked.
- Clicking `运行任务` records `task_authorized=true` for every product in that batch snapshot.
- There is no per-product `WAITING_REVIEW` or `APPROVED` gate.
- The collection inbox has no product-count limit; each product has a hard maximum of 10 selected SKUs.
- Final upload preflight and `ozon-draft.json.upload_allowed` must both be `true`.
- Images are served through a temporary Cloudflare HTTPS tunnel only while Ozon imports them.
- The client allowlist contains only product import and import-status endpoints.
- No stock, warehouse, activation, or inventory endpoint exists in this module.
- After submission, the actual shop name is saved in `status.json.ozon.shop_name`.

Dry run without API writes:

```bash
python3 ozon-uploader/cli.py products/P000004 --execute
```

The complete local request is saved as `output/ozon-upload-payload.json`.

Execute after the product package passes every gate:

```bash
UPLOAD_MODE=production python3 ozon-uploader/cli.py products/P000004 --shop zhonglian1 --execute
```
