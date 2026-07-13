import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from scripts import package_edge_extension


class EdgeExtensionPackagingTest(unittest.TestCase):
    def test_release_is_built_from_single_source_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            release_dir = Path(directory)
            with patch.object(package_edge_extension, "RELEASE_DIR", release_dir):
                output = package_edge_extension.package_extension()
            self.assertEqual(output.name, "1688商品采集插件-0.4.6.zip")
            with zipfile.ZipFile(output) as archive:
                names = set(archive.namelist())
                self.assertIn("edge-extension/manifest.json", names)
                self.assertIn("edge-extension/category-tree.zh-CN.json", names)
                self.assertFalse(any("node_modules" in name for name in names))


if __name__ == "__main__":
    unittest.main()
