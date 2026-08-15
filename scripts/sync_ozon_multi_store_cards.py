#!/usr/bin/env python3
"""Link secondary-shop offers to one canonical Ozon card and copy its images.

The source shop is read-only. Secondary shops keep their existing offer IDs and
prices. No stock, warehouse, or inventory endpoint is available to this client.
"""
from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence

try:
    from audit_ozon_cross_shop_duplicates import load_shops, now_iso
    from pipeline_runtime import now
    from store_publications import load_publications, save_publications
except ModuleNotFoundError:  # Imported as scripts.sync_ozon_multi_store_cards.
    from scripts.audit_ozon_cross_shop_duplicates import load_shops, now_iso
    from scripts.pipeline_runtime import now
    from scripts.store_publications import load_publications, save_publications


ROOT = Path(__file__).resolve().parents[1]
CONFIRMATION = "SYNC_SECONDARY_SHOPS_BY_SOURCE_SKU"
READ_ENDPOINTS = frozenset({"/v3/product/info/list", "/v1/product/import/info"})
WRITE_ENDPOINTS = frozenset({"/v1/product/import-by-sku", "/v1/product/pictures/import"})
DUPLICATE_CODE = "SPU_ALREADY_EXISTS_IN_ANOTHER_ACCOUNT"


def chunks(values: Sequence[Any], size: int) -> Iterable[Sequence[Any]]:
    for index in range(0, len(values), size):
        yield values[index:index + size]


def model_id(item: Mapping[str, Any] | None) -> str:
    info = item.get("model_info") if isinstance((item or {}).get("model_info"), dict) else {}
    return str(info.get("model_id") or "")


def ordered_images(item: Mapping[str, Any] | None) -> List[str]:
    values: List[str] = []
    card = item or {}
    primary = card.get("primary_image") or []
    images = card.get("images") or []
    for raw in [*primary, *images]:
        value = str(raw or "").strip()
        if value and value not in values:
            values.append(value)
    return values[:15]


def error_codes(item: Mapping[str, Any] | None) -> List[str]:
    return [
        str(error.get("code") or "")
        for error in (item or {}).get("errors") or []
        if isinstance(error, dict) and str(error.get("code") or "")
    ]


def offer_map(product_dir: Path, shop_id: str) -> Dict[str, str]:
    path = product_dir / "output/store-runs" / shop_id / "store-offer-id-map.json"
    if not path.is_file():
        raise RuntimeError(f"店铺 {shop_id} 缺少商品货号映射")
    payload = json.loads(path.read_text(encoding="utf-8"))
    mapping = {
        str(item.get("sku_id") or ""): str(item.get("offer_id") or "")
        for item in payload.get("sku_offer_ids") or []
        if str(item.get("sku_id") or "") and str(item.get("offer_id") or "")
    }
    if not mapping:
        raise RuntimeError(f"店铺 {shop_id} 的商品货号映射为空")
    return mapping


def build_request_item(source: Mapping[str, Any], target: Mapping[str, Any]) -> Dict[str, Any]:
    source_sku = str(source.get("sku") or "")
    price = str(target.get("price") or "").strip()
    name = str(source.get("name") or "").strip()
    offer_id = str(target.get("offer_id") or "").strip()
    if not source_sku.isdigit() or not name or not offer_id or not price:
        raise RuntimeError("多店挂卡缺少主卡SKU、标题、目标货号或目标售价")
    item: Dict[str, Any] = {
        "sku": int(source_sku),
        "name": name,
        "offer_id": offer_id,
        "currency_code": str(target.get("currency_code") or "CNY"),
        "price": price,
        "vat": str(target.get("vat") or "0.00"),
    }
    old_price = str(target.get("old_price") or "").strip()
    if old_price:
        item["old_price"] = old_price
    return item


def pair_action(source: Mapping[str, Any], target: Mapping[str, Any]) -> str:
    source_model = model_id(source)
    target_model = model_id(target)
    source_name = str(source.get("name") or "").strip()
    target_name = str(target.get("name") or "").strip()
    if target_model and (target_model != source_model or target_name != source_name):
        return "BLOCK_DIFFERENT_REMOTE_CARD"
    if target_model and str(target.get("sku") or "").isdigit():
        return "ALREADY_SYNCED" if ordered_images(target) else "COPY_IMAGES_ONLY"
    return "LINK_AND_COPY_IMAGES"


