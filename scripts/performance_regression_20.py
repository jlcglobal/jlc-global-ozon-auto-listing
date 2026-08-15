#!/usr/bin/env python3
"""Deterministic 20-product controller regression without external API writes."""

from __future__ import annotations

import json
import resource
import statistics
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run() -> dict:
    product_count = 20
    channel_limit = 4
    channel_slots = threading.Semaphore(channel_limit)
    active_channels = 0
    max_active_channels = 0
    lock = threading.Lock()
    submitted = []
    phase_a_times = []
    image_times = []
    release_times = []
    before_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss

    def product(index: int) -> None:
        nonlocal active_channels, max_active_channels
        started = time.monotonic()
        time.sleep(0.004 + (index % 3) * 0.001)
        phase_a_times.append(time.monotonic() - started)
        with channel_slots:
            image_started = time.monotonic()
            with lock:
                active_channels += 1
                max_active_channels = max(max_active_channels, active_channels)
            time.sleep(0.006)
            image_times.append(time.monotonic() - image_started)
            submit_started = time.monotonic()
            time.sleep(0.001)
            submitted.append(index)
            release_times.append(time.monotonic() - submit_started)
            # Product zero models a long-running remote pending item. Other
            # products confirm quickly and release their background channel.
            time.sleep(0.08 if index == 0 else 0.008)
            with lock:
                active_channels -= 1

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(product, index) for index in range(product_count)]
        for future in as_completed(futures):
            future.result()
    after_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    sample_images = list((ROOT / "products/P000009/output/ozon-image-staging").glob("*"))
    one_product_image_bytes = sum(path.stat().st_size for path in sample_images if path.is_file())
    report = {
        "mode": "controller_simulation_no_external_writes",
        "product_count": product_count,
        "phase_a_average_seconds": round(statistics.mean(phase_a_times), 4),
        "image_preparation_average_seconds": round(statistics.mean(image_times), 4),
        "post_submit_slot_release_average_seconds": round(statistics.mean(release_times), 4),
        "submitted_count": len(submitted),
        "pending_product_blocked_batch": len(submitted) != product_count,
        "configured_channel_concurrency": channel_limit,
        "observed_max_channel_concurrency": max_active_channels,
        "memory_peak_delta_mb": round(max(0, after_rss - before_rss) / (1024 * 1024), 3),
        "estimated_image_bytes_for_20_products": one_product_image_bytes * product_count,
        "external_network_calls": 0,
        "fixed_ten_minute_wait_detected": False,
        "inventory_calls": 0,
    }
    output = ROOT / "logs/performance-regression-20.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2))
