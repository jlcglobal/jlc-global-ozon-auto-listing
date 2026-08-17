#!/usr/bin/env python3
"""Weekly controlled Ozon search-visibility update for JLC shops.

The job is intentionally narrow: it updates only attributes 23171 and 4191,
requires an explicit confirmation token, writes backups/preflights first, then
waits for Ozon task status and field readback after every submitted batch.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
CONFIRMATION = "APPLY_WEEKLY_SEARCH_VISIBILITY_AUTO_20260804"
MIN_COVERAGE = 0.92
ALL_SHOPS = ("zhonglian1", "zhonglian2", "volttech", "zhonglian3", "zhonglian4", "zhonglian5", "jlc-blobal-6")


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


repair = _load_module("weekly_zhonglian1_repair", ROOT / "scripts/repair_zhonglian1_search_visibility_fields.py")
six = _load_module("weekly_six_shop_repair", ROOT / "scripts/repair_six_shop_search_visibility_fields.py")


def _project_path(path_value: str) -> Path:
    path = Path(path_value)
    return path if path.is_absolute() else ROOT / path


def _preflight_path(payload: Mapping[str, Any]) -> Path:
    return _project_path(str(payload.get("preflight_report") or ""))


def _summary_int(summary: Mapping[str, Any], key: str) -> int:
    try:
        return int(summary.get(key) or 0)
    except (TypeError, ValueError):
        return 0


def _summary_float(summary: Mapping[str, Any], key: str) -> float:
    try:
        return float(summary.get(key) or 0)
    except (TypeError, ValueError):
        return 0.0


def _no_update_result(shop_id: str, preflight: Mapping[str, Any], reason: str) -> Dict[str, Any]:
    return {
        "shop_id": shop_id,
        "status": "no_ready_updates",
        "reason": reason,
        "backup_report": preflight.get("backup_report") or "",
        "preflight_report": preflight.get("preflight_report") or "",
        "summary": preflight.get("summary") or {},
        "submitted_card_count": 0,
        "processed_card_count": 0,
        "card_success_count": 0,
        "field_readback_match_count": 0,
        "field_readback_mismatch_count": 0,
        "task_ids": [],
        "write_api_calls": 0,
        "read_api_calls": int(preflight.get("read_api_calls") or 0),
        "inventory_api_calls": int(preflight.get("inventory_api_calls") or 0),
    }


def _coverage_block_result(shop_id: str, preflight: Mapping[str, Any], min_coverage: float) -> Dict[str, Any]:
    summary = preflight.get("summary") or {}
    return {
        "shop_id": shop_id,
        "status": "coverage_below_min_stopped",
        "reason": f"safe coverage {_summary_float(summary, 'coverage_ratio'):.2%} is below {min_coverage:.2%}",
        "backup_report": preflight.get("backup_report") or "",
        "preflight_report": preflight.get("preflight_report") or "",
        "summary": summary,
        "submitted_card_count": 0,
        "processed_card_count": 0,
        "card_success_count": 0,
        "field_readback_match_count": 0,
        "field_readback_mismatch_count": 0,
        "task_ids": [],
        "write_api_calls": 0,
        "read_api_calls": int(preflight.get("read_api_calls") or 0),
        "inventory_api_calls": int(preflight.get("inventory_api_calls") or 0),
    }


def _receipt_result(shop_id: str, receipt: Mapping[str, Any], preflight: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "shop_id": shop_id,
        "status": receipt.get("status") or "unknown",
        "backup_report": preflight.get("backup_report") or "",
        "preflight_report": preflight.get("preflight_report") or "",
        "receipt_report": receipt.get("receipt_report") or "",
        "summary": preflight.get("summary") or {},
        "submitted_card_count": int(receipt.get("submitted_card_count") or 0),
        "processed_card_count": int(receipt.get("processed_card_count") or 0),
        "card_success_count": int(receipt.get("card_success_count") or 0),
        "field_readback_match_count": int(receipt.get("field_readback_match_count") or 0),
        "field_readback_mismatch_count": int(receipt.get("field_readback_mismatch_count") or 0),
        "field_success_ratio": receipt.get("field_success_ratio") or 0,
        "task_ids": receipt.get("task_ids") or [],
        "write_api_calls": int(receipt.get("write_api_calls") or 0),
        "read_api_calls": int(receipt.get("read_api_calls") or 0),
        "inventory_api_calls": int(receipt.get("inventory_api_calls") or 0),
    }


def _apply_preflight(
    shop_id: str,
    preflight: Mapping[str, Any],
    *,
    batch_size: int,
    verify_seconds: int,
    min_coverage: float,
) -> Dict[str, Any]:
    summary = preflight.get("summary") or {}
    if _summary_float(summary, "coverage_ratio") < min_coverage:
        return _coverage_block_result(shop_id, preflight, min_coverage)
    if _summary_int(summary, "requires_update_cards") <= 0:
        return _no_update_result(shop_id, preflight, "all_ready_cards_already_ok")
    client = repair.CountingOzonClient(repair.load_credentials(shop_id))
    receipt = repair.apply_rows(
        client,
        _preflight_path(preflight),
        preflight,
        scope="all",
        batch_size=batch_size,
        verify_seconds=verify_seconds,
        min_coverage=min_coverage,
    )
    return _receipt_result(shop_id, receipt, preflight)


def run_shop(
    shop_id: str,
    *,
    batch_size: int,
    verify_seconds: int,
    min_coverage: float,
    include_query_details: bool,
) -> Dict[str, Any]:
    if shop_id == "zhonglian1":
        repair.SHOP_ID = "zhonglian1"
        client = repair.CountingOzonClient(repair.load_credentials(shop_id))
        _backup, preflight = repair.build_preflight(client)
        return _apply_preflight(
            shop_id,
            preflight,
            batch_size=batch_size,
            verify_seconds=verify_seconds,
            min_coverage=min_coverage,
        )

    six.set_shop(shop_id)
    repair.SHOP_ID = shop_id
    _backup_path, _full_path, _apply_path, full_preflight, apply_preflight = six.build_shop_preflight(
        shop_id,
        include_query_details=include_query_details,
    )
    full_summary = full_preflight.get("summary") or {}
    if _summary_float(full_summary, "coverage_ratio") < min_coverage:
        return _coverage_block_result(shop_id, full_preflight, min_coverage)
    if _summary_int(apply_preflight.get("summary") or {}, "requires_update_cards") <= 0:
        return _no_update_result(shop_id, full_preflight, "all_ready_cards_already_ok")
    return _apply_preflight(
        shop_id,
        apply_preflight,
        batch_size=batch_size,
        verify_seconds=verify_seconds,
        min_coverage=1.0,
    )


def save_summary(results: Sequence[Mapping[str, Any]], shops: Sequence[str], min_coverage: float) -> Dict[str, Any]:
    summary = {
        "shop_count": len(results),
        "verified_or_no_update_shops": sum(1 for item in results if item.get("status") in {"verified", "no_ready_updates"}),
        "stopped_shops": sum(1 for item in results if item.get("status") not in {"verified", "no_ready_updates"}),
        "submitted_card_count": sum(int(item.get("submitted_card_count") or 0) for item in results),
        "processed_card_count": sum(int(item.get("processed_card_count") or 0) for item in results),
        "card_success_count": sum(int(item.get("card_success_count") or 0) for item in results),
        "field_readback_match_count": sum(int(item.get("field_readback_match_count") or 0) for item in results),
        "field_readback_mismatch_count": sum(int(item.get("field_readback_mismatch_count") or 0) for item in results),
        "write_api_calls": sum(int(item.get("write_api_calls") or 0) for item in results),
        "read_api_calls": sum(int(item.get("read_api_calls") or 0) for item in results),
        "inventory_api_calls": sum(int(item.get("inventory_api_calls") or 0) for item in results),
    }
    payload = {
        "schema_version": "1.0.0",
        "mode": "weekly_search_visibility_auto_update",
        "shops": list(shops),
        "min_coverage": min_coverage,
        "changes": [f"attribute_{repair.OZON_HASHTAG_ATTRIBUTE_ID}", f"attribute_{repair.OZON_ANNOTATION_ATTRIBUTE_ID}"],
        "untouched": ["title", "price", "images", "brand", "category", "sku", "stock", "warehouse", "activation"],
        "summary": summary,
        "results": list(results),
    }
    path = repair.save_report("weekly-search-visibility-auto-update", payload)
    payload["summary_report"] = repair.project_relative(path)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--confirm", default="")
    parser.add_argument("--shops", nargs="*", default=list(ALL_SHOPS))
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--verify-seconds", type=int, default=300)
    parser.add_argument("--min-coverage", type=float, default=MIN_COVERAGE)
    parser.add_argument("--skip-query-details", action="store_true")
    args = parser.parse_args()

    if args.confirm != CONFIRMATION:
        raise RuntimeError(f"Explicit confirmation is required: {CONFIRMATION}")
    if args.verify_seconds < 300:
        raise RuntimeError("Delayed field verification must run for at least 300 seconds")
    shops = [str(shop).strip() for shop in args.shops if str(shop).strip()]
    invalid = [shop for shop in shops if shop not in ALL_SHOPS]
    if invalid:
        raise RuntimeError(f"These shops are not in the JLC allowlist: {invalid}")

    results: List[Dict[str, Any]] = []
    for shop_id in shops:
        result = run_shop(
            shop_id,
            batch_size=args.batch_size,
            verify_seconds=args.verify_seconds,
            min_coverage=max(0.0, min(1.0, float(args.min_coverage))),
            include_query_details=not args.skip_query_details,
        )
        results.append(result)
        print(json.dumps({"mode": "shop_done", **result}, ensure_ascii=False), flush=True)
        if result.get("status") not in {"verified", "no_ready_updates"}:
            break

    final = save_summary(results, shops, max(0.0, min(1.0, float(args.min_coverage))))
    print(json.dumps({"mode": "weekly_summary", "summary_report": final["summary_report"], "summary": final["summary"]}, ensure_ascii=False), flush=True)
    summary = final["summary"]
    if int(summary.get("inventory_api_calls") or 0) != 0:
        return 2
    if int(summary.get("stopped_shops") or 0) != 0:
        return 2
    if int(summary.get("field_readback_mismatch_count") or 0) != 0:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
