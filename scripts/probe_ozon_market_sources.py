#!/usr/bin/env python3
"""Probe real Ozon read-only market-data capabilities and persist safe status."""

from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "market-intelligence"))
sys.path.insert(0, str(ROOT / "scripts"))

from market_intelligence import MarketStore, OzonAnalyticsReadOnlyClient, probe_ozon_sources  # noqa: E402
from workbench_stores import load_registry, read_secret  # noqa: E402


def main() -> int:
    registry = load_registry(ROOT)
    store_id = str(registry.get("default_read_shop") or "")
    shop = next((item for item in registry.get("shops") or [] if str(item.get("id")) == store_id), None)
    if not shop:
        raise SystemExit("No default Ozon read-only shop is configured")
    secrets = read_secret(ROOT, shop)
    client = OzonAnalyticsReadOnlyClient(
        secrets.get(str(shop["client_id_env"]), ""),
        secrets.get(str(shop["api_key_env"]), ""),
    )
    records = probe_ozon_sources(client)
    store = MarketStore(ROOT / "market-intelligence/market.sqlite")
    store.initialize()
    for record in records.values():
        store.upsert_source_status(record)
    print(json.dumps({"schema_version": "1.0.0", "sources": list(records.values())}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
