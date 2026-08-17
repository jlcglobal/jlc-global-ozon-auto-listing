#!/usr/bin/env python3
"""Controlled search-visibility repair for the six non-primary JLC Ozon shops.

This script updates only Ozon attributes 23171 (hashtags) and 4191 (annotation).
It runs each shop independently, keeps a full read-only backup/preflight, and
applies only rows that pass the same field/risk checks as the zhonglian1 repair.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence, Tuple


ROOT = Path(__file__).resolve().parents[1]
REMAINING_SHOPS = ("zhonglian2", "volttech", "zhonglian3", "zhonglian4", "zhonglian5", "jlc-blobal-6")
CONFIRMATION = "APPLY_SIX_SHOPS_SEARCH_VISIBILITY_20260804"

repair_spec = importlib.util.spec_from_file_location(
    "zhonglian1_repair",
    ROOT / "scripts/repair_zhonglian1_search_visibility_fields.py",
)
repair = importlib.util.module_from_spec(repair_spec)
assert repair_spec.loader is not None
repair_spec.loader.exec_module(repair)

generated_spec = importlib.util.spec_from_file_location(
    "generated_intro_repair",
    ROOT / "scripts/repair_zhonglian1_generated_intro_remaining.py",
)
generated = importlib.util.module_from_spec(generated_spec)
assert generated_spec.loader is not None
generated_spec.loader.exec_module(generated)


def set_shop(shop_id: str) -> None:
    repair.SHOP_ID = shop_id


def safe_intro_from_card(card: Mapping[str, Any], query_rows: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    current_intro = str(card.get("current_intro") or "").strip()
    intro_risk = repair.intro_moderation_risk_reason(current_intro)
    policy_risk = final_intro_risk_reason(current_intro) if current_intro else ""
    generated_intro = ""
    generated_reason = ""
    if not current_intro:
        generated_intro = generated.generated_intro(card)
        generated_reason = "missing_current_intro"
    elif intro_risk:
        generated_intro = generated.generated_intro(card)
        generated_reason = intro_risk
    elif policy_risk in {"intro_too_long", "html_in_intro", "intro_policy_marketing", "intro_policy_brand", "intro_policy_bad_text"}:
        generated_intro = generated.generated_intro(card)
        generated_reason = policy_risk

    if generated_intro:
        search_term, row = repair.select_intro_term(card, generated_intro, query_rows)
        supplement = ""
        if search_term and not repair._query_policy_block_reason(search_term):
            supplement = f"По назначению это {search_term}."
        final_intro = f"{generated_intro}\n\n{supplement}" if supplement else generated_intro
        return {
            "status": "generated_from_current_ozon_facts",
            "source_type": "generated_intro_for_missing_or_risky_current",
            "source_report": "",
            "base_intro": "",
            "final_intro": final_intro,
            "supplement": supplement,
            "search_term": search_term,
            "search_count": repair._number(row.get("count")) if row else 0,
            "search_source": row.get("source_kind") if row else "",
            "error": "" if final_intro else "empty_generated_intro",
            "generated_reason": generated_reason,
        }

    intro = repair.build_intro(card, None, query_rows)
    intro["status"] = "current_intro_preserved"
    intro["source_type"] = "current_ozon_intro"
    return intro


def final_intro_risk_reason(value: str) -> str:
    text = str(value or "")
    if not text.strip():
        return "empty_final_intro"
    if re.search(r"</?[a-z][^>]*>", text, flags=re.IGNORECASE):
        return "html_in_intro"
    if len(text) > 900:
        return "intro_too_long"
    blocked = repair._query_policy_block_reason(text)
    if blocked and blocked != "empty":
        return f"intro_policy_{blocked}"
    return ""


def build_shop_preflight(shop_id: str, *, include_query_details: bool = True) -> Tuple[Path, Path, Path, Dict[str, Any], Dict[str, Any]]:
    set_shop(shop_id)
    client = repair.CountingOzonClient(repair.load_credentials(shop_id))
    conflict = repair.active_write_conflicts()
    if conflict["active"]:
        raise RuntimeError("Active production write conflict: " + "; ".join(conflict["reasons"]))

    catalog = repair.list_catalog(client)
    product_ids = sorted({int(item["product_id"]) for item in catalog if str(item.get("product_id") or "").isdigit()})
    offer_ids = [str(item.get("offer_id") or "").strip() for item in catalog if str(item.get("offer_id") or "").strip()]
    info_rows = repair.product_info(client, product_ids)
    attribute_rows = repair.product_attributes(client, offer_ids)
    cards = repair.build_cards(catalog, info_rows, attribute_rows)
    date_from, date_to = repair.search_window()
    if include_query_details:
        api_query_rows_by_sku, api_query_errors = repair.product_query_details(
            client,
            [str(card.get("sku") or "") for card in cards],
            date_from=date_from,
            date_to=date_to,
        )
        api_query_mode = "ozon_product_queries_details"
    else:
        api_query_rows_by_sku = {}
        api_query_errors = [{
            "mode": "skipped",
            "reason": "operator_fast_path_uses_product_facts_when_search_details_are_unavailable_or_slow",
        }]
        api_query_mode = "skipped_operator_fast_path"

    backup = {
        "schema_version": "1.0.0",
        "mode": "six_shop_remote_backup",
        "shop_id": shop_id,
        "generated_at": repair.now_iso(),
        "api_query_date_from": date_from,
        "api_query_date_to": date_to,
        "catalog_card_count": len(catalog),
        "backup_card_count": len(cards),
        "catalog": catalog,
        "product_info": info_rows,
        "product_attributes": attribute_rows,
        "api_top15_search_queries_by_sku": api_query_rows_by_sku,
        "api_top15_search_query_errors": api_query_errors,
        "api_query_mode": api_query_mode,
        "cards": cards,
        "write_api_calls": 0,
        "read_api_calls": client.read_api_calls,
        "inventory_api_calls": client.inventory_api_calls,
    }
    backup_path = repair.save_report(f"{shop_id}-six-shop-backup", backup)

    items: List[Dict[str, Any]] = []
    for card in cards:
        sku = str(card.get("sku") or "").strip()
        api_query_rows = api_query_rows_by_sku.get(sku, [])
        query_rows = api_query_rows
        intro = safe_intro_from_card(card, query_rows)
        final_intro = str(intro.get("final_intro") or "").strip()
        tag_card = dict(card)
        cleaned = generated.cleaned_title(str(card.get("title") or ""))
        if cleaned:
            tag_card["title"] = cleaned
        base_for_tags = final_intro or str(card.get("current_intro") or "")
        candidates = repair.tag_candidates(tag_card, base_for_tags, query_rows)
        extra_candidates = [
            {"tag": repair.canonical_hashtag(phrase), "source": "one_time_product_type", "phrase": phrase}
            for phrase in generated.one_time_extra_phrases(tag_card)
        ]
        candidates = repair.filter_card_candidates([*extra_candidates, *candidates], tag_card)
        merged = repair.merge_tags(card.get("current_subject_tags") or [], candidates, query_rows, tag_card)
        final_tags = [str(tag) for tag in merged.get("final_tags") or []]
        new_tag_details = [dict(item) for item in merged.get("new_tag_details") or [] if isinstance(item, Mapping)]
        new_api_tags = [
            item["tag"] for item in new_tag_details
            if str(item.get("source") or "") in repair.SEARCH_TAG_SOURCES
        ]
        generated_tags = [
            item["tag"] for item in new_tag_details
            if str(item.get("source") or "") not in repair.SEARCH_TAG_SOURCES
        ]

        errors = [value for value in (merged.get("error"), intro.get("error")) if value]
        if len(final_tags) != 30:
            errors.append("final_subject_tag_count_not_30")
        if any(not repair.valid_existing_tag(tag) for tag in final_tags):
            errors.append("invalid_final_subject_tag")
        if len({repair.tag_key(tag) for tag in final_tags}) != len(final_tags):
            errors.append("duplicate_final_subject_tag")
        intro_risk = final_intro_risk_reason(final_intro) or repair.intro_moderation_risk_reason(final_intro)
        if intro_risk:
            errors.append(intro_risk)
        card_risk = repair.card_update_risk_reason(card, final_intro)
        if card_risk:
            errors.append(card_risk)
        tag_risk = repair.final_tag_moderation_risk_reason(final_tags)
        if tag_risk:
            errors.append(tag_risk)
        if str(intro.get("base_intro") or "") and not final_intro.startswith(str(intro.get("base_intro") or "")):
            errors.append("current_intro_prefix_not_preserved")

        current_valid_tags = [tag for tag in card.get("current_subject_tags") or [] if repair.valid_existing_tag(tag)]
        tag_update_required = [repair.tag_key(tag) for tag in final_tags] != [repair.tag_key(tag) for tag in current_valid_tags]
        intro_update_required = final_intro != str(card.get("current_intro") or "").strip()
        row_status = "ready" if not errors else "skipped"
        if row_status == "ready" and not tag_update_required and not intro_update_required:
            row_status = "already_ok"
        items.append({
            "status": row_status,
            "product_id": card.get("product_id") or "",
            "offer_id": card.get("offer_id") or "",
            "sku": card.get("sku") or "",
            "title": card.get("title") or "",
            "current_subject_tags": card.get("current_subject_tags") or [],
            "current_valid_subject_tags": current_valid_tags,
            "current_valid_subject_tag_count": len(current_valid_tags),
            "invalid_current_subject_tags": merged.get("invalid_existing_tags") or [],
            "final_subject_tags": final_tags,
            "final_subject_tag_count": len(final_tags),
            "new_subject_tags": merged.get("new_tags") or [],
            "new_tag_details": new_tag_details,
            "new_api_subject_tags": new_api_tags,
            "generated_subject_tags": generated_tags,
            "new_api_subject_tag_count": len(new_api_tags),
            "generated_subject_tag_count": len(generated_tags),
            "removed_subject_tags": merged.get("removed_tags") or [],
            "subject_tag_strategy": merged.get("strategy") or "",
            "tag_candidates_sample": candidates[:20],
            "current_intro": str(card.get("current_intro") or ""),
            "intro_source_status": intro.get("status") or "",
            "intro_source_type": intro.get("source_type") or "",
            "intro_source_report": intro.get("source_report") or "",
            "base_intro": intro.get("base_intro") or "",
            "final_intro": final_intro,
            "final_intro_length": len(final_intro),
            "intro_supplement": intro.get("supplement") or "",
            "intro_search_term": intro.get("search_term") or "",
            "intro_search_count": intro.get("search_count") or 0,
            "intro_search_source": intro.get("search_source") or "",
            "tag_update_required": tag_update_required,
            "intro_update_required": intro_update_required,
            "requires_update": tag_update_required or intro_update_required,
            "api_top15_query_count": len(api_query_rows),
            "api_top15_queries": api_query_rows,
            "reliable_search_query_count": len(query_rows),
            "query_source_status": "api_top15" if api_query_rows else "generated_from_current_ozon_facts",
            "errors": errors,
        })

    ready_or_ok = [item for item in items if item["status"] in {"ready", "already_ok"}]
    full_preflight = {
        "schema_version": "1.0.0",
        "mode": "six_shop_full_preflight",
        "shop_id": shop_id,
        "generated_at": repair.now_iso(),
        "api_query_date_from": date_from,
        "api_query_date_to": date_to,
        "api_top15_query_error_count": len(api_query_errors),
        "api_top15_query_errors": api_query_errors,
        "api_query_mode": api_query_mode,
        "backup_report": repair.project_relative(backup_path),
        "current_write_conflict": conflict,
        "confirmation_required_for_apply": CONFIRMATION,
        "changes": [f"attribute_{repair.OZON_HASHTAG_ATTRIBUTE_ID}", f"attribute_{repair.OZON_ANNOTATION_ATTRIBUTE_ID}"],
        "untouched": ["title", "price", "images", "brand", "category", "sku", "stock", "warehouse", "activation"],
        "summary": {
            "total_cards": len(items),
            "backup_cards": len(cards),
            "ready_cards": sum(item["status"] == "ready" for item in items),
            "already_ok_cards": sum(item["status"] == "already_ok" for item in items),
            "skipped_cards": sum(item["status"] == "skipped" for item in items),
            "requires_update_cards": sum(bool(item["requires_update"]) and item["status"] == "ready" for item in items),
            "generated_intro_cards": sum(item["intro_source_status"] == "generated_from_current_ozon_facts" for item in items),
            "current_intro_preserved_cards": sum(item["intro_source_status"] == "current_intro_preserved" for item in items),
            "thirty_tag_ready_or_ok_cards": sum(item["final_subject_tag_count"] == 30 and item["status"] in {"ready", "already_ok"} for item in items),
            "api_top15_cards_with_queries": sum(item["api_top15_query_count"] > 0 for item in items),
            "new_api_subject_tag_total": sum(item["new_api_subject_tag_count"] for item in items),
            "generated_subject_tag_total": sum(item["generated_subject_tag_count"] for item in items),
            "coverage_ratio": round(len(ready_or_ok) / len(items), 4) if items else 0,
        },
        "items": items,
        "write_api_calls": 0,
        "read_api_calls": client.read_api_calls,
        "inventory_api_calls": client.inventory_api_calls,
    }
    full_path = repair.save_report(f"{shop_id}-six-shop-full-preflight", full_preflight)
    full_preflight["preflight_report"] = repair.project_relative(full_path)
    full_path.write_text(json.dumps(full_preflight, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    apply_items = [item for item in items if item["status"] == "ready" and item["requires_update"]]
    apply_preflight = dict(full_preflight)
    apply_preflight["mode"] = "six_shop_apply_preflight"
    apply_preflight["source_full_preflight_report"] = repair.project_relative(full_path)
    apply_preflight["items"] = apply_items
    apply_preflight["summary"] = {
        **full_preflight["summary"],
        "total_cards": len(apply_items),
        "backup_cards": len(apply_items),
        "ready_cards": len(apply_items),
        "already_ok_cards": 0,
        "skipped_cards": 0,
        "requires_update_cards": len(apply_items),
        "coverage_ratio": 1.0 if apply_items else 0,
        "full_preflight_coverage_ratio": full_preflight["summary"]["coverage_ratio"],
    }
    apply_path = repair.save_report(f"{shop_id}-six-shop-apply-preflight", apply_preflight)
    apply_preflight["preflight_report"] = repair.project_relative(apply_path)
    apply_path.write_text(json.dumps(apply_preflight, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return backup_path, full_path, apply_path, full_preflight, apply_preflight


def fast_field_audit(shop_id: str) -> Tuple[Path, Dict[str, Any]]:
    set_shop(shop_id)
    client = repair.CountingOzonClient(repair.load_credentials(shop_id))
    catalog = repair.list_catalog(client)
    offer_ids = [str(item.get("offer_id") or "") for item in catalog if item.get("offer_id")]
    attrs = repair.product_attributes(client, offer_ids)
    by_offer = {str(item.get("offer_id") or ""): item for item in attrs}
    items: List[Dict[str, Any]] = []
    for card in catalog:
        offer_id = str(card.get("offer_id") or "")
        attr = by_offer.get(offer_id, {})
        tags = repair.split_tag_values(repair.attribute_value_texts(attr, repair.OZON_HASHTAG_ATTRIBUTE_ID))
        intro_values = repair.attribute_value_texts(attr, repair.OZON_ANNOTATION_ATTRIBUTE_ID)
        intro = intro_values[0] if intro_values else ""
        tag_shape_ok = (
            len(tags) == 30
            and all(str(tag).startswith("#") and repair.tag_body_length(str(tag)) <= repair.OZON_SUBJECT_TAG_MAX_BODY_LENGTH for tag in tags)
        )
        items.append({
            "product_id": str(card.get("product_id") or ""),
            "offer_id": offer_id,
            "tag_count": len(tags),
            "tag_shape_ok": tag_shape_ok,
            "intro_nonempty": bool(str(intro).strip()),
        })
    summary = {
        "shop_id": shop_id,
        "total_cards": len(catalog),
        "attribute_rows_read": len(attrs),
        "exact_30_tag_cards": sum(1 for item in items if item["tag_shape_ok"]),
        "not_exact_30_tag_cards": sum(1 for item in items if not item["tag_shape_ok"]),
        "intro_nonempty_cards": sum(1 for item in items if item["intro_nonempty"]),
        "intro_empty_cards": sum(1 for item in items if not item["intro_nonempty"]),
        "read_api_calls": client.read_api_calls,
        "write_api_calls": client.write_api_calls,
        "inventory_api_calls": client.inventory_api_calls,
    }
    report = {
        "schema_version": "1.0.0",
        "mode": "six_shop_fast_field_audit",
        "shop_id": shop_id,
        "summary": summary,
        "not_exact_30_items": [item for item in items if not item["tag_shape_ok"]],
        "empty_intro_examples": [item for item in items if not item["intro_nonempty"]][:100],
    }
    path = repair.save_report(f"{shop_id}-six-shop-fast-field-audit", report)
    report["report"] = repair.project_relative(path)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path, report


def run_preflight(shops: Sequence[str], *, include_query_details: bool = True) -> Dict[str, Any]:
    results: List[Dict[str, Any]] = []
    for shop_id in shops:
        backup_path, full_path, apply_path, full_preflight, apply_preflight = build_shop_preflight(
            shop_id,
            include_query_details=include_query_details,
        )
        results.append({
            "shop_id": shop_id,
            "backup_report": repair.project_relative(backup_path),
            "full_preflight_report": repair.project_relative(full_path),
            "apply_preflight_report": repair.project_relative(apply_path),
            "full_summary": full_preflight["summary"],
            "apply_summary": apply_preflight["summary"],
            "write_api_calls": 0,
            "read_api_calls": full_preflight["read_api_calls"],
            "inventory_api_calls": full_preflight["inventory_api_calls"],
            "api_query_mode": full_preflight.get("api_query_mode") or "",
        })
        print(json.dumps({"mode": "preflight_done", **results[-1]}, ensure_ascii=False), flush=True)
    summary = {
        "shops": list(shops),
        "shop_count": len(shops),
        "total_cards": sum(int(item["full_summary"].get("total_cards") or 0) for item in results),
        "ready_to_update_cards": sum(int(item["apply_summary"].get("requires_update_cards") or 0) for item in results),
        "already_ok_cards": sum(int(item["full_summary"].get("already_ok_cards") or 0) for item in results),
        "skipped_cards": sum(int(item["full_summary"].get("skipped_cards") or 0) for item in results),
        "write_api_calls": 0,
        "inventory_api_calls": sum(int(item.get("inventory_api_calls") or 0) for item in results),
    }
    payload = {"schema_version": "1.0.0", "mode": "six_shop_preflight_summary", "summary": summary, "shops": results}
    path = repair.save_report("six-shop-preflight-summary", payload)
    payload["summary_report"] = repair.project_relative(path)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"mode": "preflight_summary", "summary_report": payload["summary_report"], "summary": summary}, ensure_ascii=False), flush=True)
    return payload


def run_apply(preflight_summary_path: Path, *, batch_size: int, verify_seconds: int, min_coverage: float = 0.95) -> Dict[str, Any]:
    payload = json.loads(preflight_summary_path.read_text(encoding="utf-8"))
    results: List[Dict[str, Any]] = []
    for shop in payload.get("shops") or []:
        shop_id = str(shop.get("shop_id") or "")
        apply_report = str(shop.get("apply_preflight_report") or "")
        apply_summary = shop.get("apply_summary") or {}
        if int(apply_summary.get("requires_update_cards") or 0) <= 0:
            results.append({"shop_id": shop_id, "status": "no_ready_updates", "write_api_calls": 0, "inventory_api_calls": 0})
            continue
        set_shop(shop_id)
        preflight_path, preflight = repair.load_preflight(apply_report)
        receipt = repair.apply_rows(
            repair.CountingOzonClient(repair.load_credentials(shop_id)),
            preflight_path,
            preflight,
            scope="all",
            batch_size=batch_size,
            verify_seconds=verify_seconds,
            min_coverage=min_coverage,
        )
        result = {
            "shop_id": shop_id,
            "status": receipt["status"],
            "submitted_card_count": receipt["submitted_card_count"],
            "processed_card_count": receipt["processed_card_count"],
            "card_success_count": receipt["card_success_count"],
            "field_readback_match_count": receipt["field_readback_match_count"],
            "field_readback_mismatch_count": receipt["field_readback_mismatch_count"],
            "field_success_ratio": receipt["field_success_ratio"],
            "task_ids": receipt["task_ids"],
            "write_api_calls": receipt["write_api_calls"],
            "read_api_calls": receipt["read_api_calls"],
            "inventory_api_calls": receipt["inventory_api_calls"],
            "receipt_report": receipt["receipt_report"],
        }
        results.append(result)
        print(json.dumps({"mode": "apply_done", **result}, ensure_ascii=False), flush=True)
        if receipt["status"] != "verified":
            break

    audits: List[Dict[str, Any]] = []
    for result in results:
        shop_id = str(result.get("shop_id") or "")
        if not shop_id:
            continue
        audit_path, audit = fast_field_audit(shop_id)
        audits.append({
            "shop_id": shop_id,
            "audit_report": repair.project_relative(audit_path),
            "summary": audit["summary"],
        })
        print(json.dumps({"mode": "audit_done", "shop_id": shop_id, "audit_report": repair.project_relative(audit_path), "summary": audit["summary"]}, ensure_ascii=False), flush=True)

    summary = {
        "shops_attempted": len(results),
        "verified_shops": sum(1 for item in results if item.get("status") in {"verified", "no_ready_updates"}),
        "submitted_card_count": sum(int(item.get("submitted_card_count") or 0) for item in results),
        "card_success_count": sum(int(item.get("card_success_count") or 0) for item in results),
        "field_readback_match_count": sum(int(item.get("field_readback_match_count") or 0) for item in results),
        "field_readback_mismatch_count": sum(int(item.get("field_readback_mismatch_count") or 0) for item in results),
        "write_api_calls": sum(int(item.get("write_api_calls") or 0) for item in results),
        "read_api_calls": sum(int(item.get("read_api_calls") or 0) for item in results),
        "inventory_api_calls": sum(int(item.get("inventory_api_calls") or 0) for item in results),
    }
    final = {
        "schema_version": "1.0.0",
        "mode": "six_shop_apply_summary",
        "preflight_summary_report": repair.project_relative(preflight_summary_path),
        "summary": summary,
        "shops": results,
        "audits": audits,
    }
    path = repair.save_report("six-shop-apply-summary", final)
    final["summary_report"] = repair.project_relative(path)
    path.write_text(json.dumps(final, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"mode": "apply_summary", "summary_report": final["summary_report"], "summary": summary}, ensure_ascii=False), flush=True)
    return final


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--preflight-summary", default="")
    parser.add_argument("--confirm", default="")
    parser.add_argument("--shops", nargs="*", default=list(REMAINING_SHOPS))
    parser.add_argument("--skip-query-details", action="store_true")
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--verify-seconds", type=int, default=300)
    parser.add_argument("--min-coverage", type=float, default=0.95)
    args = parser.parse_args()

    if args.preflight == args.apply:
        parser.error("Use exactly one of --preflight or --apply")
    shops = [str(shop).strip() for shop in args.shops if str(shop).strip()]
    invalid = [shop for shop in shops if shop not in REMAINING_SHOPS]
    if invalid:
        raise RuntimeError(f"These shops are not in the six-shop allowlist: {invalid}")
    if "zhonglian1" in shops:
        raise RuntimeError("zhonglian1 is already complete and is not allowed in this script")

    if args.preflight:
        run_preflight(shops, include_query_details=not args.skip_query_details)
        return 0

    if args.confirm != CONFIRMATION:
        raise RuntimeError(f"Explicit confirmation is required: {CONFIRMATION}")
    if not args.preflight_summary:
        raise RuntimeError("--preflight-summary is required for apply")
    if args.verify_seconds < 300:
        raise RuntimeError("Delayed field verification must run for at least 300 seconds")
    final = run_apply(
        ROOT / args.preflight_summary if not Path(args.preflight_summary).is_absolute() else Path(args.preflight_summary),
        batch_size=args.batch_size,
        verify_seconds=args.verify_seconds,
        min_coverage=args.min_coverage,
    )
    return 0 if final["summary"]["field_readback_mismatch_count"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
