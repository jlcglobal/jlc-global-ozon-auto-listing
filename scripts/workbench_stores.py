"""Secure local Ozon store registry for the internal workbench."""
from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def load_json(path: Path, default: Any) -> Any:
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temporary = Path(handle.name)
    temporary.replace(path)


def normalize_store_id(value: str) -> str:
    store_id = re.sub(r"[^a-zA-Z0-9_-]+", "-", value.strip()).strip("-").lower()
    if not store_id or len(store_id) > 48:
        raise ValueError("店铺标识只能包含字母、数字、横线和下划线，最长48字符")
    return store_id


def registry_path(root: Path) -> Path:
    return root / "ozon-adapter/shops.json"


def secret_path(root: Path, store_id: str) -> Path:
    return root / "ozon-adapter" / f".env.{store_id}"


def load_registry(root: Path) -> Dict[str, Any]:
    registry = load_json(registry_path(root), {"schema_version": "1.1.0", "default_read_shop": None, "shops": []})
    registry["schema_version"] = "1.1.0"
    for shop in registry.get("shops") or []:
        store_id = normalize_store_id(str(shop.get("id") or shop.get("name") or ""))
        shop.setdefault("id", store_id)
        shop.setdefault("display_name", shop.get("name") or store_id)
        shop.setdefault("name", store_id)
        shop.setdefault("enabled", True)
        shop.setdefault("notes", "")
        shop.setdefault("validation_status", "unverified")
        shop.setdefault("last_validated_at", None)
        shop.setdefault("last_validation_error", None)
        shop.setdefault("default_currency_code", "CNY")
        shop.setdefault("client_id_env", f"OZON_{store_id.upper().replace('-', '_')}_CLIENT_ID")
        shop.setdefault("api_key_env", f"OZON_{store_id.upper().replace('-', '_')}_API_KEY")
        path = secret_path(root, store_id)
        if path.is_file():
            path.chmod(0o600)
    return registry


def read_secret(root: Path, shop: Mapping[str, Any]) -> Dict[str, str]:
    values: Dict[str, str] = {}
    path = secret_path(root, str(shop["id"]))
    if path.is_file():
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip('"').strip("'")
    for key in (str(shop["client_id_env"]), str(shop["api_key_env"])):
        if os.getenv(key):
            values.setdefault(key, str(os.getenv(key)))
    return values


def credential_state(root: Path, shop: Mapping[str, Any]) -> Dict[str, bool]:
    values = read_secret(root, shop)
    return {
        "client_id_configured": bool(values.get(str(shop["client_id_env"]))),
        "api_key_configured": bool(values.get(str(shop["api_key_env"]))),
    }


def store_stats(root: Path, store_id: str) -> Dict[str, int]:
    associated = 0
    pending = 0
    for path in (root / "products").glob("P*/output/store-publications.json"):
        data = load_json(path, {})
        record = (data.get("stores") or {}).get(store_id)
        if not isinstance(record, dict):
            continue
        associated += 1
        if str(record.get("status") or "") in {"SELECTED", "QUEUED", "UPLOADING", "PENDING_REMOTE", "QUERY_ERROR"}:
            pending += 1
    return {"associated_product_count": associated, "pending_task_count": pending}


def public_store(root: Path, shop: Mapping[str, Any]) -> Dict[str, Any]:
    configured = credential_state(root, shop)
    validation = str(shop.get("validation_status") or "unverified")
    if not shop.get("enabled", True):
        connection_status = "disabled"
    elif validation == "connected" and all(configured.values()):
        connection_status = "connected"
    elif validation == "failed":
        connection_status = "failed"
    else:
        connection_status = "unverified"
    return {
        "id": shop["id"], "display_name": shop.get("display_name") or shop["id"],
        "currency": shop.get("default_currency_code") or "CNY", "notes": shop.get("notes") or "",
        "enabled": bool(shop.get("enabled", True)), "connection_status": connection_status,
        "client_id_configured": configured["client_id_configured"],
        "api_key_configured": configured["api_key_configured"],
        "credentials_display": "已配置" if all(configured.values()) else "未配置",
        "last_validated_at": shop.get("last_validated_at"),
        "last_validation_error": shop.get("last_validation_error") if validation == "failed" else None,
        **store_stats(root, str(shop["id"])),
    }


def list_stores(root: Path) -> List[Dict[str, Any]]:
    return [public_store(root, shop) for shop in load_registry(root).get("shops") or []]


def save_credentials(root: Path, shop: Mapping[str, Any], client_id: str, api_key: str) -> None:
    path = secret_path(root, str(shop["id"]))
    path.write_text(
        f"{shop['client_id_env']}={client_id.strip()}\n{shop['api_key_env']}={api_key.strip()}\n",
        encoding="utf-8",
    )
    path.chmod(0o600)


