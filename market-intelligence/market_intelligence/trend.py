"""Build traceable day-over-day trend reports from imported market snapshots."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .storage import MarketStore


def _number(value: Any) -> Optional[float]:
    if value in {None, "", "unknown"}:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _delta(current: Any, baseline: Any) -> Optional[float]:
    current_number = _number(current)
    baseline_number = _number(baseline)
    if current_number is None or baseline_number is None:
        return None
    return round(current_number - baseline_number, 2)


def _atomic_write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        temporary = Path(handle.name)
    os.replace(temporary, path)


def build_trend_report(store: MarketStore, generated_at: str) -> Dict[str, Any]:
    with store.connect() as connection:
        date_rows = connection.execute(
            "SELECT observed_on, COUNT(*) AS item_count FROM product_snapshots GROUP BY observed_on ORDER BY observed_on"
        ).fetchall()
    snapshot_dates = [row["observed_on"] for row in date_rows]
    report: Dict[str, Any] = {
        "schema_version": "1.0.0",
        "generated_at": generated_at,
        "snapshot_dates": snapshot_dates,
        "days_collected": len(snapshot_dates),
        "latest_snapshot_on": snapshot_dates[-1] if snapshot_dates else "unknown",
        "baseline_snapshot_on": snapshot_dates[-2] if len(snapshot_dates) >= 2 else "unknown",
        "state": "ready" if len(snapshot_dates) >= 2 else "collecting",
        "notice": "趋势对比已生成" if len(snapshot_dates) >= 2 else "已保存首日快照，至少需要两个不同日期才能比较趋势",
        "new_products": [],
        "dropped_products": [],
        "top_sales_amount_increase": [],
        "top_unit_increase": [],
        "top_sales_amount_decrease": [],
    }
    if len(snapshot_dates) < 2:
        return report

    latest_on, baseline_on = snapshot_dates[-1], snapshot_dates[-2]
    with store.connect() as connection:
        rows = connection.execute(
            """
            SELECT p.source_product_id, p.title_ru, p.title_zh, p.product_url,
                   current.facts_json AS current_json, baseline.facts_json AS baseline_json
            FROM product_snapshots current
            JOIN product_snapshots baseline ON baseline.product_key = current.product_key AND baseline.observed_on = ?
            JOIN products p ON p.product_key = current.product_key
            WHERE current.observed_on = ?
            """,
            (baseline_on, latest_on),
        ).fetchall()
        new_rows = connection.execute(
            """
            SELECT p.source_product_id, p.title_ru, p.title_zh, p.product_url
            FROM product_snapshots current JOIN products p ON p.product_key = current.product_key
            LEFT JOIN product_snapshots baseline ON baseline.product_key = current.product_key AND baseline.observed_on = ?
            WHERE current.observed_on = ? AND baseline.product_key IS NULL
            LIMIT 200
            """,
            (baseline_on, latest_on),
        ).fetchall()
        dropped_rows = connection.execute(
            """
            SELECT p.source_product_id, p.title_ru, p.title_zh, p.product_url
            FROM product_snapshots baseline JOIN products p ON p.product_key = baseline.product_key
            LEFT JOIN product_snapshots current ON current.product_key = baseline.product_key AND current.observed_on = ?
            WHERE baseline.observed_on = ? AND current.product_key IS NULL
            LIMIT 200
            """,
            (latest_on, baseline_on),
        ).fetchall()

    compared: List[Dict[str, Any]] = []
    for row in rows:
        current = json.loads(row["current_json"])
        baseline = json.loads(row["baseline_json"])
        compared.append({
            "source_product_id": row["source_product_id"],
            "title_ru": row["title_ru"],
            "title_zh": row["title_zh"],
            "product_url": row["product_url"],
            "category_level_1": current.get("category_level_1", "unknown"),
            "category_level_3": current.get("category_level_3", "unknown"),
            "ordered_amount_rub": _number(current.get("ordered_amount_rub")),
            "ordered_units": _number(current.get("ordered_units")),
            "ordered_amount_change_rub": _delta(current.get("ordered_amount_rub"), baseline.get("ordered_amount_rub")),
            "ordered_units_change": _delta(current.get("ordered_units"), baseline.get("ordered_units")),
            "growth_percent_change": _delta(current.get("ordered_amount_growth_percent"), baseline.get("ordered_amount_growth_percent")),
        })

    def ranked(field: str, reverse: bool = True) -> List[Dict[str, Any]]:
        items = [item for item in compared if item[field] is not None]
        return sorted(items, key=lambda item: float(item[field]), reverse=reverse)[:100]

    report.update({
        "compared_product_count": len(compared),
        "new_products": [dict(row) for row in new_rows],
        "dropped_products": [dict(row) for row in dropped_rows],
        "top_sales_amount_increase": ranked("ordered_amount_change_rub"),
        "top_unit_increase": ranked("ordered_units_change"),
        "top_sales_amount_decrease": ranked("ordered_amount_change_rub", reverse=False),
    })
    return report


def write_trend_report(report: Dict[str, Any], reports_dir: Path) -> Dict[str, str]:
    latest_on = str(report.get("latest_snapshot_on") or date.today().isoformat())
    dated_path = reports_dir / f"trend-{latest_on}.json"
    latest_path = reports_dir / "latest.json"
    _atomic_write_json(dated_path, report)
    _atomic_write_json(latest_path, report)
    return {"dated_report": str(dated_path.resolve()), "latest_report": str(latest_path.resolve())}
