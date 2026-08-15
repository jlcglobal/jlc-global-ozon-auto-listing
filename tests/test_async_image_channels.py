import json
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class AsyncImageChannelPerformanceTest(unittest.TestCase):
    def test_image_status_monitor_is_one_shot_not_a_polling_loop(self):
        source = (ROOT / "scripts/image_status_monitor.py").read_text(encoding="utf-8")
        self.assertNotIn("while queue_path.is_file()", source)
        self.assertNotIn("time.sleep", source)

    def test_twenty_products_release_main_slots_without_remote_blocking(self):
        completed = subprocess.run(
            [sys.executable, "scripts/performance_regression_20.py"],
            cwd=ROOT, capture_output=True, text=True, check=True,
        )
        report = json.loads(completed.stdout)
        self.assertEqual(report["submitted_count"], 20)
        self.assertFalse(report["pending_product_blocked_batch"])
        self.assertLessEqual(report["observed_max_channel_concurrency"], 4)
        self.assertFalse(report["fixed_ten_minute_wait_detected"])
        self.assertEqual(report["external_network_calls"], 0)
        self.assertEqual(report["inventory_calls"], 0)


if __name__ == "__main__":
    unittest.main()
