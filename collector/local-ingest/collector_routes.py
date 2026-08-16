"""Routes/helpers extracted from app.py (2026-08-14).

Executed in app.py's globals (bottom of app.py); no imports needed here.
"""

@app.get("/api/inbox/products")
def list_inbox_products() -> Dict[str, Any]:
    active_product_ids = {path.name for path in retryable_products(ROOT)}
    products = [
        read_product_card(product_dir)
        for product_dir in sorted(PRODUCTS_DIR.glob("P[0-9]*"), reverse=True)
        if (product_dir / "status.json").is_file()
        and (product_dir / "input/source.json").is_file()
        and product_is_owned(product_dir)
        and not product_is_archived(product_dir)
        and "1688.com/offer/" in str(json.loads((product_dir / "input/source.json").read_text(encoding="utf-8")).get("source_url") or "")
    ]
    for item in products:
        item["in_current_inbox"] = item["product_id"] in active_product_ids
    pending = [item for item in products if item["in_current_inbox"]]
    return {
        "products": products,
        "product_count": len(products),
        "pending_product_count": len(pending),
        "pending_sku_count": sum(item["selected_sku_count"] for item in pending),
        "max_selected_skus_per_product": MAX_SELECTED_SKUS_PER_PRODUCT,
    }


def sync_remote_ozon_status_once(product_ids: Optional[List[str]] = None) -> Dict[str, Any]:
    """Run one explicit read-only Ozon task recovery pass for submitted products."""
    targets = [
        path for path in owned_product_dirs()
        if product_ids is None or path.name in set(product_ids)
    ]
    synced: List[str] = []
    store_checks = 0
    failures: List[Dict[str, str]] = []
    for product_dir in targets:
        try:
            result = refresh_pending_stores(ROOT, product_dir)
        except Exception as exc:
            failures.append({"product_id": product_dir.name, "error": str(exc)})
            continue
        checked = result.get("checked") or []
        if checked:
            synced.append(product_dir.name)
            store_checks += len(checked)
    return {
        "synced_product_ids": synced, "store_checks": store_checks,
        "write_api_calls": 0, "read_api_calls": store_checks, "inventory_api_calls": 0,
        "failures": failures,
        "message": "已执行一次只读 Ozon 任务结果查询；没有创建、更新或库存调用。",
    }


def ensure_image_status_monitor() -> None:
    # Image channels use a fixed local TTL.  The old monitor depended on
    # remote Ozon checks and is intentionally not started anymore.
    return None


@app.post("/api/inbox/refresh-ozon-status")
def refresh_ozon_status() -> Dict[str, Any]:
    return sync_remote_ozon_status_once([path.name for path in owned_product_dirs()])


@app.get("/api/inbox/products/{product_id}/thumbnail")
def product_thumbnail(product_id: str) -> FileResponse:
    product_dir = PRODUCTS_DIR / product_id
    if not re.fullmatch(r"P[0-9]{6}", product_id) or not product_dir.is_dir() or not product_is_owned(product_dir) or product_is_archived(product_dir):
        raise HTTPException(status_code=404, detail="Product not found")
    candidates = sorted((product_dir / "input/main-images").glob("*"))
    image = next((path for path in candidates if path.is_file()), None)
    if image is None:
        raise HTTPException(status_code=404, detail="Thumbnail not found")
    return FileResponse(image)


@app.post("/api/inbox/products/{product_id}/open-directory")
def open_product_directory(product_id: str) -> Dict[str, Any]:
    product_dir = PRODUCTS_DIR / product_id
    if not re.fullmatch(r"P[0-9]{6}", product_id) or not product_dir.is_dir() or not product_is_owned(product_dir) or product_is_archived(product_dir):
        raise HTTPException(status_code=404, detail="Product not found")
    subprocess.Popen(["/usr/bin/open", str(product_dir)], close_fds=True)
    return {"status": "opened", "product_id": product_id, "path": str(product_dir)}


@app.delete("/api/inbox/products/{product_id}")
async def delete_inbox_product(product_id: str, request: Request) -> Dict[str, Any]:
    payload = await request.json()
    if not isinstance(payload, dict) or payload.get("confirm_product_id") != product_id:
        raise HTTPException(status_code=422, detail="必须明确确认要彻底删除的商品ID")
    product_dir = PRODUCTS_DIR / product_id
    if not re.fullmatch(r"P[0-9]{6}", product_id) or not product_dir.is_dir() or not product_is_owned(product_dir):
        raise HTTPException(status_code=404, detail="Product not found")
    result = purge_local_product(ROOT, product_id)
    if result["status"] != "deleted":
        raise HTTPException(status_code=500, detail={"message": "商品未完全删除，可重新执行清理", **result})
    return result


def running_batch_pid() -> Optional[int]:
    if not BATCH_PID_PATH.is_file():
        if CURRENT_BATCH_PATH.is_file():
            current = load_optional_json(CURRENT_BATCH_PATH)
            CURRENT_BATCH_PATH.unlink(missing_ok=True)
            BATCH_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with BATCH_LOG_PATH.open("a", encoding="utf-8") as handle:
                handle.write(
                    f"[{now_iso()}] cleared orphan current-batch without pid; "
                    f"previous_batch={current.get('batch_id')}\n"
                )
        return None
    try:
        pid = int(BATCH_PID_PATH.read_text(encoding="utf-8").strip())
        if not _pid_is_alive(pid):
            raise OSError("batch process is not alive")
        return pid
    except (OSError, TypeError, ValueError):
        BATCH_PID_PATH.unlink(missing_ok=True)
        current = load_optional_json(CURRENT_BATCH_PATH)
        if current.get("pid"):
            CURRENT_BATCH_PATH.unlink(missing_ok=True)
            BATCH_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with BATCH_LOG_PATH.open("a", encoding="utf-8") as handle:
                handle.write(
                    f"[{now_iso()}] cleared stale batch runner pid/current-batch; "
                    f"previous_pid={current.get('pid')} previous_batch={current.get('batch_id')}\n"
                )
        return None


def active_product_worker(product_dir: Path) -> Optional[Dict[str, Any]]:
    """Return a live registered worker so UI state follows the real process."""
    worker_path = ROOT / "logs/product-workers" / f"{product_dir.name}.json"
    worker = load_optional_json(worker_path)
    try:
        pid = int(worker.get("pid"))
        if not _pid_is_alive(pid):
            raise OSError("product worker is not alive")
    except (OSError, TypeError, ValueError):
        if worker_path.is_file():
            worker_path.unlink(missing_ok=True)
    else:
        return worker

    slot_workers: List[Tuple[Path, Dict[str, Any]]] = []
    for slot_worker_path in sorted((ROOT / "logs/image-slot-workers").glob(f"{product_dir.name}--*.json")):
        slot_worker = load_optional_json(slot_worker_path)
        try:
            slot_pid = int(slot_worker.get("pid"))
            if not _pid_is_alive(slot_pid):
                raise OSError("image slot worker is not alive")
        except (OSError, TypeError, ValueError):
            slot_worker_path.unlink(missing_ok=True)
            continue
        slot_workers.append((slot_worker_path, slot_worker))
    if not slot_workers:
        return None
    latest_path, latest = max(
        slot_workers,
        key=lambda item: str(item[1].get("last_heartbeat_at") or item[1].get("started_at") or ""),
    )
    active_slots = [path.stem.split("--", 1)[1] for path, _ in slot_workers if "--" in path.stem]
    worker = dict(latest)
    worker["step"] = "image_generation"
    worker["slot"] = latest_path.stem.split("--", 1)[1] if "--" in latest_path.stem else "unknown"
    worker["active_slots"] = active_slots
    worker["active_slot_worker_count"] = len(slot_workers)
    return worker


def current_public_warnings(warnings: List[Any]) -> List[str]:
    """Hide recovered runtime noise from current workbench state."""
    resolved_fragments = (
        "ecommerce_design wrote an empty",
        "ecommerce_design wrote an incomplete",
        "ecommerce_design wrote an invalid",
        "RuntimeError: Step ecommerce_design timed out",
        "已归档无法继续使用的电商设计",
        "已把电商设计超时从商品错误改为AI设计等待状态",
        "已按正式步骤顺序修正队列入口",
        "等待用户",
        "store-publications.json",
    )
    result: List[str] = []
    for warning in warnings or []:
        text = str(warning)
        if any(fragment in text for fragment in resolved_fragments):
            continue
        result.append(text)
    return result


