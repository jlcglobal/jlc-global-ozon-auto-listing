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
    extract_public_image_url,
    generate_local_keyword_records,
    probe_ozon_sources,
)
from market_intelligence.bestsellers_import import normalize_row  # noqa: E402
from market_intelligence.search_queries_import import normalize_query_row  # noqa: E402
import market_intelligence.storage as storage_module  # noqa: E402


class FakeAnalyticsTransport:
    def __init__(self, premium_required=False):
        self.calls = []
        self.premium_required = premium_required

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
            return {"items": [{"id": 4259045262, "offer_id": "offer-1", "sku": 4042683166}]}
        if endpoint == OzonAnalyticsReadOnlyClient.PRODUCT_QUERIES_ENDPOINT:
            if self.premium_required:
                raise OzonAnalyticsPermissionError(
                    endpoint,
                    "Analytics for the specified period is available starting from the premium subscription",
                    403,
                )
            return {"total": 2, "items": [{"name": "термос"}]}
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
        self.assertEqual(records["ozon_free_market_analytics"]["state"], "login_required")

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


if __name__ == "__main__":
    unittest.main()
