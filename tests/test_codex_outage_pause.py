import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import scripts.run_batch as runner


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


class CodexOutagePauseTest(unittest.TestCase):
    def make_product(self, root: Path) -> Path:
        product = root / "products/P000201"
        write_json(product / "status.json", {
            "product_id": "P000201",
            "status": "PROCESSING",
            "current_step": "product_analysis",
            "next_action": "product_analysis",
            "completed_steps": ["collect_source", "validate_source"],
            "pending_steps": ["product_analysis", "category_match"],
            "retry_count_by_step": {},
            "warnings": [],
            "history": [],
            "steps": [],
        })
        return product

    def test_outage_preserves_checkpoint_and_does_not_create_fallback_analysis(self):
        with tempfile.TemporaryDirectory() as directory:
            product = self.make_product(Path(directory))
            status = runner.mark_codex_service_waiting(
                product, "product_analysis", {"codex_outage_retry_seconds": 30},
            )

            self.assertEqual(status["status"], "PROCESSING")
            self.assertEqual(status["current_step"], "product_analysis")
            self.assertEqual(status["next_action"], "product_analysis")
            self.assertEqual(status["completed_steps"], ["collect_source", "validate_source"])
            self.assertEqual(status["retry_count_by_step"], {})
            self.assertEqual(status["ai_service_state"], "waiting_for_recovery")
            self.assertFalse((product / "output/product-analysis.json").exists())

    def test_retry_delay_expires_without_changing_product_checkpoint(self):
        now = datetime.now(timezone.utc)
        status = {
            "ai_service_state": "waiting_for_recovery",
            "ai_service_retry_after": (now + timedelta(seconds=30)).isoformat(),
        }
        self.assertGreater(runner.codex_retry_remaining_seconds(status, now), 29)
        self.assertEqual(
            runner.codex_retry_remaining_seconds(status, now + timedelta(seconds=31)),
            0.0,
        )

    def test_clear_wait_state_keeps_same_next_action(self):
        with tempfile.TemporaryDirectory() as directory:
            product = self.make_product(Path(directory))
            runner.mark_codex_service_waiting(
                product, "product_analysis", {"codex_outage_retry_seconds": 30},
            )
            runner.clear_codex_service_waiting(product)
            status = json.loads((product / "status.json").read_text(encoding="utf-8"))

            self.assertEqual(status["ai_service_state"], "normal")
            self.assertEqual(status["next_action"], "product_analysis")
            self.assertEqual(status["completed_steps"], ["collect_source", "validate_source"])

    def test_only_current_codex_attempt_is_checked_for_transport_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "full-pipeline.log"
            log.write_text("old attempt: 403 Forbidden\n", encoding="utf-8")
            offset = log.stat().st_size
            with log.open("a", encoding="utf-8") as handle:
                handle.write("new attempt: schema validation failed\n")

            self.assertTrue(runner.codex_worker_unavailable(log))
            self.assertFalse(runner.codex_worker_unavailable(log, offset))

    def test_usage_limit_waits_without_consuming_product_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            product = self.make_product(root)
            log = root / "full-pipeline.log"
            log.write_text(
                "ERROR: You've hit your usage limit. Purchase more credits or try again later.\n",
                encoding="utf-8",
            )

            self.assertTrue(runner.codex_worker_unavailable(log))
            self.assertTrue(runner.codex_usage_limit_reached(log))
            status = runner.mark_codex_service_waiting(
                product,
                "product_analysis",
                {"codex_usage_limit_retry_seconds": 600},
                "usage_limit",
            )

            self.assertEqual(status["status"], "PROCESSING")
            self.assertEqual(status["next_action"], "product_analysis")
            self.assertEqual(status["retry_count_by_step"], {})
            self.assertEqual(status["error_code"], "AI_SERVICE_CAPACITY_WAIT")
            self.assertEqual(status["ai_service_reason"], "codex_usage_limit")
            self.assertIn("10分钟", status["error_message"])

    def test_local_fallback_is_removed_and_workbench_explains_the_wait(self):
        root = Path(__file__).resolve().parents[1]
        script = (root / "collector/local-ingest/static/workbench.js").read_text(encoding="utf-8")

        self.assertFalse((root / "scripts/local_analysis_fallback.py").exists())
        self.assertNotIn("write_source_only_analysis", runner.__dict__)
        self.assertIn("等待联网大模型恢复", script)
        self.assertIn("AI额度等待中", script)
        self.assertIn("codex_usage_limit", script)
        self.assertIn("不会使用本地备用分析", script)
        self.assertIn("ai_service_retry_after", script)

    def test_missing_codex_worker_is_classified_as_temporarily_unavailable(self):
        with patch.object(runner.shutil, "which", return_value=None):
            with self.assertRaisesRegex(FileNotFoundError, "Codex executable is unavailable"):
                runner.codex_command({})


if __name__ == "__main__":
    unittest.main()
