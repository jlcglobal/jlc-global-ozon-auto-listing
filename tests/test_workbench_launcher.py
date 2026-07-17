import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class WorkbenchLauncherTest(unittest.TestCase):
    def test_launcher_is_portable_and_reuses_running_service(self):
        launcher = (ROOT / "启动工作室工作台.command").read_text(encoding="utf-8")
        self.assertIn('PROJECT_DIR="${0:A:h}"', launcher)
        self.assertIn("is_healthy", launcher)
        self.assertIn("工作台已经在运行", launcher)
        self.assertIn("import fastapi, uvicorn", launcher)
        self.assertIn("/usr/bin/nohup", launcher)
        self.assertIn("run_workbench_service.sh", launcher)
        self.assertIn("工作台已在后台运行", launcher)
        self.assertIn("workbench-stop-requested", launcher)
        self.assertNotIn("请保持这个终端窗口开启", launcher)
        self.assertNotIn("/Users/apple/Documents/crossborder-ai-factory", launcher)

    def test_background_service_restarts_failed_server(self):
        script = (ROOT / "scripts/run_workbench_service.sh").read_text(encoding="utf-8")
        self.assertIn('while [[ ! -f "$STOP_FILE" ]]', script)
        self.assertIn("2秒后自动重启", script)
        self.assertIn("workbench-stop-requested", script)
        self.assertIn("安全退出", script)
        self.assertIn("--host 0.0.0.0", script)
        self.assertIn("--port 8765", script)

    def test_mac_app_bundle_launches_the_existing_workbench(self):
        plist = ROOT / "mac-launcher/AI Factory.app/Contents/Info.plist"
        executable = ROOT / "mac-launcher/AI Factory.app/Contents/MacOS/AI Factory"
        self.assertTrue(plist.is_file())
        self.assertTrue(executable.is_file())
        self.assertIn("com.hongchen.crossborder-ai-factory", plist.read_text(encoding="utf-8"))
        source = executable.read_text(encoding="utf-8")
        self.assertIn("run_workbench_service.sh", source)
        self.assertIn("http://127.0.0.1:8765/health", source)
        self.assertIn("http://127.0.0.1:8765/workbench", source)
        self.assertIn("workbench-stop-requested", source)
        self.assertNotIn("启动工作室工作台.command", source)


if __name__ == "__main__":
    unittest.main()
