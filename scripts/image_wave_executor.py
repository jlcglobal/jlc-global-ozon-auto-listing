#!/usr/bin/env python3
"""Execute image slots in real bounded parallel waves.

The scheduler decides which slots are pending.  This module is deliberately
model-agnostic: the caller supplies one isolated runner per slot.  A wave does
not advance until every slot in that wave reaches a terminal result.  Passed
slots are never submitted again; only failed slots receive the single allowed
retry.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable, Dict, List

from scripts.image_slot_scheduler import pending_slots


Slot = Dict[str, Any]
SlotResult = Dict[str, Any]
SlotRunner = Callable[[Slot, int], SlotResult]
AttemptHook = Callable[[Slot, int], None]
WaveHook = Callable[[int, List[Slot], List[SlotResult]], None]
BeforeWaveHook = Callable[[int, List[Slot], int], None]


def _run_wave(slots: List[Slot], attempt: int, runner: SlotRunner) -> List[SlotResult]:
    """Start every slot in one wave before waiting for its peers."""
    if not slots:
        return []
    results: Dict[str, SlotResult] = {}
    with ThreadPoolExecutor(max_workers=len(slots), thread_name_prefix="image-slot") as executor:
        futures = {executor.submit(runner, slot, attempt): slot for slot in slots}
        for future in as_completed(futures):
            slot = futures[future]
            slot_name = str(slot.get("slot") or "unknown")
            try:
                result = dict(future.result())
            except Exception as exc:  # The parent records one slot failure, not a whole-wave crash.
                if type(exc).__name__ in {"BatchSafeStopRequested", "ProductDeletionRequested"}:
                    raise
                result = {
                    "slot": slot_name,
                    "status": "failed",
                    "attempt": attempt,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            result.setdefault("slot", slot_name)
            result.setdefault("attempt", attempt)
            result["status"] = str(result.get("status") or "failed").strip().lower()
            results[slot_name] = result
    # Return deterministic plan order even though futures finish out of order.
    return [results[str(slot.get("slot") or "unknown")] for slot in slots]


def execute_image_slot_waves(
    product_dir: Any,
    concurrency: int,
    runner: SlotRunner,
    *,
    max_attempts: int = 2,
    before_attempt: AttemptHook | None = None,
    before_wave: BeforeWaveHook | None = None,
    after_wave: WaveHook | None = None,
) -> Dict[str, Any]:
    """Run main-image waves first, then detail waves, with real concurrency.

    ``max_attempts=2`` means one initial attempt plus one targeted retry.  A
    service outage is not consumed as an image retry and is returned to the
    caller so the product can wait for connected Codex without discarding
    successful peers.
    """
    concurrency = max(1, min(int(concurrency), 3))
    max_attempts = max(1, int(max_attempts))
    schedule = pending_slots(product_dir, concurrency)
    all_results: List[SlotResult] = []
    failed: List[SlotResult] = []
    service_unavailable: List[SlotResult] = []
    prelaunch_failure: List[SlotResult] = []

    for wave_index, planned_wave in enumerate(schedule.get("waves") or [], start=1):
        wave = list(planned_wave)
        if before_attempt:
            for slot in wave:
                before_attempt(slot, 1)
        if before_wave:
            before_wave(wave_index, wave, 1)
        first_results = _run_wave(wave, 1, runner)
        all_results.extend(first_results)
        if after_wave:
            after_wave(wave_index, wave, first_results)

        service_unavailable.extend(
            result for result in first_results if result["status"] == "service_unavailable"
        )
        prelaunch_failure.extend(
            result for result in first_results if result["status"] == "prelaunch_failure"
        )
        retry_slots = [
            slot for slot, result in zip(wave, first_results)
            if result["status"] not in {"passed", "service_unavailable", "prelaunch_failure"}
        ]
        if service_unavailable or prelaunch_failure:
            break

        for attempt in range(2, max_attempts + 1):
            if not retry_slots:
                break
            if before_attempt:
                for slot in retry_slots:
                    before_attempt(slot, attempt)
            if before_wave:
                before_wave(wave_index, retry_slots, attempt)
            retry_results = _run_wave(retry_slots, attempt, runner)
            all_results.extend(retry_results)
            if after_wave:
                after_wave(wave_index, retry_slots, retry_results)
            service_unavailable.extend(
                result for result in retry_results if result["status"] == "service_unavailable"
            )
            prelaunch_failure.extend(
                result for result in retry_results if result["status"] == "prelaunch_failure"
            )
            retry_slots = [
                slot for slot, result in zip(retry_slots, retry_results)
                if result["status"] not in {"passed", "service_unavailable", "prelaunch_failure"}
            ]
            if service_unavailable or prelaunch_failure:
                break
        if service_unavailable or prelaunch_failure:
            break
        if retry_slots:
            latest = {str(item.get("slot")): item for item in all_results}
            failed.extend(latest[str(slot.get("slot"))] for slot in retry_slots)
            # The next commercial wave must not start after this product has a
            # terminal failed slot. Other products in the batch remain free to continue.
            break

    passed_by_slot = {
        str(result.get("slot")): result
        for result in all_results
        if result.get("status") == "passed"
    }
    return {
        "product_id": schedule.get("product_id"),
        "concurrency": concurrency,
        "wave_count": len(schedule.get("waves") or []),
        "pending_slot_count": schedule.get("pending_slot_count", 0),
        "passed": list(passed_by_slot.values()),
        "failed": failed,
        "service_unavailable": service_unavailable,
        "prelaunch_failure": prelaunch_failure,
        "results": all_results,
    }
