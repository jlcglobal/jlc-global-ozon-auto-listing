#!/usr/bin/env python3
"""Import a source-preserved Ozon official search query capture."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "market-intelligence"))

from market_intelligence import MarketStore, import_search_query_file  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    args = parser.parse_args()
    store = MarketStore(ROOT / "market-intelligence/market.sqlite")
    store.initialize()
    print(json.dumps(import_search_query_file(args.source, store), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
