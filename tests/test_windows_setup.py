import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class WindowsSetupTest(unittest.TestCase):
    def test_codex_facing_windows_bootstrap_builds_both_frontends(self):
        script = (ROOT / "scripts/setup_windows.ps1").read_text(encoding="utf-8")
        self.assertIn("Python.Python.3.12", script)
        self.assertIn("OpenJS.NodeJS.LTS", script)
        self.assertIn("@openai/codex", script)
        self.assertIn('Push-Location "collector\\workbench-command-center"', script)
        self.assertIn('Push-Location "collector\\edge-extension"', script)
        self.assertIn("npm.cmd run build", script)
        self.assertIn(".venv\\Scripts\\python.exe", script)
        self.assertIn("-m uvicorn", script)

    def test_pipeline_uses_platform_specific_venv_and_process_groups(self):
        runner = (ROOT / "scripts/run_batch.py").read_text(encoding="utf-8")
        self.assertIn('"Scripts/python.exe" if os.name == "nt" else "bin/python"', runner)
        self.assertIn("subprocess.CREATE_NEW_PROCESS_GROUP", runner)
        self.assertIn('["taskkill", "/PID", str(process.pid), "/T", "/F"]', runner)

    def test_readme_warns_that_github_source_requires_frontend_build(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("setup_windows.ps1", readme)
        self.assertIn("dist", readme)


if __name__ == "__main__":
    unittest.main()
