import asyncio
import copy
import json
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "market-intelligence"))

from market_intelligence import (  # noqa: E402
    MarketEnricher,
    MarketStore,
    OzonAnalyticsPermissionError,
    OzonAnalyticsReadOnlyClient,
    calculate_index,
    build_trend_report,
    build_search_visibility_plan,
    build_traffic_performance_plan,
    collect_seller_search_visibility,
    extract_public_image_url,
    generate_local_keyword_records,
    normalize_seerfar_keyword_rows,
    normalize_traffic_item,
    parse_ozon_product_query_text,
    parse_yandex_wordstat_text,
    probe_ozon_sources,
)
from market_intelligence.bestsellers_import import normalize_row  # noqa: E402
from market_intelligence.search_queries_import import normalize_query_row  # noqa: E402
import market_intelligence.storage as storage_module  # noqa: E402


class _FakeAsyncRequest:
    """Test double for FastAPI Request.json(); payload is fixed per test."""

    def __init__(self, payload=None):
        self._payload = payload if payload is not None else {}

    async def json(self):
        return self._payload


class FakeAnalyticsTransport:
    def __init__(self, premium_required=False, empty_query_details=False):
        self.calls = []
        self.premium_required = premium_required
        self.empty_query_details = empty_query_details

    def __call__(self, endpoint, payload):
        self.calls.append((endpoint, copy.deepcopy(payload)))
        if endpoint == OzonAnalyticsReadOnlyClient.PRODUCT_LIST_ENDPOINT:
            return {
                "result": {
                    "total": 741,
                    "items": [{"offer_id": "offer-1", "product_id": 4259045262}],
                    "last_id": "next",
                }
            }
        if endpoint == OzonAnalyticsReadOnlyClient.PRODUCT_INFO_ENDPOINT:
            return {"items": [{
                "id": 4259045262,
                "offer_id": "offer-1",
                "sku": 4042683166,
                "name": "Термос",
                "primary_image": "https://cdn.example.com/thermos.jpg",
                "marketing_price": "1690",
                "currency_code": "RUB",
                "created_at": "2026-04-20T10:00:00Z",
                "updated_at": "2026-04-22T08:17:12Z",
                "depth": 220,
                "width": 80,
                "height": 80,
                "dimension_unit": "mm",
                "weight": 320,
                "weight_unit": "g",
            }]}
        if endpoint == OzonAnalyticsReadOnlyClient.PRODUCT_ATTRIBUTES_ENDPOINT:
            return {
                "result": {
                    "items": [{
                        "product_id": 4259045262,
                        "offer_id": "offer-1",
                        "attributes": [{
                            "id": 23171,
                            "name": "Хештеги",
                            "values": [{"value": "#термос"}, {"value": "#походный"}],
                        }],
                    }],
                },
            }
        if endpoint == OzonAnalyticsReadOnlyClient.PRODUCT_QUERIES_ENDPOINT:
            if self.premium_required:
                raise OzonAnalyticsPermissionError(
                    endpoint,
                    "Analytics for the specified period is available starting from the premium subscription",
                    403,
                )
            return {"total": 1, "items": [{"sku": 4042683166, "name": "Термос", "unique_search_users": 50}]}
        if endpoint == OzonAnalyticsReadOnlyClient.PRODUCT_QUERY_DETAILS_ENDPOINT:
            if self.premium_required:
                raise OzonAnalyticsPermissionError(
                    endpoint,
                    "Analytics for the specified period is available starting from the premium subscription",
                    403,
                )
            if self.empty_query_details:
                return {"total": 0, "queries": []}
            return {
                "total": 1,
                "queries": [{
                    "query": "термос",
                    "sku": 4042683166,
                    "unique_search_users": 50,
                    "unique_view_users": 12,
                    "order_count": 2,
                    "gmv": 1800,
                    "position": 17,
                    "query_index": 42,
                    "view_conversion": 0.24,
                }],
            }
        if endpoint == OzonAnalyticsReadOnlyClient.FBO_POSTING_ENDPOINT:
            return {"result": [{
                "posting_number": "FBO-1",
                "products": [{
                    "sku": 4042683166,
                    "offer_id": "offer-1",
                    "product_id": 4259045262,
                    "quantity": 3,
                }],
            }]}
        if endpoint == OzonAnalyticsReadOnlyClient.FBS_POSTING_ENDPOINT:
            return {"result": {"postings": []}}
        raise AssertionError(f"Unexpected endpoint: {endpoint}")


