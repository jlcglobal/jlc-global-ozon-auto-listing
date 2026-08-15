# Local Ingest Service

Start from the project root:

```bash
python3 -m uvicorn app:app --app-dir collector/local-ingest --host 127.0.0.1 --port 8765
```

Endpoints:

- `GET /health`
- `POST /api/collector/products`
- `GET /api/collector/products/{product_id}`
- `GET /api/collector/products/{product_id}/status`
- `POST /api/tasks/run` starts one batch for every currently `COLLECTED` product
- `GET /api/tasks/status` reports the active batch and its latest result

The collection inbox has no product-count limit. Each individual product must contain between 1 and 10 selected SKUs; the same limit is enforced again when the product enters the batch queue.

If the same `source_url` already exists, the service returns `409` by default. The extension then offers two choices: show the existing product ID or create a new capture version with `allow_new_version=true`.
