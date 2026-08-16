import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.image_slot_scheduler import pending_slots
from scripts.image_wave_executor import execute_image_slot_waves
from scripts.ozon_metadata_prewarm import prewarm_category_tree
from scripts.pipeline_observability import (
    shared_analysis_cache_restore,
    shared_analysis_cache_store,
    shared_analysis_input_hash,
)
from scripts.run_batch import (
    BatchSafeStopRequested,
    complete_embedded_image_qc,
    image_slot_log_path,
    image_slot_prompt,
    image_slot_result_path,
    run_registered_process,
    run_single_image_slot,
    russian_copy_quality_errors,
    safe_stop_requested,
)

VALID_HASHTAGS = [
    "#товар", "#покупка", "#практично", "#удобно", "#надежно", "#хранение",
    "#организация", "#дом", "#дача", "#кухня", "#гараж", "#мастерская",
    "#поездка", "#семья", "#качество", "#комфорт", "#выбор", "#польза",
    "#решение", "#стиль", "#заказ", "#простота", "#ежедневно", "#аккуратно",
    "#чистота", "#безопасно", "#универсально", "#сезон", "#интерьер", "#порядок",
]


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


class FakeOzonReadClient:
    def __init__(self):
        self.calls = 0

    def get_category_tree(self, language="DEFAULT"):
        self.calls += 1
        category_name = "测试类目" if language == "ZH_HANS" else "Тестовая категория"
        type_name = "测试类型" if language == "ZH_HANS" else "Тестовый тип"
        return {"result": [{
            "description_category_id": 1,
            "category_name": category_name,
            "children": [{"type_id": 2, "type_name": type_name}],
        }]}