def effective_product_status(
    product_dir: Path,
    status: Dict[str, Any],
    *,
    task_snapshot: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Overlay stale persisted STOPPED/QUEUED values while a real worker is alive."""
    status = reconcile_completed_artifacts(product_dir, dict(status))
    # Resolve the owning project from the products directory.  This keeps
    # tests/secondary workspaces isolated from the main repository's cutover
    # marker while preserving the normal workbench behaviour.
    state_root = product_dir.parents[1] if len(product_dir.parents) > 1 else ROOT
    if task_snapshot is not None or cutover_active(state_root):
        snapshot = task_snapshot if task_snapshot is not None else product_snapshot(state_root, product_dir.name)
        canonical = snapshot.get("product")
        canonical_status = snapshot_effective_aggregate_status(snapshot)
        if canonical_status in {"CREATED", "PARTIAL", "PARTIAL_FAILED", "PENDING_REMOTE", "FAILED", "HANDED_OFF_TO_OZON"}:
            if canonical_status == "HANDED_OFF_TO_OZON":
                display_status = "PENDING_REMOTE"
            elif canonical_status in {"FAILED", "PARTIAL_FAILED"}:
                display_status = "NEEDS_ATTENTION"
            else:
                display_status = canonical_status
            status = dict(status)
            status["status"] = display_status
            status["progress"] = (
                100 if display_status in TERMINAL_PUBLICATION_STATES
                else 99 if display_status == "PENDING_REMOTE"
                else min(int(status.get("progress") or 0), 95) if canonical_status == "PARTIAL"
                else status.get("progress", 0)
            )
            status["current_step"] = "ozon_upload"
            status["active_step"] = None
            selected_stores = [
                item for item in snapshot.get("stores") or []
                if bool(item.get("selected"))
            ]
            selected_publication_ids = {
                item.get("id") for item in selected_stores if item.get("id") is not None
            }
            submitted_skus = [
                item for item in snapshot.get("sku_publications") or []
                if item.get("publication_id") in selected_publication_ids
            ]
            first_task = next(
                (
                    str(item.get("task_id")) for item in submitted_skus
                    if item.get("task_id") not in {None, "", "unknown", "UNKNOWN"}
                ),
                "unknown",
            )
            first_offer = next(
                (
                    str(item.get("offer_id")) for item in submitted_skus
                    if item.get("offer_id") not in {None, "", "unknown", "UNKNOWN"}
                ),
                "unknown",
            )
            first_product = next(
                (
                    str(item.get("ozon_product_id")) for item in submitted_skus
                    if item.get("ozon_product_id") not in {None, "", "unknown", "UNKNOWN"}
                ),
                "unknown",
            )
            status["api_write_count"] = sum(
                int(item.get("api_write_count") or 0) for item in selected_stores
            )
            ozon = dict(status.get("ozon") or {})
            ozon.update({
                "upload_status": (
                    "uploading" if canonical_status == "HANDED_OFF_TO_OZON"
                    else "uploaded" if canonical_status == "CREATED"
                    else "uploading" if canonical_status == "PENDING_REMOTE"
                    else "partial" if canonical_status == "PARTIAL"
                    else ozon.get("upload_status") or "not_started"
                ),
                "shop_name": str(selected_stores[0].get("store_id")) if selected_stores else "unknown",
                "task_id": first_task,
                "offer_id": first_offer,
                "product_id": first_product,
            })
            status["ozon"] = ozon
            if display_status == "PENDING_REMOTE":
                status.update({
                    "error_code": "unknown",
                    "error_message": "",
                    "failed_step": "unknown",
                    "next_action": "read_only_status_query",
                    "task_authorized": False,
                    "upload_priority_state": "waiting_remote",
                    "completed_at": "unknown",
                })
            if canonical_status in TERMINAL_PUBLICATION_STATES:
                status.update({
                    "error_code": "unknown",
                    "error_message": "",
                    "failed_step": "unknown",
                    "next_action": "complete",
                    "task_authorized": False,
                    "upload_priority_state": "completed",
                    "last_run_at": (canonical or {}).get("updated_at") or status.get("last_run_at"),
                })
                status["warnings"] = current_public_warnings(status.get("warnings") or [])
                completed_steps = list(status.get("completed_steps") or [])
                if "ozon_upload" not in completed_steps:
                    completed_steps.append("ozon_upload")
                status["completed_steps"] = completed_steps
                status["pending_steps"] = [
                    step for step in status.get("pending_steps") or []
                    if step != "ozon_upload"
                ]
                upload_steps = [
                    item for item in status.get("steps") or []
                    if item.get("name") == "ozon_upload"
                ]
                if not upload_steps or upload_steps[-1].get("status") != "completed":
                    status.setdefault("steps", []).append({
                        "name": "ozon_upload",
                        "status": "completed",
                        "started_at": (canonical or {}).get("updated_at") or now_iso(),
                        "finished_at": (canonical or {}).get("updated_at") or now_iso(),
                        "retry_count": int((status.get("retry_count_by_step") or {}).get("ozon_upload", 0)),
                        "retryable": False,
                        "error": None,
                    })
            elif canonical_status == "PARTIAL":
                status.update({
                    "error_code": "unknown",
                    "error_message": "部分已选店铺尚未提交；可继续上传未完成店铺",
                    "failed_step": "unknown",
                    "next_action": "ozon_upload",
                    "task_authorized": False,
                    "upload_priority_state": "partial",
                    "completed_at": "unknown",
                })
                status["completed_steps"] = [
                    step for step in status.get("completed_steps") or []
                    if step != "ozon_upload"
                ]
                status["pending_steps"] = list(dict.fromkeys([
                    *(status.get("pending_steps") or []), "ozon_upload",
                ]))
            elif canonical_status in {"FAILED", "PARTIAL_FAILED"}:
                failed_stores = [
                    item for item in selected_stores
                    if str(item.get("status") or "").upper() in {"FAILED", "QUERY_ERROR"}
                ]
                failed_reason = next(
                    (
                        str(item.get("last_error"))
                        for item in failed_stores
                        if item.get("last_error") not in {None, "", "unknown", "UNKNOWN"}
                    ),
                    status.get("error_message") or "一家或多家店铺上传失败；只允许重试失败店铺",
                )
                status.update({
                    "error_code": "STORE_UPLOAD_FAILED",
                    "error_message": failed_reason,
                    "failed_step": "ozon_upload",
                    "next_action": "retry_failed_store",
                    "task_authorized": True,
                    "upload_priority_state": "needs_attention",
                    "completed_at": "unknown",
                })
                ozon = dict(status.get("ozon") or {})
                ozon.update({
                    "upload_status": "failed",
                    "errors": ozon.get("errors") or [{"reason": failed_reason}],
                })
                status["ozon"] = ozon
                status["completed_steps"] = [
                    step for step in status.get("completed_steps") or []
                    if step != "ozon_upload"
                ]
                status["pending_steps"] = list(dict.fromkeys([
                    *(status.get("pending_steps") or []), "ozon_upload",
                ]))
    status_name = str(status.get("status") or "").upper()
    ozon = status.get("ozon") or {}
    if status_name == "HANDED_OFF_TO_OZON" and not known_remote_identity(ozon.get("product_id")):
        status = dict(status)
        ozon = dict(ozon)
        ozon["upload_status"] = "uploading"
        status.update({
            "status": "PENDING_REMOTE",
            "progress": 99,
            "next_action": "read_only_status_query",
            "task_authorized": False,
            "upload_priority_state": "waiting_remote",
            "completed_at": "unknown",
            "error_code": "unknown",
            "error_message": "",
            "failed_step": "unknown",
            "ozon": ozon,
        })
    if str(status.get("status") or "").upper() in (REMOTE_PENDING_PUBLICATION_STATES | TERMINAL_PUBLICATION_STATES):
        status = dict(status)
        status["warnings"] = current_public_warnings(status.get("warnings") or [])
    worker = active_product_worker(product_dir)
    if not worker:
        return status
    effective = dict(status)
    effective.update({
        "status": "PROCESSING",
        "current_step": status.get("current_step") if status.get("current_step") not in {None, "", "queue"} else "image_generation",
        "active_step": status.get("active_step") or {
            "name": status.get("current_step") if status.get("current_step") not in {None, "", "queue"} else "image_generation",
            "started_at": worker.get("started_at") or now_iso(),
        },
        "last_run_at": worker.get("started_at") or status.get("last_run_at") or now_iso(),
    })
    return effective


def prepare_partial_upload_resume(product_dir: Path, effective_status: Dict[str, Any]) -> Dict[str, Any]:
    """Persist the effective PARTIAL state so the batch runner can resume ozon_upload."""
    if str(effective_status.get("status") or "").upper() != "PARTIAL":
        return effective_status
    status_path = product_dir / "status.json"
    stored_status = load_optional_json(status_path)
    completed = [
        step for step in (effective_status.get("completed_steps") or stored_status.get("completed_steps") or [])
        if step in PIPELINE_STEPS and step != "ozon_upload"
    ]
    if not completed:
        completed = [step for step in PIPELINE_STEPS if step != "ozon_upload"]
    stored_status.update({
        "status": "PARTIAL",
        "current_step": "ozon_upload",
        "active_step": None,
        "progress": min(int(stored_status.get("progress") or effective_status.get("progress") or 95), 95),
        "completed_steps": completed,
        "pending_steps": ["ozon_upload"],
        "next_action": "ozon_upload",
        "task_authorized": True,
        "failed_step": "unknown",
        "error_code": "unknown",
        "error_message": "部分已选店铺尚未提交；继续时只处理未完成店铺。",
        "human_message": "部分店铺未提交，继续上传未完成店铺。",
        "attention_required": False,
    })
    stored_status.setdefault("history", []).append({
        "from": effective_status.get("previous_status") or "HANDED_OFF_TO_OZON",
        "to": "PARTIAL",
        "at": now_iso(),
        "reason": "同步店铺发布表中的部分提交状态，用于继续上传未完成店铺。",
    })
    atomic_write_json(status_path, stored_status)
    return stored_status


def launch_batch_process(batch: Dict[str, Any]) -> Dict[str, Any]:
    BATCH_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    log_handle = BATCH_LOG_PATH.open("a", encoding="utf-8")
    process = subprocess.Popen(
        [sys.executable, str(ROOT / "scripts/run_batch.py"), "--batch-id", batch["batch_id"]],
        cwd=ROOT, stdout=log_handle, stderr=subprocess.STDOUT,
        start_new_session=True, close_fds=True,
    )
    log_handle.close()
    BATCH_PID_PATH.write_text(str(process.pid), encoding="utf-8")
    atomic_write_json(CURRENT_BATCH_PATH, {"batch_id": batch["batch_id"], "pid": process.pid, "started_at": now_iso()})
    return {"pid": process.pid, "batch_id": batch["batch_id"]}


def connected_store_ids() -> List[str]:
    return [store["id"] for store in list_stores(ROOT) if store["enabled"] and store["connection_status"] == "connected"]


UPLOAD_READY_PRODUCT_STATES = {"WAITING_MANUAL_REVIEW"}
UPLOAD_NOT_STARTED_STATES = {"", "unknown", "not_started", "failed"}


def waiting_for_user_upload(product_id: str) -> bool:
    """True when a product is locally finished and the next click means upload."""
    status = load_optional_json(PRODUCTS_DIR / product_id / "status.json")
    return (
        str(status.get("status") or "").upper() in UPLOAD_READY_PRODUCT_STATES
        and int(status.get("api_write_count") or 0) == 0
        and str((status.get("ozon") or {}).get("upload_status") or "not_started") in UPLOAD_NOT_STARTED_STATES
    )


def validate_target_stores(store_ids: Iterable[str]) -> List[str]:
    selected = list(dict.fromkeys(str(value) for value in store_ids if str(value).strip()))
    if not selected:
        raise HTTPException(status_code=422, detail="请先明确选择至少一家已验证店铺")
    available = set(connected_store_ids())
    unavailable = [store_id for store_id in selected if store_id not in available]
    if unavailable:
        raise HTTPException(status_code=422, detail="店铺未启用或尚未通过只读验证：" + "、".join(unavailable))
    return selected


def saved_target_store_candidates(product_dir: Path) -> List[str]:
    """Return the stores already tied to this product/run.

    The workbench has multiple entry points.  The product detail page keeps the
    currently selected stores in browser state, while inbox/attention cards only
    know the product id.  A Continue click from those cards must still resume the
    same product instead of silently doing nothing because the browser did not
    include store_ids in the request.
    """
    candidates: List[str] = []

    def add_many(values: Any) -> None:
        if not isinstance(values, list):
            return
        for value in values:
            text = str(value or "").strip()
            if text:
                candidates.append(text)

    status = effective_product_status(
        product_dir,
        load_optional_json(product_dir / "status.json"),
    )
    add_many(status.get("target_store_ids_for_run"))
    add_many(status.get("target_store_ids"))

    batch_id = str(status.get("batch_id") or "").strip()
    if batch_id and batch_id != "unknown":
        batch = load_optional_json(batch_path(ROOT, batch_id))
        add_many(batch.get("target_store_ids"))
        for entry in batch.get("products") or []:
            if str(entry.get("product_id") or "") == product_dir.name:
                add_many(entry.get("target_store_ids"))

    publications = load_publications(product_dir)
    for store_id, record in (publications.get("stores") or {}).items():
        if record.get("selected"):
            candidates.append(str(record.get("store_id") or store_id))

    return list(dict.fromkeys(value for value in candidates if value))


def _confirmation_source_label(source: str) -> str:
    return {
        "1688": "1688文字",
        "sku_specification": "SKU文字",
        "product_analysis": "已有商品分析",
        "estimated": "本地同类规则",
    }.get(str(source or ""), "本地规则")


def _source_material(source: Dict[str, Any]) -> Dict[str, Any]:
    material_names = {"材质", "材料", "主体材质", "产品材质", "面料"}
    for item in source.get("product_attributes") or []:
        if str(item.get("name_cn") or "").strip() not in material_names:
            continue
        value = str(item.get("value_cn") or "").strip()
        if value and value != "unknown":
            return {
                "value": value,
                "confidence": 100,
                "source": "1688文字",
                "estimated": False,
                "needs_input": False,
            }
    return {
        "value": "unknown",
        "confidence": 0,
        "source": "没有可靠依据",
        "estimated": False,
        "needs_input": True,
    }


def _confirmation_image_url(product_id: str, image_type: str, index: int) -> str:
    return f"/api/workbench/products/{urllib.parse.quote(product_id)}/source-images/{image_type}/{index}"


def _source_image_entries(product_id: str, source: Dict[str, Any], image_type: str) -> List[Dict[str, Any]]:
    if image_type == "sku":
        values = source.get("skus") or []
        result = []
        for index, item in enumerate(values):
            local_path = item.get("local_image_path") or item.get("variant_local_image_path")
            if not local_path or local_path == "unknown":
                continue
            result.append({
                "index": index,
                "label": str(item.get("sku_name") or item.get("sku_id") or f"SKU {index + 1}"),
                "url": _confirmation_image_url(product_id, "sku", index),
            })
        return result
    source_key = "main_images" if image_type == "main" else "detail_images"
    result = []
    for index, item in enumerate(source.get(source_key) or []):
        local_path = item.get("local_path")
        if not local_path or local_path == "unknown":
            continue
        result.append({
            "index": index,
            "label": "1688主图" if image_type == "main" else "1688详情图",
            "url": _confirmation_image_url(product_id, image_type, index),
        })
    return result


def workbench_sku_image_binding_candidates(product_dir: Path, source: Dict[str, Any]) -> List[Dict[str, Any]]:
    product_id = product_dir.name
    candidates = []
    for item in available_binding_candidates(product_dir, source):
        candidate = dict(item)
        image_type = str(candidate.get("image_type") or "")
        source_index = int(candidate.get("source_index") or 0)
        candidate["url"] = _confirmation_image_url(product_id, image_type, source_index)
        if image_type == "main":
            candidate["display_source"] = "1688主图"
        elif image_type == "detail":
            candidate["display_source"] = "1688详情图"
        else:
            candidate["display_source"] = "SKU图"
        candidates.append(candidate)
    return candidates


def build_product_confirmation(product_dir: Path) -> Dict[str, Any]:
    source = load_optional_json(product_dir / "input/source.json")
    category = load_optional_json(product_dir / "input/category-selection.json")
    analysis = load_optional_json(product_dir / "output/product-analysis.json", {
        "product_type": source.get("title_cn") or "unknown",
        "category": " / ".join(category.get("category_path_zh") or category.get("category_path") or []),
        "facts": {},
    })
    rules = load_optional_json(PRICING_RULES_PATH)
    profiles = rules.get("measurement_profiles") or []
    package_rules = rules.get("package_estimation") or {}
    if not profiles or not package_rules:
        raise HTTPException(status_code=500, detail="本地重量尺寸规则未配置")
    product_weight = estimate_product_weight(source, analysis, profiles)
    product_dimensions = estimate_product_dimensions(source, analysis, profiles)
    product_weight = fit_estimated_product_weight_to_confirmed_package(source, product_weight, package_rules)
    product_dimensions = fit_estimated_product_dimensions_to_confirmed_package(source, product_dimensions, package_rules)
    package_weight = estimate_package_weight(source, product_weight, package_rules)
    package_dimensions = estimate_package_dimensions(source, product_dimensions, package_rules)
    material = _source_material(source)
    sku_images = _source_image_entries(product_dir.name, source, "sku")
    main_images = _source_image_entries(product_dir.name, source, "main")
    detail_images = _source_image_entries(product_dir.name, source, "detail")
    selected_image = (sku_images or main_images or detail_images or [{}])[0].get("url")
    path_zh = category.get("category_path_zh") or category.get("category_path") or []
    sku_values = []
    for sku in source.get("skus") or []:
        option_text = " / ".join(
            str(item.get("value_cn") or item.get("value") or "")
            for item in sku.get("option_values") or []
            if item.get("value_cn") or item.get("value")
        )
        sku_values.append({
            "sku_id": str(sku.get("sku_id") or "unknown"),
            "name": str(sku.get("sku_name") or option_text or "未命名SKU"),
            "option_text": option_text or str(sku.get("sku_name") or "未确认规格"),
            "purchase_price_cny": sku.get("purchase_price"),
        })
    fields = {
        "product_dimensions": {
            "value": {key: product_dimensions[key] for key in ("length", "width", "height")},
            "unit": "cm", "confidence": int(product_dimensions["confidence"]),
            "source": _confirmation_source_label(product_dimensions["source"]),
            "estimated": bool(product_dimensions["estimated"]),
        },
        "product_weight_g": {
            "value": product_weight["value"], "unit": "g", "confidence": int(product_weight["confidence"]),
            "source": _confirmation_source_label(product_weight["source"]),
            "estimated": bool(product_weight["estimated"]),
        },
        "package_dimensions": {
            "value": {key: package_dimensions[key] for key in ("length", "width", "height")},
            "unit": "cm", "confidence": int(package_dimensions["confidence"]),
            "source": _confirmation_source_label(package_dimensions["source"]),
            "estimated": bool(package_dimensions["estimated"]),
        },
        "package_weight_g": {
            "value": package_weight["value"], "unit": "g", "confidence": int(package_weight["confidence"]),
            "source": _confirmation_source_label(package_weight["source"]),
            "estimated": bool(package_weight["estimated"]),
        },
        "material": material,
    }
    uncertain_count = sum(
        1 for item in fields.values()
        if item.get("estimated") or item.get("needs_input") or int(item.get("confidence") or 0) < 80
    )
    rules_snapshot = category.get("rules_snapshot") or {}
    return {
        "product_id": product_dir.name,
        "title_cn": str(source.get("title_cn") or product_dir.name),
        "source_url": str(source.get("source_url") or "unknown"),
        "category_id": category.get("category_id"),
        "type_id": category.get("type_id"),
        "category_path_zh": path_zh,
        "rules_snapshot_hash": category.get("rules_snapshot_hash") or "unknown",
        "required_attribute_count": len(rules_snapshot.get("required_attribute_ids") or []),
        "aspect_attribute_count": len(rules_snapshot.get("aspect_attribute_ids") or []),
        "sku_count": len(sku_values),
        "skus": sku_values,
        "fields": fields,
        "uncertain_count": uncertain_count,
        "thumbnail_url": selected_image,
        "sku_images": sku_images,
        "main_images": main_images,
        "reference_images": detail_images or main_images,
        "ordinary_field_count": max(0, len(rules_snapshot.get("attributes") or []) - uncertain_count),
        "omitted_without_evidence": ["认证", "承重", "特殊安全功能"],
    }


def build_batch_confirmation(batch: Dict[str, Any]) -> Dict[str, Any]:
    products = [build_product_confirmation(workbench_product_dir(product_id)) for product_id in batch_product_ids(batch)]
    return {
        "schema_version": "1.0.0",
        "batch_id": batch.get("batch_id"),
        "status": batch.get("status"),
        "mode": "auto" if batch.get("auto_upload") else "manual",
        "target_store_ids": batch.get("target_store_ids") or [],
        "product_count": len(products),
        "sku_count": sum(item["sku_count"] for item in products),
        "uncertain_count": sum(item["uncertain_count"] for item in products),
        "estimated_seconds": max(15, min(90, sum(item["uncertain_count"] for item in products) * 5)),
        "products": products,
        "created_at": batch.get("created_at"),
        "confirmed_at": batch.get("confirmed_at") or "unknown",
        "write_api_calls": 0,
        "inventory_api_calls": 0,
    }


def load_workbench_run_queue() -> Dict[str, Any]:
    if not WORKBENCH_RUN_QUEUE_PATH.is_file():
        return {"schema_version": "1.0.0", "items": []}
    try:
        data = json.loads(WORKBENCH_RUN_QUEUE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"schema_version": "1.0.0", "items": []}
    return {"schema_version": "1.0.0", "items": list(data.get("items") or [])}


def save_workbench_run_queue(queue: Dict[str, Any]) -> None:
    atomic_write_json(WORKBENCH_RUN_QUEUE_PATH, {
        "schema_version": "1.0.0", "updated_at": now_iso(), "items": list(queue.get("items") or []),
    })


def bounded_parallel_plan() -> Dict[str, Any]:
    settings = load_optional_json(ROOT / "config/pipeline-settings.json")
    codex_workers = max(1, int(settings.get("codex_concurrency", 2)))
    image_products = max(1, int(settings.get("image_generation_concurrency", 1)))
    image_slots = max(1, int(settings.get("image_slot_concurrency", 3)))
    upload_products = max(1, int(settings.get("ozon_write_concurrency", 1)))
    return {
        "mode": "bounded_parallel",
        "label": "受控并行",
        "codex_product_concurrency": codex_workers,
        "image_product_concurrency": image_products,
        "image_slot_concurrency": image_slots,
        "ozon_write_concurrency": upload_products,
        "summary": (
            f"资料最多{codex_workers}件并行 · 生图同时{image_products}件/每件{image_slots}张 · "
            f"上传同时{upload_products}件"
        ),
    }


def workbench_queue_summary() -> Dict[str, Any]:
    queue_items = list(load_workbench_run_queue().get("items") or [])
    queued_product_count = 0
    priority_batch_ids: List[str] = []
    priority_product_ids: List[str] = []
    for item in queue_items:
        batch_id = str(item.get("batch_id") or "")
        batch = load_optional_json(batch_path(ROOT, batch_id)) if batch_id else {}
        queued_product_count += len(batch_product_ids(batch)) or int(item.get("product_count") or 0)
        if item.get("priority") == "manual_upload" or batch.get("execution_priority") == "manual_upload":
            priority_batch_ids.append(batch_id)
            priority_product_ids.extend(batch_product_ids(batch))

    active_pid = running_batch_pid()
    current = load_optional_json(CURRENT_BATCH_PATH) if active_pid else {}
    active_batch_id = str(current.get("batch_id") or "")
    active_batch = load_optional_json(batch_path(ROOT, active_batch_id)) if active_batch_id else {}
    active_product_count = len(batch_product_ids(active_batch))
    active_priority_upload = active_batch.get("execution_priority") == "manual_upload"
    if active_priority_upload:
        priority_batch_ids.append(active_batch_id)
        priority_product_ids.extend(batch_product_ids(active_batch))
    stop_request = load_optional_json(SAFE_STOP_REQUEST_PATH)
    priority_preemption_pending = (
        str(stop_request.get("mode") or "") == "priority_manual_upload"
        and str(stop_request.get("priority_batch_id") or "") in priority_batch_ids
    )
    waiting_for_active = bool(active_pid and queued_product_count)

    priority_names = "、".join(dict.fromkeys(priority_product_ids))
    if active_priority_upload:
        message = f"{priority_names or '已确认商品'} 正在优先提交 Ozon"
    elif priority_preemption_pending:
        message = f"{priority_names or '已确认商品'} 已设为优先上传，正在安全结束当前生成步骤"
    elif priority_product_ids and active_pid:
        message = f"{priority_names} 已进入优先上传队列，等待当前不可中断步骤结束"
    elif priority_product_ids:
        message = f"{priority_names} 已进入优先上传队列"
    elif waiting_for_active:
        message = (
            f"当前 {active_product_count or 1} 件处理中，后续 {queued_product_count} 件已合并为 "
            f"{len(queue_items)} 个任务"
        )
    elif queued_product_count:
        message = f"{queued_product_count} 件商品已合并为 {len(queue_items)} 个任务，正在按上限调度"
    elif active_pid:
        message = f"{active_product_count or 1} 件商品任务已开始，系统正按并发上限处理"
    else:
        message = "当前没有等待运行的商品"
    return {
        "active_batch_id": active_batch_id or None,
        "active_product_count": active_product_count,
        "queued_batch_count": len(queue_items),
        "queued_product_count": queued_product_count,
        "waiting_for_active": waiting_for_active,
        "priority_batch_ids": list(dict.fromkeys(priority_batch_ids)),
        "priority_product_ids": list(dict.fromkeys(priority_product_ids)),
        "priority_preemption_pending": priority_preemption_pending,
        "active_priority_upload": active_priority_upload,
        "message": message,
    }


def confirmed_manual_upload_batch(batch: Dict[str, Any]) -> bool:
    if not batch.get("auto_upload") or not batch_product_ids(batch):
        return False
    for product_id in batch_product_ids(batch):
        status = load_optional_json(PRODUCTS_DIR / product_id / "status.json")
        if (
            str(status.get("status") or "").upper() not in UPLOAD_READY_PRODUCT_STATES
            or int(status.get("api_write_count") or 0) > 0
            or str((status.get("ozon") or {}).get("upload_status") or "not_started")
            not in UPLOAD_NOT_STARTED_STATES
        ):
            return False
    return True


def mark_confirmed_upload_priority(batch: Dict[str, Any]) -> Dict[str, Any]:
    batch_id = str(batch.get("batch_id") or "")
    batch.update({
        "execution_priority": "manual_upload",
        "priority_reason": "user_confirmed_upload",
        "priority_requested_at": batch.get("priority_requested_at") or now_iso(),
    })
    for product_id in batch_product_ids(batch):
        status_path = PRODUCTS_DIR / product_id / "status.json"
        status = load_optional_json(status_path)
        if str(status.get("status") or "").upper() not in UPLOAD_READY_PRODUCT_STATES:
            continue
        status.update({
            "next_action": "ozon_upload",
            "task_authorized": True,
            "batch_id": batch_id,
            "upload_priority": "manual_upload",
            "upload_priority_state": "queued",
            "last_run_at": now_iso(),
        })
        status["completed_steps"] = [
            step for step in status.get("completed_steps") or [] if step != "ozon_upload"
        ]
        status["pending_steps"] = list(dict.fromkeys([
            *(status.get("pending_steps") or []), "ozon_upload",
        ]))
        atomic_write_json(status_path, status)
    atomic_write_json(batch_path(ROOT, batch_id), batch)
    return batch


def mark_products_queued_for_batch(batch: Dict[str, Any], *, priority_upload: bool = False) -> None:
    """Reflect a queued batch in product status so workbench buttons are not inert.

    The queued batch remains the source of truth for duplicate protection.  This
    status projection is only for the workbench UI and keeps the previous failure
    fields in audit keys so a queued upload retry no longer looks like an
    unresolved local error.
    """
    batch_id = str(batch.get("batch_id") or "")
    queued_at = now_iso()
    for product_id in batch_product_ids(batch):
        status_path = PRODUCTS_DIR / product_id / "status.json"
        status = load_optional_json(status_path)
        product_dir = PRODUCTS_DIR / product_id
        if active_product_worker(product_dir):
            continue
        current = str(status.get("status") or "").upper()
        if current in {"HANDED_OFF_TO_OZON", "CREATED", "UPLOADED", "ACTIVE", "PENDING_REMOTE"}:
            continue
        if int(status.get("api_write_count") or 0) > 0 and str((status.get("ozon") or {}).get("upload_status") or "") not in {"failed", "not_started", "unknown", ""}:
            continue
        status = normalize_checkpoint(reconcile_completed_artifacts(product_dir, status))
        next_action = status.get("pending_steps", ["complete"])[0] if status.get("pending_steps") else "complete"
        status.setdefault("queued_from_status", status.get("status") or "unknown")
        status.setdefault("queued_from_failed_step", status.get("failed_step") or "unknown")
        status.setdefault("queued_from_error_message", status.get("error_message") or "unknown")
        previous = status.get("status") or "unknown"
        status.update({
            "status": "QUEUED",
            "current_step": "queue",
            "batch_id": batch_id,
            "task_authorized": True,
            "queued_at": queued_at,
            "last_run_at": queued_at,
            "error_code": "unknown",
            "error_message": "unknown",
        })
        if batch.get("auto_upload"):
            status["next_action"] = next_action
            status["upload_priority_state"] = "queued" if priority_upload else "queued"
        else:
            status["next_action"] = next_action if next_action in PIPELINE_STEPS else (status.get("next_action") or "run")
        history = status.setdefault("history", [])
        if not history or history[-1].get("to") != "QUEUED":
            history.append({
                "from": previous,
                "to": "QUEUED",
                "at": queued_at,
                "reason": f"User started batch task {batch_id}; no per-product review is required.",
            })
        atomic_write_json(status_path, status)


def active_batch_preemptible_for_upload(batch_id: str) -> bool:
    batch = load_optional_json(batch_path(ROOT, batch_id)) if batch_id else {}
    if not batch or batch.get("execution_priority") == "manual_upload":
        return False
    existing_stop = load_optional_json(SAFE_STOP_REQUEST_PATH)
    if existing_stop and str(existing_stop.get("batch_id") or "") != batch_id:
        return False
    for product_id in batch_product_ids(batch):
        status = load_optional_json(PRODUCTS_DIR / product_id / "status.json")
        current_step = str(status.get("current_step") or "")
        next_action = str(status.get("next_action") or "")
        upload_status = str((status.get("ozon") or {}).get("upload_status") or "")
        if (
            int(status.get("api_write_count") or 0) > 0
            or current_step == "ozon_upload"
            or next_action == "ozon_upload"
            or upload_status not in {"", "unknown", "not_started", "failed"}
        ):
            return False
    return True


def request_priority_upload_preemption(queue: Dict[str, Any], priority_batch_id: str) -> bool:
    # A production run is authorized to continue through generation and upload.
    # Later queued uploads must wait; they may not stop the active product.
    return False


def reconcile_priority_upload_queue() -> Dict[str, Any]:
    with BATCH_QUEUE_LOCK:
        queue = load_workbench_run_queue()
        items = list(queue.get("items") or [])
        priority_batch_ids: List[str] = []
        for item in items:
            batch_id = str(item.get("batch_id") or "")
            batch = load_optional_json(batch_path(ROOT, batch_id)) if batch_id else {}
            if not batch or not (
                item.get("priority") == "manual_upload"
                or batch.get("execution_priority") == "manual_upload"
                or confirmed_manual_upload_batch(batch)
            ):
                continue
            mark_confirmed_upload_priority(batch)
            item.update({"source": "manual_upload", "priority": "manual_upload"})
            priority_batch_ids.append(batch_id)
        queue["items"] = sorted(
            items,
            key=lambda item: 0 if item.get("priority") == "manual_upload" else 1,
        )
        preemption_requested = bool(priority_batch_ids) and request_priority_upload_preemption(
            queue, priority_batch_ids[0]
        )
        save_workbench_run_queue(queue)
        return {
            "priority_batch_ids": priority_batch_ids,
            "preemption_requested": preemption_requested,
            "queue": queue["items"],
        }


def _queued_batch_merge_signature(batch: Dict[str, Any]) -> Optional[tuple[Any, ...]]:
    if (
        str(batch.get("status") or "").upper() != "QUEUED"
        or bool(batch.get("auto_upload"))
        or str(batch.get("review_mode") or "manual") != "manual"
    ):
        return None
    product_ids = batch_product_ids(batch)
    if not product_ids:
        return None
    for product_id in product_ids:
        product_dir = PRODUCTS_DIR / product_id
        status = load_optional_json(product_dir / "status.json")
        if (
            not product_dir.is_dir()
            or str(status.get("status") or "").upper() not in {"COLLECTED", "QUEUED"}
            or int(status.get("api_write_count") or 0) > 0
            or (product_dir / "output/ozon-write-receipt.json").is_file()
        ):
            return None
    return (
        tuple(str(value) for value in batch.get("target_store_ids") or []),
        str(batch.get("review_mode") or "manual"),
        bool(batch.get("auto_upload")),
    )


def coalesce_compatible_queued_batches() -> Dict[str, Any]:
    """Merge compatible, not-yet-started generation batches without touching active work."""
    queue = load_workbench_run_queue()
    items = list(queue.get("items") or [])
    if len(items) < 2:
        return {"merged_batch_count": 0, "product_count": 0, "batch_map": {}}
    output_items: List[Dict[str, Any]] = []
    targets: Dict[tuple[Any, ...], Dict[str, Any]] = {}
    batch_map: Dict[str, str] = {}
    merged_batch_count = 0
    merged_product_count = 0
    touched_targets: set[str] = set()
    for item in items:
        batch_id = str(item.get("batch_id") or "")
        batch = load_optional_json(batch_path(ROOT, batch_id)) if batch_id else {}
        signature = _queued_batch_merge_signature(batch)
        if signature is None or str(item.get("source") or "") not in {"workbench_batch", "single_product"}:
            output_items.append(item)
            continue
        target = targets.get(signature)
        if target is None:
            target = {"batch_id": batch_id, "batch": batch, "item": dict(item)}
            targets[signature] = target
            output_items.append(target["item"])
            continue
        target_batch = target["batch"]
        existing_ids = set(batch_product_ids(target_batch))
        additions = [entry for entry in batch.get("products") or [] if str(entry.get("product_id") or "") not in existing_ids]
        if not additions:
            batch_map[batch_id] = target["batch_id"]
            merged_batch_count += 1
            continue
        target_batch.setdefault("products", []).extend(additions)
        target_batch["product_count"] = len(target_batch["products"])
        target_batch["sku_count"] = sum(int(entry.get("selected_sku_count") or 0) for entry in target_batch["products"])
        target_batch.update({"processing_count": 0, "success_count": 0, "failed_count": 0, "progress": 0})
        target["item"]["product_count"] = target_batch["product_count"]
        touched_targets.add(target["batch_id"])
        for entry in additions:
            product_id = str(entry.get("product_id") or "")
            status_path = PRODUCTS_DIR / product_id / "status.json"
            status = load_optional_json(status_path)
            if status:
                status["batch_id"] = target["batch_id"]
                atomic_write_json(status_path, status)
        batch.update({
            "status": "STOPPED",
            "completed_at": now_iso(),
            "processing_count": 0,
            "local_lifecycle_status": "ARCHIVED",
            "archived_at": now_iso(),
            "archive_reason": f"queue_coalesced_into_{target['batch_id']}",
        })
        atomic_write_json(batch_path(ROOT, batch_id), batch)
        batch_map[batch_id] = target["batch_id"]
        merged_batch_count += 1
        merged_product_count += len(additions)
    for target in targets.values():
        if target["batch_id"] in touched_targets:
            atomic_write_json(batch_path(ROOT, target["batch_id"]), target["batch"])
    if merged_batch_count:
        queue["items"] = output_items
        save_workbench_run_queue(queue)
    return {
        "merged_batch_count": merged_batch_count,
        "product_count": sum(int(item.get("product_count") or 0) for item in output_items),
        "merged_product_count": merged_product_count,
        "batch_map": batch_map,
        "queue": output_items,
    }


def batch_product_ids(batch: Dict[str, Any]) -> List[str]:
    return [str(item.get("product_id")) for item in batch.get("products") or [] if item.get("product_id")]


def reserved_product_batches() -> Dict[str, str]:
    reserved: Dict[str, str] = {}
    if running_batch_pid() is not None:
        current = load_optional_json(CURRENT_BATCH_PATH)
        batch_id = str(current.get("batch_id") or "")
        current_batch = load_optional_json(batch_path(ROOT, batch_id)) if batch_id else {}
        for product_id in batch_product_ids(current_batch):
            reserved[product_id] = batch_id
    for item in load_workbench_run_queue().get("items") or []:
        batch_id = str(item.get("batch_id") or "")
        queued_batch = load_optional_json(batch_path(ROOT, batch_id)) if batch_id else {}
        for product_id in batch_product_ids(queued_batch):
            reserved.setdefault(product_id, batch_id)
    for path in (ROOT / "batches").glob("B-*/batch.json"):
        waiting_batch = load_optional_json(path)
        if str(waiting_batch.get("local_lifecycle_status") or "").upper() == "ARCHIVED":
            continue
        if waiting_batch.get("status") != "AWAITING_CONFIRMATION":
            continue
        batch_id = str(waiting_batch.get("batch_id") or path.parent.name)
        for product_id in batch_product_ids(waiting_batch):
            reserved.setdefault(product_id, batch_id)
    return reserved


def dispatch_next_queued_batch() -> Optional[Dict[str, Any]]:
    with BATCH_QUEUE_LOCK:
        if running_batch_pid() is not None:
            return None
        queue = load_workbench_run_queue()
        items = list(queue.get("items") or [])
        while items:
            item = items.pop(0)
            batch_id = str(item.get("batch_id") or "")
            queued_batch = load_optional_json(batch_path(ROOT, batch_id)) if batch_id else {}
            valid_products = [
                product_id for product_id in batch_product_ids(queued_batch)
                if (ROOT / "products" / product_id).is_dir()
                and not product_is_archived(ROOT / "products" / product_id)
                and not deletion_marker_path(ROOT, product_id).is_file()
            ]
            if not queued_batch or not valid_products:
                queue["items"] = items
                save_workbench_run_queue(queue)
                continue
            launched = launch_batch_process(queued_batch)
            queue["items"] = items
            save_workbench_run_queue(queue)
            return {"status": "started", **launched, "queue_position": 0}
        queue["items"] = []
        save_workbench_run_queue(queue)
        return None


def batch_dispatcher_worker() -> None:
    while True:
        try:
            dispatch_next_queued_batch()
        except Exception as exc:
            BATCH_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with BATCH_LOG_PATH.open("a", encoding="utf-8") as handle:
                handle.write(f"[{now_iso()}] batch dispatcher error: {exc}\n")
        BATCH_DISPATCHER_WAKE.wait(2)
        BATCH_DISPATCHER_WAKE.clear()


def ensure_batch_dispatcher() -> None:
    global BATCH_DISPATCHER_STARTED
    with BATCH_DISPATCHER_LOCK:
        if BATCH_DISPATCHER_STARTED:
            BATCH_DISPATCHER_WAKE.set()
            return
        threading.Thread(target=batch_dispatcher_worker, daemon=True, name="workbench-batch-dispatcher").start()
        BATCH_DISPATCHER_STARTED = True


def _pid_is_alive(value: Any) -> bool:
    try:
        pid = int(value)
        os.kill(pid, 0)
    except (OSError, TypeError, ValueError):
        return False
    try:
        state = subprocess.run(
            ["/bin/ps", "-o", "stat=", "-p", str(pid)],
            capture_output=True, text=True, timeout=2, check=False,
        ).stdout.strip()
        if not state or state.upper().startswith("Z"):
            return False
    except (OSError, subprocess.SubprocessError):
        pass
    return True


def recover_interrupted_batch() -> Optional[Dict[str, Any]]:
    """Resume an authorized local batch after an unexpected computer/service restart."""
    if "pytest" in sys.modules or os.getenv("CAF_DISABLE_BATCH_RECOVERY", "0") == "1":
        return None
    if running_batch_pid() is not None or not CURRENT_BATCH_PATH.is_file():
        return None
    current = load_optional_json(CURRENT_BATCH_PATH)
    batch_id = str(current.get("batch_id") or "")
    batch = load_optional_json(batch_path(ROOT, batch_id)) if batch_id else {}
    if batch.get("status") not in {"RUNNING", "QUEUED"}:
        return None
    resumable = []
    for product_id in batch_product_ids(batch):
        product_dir = ROOT / "products" / product_id
        status = load_optional_json(product_dir / "status.json")
        if (
            product_dir.is_dir()
            and not product_is_archived(product_dir)
            and status.get("task_authorized") is True
            and status.get("status") in {"PROCESSING", "QUEUED"}
        ):
            resumable.append(product_id)
        worker_path = ROOT / "logs/product-workers" / f"{product_id}.json"
        worker = load_optional_json(worker_path)
        if worker_path.is_file() and not _pid_is_alive(worker.get("pid")):
            worker_path.unlink(missing_ok=True)
        lock_path = product_dir / ".pipeline.lock"
        if lock_path.is_file():
            try:
                lock_pid = int(lock_path.read_text(encoding="utf-8").strip())
            except (OSError, ValueError):
                lock_pid = 0
            if not _pid_is_alive(lock_pid):
                lock_path.unlink(missing_ok=True)
    if not resumable:
        return None
    launched = launch_batch_process(batch)
    BATCH_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with BATCH_LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(
            f"[{now_iso()}] 自动恢复意外中断批次 {batch_id}，从文件断点继续：{', '.join(resumable)}\n"
        )
    return {"status": "recovered", **launched, "product_ids": resumable}


def launch_or_enqueue_batch(batch: Dict[str, Any], source: str) -> Dict[str, Any]:
    with BATCH_QUEUE_LOCK:
        priority_upload = source == "manual_upload" or confirmed_manual_upload_batch(batch)
        if priority_upload:
            batch = mark_confirmed_upload_priority(batch)
        if running_batch_pid() is None:
            launched = launch_batch_process(batch)
            ensure_batch_dispatcher()
            return {
                "status": "started", **launched, "queue_position": 0,
                "priority_upload": priority_upload,
                "message": "已开始优先提交 Ozon" if priority_upload else "任务已启动",
            }
        queue = load_workbench_run_queue()
        items = list(queue.get("items") or [])
        queued_item = {
            "batch_id": batch["batch_id"], "source": source,
            "queued_at": now_iso(), "product_count": batch.get("product_count", 0),
        }
        if priority_upload:
            queued_item.update({"source": "manual_upload", "priority": "manual_upload"})
            first_regular = next(
                (index for index, item in enumerate(items) if item.get("priority") != "manual_upload"),
                len(items),
            )
            items.insert(first_regular, queued_item)
        else:
            items.append(queued_item)
        queue["items"] = items
        preemption_requested = (
            request_priority_upload_preemption(queue, batch["batch_id"])
            if priority_upload else False
        )
        save_workbench_run_queue(queue)
        mark_products_queued_for_batch(batch, priority_upload=priority_upload)
        merge_result = coalesce_compatible_queued_batches()
        effective_batch_id = merge_result.get("batch_map", {}).get(batch["batch_id"], batch["batch_id"])
        current_items = load_workbench_run_queue().get("items") or []
        queue_position = next(
            (index for index, item in enumerate(current_items, start=1) if item.get("batch_id") == effective_batch_id),
            len(current_items),
        )
        ensure_batch_dispatcher()
        return {
            "status": "queued", "batch_id": effective_batch_id, "queue_position": queue_position,
            "execution_plan": bounded_parallel_plan(),
            "coalesced": effective_batch_id != batch["batch_id"],
            "priority_upload": priority_upload,
            "preemption_requested": preemption_requested,
            "message": (
                "已设为优先上传，正在安全结束当前生成步骤"
                if preemption_requested else
                "已进入优先上传队列，等待当前不可中断步骤结束"
                if priority_upload else
                "任务已加入队列"
            ),
        }


@app.post("/api/tasks/run")
def run_collected_tasks() -> Dict[str, Any]:
    selected_stores: List[str] = []
    auto_upload = True
    # Starting a batch only schedules this local production pipeline.  It does
    # not start remote status polling and never waits on Ozon.
    with BATCH_QUEUE_LOCK:
        reserved = reserved_product_batches()
        product_ids = [
            path.name for path in collected_products(ROOT)
            if path.name not in reserved and product_is_owned(path)
        ]
        if not product_ids:
            return {"status": "empty", "queued_products": 0, "already_queued_products": len(reserved)}
        for product_id in product_ids:
            try:
                validate_formal_product_input(PRODUCTS_DIR / product_id)
            except ProductionInputError as exc:
                raise HTTPException(
                    status_code=422,
                    detail=f"{product_id} 不是当前工作台本次采集的正式输入，任务未启动：{exc}",
                ) from exc
        product_store_overrides: Dict[str, List[str]] = {}
        for product_id in product_ids:
            product_dir = PRODUCTS_DIR / product_id
            saved_store_ids = saved_target_store_candidates(product_dir)
            if not saved_store_ids:
                raise HTTPException(
                    status_code=422,
                    detail=f"{product_id} 还没有保存目标店铺，无法自动提交。请先在商品中选择店铺后再运行。",
                )
            product_store_overrides[product_id] = validate_target_stores(saved_store_ids)
        selected_stores = list(dict.fromkeys(
            store_id
            for store_ids in product_store_overrides.values()
            for store_id in store_ids
        ))
        batch = create_batch(
            ROOT,
            product_ids=product_ids,
            target_store_ids=selected_stores,
            auto_upload=auto_upload,
            product_store_overrides=product_store_overrides,
        )
        save_batch_owner(batch["batch_id"])
        launched = launch_or_enqueue_batch(batch, "collector")
    return {
        "status": launched["status"],
        "pid": launched.get("pid"),
        "batch_id": batch["batch_id"],
        "queue_position": launched.get("queue_position", 0),
        "queued_products": batch["product_count"],
        "queued_skus": batch["sku_count"],
        "max_selected_skus_per_product": MAX_SELECTED_SKUS_PER_PRODUCT,
        "target_store_ids": selected_stores,
        "auto_upload": auto_upload,
    }


def overlay_live_batch_status(batch: Dict[str, Any], products_dir: Path) -> Dict[str, Any]:
    live_products = []
    progress_values = []
    for entry in batch.get("products") or []:
        product_id = str(entry.get("product_id") or "")
        status_path = products_dir / product_id / "status.json"
        if not status_path.is_file():
            live_products.append(entry)
            continue
        status = effective_product_status(
            products_dir / product_id,
            json.loads(status_path.read_text(encoding="utf-8")),
        )
        progress = int(status.get("progress") or 0)
        live_products.append({
            **entry,
            "status": status.get("status", entry.get("status", "unknown")),
            "current_step": status.get("current_step", entry.get("current_step", "none")),
            "progress": progress,
            "started_at": status.get("started_at", entry.get("started_at", "unknown")),
            "completed_at": status.get("completed_at", entry.get("completed_at", "unknown")),
            "warnings": status.get("warnings", entry.get("warnings", [])),
            "errors": [status.get("error_message")]
            if status.get("error_message") not in {None, "unknown"} else [],
        })
        progress_values.append(progress)
    result = {**batch, "products": live_products}
    if progress_values:
        result["progress"] = round(sum(progress_values) / len(progress_values))
    status_values = [str(item.get("status") or "").upper() for item in live_products]
    if status_values:
        success_count = sum(item in TERMINAL_PUBLICATION_STATES for item in status_values)
        failed_count = sum(item in {*ATTENTION_STATES, "PARTIAL_FAILED"} for item in status_values)
        processing_count = sum(item in {"QUEUED", "PROCESSING", "UPLOADING"} for item in status_values)
        pending_remote_count = sum(item in REMOTE_PENDING_PUBLICATION_STATES for item in status_values)
        incomplete_count = max(
            0,
            len(status_values) - success_count - failed_count - processing_count - pending_remote_count,
        )
        result.update({
            "product_count": len(status_values),
            "success_count": success_count,
            "failed_count": failed_count,
            "processing_count": processing_count,
            "pending_remote_count": pending_remote_count,
            "incomplete_count": incomplete_count,
        })
        if success_count == len(status_values):
            result["status"] = "COMPLETED"
        elif failed_count:
            result["status"] = "COMPLETED_WITH_ERRORS"
        elif processing_count:
            result["status"] = "RUNNING"
        elif pending_remote_count or incomplete_count:
            result["status"] = "INCOMPLETE"
            result["display_status"] = "等待Ozon结果" if pending_remote_count and not incomplete_count else "未完成"
    return result


def manual_upload_product_ids(batch: Dict[str, Any]) -> List[str]:
    if batch.get("auto_upload", False):
        return []
    return [
        str(item.get("product_id"))
        for item in batch.get("products") or []
        if str(item.get("status") or "").upper() == "WAITING_MANUAL_REVIEW"
        and item.get("product_id")
    ]


@app.get("/api/tasks/status")
def get_batch_status() -> Dict[str, Any]:
    pid = running_batch_pid()
    current = json.loads(CURRENT_BATCH_PATH.read_text(encoding="utf-8")) if CURRENT_BATCH_PATH.is_file() else {}
    current_path = batch_path(ROOT, current.get("batch_id", "")) if current.get("batch_id") else None
    batch = json.loads(current_path.read_text(encoding="utf-8")) if current_path and current_path.is_file() else None
    if batch and batch_is_owned(str(batch.get("batch_id") or "")):
        batch = overlay_live_batch_status(batch, PRODUCTS_DIR)
    elif batch:
        batch = None
    report_path = ROOT / "batch-result.json"
    report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.is_file() else None
    if report and not batch_is_owned(str(report.get("batch_id") or "")):
        report = None
    elif report:
        report = overlay_live_batch_status(report, PRODUCTS_DIR)
    return {"running": pid is not None, "pid": pid, "current_batch": batch, "last_result": report}


@app.post("/api/collector/products")
async def create_product(request: Request) -> Dict[str, Any]:
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail={"message": "Payload must be a JSON object"})
    return ingest_capture(payload, current_operator())


@app.post("/api/collector/ozon-reference-page")
async def create_collector_ozon_reference_page(request: Request) -> Dict[str, Any]:
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="Ozon参考页面快照格式错误")
    source_url = normalize_ozon_reference_url(payload.get("source_url") or payload.get("url"))
    if "ozon." not in urllib.parse.urlparse(source_url).netloc:
        raise HTTPException(status_code=422, detail="当前页面不是 Ozon 商品页")
    manual_inputs = parse_ozon_reference_manual_inputs(payload)
    reference = extract_ozon_reference_from_browser_snapshot(source_url, payload)
    if not reference.get("title") and not reference.get("image_urls"):
        raise HTTPException(status_code=422, detail="插件没有在当前 Ozon 页面读取到标题或商品图片，请等待页面加载完成后重试")
    now = now_iso()
    operator_id = current_operator_id()
    with BATCH_QUEUE_LOCK:
        data = load_ozon_reference_tasks()
        items = [item for item in data.get("items") or [] if isinstance(item, dict)]
        match_index = next(
            (
                index for index, item in enumerate(items)
                if normalize_ozon_reference_url(item.get("source_url")) == source_url
            ),
            -1,
        )
        if match_index >= 0:
            task = dict(items[match_index])
        else:
            task = {
                "schema_version": "1.0.0",
                "task_id": ozon_reference_task_id(source_url, now),
                "source_kind": "ozon_reference_listing",
                "source_url": source_url,
                "status": "queued",
                "display_status": "待处理",
                "target_store_ids": [],
                "mode": "create_without_inventory",
                "inventory_submission_enabled": False,
                "manual_inputs": manual_inputs,
                "created_at": now,
                "created_by": operator_id,
                "write_api_calls": 0,
                "inventory_api_calls": 0,
            }
        task["manual_inputs"] = manual_inputs
        task.update({
            "source_url": source_url,
            "status": "processing",
            "display_status": "插件采集中",
            "pipeline_status": "capturing_ozon_public_card_from_browser",
            "updated_at": now,
            "message": "正在使用浏览器插件提交的 Ozon 页面快照生成参考资料。",
            "write_api_calls": 0,
            "inventory_api_calls": 0,
        })
        if match_index >= 0:
            items[match_index] = task
        else:
            items.append(task)
        data["items"] = items
        save_ozon_reference_tasks(data)
    task_dir = ozon_reference_task_dir(str(task.get("task_id") or ""))
    task_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_json(task_dir / "browser-snapshot.json", {
        "schema_version": "1.0.0",
        "captured_at": now,
        "source_url": source_url,
        "plugin_version": payload.get("plugin_version") or "unknown",
        "title": reference.get("title") or "",
        "description": reference.get("description") or "",
        "category_path": reference.get("category_path") or [],
        "price": reference.get("price") or "",
        "currency": reference.get("currency") or "",
        "image_urls": reference.get("image_urls") or [],
        "page_text": reference.get("page_text") or "",
    })
    try:
        updated = materialize_ozon_reference_capture(
            task,
            reference,
            content_type="browser-plugin-snapshot",
            source_text=json.dumps(compact_ozon_reference_payload_for_storage(payload), ensure_ascii=False, indent=2),
            source_filename="browser-snapshot.raw.json",
            capture_method="browser_plugin",
        )
    except Exception as exc:
        updated = {
            **task,
            "status": "failed",
            "display_status": "插件采集失败",
            "pipeline_status": "ozon_reference_browser_capture_failed",
            "updated_at": now_iso(),
            "message": f"浏览器插件已读到页面，但生成参考资料失败：{exc}",
            "write_api_calls": 0,
            "inventory_api_calls": 0,
        }
    with BATCH_QUEUE_LOCK:
        data = load_ozon_reference_tasks()
        items = [item for item in data.get("items") or [] if isinstance(item, dict)]
        for index, existing in enumerate(items):
            if str(existing.get("task_id")) == str(updated.get("task_id")):
                items[index] = updated
                break
        else:
            items.append(updated)
        data["items"] = items
        save_ozon_reference_tasks(data)
    if str(updated.get("status") or "") == "waiting_ai_design":
        ensure_ozon_reference_dispatcher()
        OZON_REFERENCE_DISPATCHER_WAKE.set()
    return {
        "status": updated.get("status") or "unknown",
        "task": public_ozon_reference_task(updated),
        "message": updated.get("message") or "Ozon参考页面快照已接收",
        "write_api_calls": 0,
        "inventory_api_calls": 0,
    }


@app.get("/api/collector/categories/cache")
def collector_category_cache() -> FileResponse:
    cache = load_translated_tree_cache(ROOT)
    cache_path = effective_tree_cache_path(ROOT)
    if not cache or not cache_path.is_file():
        raise HTTPException(
            status_code=503,
            detail={"message": "Ozon官方简体中文类目尚未同步，禁止使用本地翻译类目"},
        )
    return FileResponse(
        cache_path,
        media_type="application/json",
        headers={
            "Cache-Control": "no-cache",
            "X-Ozon-Category-Source": "ozon_seller_api",
            "X-Ozon-Category-Language": "ZH_HANS",
            "X-Ozon-Category-Version": str(cache.get("cache_version") or "unknown"),
            "X-Ozon-Category-Generated-At": str(cache.get("generated_at") or "unknown"),
        },
    )


@app.get("/api/collector/categories")
def collector_category_search(q: str = "", limit: int = 30) -> Dict[str, Any]:
    if not load_translated_tree_cache(ROOT):
        raise HTTPException(status_code=503, detail={"message": "Ozon官方简体中文类目尚未同步"})
    items = search_categories(ROOT, q, limit)
    return {"query": q, "items": items, "count": len(items), "ozon_write_api_calls": 0, "inventory_api_calls": 0}


@app.get("/api/collector/categories/tree")
def collector_category_tree(parent_id: str = "root") -> Dict[str, Any]:
    try:
        items = category_tree_children(ROOT, parent_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail={"message": str(exc)}) from exc
    cache = load_translated_tree_cache(ROOT)
    return {
        "parent_id": parent_id,
        "items": items,
        "count": len(items),
        "locale": cache.get("locale") or "unknown",
        "cache_version": cache.get("cache_version") or "dynamic-fallback",
        "cache_source": cache.get("source") or "unavailable",
        "api_language": cache.get("api_language") or "unknown",
        "ozon_write_api_calls": 0,
        "inventory_api_calls": 0,
    }


@app.get("/api/collector/categories/recommendations")
def collector_category_recommendations(q: str) -> Dict[str, Any]:
    items = recommend_categories(ROOT, q)
    return {"query": q, "items": items[:3], "count": min(len(items), 3), "final_choice_required": True}


@app.get("/api/collector/categories/preferences")
def collector_category_preferences() -> Dict[str, Any]:
    return {**public_preferences(ROOT), "ozon_write_api_calls": 0, "inventory_api_calls": 0}


@app.put("/api/collector/categories/favorite")
async def collector_category_favorite(request: Request) -> Dict[str, Any]:
    payload = await request.json()
    try:
        return set_favorite(
            ROOT, int(payload.get("category_id")), int(payload.get("type_id")), bool(payload.get("favorite", True))
        )
    except (TypeError, ValueError, KeyError) as exc:
        raise HTTPException(status_code=422, detail={"message": str(exc)}) from exc


@app.post("/api/collector/categories/rules")
async def collector_category_rules(request: Request) -> Dict[str, Any]:
    payload = await request.json()
    try:
        return prepare_rules(
            ROOT,
            int(payload.get("category_id")),
            int(payload.get("type_id")),
            str(payload.get("shop_id") or "zhonglian1"),
            allow_fetch=bool(payload.get("allow_readonly_fetch", True)),
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=409, detail={"message": str(exc)}) from exc
    except (TypeError, ValueError, KeyError) as exc:
        raise HTTPException(status_code=422, detail={"message": str(exc)}) from exc


@app.get("/api/collector/products/{product_id}")
def get_product(product_id: str) -> Dict[str, Any]:
    product_dir = PRODUCTS_DIR / product_id
    source_path = product_dir / "input/source.json"
    status_path = product_dir / "status.json"
    if not source_path.is_file() or not status_path.is_file() or not product_is_owned(product_dir):
        raise HTTPException(status_code=404, detail="Product not found")
    return {
        "product_id": product_id,
        "source": json.loads(source_path.read_text(encoding="utf-8")),
        "status": json.loads(status_path.read_text(encoding="utf-8")),
        "category_selection": json.loads((product_dir / "input/category-selection.json").read_text(encoding="utf-8"))
        if (product_dir / "input/category-selection.json").is_file() else None,
    }


@app.put("/api/collector/products/{product_id}/category")
async def update_collected_product_category(product_id: str, request: Request) -> Dict[str, Any]:
    product_dir = PRODUCTS_DIR / product_id
    if not re.fullmatch(r"P[0-9]{6}", product_id) or not product_dir.is_dir() or not product_is_owned(product_dir):
        raise HTTPException(status_code=404, detail="Product not found")
    ensure_workbench_product_mutable(product_dir)
    payload = await request.json()
    try:
        # The category picker identifies a leaf by category/type.  Its rules
        # snapshot is derived read-only metadata, not an operator input.  Load
        # it here when an older or lightweight client did not send one.
        if not isinstance(payload.get("rules_snapshot"), dict):
            category_id = int(payload.get("category_id"))
            type_id = int(payload.get("type_id"))
            shop_id = str(payload.get("shop_id") or "zhonglian1")
            payload = {
                **payload,
                "rules_snapshot": prepare_rules(
                    ROOT,
                    category_id,
                    type_id,
                    shop_id,
                    allow_fetch=True,
                ),
            }
        selection = build_selection(ROOT, {"ozon_category_selection": payload}, preferences_root=PRODUCTS_DIR.parent)
        selection_errors = validate_json(selection, "category-selection.schema.json")
        if selection_errors:
            raise ValueError("类目选择数据无效：" + "；".join(selection_errors))
        return replace_collected_category(product_dir, selection)
    except (ValueError, TypeError, KeyError) as exc:
        raise HTTPException(status_code=409, detail={"message": str(exc)}) from exc


@app.get("/api/collector/products/{product_id}/status")
def get_product_status(product_id: str) -> Dict[str, Any]:
    product_dir = PRODUCTS_DIR / product_id
    status_path = product_dir / "status.json"
    if not status_path.is_file() or not product_is_owned(product_dir):
        raise HTTPException(status_code=404, detail="Product not found")
    return json.loads(status_path.read_text(encoding="utf-8"))


@app.get("/api/collector/duplicates")
def get_duplicate(source_url: str) -> Dict[str, Any]:
    duplicate_of = find_existing_source_urls().get(source_url)
    return {
        "exists": duplicate_of is not None,
        "product_id": duplicate_of,
        "source_url": source_url
    }


# ---------------------------------------------------------------------------
# AI product production workbench
# ---------------------------------------------------------------------------

WORKBENCH_EDITABLE_FIELDS = {
    "title_ru", "short_title", "description_ru", "bullets_ru", "tags",
    "attributes", "sku_overrides", "image_order", "selected_shop", "selected_store_ids",
    "auto_advance", "review_mode", "review_depth", "notes", "image_prompts",
}
WORKBENCH_PRODUCT_GLOB = "P[0-9][0-9][0-9][0-9][0-9][0-9]"
SYSTEM_MODEL_ATTRIBUTE_NAMES = (
    "Название модели (для объединения в одну карточку)",
    "Название модели для шаблона наименования",
    "Название модели",
    "Модель",
)
SYSTEM_MODEL_NAME_STRATEGY = "stable_random_numeric_v1"
WORKBENCH_TAG_PATTERN = re.compile(r"^#[А-Яа-яЁё]+$")
OZON_REFERENCE_TASK_ACTIVE_STATES = {
    "queued",
    "captured",
    "processing",
    "waiting_adapter",
    "waiting_ai_design",
    "processing_ai_design",
}

OZON_REFERENCE_IMAGE_LIMIT = 16
SKU_FACT_FIELD_UNITS = {
    "color": "text",
    "capacity_ml": "ml",
    "specification_text": "text",
    "product_weight_g": "g",
    "product_length_mm": "mm",
    "product_width_mm": "mm",
    "product_height_mm": "mm",
    "package_weight_g": "g",
    "package_length_mm": "mm",
    "package_width_mm": "mm",
    "package_height_mm": "mm",
    "quantity_pcs": "pcs",
}
SKU_NUMERIC_FACT_FIELDS = {
    key for key, unit in SKU_FACT_FIELD_UNITS.items()
    if unit in {"ml", "g", "mm", "pcs"}
}


def normalize_workbench_tag(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    letters = "".join(re.findall(r"[А-Яа-яЁё]+", raw))
    if not letters:
        return ""
    tag = f"#{letters.casefold()}"
    if len(tag) > 30:
        tag = tag[:30]
    return tag if WORKBENCH_TAG_PATTERN.fullmatch(tag) else ""


def ozon_reference_tasks_path() -> Path:
    return ROOT / "runtime" / OZON_REFERENCE_TASKS_FILENAME


def ozon_reference_task_dir(task_id: str) -> Path:
    safe_id = re.sub(r"[^A-Za-z0-9._-]", "_", str(task_id or "unknown"))
    return ROOT / "runtime" / "ozon-reference-tasks" / safe_id


def normalize_ozon_reference_url(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("Ozon链接不能为空")
    parsed = urllib.parse.urlparse(raw if re.match(r"^https?://", raw, flags=re.IGNORECASE) else f"https://{raw}")
    host = parsed.netloc.casefold()
    if host.startswith("www."):
        host = host[4:]
    if host != "ozon.ru" and not host.endswith(".ozon.ru"):
        raise ValueError(f"只支持 Ozon 商品卡链接：{raw}")
    path = re.sub(r"/{2,}", "/", parsed.path or "/")
    if len(path.strip("/")) < 2:
        raise ValueError(f"Ozon商品卡路径无效：{raw}")
    return urllib.parse.urlunparse(("https", host, path.rstrip("/") or "/", "", "", ""))


def parse_ozon_reference_urls(text: Any) -> List[str]:
    if isinstance(text, list):
        raw_values = [str(item) for item in text]
    else:
        raw_values = re.split(r"[\s,，；;]+", str(text or ""))
    urls: List[str] = []
    errors: List[str] = []
    for raw in raw_values:
        raw = raw.strip()
        if not raw:
            continue
        try:
            urls.append(normalize_ozon_reference_url(raw))
        except ValueError as exc:
            errors.append(str(exc))
    if errors:
        raise HTTPException(status_code=422, detail="；".join(errors[:5]))
    return list(dict.fromkeys(urls))


def parse_fitkun_reference_images(payload: Dict[str, Any], source_url: str = "") -> List[Dict[str, Any]]:
    raw_images = (
        payload.get("fitkun_images")
        or payload.get("reference_images")
        or payload.get("imported_images")
        or []
    )
    if isinstance(raw_images, str):
        raw_images = [
            {"url": value.strip()}
            for value in raw_images.splitlines()
            if value.strip()
        ]
    if not isinstance(raw_images, list):
        raise HTTPException(status_code=422, detail="FITKUN图片格式错误")
    images: List[Dict[str, Any]] = []
    for index, raw in enumerate(raw_images[:24], start=1):
        item = raw if isinstance(raw, dict) else {"url": raw}
        data_url = str(item.get("data_url") or "").strip()
        raw_url = item.get("url") or item.get("src") or item.get("current_src") or ""
        url = ""
        if raw_url:
            normalized = normalize_url(raw_url, source_url or "https://www.ozon.ru/")
            if normalized:
                url = normalize_ozon_reference_image_url(normalized)
        if not url:
            digest = hashlib.sha256((data_url or f"fitkun-{index}").encode("utf-8")).hexdigest()[:16]
            url = f"fitkun-inline://image-{index:03d}-{digest}.jpg"
        if not data_url and not url:
            continue
        images.append({
            "url": url,
            "data_url": data_url,
            "content_type": item.get("content_type") or item.get("type") or "image/jpeg",
            "byte_size": item.get("byte_size") or item.get("size") or 0,
            "source": "fitkun_image_extractor",
            "source_order": index - 1,
            "name": str(item.get("name") or item.get("filename") or f"fitkun-{index:03d}"),
        })
    return images


def parse_ozon_reference_items(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    raw_items = payload.get("items") if isinstance(payload.get("items"), list) else []
    parsed_items: List[Dict[str, Any]] = []
    errors: List[str] = []
    if raw_items:
        for index, item in enumerate(raw_items, start=1):
            if not isinstance(item, dict):
                errors.append(f"第{index}个链接内容格式错误")
                continue
            raw_url = item.get("url") or item.get("source_url") or item.get("link")
            try:
                normalized_url = normalize_ozon_reference_url(raw_url)
                parsed_items.append({
                    "source_url": normalized_url,
                    "manual_inputs": parse_ozon_reference_manual_inputs(item),
                    "fitkun_images": parse_fitkun_reference_images(item, normalized_url),
                })
            except (ValueError, HTTPException) as exc:
                detail = exc.detail if isinstance(exc, HTTPException) else str(exc)
                errors.append(str(detail))
    else:
        manual_inputs = parse_ozon_reference_manual_inputs(payload)
        parsed_items = [
            {
                "source_url": url,
                "manual_inputs": manual_inputs,
                "fitkun_images": parse_fitkun_reference_images(payload, url),
            }
            for url in parse_ozon_reference_urls(payload.get("urls") or payload.get("text") or "")
        ]
    if errors:
        raise HTTPException(status_code=422, detail="；".join(errors[:5]))
    unique: Dict[str, Dict[str, Any]] = {}
    for item in parsed_items:
        unique.setdefault(str(item["source_url"]), item)
    return list(unique.values())


def _positive_optional_number(value: Any, field_label: str) -> Optional[float]:
    if value in {None, ""}:
        return None
    try:
        number = float(str(value).strip().replace(",", "."))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=f"{field_label} 必须是数字") from exc
    if not math.isfinite(number) or number <= 0:
        raise HTTPException(status_code=422, detail=f"{field_label} 必须大于0")
    return round(number, 3)


def parse_ozon_reference_manual_inputs(payload: Dict[str, Any]) -> Dict[str, Any]:
    raw = payload.get("manual_inputs") if isinstance(payload.get("manual_inputs"), dict) else {}
    length_mm = _positive_optional_number(raw.get("length_mm") or payload.get("length_mm"), "长度")
    width_mm = _positive_optional_number(raw.get("width_mm") or payload.get("width_mm"), "宽度")
    height_mm = _positive_optional_number(raw.get("height_mm") or payload.get("height_mm"), "高度")
    weight_g = _positive_optional_number(raw.get("weight_g") or payload.get("weight_g"), "重量")
    selling_price_cny = _positive_optional_number(
        raw.get("selling_price_cny") or payload.get("selling_price_cny") or payload.get("price_cny"),
        "售价",
    )
    dimensions = {
        key: value for key, value in {
            "length_mm": length_mm,
            "width_mm": width_mm,
            "height_mm": height_mm,
        }.items()
        if value is not None
    }
    manual_inputs = {
        "schema_version": "1.0.0",
        "source_kind": "operator_input",
        "unit_defaults": {
            "length": "mm",
            "width": "mm",
            "height": "mm",
            "weight": "g",
            "selling_price": "CNY",
        },
        "package_dimensions_mm": dimensions,
        "package_weight_g": weight_g,
        "selling_price_cny": selling_price_cny,
    }
    raw_category = raw.get("ozon_category_selection") or raw.get("category_selection") or payload.get("ozon_category_selection") or payload.get("category_selection")
    if isinstance(raw_category, dict):
        try:
            category_selection = build_selection(
                ROOT,
                {"ozon_category_selection": raw_category},
                preferences_root=PRODUCTS_DIR.parent,
            )
        except (ValueError, TypeError, KeyError) as exc:
            raise HTTPException(status_code=422, detail=f"类目选择无效：{exc}") from exc
        selection_errors = validate_json(category_selection, "category-selection.schema.json")
        if selection_errors:
            raise HTTPException(status_code=422, detail={"message": "类目选择数据无效", "errors": selection_errors})
        manual_inputs["ozon_category_selection"] = category_selection
    manual_inputs["has_values"] = bool(dimensions or weight_g is not None or selling_price_cny is not None)
    return manual_inputs


def load_ozon_reference_tasks() -> Dict[str, Any]:
    return load_optional_json(ozon_reference_tasks_path(), {
        "schema_version": "1.0.0",
        "items": [],
        "updated_at": None,
    })


def save_ozon_reference_tasks(data: Dict[str, Any]) -> None:
    data["schema_version"] = "1.0.0"
    data["updated_at"] = now_iso()
    atomic_write_json(ozon_reference_tasks_path(), data)


def ozon_reference_task_id(url: str, created_at: str) -> str:
    digest = hashlib.sha256(f"{url}|{created_at}".encode("utf-8")).hexdigest()[:12].upper()
    return f"OZT-{datetime.now(timezone.utc).strftime('%Y%m%d')}-{digest}"


def public_ozon_reference_task(item: Dict[str, Any]) -> Dict[str, Any]:
    capture = item.get("capture") if isinstance(item.get("capture"), dict) else {}
    manual_inputs = item.get("manual_inputs") if isinstance(item.get("manual_inputs"), dict) else {}
    fitkun_images = item.get("fitkun_images") if isinstance(item.get("fitkun_images"), list) else []
    return {
        "task_id": str(item.get("task_id") or ""),
        "source_url": str(item.get("source_url") or ""),
        "status": str(item.get("status") or "queued"),
        "display_status": str(item.get("display_status") or "待处理"),
        "target_store_ids": list(item.get("target_store_ids") or []),
        "created_at": item.get("created_at"),
        "updated_at": item.get("updated_at"),
        "mode": str(item.get("mode") or "create_without_inventory"),
        "inventory_submission_enabled": False,
        "message": str(item.get("message") or "已加入 Ozon 参考上架队列，等待自动处理。"),
        "reference_title": str(capture.get("title") or item.get("reference_title") or ""),
        "captured_image_count": int(item.get("captured_image_count") or 0),
        "fitkun_image_count": len(fitkun_images),
        "capture_artifact_path": str(item.get("capture_artifact_path") or ""),
        "brief_artifact_path": str(item.get("brief_artifact_path") or ""),
        "generation_artifact_path": str(item.get("generation_artifact_path") or ""),
        "designer_input_artifact_path": str(item.get("designer_input_artifact_path") or ""),
        "ai_design_request_artifact_path": str(item.get("ai_design_request_artifact_path") or ""),
        "listing_draft_artifact_path": str(item.get("listing_draft_artifact_path") or ""),
        "created_product_id": str(item.get("created_product_id") or ""),
        "created_product_path": str(item.get("created_product_path") or ""),
        "missing_fields": list(item.get("missing_fields") or []),
        "manual_inputs": manual_inputs,
        "write_api_calls": 0,
        "inventory_api_calls": 0,
    }


def download_public_ozon_page(url: str, timeout: int = 25) -> Tuple[str, Optional[str]]:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.5",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read(4 * 1024 * 1024)
        content_type = response.headers.get("content-type")
    charset = "utf-8"
    match = re.search(r"charset=([A-Za-z0-9._-]+)", str(content_type or ""), flags=re.IGNORECASE)
    if match:
        charset = match.group(1)
    return raw.decode(charset, errors="replace"), content_type


def _first_html_attr(html_text: str, pattern: str) -> str:
    match = re.search(pattern, html_text, flags=re.IGNORECASE | re.DOTALL)
    return html.unescape(match.group(1)).strip() if match else ""


def _clean_reference_text(value: Any, limit: int = 500) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit].strip()


def extract_json_ld_objects(html_text: str) -> List[Dict[str, Any]]:
    objects: List[Dict[str, Any]] = []
    for match in re.finditer(
        r"<script[^>]+type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>",
        html_text,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        raw = html.unescape(match.group(1)).strip()
        if not raw:
            continue
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            continue
        stack = parsed if isinstance(parsed, list) else [parsed]
        while stack:
            item = stack.pop(0)
            if isinstance(item, dict):
                objects.append(item)
                graph = item.get("@graph")
                if isinstance(graph, list):
                    stack.extend(graph)
            elif isinstance(item, list):
                stack.extend(item)
    return objects


def _json_ld_type(value: Any) -> str:
    if isinstance(value, list):
        return " ".join(str(item) for item in value).casefold()
    return str(value or "").casefold()


def extract_ozon_reference_from_html(source_url: str, html_text: str) -> Dict[str, Any]:
    title = _clean_reference_text(_first_html_attr(
        html_text,
        r"<meta[^>]+(?:property|name)=[\"']og:title[\"'][^>]+content=[\"']([^\"']+)[\"']",
    ))
    if not title:
        title = _clean_reference_text(_first_html_attr(html_text, r"<title[^>]*>(.*?)</title>"))
    description = _clean_reference_text(_first_html_attr(
        html_text,
        r"<meta[^>]+(?:property|name)=[\"'](?:og:description|description)[\"'][^>]+content=[\"']([^\"']+)[\"']",
    ))
    image_urls: List[str] = []
    for pattern in (
        r"<meta[^>]+property=[\"']og:image(?::secure_url)?[\"'][^>]+content=[\"']([^\"']+)[\"']",
        r"<img[^>]+(?:src|data-src|data-lazy-src)=[\"']([^\"']+)[\"']",
    ):
        for value in re.findall(pattern, html_text, flags=re.IGNORECASE | re.DOTALL):
            normalized = normalize_url(html.unescape(value), source_url)
            normalized = normalize_ozon_reference_image_url(normalized or "")
            if normalized and not is_disallowed_ozon_reference_image_url(normalized):
                image_urls.append(normalized)
    category_path: List[str] = []
    price = ""
    currency = ""
    for item in extract_json_ld_objects(html_text):
        item_type = _json_ld_type(item.get("@type"))
        if "product" in item_type:
            title = title or _clean_reference_text(item.get("name"))
            description = description or _clean_reference_text(item.get("description"))
            images = item.get("image")
            if isinstance(images, str):
                images = [images]
            for image_url in images or []:
                normalized = normalize_url(image_url, source_url)
                normalized = normalize_ozon_reference_image_url(normalized or "")
                if normalized and not is_disallowed_ozon_reference_image_url(normalized):
                    image_urls.append(normalized)
            offers = item.get("offers") if isinstance(item.get("offers"), dict) else {}
            price = str(offers.get("price") or price or "").strip()
            currency = str(offers.get("priceCurrency") or currency or "").strip()
        if "breadcrumblist" in item_type:
            elements = item.get("itemListElement") or []
            for element in elements:
                if isinstance(element, dict):
                    nested_item = element.get("item") if isinstance(element.get("item"), dict) else {}
                    name = element.get("name") or nested_item.get("name") or ""
                    clean = _clean_reference_text(name, limit=80)
                    if clean:
                        category_path.append(clean)
    return {
        "source_url": source_url,
        "title": title,
        "description": description,
        "category_path": list(dict.fromkeys(category_path)),
        "price": price,
        "currency": currency,
        "image_urls": list(dict.fromkeys(image_urls))[:OZON_REFERENCE_IMAGE_LIMIT],
    }


def extract_ozon_reference_from_browser_snapshot(source_url: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    image_urls: List[str] = []
    inline_images: Dict[str, Dict[str, Any]] = {}
    for value in payload.get("image_urls") or []:
        normalized = normalize_url(value, source_url)
        normalized = normalize_ozon_reference_image_url(normalized or "")
        if normalized and not is_disallowed_ozon_reference_image_url(normalized):
            image_urls.append(normalized)
    for item in payload.get("images") or []:
        if not isinstance(item, dict):
            continue
        normalized = normalize_url(item.get("url") or item.get("src") or item.get("current_src"), source_url)
        normalized = normalize_ozon_reference_image_url(normalized or "")
        if normalized and not is_disallowed_ozon_reference_image_url(normalized):
            image_urls.append(normalized)
            if item.get("data_url"):
                inline_images[normalized] = {
                    "data_url": item.get("data_url"),
                    "content_type": item.get("content_type") or "image/jpeg",
                    "byte_size": item.get("byte_size") or 0,
                }
    category_path = [
        _clean_reference_text(item, limit=80)
        for item in payload.get("category_path") or []
        if _clean_reference_text(item, limit=80)
    ]
    return {
        "source_url": source_url,
        "title": _clean_reference_text(payload.get("title") or payload.get("title_ru"), limit=500),
        "description": _clean_reference_text(payload.get("description") or payload.get("description_ru"), limit=1200),
        "category_path": list(dict.fromkeys(category_path)),
        "price": _clean_reference_text(payload.get("price") or "", limit=80),
        "currency": _clean_reference_text(payload.get("currency") or "RUB", limit=16),
        "page_text": _clean_reference_text(payload.get("page_text") or "", limit=3000),
        "image_urls": list(dict.fromkeys(image_urls))[:OZON_REFERENCE_IMAGE_LIMIT],
        "inline_images": inline_images,
    }


def compact_ozon_reference_payload_for_storage(payload: Dict[str, Any]) -> Dict[str, Any]:
    compact = dict(payload)
    compact_images = []
    for item in compact.get("images") or []:
        if not isinstance(item, dict):
            continue
        compact_item = {key: value for key, value in item.items() if key != "data_url"}
        compact_item["has_inline_image_data"] = bool(item.get("data_url"))
        compact_images.append(compact_item)
    compact["images"] = compact_images
    return compact


def materialize_ozon_reference_capture(
    task: Dict[str, Any],
    reference: Dict[str, Any],
    content_type: str = "unknown",
    source_text: str = "",
    source_filename: str = "source.html",
    capture_method: str = "server_http",
) -> Dict[str, Any]:
    task_id = str(task.get("task_id") or "")
    source_url = normalize_ozon_reference_url(task.get("source_url") or reference.get("source_url"))
    task_dir = ozon_reference_task_dir(task_id)
    image_dir = task_dir / "images"
    captured_at = now_iso()
    if not reference.get("title") and not reference.get("image_urls"):
        raise ValueError("公开页面没有解析到商品标题或图片，可能被Ozon反爬拦截或链接不是商品卡")
    task_dir.mkdir(parents=True, exist_ok=True)
    if source_text:
        (task_dir / source_filename).write_text(source_text, encoding="utf-8")
    warnings: List[str] = []
    url_cache: Dict[str, Dict[str, Any]] = {}
    hash_cache: Dict[str, Dict[str, Any]] = {}
    inline_images = reference.get("inline_images") if isinstance(reference.get("inline_images"), dict) else {}
    image_inputs = []
    for index, url in enumerate(reference.get("image_urls") or []):
        item = {"url": url, "source": "ozon_public_card", "source_order": index}
        inline = inline_images.get(url) if isinstance(inline_images.get(url), dict) else {}
        if inline.get("data_url"):
            item.update({
                "data_url": inline.get("data_url"),
                "content_type": inline.get("content_type") or "image/jpeg",
                "byte_size": inline.get("byte_size") or 0,
            })
        elif capture_method in {"browser_plugin", "fitkun_import"}:
            source_label = "FITKUN" if capture_method == "fitkun_import" else "浏览器插件"
            warnings.append(f"Ozon参考图未由{source_label}带回图片数据，已跳过后端直连下载：{url}")
            continue
        image_inputs.append(item)
    downloaded_images = download_image_group(
        image_inputs,
        image_dir,
        "reference",
        url_cache,
        hash_cache,
        warnings,
        allowed_host_suffixes=ALLOWED_OZON_REFERENCE_IMAGE_HOST_SUFFIXES,
    )
    capture = {
        "schema_version": "1.0.0",
        "task_id": task_id,
        "source_kind": "ozon_reference_listing",
        "source_url": source_url,
        "captured_at": captured_at,
        "content_type": content_type or "unknown",
        "capture_method": capture_method,
        "manual_inputs": task.get("manual_inputs") if isinstance(task.get("manual_inputs"), dict) else {},
        "reference": {
            "title": reference.get("title") or "",
            "description": reference.get("description") or "",
            "category_path": reference.get("category_path") or [],
            "price": reference.get("price") or "",
            "currency": reference.get("currency") or "",
            "page_text": reference.get("page_text") or "",
        },
        "image_urls": reference.get("image_urls") or [],
        "images": downloaded_images,
        "warnings": warnings,
        "fact_policy": {
            "use_as_competitor_reference_only": True,
            "do_not_copy_store_name_brand_watermark_or_exact_text": True,
            "inventory_submission_enabled": False,
            "ozon_api_write_calls": 0,
            "inventory_api_calls": 0,
        },
        "next_action": "等待接入 Ozon 参考商品卡生成适配器",
    }
    atomic_write_json(task_dir / "capture.json", capture)
    brief = build_ozon_reference_brief(capture)
    atomic_write_json(task_dir / "brief.json", brief)
    generation_input = build_ozon_reference_generation_input(brief)
    atomic_write_json(task_dir / "generation-input.json", generation_input)
    designer_input = build_ozon_reference_designer_input(generation_input)
    designer_errors = validate_json(designer_input, "ozon-reference-designer-input.schema.json")
    if designer_errors:
        raise ValueError("Ozon参考商品卡生成输入校验失败：" + "；".join(designer_errors[:5]))
    atomic_write_json(task_dir / "designer-input.json", designer_input)
    ai_design_request = build_ozon_reference_ai_design_request(designer_input, task_dir)
    ai_request_errors = validate_json(ai_design_request, "ozon-reference-ai-design-request.schema.json")
    if ai_request_errors:
        raise ValueError("Ozon参考AI设计请求校验失败：" + "；".join(ai_request_errors[:5]))
    atomic_write_json(task_dir / "ai-design-request.json", ai_design_request)
    captured_images = [
        item for item in downloaded_images
        if item.get("download_status") in {"downloaded", "skipped_duplicate_content", "skipped_duplicate_url"}
        and item.get("local_path") not in {"", "unknown", None}
    ]
    ready = bool(designer_input.get("ready_for_ai_design"))
    missing_fields = generation_input.get("missing_fields") or []
    return {
        **task,
        "source_url": source_url,
        "status": "waiting_ai_design" if ready else "captured",
        "display_status": "等待AI生成商品卡" if ready else "已抓取，缺参数",
        "pipeline_status": "ozon_reference_ai_design_request_ready" if ready else "ozon_reference_captured_missing_inputs",
        "updated_at": captured_at,
        "capture": capture["reference"],
        "reference_title": capture["reference"]["title"],
        "captured_image_count": len(captured_images),
        "capture_artifact_path": str((task_dir / "capture.json").relative_to(ROOT)),
        "brief_artifact_path": str((task_dir / "brief.json").relative_to(ROOT)),
        "generation_artifact_path": str((task_dir / "generation-input.json").relative_to(ROOT)),
        "designer_input_artifact_path": str((task_dir / "designer-input.json").relative_to(ROOT)),
        "ai_design_request_artifact_path": str((task_dir / "ai-design-request.json").relative_to(ROOT)),
        "missing_fields": missing_fields,
        "message": (
            f"已通过浏览器插件采集 Ozon 商品卡：图片 {len(captured_images)} 张，已准备AI商品卡生成请求。"
            if ready else
            f"已通过浏览器插件采集 Ozon 商品卡：图片 {len(captured_images)} 张；还缺：{'、'.join(str(item) for item in missing_fields)}。"
        ),
        "write_api_calls": 0,
        "inventory_api_calls": 0,
    }


def build_ozon_reference_brief(capture: Dict[str, Any]) -> Dict[str, Any]:
    reference = capture.get("reference") if isinstance(capture.get("reference"), dict) else {}
    manual_inputs = capture.get("manual_inputs") if isinstance(capture.get("manual_inputs"), dict) else {}
    usable_images = [
        item for item in capture.get("images") or []
        if isinstance(item, dict)
        and item.get("local_path") not in {"", "unknown", None}
        and item.get("download_status") in {"downloaded", "skipped_duplicate_content", "skipped_duplicate_url"}
    ]
    return {
        "schema_version": "1.0.0",
        "task_id": capture.get("task_id"),
        "source_kind": "ozon_reference_listing",
        "source_url": capture.get("source_url"),
        "generated_at": now_iso(),
        "mode": "create_without_inventory",
        "inventory_submission_enabled": False,
        "seo_reference": {
            "title_ru": reference.get("title") or "",
            "description_ru": reference.get("description") or "",
            "category_path_ru": reference.get("category_path") or [],
            "price_rub_reference": reference.get("price") or "",
            "currency": reference.get("currency") or "",
            "usage": "用于SEO方向、买家表达和类目理解；必须生成我方文案，不得逐字复制。",
        },
        "operator_inputs": {
            "unit_defaults": manual_inputs.get("unit_defaults") or {
                "length": "mm",
                "width": "mm",
                "height": "mm",
                "weight": "g",
                "selling_price": "CNY",
            },
            "package_dimensions_mm": manual_inputs.get("package_dimensions_mm") or {},
            "package_weight_g": manual_inputs.get("package_weight_g"),
            "selling_price_cny": manual_inputs.get("selling_price_cny"),
            "ozon_category_selection": manual_inputs.get("ozon_category_selection") or {},
            "source": "Ozon参考上架表单手动填写",
            "usage": "后续商品卡生成和定价适配优先使用这些手填数据；不改写公开Ozon参考页。",
        },
        "image_reference": {
            "image_count": len(usable_images),
            "images": [
                {
                    "local_path": item.get("local_path"),
                    "original_url": item.get("original_url"),
                    "role": "ozon_competitor_real_photo_reference",
                    "usage": "只参考相机实拍感、构图、光线和背景真实度；不得复制水印、店铺名、平台标识或竞品文字。",
                }
                for item in usable_images
            ],
        },
        "fact_policy": {
            "competitor_card_is_reference_not_ownership_proof": True,
            "forbidden_to_copy": [
                "竞品店铺名",
                "竞品水印",
                "竞品品牌",
                "竞品型号",
                "竞品认证",
                "竞品原文标题和简介",
                "未在图片或文字中出现的具体配件",
            ],
            "allowed_to_reference": [
                "商品类型",
                "可见外观",
                "可见颜色",
                "可见使用场景",
                "Ozon俄语买家表达方向",
                "实拍图片的镜头和光线感觉",
            ],
        },
        "missing_production_inputs": [
            {
                "field": "purchase_cost_cny",
                "reason": "Ozon公开商品卡只有竞品售价，不是采购价；后续定价需要独立策略或用户成本来源。",
                "blocks_direct_pricing": True,
            },
            {
                "field": "package_weight_dimensions",
                "reason": "Ozon公开商品卡通常不暴露可靠包装重量尺寸；不能凭空提交。",
                "blocks_shipping_price": True,
            },
            {
                "field": "sku_offer_mapping",
                "reason": "公开页面可能不完整暴露所有SKU和SKU图片；需要后续适配器解析或降级为单商品。",
                "blocks_variant_creation": False,
            },
        ],
        "next_action": "交给 Ozon 参考商品卡生成适配器，生成我方商品资料和实拍风图片计划。",
        "write_api_calls": 0,
        "inventory_api_calls": 0,
    }


def ozon_reference_manual_missing_fields(manual_inputs: Dict[str, Any]) -> List[str]:
    dimensions = manual_inputs.get("package_dimensions_mm") if isinstance(manual_inputs.get("package_dimensions_mm"), dict) else {}
    category_selection = manual_inputs.get("ozon_category_selection") if isinstance(manual_inputs.get("ozon_category_selection"), dict) else {}
    missing = []
    for key, label in (
        ("length_mm", "长MM"),
        ("width_mm", "宽MM"),
        ("height_mm", "高MM"),
    ):
        if not isinstance(dimensions.get(key), (int, float)) or float(dimensions.get(key) or 0) <= 0:
            missing.append(label)
    if not isinstance(manual_inputs.get("package_weight_g"), (int, float)) or float(manual_inputs.get("package_weight_g") or 0) <= 0:
        missing.append("重量G")
    if not isinstance(manual_inputs.get("selling_price_cny"), (int, float)) or float(manual_inputs.get("selling_price_cny") or 0) <= 0:
        missing.append("售价CNY")
    if not isinstance(category_selection.get("category_id"), int) or not isinstance(category_selection.get("type_id"), int):
        missing.append("最终Ozon类目")
    return missing


def require_complete_ozon_reference_manual_inputs(reference_items: List[Dict[str, Any]]) -> None:
    missing_by_item = []
    for index, item in enumerate(reference_items, start=1):
        missing = ozon_reference_manual_missing_fields(item.get("manual_inputs") or {})
        if missing:
            missing_by_item.append({
                "index": index,
                "source_url": item.get("source_url") or "unknown",
                "missing_fields": missing,
            })
    if missing_by_item:
        first = missing_by_item[0]
        raise HTTPException(
            status_code=422,
            detail={
                "message": (
                    "开始 Ozon 参考自动生产前必须先补齐每个链接的长、宽、高、重量、售价和最终 Ozon 类目；"
                    f"第{first['index']}个链接还缺：{'、'.join(first['missing_fields'])}"
                ),
                "missing_items": missing_by_item,
                "write_api_calls": 0,
                "inventory_api_calls": 0,
            },
        )


def build_ozon_reference_generation_input(brief: Dict[str, Any]) -> Dict[str, Any]:
    operator_inputs = brief.get("operator_inputs") if isinstance(brief.get("operator_inputs"), dict) else {}
    image_reference = brief.get("image_reference") if isinstance(brief.get("image_reference"), dict) else {}
    seo_reference = brief.get("seo_reference") if isinstance(brief.get("seo_reference"), dict) else {}
    missing_fields = ozon_reference_manual_missing_fields({
        "package_dimensions_mm": operator_inputs.get("package_dimensions_mm") or {},
        "package_weight_g": operator_inputs.get("package_weight_g"),
        "selling_price_cny": operator_inputs.get("selling_price_cny"),
        "ozon_category_selection": operator_inputs.get("ozon_category_selection") or {},
    })
    if not seo_reference.get("title_ru"):
        missing_fields.append("Ozon参考标题")
    if int(image_reference.get("image_count") or 0) <= 0:
        missing_fields.append("Ozon参考图片")
    ready = not missing_fields
    return {
        "schema_version": "1.0.0",
        "task_id": brief.get("task_id"),
        "source_kind": "ozon_reference_listing",
        "source_url": brief.get("source_url"),
        "generated_at": now_iso(),
        "ready_for_ecommerce_design": ready,
        "missing_fields": missing_fields,
        "mode": "create_without_inventory",
        "inventory_submission_enabled": False,
        "inputs": {
            "seo_reference": seo_reference,
            "operator_inputs": operator_inputs,
            "image_reference": image_reference,
        },
        "generation_contract": {
            "create_own_ozon_listing_copy": True,
            "use_reference_text_for_seo_direction_only": True,
            "reverse_reference_images_for_real_photo_prompt": True,
            "do_not_copy_competitor_brand_watermark_store_name_or_exact_text": True,
            "do_not_submit_inventory": True,
            "next_skill_order": [
                "ozon-image-prompt-reverse",
                "ozon-ecommerce-designer",
                "image-planner",
                "image-generator",
                "ozon-uploader-without-inventory",
            ],
        },
        "write_api_calls": 0,
        "inventory_api_calls": 0,
    }


def build_ozon_reference_designer_input(generation_input: Dict[str, Any]) -> Dict[str, Any]:
    inputs = generation_input.get("inputs") if isinstance(generation_input.get("inputs"), dict) else {}
    seo_reference = inputs.get("seo_reference") if isinstance(inputs.get("seo_reference"), dict) else {}
    operator_inputs = inputs.get("operator_inputs") if isinstance(inputs.get("operator_inputs"), dict) else {}
    category_selection = operator_inputs.get("ozon_category_selection") if isinstance(operator_inputs.get("ozon_category_selection"), dict) else {}
    image_reference = inputs.get("image_reference") if isinstance(inputs.get("image_reference"), dict) else {}
    ready = bool(generation_input.get("ready_for_ecommerce_design")) and not generation_input.get("missing_fields")
    return {
        "schema_version": "1.0.0",
        "task_id": generation_input.get("task_id"),
        "source_kind": "ozon_reference_listing",
        "source_url": generation_input.get("source_url"),
        "generated_at": now_iso(),
        "ready_for_ai_design": ready,
        "mode": "create_without_inventory",
        "inventory_submission_enabled": False,
        "operator_inputs": {
            "package_dimensions_mm": operator_inputs.get("package_dimensions_mm") or {},
            "package_weight_g": operator_inputs.get("package_weight_g"),
            "selling_price_cny": operator_inputs.get("selling_price_cny"),
            "ozon_category_selection": category_selection,
            "unit_defaults": operator_inputs.get("unit_defaults") or {
                "length": "mm",
                "width": "mm",
                "height": "mm",
                "weight": "g",
                "selling_price": "CNY",
            },
            "source": "Ozon参考上架表单逐链接手动填写",
        },
        "seo_reference": {
            "title_ru": seo_reference.get("title_ru") or "",
            "description_ru": seo_reference.get("description_ru") or "",
            "category_path_ru": seo_reference.get("category_path_ru") or [],
            "competitor_price_rub": seo_reference.get("price_rub_reference") or "",
            "usage": [
                "分析Ozon俄语买家搜索表达",
                "提炼商品类型、用途和可见卖点方向",
                "生成我方SEO标题、简介、卖点和标签",
                "不得逐字复制竞品标题、简介或标签",
            ],
        },
        "visual_reference": {
            "image_count": image_reference.get("image_count") or 0,
            "images": image_reference.get("images") or [],
            "usage": [
                "反推相机实拍感、镜头距离、光线、浅景深、真实背景和卖家图粗糙度",
                "生成我方实拍风商品图提示词",
                "不得搬运竞品图片作为最终图",
                "不得复制水印、店铺名、平台Logo或图中文字",
            ],
        },
        "copy_contract": {
            "language": "ru",
            "title": "自然俄语SEO标题，商品词+用途+核心卖点+关键规格，不要中文语序直译",
            "description": "多段商品描述，不能只写一句；覆盖价值、场景、优势、使用提示和购买提醒",
            "hashtags": "俄文#标签，每个以#开头，只允许俄文字母，不要品牌、数字、下划线、拉丁字母",
            "must_be_own_listing": True,
        },
        "image_contract": {
            "style": "real camera / phone seller product photo, not obvious AI poster, not over-polished studio render",
            "reference_mode": "visual_feel_only",
            "main_image": "商品主体接近参考图的真实拍摄感，可换干净真实背景，不复制水印文字",
            "detail_images": "围绕材质、结构、尺寸、使用场景、模特比例和购买提醒生成多角度实拍风图",
        },
        "attribute_contract": {
            "final_ozon_category": {
                "category_id": category_selection.get("category_id"),
                "type_id": category_selection.get("type_id"),
                "category_name_zh": category_selection.get("category_name_zh"),
                "category_path_zh": category_selection.get("category_path_zh") or [],
                "category_name_ru": category_selection.get("category_name_ru"),
                "category_path_ru": category_selection.get("category_path") or [],
                "rules_snapshot_hash": category_selection.get("rules_snapshot_hash"),
                "source": "operator_final_choice",
            },
            "category_from_ozon_reference_is_hint_only": True,
            "operator_final_category_is_upload_metadata": True,
            "operator_dimensions_weight_price_are_preferred": True,
            "unknown_high_risk_fields_must_remain_unknown": True,
            "do_not_invent_certification_warranty_material_or_accessories": True,
        },
        "forbidden": [
            "复制竞品店铺名",
            "复制竞品水印",
            "复制竞品品牌",
            "复制竞品型号",
            "复制竞品认证",
            "逐字复制竞品标题或简介",
            "把竞品图直接作为最终商品图",
            "提交库存",
            "调用库存、仓库、激活接口",
        ],
        "outputs_expected": [
            "own_listing_copy_ru",
            "seo_title_ru",
            "description_ru",
            "hashtags_ru",
            "attribute_draft",
            "visual_reference_analysis",
            "image_prompt_plan",
            "upload_payload_without_inventory",
        ],
        "write_api_calls": 0,
        "inventory_api_calls": 0,
    }


def build_ozon_reference_ai_design_request(designer_input: Dict[str, Any], task_dir: Path) -> Dict[str, Any]:
    input_ref = str((task_dir / "designer-input.json").relative_to(ROOT))
    output_ref = str((task_dir / "listing-design-draft.json").relative_to(ROOT))
    prompt = (
        "这是 JLC GLOBAL 的 Ozon参考上架任务，不是1688正式商品目录任务。"
        "只读取 input_ref 指向的 designer-input.json 以及其中列出的本任务参考图片；"
        "不得读取 products/ 下任何商品，不得读取其他 Ozon参考任务，不得读取 test-data 或旧输出。"
        "目标：基于 Ozon 公开商品卡参考，生成我方 Ozon 商品卡草稿和实拍风图片设计草稿。"
        "Ozon竞品文字只能用于SEO方向、买家表达和类目理解；必须重写为我方俄文标题、简介、卖点和标签，不能逐字复制。"
        "Ozon竞品图片只能用于反推相机实拍感、镜头、光线、浅景深、真实背景和卖家图质感；不得搬运为最终图，不得保留水印、店铺名、Logo或图中文字。"
        "手填长宽高、重量和售价来自 operator_inputs，单位固定为 mm/g/CNY，优先用于后续商品卡和定价适配。"
        "输出必须写入 expected_output_path 指向的 listing-design-draft.json。"
        "输出JSON必须包含：own_listing_copy_ru、seo_title_ru、short_title_ru、description_ru、hashtags_ru、"
        "attribute_draft、visual_reference_analysis、image_prompt_plan、risks、next_action。"
        "简介必须是多段俄文电商描述，不能只写一句；标签必须以#开头且只用俄文字母；属性草稿必须区分可填、可估算、未知高风险。"
        "图片计划必须是实拍风商品图方向，不是海报模板；产品主体和可见事实优先，文字克制。"
        "禁止提交库存、warehouse_id、stock、激活接口；禁止调用 Ozon Seller API 写入、更新、只读回查或库存接口。"
        "最终只输出 DONE ozon_reference_ai_design。"
    )
    return {
        "schema_version": "1.0.0",
        "task_id": designer_input.get("task_id"),
        "source_kind": "ozon_reference_listing",
        "source_url": designer_input.get("source_url"),
        "generated_at": now_iso(),
        "input_ref": input_ref,
        "expected_output_path": output_ref,
        "prompt": prompt,
        "must_not_call": [
            "Ozon Seller API create/update",
            "Ozon Seller API readback",
            "inventory API",
            "warehouse API",
            "activation API",
            "third-party hosted image API",
            "products/P* formal 1688 pipeline",
        ],
        "write_api_calls": 0,
        "inventory_api_calls": 0,
    }


def rebuild_ozon_reference_task_artifacts_from_capture(
    task: Dict[str, Any],
    capture: Dict[str, Any],
) -> Dict[str, Any]:
    task_id = str(task.get("task_id") or capture.get("task_id") or "")
    task_dir = ozon_reference_task_dir(task_id)
    captured_at = now_iso()
    updated_capture = dict(capture)
    updated_capture["manual_inputs"] = task.get("manual_inputs") if isinstance(task.get("manual_inputs"), dict) else {}
    updated_capture["captured_at"] = updated_capture.get("captured_at") or captured_at
    atomic_write_json(task_dir / "capture.json", updated_capture)
    brief = build_ozon_reference_brief(updated_capture)
    atomic_write_json(task_dir / "brief.json", brief)
    generation_input = build_ozon_reference_generation_input(brief)
    atomic_write_json(task_dir / "generation-input.json", generation_input)
    designer_input = build_ozon_reference_designer_input(generation_input)
    designer_errors = validate_json(designer_input, "ozon-reference-designer-input.schema.json")
    if designer_errors:
        raise ValueError("Ozon参考商品卡生成输入校验失败：" + "；".join(designer_errors[:5]))
    atomic_write_json(task_dir / "designer-input.json", designer_input)
    ai_design_request = build_ozon_reference_ai_design_request(designer_input, task_dir)
    ai_request_errors = validate_json(ai_design_request, "ozon-reference-ai-design-request.schema.json")
    if ai_request_errors:
        raise ValueError("Ozon参考AI设计请求校验失败：" + "；".join(ai_request_errors[:5]))
    atomic_write_json(task_dir / "ai-design-request.json", ai_design_request)
    captured_images = [
        item for item in updated_capture.get("images") or []
        if item.get("download_status") in {"downloaded", "skipped_duplicate_content", "skipped_duplicate_url"}
        and item.get("local_path") not in {"", "unknown", None}
    ]
    ready = bool(designer_input.get("ready_for_ai_design"))
    missing_fields = generation_input.get("missing_fields") or []
    return {
        **task,
        "source_url": updated_capture.get("source_url") or task.get("source_url"),
        "status": "waiting_ai_design" if ready else "captured",
        "display_status": "等待AI生成商品卡" if ready else "已采集，待补参数",
        "pipeline_status": "ozon_reference_ai_design_request_ready" if ready else "ozon_reference_captured_missing_inputs",
        "updated_at": captured_at,
        "capture": updated_capture.get("reference") or {},
        "reference_title": (updated_capture.get("reference") or {}).get("title") or task.get("reference_title") or "",
        "captured_image_count": len(captured_images),
        "capture_artifact_path": str((task_dir / "capture.json").relative_to(ROOT)),
        "brief_artifact_path": str((task_dir / "brief.json").relative_to(ROOT)),
        "generation_artifact_path": str((task_dir / "generation-input.json").relative_to(ROOT)),
        "designer_input_artifact_path": str((task_dir / "designer-input.json").relative_to(ROOT)),
        "ai_design_request_artifact_path": str((task_dir / "ai-design-request.json").relative_to(ROOT)),
        "missing_fields": missing_fields,
        "message": (
            f"Ozon参考页已采集，图片 {len(captured_images)} 张；参数已补齐，等待AI生成商品卡。"
            if ready else
            f"Ozon参考页已采集，图片 {len(captured_images)} 张；还缺：{'、'.join(str(item) for item in missing_fields)}。"
        ),
        "write_api_calls": 0,
        "inventory_api_calls": 0,
    }


def capture_ozon_reference_task(task: Dict[str, Any]) -> Dict[str, Any]:
    source_url = normalize_ozon_reference_url(task.get("source_url"))
    html_text, content_type = download_public_ozon_page(source_url)
    reference = extract_ozon_reference_from_html(source_url, html_text)
    fitkun_images = task.get("fitkun_images") if isinstance(task.get("fitkun_images"), list) else []
    if fitkun_images:
        inline_images = reference.get("inline_images") if isinstance(reference.get("inline_images"), dict) else {}
        image_urls = list(reference.get("image_urls") or [])
        for item in fitkun_images:
            if not isinstance(item, dict):
                continue
            url = normalize_ozon_reference_image_url(str(item.get("url") or ""))
            if not url:
                continue
            image_urls.insert(0, url)
            if item.get("data_url"):
                inline_images[url] = {
                    "data_url": item.get("data_url"),
                    "content_type": item.get("content_type") or "image/jpeg",
                    "byte_size": item.get("byte_size") or 0,
                }
        reference["image_urls"] = list(dict.fromkeys(image_urls))[:OZON_REFERENCE_IMAGE_LIMIT]
        reference["inline_images"] = inline_images
    return materialize_ozon_reference_capture(
        task,
        reference,
        content_type=content_type or "unknown",
        source_text=html_text,
        source_filename="source.html",
        capture_method="fitkun_import" if fitkun_images else "server_http",
    )


# Ozon-reference helpers moved to reference_helpers.py (exec'd at the bottom).

def _normalize_sku_fact_value(field_name: str, value: Any) -> Any:
    if field_name in SKU_NUMERIC_FACT_FIELDS:
        if value in {None, ""}:
            return None
        try:
            number = float(str(value).replace(",", "."))
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=f"{field_name} 必须是数字") from exc
        if not math.isfinite(number) or number <= 0:
            raise HTTPException(status_code=422, detail=f"{field_name} 必须大于0")
        return int(number) if number.is_integer() else round(number, 3)
    text = str(value or "").strip()
    return text or None


def persist_workbench_sku_overrides(
    product_dir: Path,
    payload_overrides: Dict[str, Dict[str, Any]],
    updated_at: str,
) -> bool:
    source = load_optional_json(product_dir / "input/source.json")
    collection_id = str(source.get("collection_id") or "unknown")
    path = product_dir / "input/workbench-sku-overrides.json"
    current = load_optional_json(path, {
        "schema_version": "1.0.0",
        "product_id": product_dir.name,
        "collection_id": collection_id,
        "source_kind": "workbench_collection",
        "overrides": [],
    })
    existing: Dict[Tuple[str, str], Dict[str, Any]] = {
        (str(item.get("sku_id")), str(item.get("field_name"))): dict(item)
        for item in current.get("overrides") or []
        if isinstance(item, dict)
    }
    changed = False
    for sku_id, values in payload_overrides.items():
        for field_name, value in values.items():
            if field_name in {"selling_price_cny", "selling_price_rub"}:
                continue
            canonical_unit = (
                SKU_FACT_FIELD_UNITS.get(field_name)
                or ("ozon_dictionary" if str(field_name).startswith("attribute:") else "text")
            )
            normalized_value = _normalize_sku_fact_value(str(field_name), value)
            key = (str(sku_id), str(field_name))
            if normalized_value is None:
                if key in existing:
                    existing.pop(key, None)
                    changed = True
                continue
            record = {
                "product_id": product_dir.name,
                "collection_id": collection_id,
                "sku_id": str(sku_id),
                "field_name": str(field_name),
                "canonical_value": normalized_value,
                "canonical_unit": canonical_unit,
                "source_kind": "workbench_collection",
                "mapping_method": "manual_workbench_edit",
                "updated_at": updated_at,
            }
            if existing.get(key) != record:
                existing[key] = record
                changed = True
    if not changed:
        return False
    merged = {
        "schema_version": "1.0.0",
        "product_id": product_dir.name,
        "collection_id": collection_id,
        "source_kind": "workbench_collection",
        "updated_at": updated_at,
        "overrides": sorted(existing.values(), key=lambda item: (item["sku_id"], item["field_name"])),
    }
    atomic_write_json(path, merged)
    invalidation_path = product_dir / "output/sku-fact-invalidation.json"
    invalidation = load_optional_json(invalidation_path, {
        "schema_version": "1.0.0",
        "product_id": product_dir.name,
        "events": [],
    })
    invalidation.setdefault("events", []).append({
        "at": updated_at,
        "reason": "workbench_sku_override_changed",
        "downstream": [
            "merged-product-facts",
            "attribute-fill-input",
            "ozon-ecommerce-design.attributes",
            "ozon-attributes-final",
            "image-plan.sku-copy",
            "pricing-result.shipping",
            "ozon-draft",
            "ozon-upload-payload",
        ],
    })
    invalidation["events"] = invalidation["events"][-50:]
    atomic_write_json(invalidation_path, invalidation)
    return True


def deep_merge_sku_overrides(existing: Dict[str, Any], incoming: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(existing or {})
    for sku_id, values in (incoming or {}).items():
        if isinstance(values, dict):
            current = dict(merged.get(str(sku_id)) if isinstance(merged.get(str(sku_id)), dict) else {})
            for field_name, value in values.items():
                if value in {None, ""}:
                    current.pop(str(field_name), None)
                else:
                    current[str(field_name)] = value
            if current:
                merged[str(sku_id)] = current
            else:
                merged.pop(str(sku_id), None)
        else:
            merged[str(sku_id)] = values
    return merged


def workbench_sku_dynamic_attribute_unit(attribute: Dict[str, Any]) -> str:
    name = str(attribute.get("attribute_name") or attribute.get("name") or "").casefold()
    description = str(attribute.get("description") or "").casefold()
    text = f"{name} {description}"
    if any(token in text for token in ("цвет", "color", "颜色")):
        return "text"
    if any(token in text for token in ("объем", "объём", "емкость", "ёмкость", "capacity", "容量")):
        return "ml"
    if any(token in text for token in ("длина", "ширина", "высота", "размер", "габарит", "length", "width", "height", "尺寸")):
        return "mm"
    if any(token in text for token in ("вес", "масса", "weight", "重量")):
        return "g"
    if any(token in text for token in ("количество", "шт", "pcs", "装量", "件数", "件")):
        return "pcs"
    return "text"


def is_workbench_sku_dynamic_attribute(attribute: Dict[str, Any]) -> bool:
    if not bool(attribute.get("is_aspect")):
        return False
    name = str(attribute.get("attribute_name") or attribute.get("name") or "").casefold()
    description = str(attribute.get("description") or "").casefold()
    text = f"{name} {description}"
    sku_tokens = (
        "цвет", "color", "颜色",
        "объем", "объём", "емкость", "ёмкость", "capacity", "容量",
        "длина", "ширина", "высота", "размер", "габарит", "length", "width", "height", "尺寸", "规格",
        "вес", "масса", "weight", "重量",
        "количество", "шт", "pcs", "装量", "件数",
    )
    return any(token in text for token in sku_tokens)


def workbench_category_dynamic_attributes(product_dir: Path) -> Dict[str, Dict[str, Any]]:
    metadata = load_optional_json(product_dir / "output/ozon-category-attributes.json")
    dynamic: Dict[str, Dict[str, Any]] = {}
    for attribute in metadata.get("attributes") or []:
        if not isinstance(attribute, dict) or not is_workbench_sku_dynamic_attribute(attribute):
            continue
        attribute_id = str(attribute.get("attribute_id") or "").strip()
        if not attribute_id:
            continue
        dynamic[attribute_id] = {
            "attribute_id": attribute.get("attribute_id"),
            "attribute_name": attribute.get("attribute_name") or attribute.get("name") or attribute_id,
            "description": attribute.get("description") or "",
            "type": attribute.get("type") or attribute.get("attribute_type") or "String",
            "required": bool(attribute.get("required")),
            "is_aspect": bool(attribute.get("is_aspect")),
            "is_collection": bool(attribute.get("is_collection")),
            "max_value_count": attribute.get("max_value_count"),
            "dictionary_id": attribute.get("dictionary_id"),
            "allowed_values": attribute.get("allowed_values") or [],
            "canonical_value": "",
            "canonical_unit": workbench_sku_dynamic_attribute_unit(attribute),
            "source": "output/ozon-category-attributes.json",
            "mapping_method": "pending_workbench_sku_value",
        }
    return dynamic


def invalidate_sku_fact_outputs(product_dir: Path) -> None:
    for relative in (
        "output/merged-product-facts.json",
        "output/attribute-fill-input.json",
        "output/sku-run-snapshot.json",
        "output/ozon-attributes-final.json",
        "output/ozon-draft.json",
        "output/ozon-upload-config.json",
    ):
        path = product_dir / relative
        if path.is_file():
            path.unlink()


def load_optional_json(path: Path, default: Any = None) -> Any:
    if not path.is_file():
        return {} if default is None else default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {} if default is None else default


def normalized_attribute_name(value: Any) -> str:
    return re.sub(r"[^a-zа-яё0-9]", "", str(value or "").casefold())


SYSTEM_MODEL_ATTRIBUTE_NAME_KEYS = {
    normalized_attribute_name(name) for name in SYSTEM_MODEL_ATTRIBUTE_NAMES
}


def is_system_model_attribute(item: Dict[str, Any]) -> bool:
    return normalized_attribute_name(item.get("attribute_name")) in SYSTEM_MODEL_ATTRIBUTE_NAME_KEYS


MEASUREMENT_ATTRIBUTE_NAME_KEYS = {
    normalized_attribute_name(name)
    for name in (
        "Вес товара, г", "Вес товара", "Вес, г",
        "Вес товара с упаковкой", "Вес с упаковкой", "Вес с упаковкой, г", "Вес упаковки",
        "Длина, мм", "Длина товара, мм", "Длина, см", "Длина товара, см",
        "Ширина, мм", "Ширина товара, мм", "Ширина, см", "Ширина товара, см",
        "Высота, мм", "Высота товара, мм", "Высота, см", "Высота товара, см",
        "Размер упаковки", "Габариты упаковки",
        "Размеры, мм", "Размеры товара, мм",
    )
}


def is_measurement_attribute(item: Dict[str, Any]) -> bool:
    return normalized_attribute_name(item.get("attribute_name")) in MEASUREMENT_ATTRIBUTE_NAME_KEYS


def stable_product_model_name(product_dir: Path, source: Dict[str, Any]) -> str:
    """Same product => same 12-digit model name; different product => different."""
    identity = {
        "strategy": SYSTEM_MODEL_NAME_STRATEGY,
        "product_id": str(source.get("product_id") or product_dir.name),
        "collection_id": str(source.get("collection_id") or "unknown"),
        "source_product_id": str(
            source.get("source_product_id") or source.get("offer_id") or "unknown"
        ),
        "source_url": str(
            source.get("canonical_source_url") or source.get("source_url") or "unknown"
        ),
    }
    encoded = json.dumps(
        identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    digest = hashlib.sha256(encoded).digest()
    number = 100_000_000_000 + int.from_bytes(digest[:8], "big") % 900_000_000_000
    return str(number)


def refresh_attribute_summary(attributes: Dict[str, Any]) -> None:
    def has_value(value: Any) -> bool:
        if value is None:
            return False
        if isinstance(value, str):
            return bool(value.strip()) and value.strip().casefold() != "unknown"
        if isinstance(value, (list, dict)):
            return bool(value)
        return True

    items = attributes.get("attributes") or []
    filled = [item for item in items if has_value(item.get("value"))]
    missing_required = [
        item for item in items
        if item.get("required") and not has_value(item.get("value"))
    ]
    attributes["summary"] = {
        **(attributes.get("summary") or {}),
        "total_count": len(items),
        "filled_count": len(filled),
        "unknown_count": len(items) - len(filled),
        "required_count": sum(bool(item.get("required")) for item in items),
        "required_filled_count": sum(
            bool(item.get("required")) and has_value(item.get("value"))
            for item in items
        ),
        "mapped_count": sum(
            bool(item.get("required")) and has_value(item.get("value"))
            for item in items
        ),
        "missing_count": len(missing_required),
    }
    attributes["missing_required_attributes"] = missing_required


def workbench_attribute_view(product_dir: Path) -> Dict[str, Any]:
    """Return current compiled values without exposing the full Ozon dictionaries."""
    final = load_optional_json(product_dir / "output/ozon-attributes-final.json")
    raw = final if final.get("attributes") else load_optional_json(product_dir / "output/ozon-attributes.json")
    translations = load_optional_json(
        ATTRIBUTE_TRANSLATIONS_PATH, {"translations": {}}
    ).get("translations") or {}

    def has_value(value: Any) -> bool:
        if value is None:
            return False
        if isinstance(value, str):
            return bool(value.strip()) and value.strip().casefold() != "unknown"
        if isinstance(value, (list, dict)):
            return bool(value)
        return True

    public_items: List[Dict[str, Any]] = []
    for item in raw.get("attributes") or []:
        value = item.get("value")
        source = str(item.get("source") or "unknown")
        public_items.append({
            "attribute_id": item.get("attribute_id"),
            "attribute_name": item.get("attribute_name") or str(item.get("attribute_id") or "unknown"),
            "attribute_name_zh": translations.get(
                str(item.get("attribute_name") or ""),
                item.get("attribute_name") or str(item.get("attribute_id") or "unknown"),
            ),
            "required": bool(item.get("required")),
            "value": value,
            "source": source,
            "confidence": item.get("confidence"),
            "dictionary_value_id": item.get("dictionary_value_id"),
            "validation_status": (
                "estimated" if source == "AI_estimated"
                else "valid" if has_value(value)
                else "unknown"
            ),
        })

    filled = [item for item in public_items if has_value(item.get("value"))]
    missing_required = [
        item for item in public_items
        if item.get("required") and not has_value(item.get("value"))
    ]
    estimated = [item for item in public_items if item.get("source") == "AI_estimated"]
    summary = {
        "total": len(public_items),
        "filled": len(filled),
        "missing_required": len(missing_required),
        "estimated": len(estimated),
        "unknown": len(public_items) - len(filled),
        # Keep the legacy field names until the old static reader is removed.
        "total_count": len(public_items),
        "filled_count": len(filled),
        "unknown_count": len(public_items) - len(filled),
        "required_count": sum(bool(item.get("required")) for item in public_items),
        "required_filled_count": sum(
            bool(item.get("required")) and has_value(item.get("value"))
            for item in public_items
        ),
        "missing_count": len(missing_required),
    }
    return {
        "schema_version": raw.get("schema_version") or "1.0.0",
        "product_id": raw.get("product_id") or product_dir.name,
        "category_id": raw.get("category_id"),
        "type_id": raw.get("type_id"),
        "metadata_source": raw.get("schema_source") or raw.get("metadata_source") or "unknown",
        "attributes": public_items,
        "missing_required_attributes": missing_required,
        "summary": summary,
        "warnings": raw.get("warnings") or [],
    }


def apply_stable_model_attributes(
    product_dir: Path,
    source: Dict[str, Any],
    attributes: Dict[str, Any],
) -> str | None:
    model_items = [
        item for item in attributes.get("attributes") or []
        if is_system_model_attribute(item)
    ]
    if not model_items:
        return None
    model_name = stable_product_model_name(product_dir, source)
    for item in model_items:
        item["value"] = model_name
        item["source"] = "AI_estimated"
        item["confidence"] = max(float(item.get("confidence") or 0), 0.95)
        item["dictionary_value_id"] = None
        item["evidence"] = [
            "system.stable_product_model_name",
            SYSTEM_MODEL_NAME_STRATEGY,
        ]
        item["validation_status"] = "estimated"
    refresh_attribute_summary(attributes)
    return model_name


def system_model_attribute_ids(product_dir: Path) -> set[str]:
    ids: set[str] = set()
    for name in ("ozon-attributes.json", "ozon-attributes-final.json"):
        data = load_optional_json(product_dir / "output" / name)
        for item in data.get("attributes") or []:
            if item.get("attribute_id") is not None and is_system_model_attribute(item):
                ids.add(str(item.get("attribute_id")))
    return ids


def workbench_settings() -> Dict[str, Any]:
    value = load_optional_json(ROOT / "config/workbench-settings.json", DEFAULT_WORKBENCH_SETTINGS.copy())
    # Historical settings files may still contain the former manual default.
    # Do not let that stale local preference insert a second upload click into
    # a newly authorized production batch.
    return {
        **DEFAULT_WORKBENCH_SETTINGS,
        **(value if isinstance(value, dict) else {}),
        "auto_mode_enabled": True,
        "default_review_mode": "automatic",
    }


def save_workbench_settings(patch: Dict[str, Any]) -> Dict[str, Any]:
    settings = workbench_settings()
    # A Run Task authorization now always carries the product through public
    # image hosting and the selected-store submission.  Keep accepting the
    # historical settings request shape, but never persist a stale manual
    # preference that would add a second upload click to a new batch.
    settings["auto_mode_enabled"] = True
    settings["default_review_mode"] = "automatic"
    settings["learning_threshold"] = 2
    settings["updated_at"] = now_iso()
    atomic_write_json(ROOT / "config/workbench-settings.json", settings)
    return settings


def workbench_product_dir(product_id: str) -> Path:
    if not re.fullmatch(r"P[0-9]{6}", product_id):
        raise HTTPException(status_code=404, detail="商品不存在")
    product_dir = PRODUCTS_DIR / product_id
    if not product_dir.is_dir() or not product_is_owned(product_dir) or product_is_archived(product_dir):
        raise HTTPException(status_code=404, detail="商品不存在")
    return product_dir


def ensure_workbench_product_mutable(product_dir: Path) -> None:
    """Keep submitted products as local read-only records.

    A known Ozon task id is the local terminal handoff.  The operator handles
    later edits in the Ozon product-card backend, so no workbench endpoint may
    silently mutate or prepare the same local product for another submission.
    """
    status = effective_product_status(
        product_dir,
        load_optional_json(product_dir / "status.json"),
    )
    status_name = str(status.get("status") or "").upper()
    ozon = status.get("ozon") or {}
    has_remote_task = known_remote_identity(ozon.get("task_id")) or int(status.get("api_write_count") or 0) > 0
    if status_name in TERMINAL_PUBLICATION_STATES or status_name in REMOTE_PENDING_PUBLICATION_STATES or has_remote_task:
        raise HTTPException(
            status_code=409,
            detail="该商品已经提交Ozon，本地记录只读；请先只读查询Ozon任务结果，确认失败后才能重新上传。",
        )


def public_state(status_name: str) -> str:
    value = str(status_name or "unknown").upper()
    if value in ATTENTION_STATES:
        return "需要处理"
    if "FAIL" in value or "ERROR" in value:
        return "失败"
    if value == "PARTIAL":
        return "部分提交"
    if value in {"PENDING_REMOTE", "HANDED_OFF_TO_OZON", "SUBMITTED", "UPLOADING", "OZON_MODERATION"}:
        return "等待Ozon处理"
    if value in {"CREATED", "UPLOADED", "ACTIVE", "SUCCESS", "IMPORTED"}:
        return "完成"
    if value == "OZON_REFERENCE_DRAFT":
        return "参考草稿"
    if value == "OZON_REFERENCE_IMAGES_PARTIAL":
        return "参考图片部分完成"
    if value == "OZON_REFERENCE_IMAGES_GENERATED":
        return "参考图片已生成"
    if value == "OZON_REFERENCE_CARD_READY":
        return "商品卡已完成"
    if value == "WAITING_MANUAL_REVIEW":
        return "等待上传"
    if value in {"COLLECTED", "STOPPED", "WAITING", "NOT_STARTED", "UNKNOWN"}:
        return "待处理"
    return "处理中"


def workflow_bucket(status_name: str) -> str:
    value = str(status_name or "unknown").upper()
    if value in {"CREATED", "UPLOADED", "ACTIVE"}:
        return "已完成"
    if value == "PARTIAL":
        return "待继续"
    if value in {"SUBMITTED", "UPLOADING", "PENDING_REMOTE", "OZON_MODERATION", "HANDED_OFF_TO_OZON"}:
        return "等待Ozon"
    if value == "PARTIAL_FAILED":
        return "部分失败"
    if value == "WAITING_MANUAL_REVIEW":
        return "等待上传"
    if value == "OZON_REFERENCE_DRAFT":
        return "参考草稿"
    if value == "OZON_REFERENCE_IMAGES_PARTIAL":
        return "参考图片待补齐"
    if value == "OZON_REFERENCE_CARD_READY":
        return "待上传"
    if value in ATTENTION_STATES:
        return "需要处理"
    if value in {"QUEUED", "PROCESSING"}:
        return "生成中"
    if value == "OZON_REFERENCE_IMAGES_GENERATED":
        return "参考图片已生成"
    if value in {"CATEGORY_MATCHED", "CONTENT_GENERATED", "IMAGES_GENERATED", "PRICED"}:
        return "待继续"
    if value == "STOPPED":
        return "已停止"
    return "采集箱"


def friendly_pipeline_error(status: Dict[str, Any]) -> Dict[str, str]:
    """Turn an internal pipeline error into a short, actionable UI message.

    The original error is kept as ``technical`` so diagnostics remain possible,
    but normal workbench screens should tell the operator what happened and
    where to fix it instead of exposing Python/HTTP wording.
    """
    raw = str(status.get("error_message") or "任务没有完成")
    step = str(status.get("failed_step") or status.get("current_step") or "")
    text = raw.casefold()
    result = {
        "title": "商品处理没有完成",
        "message": "这件商品在当前步骤没有完成，已保留前面已经生成的内容。点“立即修改”检查后再继续。",
        "action": "检查并修改",
        "tab": "risk",
        "technical": raw,
        "step": step,
    }
    issue_summary = (
        status.get("ozon_issue_summary")
        or (status.get("ozon") or {}).get("issue_summary")
        or {}
    )
    issue_bucket = str(issue_summary.get("primary_bucket") or "").lower()
    if issue_summary.get("has_issues"):
        issue_title = str(issue_summary.get("primary_label") or "Ozon 上传问题")
        issue_message = str(issue_summary.get("message") or raw)
        tab = "store"
        action_label = "查看上传问题"
        if issue_bucket == "image_link":
            tab = "images"
            action_label = "重传图片"
        elif issue_bucket in {"numeric_contract", "logistics_weight"}:
            tab = "category" if issue_bucket == "numeric_contract" else "price"
            action_label = "修正字段"
        elif issue_bucket == "description_decline":
            tab = "content"
            action_label = "修正文案"
        elif issue_bucket == "category_mismatch":
            tab = "category"
            action_label = "修改类目"
        elif issue_bucket == "duplicate_spu":
            tab = "store"
            action_label = "处理重复"
        elif issue_bucket == "store_auth":
            tab = "store"
            action_label = "检查店铺"
        result.update({
            "title": issue_title,
            "message": issue_message,
            "action": action_label,
            "tab": tab,
        })
    elif any(token in text for token in (
        "missing_required_sku_reference", "image_source_preflight_blocked", "缺少真实参考图",
        "no registered sku-bound real image", "has no registered sku reference",
    )):
        result.update({
            "title": "SKU缺少参考图",
            "message": "该SKU缺少参考图，请从本商品已采集图片中选择一张绑定后继续。系统会标记为用户绑定参考图，不会伪装成1688 SKU专属图。",
            "action": "绑定SKU参考图",
            "tab": "sku",
        })
    elif "failed to fetch" in text or "connection" in text or "timed out" in text and "ozon" in text:
        result.update({
            "title": "主电脑工作台没有回应",
            "message": "连接主电脑失败。先确认工作台服务正在运行，再点“重试”；不会重复上传商品。",
            "action": "重试任务",
            "tab": "risk",
        })
    elif any(token in text for token in ("empty_placeholder_panel", "placeholder", "空白占位", "空面板")):
        result.update({
            "title": "图片里有空白占位框",
            "message": "这张图片只生成了一个空框，没有有效卖点内容。系统会保留其他合格图片，只重做这张。",
            "action": "重做这张图片",
            "tab": "images",
        })
    elif "image" in text or "image_generation" in text or step in {"image_generation", "image_qc", "image_plan"}:
        result.update({
            "title": "图片步骤没有完成",
            "message": "部分图片没有生成或质检未通过，已完成的图片会保留。进入“图片”页，只重做失败图片即可。",
            "action": "修改图片",
            "tab": "images",
        })
    elif any(token in text for token in ("attribute", "required", "dictionary", "6383", "field_completion")) or step in {"field_completion", "category_match", "variant_rules"}:
        result.update({
            "title": "类目属性需要修改",
            "message": "有类目属性没有填对。进入“类目”，修改带“必须填写”或错误提示的字段；可选字段不影响继续。",
            "action": "修改类目属性",
            "tab": "category",
        })
    elif any(token in text for token in ("price", "pricing", "selling_price", "measurements")) or step in {"measurements"}:
        result.update({
            "title": "价格或尺寸需要修改",
            "message": "售价、重量或尺寸资料不完整。进入“价格”或“SKU”，修改后再继续。",
            "action": "修改价格或尺寸",
            "tab": "price",
        })
    elif any(token in text for token in ("upload", "offer", "duplicate", "pending", "store")) or step in {"ozon_upload", "offer_exists_check", "upload_feasibility"}:
        result.update({
            "title": "上传被阻止",
            "message": "点击上传时发现缺图、店铺、重复提交或状态不明确等风险。修改后再点上传即可。",
            "action": "处理上传问题",
            "tab": "store",
        })
    elif any(token in text for token in ("codex", "403", "429", "analysis")) or step in {"product_analysis", "product_positioning", "ecommerce_design", "russian_copy"}:
        result.update({
            "title": "商品资料生成没有完成",
            "message": "商品资料生成遇到问题。已保留采集内容，进入“资料”页检查标题、卖点和简介后再继续。",
            "action": "修改商品资料",
            "tab": "content",
        })
    return result


def image_plan_items(plan: Dict[str, Any]) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for key in ("main_images", "detail_images", "disclaimer_images", "color_samples"):
        for item in plan.get(key) or []:
            if isinstance(item, dict) and item.get("slot"):
                items.append(item)
    return sorted(items, key=lambda item: int(item.get("workbench_order", len(items))))


def find_image_plan_item(plan: Dict[str, Any], slot: str) -> Tuple[str, Dict[str, Any]]:
    for key in ("main_images", "detail_images", "disclaimer_images", "color_samples"):
        for item in plan.get(key) or []:
            if str(item.get("slot")) == slot:
                return key, item
    raise HTTPException(status_code=404, detail="图片槽位不存在")


def product_output_image_path(product_dir: Path, raw_path: Any) -> Optional[Path]:
    """Resolve a planned image inside this product's two generated-image trees only."""
    value = str(raw_path or "").strip()
    if not value or value == "unknown":
        return None
    candidate = Path(value)
    resolved = candidate.resolve() if candidate.is_absolute() else (ROOT / candidate).resolve()
    allowed_roots = ((product_dir / "output/generated-images").resolve(),)
    if not any(allowed_root in resolved.parents for allowed_root in allowed_roots):
        return None
    return resolved


def regeneration_slot_names(value: Any) -> List[str]:
    """Accept both legacy slot strings and detailed retry records."""
    names: List[str] = []
    for item in value or []:
        if isinstance(item, dict):
            item = item.get("slot") or item.get("image_slot")
        slot = str(item or "").strip()
        if slot and slot not in names:
            names.append(slot)
    return names


def workbench_images(
    product_dir: Path,
    plan: Dict[str, Any],
    qc: Dict[str, Any],
    status: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    request = load_optional_json(product_dir / "output/image-regeneration-request.json")
    hard_gate = load_optional_json(product_dir / "output/image-hard-gate.json")
    hard_checked_slots = {
        str(item.get("slot") if isinstance(item, dict) else item)
        for item in hard_gate.get("checked_slots") or []
        if str(item.get("slot") if isinstance(item, dict) else item).strip()
    }
    retry_slots = set(regeneration_slot_names([
        *(request.get("requested_slots") or []),
        *(request.get("failed_slots") or []),
    ]))
    issue_by_slot: Dict[str, List[Dict[str, Any]]] = {}
    for issue in qc.get("issues") or []:
        for slot in issue.get("image_slots") or []:
            issue_by_slot.setdefault(str(slot), []).append(issue)
    score = qc.get("score")
    images = []
    active_generation = bool(status and status.get("current_step") == "image_generation")
    active_slots = {str(value) for value in (status or {}).get("active_image_slots") or []}
    retry_counts = (status or {}).get("image_slot_retry_count_by_slot") or {}
    planned_items = image_plan_items(plan)
    if active_generation and not active_slots:
        # Compatibility for workers started before per-slot progress existed.
        # Show the next bounded wave as active instead of leaving the gallery at WAITING.
        fallback_active_slots = [
            str(item.get("slot"))
            for item in planned_items
            if str(item.get("slot") or "") not in hard_checked_slots
            and not (
                (path := product_output_image_path(product_dir, item.get("output_path")))
                and path.is_file()
            )
        ]
        active_slots = set(fallback_active_slots[:3])
    for index, item in enumerate(planned_items):
        slot = str(item.get("slot"))
        raw_path = str(item.get("output_path") or "")
        image_path = product_output_image_path(product_dir, raw_path)
        exists = bool(image_path and image_path.is_file())
        issues = issue_by_slot.get(slot, [])
        blocking_issues = [
            entry for entry in issues
            if str(entry.get("severity") or "").lower() in {"high", "critical"}
            or str(entry.get("code") or "") in set(qc.get("critical_failures") or [])
        ]
        if slot in retry_slots:
            state = "RETRYING"
        elif exists and issues and (blocking_issues or qc.get("decision") == "reject"):
            state = "FAIL"
        elif exists and qc:
            state = "PASS" if qc.get("decision") != "reject" else "QC"
        elif exists and slot in hard_checked_slots:
            state = "PASS"
        elif exists:
            # A file appearing on disk only means generation returned bytes.
            # It is not a completed ecommerce image until the slot hard gate
            # has checked it.  This prevents half-finished/placeholder images
            # from being shown as approved workbench results.
            state = "QC"
        elif item.get("status") in {"generating", "processing"}:
            state = "GENERATING"
        elif active_generation and slot in active_slots:
            state = "GENERATING"
        else:
            state = "WAITING"
        version = None
        if exists and image_path:
            stat_result = image_path.stat()
            version = f"{stat_result.st_mtime_ns}-{stat_result.st_size}"
        base_url = (
            f"/api/workbench/products/{product_dir.name}/images/{urllib.parse.quote(slot)}"
            if exists else None
        )
        images.append({
            "slot": slot,
            "type": item.get("image_type") or item.get("type") or "detail",
            "state": state,
            "url": f"{base_url}?v={version}" if base_url else None,
            "download_url": f"{base_url}?v={version}&download=1" if base_url else None,
            "prompt_brief": item.get("prompt_brief") or "",
            "russian_text": item.get("russian_text") or [],
            "purpose": item.get("selling_goal") or item.get("purpose") or "",
            "variant_scope": item.get("variant_scope") or "shared",
            "shared_across_variants": bool(item.get("shared_across_variants")),
            "source_sku_id": str(item.get("source_sku_id") or ""),
            "score": score,
            "issues": [entry.get("message") or entry.get("code") for entry in issues],
            "retry_count": int(retry_counts.get(slot) or 0),
            "order": index,
        })
    return images


def build_sku_image_groups(
    skus: List[Dict[str, Any]], images: List[Dict[str, Any]], *, exact_binding: bool = False,
) -> List[Dict[str, Any]]:
    """Present every selected SKU with its own main image and shared details."""
    shared_details = [
        item for item in images
        if item.get("type") != "main"
        and (
            item.get("shared_across_variants")
            or item.get("variant_scope") == "shared"
            or item.get("source_sku_id") in {"", "all"}
        )
    ]
    generic_mains = [
        item for item in images
        if item.get("type") == "main"
        and item.get("source_sku_id") in {"", "all"}
    ]
    groups: List[Dict[str, Any]] = []
    for sku in skus:
        sku_id = str(sku.get("sku_id") or "")
        main_image = next(
            (
                item for item in images
                if item.get("type") == "main"
                and (
                    str(item.get("source_sku_id") or "") == sku_id
                    or str(item.get("slot") or "") == f"main-{sku_id}"
                )
            ),
            None if exact_binding else (generic_mains[0] if generic_mains else None),
        )
        group_images = ([main_image] if main_image else []) + shared_details
        groups.append({
            "sku_id": sku_id,
            "sku_name": sku.get("name") or sku_id,
            "option_text": sku.get("option_text") or sku.get("name") or sku_id,
            "main_image": main_image,
            "detail_images": shared_details,
            "images": group_images,
            "main_image_missing": main_image is None,
        })
    return groups


def readable_timeline(status: Dict[str, Any], product_dir: Path) -> List[Dict[str, Any]]:
    step_labels = {
        "collect_source": "完成1688采集", "validate_source": "完成采集数据检查",
        "product_analysis": "完成商品理解", "category_match": "完成Ozon类目匹配",
        "ecommerce_design": "完成Ozon电商方案",
        "variant_rules": "完成SKU变体判断", "measurements": "完成重量尺寸处理",
        "russian_copy": "完成俄文资料", "image_plan": "完成图片方案",
        "image_generation": "完成图片生成", "image_qc": "完成图片质检",
        "field_completion": "完成Ozon字段整理", "ozon_upload": "提交Ozon",
    }
    result: List[Dict[str, Any]] = []
    for item in status.get("steps") or []:
        label = step_labels.get(str(item.get("name")), str(item.get("name") or "任务更新"))
        if item.get("status") == "failed":
            label = f"{label}失败"
        result.append({
            "at": item.get("finished_at") or item.get("started_at") or "unknown",
            "message": label,
            "level": "error" if item.get("status") == "failed" else "info",
        })
    ozon = status.get("ozon") or {}
    if ozon.get("task_id") not in {None, "unknown", ""}:
        result.append({
            "at": status.get("last_run_at") or "unknown",
            "message": f"已获得Ozon任务号 {ozon.get('task_id')}",
            "level": "info",
        })
    return sorted(result, key=lambda item: str(item.get("at") or ""), reverse=True)[:80]


PIPELINE_STEP_LABELS = {
    "queue": "排队等待",
    "collect_source": "1688采集",
    "validate_source": "检查采集数据",
    "product_analysis": "分析商品事实",
    "category_match": "匹配Ozon类目",
    "variant_rules": "判断SKU变体",
    "measurements": "处理尺寸重量",
    "offer_exists_check": "检查是否已存在",
    "upload_feasibility": "检查上传条件",
    "product_positioning": "确定商品定位",
    "ecommerce_design": "设计上架方案",
    "russian_copy": "生成俄文资料",
    "field_completion": "填写Ozon属性",
    "image_plan": "规划图片方案",
    "image_generation": "生成商品图片",
    "image_qc": "图片质检",
    "ozon_upload": "提交Ozon",
    "read_only_status_query": "等待Ozon处理",
    "complete": "已完成",
    "manual_ozon_upload": "等待上传",
}


def workbench_pipeline_progress(product_dir: Path, status: Dict[str, Any], images: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Small UI-facing progress contract; it never changes production state."""
    raw_status = str(status.get("status") or "unknown").upper()
    worker = active_product_worker(product_dir)
    is_running = raw_status in {"QUEUED", "PROCESSING", "UPLOADING", "RUNNING"} or bool(worker)
    step = str(
        (worker or {}).get("step")
        or (status.get("current_step") if is_running else status.get("next_action"))
        or status.get("current_step")
        or "queue"
    )
    active_step = status.get("active_step") if isinstance(status.get("active_step"), dict) else {}
    active_started_at = str(active_step.get("started_at") or "").strip()
    active_elapsed_seconds: Optional[int] = None
    if active_started_at:
        try:
            started = datetime.fromisoformat(active_started_at.replace("Z", "+00:00"))
            if started.tzinfo is None:
                started = started.replace(tzinfo=timezone.utc)
            active_elapsed_seconds = max(0, int((datetime.now(timezone.utc) - started.astimezone(timezone.utc)).total_seconds()))
        except ValueError:
            active_elapsed_seconds = None
    retry_count = int((status.get("retry_count_by_step") or {}).get(step) or 0)
    generated_images = len([item for item in images if item.get("url")])
    planned_images = int(
        status.get("planned_image_slots")
        or len(images)
        or 0
    )
    completed_images = int(status.get("completed_image_slots") or generated_images)
    active_slots = [
        str(item) for item in ((worker or {}).get("active_slots") or status.get("active_image_slots") or [])
        if str(item).strip()
    ]
    performance = load_optional_json(product_dir / "output/performance-report.json", {"steps": []})
    step_rows = [
        row for row in performance.get("steps") or []
        if str(row.get("step") or "") == step
    ]
    latest = step_rows[-1] if step_rows else {}
    all_rows = performance.get("steps") or []
    last_step = all_rows[-1] if all_rows else {}
    if raw_status in {"PENDING_REMOTE", "HANDED_OFF_TO_OZON", "SUBMITTED", "UPLOADING", "OZON_MODERATION"} and not worker:
        ozon = status.get("ozon") if isinstance(status.get("ozon"), dict) else {}
        task_id = str(ozon.get("task_id") or "").strip()
        task_text = f"；任务号 {task_id}" if task_id and task_id.lower() != "unknown" else ""
        status_note = f"已提交Ozon，等待平台处理{task_text}。本地不会重复上传。"
    elif step == "image_generation" and planned_images:
        slot_text = f"；当前：{'、'.join(active_slots)}" if active_slots else ""
        status_note = f"图片进度：已生成 {generated_images}/{planned_images}，已通过 {completed_images}/{planned_images}{slot_text}。"
    elif step == "ecommerce_design":
        if str(status.get("ai_service_state") or "") == "waiting_for_recovery":
            status_note = str(status.get("error_message") or "").strip() or "电商设计等待联网模型恢复，断点已保留。"
        elif active_elapsed_seconds is not None:
            status_note = (
                f"电商设计第{retry_count + 1}次生成中，已运行{active_elapsed_seconds}秒；"
                "正在生成标题、属性、图片分镜和提示词。"
            )
        else:
            status_note = "正在生成标题、属性、图片分镜和提示词；已有有效方案会直接复用。"
    elif step == "ozon_upload":
        status_note = "正在提交Ozon；已写入过的店铺不会重复提交。"
    else:
        status_note = str(status.get("image_progress_note") or "").strip()
        status_note = status_note or "后台正在处理，已完成断点会保留。"
    return {
        "step": step,
        "step_label": PIPELINE_STEP_LABELS.get(step, step),
        "is_running": is_running,
        "progress": int(status.get("progress") or 0),
        "status_note": status_note,
        "active_step": active_step,
        "active_step_elapsed_seconds": active_elapsed_seconds,
        "active_step_attempt": retry_count + 1,
        "ai_service_state": status.get("ai_service_state") or "normal",
        "ai_service_reason": status.get("ai_service_reason") or "unknown",
        "ai_service_retry_after": status.get("ai_service_retry_after") or "unknown",
        "worker_pid": worker.get("pid") if isinstance(worker, dict) else None,
        "worker_last_heartbeat_at": worker.get("last_heartbeat_at") if isinstance(worker, dict) else None,
        "worker_last_progress_at": worker.get("last_progress_at") if isinstance(worker, dict) else None,
        "active_slot_worker_count": worker.get("active_slot_worker_count") if isinstance(worker, dict) else 0,
        "active_image_slots": active_slots,
        "image_wave": status.get("image_wave") or 0,
        "image_parallelism": status.get("image_parallelism") or 0,
        "planned_image_slots": planned_images,
        "generated_image_slots": generated_images,
        "completed_image_slots": completed_images,
        "latest_step_duration_seconds": latest.get("duration_seconds"),
        "last_recorded_step": last_step.get("step") or "unknown",
        "last_recorded_status": last_step.get("status") or "unknown",
        "last_recorded_duration_seconds": last_step.get("duration_seconds"),
    }


def production_readiness_state(
    product_dir: Path,
    status: Dict[str, Any],
    plan: Dict[str, Any],
) -> Dict[str, Any]:
    """Describe whether this product can still run or upload under today's rules."""
    raw_status = str(status.get("status") or "unknown").upper()
    terminal = raw_status in TERMINAL_PUBLICATION_STATES
    remote_pending = (
        not terminal
        and (
            raw_status in REMOTE_PENDING_PUBLICATION_STATES
            or known_remote_identity((status.get("ozon") or {}).get("task_id"))
        )
    )
    source = load_optional_json(product_dir / "input/source.json")
    if source.get("source_kind") == "ozon_reference_draft":
        generated_count = len(list((product_dir / "output/generated-images").rglob("*.png"))) if (product_dir / "output/generated-images").exists() else 0
        if remote_pending:
            state = "submitted_read_only"
            message = "商品卡已提交Ozon，正在等待Ozon后台处理；库存未设置，本地不能重复上传。"
        elif terminal:
            state = "terminal_publication"
            message = "商品卡已提交完成；库存未设置。"
        elif raw_status == "OZON_REFERENCE_CARD_READY":
            state = "ozon_reference_card_ready"
            message = "Ozon参考商品卡已在本地完成；可直接提交Ozon，库存不会设置。"
        elif raw_status == "OZON_REFERENCE_IMAGES_GENERATED":
            state = "ozon_reference_images_generated"
            message = "Ozon参考商品图已生成；本地没有提交Ozon，也没有调用库存接口。"
        elif raw_status == "OZON_REFERENCE_IMAGES_PARTIAL":
            state = "ozon_reference_images_partial"
            message = f"Ozon参考商品图已生成 {generated_count} 张；点击继续会只补缺失图片。"
        elif raw_status in ATTENTION_STATES:
            state = "ozon_reference_needs_retry"
            message = "Ozon参考生图未完整完成；已生成图片已保留，点击继续会补缺失图片。"
        elif raw_status == "PROCESSING":
            state = "ozon_reference_generating_images"
            message = f"Ozon参考实拍风图片正在生成，已生成 {generated_count} 张。"
        else:
            state = "ozon_reference_draft"
            message = "这是Ozon公开商品卡参考草稿，可以继续生成我方实拍风商品图。"
        return {
            "formal_input_valid": True,
            "image_rules_present": bool(plan.get("main_images") or plan.get("detail_images")),
            "manual_image_confirmation_required": False,
            "terminal_publication": terminal,
            "blocking": False,
            "state": state,
            "message": message,
            "errors": [],
        }
    formal_error = None
    try:
        validate_formal_product_input(product_dir)
    except ProductionInputError as exc:
        formal_error = str(exc)
    has_image_rules = asset_boundaries_enabled(product_dir)
    image_rules = load_optional_json(asset_contract_path(product_dir)) if has_image_rules else {}
    manual_image_confirmation_required = bool(image_rules.get("manual_confirmation_required"))
    base = {
        "formal_input_valid": formal_error is None,
        "image_rules_present": has_image_rules,
        "manual_image_confirmation_required": manual_image_confirmation_required,
        "terminal_publication": terminal,
        "blocking": False,
        "errors": [],
    }
    if remote_pending:
        return {
            **base,
            "state": "submitted_read_only",
            "terminal_publication": False,
            "message": "商品已提交Ozon，正在等待Ozon生成商品卡；本地只允许执行只读状态查询，不能重复上传。",
        }
    if terminal:
        if formal_error or not has_image_rules:
            return {
                **base,
                "state": "legacy_submitted_read_only",
                "message": "这件商品已在旧流程提交Ozon，只保留查看记录；不会再次上传，也不需要补新版图片确认。",
                "errors": [formal_error] if formal_error else [],
            }
        return {
            **base,
            "state": "terminal_publication",
            "message": "Ozon 已返回商品卡创建结果；本地只保留记录，不能重复上传。",
        }
    if formal_error:
        return {
            **base,
            "state": "formal_input_blocked",
            "blocking": True,
            "message": "这件商品不是当前工作台本次采集的正式商品，已禁止继续运行和再次上传。请重新从工作台采集为新商品。",
            "errors": [formal_error],
        }
    if raw_status == "WAITING_MANUAL_REVIEW":
        if is_auto_upload_ready_status(status):
            return {
                **base,
                "state": "ready_for_auto_upload",
                "message": "商品资料和图片已经完成，当前批次已授权自动上传；继续生产会直接进入Ozon上传步骤。",
            }
        return {
            **base,
            "state": "ready_for_operator_upload",
            "message": "图片技术质检通过后自动使用；手动模式下你点击上传才会提交，不再逐张人工确认图片。",
        }
    return {
        **base,
        "state": "current_rules",
        "message": "当前商品使用本次工作台采集资料。",
    }


def product_ui_state(
    product_dir: Path,
    status: Dict[str, Any],
    readiness: Dict[str, Any],
    risk: Dict[str, Any],
    images: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Single translated state contract for Command Center display only."""
    raw_status = str(status.get("status") or "unknown").upper()
    readiness_state = str(readiness.get("state") or "").lower()
    issue_summary = (
        status.get("ozon_issue_summary")
        or (status.get("ozon") or {}).get("issue_summary")
        or {}
    )
    source = load_optional_json(product_dir / "input/source.json")
    source_kind = str(source.get("source_kind") or "workbench_collection")
    image_count = len([item for item in images if item.get("url")])

    def action(action_id: str, label: str, enabled: bool = True, reason: str = "") -> Dict[str, Any]:
        return {"id": action_id, "label": label, "enabled": enabled, "reason": reason}

    if source_kind == "ozon_reference_draft":
        if raw_status in REMOTE_PENDING_PUBLICATION_STATES or readiness_state == "submitted_read_only":
            return {
                "schema_version": "1.0.0",
                "kind": "ozon_reference",
                "state": "submitted_read_only",
                "tone": "running",
                "title": "等待 Ozon 处理",
                "message": "商品卡已提交 Ozon，正在等待后台生成商品卡；库存未设置，不能重复提交。",
                "progress_label": "等待 Ozon",
                "blocking": False,
                "primary_action": action("read_only_status_query", "等待 Ozon", False, "已提交，等待 Ozon 后台处理"),
                "secondary_actions": [action("view_details", "查看详情")],
            }
        if raw_status in TERMINAL_PUBLICATION_STATES or readiness_state == "terminal_publication":
            return {
                "schema_version": "1.0.0",
                "kind": "ozon_reference",
                "state": "terminal_publication",
                "tone": "ok",
                "title": "商品卡已提交",
                "message": "Ozon 商品卡已完成交接；库存未设置。",
                "progress_label": "已提交",
                "blocking": False,
                "primary_action": action("view_details", "查看详情"),
                "secondary_actions": [],
            }
        if raw_status == "OZON_REFERENCE_CARD_READY" or readiness_state == "ozon_reference_card_ready":
            return {
                "schema_version": "1.0.0",
                "kind": "ozon_reference",
                "state": "ozon_reference_card_ready",
                "tone": "ok",
                "title": "商品卡已完成",
                "message": f"已生成 {image_count} 张图片和 Ozon 商品卡资料；可直接提交 Ozon，库存不会设置。",
                "progress_label": "待提交 Ozon",
                "blocking": False,
                "primary_action": action("continue_reference_upload", "直接上传 Ozon"),
                "secondary_actions": [action("view_details", "查看详情"), action("regenerate_images", "重新生成图片")],
            }
        if raw_status == "PROCESSING" or readiness_state == "ozon_reference_generating_images":
            return {
                "schema_version": "1.0.0",
                "kind": "ozon_reference",
                "state": "ozon_reference_generating_images",
                "tone": "running",
                "title": "正在生成参考图片",
                "message": f"已采集参考图，正在生成我方实拍风商品图；当前已看到 {image_count} 张图片。",
                "progress_label": "参考图片生成中",
                "blocking": False,
                "primary_action": action("generation_running", "生成中", False, "后台正在生成图片"),
                "secondary_actions": [action("view_details", "查看详情")],
            }
        if raw_status == "OZON_REFERENCE_IMAGES_GENERATED" or readiness_state == "ozon_reference_images_generated":
            return {
                "schema_version": "1.0.0",
                "kind": "ozon_reference",
                "state": "ozon_reference_images_generated",
                "tone": "ok",
                "title": "参考图片已生成",
                "message": f"已生成 {image_count} 张可查看图片；本地没有提交 Ozon，也没有调用库存接口。",
                "progress_label": "图片已生成",
                "blocking": False,
                "primary_action": action("view_details", "查看详情"),
                "secondary_actions": [action("regenerate_images", "重新生成图片")],
            }
        if raw_status == "OZON_REFERENCE_IMAGES_PARTIAL" or readiness_state == "ozon_reference_images_partial":
            return {
                "schema_version": "1.0.0",
                "kind": "ozon_reference",
                "state": "ozon_reference_images_partial",
                "tone": "warning",
                "title": "参考图片部分完成",
                "message": f"已生成 {image_count} 张图片；继续生产只补缺失图片，不会重新提交 Ozon。",
                "progress_label": "待补齐图片",
                "blocking": False,
                "primary_action": action("continue_reference_images", "继续补图"),
                "secondary_actions": [action("view_details", "查看详情")],
            }
        if raw_status in ATTENTION_STATES or readiness_state == "ozon_reference_needs_retry":
            error = friendly_pipeline_error(status)
            return {
                "schema_version": "1.0.0",
                "kind": "ozon_reference",
                "state": "ozon_reference_needs_retry",
                "tone": "warning",
                "title": "参考图片需要续跑",
                "message": error.get("message") or "参考图片没有完整生成；已生成的图片会保留，继续生产只补缺失图片。",
                "progress_label": "可继续补图",
                "blocking": False,
                "primary_action": action("continue_reference_images", "继续补图"),
                "secondary_actions": [action("view_details", "查看详情")],
            }
        return {
            "schema_version": "1.0.0",
            "kind": "ozon_reference",
            "state": "ozon_reference_draft",
            "tone": "idle",
            "title": "Ozon参考草稿",
            "message": "已采集 Ozon 参考商品信息，可继续生成我方实拍风商品图。",
            "progress_label": "待生成参考图片",
            "blocking": False,
            "primary_action": action("continue_reference_images", "生成参考图片"),
            "secondary_actions": [action("view_details", "查看详情")],
        }

    if readiness.get("blocking"):
        return {
            "schema_version": "1.0.0",
            "kind": "workbench_collection",
            "state": readiness_state or "blocked",
            "tone": "danger",
            "title": "生产被阻断",
            "message": (readiness.get("errors") or [readiness.get("message") or "当前商品资料不符合生产要求。"])[0],
            "progress_label": "需要处理",
            "blocking": True,
            "primary_action": action("view_details", "查看处理方法"),
            "secondary_actions": [],
        }
    if issue_summary.get("has_issues") and raw_status in (TERMINAL_PUBLICATION_STATES | REMOTE_PENDING_PUBLICATION_STATES):
        issue_bucket = str(issue_summary.get("primary_bucket") or "").lower()
        primary_action = "view_details"
        primary_label = "查看处理方法"
        if issue_bucket == "image_link":
            primary_label = "查看图片问题"
        elif issue_bucket in {"numeric_contract", "logistics_weight"}:
            primary_label = "查看字段问题"
        elif issue_bucket == "duplicate_spu":
            primary_label = "查看重复问题"
        return {
            "schema_version": "1.0.0",
            "kind": "workbench_collection",
            "state": "ozon_issue_reported",
            "tone": "warning",
            "title": str(issue_summary.get("primary_label") or "Ozon 返回问题"),
            "message": str(issue_summary.get("message") or "Ozon 返回了需要查看的问题。"),
            "progress_label": "Ozon有提示",
            "blocking": False,
            "primary_action": action(primary_action, primary_label),
            "secondary_actions": [action("view_details", "查看详情")],
        }
    if raw_status in ATTENTION_STATES:
        error = friendly_pipeline_error(status)
        return {
            "schema_version": "1.0.0",
            "kind": "workbench_collection",
            "state": "needs_attention",
            "tone": "danger" if (risk.get("level") == "high") else "warning",
            "title": error.get("title") or "需要处理",
            "message": error.get("message") or "当前商品有失败步骤，需要继续处理。",
            "progress_label": "需要处理",
            "blocking": False,
            "primary_action": action("continue_production", error.get("action") or "查看并继续"),
            "secondary_actions": [action("view_details", "查看详情")],
        }
    if raw_status in TERMINAL_PUBLICATION_STATES or readiness_state == "terminal_publication":
        product_id = str(((status.get("ozon") or {}).get("product_id")) or "").strip()
        product_note = f"；Ozon商品ID：{product_id}" if product_id and product_id.lower() != "unknown" else "。"
        return {
            "schema_version": "1.0.0",
            "kind": "workbench_collection",
            "state": "terminal_publication",
            "tone": "ok",
            "title": "Ozon已创建",
            "message": f"商品已创建成功，本地只保留记录，不能重复上传{product_note}",
            "progress_label": "已完成",
            "blocking": False,
            "primary_action": action("view_details", "查看上架结果"),
            "secondary_actions": [],
        }
    if readiness_state in {"submitted_read_only", "legacy_submitted_read_only"}:
        return {
            "schema_version": "1.0.0",
            "kind": "workbench_collection",
            "state": readiness_state,
            "tone": "ok",
            "title": "已提交 Ozon",
            "message": readiness.get("message") or "商品已提交 Ozon，本地不会重复上传。",
            "progress_label": "Ozon已接收",
            "blocking": False,
            "primary_action": action("read_only_status_query", "查询Ozon结果"),
            "secondary_actions": [action("view_details", "查看详情")],
        }
    if (
        str(status.get("ai_service_state") or "") == "waiting_for_recovery"
        and raw_status in {"PROCESSING", "QUEUED", "RUNNING"}
    ):
        return {
            "schema_version": "1.0.0",
            "kind": "workbench_collection",
            "state": "waiting_for_ai_service",
            "tone": "warning",
            "title": "AI设计等待",
            "message": str(status.get("error_message") or "联网模型正在恢复，已保留断点。"),
            "progress_label": "可继续",
            "blocking": False,
            "primary_action": action("continue_production", "继续生成"),
            "secondary_actions": [action("view_details", "查看详情")],
        }
    if raw_status in {"PROCESSING", "QUEUED", "UPLOADING", "RUNNING"}:
        return {
            "schema_version": "1.0.0",
            "kind": "workbench_collection",
            "state": "running",
            "tone": "running",
            "title": "生产中",
            "message": "商品正在后台生产；打开详情可以查看实时步骤和图片。",
            "progress_label": "后台生产中",
            "blocking": False,
            "primary_action": action("production_running", "生产中", False, "后台正在运行"),
            "secondary_actions": [action("view_details", "查看详情")],
        }
    return {
        "schema_version": "1.0.0",
        "kind": "workbench_collection",
        "state": readiness_state or raw_status.lower(),
        "tone": "idle",
        "title": "可继续生产",
        "message": readiness.get("message") or "当前商品可以继续执行生产流程。",
        "progress_label": "等待继续",
        "blocking": False,
        "primary_action": action("continue_production", product_primary_action({"status": status}).get("label") or "继续生产"),
        "secondary_actions": [action("view_details", "查看详情")],
    }


def calculate_risk(
    status: Dict[str, Any],
    category: Dict[str, Any],
    attributes: Dict[str, Any],
    qc: Dict[str, Any],
    *,
    analysis: Optional[Dict[str, Any]] = None,
    readiness_state: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    items: List[Dict[str, str]] = []
    raw_status = str(status.get("status") or "").upper()
    if raw_status in TERMINAL_PUBLICATION_STATES or raw_status in REMOTE_PENDING_PUBLICATION_STATES:
        issue_summary = (
            status.get("ozon_issue_summary")
            or (status.get("ozon") or {}).get("issue_summary")
            or {}
        )
        if not issue_summary.get("has_issues"):
            return {"level": "low", "items": []}
        bucket = str(issue_summary.get("primary_bucket") or "ozon_issue")
        message = str(issue_summary.get("message") or "Ozon 返回了需要查看的问题。")
        label = str(issue_summary.get("primary_label") or "Ozon 返回问题")
        return {
            "level": "medium",
            "items": [{
                "level": "medium",
                "code": bucket,
                "title": label,
                "message": message,
                "action": str(issue_summary.get("primary_action") or "查看处理方法"),
                "tab": "ozon",
            }],
        }
    if str(status.get("status") or "").upper() in ATTENTION_STATES:
        error = friendly_pipeline_error(status)
        items.append({
            "level": "high", "code": "pipeline_failed",
            "message": error["message"], "title": error["title"],
            "action": error["action"], "tab": error["tab"],
            "technical": error["technical"], "step": error["step"],
        })
    missing = attributes.get("missing_required_attributes") or []
    if missing:
        names = "、".join(str(item.get("attribute_name") or item.get("attribute_id")) for item in missing[:4])
        items.append({"level": "high", "code": "required_attributes", "message": f"缺少必填属性：{names}"})
    if qc.get("decision") == "reject":
        items.append({"level": "high", "code": "image_qc", "message": f"图片质检未通过，得分 {qc.get('score', '未知')}"})
    for index, item in enumerate((analysis or {}).get("risks") or []):
        message = str(item.get("message") or "").strip()
        if not message:
            continue
        level = str(item.get("level") or "medium").lower()
        items.append({
            "level": level if level in {"low", "medium", "high"} else "medium",
            "code": f"analysis_{item.get('area') or index}",
            "title": "商品分析风险",
            "message": message,
            "blocking": bool(item.get("blocking")),
        })
    if (readiness_state or {}).get("blocking"):
        items.append({
            "level": "high",
            "code": "formal_input_blocked",
            "title": "输入资料不符合当前流程",
            "message": str(readiness_state.get("message") or "当前商品不能继续运行或上传"),
        })
    elif (readiness_state or {}).get("state") == "legacy_submitted_read_only":
        items.append({
            "level": "medium",
            "code": "legacy_submitted_read_only",
            "title": "旧流程提交记录",
            "message": str(readiness_state.get("message")),
        })
    confidence = category.get("confidence")
    if isinstance(confidence, (int, float)) and confidence < 0.8:
        items.append({"level": "medium", "code": "category_confidence", "message": f"类目置信度较低：{confidence:.0%}"})
    if any(item["level"] == "high" for item in items):
        level = "high"
    elif items:
        level = "medium"
    else:
        level = "low"
    return {"level": level, "items": items}


def prelisting_assessment(pricing: Dict[str, Any], qc: Dict[str, Any], risk: Dict[str, Any]) -> Dict[str, Any]:
    sku_prices = pricing.get("sku_pricing") or []
    profits = [item.get("estimated_profit_cny") for item in sku_prices if isinstance(item.get("estimated_profit_cny"), (int, float))]
    profit_rates = [item.get("profit_rate_markup") for item in sku_prices if isinstance(item.get("profit_rate_markup"), (int, float))]
    profit_score = min(100, max(0, round(45 + (max(profit_rates or [0]) * 70))))
    image_score = int(qc.get("score") or (85 if qc.get("decision") == "pass" else 55))
    risk_penalty = {"low": 4, "medium": 22, "high": 48}.get(str(risk.get("level")), 30)
    market_score = max(20, 86 - risk_penalty)
    competition_risk = min(100, 32 + risk_penalty)
    return_risk = min(100, 24 + risk_penalty)
    overall = round((profit_score * .32) + (market_score * .24) + (image_score * .24) + ((100 - competition_risk) * .1) + ((100 - return_risk) * .1))
    advice = "优先处理" if overall >= 80 else "可以测试" if overall >= 58 else "暂缓处理"
    selling_prices = [item.get("selling_price_rub") for item in sku_prices if isinstance(item.get("selling_price_rub"), (int, float))]
    costs = [item.get("base_cost_cny") for item in sku_prices if isinstance(item.get("base_cost_cny"), (int, float))]
    exchange_value = pricing.get("exchange_rate") or 12
    if isinstance(exchange_value, dict):
        exchange_value = exchange_value.get("cny_to_rub") or exchange_value.get("value") or 12
    exchange = float(exchange_value)
    rule_price = min(selling_prices) if selling_prices else None
    break_even = round(max(costs or [0]) * exchange * 1.31) if costs else None
    return {
        "profit_potential": profit_score, "russia_fit": market_score, "image_sales_potential": image_score,
        "competition_risk": competition_risk, "return_risk": return_risk,
        "overall_score": overall, "advice": advice,
        "pricing_advice": {
            "break_even_price_rub": break_even, "rule_price_rub": rule_price,
            "suggested_range_rub": [round(rule_price * .97), round(rule_price * 1.08)] if rule_price else [],
            "high_profit_test_price_rub": round(rule_price * 1.12) if rule_price else None,
            "estimated_profit_cny": round(min(profits), 2) if profits else None,
            "minimum_rules_respected": True,
        },
        "source": "现有成本、定价、图片质检和风险结果的价格建议，不含销量预测",
    }


def build_ai_suggestions(product_dir: Path, content: Dict[str, Any], risk: Dict[str, Any], qc: Dict[str, Any]) -> List[Dict[str, Any]]:
    candidates: List[Dict[str, Any]] = []
    if len(content.get("tags") or []) != 30:
        candidates.append({"id": "tag_count", "type": "copy", "title": "补齐30个俄文主题标签", "detail": "每个标签单独保存且不超过30个字符。"})
    if qc.get("decision") == "reject":
        candidates.append({"id": "image_qc", "type": "image", "title": "仅重做未通过图片", "detail": "保留合格图片，不重做整套。"})
    for item in risk.get("items") or []:
        candidates.append({"id": f"risk_{item.get('code')}", "type": "risk", "title": "处理商品问题", "detail": item.get("message")})
    for candidate in candidates:
        candidate.update({
            "status": "accept",
            "non_blocking": True,
            "auto_applied": True,
            "applied_by": "automatic_ai_suggestion_policy",
        })
    return candidates


def pending_product_question(product_dir: Path) -> Dict[str, Any]:
    value = load_optional_json(product_dir / "input/pending-question.json")
    return value if str(value.get("status") or "").upper() == "OPEN" else {}


def is_auto_upload_ready_status(status_payload: Dict[str, Any]) -> bool:
    status = str(status_payload.get("status") or "unknown").upper()
    if status != "WAITING_MANUAL_REVIEW":
        return False
    pending_steps = {str(item).lower() for item in (status_payload.get("pending_steps") or [])}
    next_action = str(status_payload.get("next_action") or "").lower()
    return (
        status_payload.get("task_authorized") is True
        and status_payload.get("auto_upload") is True
        and status_payload.get("manual_confirmation_required") is not True
        and (next_action == "ozon_upload" or "ozon_upload" in pending_steps)
    )


def product_primary_action(detail: Dict[str, Any]) -> Dict[str, str]:
    status_payload = detail.get("status") or {}
    status = str(status_payload.get("status") or "unknown").upper()
    failed_step = str(status_payload.get("failed_step") or status_payload.get("current_step") or "").lower()
    next_action = str(status_payload.get("next_action") or "").lower()
    current_step = str(status_payload.get("current_step") or detail.get("current_step") or "").lower()
    pending_steps = {str(item).lower() for item in (status_payload.get("pending_steps") or [])}
    active_pipeline_steps = {"product_analysis", "category_match", "variant_rules", "measurements",
                             "offer_exists_check", "upload_feasibility", "product_positioning",
                             "ecommerce_design", "russian_copy", "field_completion", "image_plan",
                             "image_generation", "image_qc", "ozon_upload"}
    has_real_failure = status in ATTENTION_STATES and failed_step not in {"", "unknown", "none"}
    awaiting_pipeline_step = (
        status_payload.get("task_authorized") is True
        and not has_real_failure
        and status not in {"COLLECTED", "STOPPED", "WAITING_MANUAL_REVIEW", "CREATED", "UPLOADED", "ACTIVE", "HANDED_OFF_TO_OZON"}
        and (
            current_step in active_pipeline_steps
            or next_action in active_pipeline_steps
            or bool(pending_steps & active_pipeline_steps)
        )
    )
    if detail.get("pending_question"):
        return {"key": "answer", "label": "回答问题"}
    if status == "COLLECTED":
        return {"key": "run", "label": "运行任务"}
    if status == "STOPPED":
        return {"key": "fix", "label": "继续生成"}
    if awaiting_pipeline_step:
        if running_batch_pid() is not None:
            return {"key": "status", "label": "查看进度"}
        return {"key": "fix", "label": "继续生成"}
    if status in ATTENTION_STATES and (
        failed_step in {"ozon_upload", "manual_ozon_upload"}
        or next_action in {"ozon_upload", "retry_failed_store", "retry_failed_stores"}
    ):
        return {"key": "review_upload", "label": "重新上传"}
    if status in ATTENTION_STATES:
        return {"key": "fix", "label": "查看并继续"}
    if status in {"CATEGORY_MATCHED", "CONTENT_GENERATED", "IMAGES_GENERATED", "PRICED"}:
        return {"key": "fix", "label": "继续生成"}
    if status == "WAITING_MANUAL_REVIEW":
        if is_auto_upload_ready_status(status_payload):
            if running_batch_pid() is not None:
                return {"key": "status", "label": "查看进度"}
            return {"key": "fix", "label": "继续生产"}
        return {"key": "review_upload", "label": "确认上传"}
    if status == "PARTIAL":
        return {"key": "review_upload", "label": "继续上传未完成店铺"}
    if status in {"HANDED_OFF_TO_OZON", "SUBMITTED", "UPLOADING", "PENDING_REMOTE", "OZON_MODERATION"}:
        return {"key": "status", "label": "查询Ozon结果"}
    if status in {"CREATED", "UPLOADED", "ACTIVE"}:
        return {"key": "result", "label": "查看上架结果"}
    if status == "PARTIAL_FAILED":
        return {"key": "retry_failed_store", "label": "仅重试失败店铺"}
    return {"key": "status", "label": "查看进度"}


def workbench_product_detail(product_id: str) -> Dict[str, Any]:
    product_dir = workbench_product_dir(product_id)
    source = load_optional_json(product_dir / "input/source.json")
    if source.get("source_kind") == "manual_test":
        raise HTTPException(status_code=404, detail="手动测试样品不属于正式工作台商品")
    status = effective_product_status(product_dir, load_optional_json(product_dir / "status.json"))
    analysis = load_optional_json(product_dir / "output/product-analysis.json")
    copy = load_optional_json(product_dir / "output/copy-ru.json")
    title_data = load_optional_json(product_dir / "output/title-ru.json")
    description_data = load_optional_json(product_dir / "output/description-ru.json")
    category = load_optional_json(product_dir / "output/ozon-category.json")
    selected_category = load_optional_json(product_dir / "input/category-selection.json")
    selected_catalog_category: Dict[str, Any] = {}
    if selected_category:
        try:
            selected_catalog_category = get_category(
                ROOT, int(selected_category.get("category_id")), int(selected_category.get("type_id"))
            )
        except (TypeError, ValueError):
            selected_catalog_category = {}
    category_name_zh = (selected_category or {}).get("category_name_zh") or selected_catalog_category.get("name_zh")
    category_path_zh = (selected_category or {}).get("category_path_zh") or selected_catalog_category.get("path_zh") or []
    if not category:
        if selected_category:
            category = {
                "category_id": selected_category.get("category_id"),
                "type_id": selected_category.get("type_id"),
                "category_name": category_name_zh or selected_category.get("category_name_ru"),
                "category_name_zh": category_name_zh,
                "category_path": selected_category.get("category_path") or [],
                "category_path_zh": category_path_zh,
                "match_status": "user_selected_at_collection",
                "confidence": 1.0,
                "rules_snapshot_hash": selected_category.get("rules_snapshot_hash"),
            }
    elif selected_category:
        category["category_name_zh"] = category_name_zh
        category["category_path_zh"] = category_path_zh
    attributes = workbench_attribute_view(product_dir)
    final_attributes = load_optional_json(product_dir / "output/ozon-attributes-final.json")
    pricing = load_optional_json(product_dir / "output/pricing-result.json")
    plan = load_optional_json(product_dir / "output/image-plan.json")
    qc = load_optional_json(product_dir / "output/image-qc-report.json")
    upload_config = load_optional_json(product_dir / "output/ozon-upload-config.json")

    def _positive_number(value: Any) -> Optional[float]:
        if isinstance(value, bool) or value in {None, "", "unknown"}:
            return None
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if number > 0 else None

    def _dimensions_mm_to_cm(value: Any) -> Optional[Dict[str, float]]:
        if not isinstance(value, dict):
            return None
        result: Dict[str, float] = {}
        for source_key, target_key in (("length_mm", "length"), ("width_mm", "width"), ("height_mm", "height")):
            number = _positive_number(value.get(source_key))
            if number is None:
                return None
            result[target_key] = round(number / 10, 1)
        return result

    product_weight_g = _positive_number((upload_config.get("product_weight") or {}).get("value_g"))
    package_weight_g = _positive_number((upload_config.get("package_weight") or {}).get("value_g"))
    product_dimensions_cm = _dimensions_mm_to_cm(upload_config.get("product_dimensions"))
    package_dimensions_cm = _dimensions_mm_to_cm(upload_config.get("package_dimensions"))
    package_check_available = bool(product_weight_g or package_weight_g or product_dimensions_cm or package_dimensions_cm)
    package_check_passed = (
        bool(product_weight_g and package_weight_g and package_weight_g > product_weight_g)
        and bool(product_dimensions_cm and package_dimensions_cm)
        and all(
            package_dimensions_cm[key] > product_dimensions_cm[key]
            for key in ("length", "width", "height")
        )
    )
    package_check = {
        "available": package_check_available,
        "passed": package_check_passed if package_check_available else False,
        "message": (
            "包装重量和长宽高都大于商品本体"
            if package_check_passed
            else "还没有完整通过包装检查" if package_check_available
            else "价格包装数据未生成"
        ),
        "product_weight_g": product_weight_g,
        "package_weight_g": package_weight_g,
        "product_dimensions_cm": product_dimensions_cm,
        "package_dimensions_cm": package_dimensions_cm,
    }
    source_confirmed_measurements = any(
        "source.product_attributes.sku_measurement_table" in str((upload_config.get(field) or {}).get("source") or "")
        for field in ("product_weight", "product_dimensions", "package_weight", "package_dimensions")
    )
    draft = load_optional_json(product_dir / "output/workbench-draft.json")
    manual_attributes = draft.get("attributes") or {}
    for item in attributes.get("attributes") or []:
        attribute_id = str(item.get("attribute_id"))
        if attribute_id in manual_attributes:
            if is_system_model_attribute(item):
                continue
            if is_measurement_attribute(item) and source_confirmed_measurements:
                continue
            item["value"] = manual_attributes[attribute_id]
            item["source"] = "人工修改"
            item["validation_status"] = "pending_dictionary_validation"
    apply_stable_model_attributes(product_dir, source, attributes)
    sku_overrides = draft.get("sku_overrides") or {}
    for item in pricing.get("sku_pricing") or []:
        override = sku_overrides.get(str(item.get("sku_id"))) or {}
        for field in ("selling_price_cny", "selling_price_rub"):
            if field in override:
                item[field] = override[field]
    rich = {}
    for name in ("ozon-rich-content.json", "rich-content.json", "ozon-draft.json"):
        candidate = load_optional_json(product_dir / "output" / name)
        if candidate:
            rich = candidate
            break
    final_tags = load_optional_json(product_dir / "output/ozon-tags.json")
    tags = final_tags.get("tags") or copy.get("keywords_ru") or copy.get("keywords") or []
    if not isinstance(tags, list):
        tags = [str(tags)]
    tags = [value if str(value).startswith("#") else f"#{value}" for value in tags]
    content = {
        "title_ru": title_data.get("title_ru") or copy.get("title_ru") or "",
        "title_zh_reference": source.get("title_cn") or "unknown",
        "short_title": copy.get("short_title") or "",
        "description_ru": description_data.get("description_ru") or copy.get("description_ru") or copy.get("description") or "",
        "description_zh_reference": "；".join(
            str(item.get("text") if isinstance(item, dict) else item)
            for item in (analysis.get("selling_points") or [])[:6]
            if str(item.get("text") if isinstance(item, dict) else item).strip()
        ) or source.get("title_cn") or "unknown",
        "bullets_ru": copy.get("bullets_ru") or [],
        "tags": tags,
    }
    for field in WORKBENCH_EDITABLE_FIELDS:
        if field in draft:
            content[field] = draft[field]
    merged_facts = load_optional_json(product_dir / "output/merged-product-facts.json")
    if not merged_facts.get("sku_rows"):
        try:
            from product_fact_merger import merge_product_facts  # noqa: WPS433 - loaded only for workbench display
            merged_facts = merge_product_facts(product_dir)
        except Exception:
            merged_facts = {}
    sku_rows_by_id = {
        str(item.get("sku_id")): item
        for item in merged_facts.get("sku_rows") or []
        if item.get("sku_id") is not None
    }
    final_attrs_by_sku = final_attributes.get("attributes_by_sku") or {}
    category_dynamic_attrs = workbench_category_dynamic_attributes(product_dir)
    sku_pricing = {str(item.get("sku_id")): item for item in pricing.get("sku_pricing") or []}
    selected_sku_ids = [
        str(item.get("sku_id") or "")
        for item in source.get("skus") or []
        if str(item.get("sku_id") or "")
    ]
    sku_image_bindings = load_sku_image_bindings(product_dir, selected_sku_ids, strict=False)
    sku_image_binding_candidates = workbench_sku_image_binding_candidates(product_dir, source)
    candidate_urls = {
        str(item.get("path") or ""): str(item.get("url") or "")
        for item in sku_image_binding_candidates
        if item.get("path") and item.get("url")
    }
    image_source_preflight = load_optional_json(product_dir / "output/image-source-preflight.json")
    preflight_blocked_sku_ids = {
        str(value)
        for value in image_source_preflight.get("blocked_sku_ids") or []
        if str(value or "").strip()
    }
    skus = []
    for index, sku in enumerate(source.get("skus") or []):
        price = sku_pricing.get(str(sku.get("sku_id"))) or {}
        sku_id = str(sku.get("sku_id"))
        sku_row_data = sku_rows_by_id.get(sku_id) or {}
        options = sku.get("option_values") or []
        option_labels = []
        for value in options:
            label = str(
                value.get("value_cn") or value.get("value") or ""
                if isinstance(value, dict) else value
            ).strip()
            if label:
                option_labels.append(label)
        option_text = " / ".join(option_labels)
        source_data = sku.get("source_data") or {}
        dimensions_cm = source_data.get("external_dimensions_cm") if isinstance(source_data.get("external_dimensions_cm"), dict) else {}
        sku_row_data = copy_module.deepcopy(sku_row_data)
        sku_binding = sku_image_bindings.get(sku_id)
        sku_owned_image_missing = bool(sku.get("sku_image_missing"))
        sku_reference_blocked = sku_id in preflight_blocked_sku_ids
        sku_owned_image_url = "" if sku_owned_image_missing else f"/api/workbench/products/{product_id}/source-images/sku/{index}"
        sku_bound_image_url = candidate_urls.get(str((sku_binding or {}).get("selected_image_path") or ""))
        sku_image_url = sku_owned_image_url or sku_bound_image_url or ""
        dynamic_attributes = sku_row_data.setdefault("dynamic_attributes", {})
        for attribute_id, category_attr in category_dynamic_attrs.items():
            dynamic_attributes.setdefault(attribute_id, copy_module.deepcopy(category_attr))
        for final_attr in final_attrs_by_sku.get(sku_id) or []:
            if not isinstance(final_attr, dict):
                continue
            attribute_id = str(final_attr.get("attribute_id") or "").strip()
            if not attribute_id:
                continue
            dynamic_attributes[attribute_id] = {
                **final_attr,
                "canonical_value": final_attr.get("canonical_value", final_attr.get("target_value", final_attr.get("value"))),
                "canonical_unit": final_attr.get("canonical_unit") or final_attr.get("target_unit") or "ozon",
            }
        skus.append({
            "sku_id": sku.get("sku_id"), "name": sku.get("sku_name"),
            "options": options, "option_text": option_text, "purchase_price_cny": sku.get("purchase_price"),
            "selling_price_cny": price.get("selling_price_cny"),
            "selling_price_rub": price.get("selling_price_rub"),
            "profit_cny": price.get("estimated_profit_cny"),
            "profit_rate": price.get("profit_rate_markup"),
            "weight_g": ((price.get("shipping") or {}).get("weight") or {}).get("actual_weight_g"),
            "capacity_ml": source_data.get("capacity_ml"),
            "dimensions_cm": dimensions_cm,
            "offer_id": ((status.get("ozon") or {}).get("offer_id") or "unknown"),
            "variant_decision": analysis.get("variant_decision") or analysis.get("grouping_decision") or "按Ozon变体规则",
            "aspect_basis": analysis.get("is_aspect_basis") or category.get("variant_basis") or "以当前类目is_aspect规则为准",
            "image_missing": bool((sku_owned_image_missing or sku_reference_blocked) and not sku_binding),
            "binding_required": bool((sku_owned_image_missing or sku_reference_blocked) and not sku_binding),
            "binding_status": "bound_reference" if sku_binding else "missing_sku_image" if sku_owned_image_missing or sku_reference_blocked else "sku_owned_image",
            "image_binding": sku_binding,
            "image_url": sku_image_url,
            "sku_row": sku_row_data,
        })
    readiness_state = production_readiness_state(product_dir, status, plan)
    risk = calculate_risk(
        status, category, attributes, qc,
        analysis=analysis,
        readiness_state=readiness_state,
    )
    stores = list_stores(ROOT)
    publications = load_publications(product_dir, [store["id"] for store in stores])
    assessment = prelisting_assessment(pricing, qc, risk)
    images = workbench_images(product_dir, plan, qc, status)
    pipeline_progress = workbench_pipeline_progress(product_dir, status, images)
    ui_state = product_ui_state(product_dir, status, readiness_state, risk, images)
    assets = asset_inventory(product_dir)
    for bucket, values in assets.items():
        for value in values:
            value["url"] = (
                f"/api/workbench/products/{product_id}/assets/{bucket}/"
                + urllib.parse.quote(value["path"], safe="/")
            )
    detail = {
        "product_id": product_id,
        # Keep detail and list responses on the same public status contract.
        # The UI must never have to infer a product state from nested data.
        "raw_status": status.get("status") or "unknown",
        "current_step": status.get("current_step") or "queue",
        "source": {
            "title_cn": source.get("title_cn") or "unknown", "source_url": source.get("source_url") or "unknown",
            "captured_at": source.get("captured_at") or "unknown", "main_image_count": len(source.get("main_images") or []),
            "detail_image_count": len(source.get("detail_images") or []),
            "product_id": source.get("product_id") or product_id,
            "collection_id": source.get("collection_id") or "unknown",
            "source_kind": source.get("source_kind") or "unknown",
        },
        "status": status,
        "batch_running": running_batch_pid() is not None,
        "public_state": public_state(status.get("status")),
        "handoff_message": "已提交Ozon，正在等待Ozon生成商品卡；本地可执行只读状态查询。" if str(status.get("status") or "").upper() in {"HANDED_OFF_TO_OZON", "PENDING_REMOTE"} else None,
        "progress": int(status.get("progress") or 0),
        "pipeline_progress": pipeline_progress,
        "content": content,
        "draft": {"version": int(draft.get("version") or 0), "saved_at": draft.get("saved_at"), "locked_fields": draft.get("locked_fields") or []},
        "analysis": analysis,
        "merged_facts": merged_facts,
        "category": category,
        "attributes": attributes,
        "pricing": pricing,
        "package_check": package_check,
        "skus": skus,
        "sku_image_binding_candidates": sku_image_binding_candidates,
        "images": images,
        "image_assets": assets,
        "image_contract": {
            "selected_sku_count": len(skus),
            "expected_main_count": len(skus),
            "expected_shared_detail_count": 8,
            "expected_total_count": len(skus) + 8,
            "actual_main_count": sum(item.get("type") == "main" for item in images),
            "actual_shared_detail_count": sum(item.get("type") != "main" for item in images),
        },
        "image_groups": build_sku_image_groups(
            skus, images, exact_binding=asset_boundaries_enabled(product_dir),
        ),
        "image_qc": qc,
        "production_readiness": readiness_state,
        "ui_state": ui_state,
        "rich_content": rich,
        "risk": risk,
        "error": friendly_pipeline_error(status) if str(status.get("status") or "").upper() in ATTENTION_STATES else None,
        "prelisting_assessment": assessment,
        "stores": stores,
        "publications": publications,
        "publication_summary": publication_summary(publications),
        "ai_suggestions": build_ai_suggestions(product_dir, content, risk, qc),
        "ozon": status.get("ozon") or {},
        "timeline": readable_timeline(status, product_dir),
        "workbench_settings": workbench_settings(),
        "owner": product_owner(product_dir),
        "pending_question": pending_product_question(product_dir),
        "visual_preference": load_optional_json(product_dir / "input/visual-preference.json", {
            "set_hint": "", "slot_hints": {},
        }),
    }
    detail["primary_action"] = product_primary_action(detail)
    detail["attention_required"] = bool(
        detail["pending_question"]
        or detail["production_readiness"].get("blocking")
        or str(status.get("status") or "").upper() in {*ATTENTION_STATES, "PARTIAL"}
        or (
            str(status.get("status") or "").upper() == "WAITING_MANUAL_REVIEW"
            and not is_auto_upload_ready_status(status)
        )
    )
    return detail


def workbench_card_context(
    product_dir: Path,
    *,
    store_ids: Optional[List[str]] = None,
    batch_running: Optional[bool] = None,
) -> Dict[str, Any]:
    """Load only the fields needed by workbench list/summary/risk views."""
    product_id = product_dir.name
    source = load_optional_json(product_dir / "input/source.json")
    if source.get("source_kind") == "manual_test":
        raise HTTPException(status_code=404, detail="手动测试样品不属于正式工作台商品")
    status = effective_product_status(product_dir, load_optional_json(product_dir / "status.json"))
    analysis = load_optional_json(product_dir / "output/product-analysis.json")
    copy_data = load_optional_json(product_dir / "output/copy-ru.json")
    title_data = load_optional_json(product_dir / "output/title-ru.json")
    category = load_optional_json(product_dir / "output/ozon-category.json")
    attributes = workbench_attribute_view(product_dir)
    pricing = load_optional_json(product_dir / "output/pricing-result.json")
    plan = load_optional_json(product_dir / "output/image-plan.json")
    qc = load_optional_json(product_dir / "output/image-qc-report.json")
    images = workbench_images(product_dir, plan, qc, status)
    pipeline_progress = workbench_pipeline_progress(product_dir, status, images)
    risk = calculate_risk(status, category, attributes, qc, analysis=analysis)
    publication_store_ids = store_ids if store_ids is not None else [store["id"] for store in list_stores(ROOT)]
    publications = load_publications(product_dir, publication_store_ids)
    publication_values = list((publications.get("stores") or {}).values())
    publication_counts = publication_summary(publications)
    pending_question = pending_product_question(product_dir)
    public_status = public_state(status.get("status"))
    primary_action = product_primary_action({
        "status": status,
        "current_step": status.get("current_step") or "queue",
        "pending_question": pending_question,
    })
    return {
        "product_id": product_id,
        "source": source,
        "status": status,
        "content": {
            "title_ru": title_data.get("title_ru") or copy_data.get("title_ru") or "",
        },
        "pricing": pricing,
        "skus": source.get("skus") or [],
        "images": images,
        "risk": risk,
        "public_state": public_status,
        "progress": int(status.get("progress") or 0),
        "pipeline_progress": pipeline_progress,
        "publication_values": publication_values,
        "publication_summary": publication_counts,
        "owner": product_owner(product_dir),
        "primary_action": primary_action,
        "batch_running": (running_batch_pid() is not None) if batch_running is None else batch_running,
        "attention_required": bool(
            pending_question
            or risk.get("level") == "high"
            or str(status.get("status") or "").upper() in {*ATTENTION_STATES, "PARTIAL"}
            or (
                str(status.get("status") or "").upper() == "WAITING_MANUAL_REVIEW"
                and not is_auto_upload_ready_status(status)
            )
        ),
        "pending_question": pending_question,
        "error": friendly_pipeline_error(status) if str(status.get("status") or "").upper() in ATTENTION_STATES else None,
        "handoff_message": "已提交Ozon，正在等待Ozon生成商品卡；本地可执行只读状态查询。" if str(status.get("status") or "").upper() in {"HANDED_OFF_TO_OZON", "PENDING_REMOTE"} else None,
        "ozon": status.get("ozon") or {},
    }


def workbench_card(
    product_dir: Path,
    *,
    store_ids: Optional[List[str]] = None,
    batch_running: Optional[bool] = None,
) -> Dict[str, Any]:
    detail = workbench_card_context(product_dir, store_ids=store_ids, batch_running=batch_running)
    price_items = detail["pricing"].get("sku_pricing") or []
    prices = [item.get("purchase_cost_cny") for item in price_items if isinstance(item.get("purchase_cost_cny"), (int, float))]
    if not prices:
        prices = [
            item.get("purchase_price")
            for item in detail["skus"]
            if isinstance(item.get("purchase_price"), (int, float))
        ]
    thumbnail_exists = any(path.is_file() for path in (product_dir / "input/main-images").glob("*"))
    remote_search = []
    for publication in detail["publication_values"]:
        remote_search.append(str(publication.get("store_id") or ""))
        for sku in publication.get("sku_publications") or []:
            remote_search.extend(str(sku.get(key) or "") for key in ("sku_id", "offer_id", "task_id", "ozon_product_id"))
    remote_search.extend(str(sku.get("sku_id") or "") for sku in detail["skus"])
    remote_search.extend([str(detail["source"].get("source_url") or ""), str(detail["ozon"].get("task_id") or ""), str(detail["ozon"].get("product_id") or "")])
    return {
        "product_id": detail["product_id"], "title_cn": detail["source"]["title_cn"],
        "title_ru": detail["content"]["title_ru"], "source_url": detail["source"]["source_url"],
        "captured_at": detail["source"]["captured_at"], "state": detail["public_state"],
        "workflow_bucket": workflow_bucket(detail["status"].get("status")),
        "status": detail["status"].get("status") or "unknown",
        "raw_status": detail["status"].get("status") or "unknown", "current_step": detail["status"].get("current_step") or "queue", "progress": detail["progress"],
        "pipeline_progress": detail["pipeline_progress"],
        "sku_count": len(detail["skus"]), "purchase_price_cny": min(prices) if prices else None,
        "risk": detail["risk"], "image_count": len([item for item in detail["images"] if item.get("url")]),
        "error": detail.get("error"),
        "handoff_message": detail.get("handoff_message"),
        "thumbnail_url": f"/api/inbox/products/{detail['product_id']}/thumbnail" if thumbnail_exists else None,
        "batch_id": detail["status"].get("batch_id") or "unknown",
        "selected_store_count": detail["publication_summary"]["selected"],
        "search_terms": " ".join(remote_search),
        "owner": detail["owner"],
        "primary_action": detail["primary_action"],
        "batch_running": detail.get("batch_running", False),
        "attention_required": detail["attention_required"],
        "pending_question": detail["pending_question"],
    }


def workbench_list_card(
    product_dir: Path,
    *,
    store_ids: Optional[List[str]] = None,
    batch_running: Optional[bool] = None,
    task_snapshot: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build the polling list card without loading full attributes or image QC.

    The product detail endpoint remains the source for the complete category,
    image and quality-check view.  The list is refreshed every few seconds
    while a batch runs, so it must only read the small status and copy files.
    """
    product_id = product_dir.name
    source = load_optional_json(product_dir / "input/source.json")
    if source.get("source_kind") == "manual_test":
        raise HTTPException(status_code=404, detail="手动测试样品不属于正式工作台商品")
    status = effective_product_status(
        product_dir,
        load_optional_json(product_dir / "status.json"),
        task_snapshot=task_snapshot,
    )
    pricing = load_optional_json(product_dir / "output/pricing-result.json")
    copy_data = load_optional_json(product_dir / "output/copy-ru.json")
    title_data = load_optional_json(product_dir / "output/title-ru.json")
    # These small JSON summaries preserve real risk badges without invoking
    # the full attribute/image rendering path used by the detail drawer.
    analysis = load_optional_json(product_dir / "output/product-analysis.json")
    category = load_optional_json(product_dir / "output/ozon-category.json")
    final_attributes = load_optional_json(product_dir / "output/ozon-attributes-final.json")
    attributes = final_attributes if final_attributes.get("attributes") else load_optional_json(product_dir / "output/ozon-attributes.json")
    qc = load_optional_json(product_dir / "output/image-qc-report.json")
    skus = source.get("skus") or []
    plan = load_optional_json(product_dir / "output/image-plan.json")
    images = workbench_images(product_dir, plan, qc, status)
    pipeline_progress = workbench_pipeline_progress(product_dir, status, images)
    price_items = pricing.get("sku_pricing") or []
    prices = [
        item.get("purchase_cost_cny")
        for item in price_items
        if isinstance(item, dict) and isinstance(item.get("purchase_cost_cny"), (int, float))
    ]
    if not prices:
        prices = [
            item.get("purchase_price")
            for item in skus
            if isinstance(item, dict) and isinstance(item.get("purchase_price"), (int, float))
        ]
    publication_store_ids = store_ids if store_ids is not None else [store["id"] for store in list_stores(ROOT)]
    if task_snapshot is None:
        publications = load_publications(product_dir, publication_store_ids)
    else:
        configured_store_ids = set(publication_store_ids)
        sku_by_publication: Dict[Any, List[Dict[str, Any]]] = {}
        for item in task_snapshot.get("sku_publications") or []:
            if isinstance(item, dict):
                sku_by_publication.setdefault(item.get("publication_id"), []).append(dict(item))
        stores: Dict[str, Dict[str, Any]] = {}
        for item in task_snapshot.get("stores") or []:
            if not isinstance(item, dict):
                continue
            store_id = str(item.get("store_id") or "")
            if not store_id or store_id not in configured_store_ids:
                continue
            stores[store_id] = {
                **item,
                "sku_publications": sku_by_publication.get(item.get("id"), []),
            }
        publications = {"stores": stores}
    publication_values = list((publications.get("stores") or {}).values())
    publication_counts = publication_summary(publications)
    pending_question = pending_product_question(product_dir)
    raw_status = str(status.get("status") or "unknown")
    raw_status_upper = raw_status.upper()
    attention_required = bool(
        pending_question
        or raw_status_upper in {*ATTENTION_STATES, "PARTIAL"}
        or (
            raw_status_upper == "WAITING_MANUAL_REVIEW"
            and not is_auto_upload_ready_status(status)
        )
    )
    risk = calculate_risk(status, category, attributes, qc, analysis=analysis)
    attention_required = attention_required or risk.get("level") == "high"
    remote_search = []
    for publication in publication_values:
        remote_search.append(str(publication.get("store_id") or ""))
        for sku in publication.get("sku_publications") or []:
            if isinstance(sku, dict):
                remote_search.extend(str(sku.get(key) or "") for key in ("sku_id", "offer_id", "task_id", "ozon_product_id"))
    remote_search.extend(str(sku.get("sku_id") or "") for sku in skus if isinstance(sku, dict))
    ozon = status.get("ozon") or {}
    remote_search.extend([
        str(source.get("source_url") or ""),
        str(ozon.get("task_id") or ""),
        str(ozon.get("product_id") or ""),
    ])
    thumbnail_exists = any(path.is_file() for path in (product_dir / "input/main-images").glob("*"))
    image_count = status.get("completed_image_slots") or status.get("image_count") or len([item for item in images if item.get("url")])
    if not isinstance(image_count, int):
        image_count = 0
    return {
        "product_id": product_id,
        "title_cn": source.get("title_cn") or product_id,
        "title_ru": title_data.get("title_ru") or copy_data.get("title_ru") or "",
        "source_url": source.get("source_url") or "",
        "captured_at": source.get("captured_at") or "",
        "state": public_state(raw_status),
        "workflow_bucket": workflow_bucket(raw_status),
        "status": raw_status,
        "raw_status": raw_status,
        "current_step": status.get("current_step") or "queue",
        "progress": int(status.get("progress") or 0),
        "pipeline_progress": pipeline_progress,
        "sku_count": len(skus),
        "purchase_price_cny": min(prices) if prices else None,
        "risk": risk,
        "image_count": image_count,
        "error": friendly_pipeline_error(status) if raw_status_upper in ATTENTION_STATES else None,
        "handoff_message": "已提交Ozon，正在等待Ozon生成商品卡；本地可执行只读状态查询。" if raw_status_upper in {"HANDED_OFF_TO_OZON", "PENDING_REMOTE"} else None,
        "thumbnail_url": f"/api/inbox/products/{product_id}/thumbnail" if thumbnail_exists else None,
        "batch_id": status.get("batch_id") or "unknown",
        "selected_store_count": publication_counts["selected"],
        "search_terms": " ".join(remote_search),
        "owner": product_owner(product_dir),
        "primary_action": product_primary_action({
            "status": status,
            "current_step": status.get("current_step") or "queue",
            "pending_question": pending_question,
        }),
        "batch_running": (running_batch_pid() is not None) if batch_running is None else batch_running,
        "attention_required": attention_required,
        "pending_question": pending_question,
    }


_WORKBENCH_CARD_CACHE_LOCK = threading.Lock()
_WORKBENCH_CARD_CACHE: Dict[str, Any] = {
    "context": None,
    "entries": {},
}


def workbench_card_fingerprint(
    product_dir: Path,
    task_snapshot: Optional[Dict[str, Any]] = None,
) -> Tuple[Any, ...]:
    """Return the small set of files that can change a list-card response."""
    relative_paths = (
        "input/source.json",
        "status.json",
        "output/copy-ru.json",
        "output/title-ru.json",
        "output/pricing-result.json",
        "output/product-analysis.json",
        "output/ozon-category.json",
        "output/ozon-attributes.json",
        "output/ozon-attributes-final.json",
        "output/image-qc-report.json",
    )
    files = tuple(
        (relative_path, path.stat().st_mtime_ns if path.is_file() else 0)
        for relative_path in relative_paths
        for path in [product_dir / relative_path]
    )
    if task_snapshot is None:
        return files
    product = task_snapshot.get("product") or {}
    stores = task_snapshot.get("stores") or []
    sku_publications = task_snapshot.get("sku_publications") or []
    projection = (
        product.get("updated_at"),
        tuple((item.get("id"), item.get("updated_at"), item.get("status"), item.get("selected")) for item in stores if isinstance(item, dict)),
        tuple((item.get("id"), item.get("updated_at"), item.get("status"), item.get("task_id"), item.get("offer_id"), item.get("ozon_product_id")) for item in sku_publications if isinstance(item, dict)),
    )
    return (*files, projection)


def cached_workbench_cards() -> List[Dict[str, Any]]:
    """Refresh only changed product cards during live production polling."""
    paths = [path for path in owned_product_dirs() if (path / "status.json").is_file()]
    batch_running = running_batch_pid() is not None
    store_ids = tuple(store["id"] for store in list_stores(ROOT))
    task_snapshots: Dict[str, Dict[str, Any]] = {}
    if cutover_active(ROOT):
        task_snapshots = product_snapshots(ROOT, (path.name for path in paths))
    context = (
        str(PRODUCTS_DIR.resolve()),
        batch_running,
        store_ids,
    )
    with _WORKBENCH_CARD_CACHE_LOCK:
        if _WORKBENCH_CARD_CACHE["context"] != context:
            _WORKBENCH_CARD_CACHE["context"] = context
            _WORKBENCH_CARD_CACHE["entries"] = {}
        entries = _WORKBENCH_CARD_CACHE["entries"]
        active_ids = set()
        cards = []
        for path in paths:
            product_id = path.name
            active_ids.add(product_id)
            task_snapshot = task_snapshots.get(product_id) if task_snapshots else None
            fingerprint = workbench_card_fingerprint(path, task_snapshot)
            entry = entries.get(product_id)
            if entry is None or entry.get("fingerprint") != fingerprint:
                entry = {
                    "fingerprint": fingerprint,
                    "card": workbench_list_card(
                        path,
                        store_ids=list(store_ids),
                        batch_running=batch_running,
                        task_snapshot=task_snapshot,
                    ),
                }
                entries[product_id] = entry
            cards.append(entry["card"])
        for product_id in tuple(entries):
            if product_id not in active_ids:
                entries.pop(product_id, None)
        return list(cards)


def associated_shops(product_dir: Path, status: Dict[str, Any]) -> List[str]:
    shops: set[str] = set()
    known_keys = {"shop", "shop_name", "selected_shop", "store", "store_name"}

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key in known_keys and isinstance(item, str) and item.strip() and item != "unknown":
                    shops.add(item.strip())
                else:
                    visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(status)
    for path in (
        product_dir / "output/workbench-draft.json",
        product_dir / "output/ozon-result.json",
        product_dir / "output/ozon-upload-config.json",
        product_dir / "output/ozon-write-receipt.json",
    ):
        visit(load_optional_json(path))
    publications = load_publications(product_dir)
    for store_id, record in (publications.get("stores") or {}).items():
        if record.get("selected") or str(record.get("status")) not in {"", "NOT_SELECTED"}:
            shops.add(store_id)
    return sorted(shops)


def workbench_delete_preview(product_id: str) -> Dict[str, Any]:
    product_dir = workbench_product_dir(product_id)
    source = load_optional_json(product_dir / "input/source.json")
    status = effective_product_status(
        product_dir,
        load_optional_json(product_dir / "status.json"),
    )
    ozon = status.get("ozon") or {}
    result = load_optional_json(product_dir / "output/ozon-result.json")
    items = result.get("items") or result.get("offers") or []
    remote_ids = {
        "task_ids": sorted({str(value) for value in [ozon.get("task_id"), result.get("task_id")] if value not in {None, "", "unknown"}}),
        "offer_ids": sorted({str(value) for value in [ozon.get("offer_id"), *[item.get("offer_id") for item in items if isinstance(item, dict)]] if value not in {None, "", "unknown"}}),
        "product_ids": sorted({str(value) for value in [ozon.get("product_id"), *[item.get("product_id") or item.get("ozon_product_id") for item in items if isinstance(item, dict)]] if value not in {None, "", "unknown"}}),
    }
    submitted = bool(
        int(status.get("api_write_count") or 0) > 0
        or any(remote_ids.values())
        or str(status.get("status") or "").upper() in {"UPLOADING", "PENDING_REMOTE", "IMPORTED", "UPLOADED", "OZON_MODERATION", "ACTIVE"}
    )
    thumbnail_exists = any(path.is_file() for path in (product_dir / "input/main-images").glob("*"))
    return {
        "product_id": product_id,
        "title": source.get("title_cn") or "unknown",
        "thumbnail_url": f"/api/inbox/products/{product_id}/thumbnail" if thumbnail_exists else None,
        "sku_count": len(source.get("skus") or []),
        "status": status.get("status") or "unknown",
        "public_state": public_state(status.get("status")),
        "current_step": status.get("current_step") or "none",
        "submitted_to_ozon": submitted,
        "associated_shops": associated_shops(product_dir, status),
        "remote_ids": remote_ids,
        "remote_warning_required": submitted,
    }
