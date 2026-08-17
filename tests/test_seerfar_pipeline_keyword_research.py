import json
import tempfile
import unittest
from pathlib import Path

from scripts.seerfar_pipeline_keyword_research import queue_research, research_path, seed_keyword, wait_for_research
from scripts.seerfar_competitor_evidence import extract_public_copy, top_competitor_urls


def write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


class SeerfarPipelineKeywordResearchTest(unittest.TestCase):
    def test_uses_russian_product_type_as_new_product_seed(self):
        with tempfile.TemporaryDirectory() as directory:
            product = Path(directory) / "P000201"
            write_json(product / "output/product-analysis.json", {"product_type": "Складной туристический стул"})
            write_json(product / "output/product-positioning.json", {})
            write_json(product / "output/ozon-category.json", {"category_name": "Табурет"})

            self.assertEqual(seed_keyword(product), "Складной туристический стул")

    def test_queues_one_browser_job_and_writes_non_sensitive_artifact(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            product = root / "P000202"
            jobs_path = root / "runtime/seerfar-keyword-jobs.json"
            write_json(product / "output/product-analysis.json", {"product_type": "Термос дорожный"})
            write_json(product / "output/product-positioning.json", {})
            write_json(product / "output/ozon-category.json", {})

            result = queue_research(product, jobs_path=jobs_path)
            jobs = json.loads(jobs_path.read_text(encoding="utf-8"))["jobs"]

            self.assertEqual(result["status"], "waiting_browser")
            self.assertEqual(result["seed_keyword"], "Термос дорожный")
            self.assertEqual(len(jobs), 1)
            self.assertEqual(jobs[0]["kind"], "pipeline_keyword_research")
            self.assertEqual(jobs[0]["mode"], "keyword_miner")
            self.assertIn("seerfar/pipeline/import", jobs[0]["import_path"])
            self.assertNotIn("cookie", json.dumps(jobs[0]).lower())

    def test_login_required_stops_waiting_without_replacing_the_reason(self):
        with tempfile.TemporaryDirectory() as directory:
            product = Path(directory) / "P000203"
            write_json(research_path(product), {
                "schema_version": "1.0.0",
                "product_id": product.name,
                "status": "login_required",
                "reason": "Seerfar 登录已失效",
                "rows": [],
            })

            result = wait_for_research(product, timeout_seconds=0)

            self.assertEqual(result["status"], "login_required")
            self.assertEqual(result["reason"], "Seerfar 登录已失效")

    def test_competitor_evidence_uses_only_public_ozon_links_and_compact_copy(self):
        urls = top_competitor_urls([
            {"count": 20, "related_product_urls": ["https://www.ozon.ru/product/123456789/", "https://example.com/product/1"]},
            {"count": 10, "related_product_urls": ["https://www.ozon.ru/product/987654321/"]},
        ], 3)
        item = extract_public_copy(urls[0], "<meta property='og:title' content='Набор для ванной'><meta name='description' content='Описание #ванная'>")

        self.assertEqual(urls, ["https://www.ozon.ru/product/123456789/", "https://www.ozon.ru/product/987654321/"])
        self.assertEqual(item["title"], "Набор для ванной")
        self.assertEqual(item["theme_tags"], ["#ванная"])


if __name__ == "__main__":
    unittest.main()
