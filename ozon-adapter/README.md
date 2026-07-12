# Ozon Adapter Stage 4.1

This module exposes only read-only Ozon Seller API metadata operations.

Allowed endpoints:

- `POST /v1/description-category/tree`
- `POST /v1/description-category/attribute`
- `POST /v1/description-category/attribute/values`

Shop names and environment-variable references are stored in `shops.json`. Secrets are never stored in that file.

The first configured shop is `zhonglian1`, which reads:

- `OZON_ZHONGLIAN1_CLIENT_ID`
- `OZON_ZHONGLIAN1_API_KEY`

Future shops use their own names and environment-variable pairs. A product is not bound to the shop used for read-only metadata. The target shop is recorded only by a future confirmed upload operation.

The adapter rejects every endpoint outside the fixed allowlist. It contains no product creation, image upload, price, stock, SKU upload, or publication operation.

Fetch metadata after credentials are present:

```bash
python3 ozon-adapter/cli.py products/P000004 --shop zhonglian1 --fetch
```

Verify a previously fetched package:

```bash
python3 ozon-adapter/cli.py products/P000004 --verify
```

Stage 4.1 always writes `upload_allowed=false`.
