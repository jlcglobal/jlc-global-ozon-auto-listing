"""Routes/helpers extracted from app.py (2026-08-14).

Executed in app.py's globals (bottom of app.py); no imports needed here.
"""

@app.get("/api/workbench/risks")
def workbench_risks() -> Dict[str, Any]:
    items = []
    for card in cached_workbench_cards():
        for risk in card["risk"]["items"]:
            items.append({"product_id": card["product_id"], "title": card["title_cn"], **risk})
    rules_path = ROOT / "config/workbench-risk-rules.json"
    rules = load_optional_json(rules_path, {
        "rules": [
            {"id": "product_truth", "name": "产品真实性", "action": "block", "immutable": True},
            {"id": "ozon_hard_rule", "name": "Ozon平台硬规则", "action": "block", "immutable": True},
            {"id": "duplicate_create", "name": "重复CREATE", "action": "block", "immutable": True},
            {"id": "inventory_api", "name": "库存接口", "action": "block", "immutable": True},
            {"id": "sku_merge", "name": "SKU错误合并", "action": "block", "immutable": True},
            {"id": "category_confidence", "name": "类目置信度偏低", "action": "review", "immutable": False},
        ]
    })
    return {"items": items, "rules": rules.get("rules") or []}


@app.patch("/api/workbench/risk-rules/{rule_id}")
async def update_workbench_risk_rule(rule_id: str, request: Request) -> Dict[str, Any]:
    require_owner_role()
    path = ROOT / "config/workbench-risk-rules.json"
    current = workbench_risks()["rules"]
    rule = next((item for item in current if item.get("id") == rule_id), None)
    if not rule:
        raise HTTPException(status_code=404, detail="问题规则不存在")
    if rule.get("immutable"):
        raise HTTPException(status_code=422, detail="该硬规则永远禁止降级")
    payload = await request.json()
    action = str(payload.get("action") or "") if isinstance(payload, dict) else ""
    if action not in {"allow", "review", "block"}:
        raise HTTPException(status_code=422, detail="处理动作必须是自动通过、人工确认或禁止跳过")
    rule["action"] = action
    rule["updated_at"] = now_iso()
    atomic_write_json(path, {"schema_version": "1.0.0", "rules": current})
    return {"saved": True, "rule": rule, "write_api_calls": 0, "inventory_api_calls": 0}


@app.get("/api/workbench/shops")
def workbench_shops() -> Dict[str, Any]:
    registry = load_registry(ROOT)
    items = list_stores(ROOT)
    counts = {str(item.get("id")): {"associated": 0, "pending": 0} for item in items}
    for product_dir in owned_product_dirs():
        publications = load_publications(product_dir)
        for store_id, record in (publications.get("stores") or {}).items():
            if store_id not in counts:
                continue
            if record.get("selected") or str(record.get("status") or "") not in {"", "NOT_SELECTED"}:
                counts[store_id]["associated"] += 1
            if str(record.get("status") or "") in {"QUEUED", "UPLOADING", "PENDING_REMOTE", "OZON_MODERATION"}:
                counts[store_id]["pending"] += 1
    for item in items:
        item["associated_product_count"] = counts[str(item.get("id"))]["associated"]
        item["pending_task_count"] = counts[str(item.get("id"))]["pending"]
    return {"items": items, "default_shop": registry.get("default_read_shop")}


@app.post("/api/workbench/shops")
async def create_workbench_shop(request: Request) -> Dict[str, Any]:
    require_owner_role()
    payload = await request.json()
    try:
        item = upsert_store(ROOT, payload)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"created": True, "item": item, "write_api_calls": 0, "inventory_api_calls": 0}


@app.patch("/api/workbench/shops/{store_id}")
async def edit_workbench_shop(store_id: str, request: Request) -> Dict[str, Any]:
    require_owner_role()
    payload = await request.json()
    try:
        item = upsert_store(ROOT, payload, store_id=store_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"saved": True, "item": item, "write_api_calls": 0, "inventory_api_calls": 0}


@app.post("/api/workbench/shops/{store_id}/validate")
def validate_workbench_shop(store_id: str) -> Dict[str, Any]:
    require_owner_role()
    try:
        return validate_store_read_only(ROOT, store_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="店铺不存在") from exc


@app.post("/api/workbench/shops/{store_id}/enabled")
async def toggle_workbench_shop(store_id: str, request: Request) -> Dict[str, Any]:
    require_owner_role()
    payload = await request.json()
    try:
        item = set_enabled(ROOT, store_id, bool(payload.get("enabled")))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="店铺不存在") from exc
    return {"saved": True, "item": item, "write_api_calls": 0, "inventory_api_calls": 0}


@app.delete("/api/workbench/shops/{store_id}")
def delete_workbench_shop(store_id: str) -> Dict[str, Any]:
    require_owner_role()
    try:
        delete_store(ROOT, store_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="店铺不存在") from exc
    return {"deleted": True, "store_id": store_id, "remote_ozon_unchanged": True, "write_api_calls": 0, "inventory_api_calls": 0}
