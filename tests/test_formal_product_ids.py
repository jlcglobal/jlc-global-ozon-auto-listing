import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
APP_PATH = ROOT / "collector/local-ingest/app.py"
SPEC = importlib.util.spec_from_file_location("formal_id_workbench_app", APP_PATH)
workbench = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(workbench)


class FormalProductIdTests(unittest.TestCase):
    def test_audit_id_does_not_advance_formal_allocator_or_workbench(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            products = root / "products"
            (products / "P000006/input").mkdir(parents=True)
            (products / "P900001/input").mkdir(parents=True)
            (products / "P000006/input/source.json").write_text(json.dumps({"source_kind": "workbench_collection"}))
            (products / "P000006/status.json").write_text(json.dumps({"status": "COLLECTED"}))
            (products / "P900001/input/source.json").write_text(json.dumps({"source_kind": "workbench_collection"}))
            (products / "P900001/status.json").write_text(json.dumps({"status": "OFFLINE_ACCEPTANCE_SAMPLE"}))
            with patch.object(workbench, "ROOT", root), patch.object(workbench, "PRODUCTS_DIR", products):
                self.assertEqual([path.name for path in workbench.owned_product_dirs()], ["P000006"])
                self.assertEqual(workbench.create_product_id(), "P000007")
                self.assertTrue((products / "P000007").is_dir())


if __name__ == "__main__":
    unittest.main()
