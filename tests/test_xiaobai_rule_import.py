import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from scripts.import_xiaobai_ozon_rules import RuleImportError, import_rule_archive


class XiaobaiRuleImportTests(unittest.TestCase):
    def archive(self, root: Path, extra_file: bool = False) -> Path:
        category = {"categoryId": "10", "typeId": "20", "nameRu": "Test"}
        files = {
            "categories.json": [category],
            "attributes.json": [{
                "categoryId": "10",
                "typeId": "20",
                "requiredAttributes": [{"attributeId": "85", "nameRu": "Бренд"}],
                "optionalAttributes": [],
            }],
            "variants.json": [{
                "categoryId": "10",
                "typeId": "20",
                "attributes": [{"attributeId": "10096", "nameRu": "Цвет товара"}],
            }],
            "sync-errors.json": [],
            "version.json": {
                "version": "test-rules",
                "categoryCount": 1,
                "attributeCount": 1,
                "attributeRuleCount": 1,
                "variantRuleCount": 1,
                "updatedAt": "2026-07-10T00:00:00Z",
            },
        }
        archive = root / "rules.zip"
        with zipfile.ZipFile(archive, "w") as output:
            for name, value in files.items():
                output.writestr(name, json.dumps(value))
            if extra_file:
                output.writestr("unexpected.json", "{}")
        return archive

    def test_import_is_validated_and_idempotent(self):
        with tempfile.TemporaryDirectory() as temp_name:
            root = Path(temp_name)
            archive = self.archive(root)
            first = import_rule_archive(archive, root / "metadata")
            second = import_rule_archive(archive, root / "metadata")
            self.assertEqual(first, second)
            report = json.loads((first / "import-report.json").read_text())
            self.assertEqual(report["status"], "valid")
            self.assertEqual(report["category_count"], 1)

    def test_unexpected_archive_file_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp_name:
            root = Path(temp_name)
            archive = self.archive(root, extra_file=True)
            with self.assertRaises(RuleImportError):
                import_rule_archive(archive, root / "metadata")


if __name__ == "__main__":
    unittest.main()
