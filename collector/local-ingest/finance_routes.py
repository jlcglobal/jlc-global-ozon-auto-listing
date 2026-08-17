"""Finance routes extracted from app.py (2026-08-14).

Executed in app.py's globals (see the bottom of app.py), so every global —
FINANCE_CENTER, HTTPException, now_iso, Request, ... — resolves against the
live app module and this file needs no imports of its own.
"""

@app.get("/api/workbench/finance/overview")
def finance_overview(
    store_id: str = "all", date_from: str = "", date_to: str = "", currency: str = "CNY",
) -> Dict[str, Any]:
    try:
        return FINANCE_CENTER.overview(
            store_id=store_id, date_from=date_from or None, date_to=date_to or None, currency=currency,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/workbench/finance/orders")
def finance_orders(
    store_id: str = "all", date_from: str = "", date_to: str = "", q: str = "", limit: int = 200,
) -> Dict[str, Any]:
    try:
        return FINANCE_CENTER.orders(
            store_id=store_id, date_from=date_from or None, date_to=date_to or None, query=q, limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/workbench/finance/products")
def finance_products(
    store_id: str = "all", date_from: str = "", date_to: str = "", q: str = "", limit: int = 200,
) -> Dict[str, Any]:
    try:
        return FINANCE_CENTER.products(
            store_id=store_id, date_from=date_from or None, date_to=date_to or None, query=q, limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/workbench/finance/reconciliation")
def finance_reconciliation(store_id: str = "all", limit: int = 200) -> Dict[str, Any]:
    return FINANCE_CENTER.reconciliation(store_id=store_id, limit=limit)


@app.get("/api/workbench/finance/sync-status")
def finance_sync_status() -> Dict[str, Any]:
    return FINANCE_CENTER.sync_status()


@app.post("/api/workbench/finance/sync")
def finance_sync_now() -> Dict[str, Any]:
    require_owner_role()
    try:
        return FINANCE_CENTER.sync(trigger="manual")
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/workbench/finance/sync/start")
def finance_sync_start() -> Dict[str, Any]:
    require_owner_role()
    status = FINANCE_CENTER.sync_status(limit=5)
    if any(str(item.get("status") or "") == "running" for item in status.get("runs") or []):
        return {"status": "running", "message": "财务数据正在更新，请稍后查看", "write_api_calls": 0}

    def run_read_only_sync() -> None:
        try:
            FINANCE_CENTER.sync(trigger="manual")
        except Exception:
            # FinanceCenter persists the sanitized failure and retry state.
            return

    threading.Thread(target=run_read_only_sync, name="finance-manual-sync", daemon=True).start()
    return {
        "status": "started", "message": "已开始读取最近 90 天 Ozon 订单和财务数据",
        "ozon_write_api_calls": 0, "inventory_api_calls": 0,
    }


@app.post("/api/workbench/finance/imports/preview")
async def finance_import_preview(request: Request) -> Dict[str, Any]:
    require_owner_role()
    payload = await request.json()
    try:
        return FINANCE_CENTER.preview_import(
            file_name=str(payload.get("file_name") or ""),
            content_base64=str(payload.get("content_base64") or ""),
            file_kind=str(payload.get("file_kind") or "") or None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/workbench/finance/imports/commit")
async def finance_import_commit(request: Request) -> Dict[str, Any]:
    operator = require_owner_role()
    payload = await request.json()
    mapping = payload.get("mapping") or {}
    if not isinstance(mapping, dict):
        raise HTTPException(status_code=422, detail="字段映射格式错误")
    try:
        return FINANCE_CENTER.commit_import(
            file_name=str(payload.get("file_name") or ""),
            content_base64=str(payload.get("content_base64") or ""),
            file_kind=str(payload.get("file_kind") or ""), mapping=mapping,
            created_by=str(operator.get("id") or "owner"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/workbench/finance/purchase-costs/sku")
async def finance_set_sku_purchase_cost(request: Request) -> Dict[str, Any]:
    operator = require_owner_role()
    payload = await request.json()
    try:
        return FINANCE_CENTER.set_sku_purchase_cost(
            sku=str(payload.get("sku") or ""),
            purchase_cost_cny=payload.get("purchase_cost_cny"),
            created_by=str(operator.get("id") or "owner"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/workbench/finance/imports")
def finance_import_batches() -> Dict[str, Any]:
    require_owner_role()
    return {"items": FINANCE_CENTER.import_batches()}


@app.post("/api/workbench/finance/imports/{batch_id}/rollback")
def finance_import_rollback(batch_id: str) -> Dict[str, Any]:
    operator = require_owner_role()
    try:
        return FINANCE_CENTER.rollback_import(batch_id, rolled_back_by=str(operator.get("id") or "owner"))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="导入批次不存在") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/workbench/finance/other-entries")
def finance_other_entries(store_id: str = "all", date_from: str = "", date_to: str = "") -> Dict[str, Any]:
    try:
        items = FINANCE_CENTER.other_entries(
            store_id=store_id, date_from=date_from or None, date_to=date_to or None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"items": items}


@app.post("/api/workbench/finance/other-entries")
async def finance_create_other_entry(request: Request) -> Dict[str, Any]:
    operator = require_owner_role()
    payload = await request.json()
    try:
        return {"item": FINANCE_CENTER.save_other_entry(payload, created_by=str(operator.get("id") or "owner"))}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.patch("/api/workbench/finance/other-entries/{entry_id}")
async def finance_update_other_entry(entry_id: str, request: Request) -> Dict[str, Any]:
    operator = require_owner_role()
    payload = await request.json()
    try:
        return {"item": FINANCE_CENTER.save_other_entry(
            payload, created_by=str(operator.get("id") or "owner"), entry_id=entry_id,
        )}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.delete("/api/workbench/finance/other-entries/{entry_id}")
def finance_delete_other_entry(entry_id: str) -> Dict[str, Any]:
    require_owner_role()
    try:
        FINANCE_CENTER.delete_other_entry(entry_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="其他收支记录不存在") from exc
    return {"deleted": True, "id": entry_id}


@app.get("/api/workbench/finance/export/{export_type}")
def finance_export(
    export_type: str, store_id: str = "all", date_from: str = "", date_to: str = "",
) -> FileResponse:
    if export_type not in {"orders", "products", "reconciliation"}:
        raise HTTPException(status_code=404, detail="未知财务导出类型")
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    directory = ROOT / "logs/workbench-exports"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"finance-{export_type}-{stamp}.csv"
    if export_type == "orders":
        rows = FINANCE_CENTER.orders(store_id=store_id, date_from=date_from or None, date_to=date_to or None, limit=1000)["items"]
    elif export_type == "products":
        rows = FINANCE_CENTER.products(store_id=store_id, date_from=date_from or None, date_to=date_to or None, limit=1000)["items"]
    else:
        rows = FINANCE_CENTER.reconciliation(store_id=store_id, limit=1000)["items"]
    fields = sorted({key for row in rows for key, value in row.items() if not isinstance(value, (dict, list))})
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields or ["暂无数据"])
        writer.writeheader()
        writer.writerows({key: value for key, value in row.items() if key in fields} for row in rows)
    return FileResponse(path, filename=path.name, media_type="text/csv")
