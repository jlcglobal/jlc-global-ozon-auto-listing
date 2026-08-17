#!/usr/bin/env python3
"""Build a read-only map of Ozon duplicate-card errors across configured shops."""

from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple


ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = ROOT / "ozon-adapter/shops.json"
AUDIT_DIR = ROOT / "market-intelligence/reports/search-visibility-seo-repairs"
REPORT_DIR = ROOT / "market-intelligence/reports/ozon-cross-shop-duplicates"
DUPLICATE_CODE = "SPU_ALREADY_EXISTS_IN_ANOTHER_ACCOUNT"


def chunks(values: List[Any], size: int) -> Iterable[List[Any]]:
    for index in range(0, len(values), size):
        yield values[index:index + size]


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def load_env(path: Path) -> Dict[str, str]:
    values: Dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def load_shops() -> Tuple[List[Dict[str, str]], Dict[str, Dict[str, str]]]:
    registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    shops: List[Dict[str, str]] = []
    credentials: Dict[str, Dict[str, str]] = {}
    for raw_shop in registry.get("shops") or []:
        shop_id = str(raw_shop.get("id") or raw_shop.get("name") or "")
        if not shop_id:
            continue
        env_path = ROOT / f"ozon-adapter/.env.{shop_id}"
        if not env_path.exists():
            continue
        values = load_env(env_path)
        client_id = values.get(str(raw_shop.get("client_id_env") or ""), "").strip()
        api_key = values.get(str(raw_shop.get("api_key_env") or ""), "").strip()
        if not client_id or not api_key:
            continue
        shops.append({
            "id": shop_id,
            "name": str(raw_shop.get("name") or shop_id),
            "client_id": client_id,
        })
        credentials[shop_id] = {"client_id": client_id, "api_key": api_key}
    return shops, credentials


