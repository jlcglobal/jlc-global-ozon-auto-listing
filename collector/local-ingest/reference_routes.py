"""Ozon-reference-task code extracted from app.py (2026-08-14).

Executed in app.py's globals (bottom of app.py); no imports needed here.
"""

@app.get("/api/workbench/ozon-reference-tasks")
def workbench_ozon_reference_tasks() -> Dict[str, Any]:
    data = load_ozon_reference_tasks()
    items = [
        public_ozon_reference_task(item)
        for item in data.get("items") or []
        if isinstance(item, dict)
    ]
    items.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
    return {
        "items": items[:200],
        "total": len(items),
        "write_api_calls": 0,
        "inventory_api_calls": 0,
        "message": "Ozon参考任务只使用公开商品卡链接作为参考，不调用Ozon写入或库存接口。",
    }


@app.post("/api/workbench/ozon-reference-tasks")
async def create_workbench_ozon_reference_tasks(request: Request) -> Dict[str, Any]:
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="Ozon参考上架内容格式错误")
    reference_items = parse_ozon_reference_items(payload)
    if not reference_items:
        raise HTTPException(status_code=422, detail="请至少粘贴一个 Ozon 商品卡链接")
    if len(reference_items) > 100:
        raise HTTPException(status_code=422, detail="一次最多提交 100 个 Ozon 商品卡链接")
    require_complete_ozon_reference_manual_inputs(reference_items)
    selected_stores = validate_target_stores(payload.get("store_ids") or [])
    created_at = now_iso()
    operator_id = current_operator_id()
    with BATCH_QUEUE_LOCK:
        data = load_ozon_reference_tasks()
        existing_items = [item for item in data.get("items") or [] if isinstance(item, dict)]
        active_urls = {
            str(item.get("source_url"))
            for item in existing_items
            if str(item.get("status") or "") in OZON_REFERENCE_TASK_ACTIVE_STATES
        }
        created: List[Dict[str, Any]] = []
        duplicates: List[str] = []
        for reference_item in reference_items:
            url = str(reference_item["source_url"])
            if url in active_urls:
                duplicates.append(url)
                continue
            task = {
                "schema_version": "1.0.0",
                "task_id": ozon_reference_task_id(url, created_at),
                "source_kind": "ozon_reference_listing",
                "source_url": url,
                "status": "queued",
                "display_status": "待处理",
                "target_store_ids": selected_stores,
                "mode": "create_without_inventory",
                "inventory_submission_enabled": False,
                "manual_inputs": reference_item.get("manual_inputs") or {},
                "fitkun_images": reference_item.get("fitkun_images") or [],
                "created_at": created_at,
                "updated_at": created_at,
                "created_by": operator_id,
                "pipeline_status": "waiting_ozon_reference_adapter",
                "message": (
                    "已加入 Ozon 参考上架队列。后续自动处理会抓取公开商品卡图片和文字，"
                    "生成我方商品卡与实拍风图片；上传时仍不提交库存。"
                ),
                "write_api_calls": 0,
                "inventory_api_calls": 0,
            }
            existing_items.append(task)
            created.append(task)
            active_urls.add(url)
        data["items"] = existing_items
        save_ozon_reference_tasks(data)
        if created:
            ensure_ozon_reference_dispatcher()
            OZON_REFERENCE_DISPATCHER_WAKE.set()
    return {
        "status": "queued" if created else "already_queued",
        "created_count": len(created),
        "duplicate_count": len(duplicates),
        "target_store_ids": selected_stores,
        "items": [public_ozon_reference_task(item) for item in created],
        "duplicates": duplicates,
        "message": (
            f"已加入 {len(created)} 个 Ozon 参考上架任务。"
            if created else
            "这些 Ozon 链接已经在参考上架队列中。"
        ),
        "write_api_calls": 0,
        "inventory_api_calls": 0,
    }


