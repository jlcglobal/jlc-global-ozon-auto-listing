#!/usr/bin/env python3
"""Offline production-readiness coverage audit for the local workbench.

This script does not call Ozon, inventory, warehouse or any external AI API.
It reports what has code evidence, what has test evidence, and what remains
unproven so the operator does not need to manually click every workflow path.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List


ROOT = Path(__file__).resolve().parents[1]
REPORT_PATH = ROOT / "logs/readiness-audit.json"
SELF_CHECK_PATH = ROOT / "logs/project-self-check.json"


RISK_AREAS: List[Dict[str, Any]] = [
    {
        "code": "product_fact_lock",
        "label": "商品事实锁",
        "weight": 12,
        "description": "来源、SKU、尺寸重量、unknown/estimated 分层和最终事实边界。",
        "files": [
            "scripts/product_fact_merger.py",
            "scripts/production_input_guard.py",
            "scripts/attribute_fill_input.py",
        ],
        "tests": [
            "tests/test_sku_fact_attribute_payload.py",
            "tests/test_production_input_boundaries.py",
        ],
        "keywords": [
            "product-fact-lock.json",
            "freeze_sku_run_snapshot",
            "source_snapshot_binding",
        ],
    },
    {
        "code": "sku_variant_merge",
        "label": "SKU 变体合并",
        "weight": 10,
        "description": "按已选 SKU、is_aspect、SKU 图片和平台卡片规则处理变体。",
        "files": [
            "variant-compatibility-checker/variant_compatibility_checker/service.py",
            "scripts/sku_image_bindings.py",
            "scripts/ozon_attribute_compiler.py",
        ],
        "tests": [
            "tests/test_variant_compatibility_checker.py",
            "tests/test_variant_image_strategy.py",
            "tests/test_sku_fact_attribute_payload.py",
        ],
        "keywords": [
            "is_aspect",
            "attributes_by_sku",
            "sku_image",
        ],
    },
    {
        "code": "ozon_category_attributes",
        "label": "Ozon 类目属性和字典",
        "weight": 12,
        "description": "读取当前类目字段，按 required/aspect/dictionary 编译合法属性。",
        "files": [
            "scripts/attribute_fill_input.py",
            "scripts/ozon_attribute_compiler.py",
            "ozon-field-completion/ozon_field_completion/service.py",
        ],
        "tests": [
            "tests/test_attribute_fill_input_compaction.py",
            "tests/test_dynamic_ozon_attributes.py",
            "tests/test_ozon_field_completion.py",
            "tests/test_sku_fact_attribute_payload.py",
        ],
        "keywords": [
            "category_attribute_plan",
            "dictionary_value_id",
            "required_summary",
        ],
    },
    {
        "code": "image_fact_hallucination",
        "label": "图片事实幻觉",
        "weight": 12,
        "description": "生图只能围绕当前商品事实、参考图和本体拓扑，不凭类目模板乱加内容。",
        "files": [
            "scripts/image_planner.py",
            "scripts/image_generator_contract.py",
            "scripts/image_qc.py",
            ".agents/skills/image-generator/SKILL.md",
        ],
        "tests": [
            "tests/test_image_workflow_fix.py",
            "tests/test_batch_pipeline.py",
            "tests/test_stage34_image_qc.py",
            "tests/test_locked_product_images.py",
        ],
        "keywords": [
            "product body topology",
            "image-design-revision-request.json",
            "Category is upload metadata only",
        ],
    },
    {
        "code": "image_qc_designer_repair",
        "label": "图片质检失败自动回设计师提示词",
        "weight": 8,
        "description": "硬质检失败后回图片计划修失败图位提示词，再只重做失败图。",
        "files": [
            "scripts/run_batch.py",
            "scripts/image_planner.py",
            "scripts/image_qc.py",
        ],
        "tests": [
            "tests/test_batch_pipeline.py",
            "tests/test_image_workflow_fix.py",
            "tests/test_image_host_recovery.py",
        ],
        "keywords": [
            "route_image_qc_failures_back_to_image_plan",
            "revision_prompt_addendum",
            "preserve_passed_images",
        ],
    },
    {
        "code": "status_sync",
        "label": "状态显示统一",
        "weight": 10,
        "description": "后端状态桶、主按钮、React 轮询和旧状态兼容。",
        "files": [
            "collector/local-ingest/app.py",
            "collector/workbench-command-center/src/hooks/useProducts.ts",
            "collector/workbench-command-center/src/config/commandCenterConfig.ts",
            "scripts/validate_product.py",
        ],
        "tests": [
            "tests/test_workbench.py",
            "tests/test_workbench_gap_fill.py",
            "tests/test_collection_inbox.py",
        ],
        "keywords": [
            "effective_product_status",
            "product_primary_action",
            "productRefreshIntervalMs",
        ],
    },
    {
        "code": "batch_multi_store_resume",
        "label": "批次、多店铺和断点恢复",
        "weight": 10,
        "description": "多商品/多店铺不会互相卡死，失败步骤可继续或重试。",
        "files": [
            "scripts/pipeline_runtime.py",
            "scripts/run_batch.py",
            "scripts/multi_store_upload.py",
            "scripts/store_publications.py",
        ],
        "tests": [
            "tests/test_batch_pipeline.py",
            "tests/test_multi_store_upload.py",
            "tests/test_batch_confirmation.py",
            "tests/test_pipeline_speed_optimizations.py",
        ],
        "keywords": [
            "retry_failed_store",
            "auto_upload",
            "api_write_count",
        ],
    },
    {
        "code": "collector_detail_images",
        "label": "采集和详情图",
        "weight": 7,
        "description": "1688 采集、懒加载详情图、插件包和类目缓存。",
        "files": [
            "collector/edge-extension/src/content.ts",
            "collector/edge-extension/content.js",
            "collector/local-ingest/app.py",
        ],
        "tests": [
            "tests/test_edge_extension_packaging.py",
            "tests/test_stage2_collector.py",
            "tests/test_collector_category_selection.py",
        ],
        "keywords": [
            "detail-images",
            "data-src",
            "category-rules-cache",
        ],
    },
    {
        "code": "pricing_measurements",
        "label": "价格、重量和尺寸",
        "weight": 7,
        "description": "进货价、包装重量尺寸、线路货值和估算边界。",
        "files": [
            "pricing-engine/pricing_engine/service.py",
            "pricing-engine/pricing_engine/source_measurements.py",
            "scripts/product_fact_merger.py",
        ],
        "tests": [
            "tests/test_pricing_engine.py",
            "tests/test_run_batch_price_fallback.py",
            "tests/test_sku_fact_attribute_payload.py",
        ],
        "keywords": [
            "min_value_rub_exclusive",
            "package_weight",
            "estimated",
        ],
    },
    {
        "code": "upload_write_boundary",
        "label": "上传写接口边界",
        "weight": 7,
        "description": "只有授权批次才写 Ozon，不提交库存、仓库、激活字段。",
        "files": [
            "scripts/run_batch.py",
            "scripts/multi_store_upload.py",
            "ozon-uploader/ozon_uploader/service.py",
            "scripts/project_self_check.py",
        ],
        "tests": [
            "tests/test_stage42_ozon_uploader.py",
            "tests/test_multi_store_upload.py",
            "tests/test_collection_inbox.py",
            "tests/test_batch_confirmation.py",
        ],
        "keywords": [
            "inventory_api_calls",
            "ozon_write_api_calls",
            "task_authorized",
        ],
    },
    {
        "code": "first_config_wizard",
        "label": "首次配置向导",
        "weight": 3,
        "description": "首次启动时引导店铺、模型、本机/LAN、插件配置。",
        "files": [
            "collector/workbench-command-center/src/components/settings/StoreManagerDialog.tsx",
            "collector/workbench-command-center/src/hooks/useWorkbenchSettings.ts",
            "scripts/configure_lan_access.py",
        ],
        "tests": [
            "tests/test_workbench_operators.py",
            "tests/test_lan_collaboration.py",
        ],
        "keywords": [
            "workbench_settings",
            "lan-access.json",
            "StoreManager",
        ],
        "known_gap": "还不是完整首次配置向导，只是已有设置和局域网能力。",
    },
]


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def load_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {} if default is None else default


def read_text(root: Path, relative: str) -> str:
    path = root / relative
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def file_exists(root: Path, relative: str) -> bool:
    return (root / relative).is_file()


def run_self_check(root: Path) -> Dict[str, Any]:
    python = root / ".venv/bin/python"
    executable = str(python) if python.is_file() else sys.executable
    completed = subprocess.run(
        [executable, "scripts/project_self_check.py"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    report = load_json(root / "logs/project-self-check.json", {})
    report["_runner"] = {
        "returncode": completed.returncode,
        "stdout_tail": completed.stdout[-1000:],
        "stderr_tail": completed.stderr[-1000:],
    }
    return report


def existing_self_check(root: Path) -> Dict[str, Any]:
    return load_json(root / "logs/project-self-check.json", {})


def area_status(area: Dict[str, Any], root: Path) -> Dict[str, Any]:
    files = area["files"]
    tests = area["tests"]
    keywords = area.get("keywords") or []
    existing_files = [path for path in files if file_exists(root, path)]
    existing_tests = [path for path in tests if file_exists(root, path)]
    corpus = "\n".join(read_text(root, path) for path in [*existing_files, *existing_tests])
    matched_keywords = [keyword for keyword in keywords if keyword in corpus]
    file_ratio = len(existing_files) / max(len(files), 1)
    test_ratio = len(existing_tests) / max(len(tests), 1)
    keyword_ratio = len(matched_keywords) / max(len(keywords), 1) if keywords else 1.0
    score_ratio = (file_ratio * 0.35) + (test_ratio * 0.45) + (keyword_ratio * 0.20)
    if area.get("known_gap"):
        score_ratio = min(score_ratio, 0.72)
    if score_ratio >= 0.90:
        status = "covered"
        status_zh = "已覆盖"
    elif score_ratio >= 0.55:
        status = "partial"
        status_zh = "部分覆盖"
    else:
        status = "missing"
        status_zh = "缺口明显"
    missing_files = [path for path in files if path not in existing_files]
    missing_tests = [path for path in tests if path not in existing_tests]
    missing_keywords = [keyword for keyword in keywords if keyword not in matched_keywords]
    return {
        "code": area["code"],
        "label": area["label"],
        "status": status,
        "status_zh": status_zh,
        "score": round(score_ratio * area["weight"], 2),
        "weight": area["weight"],
        "coverage_percent": round(score_ratio * 100),
        "description": area["description"],
        "evidence_files": existing_files,
        "evidence_tests": existing_tests,
        "matched_keywords": matched_keywords,
        "missing_files": missing_files,
        "missing_tests": missing_tests,
        "missing_keywords": missing_keywords,
        "known_gap": area.get("known_gap") or "",
    }


def product_runtime_summary(root: Path) -> Dict[str, Any]:
    products_dir = root / "products"
    counts: Dict[str, int] = {}
    attention: List[str] = []
    current = 0
    if not products_dir.is_dir():
        return {"current_products": 0, "status_counts": {}, "attention_products": []}
    for product_dir in sorted(products_dir.glob("P[0-9][0-9][0-9][0-9][0-9][0-9]")):
        status = load_json(product_dir / "status.json", {})
        source = load_json(product_dir / "input/source.json", {})
        if status.get("archived_at") or str(status.get("status") or "").upper() == "ARCHIVED":
            continue
        if not (
            source.get("source_kind") == "workbench_collection"
            and source.get("product_id") == product_dir.name
            and source.get("collection_id")
        ):
            continue
        current += 1
        state = str(status.get("status") or "unknown").upper()
        counts[state] = counts.get(state, 0) + 1
        if state in {"NEEDS_ATTENTION", "FAILED", "PARTIAL_FAILED", "STOPPED"}:
            attention.append(product_dir.name)
    return {
        "current_products": current,
        "status_counts": dict(sorted(counts.items())),
        "attention_products": attention,
    }


def summarize_self_check(report: Dict[str, Any]) -> Dict[str, Any]:
    checks = report.get("checks") or []
    failures = [
        item for item in checks
        if not item.get("ok") and item.get("severity") == "error"
    ]
    warnings = [
        item for item in checks
        if not item.get("ok") and item.get("severity") == "warning"
    ]
    return {
        "decision": report.get("decision") or "UNKNOWN",
        "checked_at": report.get("checked_at") or "unknown",
        "failures": len(failures),
        "warnings": len(warnings),
        "failure_codes": [item.get("code") for item in failures],
        "warning_codes": [item.get("code") for item in warnings],
        "ozon_write_api_calls": int(report.get("ozon_write_api_calls") or 0),
        "inventory_api_calls": int(report.get("inventory_api_calls") or 0),
        "network_calls": int(report.get("network_calls") or 0),
    }


def build_audit(root: Path, *, run_check: bool = True) -> Dict[str, Any]:
    self_check = run_self_check(root) if run_check else existing_self_check(root)
    areas = [area_status(area, root) for area in RISK_AREAS]
    earned = sum(item["score"] for item in areas)
    possible = sum(item["weight"] for item in areas)
    self_summary = summarize_self_check(self_check)
    penalty = 0
    if self_summary["decision"] == "FAIL":
        penalty += 12
    if self_summary["ozon_write_api_calls"] or self_summary["inventory_api_calls"]:
        penalty += 30
    readiness_score = max(0, min(100, round((earned / max(possible, 1)) * 100 - penalty)))
    partial_or_missing = [item for item in areas if item["status"] != "covered"]
    high_priority = [
        item for item in partial_or_missing
        if item["code"] in {
            "product_fact_lock",
            "sku_variant_merge",
            "ozon_category_attributes",
            "image_fact_hallucination",
            "image_qc_designer_repair",
            "status_sync",
        }
    ]
    decision = "READY" if readiness_score >= 90 and not high_priority and self_summary["decision"] == "PASS" else (
        "NEEDS_TARGETED_TESTING" if readiness_score >= 75 and self_summary["decision"] == "PASS" else "NOT_READY"
    )
    report = {
        "schema_version": "1.0.0",
        "checked_at": now_iso(),
        "root": str(root),
        "decision": decision,
        "readiness_score": readiness_score,
        "network_calls": 0,
        "ozon_write_api_calls": self_summary["ozon_write_api_calls"],
        "inventory_api_calls": self_summary["inventory_api_calls"],
        "self_check": self_summary,
        "runtime": product_runtime_summary(root),
        "coverage": areas,
        "top_gaps": [
            {
                "code": item["code"],
                "label": item["label"],
                "status": item["status_zh"],
                "coverage_percent": item["coverage_percent"],
                "known_gap": item["known_gap"],
                "missing_tests": item["missing_tests"],
                "missing_keywords": item["missing_keywords"],
            }
            for item in sorted(partial_or_missing, key=lambda value: (value["status"] != "missing", -value["weight"]))[:8]
        ],
        "next_minimum_actions": next_actions(partial_or_missing, self_summary),
    }
    return report


def next_actions(gaps: List[Dict[str, Any]], self_summary: Dict[str, Any]) -> List[str]:
    actions: List[str] = []
    if self_summary["decision"] == "FAIL":
        actions.append("先修复 project_self_check 的 error 项；否则完整度分数不能作为上架依据。")
    priority_order = [
        "product_fact_lock",
        "sku_variant_merge",
        "ozon_category_attributes",
        "image_fact_hallucination",
        "image_qc_designer_repair",
        "status_sync",
        "first_config_wizard",
    ]
    by_code = {item["code"]: item for item in gaps}
    for code in priority_order:
        item = by_code.get(code)
        if not item:
            continue
        if item["known_gap"]:
            actions.append(f"{item['label']}：{item['known_gap']}")
        elif item["missing_tests"]:
            actions.append(f"{item['label']}：补齐测试证据 {', '.join(item['missing_tests'][:2])}。")
        elif item["missing_keywords"]:
            actions.append(f"{item['label']}：核对关键规则是否仍在入口生效：{', '.join(item['missing_keywords'][:3])}。")
        if len(actions) >= 5:
            break
    if not actions:
        actions.append("当前核心风险都有代码和测试证据；下一步用只读真实产品回归最近失败样本。")
    return actions


def write_report(report: Dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temporary = Path(handle.name)
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description="只读生成 AI Factory 生产完整度报告")
    parser.add_argument("--no-self-check", action="store_true", help="不重新运行 project_self_check，只读取现有报告")
    parser.add_argument("--output", default=str(REPORT_PATH), help="报告输出路径")
    args = parser.parse_args()
    report = build_audit(ROOT, run_check=not args.no_self_check)
    output = Path(args.output)
    write_report(report, output)
    print(json.dumps({
        "decision": report["decision"],
        "readiness_score": report["readiness_score"],
        "report": str(output),
        "top_gaps": report["top_gaps"][:5],
        "ozon_write_api_calls": report["ozon_write_api_calls"],
        "inventory_api_calls": report["inventory_api_calls"],
    }, ensure_ascii=False))
    return 0 if report["decision"] != "NOT_READY" else 1


if __name__ == "__main__":
    raise SystemExit(main())
