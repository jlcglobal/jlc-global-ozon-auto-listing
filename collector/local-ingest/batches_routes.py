"""Routes/helpers extracted from app.py (2026-08-14).

Executed in app.py's globals (bottom of app.py); no imports needed here.
"""

@app.get("/api/workbench/batches")
def workbench_batches() -> Dict[str, Any]:
    items = []
    active_pid = running_batch_pid()
    current = load_optional_json(CURRENT_BATCH_PATH)
    queued_positions = {
        str(item.get("batch_id")): index
        for index, item in enumerate(load_workbench_run_queue().get("items") or [], start=1)
    }
    for path in sorted((ROOT / "batches").glob("B-*/batch.json"), reverse=True):
        batch = load_optional_json(path)
        if str(batch.get("local_lifecycle_status") or "").upper() == "ARCHIVED":
            continue
        if batch and batch_is_owned(str(batch.get("batch_id") or path.parent.name)):
            batch = overlay_live_batch_status(batch, PRODUCTS_DIR)
            result = load_optional_json(path.with_name("batch-result.json"))
            batch["result"] = result
            batch["queue_position"] = queued_positions.get(str(batch.get("batch_id")), 0)
            batch["execution_plan"] = bounded_parallel_plan()
            ready_product_ids = manual_upload_product_ids(batch)
            batch["ready_product_ids"] = ready_product_ids
            if ready_product_ids:
                batch["status"] = "AWAITING_MANUAL_UPLOAD"
                batch["display_status"] = "待确认上传"
            else:
                batch["display_status"] = (
                    "排队中"
                    if batch["queue_position"]
                    else
                    "等待Ozon结果"
                    if batch.get("status") == "INCOMPLETE"
                    and int(batch.get("pending_remote_count") or 0) > 0
                    and int(batch.get("incomplete_count") or 0) == 0
                    else
                    "未完成"
                    if batch.get("status") == "INCOMPLETE"
                    else
                    "已中断"
                    if batch.get("status") == "RUNNING" and not (
                        active_pid and current.get("batch_id") == batch.get("batch_id")
                    )
                    else batch.get("status")
                )
            items.append(batch)
    return {
        "items": items[:100], "running_pid": active_pid,
        "queued_count": len(queued_positions), "execution_plan": bounded_parallel_plan(),
    }


@app.post("/api/workbench/batches/coalesce")
def coalesce_workbench_batches() -> Dict[str, Any]:
    require_owner_role()
    with BATCH_QUEUE_LOCK:
        result = coalesce_compatible_queued_batches()
    BATCH_DISPATCHER_WAKE.set()
    return {**result, "execution_plan": bounded_parallel_plan(), "write_api_calls": 0, "inventory_api_calls": 0}