class PipelineSpeedOptimizationTests(unittest.TestCase):
    def test_safe_stop_only_accepts_manual_or_network_modes(self):
        with tempfile.TemporaryDirectory() as directory:
            stop_path = Path(directory) / "safe-stop-request.json"
            with patch("scripts.run_batch.SAFE_STOP_REQUEST_PATH", stop_path):
                write_json(stop_path, {"batch_id": "B-RUNNING", "mode": "priority_manual_upload"})
                self.assertFalse(safe_stop_requested("B-RUNNING"))
                write_json(stop_path, {"batch_id": "B-RUNNING", "mode": "manual_operator_stop"})
                self.assertTrue(safe_stop_requested("B-RUNNING"))
                write_json(stop_path, {"batch_id": "B-RUNNING", "mode": "system_network_failure"})
                self.assertTrue(safe_stop_requested("B-RUNNING"))
                self.assertFalse(safe_stop_requested("B-OTHER"))

    def test_image_wave_executor_reaches_three_real_concurrent_slots(self):
        with tempfile.TemporaryDirectory() as directory:
            product = Path(directory) / "products/P100001"
            main = [
                {
                    "slot": f"main-{index}", "image_type": "main", "operation": "edit_real_image",
                    "status": "planned", "output_path": str(Path(directory) / f"main-{index}.png"),
                }
                for index in range(3)
            ]
            detail = [
                {
                    "slot": f"detail-{index}", "image_type": "scene", "operation": "edit_real_image",
                    "status": "planned", "output_path": str(Path(directory) / f"detail-{index}.png"),
                }
                for index in range(3)
            ]
            write_json(product / "output/image-plan.json", {
                "generator_contract": {"true_parallel_slot_executor": True},
                "main_images": main, "detail_images": detail, "disclaimer_images": [],
            })
            lock = threading.Lock()
            active = 0
            maximum = 0
            starts = []
            finishes = []

            def runner(slot, attempt):
                nonlocal active, maximum
                with lock:
                    active += 1
                    maximum = max(maximum, active)
                    starts.append(slot["slot"])
                time.sleep(0.08)
                with lock:
                    finishes.append(slot["slot"])
                    active -= 1
                return {"slot": slot["slot"], "status": "passed", "attempt": attempt}

            result = execute_image_slot_waves(product, 99, runner, max_attempts=1)
            self.assertEqual(result["concurrency"], 3)
            self.assertEqual(maximum, 3)
            self.assertEqual(len(result["passed"]), 6)
            self.assertEqual(set(starts[:3]), {"main-0", "main-1", "main-2"})
            self.assertTrue(set(finishes[:3]).issubset({"main-0", "main-1", "main-2"}))
            self.assertEqual(set(starts[3:]), {"detail-0", "detail-1", "detail-2"})

    def test_image_wave_executor_retries_only_failed_slot(self):
        with tempfile.TemporaryDirectory() as directory:
            product = Path(directory) / "products/P100001"
            items = [
                {
                    "slot": f"main-{index}", "image_type": "main", "operation": "edit_real_image",
                    "status": "planned", "output_path": str(Path(directory) / f"main-{index}.png"),
                }
                for index in range(3)
            ]
            write_json(product / "output/image-plan.json", {
                "generator_contract": {"true_parallel_slot_executor": True},
                "main_images": items, "detail_images": [], "disclaimer_images": [],
            })
            calls = {item["slot"]: 0 for item in items}

            def runner(slot, attempt):
                calls[slot["slot"]] += 1
                status = "failed" if slot["slot"] == "main-1" and attempt == 1 else "passed"
                return {"slot": slot["slot"], "status": status, "attempt": attempt}

            result = execute_image_slot_waves(product, 3, runner, max_attempts=2)
            self.assertFalse(result["failed"])
            self.assertEqual(calls, {"main-0": 1, "main-1": 2, "main-2": 1})

    def test_image_wave_executor_does_not_retry_service_outage(self):
        with tempfile.TemporaryDirectory() as directory:
            product = Path(directory) / "products/P100001"
            item = {
                "slot": "main-1", "image_type": "main", "operation": "edit_real_image",
                "status": "planned", "output_path": str(Path(directory) / "main-1.png"),
            }
            write_json(product / "output/image-plan.json", {
                "generator_contract": {"true_parallel_slot_executor": True},
                "main_images": [item], "detail_images": [], "disclaimer_images": [],
            })
            calls = []

            def runner(slot, attempt):
                calls.append((slot["slot"], attempt))
                return {"slot": slot["slot"], "status": "service_unavailable", "attempt": attempt}

            result = execute_image_slot_waves(product, 3, runner, max_attempts=2)
            self.assertEqual(calls, [("main-1", 1)])
            self.assertEqual(len(result["service_unavailable"]), 1)

    def test_russian_copy_quality_gate_does_not_block_prompt_repairable_copy(self):
        copy_value = {
            "bullets_ru": [{"text_ru": "Один подтвержденный смысл"}],
            "description_ru": "Короткое описание.",
        }
        content_value = {"hashtags_ru": VALID_HASHTAGS[:30]}
        keyword_value = {
            "approved_keywords": [{
                "source": "source_fact",
                "evidence": ["input/source.json"],
            }]
        }
        self.assertEqual(russian_copy_quality_errors(copy_value, content_value, keyword_value), [])

    def test_russian_copy_quality_gate_accepts_live_complete_copy(self):
        copy_value = {
            "bullets_ru": [{"text_ru": f"Пункт {index}"} for index in range(5)],
            "description_ru": "\n\n".join(f"Абзац {index}." for index in range(5)),
        }
        content_value = {"hashtags_ru": VALID_HASHTAGS[:30]}
        keyword_value = {
            "approved_keywords": [{
                "source": "ozon_public_search",
                "evidence": ["https://www.ozon.ru/category/test/"],
            }]
        }
        self.assertEqual(
            russian_copy_quality_errors(copy_value, content_value, keyword_value), []
        )

    def test_safe_stop_interrupts_active_child_process_group(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            product = root / "products/P000001"
            product.mkdir(parents=True)
            (product / "status.json").write_text(
                json.dumps({"batch_id": "B-STOP", "current_step": "image_generation"}),
                encoding="utf-8",
            )
            worker = root / "worker.json"
            log = root / "child.log"
            with (
                patch("scripts.run_batch.product_worker_path", return_value=worker),
                patch("scripts.run_batch.safe_stop_requested", return_value=True),
                log.open("w", encoding="utf-8") as output,
            ):
                with self.assertRaises(BatchSafeStopRequested):
                    run_registered_process(
                        [sys.executable, "-c", "import time; time.sleep(30)"],
                        product,
                        output,
                        30,
                        completion_poll_seconds=0.05,
                    )
            self.assertFalse(worker.exists())

    def test_image_slots_schedule_all_mains_before_details(self):
        with tempfile.TemporaryDirectory() as directory:
            product = Path(directory) / "products/P100001"
            items = []
            for index in range(8):
                output = Path(directory) / f"image-{index}.png"
                items.append({
                    "slot": f"slot-{index}", "image_type": "main" if index == 0 else "detail",
                    "status": "planned", "output_path": str(output),
                })
            write_json(product / "output/image-plan.json", {
                "main_images": items[:1], "detail_images": items[1:7],
                "disclaimer_images": items[7:],
            })
            result = pending_slots(product, 3)
            self.assertEqual(result["concurrency"], 3)
            self.assertTrue(result["main_images_first"])
            self.assertEqual([len(wave) for wave in result["waves"]], [1, 3, 3, 1])
            self.assertEqual(result["waves"][0][0]["image_type"], "main")

    def test_completed_retry_request_does_not_hide_other_missing_slots(self):
        with tempfile.TemporaryDirectory() as directory:
            product = Path(directory) / "products/P100001"
            completed_output = Path(directory) / "completed.png"
            completed_output.write_bytes(b"image")
            write_json(product / "output/product-lock/retry-slot.json", {"audit": {"status": "pass"}})
            write_json(product / "output/image-regeneration-request.json", {"failed_slots": ["retry-slot"]})
            write_json(product / "output/image-plan.json", {
                "main_images": [{
                    "slot": "retry-slot", "image_type": "main", "status": "planned",
                    "output_path": str(completed_output),
                }],
                "detail_images": [{
                    "slot": "missing-slot", "image_type": "detail", "status": "planned",
                    "output_path": str(Path(directory) / "missing.png"),
                }],
                "disclaimer_images": [],
            })
            result = pending_slots(product, 3)
            self.assertEqual(result["pending_slot_count"], 1)
            self.assertEqual(result["waves"][0][0]["slot"], "missing-slot")

    def test_qc_request_requires_a_newer_slot_receipt_before_it_is_complete(self):
        with tempfile.TemporaryDirectory() as directory:
            product = Path(directory) / "products/P100001"
            output = product / "output/generated-images/detail/detail-001.png"
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"image")
            digest = __import__("hashlib").sha256(b"image").hexdigest()
            receipt = {
                "product_id": "P100001",
                "slot": "detail-001",
                "output_path": "products/P100001/output/generated-images/detail/detail-001.png",
                "status": "PASS",
                "sha256": digest,
                "hard_failures": [],
                "generation_source": "built_in_image_tool",
                "designer_prompt_followed": True,
                "local_script_generation": False,
                "checked_at": "2026-08-04T10:00:00+00:00",
            }
            write_json(product / "output/image-slot-results/detail-001.json", receipt)
            write_json(product / "output/image-regeneration-request.json", {
                "source": "image_qc",
                "requested_at": "2026-08-04T11:00:00+00:00",
                "requested_slots": ["detail-001"],
            })
            write_json(product / "output/image-plan.json", {
                "generator_contract": {"true_parallel_slot_executor": True},
                "main_images": [],
                "detail_images": [{
                    "slot": "detail-001",
                    "image_type": "detail",
                    "operation": "edit_real_image",
                    "status": "generated",
                    "output_path": "products/P100001/output/generated-images/detail/detail-001.png",
                }],
                "disclaimer_images": [],
            })

            self.assertEqual(pending_slots(product, 3)["pending_slot_count"], 1)
            receipt["checked_at"] = "2026-08-04T12:00:00+00:00"
            write_json(product / "output/image-slot-results/detail-001.json", receipt)
            self.assertEqual(pending_slots(product, 3)["pending_slot_count"], 0)

    def test_requested_needs_review_slots_are_rescheduled_with_deterministic_work_first(self):
        with tempfile.TemporaryDirectory() as directory:
            product = Path(directory) / "products/P100001"
            write_json(product / "output/image-regeneration-request.json", {
                "failed_slots": ["detail-ai", "detail-compose"],
            })
            write_json(product / "output/image-plan.json", {
                "main_images": [],
                "detail_images": [
                    {
                        "slot": "detail-ai", "image_type": "benefit",
                        "operation": "edit_real_image", "status": "needs_review",
                        "output_path": str(Path(directory) / "detail-ai.png"),
                    },
                    {
                        "slot": "detail-compose", "image_type": "size_spec",
                        "operation": "compose_from_real_images", "status": "needs_review",
                        "output_path": str(Path(directory) / "detail-compose.png"),
                    },
                ],
                "disclaimer_images": [],
            })
            result = pending_slots(product, 3)
            self.assertEqual(result["pending_slot_count"], 2)
            self.assertEqual(
                [item["slot"] for item in result["waves"][0]],
                ["detail-compose", "detail-ai"],
            )

    def test_unrequested_needs_review_main_reschedules_before_details(self):
        with tempfile.TemporaryDirectory() as directory:
            product = Path(directory) / "products/P100001"
            write_json(product / "output/image-plan.json", {
                "main_images": [
                    {
                        "slot": "main-failed",
                        "image_type": "main",
                        "operation": "edit_real_image",
                        "status": "needs_review",
                        "output_path": str(Path(directory) / "main-failed.png"),
                    },
                ],
                "detail_images": [
                    {
                        "slot": "detail-next",
                        "image_type": "detail",
                        "operation": "edit_real_image",
                        "status": "planned",
                        "output_path": str(Path(directory) / "detail-next.png"),
                    },
                ],
                "disclaimer_images": [],
            })
            result = pending_slots(product, 3)
            self.assertEqual(result["pending_slot_count"], 2)
            self.assertEqual(result["waves"][0][0]["slot"], "main-failed")
            self.assertEqual(result["waves"][1][0]["slot"], "detail-next")

    def test_image_slot_prompt_accepts_formal_product_dir_outside_code_root(self):
        with tempfile.TemporaryDirectory() as directory:
            formal_root = Path(directory) / "formal-project"
            product = formal_root / "products/P100001"
            output_path = "products/P100001/output/generated-images/variant-main/main-1.png"
            prompt = image_slot_prompt(
                product,
                {
                    "slot": "main-1",
                    "image_type": "main",
                    "operation": "edit_real_image",
                    "output_path": output_path,
                },
                1,
            )
            self.assertIn(str(product / "output/image-slot-results/main-1.json"), prompt)
            self.assertIn(str(product / "output/generated-images/variant-main/main-1.png"), prompt)
            self.assertLess(len(prompt), 900)

    def test_image_slot_prompt_rejects_receipt_and_output_outside_current_product(self):
        with tempfile.TemporaryDirectory() as directory:
            product = Path(directory) / "products/P100001"
            outside = Path(directory) / "other.png"
            with self.assertRaises(ValueError):
                image_slot_prompt(
                    product,
                    {
                        "slot": "main-1",
                        "image_type": "main",
                        "operation": "edit_real_image",
                        "output_path": str(outside),
                    },
                    1,
                )

    def test_image_slot_prelaunch_failure_writes_log_and_does_not_create_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            product = Path(directory) / "products/P100001"
            result = run_single_image_slot(
                product,
                {"image_generation_timeout_seconds": 1},
                {
                    "slot": "main-1",
                    "image_type": "main",
                    "operation": "edit_real_image",
                    "output_path": str(Path(directory) / "outside.png"),
                },
                1,
            )
            self.assertEqual(result["status"], "prelaunch_failure")
            self.assertEqual(result["attempt"], 1)
            self.assertFalse(image_slot_result_path(product, "main-1").exists())
            log_text = image_slot_log_path(product, "main-1").read_text(encoding="utf-8")
            self.assertIn("prelaunch_failure", log_text)
            self.assertIn("ValueError", log_text)

    def test_explicit_needs_review_recovery_reschedules_only_requested_unfinished_slot(self):
        with tempfile.TemporaryDirectory() as directory:
            product = Path(directory) / "products/P100001"
            output = Path(directory) / "products/P100001/output/generated-images/variant-main/main-1.png"
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"finished")
            digest = __import__("hashlib").sha256(b"finished").hexdigest()
            write_json(product / "output/image-slot-results/main-1.json", {
                "product_id": "P100001",
                "slot": "main-1",
                "output_path": "products/P100001/output/generated-images/variant-main/main-1.png",
                "status": "PASS",
                "sha256": digest,
                "hard_failures": [],
                "generation_source": "built_in_image_tool",
                "designer_prompt_followed": True,
                "local_script_generation": False,
            })
            write_json(product / "output/image-regeneration-request.json", {
                "requested_slots": ["main-1", "main-2"],
                "failure_kind": "prelaunch_failure",
                "consume_image_retry": False,
            })
            write_json(product / "output/image-plan.json", {
                "generator_contract": {"true_parallel_slot_executor": True},
                "main_images": [
                    {
                        "slot": "main-1",
                        "image_type": "main",
                        "operation": "edit_real_image",
                        "status": "needs_review",
                        "output_path": "products/P100001/output/generated-images/variant-main/main-1.png",
                    },
                    {
                        "slot": "main-2",
                        "image_type": "main",
                        "operation": "edit_real_image",
                        "status": "needs_review",
                        "output_path": "products/P100001/output/generated-images/variant-main/main-2.png",
                    },
                ],
                "detail_images": [],
                "disclaimer_images": [],
            })
            result = pending_slots(product, 3)
            self.assertEqual(result["pending_slot_count"], 1)
            self.assertEqual(result["waves"][0][0]["slot"], "main-2")

    def test_legacy_image_receipt_without_builtin_source_is_rescheduled(self):
        with tempfile.TemporaryDirectory() as directory:
            product = Path(directory) / "products/P100001"
            output = Path(directory) / "products/P100001/output/generated-images/variant-main/main-1.png"
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"finished")
            digest = __import__("hashlib").sha256(b"finished").hexdigest()
            write_json(product / "output/image-slot-results/main-1.json", {
                "product_id": "P100001",
                "slot": "main-1",
                "output_path": "products/P100001/output/generated-images/variant-main/main-1.png",
                "status": "PASS",
                "sha256": digest,
                "hard_failures": [],
            })
            write_json(product / "output/image-regeneration-request.json", {
                "requested_slots": ["main-1"],
            })
            write_json(product / "output/image-plan.json", {
                "generator_contract": {"true_parallel_slot_executor": True},
                "main_images": [{
                    "slot": "main-1",
                    "image_type": "main",
                    "operation": "edit_real_image",
                    "status": "needs_review",
                    "output_path": "products/P100001/output/generated-images/variant-main/main-1.png",
                }],
                "detail_images": [],
                "disclaimer_images": [],
            })

            result = pending_slots(product, 3)

            self.assertEqual(result["pending_slot_count"], 1)
            self.assertEqual(result["waves"][0][0]["slot"], "main-1")

    def test_legacy_plan_counts_saved_png_without_pixel_lock(self):
        """A saved image from the pre-lock plan must not freeze progress at 78%."""
        with tempfile.TemporaryDirectory() as directory:
            product = Path(directory) / "products/P100001"
            output = Path(directory) / "saved.png"
            output.write_bytes(b"png")
            write_json(product / "output/image-plan.json", {
                "generator_contract": {"product_pixel_lock_required": False},
                "main_images": [{
                    "slot": "main-1", "status": "generated", "image_type": "main",
                    "output_path": str(output),
                }],
                "detail_images": [], "disclaimer_images": [],
            })
            from scripts.run_batch import completed_image_slot_count
            self.assertEqual(completed_image_slot_count(product), 1)

    def test_shared_analysis_cache_uses_source_and_image_fingerprints(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "products/P100001"
            second = root / "products/P100002"
            source = {
                "source_url": "https://detail.1688.com/offer/1.html",
                "title_cn": "测试商品",
                "product_attributes": [{"name_cn": "材质", "value_cn": "硅胶"}],
                "skus": [{"sku_id": "sku-1", "sku_name": "绿色", "option_values": []}],
            }
            for product in (first, second):
                write_json(product / "input/source.json", source)
                image = product / "input/main-images/main.jpg"
                image.parent.mkdir(parents=True, exist_ok=True)
                image.write_bytes(b"same-real-image")
            write_json(first / "output/product-analysis.json", {
                "product_id": "P100001",
                "source_refs": ["products/P100001/input/source.json"],
                "facts": {"title_cn": "测试商品"},
            })
            with patch("scripts.pipeline_observability.ROOT", root):
                first_key = shared_analysis_input_hash(first)
                second_key = shared_analysis_input_hash(second)
                self.assertEqual(first_key, second_key)
                shared_analysis_cache_store(first, first_key)
                self.assertTrue(shared_analysis_cache_restore(second, second_key))
            restored = json.loads((second / "output/product-analysis.json").read_text())
            self.assertEqual(restored["product_id"], "P100002")
            self.assertEqual(restored["source_refs"], ["products/P100002/input/source.json"])

    def test_ozon_prewarm_uses_read_only_tree_and_reuses_fresh_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            client = FakeOzonReadClient()
            settings = {"shop_name": "test", "ozon_metadata_cache_hours": 24}
            first = prewarm_category_tree(settings, root=root, client=client)
            second = prewarm_category_tree(settings, root=root, client=client)
            self.assertEqual(first["status"], "prewarmed")
            self.assertEqual(second["status"], "cache_fresh")
            self.assertEqual(client.calls, 2)
            cache = json.loads(Path(first["cache_path"]).read_text(encoding="utf-8"))
            self.assertEqual(cache["api_language"], "ZH_HANS")
            self.assertEqual(cache["catalog_compatibility"], "runtime_live_tree")

    def test_embedded_qc_completes_without_second_codex_task(self):
        with tempfile.TemporaryDirectory() as directory:
            product = Path(directory) / "products/P100001"
            write_json(product / "output/image-qc-report.json", {"decision": "pass"})
            write_json(product / "output/image-regeneration-request.json", {"failed_slots": ["detail-001"]})
            write_json(product / "input/source.json", {"product_id": "P100001"})
            write_json(product / "input/raw-snapshot.json", {"product_id": "P100001"})
            with (
                patch("scripts.run_batch.run_local_step", return_value=True) as local_step,
                patch("scripts.run_batch.complete_step") as checkpoint,
                patch("scripts.run_batch.cache_store") as cache,
            ):
                result = complete_embedded_image_qc(
                    product, {"merge_image_generation_and_qc": True}, product / "log.txt"
            )
            self.assertTrue(result)
            self.assertFalse((product / "output/image-regeneration-request.json").exists())
            local_step.assert_called_once()
            checkpoint.assert_called_once_with(product, "image_qc")
            cache.assert_called_once()


if __name__ == "__main__":
    unittest.main()