class OzonClient:
    def __init__(self, credentials: Mapping[str, str], *, allow_write: bool) -> None:
        self.headers = {
            "Client-Id": str(credentials["client_id"]),
            "Api-Key": str(credentials["api_key"]),
            "Content-Type": "application/json",
        }
        self.allow_write = allow_write
        self.read_api_calls = 0
        self.write_api_calls = 0

    def post(self, endpoint: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        is_read = endpoint in READ_ENDPOINTS
        is_write = endpoint in WRITE_ENDPOINTS
        if not is_read and not is_write:
            raise RuntimeError(f"禁止的Ozon接口：{endpoint}")
        if is_write and not self.allow_write:
            raise RuntimeError("当前客户端只允许读取 1 店")
        attempts = 4 if is_read else 2
        for attempt in range(1, attempts + 1):
            if is_read:
                self.read_api_calls += 1
            else:
                self.write_api_calls += 1
            request = urllib.request.Request(
                f"https://api-seller.ozon.ru{endpoint}",
                data=json.dumps(payload).encode("utf-8"),
                headers=self.headers,
                method="POST",
            )
            try:
                with urllib.request.urlopen(request, timeout=45) as response:
                    result = json.loads(response.read().decode("utf-8"))
                if not isinstance(result, dict):
                    raise RuntimeError(f"Ozon {endpoint} 返回格式错误")
                return result
            except urllib.error.HTTPError as exc:
                body = exc.read().decode("utf-8", errors="replace")
                if exc.code == 429 and attempt < attempts:
                    try:
                        retry_after = float(exc.headers.get("Retry-After") or attempt)
                    except (TypeError, ValueError):
                        retry_after = float(attempt)
                    time.sleep(max(1.0, retry_after))
                    continue
                raise RuntimeError(f"Ozon {endpoint} HTTP {exc.code}: {body[:500]}") from exc
        raise RuntimeError(f"Ozon {endpoint} 多次请求均未成功")

    def products(self, offer_ids: Sequence[str]) -> Dict[str, Dict[str, Any]]:
        rows: Dict[str, Dict[str, Any]] = {}
        for group in chunks(sorted(set(offer_ids)), 100):
            response = self.post("/v3/product/info/list", {
                "offer_id": list(group), "product_id": [], "sku": [],
            })
            for item in response.get("items") or []:
                if isinstance(item, dict):
                    rows[str(item.get("offer_id") or "")] = dict(item)
        return rows

    def task(self, task_id: int) -> Dict[str, Any]:
        result = self.post("/v1/product/import/info", {"task_id": task_id}).get("result") or {}
        return {
            "checked_at": now_iso(),
            "items": [
                {
                    "product_id": str(item.get("product_id") or ""),
                    "offer_id": str(item.get("offer_id") or ""),
                    "status": str(item.get("status") or ""),
                    "errors": [dict(error) for error in item.get("errors") or [] if isinstance(error, dict)],
                }
                for item in result.get("items") or []
                if isinstance(item, dict)
            ],
        }


def terminal_task(snapshot: Mapping[str, Any]) -> bool:
    items = list(snapshot.get("items") or [])
    return bool(items) and all(
        str(item.get("status") or "").casefold() in {"imported", "failed", "skipped"}
        for item in items
    )


def source_card_ready(item: Mapping[str, Any] | None) -> bool:
    statuses = (item or {}).get("statuses") or {}
    return bool(
        item
        and str(item.get("sku") or "").isdigit()
        and model_id(item)
        and ordered_images(item)
        and statuses.get("moderate_status") == "approved"
        and statuses.get("validation_status") == "success"
        and not item.get("is_archived")
    )


def snapshot_row(source_sku: str, source: Mapping[str, Any], target: Mapping[str, Any]) -> Dict[str, Any]:
    statuses = target.get("statuses") if isinstance(target.get("statuses"), dict) else {}
    source_title = str(source.get("name") or "").strip()
    target_title = str(target.get("name") or "").strip()
    source_model = model_id(source)
    target_model = model_id(target)
    target_ozon_sku = str(target.get("sku") or "")
    target_image_count = len(ordered_images(target))
    expected_image_count = len(ordered_images(source))
    errors = error_codes(target)
    return {
        "source_sku_id": source_sku,
        "source_offer_id": str(source.get("offer_id") or ""),
        "source_ozon_sku": str(source.get("sku") or ""),
        "source_model_id": source_model,
        "source_title": source_title,
        "target_offer_id": str(target.get("offer_id") or ""),
        "target_product_id": str(target.get("id") or ""),
        "target_ozon_sku": target_ozon_sku,
        "target_model_id": target_model,
        "target_title": target_title,
        "target_price": str(target.get("price") or ""),
        "target_status": str(statuses.get("status_name") or ""),
        "target_validation": str(statuses.get("validation_status") or ""),
        "target_image_count": target_image_count,
        "expected_image_count": expected_image_count,
        "target_has_ozon_sku": target_ozon_sku.isdigit(),
        "model_id_matches_source": bool(source_model and source_model == target_model),
        "title_matches_source": bool(source_title and source_title == target_title),
        "image_count_matches_source": bool(expected_image_count and target_image_count >= expected_image_count),
        "duplicate_error_absent": DUPLICATE_CODE not in errors,
        "shipment_image_error_absent": "image_absent_with_shipment" not in errors,
        "errors": errors,
        "action": pair_action(source, target),
    }


def build_target_plan(
    source_by_sku: Mapping[str, Mapping[str, Any]],
    target_by_sku: Mapping[str, Mapping[str, Any]],
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for source_sku, source in source_by_sku.items():
        target = target_by_sku.get(source_sku)
        if not target:
            raise RuntimeError(f"目标店铺缺少SKU {source_sku} 的原商品草稿")
        if not source_card_ready(source):
            raise RuntimeError(f"1店SKU {source_sku} 还不是可用主卡")
        row = snapshot_row(source_sku, source, target)
        if row["action"] == "BLOCK_DIFFERENT_REMOTE_CARD":
            raise RuntimeError(f"目标货号 {row['target_offer_id']} 已连接到另一张Ozon卡，已停止")
        rows.append(row)
    return rows


def _wait_task(client: OzonClient, task_id: int, timeout_seconds: int) -> Dict[str, Any]:
    started = time.monotonic()
    latest: Dict[str, Any] = {"items": []}
    while time.monotonic() - started < timeout_seconds:
        latest = client.task(task_id)
        if terminal_task(latest):
            return latest
        time.sleep(10)
    return latest


def _wait_cards(
    client: OzonClient,
    offer_ids: Sequence[str],
    predicate,
    timeout_seconds: int,
) -> Dict[str, Dict[str, Any]]:
    started = time.monotonic()
    latest: Dict[str, Dict[str, Any]] = {}
    while time.monotonic() - started < timeout_seconds:
        latest = client.products(offer_ids)
        if len(latest) == len(set(offer_ids)) and all(predicate(latest.get(offer_id) or {}) for offer_id in offer_ids):
            return latest
        time.sleep(10)
    return latest


def _wait_cards_stable(
    client: OzonClient,
    offer_ids: Sequence[str],
    predicate,
    timeout_seconds: int,
    stability_seconds: int,
) -> Dict[str, Dict[str, Any]]:
    started = time.monotonic()
    stable_since: float | None = None
    latest: Dict[str, Dict[str, Any]] = {}
    while time.monotonic() - started < timeout_seconds:
        latest = client.products(offer_ids)
        ok = len(latest) == len(set(offer_ids)) and all(predicate(latest.get(offer_id) or {}) for offer_id in offer_ids)
        if ok:
            stable_since = stable_since or time.monotonic()
            if time.monotonic() - stable_since >= stability_seconds:
                return latest
        else:
            stable_since = None
        time.sleep(10)
    return latest


def card_verified_against_source(source: Mapping[str, Any], target: Mapping[str, Any]) -> bool:
    row = snapshot_row("", source, target)
    return bool(
        row["target_has_ozon_sku"]
        and row["model_id_matches_source"]
        and row["title_matches_source"]
        and row["image_count_matches_source"]
        and row["duplicate_error_absent"]
        and row["shipment_image_error_absent"]
    )


def sync_target(
    client: OzonClient,
    source_by_sku: Mapping[str, Mapping[str, Any]],
    target_offer_map: Mapping[str, str],
    target_cards: Mapping[str, Mapping[str, Any]],
    timeout_seconds: int,
    stability_seconds: int,
) -> Dict[str, Any]:
    target_by_sku = {
        source_sku: target_cards.get(offer_id) or {}
        for source_sku, offer_id in target_offer_map.items()
    }
    plan = build_target_plan(source_by_sku, target_by_sku)
    link_rows = [row for row in plan if row["action"] == "LINK_AND_COPY_IMAGES"]
    task_id = 0
    task_snapshot: Dict[str, Any] = {"items": []}
    if link_rows:
        requests = [
            build_request_item(source_by_sku[row["source_sku_id"]], target_by_sku[row["source_sku_id"]])
            for row in link_rows
        ]
        response = client.post("/v1/product/import-by-sku", {"items": requests})
        result = response.get("result") if isinstance(response.get("result"), dict) else {}
        task_id = int(result.get("task_id") or 0)
        unmatched = list(result.get("unmatched_sku_list") or [])
        if task_id <= 0 or unmatched:
            raise RuntimeError(f"Ozon挂卡未返回有效任务：task_id={task_id}, unmatched={unmatched}")
        task_snapshot = _wait_task(client, task_id, timeout_seconds)
        task_errors = [
            error
            for item in task_snapshot.get("items") or []
            for error in item.get("errors") or []
        ]
        if task_errors or not all(
            str(item.get("status") or "").casefold() == "imported"
            for item in task_snapshot.get("items") or []
        ):
            raise RuntimeError(f"Ozon挂卡任务未全部成功：{task_snapshot}")

    expected_by_offer = {
        target_offer_map[source_sku]: source
        for source_sku, source in source_by_sku.items()
    }
    target_cards = _wait_cards(
        client,
        list(expected_by_offer),
        lambda card: bool(str(card.get("sku") or "").isdigit() and model_id(card)),
        timeout_seconds,
    )
    for offer_id, source in expected_by_offer.items():
        target = target_cards.get(offer_id) or {}
        if model_id(target) != model_id(source) or str(target.get("name") or "") != str(source.get("name") or ""):
            raise RuntimeError(f"目标货号 {offer_id} 没有回读到对应主卡")

    image_writes = 0
    for offer_id, source in expected_by_offer.items():
        target = target_cards[offer_id]
        source_images = ordered_images(source)
        if len(ordered_images(target)) >= len(source_images) and "image_absent_with_shipment" not in error_codes(target):
            continue
        client.post("/v1/product/pictures/import", {
            "product_id": int(target["id"]),
            "images": source_images,
        })
        image_writes += 1
        time.sleep(1)

    target_cards = _wait_cards_stable(
        client,
        list(expected_by_offer),
        lambda card: bool(
            card_verified_against_source(
                expected_by_offer.get(str(card.get("offer_id") or ""), {}),
                card,
            )
        ),
        timeout_seconds,
        stability_seconds,
    )
    verified_rows = []
    for source_sku, source in source_by_sku.items():
        target = target_cards.get(target_offer_map[source_sku]) or {}
        row = snapshot_row(source_sku, source, target)
        row["verified"] = card_verified_against_source(source, target)
        verified_rows.append(row)
    task_ids = [str(task_id)] if task_id else []
    return {
        "task_id": task_id,
        "task_ids": task_ids,
        "task_snapshot": task_snapshot,
        "rows": verified_rows,
        "link_item_count": len(link_rows),
        "image_write_count": image_writes,
        "task_note": "" if task_ids else "No import-by-sku write was needed in this run; cards were already linked before image verification.",
        "verified": bool(verified_rows) and all(row["verified"] for row in verified_rows),
    }


def failure_summary(result: Mapping[str, Any]) -> str:
    rows = list(result.get("rows") or [])
    failed = [row for row in rows if not row.get("verified")]
    codes: Dict[str, int] = {}
    for row in failed:
        for code in row.get("errors") or []:
            codes[str(code)] = codes.get(str(code), 0) + 1
    suffix = ""
    if codes:
        suffix = "；错误：" + "、".join(f"{code}={count}" for code, count in sorted(codes.items()))
    return f"多店Ozon主卡同步回读未通过：{len(failed)}/{len(rows)} 张未通过{suffix}"


def update_local_state(product_dir: Path, shop_id: str, result: Mapping[str, Any], write_count: int) -> None:
    publications = load_publications(product_dir, [shop_id])
    record = publications["stores"][shop_id]
    by_sku = {str(item.get("sku_id") or ""): item for item in record.get("sku_publications") or []}
    task_ids = [str(value) for value in result.get("task_ids") or [] if str(value or "")]
    for row in result.get("rows") or []:
        sku = by_sku.get(str(row.get("source_sku_id") or ""))
        if not sku:
            continue
        sku.update({
            "offer_id": row["target_offer_id"],
            "action": "LINK_BY_OZON_SKU",
            "task_id": str(result.get("task_id") or sku.get("task_id") or "unknown"),
            "sync_task_ids": task_ids,
            "sync_verified": bool(row.get("verified")),
            "sync_checked_at": now(),
            "ozon_product_id": row["target_product_id"],
            "moderation_status": "created" if row.get("verified") else "failed",
            "errors": [] if row.get("verified") else list(row.get("errors") or []),
            "warnings": [],
        })
    record.update({
        "selected": True,
        "status": "SUCCESS" if result.get("verified") else "FAILED",
        "api_write_count": int(record.get("api_write_count") or 0) + write_count,
        "submission_version": int(record.get("submission_version") or 0) + 1,
        "last_submitted_at": now(),
        "last_checked_at": now(),
        "last_error": None if result.get("verified") else failure_summary(result),
        "sync_mode": "source_ozon_sku_with_images_v1",
    })
    save_publications(product_dir, publications)


def save_report(product_dir: Path, suffix: str, payload: Mapping[str, Any]) -> Path:
    directory = product_dir / "output/multi-store-card-sync"
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    path = directory / f"{stamp}-{suffix}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--product-dir", type=Path, required=True)
    parser.add_argument("--source-shop", required=True)
    parser.add_argument("--target-shop", action="append", default=[])
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm", default="")
    parser.add_argument("--timeout-seconds", type=int, default=300)
    parser.add_argument("--stability-seconds", type=int, default=45)
    args = parser.parse_args()

    product_dir = args.product_dir.resolve()
    source_map = offer_map(product_dir, args.source_shop)
    targets = list(dict.fromkeys(args.target_shop))
    if not targets:
        targets = sorted(
            path.parent.name
            for path in (product_dir / "output/store-runs").glob("*/store-offer-id-map.json")
            if path.parent.name != args.source_shop
        )
    if not targets or args.source_shop in targets:
        raise RuntimeError("必须选择至少一个不同于1店的目标店铺")
    if args.apply and args.confirm != CONFIRMATION:
        raise RuntimeError("缺少多店主卡同步确认")
    if args.timeout_seconds < 60:
        raise RuntimeError("回读等待时间不能少于60秒")
    if args.stability_seconds < 0:
        raise RuntimeError("稳定回读时间不能为负数")

    _, credentials = load_shops()
    required_shops = [args.source_shop, *targets]
    missing = [shop for shop in required_shops if shop not in credentials]
    if missing:
        raise RuntimeError("店铺连接不可用：" + "、".join(missing))
    source_client = OzonClient(credentials[args.source_shop], allow_write=False)
    source_cards_by_offer = source_client.products(list(source_map.values()))
    source_by_sku = {
        source_sku: source_cards_by_offer.get(offer_id) or {}
        for source_sku, offer_id in source_map.items()
    }
    for source_sku, card in source_by_sku.items():
        if not source_card_ready(card):
            raise RuntimeError(f"1店SKU {source_sku} 尚未形成可同步主卡")

    preflight_targets = []
    target_clients: Dict[str, OzonClient] = {}
    target_cards_by_shop: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for shop_id in targets:
        mapping = offer_map(product_dir, shop_id)
        if set(mapping) != set(source_map):
            raise RuntimeError(f"店铺 {shop_id} 与1店SKU集合不一致")
        client = OzonClient(credentials[shop_id], allow_write=args.apply)
        cards = client.products(list(mapping.values()))
        target_clients[shop_id] = client
        target_cards_by_shop[shop_id] = cards
        rows = build_target_plan(source_by_sku, {
            source_sku: cards.get(offer_id) or {}
            for source_sku, offer_id in mapping.items()
        })
        preflight_targets.append({
            "shop_id": shop_id,
            "link_count": sum(row["action"] == "LINK_AND_COPY_IMAGES" for row in rows),
            "image_only_count": sum(row["action"] == "COPY_IMAGES_ONLY" for row in rows),
            "already_synced_count": sum(row["action"] == "ALREADY_SYNCED" for row in rows),
            "rows": rows,
        })
    preflight = {
        "schema_version": "1.0.0",
        "mode": "ozon_multi_store_card_sync_preflight",
        "generated_at": now_iso(),
        "product_id": product_dir.name,
        "source_shop_id": args.source_shop,
        "source_is_read_only": True,
        "target_shops": preflight_targets,
        "write_api_calls": 0,
        "inventory_api_calls": 0,
    }
    preflight_path = save_report(product_dir, "preflight", preflight)
    print(json.dumps({
        "phase": "preflight",
        "product_id": product_dir.name,
        "source_shop_id": args.source_shop,
        "target_shop_count": len(targets),
        "target_item_count": sum(len(item["rows"]) for item in preflight_targets),
        "write_api_calls": 0,
        "inventory_api_calls": 0,
        "report": str(preflight_path.relative_to(ROOT)),
    }, ensure_ascii=False), flush=True)
    if not args.apply:
        return 0

    results = []
    for shop_id in targets:
        client = target_clients[shop_id]
        mapping = offer_map(product_dir, shop_id)
        before_writes = client.write_api_calls
        result = sync_target(
            client,
            source_by_sku,
            mapping,
            target_cards_by_shop[shop_id],
            args.timeout_seconds,
            args.stability_seconds,
        )
        writes = client.write_api_calls - before_writes
        result.update({
            "shop_id": shop_id,
            "write_api_calls": writes,
            "inventory_api_calls": 0,
        })
        update_local_state(product_dir, shop_id, result, writes)
        results.append(result)
        print(json.dumps({
            "phase": "target_verified" if result["verified"] else "target_failed",
            "shop_id": shop_id,
            "verified_items": sum(bool(row.get("verified")) for row in result["rows"]),
            "total_items": len(result["rows"]),
            "task_id": result["task_id"],
            "write_api_calls": writes,
            "inventory_api_calls": 0,
        }, ensure_ascii=False), flush=True)
        if not result["verified"]:
            break

    receipt = {
        "schema_version": "1.0.0",
        "mode": "ozon_multi_store_card_sync_receipt",
        "completed_at": now_iso(),
        "product_id": product_dir.name,
        "source_shop_id": args.source_shop,
        "source_is_read_only": True,
        "status": "verified" if len(results) == len(targets) and all(item["verified"] for item in results) else "needs_attention",
        "results": results,
        "write_api_calls": sum(int(item["write_api_calls"]) for item in results),
        "inventory_api_calls": 0,
        "preflight_report": str(preflight_path.relative_to(ROOT)),
    }
    receipt_path = save_report(product_dir, "receipt", receipt)
    print(json.dumps({
        "phase": "complete",
        "status": receipt["status"],
        "verified_shops": sum(bool(item["verified"]) for item in results),
        "target_shops": len(targets),
        "verified_items": sum(sum(bool(row.get("verified")) for row in item["rows"]) for item in results),
        "write_api_calls": receipt["write_api_calls"],
        "inventory_api_calls": 0,
        "report": str(receipt_path.relative_to(ROOT)),
    }, ensure_ascii=False))
    return 0 if receipt["status"] == "verified" else 2


if __name__ == "__main__":
    raise SystemExit(main())
