import tempfile
import unittest
import zipfile
import hashlib
import json
from pathlib import Path
from unittest.mock import patch

from scripts import package_edge_extension

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "collector/edge-extension/manifest.json"


def current_plugin_version() -> str:
    return str(json.loads(MANIFEST.read_text(encoding="utf-8"))["version"])


class EdgeExtensionPackagingTest(unittest.TestCase):
    def test_release_is_built_from_single_source_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            release_dir = Path(directory)
            with patch.object(package_edge_extension, "RELEASE_DIR", release_dir):
                output = package_edge_extension.package_extension()
                first_hash = hashlib.sha256(output.read_bytes()).hexdigest()
                output = package_edge_extension.package_extension()
                second_hash = hashlib.sha256(output.read_bytes()).hexdigest()
            self.assertEqual(output.name, f"1688商品采集插件-{current_plugin_version()}.zip")
            self.assertEqual(first_hash, second_hash)
            with zipfile.ZipFile(output) as archive:
                names = set(archive.namelist())
                self.assertIn("edge-extension/manifest.json", names)
                self.assertIn("edge-extension/category-tree.zh-CN.json", names)
                self.assertIn("edge-extension/category-rules-cache.json", names)
                self.assertFalse(any("node_modules" in name for name in names))

    def test_collector_warms_and_parses_product_dimension_tables(self):
        source = (
            Path(__file__).resolve().parents[1]
            / "collector/edge-extension/src/content.ts"
        ).read_text(encoding="utf-8")
        self.assertIn("warmProductAttributeTables", source)
        self.assertIn("dom_product_measurement_table", source)
        self.assertIn('"产品尺寸"', source)
        self.assertIn("SKU重量", source)
        self.assertIn('axis: "weight"', source)
        self.assertIn('join(" ")', source)
        self.assertIn(f'PLUGIN_VERSION = "{current_plugin_version()}"', source)

    def test_extension_distinguishes_genuine_single_specification_offers(self):
        source = (
            Path(__file__).resolve().parents[1]
            / "collector/edge-extension/src/content.ts"
        ).read_text(encoding="utf-8")
        self.assertIn("isSingleSpecificationSku", source)
        self.assertIn("local-spec-single-offer-key-${offerId}", source)
        self.assertIn("hasVariantEvidence", source)

    def test_extension_keeps_visible_variant_images_without_a_hidden_sku_id(self):
        source = (
            Path(__file__).resolve().parents[1]
            / "collector/edge-extension/src/content.ts"
        ).read_text(encoding="utf-8")
        self.assertIn("isVisibleVariantSku", source)
        self.assertIn("visible_sku_option", source)
        self.assertIn("local-spec-variant-offer-key", source)

    def test_extension_maps_visible_option_images_back_to_real_skus(self):
        source = (
            Path(__file__).resolve().parents[1]
            / "collector/edge-extension/src/content.ts"
        ).read_text(encoding="utf-8")
        self.assertIn("skuTextMatchKeys", source)
        self.assertIn("skuKeys.includes(comparableName)", source)
        self.assertIn("data-image-url", source)
        self.assertIn("optionNode = node.closest", source)
        self.assertIn("visibleSkuRowImageMap", source)
        self.assertIn("visible_sku_row", source)
        self.assertIn("matchedOwners.size !== 1", source)
        self.assertNotIn('sku_image_source: "visible_sku_row_order"', source)

    def test_seerfar_pipeline_jobs_can_return_product_scoped_keyword_evidence(self):
        source = (
            Path(__file__).resolve().parents[1]
            / "collector/edge-extension/src/seerfar-content.ts"
        ).read_text(encoding="utf-8")
        self.assertIn("job.import_path", source)
        self.assertIn("factoryRequest(importPath", source)
        self.assertIn("SEERFAR_LOGIN_REQUIRED", source)
        self.assertIn("session_state", source)

    def test_seerfar_only_uses_an_already_open_logged_in_tab(self):
        source = (
            Path(__file__).resolve().parents[1]
            / "collector/edge-extension/src/background.ts"
        ).read_text(encoding="utf-8")
        self.assertNotIn("wakeSeerfarKeywordWorker", source)
        self.assertNotIn('chrome.tabs.create({ url: "https://seerfar.cn/', source)
        self.assertNotIn("chrome.alarms", source)

    def test_seerfar_targets_its_own_query_controls_and_rejects_chinese_rows(self):
        source = (
            Path(__file__).resolve().parents[1]
            / "collector/edge-extension/src/seerfar-content.ts"
        ).read_text(encoding="utf-8")
        self.assertIn('#magnet-keyword', source)
        self.assertIn('#tab-keyword-magnet button.quick-search', source)
        self.assertIn('#reverse-keyword', source)
        self.assertIn('isUsableMarketKeyword', source)
        self.assertIn("timeoutMs = 20000", source)

    def test_server_rechecks_seerfar_keywords_against_product_specs(self):
        # 2026-08-14：市场情报路由与助手已抽取到 market_routes.py（由 app.py 底部
        # exec 进同一 globals），源码断言改指真实所在文件。
        source = (
            Path(__file__).resolve().parents[1]
            / "collector/local-ingest/market_routes.py"
        ).read_text(encoding="utf-8")
        self.assertIn("def _seerfar_safe_keyword_rows", source)
        self.assertIn("Seerfar 返回词与当前商品规格不一致，未导入", source)
        self.assertIn("dimensional_mode_pattern", source)
        self.assertIn("overlap_count", source)

    def test_collector_warms_lazy_detail_images(self):
        source = (
            Path(__file__).resolve().parents[1]
            / "collector/edge-extension/src/content.ts"
        ).read_text(encoding="utf-8")
        self.assertIn("warmDetailImages", source)
        self.assertIn('img.getAttribute("data-lazy-src")', source)
        self.assertIn('img.getAttribute("data-ks-lazyload")', source)
        self.assertIn("#desc-lazyload-container", source)

    def test_extension_defaults_to_current_lan_workbench(self):
        root = Path(__file__).resolve().parents[1] / "collector/edge-extension"
        expected = "http://192.168.3.13:8765"
        for relative in [
            "src/background.ts",
            "src/popup.ts",
            "src/content.ts",
            "popup.html",
        ]:
            text = (root / relative).read_text(encoding="utf-8")
            self.assertIn(expected, text)
        self.assertIn("LEGACY_LOCAL_FACTORY_URLS", (root / "src/background.ts").read_text(encoding="utf-8"))
        self.assertIn("LEGACY_LOCAL_FACTORY_URLS", (root / "src/popup.ts").read_text(encoding="utf-8"))

    def test_extension_opens_collection_route_not_legacy_workbench(self):
        root = Path(__file__).resolve().parents[1] / "collector/edge-extension"
        for relative in ["src/popup.ts", "popup.js"]:
            text = (root / relative).read_text(encoding="utf-8")
            self.assertIn('workbenchEntryUrl("1688")', text)
            self.assertIn('workbenchEntryUrl("ozon")', text)
            self.assertIn('"/1688-collection"', text)
            self.assertIn('"/ozon-reference"', text)
            self.assertNotIn('${factoryConfig.baseUrl}/workbench', text)

    def test_ozon_reference_collector_extracts_lazy_and_script_images(self):
        source = (
            Path(__file__).resolve().parents[1]
            / "collector/edge-extension/src/content.ts"
        ).read_text(encoding="utf-8")
        manifest = (
            Path(__file__).resolve().parents[1]
            / "collector/edge-extension/manifest.json"
        ).read_text(encoding="utf-8")
        background = (
            Path(__file__).resolve().parents[1]
            / "collector/edge-extension/src/background.ts"
        ).read_text(encoding="utf-8")
        self.assertIn("extractOzonImageUrlsFromText", source)
        self.assertIn("ir|cdn", source)
        self.assertIn('link[rel="preload"]', source)
        self.assertIn('[style*="background"]', source)
        self.assertIn('document.documentElement.innerHTML.slice(0, 3_000_000)', source)
        self.assertIn('replace(/\\\\u002F/g, "/")', source)
        self.assertIn('w[hc]', source)
        self.assertIn('"/wc1000/"', source)
        self.assertIn("FACTORY_FETCH_IMAGE_DATA_URL", source)
        self.assertIn("FACTORY_FETCH_IMAGE_DATA_URL", background)
        self.assertIn("collectOzonInlineImages", source)
        self.assertIn("https://*.ozone.ru/*", manifest)
        self.assertIn("https://*.ozonusercontent.com/*", manifest)


if __name__ == "__main__":
    unittest.main()
