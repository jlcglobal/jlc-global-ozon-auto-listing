#!/usr/bin/env python3
"""Rank Ozon keywords and separate growth from opportunity.

Reads the standard CSV produced by fetch_seefar_keyword_growth.py and writes a
ranked CSV consumed by build_readable_weekly_report.mjs. Growth and opportunity
are scored separately per keyword-growth-framework.md.
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

SOURCE_COLUMNS = [
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

RANK_COLUMNS = SOURCE_COLUMNS + [
    "growth_rank",
    "demand_momentum",
    "competition",
    "concentration",
    "stability",
    "operational_risk",
    "opportunity_score",
    "bucket",
    "exclude_reason",
    "confidence",
    "false_growth_risk",
]


def _to_float(value):
    if value in (None, ""):
        return None
    try:
        return float(str(value).replace("%", "").replace(",", "").strip())
    except (ValueError, TypeError):
        return None


def _to_percent(value):
    """Normalize share/rate fields: Seefar emits 0–1 decimals; framework uses 0–100."""
    number = _to_float(value)
    if number is not None and number <= 1.0:
        return number * 100
    return number


def _to_growth_rate(value):
    """Seefar 用 100 作为「无增长数据」的缺省值；把它当无数据，不当真实 +100%。"""
    number = _to_float(value)
    if number is not None and abs(number - 100) < 0.01:
        return None
    return number


def _percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered) - 1) * p
    lower = int(index)
    upper = min(lower + 1, len(ordered) - 1)
    weight = index - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _growth_rank_key(row: dict):
    # Absolute search heat and growth rate together; never rate alone.
    heat = _to_float(row["monthly_search_heat"]) or 0
    rate = _to_growth_rate(row["monthly_growth_percent"]) or 0
    return (rate if rate >= 0 else -1, heat)


def _score_dimensions(row: dict, heat_median: float | None) -> tuple[int, int, int, int, int]:
    heat = _to_float(row["monthly_search_heat"])
    rate = _to_growth_rate(row["monthly_growth_percent"])
    concentration = _to_percent(row["conversion_concentration_percent"])
    competitors = _to_float(row["competitor_count"])
    sellers = _to_float(row["competitor_seller_count"])
    ads = _to_float(row["ad_competitor_count"])
    returns = _to_percent(row["return_cancel_rate_percent"])

    # Demand momentum: rate + absolute volume together.
    if rate is None:
        demand = 6
    elif rate >= 20 and heat is not None and heat > 0:
        demand = 20
    elif rate >= 10:
        demand = 15
    elif rate > 0:
        demand = 10
    else:
        demand = 5

    # Competition: competitor/ad counts. More competition -> lower.
    competition = 10
    total_compete = sum(v for v in (competitors, ads) if v is not None)
    if competitors is not None:
        competition = 18 if competitors < 300 else 14 if competitors < 1000 else 8
    elif ads is not None:
        competition = 18 if ads < 100 else 14 if ads < 400 else 8

    # Concentration: conversion concentration (not top-5 seller share).
    if concentration is None:
        concentration_score = 10
    elif concentration < 30:
        concentration_score = 20
    elif concentration < 60:
        concentration_score = 12
    else:
        concentration_score = 5

    # Stability: single export cannot prove it.
    stability = 10

    # Operational risk: return/cancel rate.
    if returns is None:
        operational = 12
    elif returns < 5:
        operational = 18
    elif returns < 15:
        operational = 12
    else:
        operational = 5

    return demand, competition, concentration_score, stability, operational


def _exclusion(
    row: dict,
    heat_p25: float | None,
    rate_p75: float | None,
    competitor_p75: float | None,
    return_median: float | None,
) -> tuple[bool, str]:
    heat = _to_float(row["monthly_search_heat"])
    rate = _to_growth_rate(row["monthly_growth_percent"])
    concentration = _to_percent(row["conversion_concentration_percent"])
    competitors = _to_float(row["competitor_count"])
    returns = _to_percent(row["return_cancel_rate_percent"])

    # 增长率异常：>300% 对月搜热度而言几乎必是数据波动或基期失真，
    # 而非真实需求翻倍，直接进排除项。
    if rate is not None and rate > 300:
        return True, "增长率异常（疑似数据波动）"
    # 低基数尖峰：热度低于池内下四分位(P25) 且增长率高于上四分位(P75)。
    if (
        heat is not None and rate is not None
        and heat_p25 is not None and rate_p75 is not None
        and heat < heat_p25 and rate > rate_p75
    ):
        return True, "低基数尖峰"
    if concentration is not None and concentration > 70:
        return True, "垄断/转化集中度过高"
    if competitors is not None and competitor_p75 is not None and competitors > competitor_p75 * 3:
        return True, "供给拥挤"
    if returns is not None and return_median and returns > return_median * 3:
        return True, "退货取消率过高"
    return False, ""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_csv", help="fetch 脚本输出的标准 CSV")
    parser.add_argument("output_csv", nargs="?", default=None)
    args = parser.parse_args()

    input_path = Path(args.input_csv)
    output_path = Path(args.output_csv) if args.output_csv else input_path.with_name("keyword_growth_rank.csv")

    with input_path.open(encoding="utf-8-sig", newline="") as handle:
        raw_rows = list(csv.DictReader(handle))

    rows = [dict(row) for row in raw_rows]
    heat_values = [_to_float(row["monthly_search_heat"]) for row in rows]
    heat_values = [value for value in heat_values if value is not None]
    heat_p25 = _percentile(heat_values, 0.25)
    rate_values = [_to_growth_rate(row["monthly_growth_percent"]) for row in rows]
    rate_values = [value for value in rate_values if value is not None]
    rate_p75 = _percentile(rate_values, 0.75)
    competitor_values = [_to_float(row["competitor_count"]) for row in rows]
    competitor_values = [value for value in competitor_values if value is not None]
    competitor_p75 = _percentile(competitor_values, 0.75)
    return_values = [_to_percent(row["return_cancel_rate_percent"]) for row in rows]
    return_values = [value for value in return_values if value is not None]
    return_median = sorted(return_values)[len(return_values) // 2] if return_values else None

    ranked = []
    for index, row in enumerate(sorted(rows, key=_growth_rank_key, reverse=True), start=1):
        demand, competition, concentration, stability, operational = _score_dimensions(row, heat_p25)
        opportunity = demand + competition + concentration + stability + operational
        excluded, reason = _exclusion(row, heat_p25, rate_p75, competitor_p75, return_median)
        bucket = "exclude" if excluded else ("opportunity" if opportunity >= 60 else "growth")
        rate = _to_growth_rate(row["monthly_growth_percent"])
        confidence = "高" if rate is not None and rate >= 20 else "中" if rate is not None and rate >= 10 else "低"
        false_risk = "低基数/单周期" if excluded else ("单周期未验证" if (rate or 0) > 0 else "无增长")
        out = {**row,
               "growth_rank": index,
               "demand_momentum": demand,
               "competition": competition,
               "concentration": concentration,
               "stability": stability,
               "operational_risk": operational,
               "opportunity_score": opportunity,
               "bucket": bucket,
               "exclude_reason": reason if excluded else "",
               "confidence": confidence,
               "false_growth_risk": false_risk}
        ranked.append(out)

    with output_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=RANK_COLUMNS)
        writer.writeheader()
        writer.writerows(ranked)
    print(f"ranked {len(ranked)} rows -> {output_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
