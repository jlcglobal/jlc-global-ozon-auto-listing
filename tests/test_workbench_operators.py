import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from scripts.workbench_operators import authenticate, list_operators, upsert_operator


class WorkbenchOperatorRegistryTest(unittest.TestCase):
    def test_generated_code_is_returned_once_and_registry_stores_only_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            item, code = upsert_operator(root, {"id": "alice", "display_name": "小李", "role": "member"})
            self.assertTrue(code)
            self.assertEqual(authenticate(root, code)["id"], "alice")
            registry_text = (root / "config/operators.json").read_text()
            self.assertNotIn(code, registry_text)
            self.assertIn("pbkdf2_sha256$", registry_text)
            self.assertTrue(item["access_code_configured"])

    def test_operator_list_never_contains_access_code_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            upsert_operator(root, {"id": "alice", "display_name": "小李", "role": "member", "access_code": "secret-code"})
            exported = json.dumps(list_operators(root))
            self.assertNotIn("secret-code", exported)
            self.assertNotIn("access_code_hash", exported)

    def test_legacy_sha256_access_code_remains_compatible(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "config/operators.json"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({
                "schema_version": "1.0.0",
                "operators": [{
                    "id": "legacy", "display_name": "旧成员", "role": "member", "enabled": True,
                    "access_code_hash": hashlib.sha256(b"old-code").hexdigest(),
                }],
            }), encoding="utf-8")
            self.assertEqual(authenticate(root, "old-code")["id"], "legacy")


if __name__ == "__main__":
    unittest.main()