def upsert_store(root: Path, payload: Mapping[str, Any], store_id: Optional[str] = None) -> Dict[str, Any]:
    registry = load_registry(root)
    value = store_id or str(payload.get("id") or payload.get("display_name") or "")
    normalized = normalize_store_id(value)
    current = next((shop for shop in registry.get("shops") or [] if shop["id"] == normalized), None)
    is_new = current is None
    if current is None:
        current = {"id": normalized, "name": normalized}
        registry.setdefault("shops", []).append(current)
    current.update({
        "display_name": str(payload.get("display_name") or current.get("display_name") or normalized).strip(),
        "default_currency_code": str(payload.get("currency") or current.get("default_currency_code") or "CNY").upper(),
        "notes": str(payload.get("notes") if "notes" in payload else current.get("notes") or "").strip(),
        "enabled": bool(payload.get("enabled", current.get("enabled", True))),
        "client_id_env": current.get("client_id_env") or f"OZON_{normalized.upper().replace('-', '_')}_CLIENT_ID",
        "api_key_env": current.get("api_key_env") or f"OZON_{normalized.upper().replace('-', '_')}_API_KEY",
        "default_vat": current.get("default_vat", "0"),
        "default_unbranded_value": current.get("default_unbranded_value", "Нет бренда"),
        "default_unbranded_dictionary_value_id": current.get("default_unbranded_dictionary_value_id", 126745801),
    })
    client_id = str(payload.get("client_id") or "").strip()
    api_key = str(payload.get("api_key") or "").strip()
    if is_new and (not client_id or not api_key):
        raise ValueError("新增店铺必须填写Client-Id和Api-Key")
    if client_id or api_key:
        existing = read_secret(root, current)
        client_id = client_id or existing.get(str(current["client_id_env"]), "")
        api_key = api_key or existing.get(str(current["api_key_env"]), "")
        if not client_id or not api_key:
            raise ValueError("Client-Id和Api-Key必须同时有效")
        save_credentials(root, current, client_id, api_key)
        current.update({"validation_status": "unverified", "last_validated_at": None, "last_validation_error": None})
    elif is_new:
        current["validation_status"] = "unverified"
    if not registry.get("default_read_shop"):
        registry["default_read_shop"] = normalized
    registry["schema_version"] = "1.1.0"
    write_json(registry_path(root), registry)
    return public_store(root, current)


def set_enabled(root: Path, store_id: str, enabled: bool) -> Dict[str, Any]:
    registry = load_registry(root)
    shop = next((item for item in registry.get("shops") or [] if item["id"] == store_id), None)
    if shop is None:
        raise KeyError(store_id)
    shop["enabled"] = bool(enabled)
    write_json(registry_path(root), registry)
    return public_store(root, shop)


def mark_store_validation_failed(root: Path, store_id: str, error: str) -> Dict[str, Any]:
    """Persist a definitive credential failure found during an upload attempt."""
    registry = load_registry(root)
    shop = next((item for item in registry.get("shops") or [] if item["id"] == store_id), None)
    if shop is None:
        raise KeyError(store_id)
    shop.update({
        "validation_status": "failed",
        "last_validated_at": now(),
        "last_validation_error": str(error)[:240],
    })
    write_json(registry_path(root), registry)
    return public_store(root, shop)


def delete_store(root: Path, store_id: str) -> None:
    registry = load_registry(root)
    before = len(registry.get("shops") or [])
    registry["shops"] = [shop for shop in registry.get("shops") or [] if shop["id"] != store_id]
    if len(registry["shops"]) == before:
        raise KeyError(store_id)
    if registry.get("default_read_shop") == store_id:
        registry["default_read_shop"] = registry["shops"][0]["id"] if registry["shops"] else None
    write_json(registry_path(root), registry)
    secret_path(root, store_id).unlink(missing_ok=True)


def validate_store_read_only(root: Path, store_id: str, transport=None) -> Dict[str, Any]:
    registry = load_registry(root)
    shop = next((item for item in registry.get("shops") or [] if item["id"] == store_id), None)
    if shop is None:
        raise KeyError(store_id)
    try:
        import sys
        adapter_root = root / "ozon-adapter"
        if not (adapter_root / "ozon_adapter").is_dir():
            adapter_root = Path(__file__).resolve().parents[1] / "ozon-adapter"
        sys.path.insert(0, str(adapter_root))
        from ozon_adapter import OzonConfig, OzonReadOnlyClient
        values = {**os.environ, **read_secret(root, shop)}
        config = OzonConfig.from_shop(store_id, registry_path(root), environ=values)
        response = OzonReadOnlyClient(config, transport=transport).get_category_tree()
        if not isinstance(response, dict):
            raise ValueError("Ozon返回格式无效")
        shop.update({"validation_status": "connected", "last_validated_at": now(), "last_validation_error": None})
    except Exception as exc:
        shop.update({"validation_status": "failed", "last_validated_at": now(), "last_validation_error": str(exc)[:240]})
    write_json(registry_path(root), registry)
    return {**public_store(root, shop), "ozon_write_api_calls": 0, "inventory_api_calls": 0}
