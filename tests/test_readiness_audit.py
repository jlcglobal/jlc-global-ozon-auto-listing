import json
import tempfile
import unittest
from pathlib import Path

from scripts.readiness_audit import build_audit, area_status


def write(path: Path, text: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class ReadinessAuditTest(unittest.TestCase):
    def test_area_status_requires_code_tests_and_keywords(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            area = {
                "code": "demo",
                "label": "演示",
                "weight": 10,
                "description": "demo",
                "files": ["scripts/demo.py"],
                "tests": ["tests/test_demo.py"],
                "keywords": ["critical_rule"],
            }
            missing = area_status(area, root)
            self.assertEqual(missing["status"], "missing")

            write(root / "scripts/demo.py", "critical_rule = True\n")
            write(root / "tests/test_demo.py", "def test_demo(): pass\n")
            covered = area_status(area, root)
            self.assertEqual(covered["status"], "covered")
            self.assertEqual(covered["coverage_percent"], 100)

    def test_build_audit_is_read_only_and_reports_gaps(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write(root / "logs/project-self-check.json", json.dumps({
                "decision": "PASS",
                "checked_at": "2026-07-28T00:00:00+08:00",
                "ozon_write_api_calls": 0,
                "inventory_api_calls": 0,
                "network_calls": 0,
                "checks": [],
            }))
            write(root / "products/P000001/status.json", json.dumps({"status": "NEEDS_ATTENTION"}))
            write(root / "products/P000001/input/source.json", json.dumps({
                "product_id": "P000001",
                "collection_id": "COL-00000001",
                "source_kind": "workbench_collection",
            }))

            report = build_audit(root, run_check=False)

            self.assertEqual(report["ozon_write_api_calls"], 0)
            self.assertEqual(report["inventory_api_calls"], 0)
            self.assertEqual(report["runtime"]["attention_products"], ["P000001"])
            self.assertIn(report["decision"], {"NOT_READY", "NEEDS_TARGETED_TESTING"})
            self.assertTrue(report["top_gaps"])
            self.assertTrue(report["next_minimum_actions"])


if __name__ == "__main__":
    unittest.main()