class OzonReadClient:
    def __init__(self, credentials: Dict[str, str]) -> None:
        self.headers = {
            "Client-Id": credentials["client_id"],
            "Api-Key": credentials["api_key"],
            "Content-Type": "application/json",
        }

    def post(self, endpoint: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        if endpoint != "/v3/product/info/list":
            raise RuntimeError(f"Endpoint is not allowed in duplicate audit: {endpoint}")
        request = urllib.request.Request(
            f"https://api-seller.ozon.ru{endpoint}",
            data=json.dumps(payload).encode("utf-8"),
            headers=self.headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                result = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(
                f"Ozon {endpoint} returned HTTP {exc.code}: {body[:500]}"
            ) from exc
        if not isinstance(result, dict):
            raise RuntimeError(f"Ozon {endpoint} returned an invalid response")
        return result


def latest_error_audit() -> Path:
    paths = sorted(AUDIT_DIR.glob("*-current-catalog-error-audit.json"))
    if not paths:
        raise RuntimeError("No current Ozon catalog error audit was found")
    return paths[-1]


def duplicate_targets(error: Dict[str, Any]) -> List[Dict[str, str]]:
    texts = error.get("texts") if isinstance(error.get("texts"), dict) else {}
    message = str(texts.get("message") or "").strip()
    if message:
        try:
            parsed = json.loads(message)
        except json.JSONDecodeError:
            parsed = {}
        targets = []
        for item in parsed.get("DUPLICATES") or []:
            if not isinstance(item, dict):
                continue
            offer_id = str(item.get("OFFERID") or "").strip()
            company_id = str(item.get("COMPANYID") or "").strip()
            if offer_id and company_id:
                targets.append({"offer_id": offer_id, "company_id": company_id})
        if targets:
            return targets

    for param in texts.get("params") or []:
        if not isinstance(param, dict) or param.get("name") != "Duplicates":
            continue
        value = str(param.get("value") or "")
        offer_id, separator, company_id = value.rpartition(" - ")
        if separator and offer_id.strip() and company_id.strip():
            return [{"offer_id": offer_id.strip(), "company_id": company_id.strip()}]
    return []


def fetch_by_product_ids(client: OzonReadClient, product_ids: List[int]) -> Dict[str, Dict[str, Any]]:
    rows: Dict[str, Dict[str, Any]] = {}
    for group in chunks(product_ids, 100):
        response = client.post("/v3/product/info/list", {
            "offer_id": [],
            "product_id": group,
            "sku": [],
        })
        for item in response.get("items") or []:
            if isinstance(item, dict):
                rows[str(item.get("id") or item.get("product_id") or "")] = dict(item)
    return rows


def fetch_by_offer_ids(client: OzonReadClient, offer_ids: List[str]) -> Dict[str, Dict[str, Any]]:
    rows: Dict[str, Dict[str, Any]] = {}
    for group in chunks(sorted(set(offer_ids)), 100):
        response = client.post("/v3/product/info/list", {
            "offer_id": group,
            "product_id": [],
            "sku": [],
        })
        for item in response.get("items") or []:
            if isinstance(item, dict):
                rows[str(item.get("offer_id") or "")] = dict(item)
    return rows


def card_snapshot(item: Dict[str, Any] | None) -> Dict[str, Any] | None:
    if not item:
        return None
    statuses = item.get("statuses") if isinstance(item.get("statuses"), dict) else {}
    stocks = item.get("stocks") if isinstance(item.get("stocks"), dict) else {}
    stock_rows = [row for row in stocks.get("stocks") or [] if isinstance(row, dict)]
    return {
        "product_id": str(item.get("id") or item.get("product_id") or ""),
        "offer_id": str(item.get("offer_id") or ""),
        "ozon_sku": str(item.get("sku") or ""),
        "name": str(item.get("name") or ""),
        "created_at": str(item.get("created_at") or ""),
        "updated_at": str(item.get("updated_at") or ""),
        "is_archived": bool(item.get("is_archived")),
        "status_name": str(statuses.get("status_name") or ""),
        "status_description": str(statuses.get("status_description") or ""),
        "moderate_status": str(statuses.get("moderate_status") or ""),
        "validation_status": str(statuses.get("validation_status") or ""),
        "price": str(item.get("price") or ""),
        "old_price": str(item.get("old_price") or ""),
        "has_stock": bool(stocks.get("has_stock")),
        "stock_present": sum(int(row.get("present") or 0) for row in stock_rows),
        "stock_reserved": sum(int(row.get("reserved") or 0) for row in stock_rows),
    }


def is_selling(card: Dict[str, Any] | None) -> bool:
    return bool(card and card.get("status_name") == "Продается" and not card.get("is_archived"))


def build_report(source_shop_id: str, audit_path: Path) -> Dict[str, Any]:
    shops, credentials = load_shops()
    shops_by_id = {shop["id"]: shop for shop in shops}
    shops_by_client_id = {shop["client_id"]: shop for shop in shops}
    if source_shop_id not in shops_by_id:
        raise RuntimeError(f"Configured shop is unavailable: {source_shop_id}")

    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    source_rows: List[Dict[str, Any]] = []
    target_offer_ids: Dict[str, List[str]] = {}
    for card in audit.get("other_error_cards") or []:
        if not isinstance(card, dict):
            continue
        targets: List[Dict[str, str]] = []
        for error in card.get("errors") or []:
            if isinstance(error, dict) and error.get("code") == DUPLICATE_CODE:
                targets.extend(duplicate_targets(error))
        if not targets:
            continue
        row = {
            "product_id": str(card.get("product_id") or ""),
            "offer_id": str(card.get("offer_id") or ""),
            "targets": targets,
        }
        source_rows.append(row)
        for target in targets:
            target_shop = shops_by_client_id.get(target["company_id"])
            if target_shop:
                target_offer_ids.setdefault(target_shop["id"], []).append(target["offer_id"])

    source_client = OzonReadClient(credentials[source_shop_id])
    source_ids = [int(row["product_id"]) for row in source_rows if row["product_id"].isdigit()]
    source_cards = fetch_by_product_ids(source_client, source_ids)
    target_cards: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for shop_id, offer_ids in target_offer_ids.items():
        client = source_client if shop_id == source_shop_id else OzonReadClient(credentials[shop_id])
        target_cards[shop_id] = fetch_by_offer_ids(client, offer_ids)

    relationships: List[Dict[str, Any]] = []
    for row in source_rows:
        source = card_snapshot(source_cards.get(row["product_id"]))
        for target in row["targets"]:
            target_shop = shops_by_client_id.get(target["company_id"])
            target_shop_id = target_shop["id"] if target_shop else "unconfigured"
            target_card = card_snapshot(
                (target_cards.get(target_shop_id) or {}).get(target["offer_id"])
            )
            relation_type = "same_shop" if target_shop_id == source_shop_id else "cross_shop"
            relationships.append({
                "relation_type": relation_type,
                "source_shop_id": source_shop_id,
                "source": source or {
                    "product_id": row["product_id"],
                    "offer_id": row["offer_id"],
                },
                "duplicate_shop_id": target_shop_id,
                "duplicate_company_id": target["company_id"],
                "duplicate_offer_id": target["offer_id"],
                "duplicate": target_card,
                "same_offer_id": bool(source and source.get("offer_id") == target["offer_id"]),
                "source_is_selling": is_selling(source),
                "duplicate_is_selling": is_selling(target_card),
                "both_are_selling": is_selling(source) and is_selling(target_card),
                "both_have_stock": bool(
                    source and source.get("has_stock") and target_card and target_card.get("has_stock")
                ),
                "migration_rule": (
                    "keep_both_live_until_shared_ozon_card_is_verified"
                    if relation_type == "cross_shop"
                    else "choose_one_same_shop_card_before_archiving_the_other"
                ),
            })

    target_shop_counts = Counter(row["duplicate_shop_id"] for row in relationships)
    unresolved = [row for row in relationships if row["duplicate"] is None]
    return {
        "schema_version": "1.0.0",
        "mode": "read_only_cross_shop_duplicate_audit",
        "generated_at": now_iso(),
        "source_shop_id": source_shop_id,
        "source_audit": str(audit_path.relative_to(ROOT)),
        "duplicate_card_count": len(source_rows),
        "relationship_count": len(relationships),
        "same_shop_relationship_count": sum(
            row["relation_type"] == "same_shop" for row in relationships
        ),
        "cross_shop_relationship_count": sum(
            row["relation_type"] == "cross_shop" for row in relationships
        ),
        "target_shop_counts": dict(sorted(target_shop_counts.items())),
        "target_found_count": sum(row["duplicate"] is not None for row in relationships),
        "target_unresolved_count": len(unresolved),
        "source_selling_count": sum(row["source_is_selling"] for row in relationships),
        "target_selling_count": sum(row["duplicate_is_selling"] for row in relationships),
        "both_selling_count": sum(row["both_are_selling"] for row in relationships),
        "same_offer_id_count": sum(row["same_offer_id"] for row in relationships),
        "both_have_stock_count": sum(row["both_have_stock"] for row in relationships),
        "relationships": relationships,
        "write_api_calls": 0,
        "inventory_api_calls": 0,
    }


def save_report(report: Dict[str, Any]) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    path = REPORT_DIR / f"{stamp}-cross-shop-duplicate-audit.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-shop", default="zhonglian1")
    parser.add_argument("--audit", type=Path, default=None)
    args = parser.parse_args()

    audit_path = args.audit.resolve() if args.audit else latest_error_audit()
    report = build_report(args.source_shop, audit_path)
    report_path = save_report(report)
    print(json.dumps({
        key: report[key]
        for key in (
            "mode",
            "source_shop_id",
            "duplicate_card_count",
            "relationship_count",
            "same_shop_relationship_count",
            "cross_shop_relationship_count",
            "target_shop_counts",
            "target_found_count",
            "target_unresolved_count",
            "source_selling_count",
            "target_selling_count",
            "both_selling_count",
            "same_offer_id_count",
            "both_have_stock_count",
            "write_api_calls",
            "inventory_api_calls",
        )
    } | {"report": str(report_path.relative_to(ROOT))}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