@app.post("/api/workbench/batches/create")
async def create_workbench_batch(request: Request) -> Dict[str, Any]:
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="批次内容格式错误")
    selected_stores = validate_target_stores(payload.get("store_ids") or [])
    product_ids = payload.get("product_ids")
    if product_ids is not None and not isinstance(product_ids, list):
        raise HTTPException(status_code=422, detail="商品列表格式错误")
    overrides = payload.get("product_store_overrides") or {}
    with BATCH_QUEUE_LOCK:
        reserved = reserved_product_batches()
        requested_ids = product_ids or [path.name for path in collected_products(ROOT) if product_is_owned(path)]
        requested_ids = [
            str(value) for value in requested_ids
            if (PRODUCTS_DIR / str(value)).is_dir() and product_is_owned(PRODUCTS_DIR / str(value))
        ]
        available_ids = [str(value) for value in requested_ids if str(value) not in reserved]
        if not available_ids:
            return {
                "status": "already_queued" if requested_ids else "empty", "product_count": 0,
                "existing_batch_ids": sorted({reserved[str(value)] for value in requested_ids if str(value) in reserved}),
            }
        for product_id in available_ids:
            try:
                validate_formal_product_input(PRODUCTS_DIR / product_id)
            except ProductionInputError as exc:
                raise HTTPException(
                    status_code=422,
                    detail=f"{product_id} 不是当前工作台本次采集的正式输入，批次未创建：{exc}",
                ) from exc
        upload_ready_ids = [product_id for product_id in available_ids if waiting_for_user_upload(product_id)]
        if upload_ready_ids and len(upload_ready_ids) != len(available_ids):
            raise HTTPException(
                status_code=409,
                detail=(
                    "等待上传的商品不能和还在生成阶段的商品放在同一个批次。"
                    "请只选择待上传商品后点击上传，或先运行未生成商品。"
                ),
            )
        auto_upload = True
        launch_reason = "automatic_submission" if upload_ready_ids else "workbench_batch"
        batch = create_batch(
            ROOT, available_ids, target_store_ids=selected_stores,
            auto_upload=auto_upload, product_store_overrides=overrides,
        )
        save_batch_owner(batch["batch_id"])
        for entry in batch["products"]:
            product_dir = workbench_product_dir(entry["product_id"])
            stores_for_product = entry.get("target_store_ids") or selected_stores
            select_stores(product_dir, stores_for_product, connected_store_ids())
            materialize_active_experience(ROOT, product_dir, now_iso())
            if auto_upload:
                final_snapshot(product_dir, stores_for_product, batch["batch_id"])
        launched = launch_or_enqueue_batch(batch, launch_reason)
    return {
        **launched, "product_count": batch["product_count"], "target_store_ids": selected_stores,
        "auto_upload": batch["auto_upload"], "write_api_calls": 0, "inventory_api_calls": 0,
    }


# Ozon-reference routes moved to reference_routes.py (exec'd at the bottom).

@app.get("/api/workbench/batches/{batch_id}/confirmation")
def get_workbench_batch_confirmation(batch_id: str) -> Dict[str, Any]:
    require_owned_batch(batch_id)
    batch = load_optional_json(batch_path(ROOT, batch_id))
    if not batch:
        raise HTTPException(status_code=404, detail="批次不存在")
    if batch.get("auto_upload"):
        raise HTTPException(status_code=409, detail="自动模式批次不需要人工确认")
    if batch.get("status") not in {"AWAITING_CONFIRMATION", "QUEUED"}:
        raise HTTPException(status_code=409, detail="当前批次已离开人工确认阶段")
    return build_batch_confirmation(batch)


def _positive_confirmation_number(value: Any, field_name: str) -> float:
    try:
        number_value = float(value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=f"{field_name}必须是数字") from exc
    if number_value <= 0:
        raise HTTPException(status_code=422, detail=f"{field_name}必须大于0")
    return round(number_value, 2)


def _positive_confirmation_integer_grams(value: Any, field_name: str) -> int:
    try:
        number_value = float(value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=f"{field_name}必须是数字") from exc
    if number_value <= 0:
        raise HTTPException(status_code=422, detail=f"{field_name}必须大于0")
    return int(math.ceil(number_value))


def _confirmed_dimensions(value: Any, field_name: str) -> Dict[str, float]:
    if not isinstance(value, dict):
        raise HTTPException(status_code=422, detail=f"{field_name}格式错误")
    return {
        key: _positive_confirmation_number(value.get(key), f"{field_name}{label}")
        for key, label in (("length", "长"), ("width", "宽"), ("height", "高"))
    }


