import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class WorkbenchLauncherTest(unittest.TestCase):
    def test_launcher_is_portable_and_reuses_running_service(self):
        launcher = (ROOT / "启动工作室工作台.command").read_text(encoding="utf-8")
        self.assertIn('PROJECT_DIR="${0:A:h}"', launcher)
        self.assertIn("is_healthy", launcher)
        self.assertIn("工作台已经在运行", launcher)
        self.assertIn("--host 0.0.0.0", launcher)
        self.assertIn("--port 8765", launcher)
        self.assertIn("import fastapi, uvicorn", launcher)
        self.assertNotIn("/Users/apple/Documents/crossborder-ai-factory", launcher)


if __name__ == "__main__":
    unittest.main()
