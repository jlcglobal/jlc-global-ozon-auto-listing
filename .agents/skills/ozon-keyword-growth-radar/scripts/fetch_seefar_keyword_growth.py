#!/usr/bin/env python3
"""Normalize a Seefar keyword-miner export into the standard keyword schema.

Seefar has no category API. Data comes from the keyword-mining page your
workbench already integrates (seerfar-content.ts). Export that table as CSV and
pass it with ``--input-csv``; this script maps Seefar columns to a fixed schema
so the rank step is stable. Never invent missing values.
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

# Standard output columns (rank step depends on these exact names).
OUTPUT_COLUMNS = [
    "keyword",
    "monthly_search_heat",
    "monthly_growth_percent",
    "market_space",
    "conversion_concentration_percent",
    "competitor_count",
    "competitor_seller_count",
    "ad_competitor_count",
    "product_count",
    "cart_conversion_percent",
    "return_cancel_rate_percent",
    "average_price_rub",
]

# Seefar export column -> standard column. Standard name wins first.
FIELD_ALIASES = {
    "keyword": ["query", "关键词", "keyword"],
    "monthly_search_heat": ["monthly_search_heat", "月搜热度", "月搜索热度"],
    "monthly_growth_percent": ["monthly_growth_percent", "月搜增长", "月增长"],
    "market_space": ["market_space", "市场空间"],
    "conversion_concentration_percent": ["conversion_concentration_percent", "转化集中度"],
    "competitor_count": ["competitor_count", "竞品数"],
    "competitor_seller_count": ["competitor_seller_count", "竞对数"],
    "ad_competitor_count": ["ad_competitor_count", "广告竞品数", "广告竞品"],
    "product_count": ["product_count", "商品数"],
    "cart_conversion_percent": ["cart_conversion_percent", "加购转化率"],
    "return_cancel_rate_percent": ["return_cancel_rate_percent", "退货取消率", "退款率"],
    "average_price_rub": ["average_price_rub", "平均价格"],
}


def _lookup(record: dict, column: str):
    if column in record and record[column] not in (None, ""):
        return record[column]
    for alias in FIELD_ALIASES.get(column, []):
        if alias in record and record[alias] not in (None, ""):
            return record[alias]
    return ""


def _to_float(value):
    if value in (None, ""):
        return None
    try:
        return float(str(value).replace("%", "").replace(",", "").strip())
    except (ValueError, TypeError):
        return None


def normalize(rows: list[dict], min_growth: float) -> list[dict]:
    out: list[dict] = []
    for record in rows:
        if not isinstance(record, dict):
            continue
        keyword = str(_lookup(record, "keyword")).strip()
        if not keyword:
            continue
        row = {column: _lookup(record, column) for column in OUTPUT_COLUMNS}
        row["keyword"] = keyword
        rate = _to_float(row["monthly_growth_percent"])
        if rate is not None and abs(rate) < min_growth:
            continue
        out.append(row)
    return out


def read_input_csv(path: Path, min_growth: float) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
    if not rows:
        raise RuntimeError(f"{path} 没有数据行；请确认 Seefar 关键词挖掘表已导出")
    return normalize(rows, min_growth)


def write_csv(rows: list[dict], output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "keyword_growth_raw.csv"
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-csv", required=True, help="Seefar 关键词挖掘导出 CSV")
    parser.add_argument("--min-growth", type=float, default=10, help="最小月搜增长百分比")
    parser.add_argument("--output-dir", default="outputs/ozon-keyword-growth")
    args = parser.parse_args()

    rows = read_input_csv(Path(args.input_csv), args.min_growth)
    path = write_csv(rows, Path(args.output_dir))
    print(f"written {len(rows)} rows -> {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