@app.post("/api/workbench/batches/{batch_id}/confirm")
async def confirm_workbench_batch(batch_id: str, request: Request) -> Dict[str, Any]:
    require_owned_batch(batch_id)
    payload = await request.json()
    confirmations = payload.get("products") if isinstance(payload, dict) else None
    if not isinstance(confirmations, list):
        raise HTTPException(status_code=422, detail="批量确认内容格式错误")
    by_product = {str(item.get("product_id")): item for item in confirmations if isinstance(item, dict)}
    with BATCH_QUEUE_LOCK:
        batch_file = batch_path(ROOT, batch_id)
        batch = load_optional_json(batch_file)
        if not batch:
            raise HTTPException(status_code=404, detail="批次不存在")
        if batch.get("status") != "AWAITING_CONFIRMATION":
            raise HTTPException(status_code=409, detail="批次已确认或已经启动，禁止重复确认")
        if batch.get("auto_upload"):
            raise HTTPException(status_code=409, detail="自动模式批次不需要人工确认")
        expected = batch_product_ids(batch)
        if set(by_product) != set(expected):
            raise HTTPException(status_code=422, detail="必须一次确认本批次的全部商品")
        normalized: Dict[str, Dict[str, Any]] = {}
        for product_id in expected:
            item = by_product[product_id]
            fields = item.get("fields") or {}
            product_dimensions = _confirmed_dimensions(fields.get("product_dimensions"), "商品尺寸")
            package_dimensions = _confirmed_dimensions(fields.get("package_dimensions"), "包装尺寸")
            product_weight = _positive_confirmation_integer_grams(fields.get("product_weight_g"), "商品净重")
            package_weight = _positive_confirmation_integer_grams(fields.get("package_weight_g"), "包装重量")
            if package_weight <= product_weight:
                raise HTTPException(status_code=422, detail=f"{product_id}：包装重量必须大于商品净重")
            if any(package_dimensions[key] <= product_dimensions[key] for key in ("length", "width", "height")):
                raise HTTPException(status_code=422, detail=f"{product_id}：包装长宽高必须分别大于商品长宽高")
            material = str(fields.get("material") or "unknown").strip() or "unknown"
            sku_prices = item.get("sku_prices") or {}
            source = load_optional_json(workbench_product_dir(product_id) / "input/source.json")
            expected_skus = {str(sku.get("sku_id")) for sku in source.get("skus") or []}
            if set(str(key) for key in sku_prices) != expected_skus:
                raise HTTPException(status_code=422, detail=f"{product_id}：必须确认全部SKU的人民币进价")
            normalized_prices = {
                str(sku_id): _positive_confirmation_number(value, f"{product_id} SKU {sku_id}进价")
                for sku_id, value in sku_prices.items()
            }
            normalized[product_id] = {
                "schema_version": "1.0.0",
                "product_id": product_id,
                "batch_id": batch_id,
                "confirmed_at": now_iso(),
                "confirmed_by": "workbench_manual_batch_confirmation",
                "fields": {
                    "product_dimensions": {**product_dimensions, "unit": "cm"},
                    "product_weight": {"value_g": product_weight},
                    "package_dimensions": {**package_dimensions, "unit": "cm"},
                    "package_weight": {"value_g": package_weight},
                    "material": material,
                },
                "sku_purchase_prices_cny": normalized_prices,
                "provenance": "estimated_human_approved",
                "inventory_submission_enabled": False,
            }
        for entry in batch["products"]:
            product_id = str(entry["product_id"])
            product_dir = workbench_product_dir(product_id)
            atomic_write_json(product_dir / "input/manual-confirmation.json", normalized[product_id])
            stores_for_product = entry.get("target_store_ids") or batch.get("target_store_ids") or []
            final_snapshot(product_dir, stores_for_product, batch_id)
            append_log(product_dir, "manual_batch_confirmation_saved", {
                "batch_id": batch_id,
                "confirmed_fields": ["product_dimensions", "product_weight", "package_dimensions", "package_weight", "material", "sku_purchase_prices_cny"],
            })
            entry.update({"status": "QUEUED", "current_step": "queue"})
        batch.update({"status": "QUEUED", "confirmed_at": now_iso(), "confirmation_count": len(normalized)})
        atomic_write_json(batch_file, batch)
        launched = launch_or_enqueue_batch(batch, "manual_confirmation")
    return {
        **launched,
        "product_count": batch.get("product_count", 0),
        "confirmed_product_count": len(normalized),
        "write_api_calls": 0,
        "inventory_api_calls": 0,
    }