class MarketIntelligenceTest(unittest.TestCase):
    def test_market_store_closes_every_connection_across_repeated_calls(self):
        opened = []

        class TrackingConnection(sqlite3.Connection):
            closed_by_store = False

            def close(self):
                self.closed_by_store = True
                return super().close()

        real_connect = storage_module.sqlite3.connect

        def tracked_connect(*args, **kwargs):
            kwargs["factory"] = TrackingConnection
            connection = real_connect(*args, **kwargs)
            opened.append(connection)
            return connection

        with tempfile.TemporaryDirectory() as temporary, patch.object(
            storage_module.sqlite3, "connect", side_effect=tracked_connect
        ):
            store = MarketStore(Path(temporary) / "market.sqlite")
            store.initialize()
            for _ in range(10):
                store.list_source_status()
        self.assertGreaterEqual(len(opened), 11)
        self.assertTrue(all(connection.closed_by_store for connection in opened))

    def test_index_uses_only_available_evidence_and_reports_completeness(self):
        result = calculate_index(
            {"official_rank": 80, "search_demand": None, "review_signal": 60},
            {"official_rank": 50, "search_demand": 30, "review_signal": 20},
        )
        self.assertEqual(result["score"], 74.3)
        self.assertEqual(result["data_completeness"], 70)
        self.assertEqual(result["missing_components"], ["search_demand"])

    def test_index_is_unknown_when_every_component_is_missing(self):
        result = calculate_index({"signal": None}, {"signal": 100})
        self.assertEqual(result["score"], "unknown")
        self.assertEqual(result["data_completeness"], 0)

    def test_store_persists_safe_source_status_and_empty_counts(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = MarketStore(Path(temporary) / "market.sqlite")
            store.initialize()
            store.upsert_source_status({
                "source_id": "ozon_seller_catalog",
                "state": "connected",
                "access_level": "official_read_only",
                "message_zh": "只读连接正常",
                "checked_at": "2026-07-13T08:00:00+08:00",
                "details": {"product_count": 741},
            })
            self.assertEqual(store.list_source_status()[0]["details"]["product_count"], 741)
            self.assertEqual(store.counts(), {"products": 0, "snapshots": 0, "keywords": 0, "favorites": 0})

    def test_official_bestseller_row_is_saved_as_fact_snapshot(self):
        headers = [
            "Название товара", "Артикул Ozon", "Ссылка на товар", "Бренд",
            "Категория 1 уровня", "Категория 3 уровня", "Заказано товаров",
            "Схема работы", "Динамика суммы заказов, %",
        ]
        values = [
            "Канистра 20 л", 2796005351, "https://www.ozon.ru/product/2796005351",
            "3TON", "Автотовары", "Канистра для топлива", 18293, "FBO", 4.18,
        ]
        record = normalize_row(headers, values, "2026-07-13T08:00:00+08:00")
        self.assertEqual(record["title_zh"], "unknown")
        self.assertEqual(record["facts"]["ordered_units"], 18293)
        self.assertEqual(record["facts"]["ordered_amount_growth_percent"], 418)
        with tempfile.TemporaryDirectory() as temporary:
            store = MarketStore(Path(temporary) / "market.sqlite")
            store.initialize()
            store.upsert_product_snapshot(record, "2026-07-13")
            self.assertEqual(store.counts()["products"], 1)
            self.assertEqual(store.counts()["snapshots"], 1)

    def test_store_returns_category_rankings_and_product_detail(self):
        headers = [
            "Название товара", "Артикул Ozon", "Ссылка на товар", "Бренд",
            "Категория 1 уровня", "Категория 3 уровня", "Заказано товаров",
            "Заказано на сумму, ₽", "Схема работы", "Динамика суммы заказов, %",
            "Объем товара, л", "Доля затрат на Ozon, FBS, %", "Доля выкупа, %",
        ]
        first = normalize_row(headers, [
            "Органайзер для дома", 1001, "https://www.ozon.ru/product/1001", "unknown",
            "Дом и сад", "Органайзер", 900, 900000, "FBS", 0.2, 2.5, 0.18, 0.92,
        ], "2026-07-13T08:00:00+08:00")
        second = normalize_row(headers, [
            "Полка для дома", 1002, "https://www.ozon.ru/product/1002", "unknown",
            "Дом и сад", "Полка", 700, 500000, "FBO", 1.8, 4, 0.22, 0.88,
        ], "2026-07-13T08:00:00+08:00")
        with tempfile.TemporaryDirectory() as temporary:
            store = MarketStore(Path(temporary) / "market.sqlite")
            store.initialize()
            store.upsert_product_snapshot(first, "2026-07-13")
            store.upsert_product_snapshot(second, "2026-07-13")
            rule = {"key": "home", "source_level_1": ["Дом и сад"]}
            hot = store.list_ranked_products(rule, ranking="hot")
            rising = store.list_ranked_products(rule, ranking="rising")
            detail = store.get_product("1001")
        self.assertEqual(hot["items"][0]["source_product_id"], "1001")
        self.assertEqual(rising["items"][0]["source_product_id"], "1002")
        self.assertEqual(detail["title_ru"], "Органайзер для дома")
        self.assertEqual(detail["keywords"], [])
        self.assertIn(detail["fbs_assessment"]["recommendation"], {"recommended", "caution"})

    def test_report_reimport_does_not_erase_cached_public_image(self):
        headers = [
            "Название товара", "Артикул Ozon", "Ссылка на товар", "Категория 1 уровня",
            "Категория 3 уровня", "Заказано товаров", "Заказано на сумму, ₽", "Схема работы",
        ]
        record = normalize_row(headers, [
            "Органайзер", 1101, "https://www.ozon.ru/product/1101", "Дом и сад",
            "Органайзер", 100, 50000, "FBS",
        ], "2026-07-13T08:00:00+08:00")
        with tempfile.TemporaryDirectory() as temporary:
            store = MarketStore(Path(temporary) / "market.sqlite")
            store.initialize()
            store.upsert_product_snapshot(record, "2026-07-13")
            store.set_product_image(record["product_key"], "/api/workbench/market-intelligence/images/1101")
            store.upsert_product_snapshot(record, "2026-07-14")
            product = store.get_product("1101")
        self.assertEqual(product["image_url"], "/api/workbench/market-intelligence/images/1101")

    def test_local_keywords_are_deterministic_and_keep_local_provenance(self):
        product = {
            "product_key": "ozon:1201", "source_product_id": "1201", "category_key": "home",
            "title_ru": "Органайзер для одежды с ящиками",
            "facts": {"category_level_3": "Органайзер для хранения"},
        }
        first = generate_local_keyword_records(product, "2026-07-13T08:00:00+08:00")
        second = generate_local_keyword_records(product, "2026-07-13T08:00:00+08:00")
        self.assertEqual(first, second)
        self.assertGreaterEqual(len(first), 15)
        self.assertTrue(all(item["evidence"]["source"] == "local_product_analysis" for item in first))
        self.assertTrue(all("official" not in item["relationship"]["method"] for item in first))

    def test_public_image_extractor_accepts_only_real_ozon_cdn_urls(self):
        valid = "https://cdn1.ozone.ru/s3/multimedia-test/product-main.jpg"
        self.assertEqual(extract_public_image_url(f'<meta property="og:image" content="{valid}">'), valid)
        self.assertIsNone(extract_public_image_url(
            '<meta property="og:image" content="https://cdn2.ozone.ru/s3/abt-challenge/incidents/images/warn.png">'
        ))
        self.assertIsNone(extract_public_image_url(
            '<meta property="og:image" content="https://images.example.com/similar-product.jpg">'
        ))

    def test_enricher_caches_verified_image_and_keeps_failed_image_unknown(self):
        headers = [
            "Название товара", "Артикул Ozon", "Ссылка на товар", "Категория 1 уровня",
            "Категория 3 уровня", "Заказано товаров", "Заказано на сумму, ₽", "Схема работы",
        ]
        record = normalize_row(headers, [
            "Органайзер", 1301, "https://www.ozon.ru/product/1301", "Дом и сад",
            "Органайзер", 100, 50000, "FBS",
        ], "2026-07-13T08:00:00+08:00")
        image_url = "https://cdn1.ozone.ru/s3/multimedia-test/product-main.jpg"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = MarketStore(root / "market.sqlite")
            store.initialize()
            store.upsert_product_snapshot(record, "2026-07-13")
            enricher = MarketEnricher(
                store, root / "images",
                page_fetcher=lambda _url, _timeout: f'<meta property="og:image" content="{image_url}">',
                image_fetcher=lambda _url, _timeout: (b"\xff\xd8\xff" + b"x" * 300, "image/jpeg"),
            )
            product = enricher.enrich_product("1301")
            cached_path = Path(store.get_product_enrichment("1301")["image_local_path"])
            self.assertTrue(cached_path.is_file())
            self.assertEqual(product["image_url"], "/api/workbench/market-intelligence/images/1301")
            self.assertGreaterEqual(len(product["keywords"]), 15)

            store.set_product_image(record["product_key"], "unknown")
            failing = MarketEnricher(
                store, root / "failed-images",
                page_fetcher=lambda _url, _timeout: (_ for _ in ()).throw(OSError("blocked")),
            )
            state = failing.enrich_image("1301", force=True)
            self.assertEqual(state, "syncing")
            self.assertEqual(store.get_product("1301")["image_url"], "unknown")
            self.assertFalse((root / "failed-images").exists())

    def test_local_matching_does_not_attach_unrelated_category_search_terms(self):
        headers = [
            "Название товара", "Артикул Ozon", "Ссылка на товар", "Категория 1 уровня",
            "Категория 3 уровня", "Заказано товаров", "Заказано на сумму, ₽", "Схема работы",
        ]
        product = normalize_row(headers, [
            "Аэрогриль электрический", 1401, "https://www.ozon.ru/product/1401", "Бытовая техника",
            "Аэрогриль", 100, 50000, "FBS",
        ], "2026-07-13T08:00:00+08:00")
        metrics = ["100", "20", "20%", "5000 ₽", "10", "5"]
        relevant = normalize_query_row({"query_ru": "аэрогриль электрический", "metrics": metrics}, 1, "2026-07-13T08:00:00+08:00")
        unrelated = normalize_query_row({"query_ru": "чайник электрический", "metrics": metrics}, 2, "2026-07-13T08:00:00+08:00")
        wrong_size = normalize_query_row({"query_ru": "аэрогриль электрический 8 л", "metrics": metrics}, 3, "2026-07-13T08:00:00+08:00")
        with tempfile.TemporaryDirectory() as temporary:
            store = MarketStore(Path(temporary) / "market.sqlite")
            store.initialize()
            store.upsert_product_snapshot(product, "2026-07-13")
            store.upsert_keyword(relevant)
            store.upsert_keyword(unrelated)
            store.upsert_keyword(wrong_size)
            MarketEnricher(store, Path(temporary) / "images").enrich_keywords("1401")
            terms = {item["keyword_ru"] for item in store.get_product("1401")["keywords"]}
        self.assertIn("аэрогриль электрический", terms)
        self.assertNotIn("чайник электрический", terms)
        self.assertNotIn("аэрогриль электрический 8 л", terms)

    def test_daily_enrichment_batch_respects_keyword_and_image_limits(self):
        headers = [
            "Название товара", "Артикул Ozon", "Ссылка на товар", "Категория 1 уровня",
            "Категория 3 уровня", "Заказано товаров", "Заказано на сумму, ₽", "Схема работы",
        ]
        first = normalize_row(headers, ["Органайзер", 1501, "https://www.ozon.ru/product/1501", "Дом и сад", "Органайзер", 10, 1000, "FBS"], "2026-07-13T08:00:00+08:00")
        second = normalize_row(headers, ["Полка", 1502, "https://www.ozon.ru/product/1502", "Дом и сад", "Полка", 10, 1000, "FBS"], "2026-07-13T08:00:00+08:00")
        with tempfile.TemporaryDirectory() as temporary:
            store = MarketStore(Path(temporary) / "market.sqlite")
            store.initialize()
            store.upsert_product_snapshot(first, "2026-07-13")
            store.upsert_product_snapshot(second, "2026-07-13")
            enricher = MarketEnricher(
                store, Path(temporary) / "images",
                page_fetcher=lambda _url, _timeout: (_ for _ in ()).throw(OSError("blocked")),
            )
            result = enricher.enrich_batch(keyword_limit=1, image_limit=1)
            ready_keywords = sum(
                store.get_product_enrichment(product_id)["keyword_state"] == "ready"
                for product_id in ("1501", "1502")
            )
        self.assertEqual(result["keyword_processed"], 1)
        self.assertEqual(result["image_processed"], 1)
        self.assertEqual(result["image_syncing"], 1)
        self.assertEqual(ready_keywords, 1)

    def test_category_keyword_filter_can_be_restricted_by_level_one(self):
        headers = [
            "Название товара", "Артикул Ozon", "Ссылка на товар", "Категория 1 уровня",
            "Категория 3 уровня", "Заказано товаров", "Заказано на сумму, ₽", "Схема работы",
        ]
        kitchen = normalize_row(headers, ["Плита кухонная", 2001, "https://www.ozon.ru/product/2001", "Бытовая техника", "Плита", 10, 1000, "FBS"], "2026-07-13T08:00:00+08:00")
        construction = normalize_row(headers, ["Виброплита", 2002, "https://www.ozon.ru/product/2002", "Строительство и ремонт", "Виброплита", 20, 2000, "FBS"], "2026-07-13T08:00:00+08:00")
        with tempfile.TemporaryDirectory() as temporary:
            store = MarketStore(Path(temporary) / "market.sqlite")
            store.initialize()
            store.upsert_product_snapshot(kitchen, "2026-07-13")
            store.upsert_product_snapshot(construction, "2026-07-13")
            result = store.list_ranked_products({
                "key": "kitchen", "source_level_3_contains": ["плита"],
                "source_level_1_restrict": ["Бытовая техника"],
            })
        self.assertEqual([item["source_product_id"] for item in result["items"]], ["2001"])

    def test_official_search_query_metrics_and_title_links_are_preserved(self):
        keyword = normalize_query_row({
            "query_ru": "аэрогриль электрический",
            "metrics": ["46 028", "11 336", "24,6 %", "6 089,8 ₽", "42", "23"],
        }, 2, "2026-07-13T08:25:00+08:00")
        self.assertEqual(keyword["keyword_zh"], "电动空气炸锅")
        self.assertEqual(keyword["metrics"]["popularity"], 46028)
        self.assertEqual(keyword["metrics"]["add_to_cart_conversion_percent"], 24.6)
        headers = ["Название товара", "Артикул Ozon", "Ссылка на товар", "Категория 1 уровня", "Категория 3 уровня", "Заказано товаров", "Заказано на сумму, ₽", "Схема работы"]
        product = normalize_row(headers, ["Аэрогриль электрический 8 л", 3001, "https://www.ozon.ru/product/3001", "Бытовая техника", "Аэрогриль", 100, 50000, "FBS"], "2026-07-13T08:00:00+08:00")
        with tempfile.TemporaryDirectory() as temporary:
            store = MarketStore(Path(temporary) / "market.sqlite")
            store.initialize()
            store.upsert_product_snapshot(product, "2026-07-13")
            store.upsert_keyword(keyword)
            linked = store.link_keyword_to_matching_products(keyword["keyword_key"], keyword["keyword_ru"])
            detail = store.get_product("3001")
            listed = store.list_keywords("kitchen")
        self.assertEqual(linked, 1)
        self.assertEqual(detail["keywords"][0]["keyword_ru"], "аэрогриль электрический")
        self.assertEqual(listed[0]["metrics"]["average_buyer_price_rub"], 6089.8)

    def test_daily_trend_report_compares_distinct_snapshot_dates(self):
        headers = [
            "Название товара", "Артикул Ozon", "Ссылка на товар", "Категория 1 уровня",
            "Категория 3 уровня", "Заказано товаров", "Заказано на сумму, ₽", "Схема работы",
        ]
        first = normalize_row(headers, ["Аэрогриль", 4001, "https://www.ozon.ru/product/4001", "Бытовая техника", "Аэрогриль", 100, 50000, "FBS"], "2026-07-12T08:00:00+08:00")
        second = normalize_row(headers, ["Аэрогриль", 4001, "https://www.ozon.ru/product/4001", "Бытовая техника", "Аэрогриль", 160, 90000, "FBS"], "2026-07-13T08:00:00+08:00")
        with tempfile.TemporaryDirectory() as temporary:
            store = MarketStore(Path(temporary) / "market.sqlite")
            store.initialize()
            store.upsert_product_snapshot(first, "2026-07-12")
            store.upsert_product_snapshot(second, "2026-07-13")
            report = build_trend_report(store, "2026-07-13T08:00:00+08:00")
        self.assertEqual(report["state"], "ready")
        self.assertEqual(report["days_collected"], 2)
        self.assertEqual(report["top_sales_amount_increase"][0]["ordered_amount_change_rub"], 40000)
        self.assertEqual(report["top_unit_increase"][0]["ordered_units_change"], 60)

    def test_store_rejects_secret_like_status_details(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = MarketStore(Path(temporary) / "market.sqlite")
            store.initialize()
            with self.assertRaisesRegex(ValueError, "credentials"):
                store.upsert_source_status({
                    "source_id": "bad",
                    "state": "error",
                    "access_level": "unavailable",
                    "message_zh": "bad",
                    "checked_at": "2026-07-13T08:00:00+08:00",
                    "details": {"api_key": "must-not-save"},
                })

    def test_analytics_client_allowlist_rejects_write_endpoints(self):
        transport = FakeAnalyticsTransport()
        client = OzonAnalyticsReadOnlyClient("client", "secret", transport=transport)
        with self.assertRaisesRegex(ValueError, "read-only allowlist"):
            client._post_json("/v3/product/import", {})
        self.assertEqual(transport.calls, [])
        self.assertNotIn("secret", repr(client))

    def test_probe_records_connected_catalog_and_free_login_requirement(self):
        transport = FakeAnalyticsTransport()
        client = OzonAnalyticsReadOnlyClient("client", "secret", transport=transport)
        records = probe_ozon_sources(
            client,
            checked_at=datetime(2026, 7, 13, tzinfo=timezone.utc),
        )
        self.assertEqual(records["ozon_seller_catalog"]["details"]["product_count"], 741)
        self.assertEqual(records["ozon_product_queries"]["state"], "connected")
        self.assertEqual(records["ozon_product_query_details"]["state"], "connected")
        self.assertEqual(records["ozon_product_query_details"]["details"]["query_count"], 1)
        self.assertEqual(records["ozon_free_market_analytics"]["state"], "login_required")
        detail_payload = next(
            payload
            for endpoint, payload in transport.calls
            if endpoint == OzonAnalyticsReadOnlyClient.PRODUCT_QUERY_DETAILS_ENDPOINT
        )
        self.assertEqual(detail_payload["date_from"], "2026-07-03T00:00:00Z")
        self.assertEqual(detail_payload["date_to"], "2026-07-10T00:00:00Z")
        self.assertEqual(detail_payload["page"], 0)

    def test_probe_records_premium_requirement_without_leaking_error_text(self):
        transport = FakeAnalyticsTransport(premium_required=True)
        client = OzonAnalyticsReadOnlyClient("client", "secret", transport=transport)
        records = probe_ozon_sources(
            client,
            checked_at=datetime(2026, 7, 13, tzinfo=timezone.utc),
        )
        record = records["ozon_product_queries"]
        self.assertEqual(record["state"], "premium_required")
        self.assertEqual(record["details"], {"http_status": 403})
        detail_record = records["ozon_product_query_details"]
        self.assertEqual(detail_record["state"], "premium_required")
        self.assertEqual(detail_record["details"], {"http_status": 403})

    def test_probe_records_empty_query_details_separately_from_permission_failure(self):
        transport = FakeAnalyticsTransport(empty_query_details=True)
        client = OzonAnalyticsReadOnlyClient("client", "secret", transport=transport)
        records = probe_ozon_sources(
            client,
            checked_at=datetime(2026, 7, 13, tzinfo=timezone.utc),
        )
        record = records["ozon_product_query_details"]
        self.assertEqual(record["state"], "empty")
        self.assertEqual(record["access_level"], "official_read_only")
        self.assertEqual(record["details"], {"query_count": 0})

    def test_status_schema_accepts_probe_records(self):
        from jsonschema import Draft202012Validator

        schema = json.loads((ROOT / "templates/market-source-status.schema.json").read_text(encoding="utf-8"))
        records = probe_ozon_sources(
            OzonAnalyticsReadOnlyClient("client", "secret", transport=FakeAnalyticsTransport()),
            checked_at=datetime(2026, 7, 13, tzinfo=timezone.utc),
        )
        validator = Draft202012Validator(schema)
        for record in records.values():
            self.assertEqual(list(validator.iter_errors(record)), [])

    def test_workbench_status_endpoint_exposes_no_fake_rankings(self):
        app_path = ROOT / "collector/local-ingest/app.py"
        import importlib.util

        spec = importlib.util.spec_from_file_location("market_workbench_app", app_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as temporary:
            db_path = Path(temporary) / "market.sqlite"
            categories_path = Path(temporary) / "categories.json"
            categories_path.write_text(json.dumps({
                "schema_version": "1.0.0",
                "categories": [{"key": "home", "name_zh": "家居", "name_ru": "Дом и сад", "enabled": True}],
            }, ensure_ascii=False), encoding="utf-8")
            with patch.object(module, "MARKET_DB_PATH", db_path), patch.object(module, "MARKET_CATEGORIES_PATH", categories_path):
                result = module.workbench_market_intelligence_status()
        self.assertFalse(result["ranking_available"])
        self.assertEqual(result["counts"]["products"], 0)
        self.assertEqual(result["categories"][0]["name_zh"], "家居")

    def test_workbench_market_products_exposes_30_day_data_and_holds_7_day(self):
        app_path = ROOT / "collector/local-ingest/app.py"
        import importlib.util

        spec = importlib.util.spec_from_file_location("market_workbench_products_app", app_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as temporary:
            db_path = Path(temporary) / "market.sqlite"
            categories_path = Path(temporary) / "categories.json"
            categories_path.write_text(json.dumps({"categories": [{
                "key": "home", "name_zh": "家居", "enabled": True,
                "source_level_1": ["Дом и сад"],
            }]}, ensure_ascii=False), encoding="utf-8")
            store = MarketStore(db_path)
            store.initialize()
            record = normalize_row([
                "Название товара", "Артикул Ozon", "Ссылка на товар", "Категория 1 уровня",
                "Категория 3 уровня", "Заказано товаров", "Заказано на сумму, ₽", "Схема работы",
            ], [
                "Органайзер", 1001, "https://www.ozon.ru/product/1001", "Дом и сад",
                "Органайзер", 100, 50000, "FBS",
            ], "2026-07-13T08:00:00+08:00")
            store.upsert_product_snapshot(record, "2026-07-13")
            with patch.object(module, "MARKET_DB_PATH", db_path), patch.object(module, "MARKET_CATEGORIES_PATH", categories_path), patch.object(module.MarketEnricher, "enrich_image", return_value="syncing"):
                thirty = module.workbench_market_intelligence_products(category="home", period=30)
                seven = module.workbench_market_intelligence_products(category="home", period=7)
                detail = module.workbench_market_intelligence_product("1001")
                keywords = module.workbench_market_intelligence_keywords(category="home")
        self.assertTrue(thirty["available"])
        self.assertEqual(thirty["total"], 1)
        self.assertFalse(seven["available"])
        self.assertEqual(detail["keyword_state"], "ready")
        self.assertGreaterEqual(len(detail["keywords"]), 15)
        self.assertEqual(detail["keyword_notice"], "关键词根据公开数据和商品信息整理")
        self.assertFalse(keywords["available"])

    def test_market_frontend_avoids_empty_image_placeholders_and_resets_detail_scroll(self):
        script = (ROOT / "collector/local-ingest/static/workbench.js").read_text(encoding="utf-8")
        stylesheet = (ROOT / "collector/local-ingest/static/workbench.css").read_text(encoding="utf-8")
        self.assertNotIn("官方榜单未提供图片", script)
        self.assertIn("Ozon市场数据 · 测试版", script)
        self.assertIn("主图同步中", script)
        self.assertIn("关键词根据公开数据和商品信息整理", script)
        self.assertIn("data-market-keywords-more", script)
        self.assertIn("index >= 15", script)
        self.assertIn("data-market-search", script)
        self.assertIn('aria-pressed="${state.marketRanking === "hot"}"', script)
        self.assertIn("scheduleMarketSearch", script)
        self.assertIn('window.scrollTo({top:0, left:0, behavior:"auto"})', script)
        self.assertIn(".market-mobile-search", stylesheet)
        self.assertIn(".market-detail-media-card", stylesheet)
        self.assertIn(".market-keyword-list", stylesheet)
        self.assertIn("min-height:180px", stylesheet)

    def test_search_visibility_plan_locks_stable_seller_title_and_splits_tags(self):
        plan = build_search_visibility_plan({
            "shop_id": "shop-a",
            "period_days": 30,
            "items": [{
                "product_id": "P100001",
                "offer_id": "offer-1",
                "title": "Органайзер для ванной комнаты",
                "queries": [
                    {"query": "органайзер для ванной", "impressions": 1000, "clicks": 80, "orders": 4, "revenue_rub": 3200},
                    {"query": "полка для косметики", "impressions": 600, "clicks": 33, "orders": 1, "revenue_rub": 700},
                ],
            }],
        }, generated_at="2026-08-02T10:00:00+08:00")

        action = plan["actions"][0]
        self.assertEqual(plan["mode"], "dry_run")
        self.assertTrue(action["title_locked"])
        self.assertEqual(action["allowed_changes"], ["subject_tags", "intro"])
        self.assertEqual(action["blocked_changes"], ["title"])
        self.assertEqual(action["title_terms"], [])
        self.assertEqual(action["order_count"], 5)
        self.assertTrue(action["intro_update_available"])
        self.assertTrue(all(tag.startswith("#") and "_" not in tag for tag in action["subject_tags"]))
        self.assertEqual(plan["safety"]["write_api_calls"], 0)
        self.assertEqual(plan["safety"]["inventory_api_calls"], 0)

    def test_search_visibility_plan_allows_title_terms_for_low_exposure_no_order(self):
        plan = build_search_visibility_plan({
            "shop_id": "shop-a",
            "period_days": 30,
            "items": [{
                "product_id": "P100002",
                "offer_ids": ["offer-2"],
                "title": "Товар для дома",
                "queries": [
                    {"query": "рюкзак женский дорожный", "impressions": 90, "clicks": 2, "orders": 0, "revenue_rub": 0},
                    {"query": "рюкзак ручная кладь", "impressions": 55, "clicks": 1, "orders": 0, "revenue_rub": 0},
                ],
            }],
        }, generated_at="2026-08-02T10:00:00+08:00")

        action = plan["actions"][0]
        self.assertEqual(action["risk_layer"], "title_optimization_candidate")
        self.assertFalse(action["title_locked"])
        self.assertIn("title", action["allowed_changes"])
        self.assertEqual(action["title_terms"][0], "рюкзак женский дорожный")
        self.assertGreaterEqual(len(action["subject_tags"]), 1)

    def test_search_visibility_plan_only_fills_subject_tags_below_thirty(self):
        existing_tags = [
            "#органайзер", "#ванная", "#полотенца", "#косметика", "#ваннаяполка",
            "#дляполотенец", "#органайзерванной", "#металл", "#напольный", "#корзина",
            "#держатель", "#комната", "#аксессуары", "#стеллаж", "#полочки",
            "#домашний", "#санузел", "#удобный", "#компактный", "#стойка",
            "#золотой", "#черный", "#белый", "#устойчивый", "#декор",
            "#мрамор", "#шампунь", "#гели", "#ванной",
        ]
        plan = build_search_visibility_plan({
            "shop_id": "shop-a",
            "period_days": 30,
            "items": [{
                "product_id": "P100004",
                "offer_id": "offer-4",
                "title": "Органайзер для ванной комнаты",
                "existing_subject_tags": existing_tags,
                "queries": [
                    {"query": "органайзер для ванной", "impressions": 1000, "clicks": 80, "orders": 4, "revenue_rub": 3200},
                    {"query": "полка для косметики", "impressions": 600, "clicks": 33, "orders": 1, "revenue_rub": 700},
                ],
            }],
        }, generated_at="2026-08-02T10:00:00+08:00")

        action = plan["actions"][0]
        self.assertEqual(action["existing_subject_tag_count"], 29)
        self.assertEqual(action["missing_subject_tag_count"], 1)
        self.assertEqual(len(action["subject_tags"]), 1)
        self.assertTrue(action["subject_tag_update_required"])
        self.assertEqual(plan["summary"]["stable_tag_only"], 1)

    def test_search_visibility_reads_space_separated_ozon_subject_tags(self):
        existing_tags = [
            "#контейнердляхраненияовощей #банкадляхранения "
            "#контейнердляовощейхранения"
        ]

        plan = build_search_visibility_plan({
            "shop_id": "shop-a",
            "period_days": 15,
            "items": [{
                "product_id": "P100004B",
                "offer_id": "offer-4b",
                "title": "Контейнер для ферментации овощей",
                "product_attributes": [{
                    "id": 23171,
                    "name": "Хештеги",
                    "values": existing_tags,
                }],
                "queries": [{
                    "query": "банка для ферментации овощей",
                    "impressions": 630,
                    "clicks": 0,
                    "orders": 0,
                }],
            }],
        }, generated_at="2026-08-03T10:00:00+08:00")

        action = plan["actions"][0]
        self.assertEqual(action["existing_subject_tag_count"], 3)
        self.assertEqual(action["missing_subject_tag_count"], 27)
        self.assertEqual(action["existing_subject_tags"], [
            "#контейнердляхраненияовощей",
            "#банкадляхранения",
            "#контейнердляовощейхранения",
        ])

    def test_search_visibility_plan_replaces_low_search_full_subject_tags(self):
        existing_tags = [
            "#органайзер", "#ванная", "#полотенца", "#косметика", "#ваннаяполка",
            "#дляполотенец", "#органайзерванной", "#металл", "#напольный", "#корзина",
            "#держатель", "#комната", "#аксессуары", "#стеллаж", "#полочки",
            "#домашний", "#санузел", "#удобный", "#компактный", "#стойка",
            "#золотой", "#черный", "#белый", "#устойчивый", "#декор",
            "#мрамор", "#шампунь", "#гели", "#ванной", "#полкадлякосметики",
        ]
        plan = build_search_visibility_plan({
            "shop_id": "shop-a",
            "period_days": 30,
            "items": [{
                "product_id": "P100005",
                "offer_id": "offer-5",
                "title": "Органайзер для ванной комнаты",
                "existing_subject_tags": existing_tags,
                "queries": [
                    {"query": "органайзер для ванной", "impressions": 1000, "clicks": 80, "orders": 4, "revenue_rub": 3200},
                    {"query": "полка для косметики", "impressions": 10, "clicks": 1, "orders": 0, "revenue_rub": 0},
                ],
            }],
        }, generated_at="2026-08-02T10:00:00+08:00")

        action = plan["actions"][0]
        self.assertEqual(action["existing_subject_tag_count"], 30)
        self.assertEqual(action["missing_subject_tag_count"], 0)
        self.assertGreaterEqual(len(action["subject_tags"]), 1)
        self.assertEqual(action["subject_tag_strategy"], "replace_low_search")
        self.assertEqual(action["subject_tag_replacement_count"], len(action["subject_tags"]))
        self.assertEqual(len(action["subject_tags_to_remove"]), len(action["subject_tags"]))
        self.assertIn("#полкадлякосметики", action["subject_tags_to_remove"])
        self.assertTrue(action["subject_tag_update_required"])
        self.assertIn("subject_tags", action["allowed_changes"])
        self.assertEqual(plan["summary"]["stable_tag_only"], 1)
        self.assertGreaterEqual(len(plan["batches"]), 1)

    def test_search_visibility_plan_keeps_full_tags_without_lower_search_proof(self):
        existing_tags = [
            "#органайзер", "#ванная", "#полотенца", "#косметика", "#ваннаяполка",
            "#дляполотенец", "#органайзерванной", "#металл", "#напольный", "#корзина",
            "#держатель", "#комната", "#аксессуары", "#стеллаж", "#полочки",
            "#домашний", "#санузел", "#удобный", "#компактный", "#стойка",
            "#золотой", "#черный", "#белый", "#устойчивый", "#декор",
            "#мрамор", "#шампунь", "#гели", "#ванной", "#дизайн",
        ]
        plan = build_search_visibility_plan({
            "shop_id": "shop-a",
            "period_days": 30,
            "items": [{
                "product_id": "P100005B",
                "offer_id": "offer-5b",
                "title": "Органайзер для ванной комнаты",
                "existing_subject_tags": existing_tags,
                "queries": [
                    {"query": "органайзер для ванной", "impressions": 1000, "clicks": 80, "orders": 4, "revenue_rub": 3200},
                ],
            }],
        }, generated_at="2026-08-02T10:00:00+08:00")

        action = plan["actions"][0]
        self.assertEqual(action["existing_subject_tag_count"], 30)
        self.assertEqual(action["subject_tag_strategy"], "replace_low_search")
        self.assertEqual(action["subject_tags"], [])
        self.assertEqual(action["subject_tags_to_remove"], [])
        self.assertFalse(action["subject_tag_update_required"])

    def test_search_visibility_plan_does_not_generate_title_tags_without_data_source(self):
        plan = build_search_visibility_plan({
            "shop_id": "shop-a",
            "period_days": 15,
            "items": [{
                "product_id": "P100006",
                "offer_id": "offer-6",
                "title": "Органайзер для кухни с полками",
                "category_name": "Дом и сад",
                "existing_subject_tags": [],
                "queries": [],
            }],
        }, generated_at="2026-08-02T10:00:00+08:00")

        action = plan["actions"][0]
        self.assertEqual(action["existing_subject_tag_count"], 0)
        self.assertEqual(action["subject_tags"], [])
        self.assertEqual(action["subject_tag_strategy"], "fill_missing")
        self.assertFalse(action["subject_tag_suggestion_available"])
        self.assertFalse(action["subject_tag_update_required"])
        self.assertEqual(action["allowed_changes"], [])
        self.assertEqual(action["data_source_status"], "title_inference_only")
        self.assertIn("暂无 Ozon/Yandex 搜索数据来源", action["reason_cn"])

    def test_search_visibility_plan_uses_yandex_wordstat_for_tags_and_intro(self):
        plan = build_search_visibility_plan({
            "shop_id": "shop-a",
            "period_days": 15,
            "items": [{
                "product_id": "P100007",
                "offer_id": "offer-7",
                "title": "Товар для дома",
                "existing_subject_tags": [],
                "queries": [],
                "yandex_wordstat": {
                    "topRequests": [
                        {"phrase": "органайзер для кухни", "count": 12000},
                        {"phrase": "полка для кухни", "count": 5000},
                    ],
                },
            }],
        }, generated_at="2026-08-02T10:00:00+08:00")

        action = plan["actions"][0]
        self.assertEqual(action["risk_layer"], "insufficient_data")
        self.assertEqual(action["allowed_changes"], ["subject_tags", "intro"])
        self.assertNotIn("title", action["allowed_changes"])
        self.assertEqual(action["evidence"]["top_yandex_wordstat"][0]["query"], "органайзер для кухни")
        self.assertEqual(action["evidence"]["reference_totals"]["yandex_wordstat_searches"], 17000)
        self.assertTrue(action["subject_tag_update_required"])
        self.assertGreaterEqual(len(action["subject_tags"]), 1)

    def test_search_visibility_plan_uses_seerfar_monthly_heat_without_calling_it_ozon_users(self):
        plan = build_search_visibility_plan({
            "shop_id": "shop-a",
            "period_days": 15,
            "items": [{
                "product_id": "P100008",
                "offer_id": "offer-8",
                "title": "Контейнер для хранения",
                "existing_subject_tags": [],
                "queries": [],
                "seerfar_keyword_mining": {"items": [
                    {"keyword": "контейнер для хранения", "monthly_search_heat": "58 990", "relevance": "100%"},
                    {"keyword": "органайзер для хранения", "monthly_search_heat": 12000, "relevance": 88},
                ]},
            }],
        }, generated_at="2026-08-10T10:00:00+08:00")

        action = plan["actions"][0]
        self.assertEqual(action["evidence"]["top_seerfar_keyword_mining"][0]["query"], "контейнер для хранения")
        self.assertEqual(action["evidence"]["top_seerfar_keyword_mining"][0]["metrics"]["monthly_search_heat"], 58990)
        self.assertEqual(action["evidence"]["reference_totals"]["seerfar_keyword_mining_search_heat"], 70990)
        self.assertTrue(action["subject_tag_update_required"])
        self.assertIn("Seerfar 月搜热度", action["reason_cn"])

    def test_search_visibility_plan_keeps_seerfar_reverse_separate_from_keyword_mining(self):
        plan = build_search_visibility_plan({
            "shop_id": "shop-a",
            "period_days": 15,
            "items": [{
                "product_id": "4423220069",
                "offer_id": "offer-9",
                "title": "Держатель для туалетной бумаги",
                "existing_subject_tags": [],
                "queries": [],
                "seerfar_keyword_reverse": {"items": [
                    {"query": "держатель для туалетной бумаги", "search_count": 28692},
                    {"query": "подставка для туалетной бумаги", "search_count": 1568},
                ]},
            }],
        }, generated_at="2026-08-10T10:00:00+08:00")

        action = plan["actions"][0]
        self.assertEqual(action["evidence"]["top_seerfar_keyword_reverse"][0]["query"], "держатель для туалетной бумаги")
        self.assertEqual(action["evidence"]["reference_totals"]["seerfar_keyword_reverse_searches"], 30260)
        self.assertFalse(action["evidence"]["top_seerfar_keyword_mining"])
        self.assertIn("Seerfar竞品反查", action["reason_cn"])

    def test_normalize_seerfar_keyword_rows_preserves_visible_metrics(self):
        rows = normalize_seerfar_keyword_rows([{
            "关键词": "контейнер для хранения",
            "月搜热度": "58 990",
            "月搜增长": "1.5%",
            "相关度": "100%",
            "加购数": "8 573",
            "广告竞品数": "1 042",
        }])
        self.assertEqual(rows[0]["count"], 58990)
        self.assertEqual(rows[0]["metrics"]["monthly_growth_percent"], 1.5)
        self.assertEqual(rows[0]["metrics"]["cart_add_count"], 8573)
        self.assertEqual(rows[0]["metrics"]["ad_competitor_count"], 1042)

    def test_search_visibility_plan_copies_only_exact_safe_search_terms(self):
        plan = build_search_visibility_plan({
            "shop_id": "shop-a",
            "period_days": 15,
            "items": [{
                "product_id": "P100007B",
                "offer_id": "offer-7b",
                "title": "Аксессуар для ванной",
                "existing_subject_tags": [],
                "queries": [
                    {"query": "полка для ванной", "impressions": 500, "clicks": 20, "orders": 0},
                    {"query": "полка для ванной premium", "impressions": 900, "clicks": 30, "orders": 0},
                    {"query": "полка huawei для ванной", "impressions": 800, "clicks": 25, "orders": 0},
                ],
            }],
        }, generated_at="2026-08-02T10:00:00+08:00")

        action = plan["actions"][0]
        self.assertEqual(action["subject_tags"], ["#полкадляванной"])
        self.assertEqual(action["intro_terms"], ["полка для ванной"])
        self.assertNotIn("premium", action["recommended_intro"].casefold())
        self.assertNotIn("huawei", action["recommended_intro"].casefold())

    def test_parse_yandex_wordstat_text_accepts_copied_rows(self):
        rows = parse_yandex_wordstat_text(
            "Фраза\tПоказы\n"
            "банка для ферментации\t4 435\n"
            "емкость для засолки;2135\n"
            "контейнер 5 литров 612\n",
            period_days=30,
        )

        self.assertEqual(rows[0]["query"], "банка для ферментации")
        self.assertEqual(rows[0]["count"], 4435)
        self.assertEqual(rows[0]["period_days"], 30)
        self.assertEqual(len(rows), 3)

    def test_parse_ozon_product_query_text_prioritizes_high_search_counts(self):
        rows = parse_ozon_product_query_text(
            "1. держатель для туалетной бумаги 28 692 Premium Premium Premium 0 ₽\n"
            "2. держатель 1 929 Premium Premium Premium 0 ₽\n"
            "3. держатель для туалетной бумаги напольный 1 709 Premium Premium Premium 0 ₽",
            period_days=15,
        )

        self.assertEqual(rows[0]["query"], "держатель для туалетной бумаги")
        self.assertEqual(rows[0]["count"], 28692)
        self.assertEqual(rows[1]["query"], "держатель")
        self.assertEqual(rows[1]["count"], 1929)

    def test_search_visibility_plan_uses_counted_competitor_terms_for_tags_and_intro(self):
        rows = parse_ozon_product_query_text(
            "держатель для туалетной бумаги\t28 692\n"
            "держатель\t1 929",
            period_days=15,
        )
        plan = build_search_visibility_plan({
            "shop_id": "shop-a",
            "period_days": 15,
            "items": [{
                "product_id": "P100008",
                "offer_id": "offer-8",
                "title": "Подставка для ванной",
                "existing_subject_tags": [],
                "product_attributes": [{"id": 4191, "name": "Аннотация", "values": ["Текущая аннотация."]}],
                "queries": [],
                "trial_reference_terms": rows,
            }],
        }, generated_at="2026-08-02T10:00:00+08:00")

        action = plan["actions"][0]
        self.assertEqual(action["data_source_status"], "trial_source")
        self.assertEqual(action["allowed_changes"], ["subject_tags", "intro"])
        self.assertNotIn("title", action["allowed_changes"])
        self.assertEqual(action["current_intro"], "Текущая аннотация.")
        self.assertEqual(action["evidence"]["top_trial_terms"][0]["query"], "держатель для туалетной бумаги")
        self.assertEqual(action["evidence"]["reference_totals"]["trial_reference_searches"], 30621)
        self.assertTrue(action["subject_tag_update_required"])
        self.assertIn("#держательдлятуалетнойбумаги", action["subject_tags"])
        self.assertNotIn("#держательдля", action["subject_tags"])
        self.assertTrue(all(len(tag.lstrip("#")) <= 30 for tag in action["subject_tags"]))
        self.assertIn("держатель для туалетной бумаги", action["intro_terms"])
        self.assertTrue(action["recommended_intro"].startswith("Текущая аннотация."))
        self.assertIn("держатель для туалетной бумаги", action["intro_supplement"])
        self.assertIn("держатель для туалетной бумаги", action["recommended_intro"])
        self.assertNotIn("Подходит для покупателей", action["recommended_intro"])

    def test_search_visibility_plan_keeps_uncounted_trial_terms_display_only(self):
        plan = build_search_visibility_plan({
            "shop_id": "shop-a",
            "period_days": 15,
            "items": [{
                "product_id": "P100009",
                "offer_id": "offer-9",
                "title": "Подставка для ванной",
                "existing_subject_tags": [],
                "queries": [],
                "competitor_terms": ["держатель для бумаги"],
            }],
        }, generated_at="2026-08-02T10:00:00+08:00")

        action = plan["actions"][0]
        self.assertEqual(action["data_source_status"], "title_inference_only")
        self.assertEqual(action["allowed_changes"], [])
        self.assertFalse(action["subject_tag_update_required"])

    def test_traffic_performance_plan_locks_title_for_recommendation_led_orders(self):
        plan = build_traffic_performance_plan({
            "shop_id": "shop-a",
            "period_days": 30,
            "items": [{
                "product_id": "P200001",
                "title": "Органайзер для ванной",
                "search": {"impressions": 1200, "clicks": 40, "orders": 2, "revenue_rub": 1600},
                "recommendation": {"impressions": 8000, "clicks": 360, "orders": 9, "revenue_rub": 7200},
                "ads": {"impressions": 500, "clicks": 20, "orders": 0, "revenue_rub": 0, "spend_rub": 0},
            }],
        }, generated_at="2026-08-02T10:00:00+08:00")

        action = plan["actions"][0]
        self.assertEqual(action["traffic_layer"], "recommendation_led")
        self.assertTrue(action["title_locked"])
        self.assertEqual(action["blocked_changes"], ["title"])
        self.assertIn("maintain_card_quality", action["focus"])
        self.assertEqual(plan["summary"]["recommendation_led"], 1)
        self.assertEqual(plan["safety"]["write_api_calls"], 0)
        self.assertEqual(plan["safety"]["inventory_api_calls"], 0)
        self.assertEqual(plan["safety"]["ad_budget_api_calls"], 0)

    def test_traffic_performance_plan_flags_ad_spend_risk_without_budget_action(self):
        normalized = normalize_traffic_item({
            "product_id": "P200002",
            "ads": {"impressions": 5000, "clicks": 120, "orders": 0, "revenue_rub": 0, "spend_rub": 900},
        })
        plan = build_traffic_performance_plan({
            "shop_id": "shop-a",
            "items": [normalized],
        }, generated_at="2026-08-02T10:00:00+08:00")

        action = plan["actions"][0]
        self.assertEqual(action["traffic_layer"], "ad_spend_risk")
        self.assertFalse(action["title_locked"])
        self.assertIn("review_ads", action["focus"])
        self.assertNotIn("increase_budget", action["focus"])
        self.assertEqual(plan["summary"]["ad_spend_risk"], 1)
        self.assertEqual(plan["safety"]["ad_budget_api_calls"], 0)

    def test_collect_seller_search_visibility_uses_read_only_analytics_endpoints(self):
        transport = FakeAnalyticsTransport()
        client = OzonAnalyticsReadOnlyClient("client", "secret", transport=transport)
        source = collect_seller_search_visibility(
            client,
            shop_id="shop-a",
            date_from="2026-07-03T00:00:00Z",
            date_to="2026-08-02T00:00:00Z",
        )

        endpoints = [endpoint for endpoint, _payload in transport.calls]
        self.assertTrue(all(endpoint in OzonAnalyticsReadOnlyClient.ALLOWED_ENDPOINTS for endpoint in endpoints))
        self.assertIn(OzonAnalyticsReadOnlyClient.PRODUCT_LIST_ENDPOINT, endpoints)
        self.assertIn(OzonAnalyticsReadOnlyClient.PRODUCT_INFO_ENDPOINT, endpoints)
        self.assertIn(OzonAnalyticsReadOnlyClient.PRODUCT_ATTRIBUTES_ENDPOINT, endpoints)
        self.assertIn(OzonAnalyticsReadOnlyClient.PRODUCT_QUERY_DETAILS_ENDPOINT, endpoints)
        self.assertIn(OzonAnalyticsReadOnlyClient.FBO_POSTING_ENDPOINT, endpoints)
        self.assertIn(OzonAnalyticsReadOnlyClient.FBS_POSTING_ENDPOINT, endpoints)
        self.assertNotIn("/v3/product/import", endpoints)
        self.assertFalse(any("stock" in endpoint.lower() for endpoint in endpoints))
        self.assertEqual(source["shop_id"], "shop-a")
        self.assertEqual(source["items"][0]["offer_id"], "offer-1")
        self.assertEqual(source["items"][0]["image_url"], "https://cdn.example.com/thermos.jpg")
        self.assertEqual(source["items"][0]["price"], "1690")
        self.assertEqual(source["items"][0]["created_at"], "2026-04-20T10:00:00Z")
        self.assertEqual(source["items"][0]["updated_at"], "2026-04-22T08:17:12Z")
        self.assertEqual(source["items"][0]["order_count"], 3)
        action = build_search_visibility_plan(source)["actions"][0]
        self.assertEqual(action["order_count"], 3)
        self.assertTrue(action["title_locked"])
        self.assertEqual(source["items"][0]["measurements"]["weight_g"], 320)
        self.assertEqual(source["items"][0]["measurements"]["length_mm"], 220)
        self.assertEqual(source["items"][0]["existing_subject_tags"], ["#термос", "#походный"])
        self.assertEqual(source["items"][0]["queries"][0]["query"], "термос")
        self.assertEqual(source["items"][0]["queries"][0]["search_users"], 50)
        self.assertEqual(source["order_read_api_calls"], 2)
        self.assertEqual(source["safety"]["write_api_calls"], 0)
        self.assertEqual(source["safety"]["inventory_api_calls"], 0)

    def test_workbench_search_visibility_sync_reads_ozon_and_saves_plan(self):
        app_path = ROOT / "collector/local-ingest/app.py"
        import importlib.util

        spec = importlib.util.spec_from_file_location("market_search_visibility_sync_app", app_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        transport = FakeAnalyticsTransport()

        FakeRequest = lambda: _FakeAsyncRequest({}
)
        with tempfile.TemporaryDirectory() as temporary, patch.object(
            module, "MARKET_SEARCH_VISIBILITY_PLAN_PATH", Path(temporary) / "latest.json"
        ), patch.object(
            module, "MARKET_DB_PATH", Path(temporary) / "market.sqlite"
        ), patch.object(
            module, "_default_search_shop", return_value=({"id": "shop-a"}, {"client_id": "client", "api_key": "secret"})
        ), patch.object(
            module, "OzonAnalyticsReadOnlyClient", side_effect=lambda client_id, api_key: OzonAnalyticsReadOnlyClient(client_id, api_key, transport=transport)
        ):
            result = asyncio.run(module.workbench_search_visibility_sync(FakeRequest()))
            latest = module.workbench_search_visibility_latest()

        endpoints = [endpoint for endpoint, _payload in transport.calls]
        self.assertIn(OzonAnalyticsReadOnlyClient.PRODUCT_QUERY_DETAILS_ENDPOINT, endpoints)
        self.assertNotIn("/v3/product/import", endpoints)
        self.assertFalse(any("stock" in endpoint.lower() for endpoint in endpoints))
        list_payload = next(payload for endpoint, payload in transport.calls if endpoint == OzonAnalyticsReadOnlyClient.PRODUCT_LIST_ENDPOINT)
        self.assertEqual(list_payload["limit"], 1000)
        self.assertTrue(result["available"])
        self.assertIn("1 条搜索词", result["notice"])
        self.assertEqual(result["write_api_calls"], 0)
        self.assertEqual(result["inventory_api_calls"], 0)
        self.assertTrue(latest["available"])
        self.assertEqual(latest["actions"][0]["evidence"]["totals"]["query_count"], 1)

    def test_workbench_search_visibility_dry_run_is_local_only(self):
        app_path = ROOT / "collector/local-ingest/app.py"
        import importlib.util

        spec = importlib.util.spec_from_file_location("market_search_visibility_app", app_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        class FakeRequest:
            async def json(self):
                return {
                    "shop_id": "shop-a",
                    "items": [{
                        "product_id": "P100003",
                        "title": "Полка",
                        "queries": [{"query": "полка настенная", "impressions": 50, "clicks": 1}],
                    }],
                }

        with tempfile.TemporaryDirectory() as temporary, patch.object(
            module, "MARKET_SEARCH_VISIBILITY_PLAN_PATH", Path(temporary) / "latest.json"
        ):
            result = asyncio.run(module.workbench_search_visibility_dry_run(FakeRequest()))
            latest = module.workbench_search_visibility_latest()

        self.assertEqual(result["safety"]["write_api_calls"], 0)
        self.assertEqual(result["safety"]["inventory_api_calls"], 0)
        self.assertIn("没有提交Ozon更新", result["notice"])
        self.assertTrue(latest["available"])
        self.assertEqual(latest["write_api_calls"], 0)

    def test_workbench_search_visibility_apply_updates_tags_and_intro(self):
        app_path = ROOT / "collector/local-ingest/app.py"
        import importlib.util

        spec = importlib.util.spec_from_file_location("market_search_visibility_apply_app", app_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        calls = []

        class FakeWriteClient:
            PRODUCT_ATTRIBUTES_UPDATE_ENDPOINT = "/v1/product/attributes/update"

            def __init__(self, config, transport=None, allow_production_write=False):
                self.config = config

            def update_product_attributes(self, request_items):
                calls.append(request_items)
                return {"result": {"task_id": 12345}}

            def get_import_info(self, task_id):
                return {
                    "result": {
                        "items": [{
                            "offer_id": "offer-1",
                            "product_id": 1001,
                            "status": "imported",
                            "errors": [{"level": "warning", "attribute_id": 23171, "message": "corrected"}],
                        }],
                        "total": 1,
                    }
                }

            def get_product_attributes(self, offer_ids):
                return {
                    "result": [{
                        "offer_id": "offer-1",
                        "product_id": 1001,
                        "attributes": [
                            {"id": module.OZON_HASHTAG_ATTRIBUTE_ID, "values": [{"value": "#органайзер #ванная"}]},
                            {"id": module.OZON_ANNOTATION_ATTRIBUTE_ID, "values": [{"value": "Описание с ключами"}]},
                        ],
                    }]
                }
        FakeRequest = lambda: _FakeAsyncRequest({"store_id": "shop-a", "product_id": "1001"}
)
        plan = build_search_visibility_plan({
            "shop_id": "shop-a",
            "period_days": 7,
            "items": [{
                "product_id": "1001",
                "offer_id": "offer-1",
                "title": "Полка",
                "existing_subject_tags": ["#домашний"],
                "queries": [{"query": "органайзер для ванной", "impressions": 50, "clicks": 1, "orders": 0}],
            }],
        }, generated_at="2026-08-02T10:00:00+08:00")

        with tempfile.TemporaryDirectory() as temporary, patch.object(
            module, "MARKET_SEARCH_VISIBILITY_PLAN_PATH", Path(temporary) / "latest.json"
        ), patch.object(
            module, "MARKET_SEARCH_VISIBILITY_UPLOAD_DIR", Path(temporary) / "uploads"
        ), patch.object(
            module, "_default_search_shop", return_value=({"id": "shop-a"}, {"client_id": "client", "api_key": "secret"})
        ), patch.object(
            module, "OzonWriteClient", FakeWriteClient
        ), patch.object(
            module, "project_relative", side_effect=lambda path: str(path)
        ):
            module.atomic_write_json(module.MARKET_SEARCH_VISIBILITY_PLAN_PATH, plan)
            result = asyncio.run(module.workbench_search_visibility_apply(FakeRequest()))
            latest = module.workbench_search_visibility_latest()

        self.assertEqual(result["endpoint"], "/v1/product/attributes/update")
        self.assertEqual(result["write_api_calls"], 1)
        self.assertEqual(result["inventory_api_calls"], 0)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0]["product_id"], 1001)
        uploaded_attribute_ids = {attribute["id"] for attribute in calls[0][0]["attributes"]}
        self.assertEqual(uploaded_attribute_ids, {module.OZON_HASHTAG_ATTRIBUTE_ID, module.OZON_ANNOTATION_ATTRIBUTE_ID})
        tag_attribute = next(attribute for attribute in calls[0][0]["attributes"] if attribute["id"] == module.OZON_HASHTAG_ATTRIBUTE_ID)
        self.assertEqual(len(tag_attribute["values"]), 1)
        self.assertIn("#органайзер", tag_attribute["values"][0]["value"])
        intro_attribute = next(attribute for attribute in calls[0][0]["attributes"] if attribute["id"] == module.OZON_ANNOTATION_ATTRIBUTE_ID)
        self.assertIn("органайзер для ванной", intro_attribute["values"][0]["value"])
        self.assertIn("intro", result["uploaded_changes"])
        self.assertTrue(all("stock" not in json.dumps(call).lower() for call in calls))
        self.assertEqual(latest["actions"][0]["last_upload"]["status"], "submitted")

    def test_workbench_search_visibility_batch_apply_updates_tags_and_intro(self):
        app_path = ROOT / "collector/local-ingest/app.py"
        import importlib.util

        spec = importlib.util.spec_from_file_location("market_search_visibility_batch_apply_app", app_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        calls = []

        class FakeWriteClient:
            PRODUCT_ATTRIBUTES_UPDATE_ENDPOINT = "/v1/product/attributes/update"

            def __init__(self, config, transport=None, allow_production_write=False):
                self.config = config

            def update_product_attributes(self, request_items):
                calls.append(request_items)
                return {"result": {"task_id": 12345}}

            def get_import_info(self, task_id):
                return {
                    "result": {
                        "items": [{
                            "offer_id": "offer-1",
                            "product_id": 1001,
                            "status": "imported",
                            "errors": [{"level": "warning", "attribute_id": 23171, "message": "corrected"}],
                        }],
                        "total": 1,
                    }
                }

            def get_product_attributes(self, offer_ids):
                return {
                    "result": [{
                        "offer_id": "offer-1",
                        "product_id": 1001,
                        "attributes": [
                            {"id": module.OZON_HASHTAG_ATTRIBUTE_ID, "values": [{"value": "#органайзер #ванная"}]},
                            {"id": module.OZON_ANNOTATION_ATTRIBUTE_ID, "values": [{"value": "Описание с ключами"}]},
                        ],
                    }]
                }
        FakeRequest = lambda: _FakeAsyncRequest({"store_id": "shop-a", "max_products": 1000, "confirm_upload": True}
)
        plan = build_search_visibility_plan({
            "shop_id": "shop-a",
            "period_days": 15,
            "items": [
                {
                    "product_id": "1001",
                    "offer_id": "offer-1",
                    "title": "Полка",
                    "existing_subject_tags": ["#домашний"],
                    "queries": [{"query": "органайзер для ванной", "impressions": 50, "clicks": 1, "orders": 0}],
                },
                {
                    "product_id": "1002",
                    "offer_id": "offer-2",
                    "title": "Банка",
                    "existing_subject_tags": [],
                    "queries": [],
                    "yandex_wordstat": {"items": [{"phrase": "банка для ферментации", "count": 4435}]},
                },
            ],
        }, generated_at="2026-08-02T10:00:00+08:00")

        with tempfile.TemporaryDirectory() as temporary, patch.object(
            module, "MARKET_SEARCH_VISIBILITY_PLAN_PATH", Path(temporary) / "latest.json"
        ), patch.object(
            module, "MARKET_SEARCH_VISIBILITY_UPLOAD_DIR", Path(temporary) / "uploads"
        ), patch.object(
            module, "_default_search_shop", return_value=({"id": "shop-a"}, {"client_id": "client", "api_key": "secret"})
        ), patch.object(
            module, "OzonWriteClient", FakeWriteClient
        ), patch.object(
            module, "project_relative", side_effect=lambda path: str(path)
        ):
            module.atomic_write_json(module.MARKET_SEARCH_VISIBILITY_PLAN_PATH, plan)
            result = asyncio.run(module.workbench_search_visibility_apply_batch(FakeRequest()))
            latest = module.workbench_search_visibility_latest()

        self.assertEqual(result["endpoint"], "/v1/product/attributes/update")
        self.assertEqual(result["uploaded_product_count"], 2)
        self.assertEqual(result["write_api_calls"], 1)
        self.assertEqual(result["inventory_api_calls"], 0)
        self.assertEqual(len(calls), 1)
        self.assertEqual({item["product_id"] for item in calls[0]}, {1001, 1002})
        self.assertEqual({item["offer_id"] for item in calls[0]}, {"offer-1", "offer-2"})
        self.assertTrue(all(
            {attribute["id"] for attribute in item["attributes"]} == {module.OZON_HASHTAG_ATTRIBUTE_ID, module.OZON_ANNOTATION_ATTRIBUTE_ID}
            for item in calls[0]
        ))
        self.assertTrue(all(
            len(next(attribute for attribute in item["attributes"] if attribute["id"] == module.OZON_HASHTAG_ATTRIBUTE_ID)["values"]) == 1
            for item in calls[0]
        ))
        self.assertEqual(result["intro_update_count"], 2)
        self.assertTrue(all("stock" not in json.dumps(call).lower() for call in calls))
        self.assertTrue(all((action.get("last_upload") or {}).get("status") == "submitted" for action in latest["actions"]))

    def test_workbench_search_visibility_upload_status_verifies_remote_attributes(self):
        app_path = ROOT / "collector/local-ingest/app.py"
        import importlib.util

        spec = importlib.util.spec_from_file_location("market_search_visibility_status_app", app_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        class FakeWriteClient:
            PRODUCT_ATTRIBUTES_UPDATE_ENDPOINT = "/v1/product/attributes/update"

            def __init__(self, config, transport=None, allow_production_write=False):
                self.config = config

            def update_product_attributes(self, request_items):
                calls.append(request_items)
                return {"result": {"task_id": 12345}}

            def get_import_info(self, task_id):
                return {
                    "result": {
                        "items": [{
                            "offer_id": "offer-1",
                            "product_id": 1001,
                            "status": "imported",
                            "errors": [{"level": "warning", "attribute_id": 23171, "message": "corrected"}],
                        }],
                        "total": 1,
                    }
                }

            def get_product_attributes(self, offer_ids):
                return {
                    "result": [{
                        "offer_id": "offer-1",
                        "product_id": 1001,
                        "attributes": [
                            {"id": module.OZON_HASHTAG_ATTRIBUTE_ID, "values": [{"value": "#органайзер #ванная"}]},
                            {"id": module.OZON_ANNOTATION_ATTRIBUTE_ID, "values": [{"value": "Описание с ключами"}]},
                        ],
                    }]
                }

        FakeRequest = lambda: _FakeAsyncRequest({"store_id": "shop-a", "product_id": "1001"}
)
        plan = build_search_visibility_plan({
            "shop_id": "shop-a",
            "period_days": 7,
            "items": [{
                "product_id": "1001",
                "offer_id": "offer-1",
                "title": "Полка",
                "existing_subject_tags": [],
                "queries": [{"query": "органайзер для ванной", "impressions": 50, "clicks": 1, "orders": 0}],
            }],
        }, generated_at="2026-08-02T10:00:00+08:00")
        plan["actions"][0]["last_upload"] = {
            "status": "submitted",
            "task_id": 12345,
            "report_path": "",
        }

        with tempfile.TemporaryDirectory() as temporary, patch.object(
            module, "MARKET_SEARCH_VISIBILITY_PLAN_PATH", Path(temporary) / "latest.json"
        ), patch.object(
            module, "_default_search_shop", return_value=({"id": "shop-a"}, {"client_id": "client", "api_key": "secret"})
        ), patch.object(
            module, "OzonWriteClient", FakeWriteClient
        ):
            module.atomic_write_json(module.MARKET_SEARCH_VISIBILITY_PLAN_PATH, plan)
            result = asyncio.run(module.workbench_search_visibility_upload_status(FakeRequest()))
            latest = module.workbench_search_visibility_latest()

        self.assertEqual(result["status"], "verified")
        self.assertEqual(result["import_status"], "imported")
        self.assertTrue(result["has_subject_tags"])
        self.assertTrue(result["has_intro"])
        self.assertEqual(result["write_api_calls"], 0)
        self.assertEqual(result["inventory_api_calls"], 0)
        self.assertEqual(latest["actions"][0]["last_upload"]["remote_status"], "verified")

    def test_workbench_search_visibility_merge_replaces_low_search_tags(self):
        app_path = ROOT / "collector/local-ingest/app.py"
        import importlib.util

        spec = importlib.util.spec_from_file_location("market_search_visibility_merge_app", app_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        existing = [
            "#дом", "#сад", "#ванная", "#кухня", "#полка", "#ящик",
            "#корзина", "#стеллаж", "#комната", "#белье", "#одежда", "#обувь",
            "#детский", "#женский", "#мужской", "#черный", "#белый", "#зеленый",
            "#металл", "#пластик", "#дерево", "#ткань", "#ручной", "#удобный",
            "#компактный", "#напольный", "#настенный", "#органайзер", "#хранение", "#декор",
        ]
        tags, new_count = module._merged_subject_tags({
            "existing_subject_tags": existing,
            "subject_tags": ["#кухонный", "#складной"],
            "subject_tags_to_remove": ["#хранение", "#декор"],
            "subject_tag_strategy": "replace_low_search",
        })

        self.assertEqual(len(tags), 30)
        self.assertIn("#кухонный", tags)
        self.assertIn("#складной", tags)
        self.assertNotIn("#хранение", tags)
        self.assertNotIn("#декор", tags)
        self.assertEqual(new_count, 2)

    def test_workbench_yandex_wordstat_import_updates_plan_locally(self):
        app_path = ROOT / "collector/local-ingest/app.py"
        import importlib.util

        spec = importlib.util.spec_from_file_location("market_yandex_wordstat_import_app", app_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        class FakeRequest:
            async def json(self):
                return {
                    "store_id": "shop-a",
                    "product_id": "1002",
                    "text": "банка для ферментации 4435\nемкость для засолки 2135",
                    "period_days": 30,
                }

        plan = build_search_visibility_plan({
            "shop_id": "shop-a",
            "period_days": 15,
            "items": [{
                "product_id": "1002",
                "title": "Банка стеклянная",
                "existing_subject_tags": [],
                "queries": [],
            }],
        }, generated_at="2026-08-02T10:00:00+08:00")

        with tempfile.TemporaryDirectory() as temporary, patch.object(
            module, "MARKET_SEARCH_VISIBILITY_PLAN_PATH", Path(temporary) / "latest.json"
        ), patch.object(
            module, "MARKET_YANDEX_WORDSTAT_IMPORT_DIR", Path(temporary) / "imports"
        ):
            module.atomic_write_json(module.MARKET_SEARCH_VISIBILITY_PLAN_PATH, plan)
            result = asyncio.run(module.workbench_search_visibility_yandex_import(FakeRequest()))
            latest = module.workbench_search_visibility_latest()

        action = latest["actions"][0]
        self.assertEqual(result["imported_count"], 2)
        self.assertEqual(action["evidence"]["top_yandex_wordstat"][0]["query"], "банка для ферментации")
        self.assertEqual(action["evidence"]["reference_totals"]["yandex_wordstat_searches"], 6570)
        self.assertEqual(action["allowed_changes"], ["subject_tags", "intro"])
        self.assertNotIn("title", action["allowed_changes"])
        self.assertGreaterEqual(len(action["subject_tags"]), 1)
        self.assertIn("банка для ферментации", action["recommended_intro"])
        self.assertEqual(result["write_api_calls"], 0)
        self.assertEqual(result["inventory_api_calls"], 0)

    def test_workbench_seerfar_import_updates_plan_locally(self):
        app_path = ROOT / "collector/local-ingest/app.py"
        import importlib.util

        spec = importlib.util.spec_from_file_location("market_seerfar_import_app", app_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        class FakeRequest:
            async def json(self):
                return {
                    "store_id": "shop-a",
                    "product_id": "1003",
                    "seed_keyword": "банка для ферментации",
                    "rows": [
                        {"query": "банка для ферментации", "monthly_search_heat": 58990, "relevance": 100},
                        {"query": "банка для засолки", "monthly_search_heat": 12000, "relevance": 87},
                    ],
                }

        plan = build_search_visibility_plan({
            "shop_id": "shop-a",
            "period_days": 15,
            "items": [{
                "product_id": "1003",
                "title": "Банка стеклянная",
                "existing_subject_tags": [],
                "queries": [],
            }],
        }, generated_at="2026-08-10T10:00:00+08:00")

        with tempfile.TemporaryDirectory() as temporary, patch.object(
            module, "MARKET_SEARCH_VISIBILITY_PLAN_PATH", Path(temporary) / "latest.json"
        ), patch.object(
            module, "MARKET_SEARCH_VISIBILITY_PLAN_CACHE_DIR", Path(temporary) / "plans"
        ), patch.object(
            module, "MARKET_SEERFAR_KEYWORD_IMPORT_DIR", Path(temporary) / "imports"
        ):
            module.atomic_write_json(module.MARKET_SEARCH_VISIBILITY_PLAN_PATH, plan)
            result = asyncio.run(module.workbench_search_visibility_seerfar_import(FakeRequest()))
            latest = module.workbench_search_visibility_latest()

        action = latest["actions"][0]
        self.assertEqual(result["imported_count"], 2)
        self.assertEqual(action["evidence"]["top_seerfar_keyword_mining"][0]["query"], "банка для ферментации")
        self.assertEqual(action["evidence"]["reference_totals"]["seerfar_keyword_mining_search_heat"], 70990)
        self.assertEqual(action["last_seerfar_keyword_import"]["status"], "imported")
        self.assertEqual(result["write_api_calls"], 0)
        self.assertEqual(result["inventory_api_calls"], 0)

    def test_workbench_seerfar_reverse_without_result_queues_keyword_fallback(self):
        app_path = ROOT / "collector/local-ingest/app.py"
        import importlib.util

        spec = importlib.util.spec_from_file_location("market_seerfar_fallback_app", app_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        class FakeRequest:
            async def json(self):
                return {
                    "job_id": "reverse-job-1",
                    "error": "SEERFAR_REVERSE_EMPTY: 该 Ozon SKU 在 Seerfar 没有可用的反查词",
                }

        plan = build_search_visibility_plan({
            "shop_id": "shop-a",
            "period_days": 15,
            "items": [{
                "product_id": "4423220069",
                "title": "Держатель для туалетной бумаги",
                "existing_subject_tags": [],
                "queries": [],
            }],
        }, generated_at="2026-08-10T10:00:00+08:00")
        jobs = {
            "schema_version": "1.0.0",
            "jobs": [{
                "job_id": "reverse-job-1",
                "product_id": "4423220069",
                "shop_id": "shop-a",
                "seed_keyword": "4423220069",
                "mode": "keyword_reverse",
                "status": "running",
            }],
        }

        with tempfile.TemporaryDirectory() as temporary, patch.object(
            module, "MARKET_SEARCH_VISIBILITY_PLAN_PATH", Path(temporary) / "latest.json"
        ), patch.object(
            module, "MARKET_SEARCH_VISIBILITY_PLAN_CACHE_DIR", Path(temporary) / "plans"
        ), patch.object(
            module, "MARKET_SEERFAR_KEYWORD_JOBS_PATH", Path(temporary) / "jobs.json"
        ):
            module.atomic_write_json(module.MARKET_SEARCH_VISIBILITY_PLAN_PATH, plan)
            module.atomic_write_json(module.MARKET_SEERFAR_KEYWORD_JOBS_PATH, jobs)
            result = asyncio.run(module.workbench_search_visibility_seerfar_fail(FakeRequest()))
            latest_jobs = module._seerfar_keyword_jobs()["jobs"]

        self.assertEqual(result["status"], "fallback_queued")
        self.assertEqual(result["fallback_job"]["mode"], "keyword_miner")
        self.assertEqual(result["fallback_job"]["seed_keyword"], "Держатель для туалетной бумаги")
        self.assertEqual(latest_jobs[0]["status"], "completed_without_reverse_result")
        self.assertEqual(latest_jobs[1]["status"], "queued")
        self.assertEqual(result["write_api_calls"], 0)
        self.assertEqual(result["inventory_api_calls"], 0)

    def test_workbench_seerfar_queue_uses_listing_title_not_ozon_sku(self):
        app_path = ROOT / "collector/local-ingest/app.py"
        import importlib.util

        spec = importlib.util.spec_from_file_location("market_seerfar_title_queue_app", app_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        FakeRequest = lambda: _FakeAsyncRequest({"store_id": "shop-a", "product_id": "4423220069"}
)
        plan = build_search_visibility_plan({
            "shop_id": "shop-a",
            "period_days": 15,
            "items": [{
                "product_id": "4423220069",
                "sku": "4042683166",
                "title": "Держатель для туалетной бумаги",
                "existing_subject_tags": [],
                "queries": [],
            }],
        }, generated_at="2026-08-10T10:00:00+08:00")

        with tempfile.TemporaryDirectory() as temporary, patch.object(
            module, "MARKET_SEARCH_VISIBILITY_PLAN_PATH", Path(temporary) / "latest.json"
        ), patch.object(
            module, "MARKET_SEARCH_VISIBILITY_PLAN_CACHE_DIR", Path(temporary) / "shops"
        ), patch.object(
            module, "MARKET_SEERFAR_KEYWORD_JOBS_PATH", Path(temporary) / "jobs.json"
        ):
            module.atomic_write_json(module.MARKET_SEARCH_VISIBILITY_PLAN_PATH, plan)
            result = asyncio.run(module.workbench_search_visibility_seerfar_queue(FakeRequest()))

        self.assertEqual(result["job"]["mode"], "keyword_miner")
        self.assertEqual(result["job"]["seed_keyword"], "Держатель для туалетной бумаги")
        self.assertIn("线上标题", result["notice"])
        self.assertEqual(result["write_api_calls"], 0)
        self.assertEqual(result["inventory_api_calls"], 0)

    def test_workbench_skips_empty_reverse_lookups_for_remaining_batch_jobs(self):
        app_path = ROOT / "collector/local-ingest/app.py"
        import importlib.util

        spec = importlib.util.spec_from_file_location("market_seerfar_reverse_skip_app", app_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        queue = {
            "jobs": [
                {
                    "kind": "deduplicated_existing_product_keyword_reverse",
                    "mode": "keyword_reverse",
                    "status": "completed_without_reverse_result",
                }
                for _ in range(20)
            ] + [{
                "kind": "deduplicated_existing_product_keyword_reverse",
                "mode": "keyword_reverse",
                "status": "queued",
                "seed_keyword": "5417433849",
                "title_seed": "Точилка для ножей",
            }],
        }

        self.assertEqual(module._skip_unavailable_batch_reverse_jobs(queue), 1)
        queued = queue["jobs"][-1]
        self.assertEqual(queued["mode"], "keyword_miner")
        self.assertEqual(queued["seed_keyword"], "Точилка для ножей")

    def test_workbench_reports_stopped_seerfar_worker_with_pending_jobs(self):
        app_path = ROOT / "collector/local-ingest/app.py"
        import importlib.util

        spec = importlib.util.spec_from_file_location("market_seerfar_worker_status_app", app_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        queue = {
            "schema_version": "1.0.0",
            "jobs": [{
                "job_id": "queued-1",
                "status": "queued",
                "created_at": "2026-01-01T00:00:00+00:00",
            }],
        }

        status = module._seerfar_worker_status(queue, stall_seconds=90)

        self.assertTrue(status["stalled"])
        self.assertEqual(status["pending_count"], 1)
        self.assertIn("重新加载插件", status["message"])

    def test_workbench_traffic_performance_dry_run_is_local_only(self):
        app_path = ROOT / "collector/local-ingest/app.py"
        import importlib.util

        spec = importlib.util.spec_from_file_location("market_traffic_performance_app", app_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        class FakeRequest:
            async def json(self):
                return {
                    "shop_id": "shop-a",
                    "items": [{
                        "product_id": "P200003",
                        "recommendation": {"impressions": 1000, "clicks": 80, "orders": 3, "revenue_rub": 2100},
                    }],
                }

        with tempfile.TemporaryDirectory() as temporary, patch.object(
            module, "MARKET_TRAFFIC_PERFORMANCE_PLAN_PATH", Path(temporary) / "latest.json"
        ):
            result = asyncio.run(module.workbench_traffic_performance_dry_run(FakeRequest()))
            latest = module.workbench_traffic_performance_latest()

        self.assertEqual(result["safety"]["write_api_calls"], 0)
        self.assertEqual(result["safety"]["inventory_api_calls"], 0)
        self.assertEqual(result["safety"]["ad_budget_api_calls"], 0)
        self.assertIn("没有调整广告预算", result["notice"])
        self.assertTrue(latest["available"])
        self.assertEqual(latest["ad_budget_api_calls"], 0)


if __name__ == "__main__":
    unittest.main()