@app.patch("/api/workbench/ozon-reference-tasks/{task_id}/manual-inputs")
async def update_workbench_ozon_reference_manual_inputs(task_id: str, request: Request) -> Dict[str, Any]:
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="Ozon参考任务参数格式错误")
    manual_inputs = parse_ozon_reference_manual_inputs(payload)
    with BATCH_QUEUE_LOCK:
        data = load_ozon_reference_tasks()
        items = [item for item in data.get("items") or [] if isinstance(item, dict)]
        match_index = next((index for index, item in enumerate(items) if str(item.get("task_id")) == str(task_id)), -1)
        if match_index < 0:
            raise HTTPException(status_code=404, detail="没有找到对应的 Ozon 参考任务")
        task = dict(items[match_index])
        if str(task.get("status") or "") in {"listing_draft_ready", "completed"} or str(task.get("created_product_id") or ""):
            raise HTTPException(status_code=409, detail="商品卡草稿已生成，请新建参考任务后再补参数")
    require_complete_ozon_reference_manual_inputs([{
        "source_url": task.get("source_url") or "unknown",
        "manual_inputs": manual_inputs,
    }])
    requested_store_ids = payload.get("store_ids") or []
    selected_stores = validate_target_stores(requested_store_ids) if requested_store_ids else list(task.get("target_store_ids") or [])
    task.update({
        "manual_inputs": manual_inputs,
        "target_store_ids": selected_stores,
        "updated_at": now_iso(),
        "message": "Ozon参考任务参数已补齐，正在准备生成商品卡。",
        "write_api_calls": 0,
        "inventory_api_calls": 0,
    })
    task_dir = ozon_reference_task_dir(str(task.get("task_id") or ""))
    capture_path = task_dir / "capture.json"
    if not capture_path.is_file():
        task.update({
            "status": "queued",
            "display_status": "待处理",
            "pipeline_status": "waiting_ozon_reference_adapter",
        })
        updated = task
    else:
        capture = load_optional_json(capture_path)
        if not capture:
            raise HTTPException(status_code=409, detail="Ozon参考采集文件为空，请重新采集参考页")
        updated = rebuild_ozon_reference_task_artifacts_from_capture(task, capture)
    with BATCH_QUEUE_LOCK:
        data = load_ozon_reference_tasks()
        items = [item for item in data.get("items") or [] if isinstance(item, dict)]
        for index, existing in enumerate(items):
            if str(existing.get("task_id")) == str(task_id):
                items[index] = updated
                break
        else:
            items.append(updated)
        data["items"] = items
        save_ozon_reference_tasks(data)
    ensure_ozon_reference_dispatcher()
    OZON_REFERENCE_DISPATCHER_WAKE.set()
    return {
        "status": "updated",
        "task": public_ozon_reference_task(updated),
        "message": updated.get("message") or "Ozon参考任务参数已保存。",
        "write_api_calls": 0,
        "inventory_api_calls": 0,
    }


@app.post("/api/workbench/ozon-reference-tasks/{task_id}/fitkun-images")
async def import_workbench_ozon_reference_fitkun_images(task_id: str, request: Request) -> Dict[str, Any]:
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="FITKUN图片导入内容格式错误")
    with BATCH_QUEUE_LOCK:
        data = load_ozon_reference_tasks()
        items = [item for item in data.get("items") or [] if isinstance(item, dict)]
        match_index = next((index for index, item in enumerate(items) if str(item.get("task_id")) == str(task_id)), -1)
        if match_index < 0:
            raise HTTPException(status_code=404, detail="没有找到对应的 Ozon 参考任务")
        task = dict(items[match_index])
        if str(task.get("status") or "") in {"listing_draft_ready", "completed"}:
            raise HTTPException(status_code=409, detail="商品卡草稿已生成，请新建参考任务后再导入图片")
        imported = parse_fitkun_reference_images(payload, str(task.get("source_url") or ""))
        if not imported:
            raise HTTPException(status_code=422, detail="没有可导入的 FITKUN 图片")
        current = [item for item in (task.get("fitkun_images") or []) if isinstance(item, dict)]
        merged: Dict[str, Dict[str, Any]] = {str(item.get("url") or index): item for index, item in enumerate(current)}
        for item in imported:
            merged[str(item.get("url") or len(merged))] = item
        task.update({
            "fitkun_images": list(merged.values())[:24],
            "updated_at": now_iso(),
            "message": f"已导入 FITKUN 图片 {len(imported)} 张；后续会优先使用这些图片生成参考资料。",
            "write_api_calls": 0,
            "inventory_api_calls": 0,
        })
        if str(task.get("status") or "") in {"failed", "captured"}:
            task.update({
                "status": "queued",
                "display_status": "待处理",
                "pipeline_status": "waiting_ozon_reference_adapter",
            })
        items[match_index] = task
        data["items"] = items
        save_ozon_reference_tasks(data)
        ensure_ozon_reference_dispatcher()
        OZON_REFERENCE_DISPATCHER_WAKE.set()
    return {
        "status": "imported",
        "task": public_ozon_reference_task(task),
        "imported_count": len(imported),
        "total_fitkun_image_count": len(task.get("fitkun_images") or []),
        "message": "FITKUN图片已导入本地参考任务，不调用Ozon或库存接口。",
        "write_api_calls": 0,
        "inventory_api_calls": 0,
    }


@app.post("/api/workbench/ozon-reference-tasks/process")
def process_workbench_ozon_reference_tasks() -> Dict[str, Any]:
    ensure_ozon_reference_dispatcher()
    OZON_REFERENCE_DISPATCHER_WAKE.set()
    return {
        "status": "queued",
        "processed_count": 0,
        "failed_count": 0,
        "items": [],
        "write_api_calls": 0,
        "inventory_api_calls": 0,
        "message": "Ozon参考队列已继续，后台正在处理。",
    }