@app.post("/api/workbench/batches/control")
async def control_batch(request: Request) -> Dict[str, Any]:
    payload = await request.json()
    action = str(payload.get("action") or "") if isinstance(payload, dict) else ""
    pid = running_batch_pid()
    if action == "cancel_confirmation":
        batch_id = str(payload.get("batch_id") or "")
        require_owned_batch(batch_id)
        batch_file = batch_path(ROOT, batch_id) if batch_id else None
        batch = load_optional_json(batch_file) if batch_file else {}
        if not batch:
            raise HTTPException(status_code=404, detail="批次不存在")
        if batch.get("status") != "AWAITING_CONFIRMATION":
            raise HTTPException(status_code=409, detail="只有尚未确认、尚未启动的批次可以直接取消")
        batch.update({"status": "CANCELLED", "cancelled_at": now_iso(), "cancel_reason": "user_cancelled_before_generation"})
        for entry in batch.get("products") or []:
            entry.update({"status": "CANCELLED", "current_step": "cancelled_before_generation"})
            product_dir = workbench_product_dir(str(entry.get("product_id")))
            append_log(product_dir, "manual_confirmation_batch_cancelled", {"batch_id": batch_id})
        atomic_write_json(batch_file, batch)
        return {
            "status": "cancelled", "batch_id": batch_id,
            "message": "本次任务已取消，商品仍保留在采集箱，可以重新运行",
            "write_api_calls": 0, "inventory_api_calls": 0,
        }
    if action == "retry_failed":
        if pid is not None:
            raise HTTPException(status_code=409, detail="当前批次仍在运行")
        failed = [
            path.name for path in retryable_products(ROOT)
            if product_is_owned(path) and load_optional_json(path / "status.json").get("status") in ATTENTION_STATES
        ]
        if not failed:
            return {"status": "empty", "message": "没有可重试的失败商品"}
        selected_stores = validate_target_stores(payload.get("store_ids") or [])
        batch = create_batch(ROOT, failed, target_store_ids=selected_stores, auto_upload=True)
        save_batch_owner(batch["batch_id"])
        for product_id in failed:
            select_stores(workbench_product_dir(product_id), selected_stores, connected_store_ids())
        launched = launch_or_enqueue_batch(batch, "retry_failed")
        return {**launched, "batch_id": batch["batch_id"], "product_count": len(failed)}
    if pid is None:
        raise HTTPException(status_code=409, detail="当前没有运行中的批次")
    if action == "stop":
        if str(payload.get("source") or "") != "manual_toolbar_v2":
            raise HTTPException(
                status_code=409,
                detail="停止请求来源过旧或不明确，已拒绝；请刷新页面后使用当前工作台的手动停止按钮。",
            )
        current = load_optional_json(CURRENT_BATCH_PATH)
        batch_id = str(current.get("batch_id") or "")
        require_owned_batch(batch_id)
        operator = current_operator()
        operator_id = str(operator.get("id") or operator.get("operator_id") or DEFAULT_OPERATOR_ID)
        device_id = str(operator.get("client_device_id") or operator.get("device_id") or "unknown")
        device_name = str(operator.get("device_name") or operator.get("display_name") or "unknown")
        atomic_write_json(SAFE_STOP_REQUEST_PATH, {
            "batch_id": batch_id,
            "pid": pid,
            "requested_at": now_iso(),
            "mode": "manual_operator_stop",
            "source": "manual_toolbar_v2",
            "requested_by": operator_id,
            "device_id": device_id,
            "device_name": device_name,
            "reason": "用户在当前工作台点击手动停止",
        })
        return {
            "status": "stopping_safely",
            "pid": pid,
            "batch_id": batch_id,
            "message": "已收到手动停止请求；系统会在最近安全断点停止并保留进度。",
        }
    signals = {"pause": signal.SIGSTOP, "continue": signal.SIGCONT}
    if action not in signals:
        raise HTTPException(status_code=422, detail="不支持的批次操作")
    current = load_optional_json(CURRENT_BATCH_PATH)
    require_owned_batch(str(current.get("batch_id") or ""))
    os.kill(pid, signals[action])
    return {"status": action, "pid": pid}
