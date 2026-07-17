import json
import tempfile
import unittest
from pathlib import Path

from scripts.project_self_check import product_contract_class


class ProjectSelfCheckContractTests(unittest.TestCase):
    def _product(self, status, source):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        product = Path(temporary.name) / "P000007"
        (product / "input").mkdir(parents=True)
        (product / "status.json").write_text(json.dumps(status), encoding="utf-8")
        (product / "input/source.json").write_text(json.dumps(source), encoding="utf-8")
        return product

    def test_archived_product_is_audit_data(self):
        product = self._product(
            {"status": "ARCHIVED", "archived_at": "2026-07-16T00:00:00+08:00"},
            {},
        )
        self.assertEqual(product_contract_class(product), "archived")

    def test_pre_contract_product_is_not_reported_as_current(self):
        product = self._product({"status": "FAILED"}, {"product_id": "P000007"})
        self.assertEqual(product_contract_class(product), "pre_contract")

    def test_current_contract_requires_matching_workbench_identity(self):
        product = self._product(
            {"status": "WAITING_MANUAL_REVIEW"},
            {
                "product_id": "P000007",
                "collection_id": "C-007",
                "source_kind": "workbench_collection",
            },
        )
        self.assertEqual(product_contract_class(product), "current")

    def test_cross_product_identity_is_pre_contract(self):
        product = self._product(
            {"status": "WAITING_MANUAL_REVIEW"},
            {
                "product_id": "P000008",
                "collection_id": "C-008",
                "source_kind": "workbench_collection",
            },
        )
        self.assertEqual(product_contract_class(product), "pre_contract")


if __name__ == "__main__":
    unittest.main()
