#!/usr/bin/env python3
"""Bridge a new-product ecommerce design to visible Seerfar keyword results.

The browser extension owns the logged-in Seerfar session.  This helper only
queues a product-scoped research request and waits briefly for the extension
to save its visible result.  A missing browser result is advisory: the product
pipeline continues with its existing source-grounded copy rules.
"""
from __future__ import annotations

import argparse
import json
import re
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List


ROOT = Path(__file__).resolve().parents[1]
JOBS_PATH = ROOT / "runtime" / "seerfar-keyword-jobs.json"
CYRILLIC_RE = re.compile(r"[А-Яа-яЁё]")
CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_json(path: Path, default: Dict[str, Any]) -> Dict[str, Any]:
    if not path.is_file():
        return dict(default)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return dict(default)
    return value if isinstance(value, dict) else dict(default)


def write_json_atomic(path: Path, value: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temporary = Path(handle.name)
    temporary.replace(path)


def _first_russian_phrase(value: Any) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if not CYRILLIC_RE.search(text):
        return ""
    cjk = CJK_RE.search(text)
    if cjk:
        text = text[:cjk.start()].rstrip(" ,，;；/-")
    return text[:120].strip()


def seed_keyword(product_dir: Path) -> str:
    analysis = load_json(product_dir / "output" / "product-analysis.json", {})
    positioning = load_json(product_dir / "output" / "product-positioning.json", {})
    category = load_json(product_dir / "output" / "ozon-category.json", {})
    candidates: List[Any] = [
        analysis.get("product_type"),
        positioning.get("market_positioning"),
        category.get("category_name"),
    ]
    for candidate in candidates:
        phrase = _first_russian_phrase(candidate)
        if phrase:
            return phrase
    return ""


def research_path(product_dir: Path) -> Path:
    return product_dir / "output" / "seerfar-keyword-research.json"


def queue_research(product_dir: Path, *, jobs_path: Path = JOBS_PATH) -> Dict[str, Any]:
    product_id = product_dir.name
    seed = seed_keyword(product_dir)
    output_path = research_path(product_dir)
    if not seed:
        result = {
            "schema_version": "1.0.0",
            "product_id": product_id,
            "status": "unavailable",
            "reason": "当前商品缺少可用于 Seerfar 挖掘的俄文商品类型或类目词",
            "rows": [],
            "updated_at": now_iso(),
        }
        write_json_atomic(output_path, result)
        return result

    existing = load_json(output_path, {})
    if (
        existing.get("status") == "completed"
        and existing.get("seed_keyword") == seed
        and isinstance(existing.get("rows"), list)
        and existing.get("rows")
    ):
        return existing

    queue = load_json(jobs_path, {"schema_version": "1.0.0", "jobs": []})
    jobs = [item for item in queue.get("jobs") or [] if isinstance(item, dict)]
    active = next(
        (
            item for item in jobs
            if item.get("kind") == "pipeline_keyword_research"
            and item.get("product_id") == product_id
            and item.get("status") in {"queued", "running"}
        ),
        None,
    )
    if active is None:
        active = {
            "job_id": f"seerfar-pipeline-{int(time.time() * 1000)}-{threading.get_ident()}",
            "kind": "pipeline_keyword_research",
            "product_id": product_id,
            "seed_keyword": seed,
            "mode": "keyword_miner",
            "import_path": "/api/workbench/market-intelligence/search-visibility/seerfar/pipeline/import",
            "status": "queued",
            "created_at": now_iso(),
        }
        jobs.append(active)
        queue["jobs"] = jobs
        queue["schema_version"] = "1.0.0"
        write_json_atomic(jobs_path, queue)

    result = {
        "schema_version": "1.0.0",
        "product_id": product_id,
        "status": "waiting_browser",
        "job_id": active.get("job_id"),
        "seed_keyword": seed,
        "mode": "keyword_miner",
        "rows": [],
        "source_notice": "等待已登录 Seerfar 页面读取可见关键词结果；不读取Cookie，不调用 Seerfar Open API。",
        "updated_at": now_iso(),
    }
    write_json_atomic(output_path, result)
    return result


def wait_for_research(product_dir: Path, *, timeout_seconds: int) -> Dict[str, Any]:
    deadline = time.monotonic() + max(0, timeout_seconds)
    output_path = research_path(product_dir)
    while True:
        result = load_json(output_path, {})
        if result.get("status") in {"completed", "unavailable", "failed", "login_required", "timed_out"}:
            return result
        if time.monotonic() >= deadline:
            result.update({
                "schema_version": "1.0.0",
                "product_id": product_dir.name,
                "status": "timed_out",
                "reason": "本轮未在等待时间内取得 Seerfar 可见结果，继续原有商品事实文案流程。",
                "updated_at": now_iso(),
            })
            result.setdefault("rows", [])
            write_json_atomic(output_path, result)
            return result
        time.sleep(1)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("product_dir", type=Path)
    parser.add_argument("--wait-seconds", type=int, default=85)
    args = parser.parse_args()
    queue_research(args.product_dir)
    result = wait_for_research(args.product_dir, timeout_seconds=args.wait_seconds)
    print(json.dumps({
        "product_id": args.product_dir.name,
        "status": result.get("status"),
        "seed_keyword": result.get("seed_keyword"),
        "row_count": len(result.get("rows") or []),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
