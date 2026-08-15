#!/usr/bin/env python3
"""Local workbench operators and access-code authentication.

Operator access is intentionally local-only. Codes are stored as hashes and
never returned after creation. Product visibility is always owner scoped;
the owner role grants settings management, not visibility into other users'
products.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


DEFAULT_OPERATOR_ID = "studio-owner"
PBKDF2_ITERATIONS = 200_000


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def code_hash(value: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", value.encode("utf-8"), bytes.fromhex(salt), PBKDF2_ITERATIONS,
    ).hex()
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt}${digest}"


def code_matches(value: str, stored_hash: str) -> bool:
    stored = str(stored_hash or "")
    if stored.startswith("pbkdf2_sha256$"):
        try:
            _, raw_iterations, salt, expected = stored.split("$", 3)
            iterations = int(raw_iterations)
            if iterations < 100_000 or len(salt) != 32 or len(expected) != 64:
                return False
            actual = hashlib.pbkdf2_hmac(
                "sha256", value.encode("utf-8"), bytes.fromhex(salt), iterations,
            ).hex()
            return secrets.compare_digest(actual, expected)
        except (TypeError, ValueError):
            return False
    # Read legacy SHA-256 records so existing local members are not locked out.
    legacy = hashlib.sha256(value.encode("utf-8")).hexdigest()
    return secrets.compare_digest(legacy, stored)


def _write_json(path: Path, value: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temporary = Path(handle.name)
    temporary.replace(path)
    path.chmod(0o600)


def _read_json(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def normalize_operator_id(value: Any) -> str:
    identifier = re.sub(r"[^a-z0-9-]+", "-", str(value or "").strip().lower()).strip("-")
    if not identifier or len(identifier) > 48:
        raise ValueError("成员ID必须使用1至48位字母、数字或短横线")
    return identifier


def _legacy_owner(root: Path) -> Dict[str, Any]:
    lan = _read_json(root / "config/lan-access.json")
    access_code = str(lan.get("access_code") or "").strip()
    return {
        "id": DEFAULT_OPERATOR_ID,
        "display_name": "工作室负责人",
        "role": "owner",
        "enabled": True,
        # The legacy LAN config still contains the local plaintext code, so a
        # fast compatibility hash avoids adding PBKDF2 latency to every
        # loopback workbench request. New operator records always use PBKDF2.
        "access_code_hash": hashlib.sha256(access_code.encode("utf-8")).hexdigest() if access_code else "",
        "created_at": "legacy-lan-access",
    }


def load_registry(root: Path) -> Dict[str, Any]:
    path = root / "config/operators.json"
    registry = _read_json(path)
    operators = registry.get("operators") if isinstance(registry, dict) else None
    if not isinstance(operators, list) or not operators:
        return {"schema_version": "1.0.0", "operators": [_legacy_owner(root)]}
    normalized = []
    for item in operators:
        if not isinstance(item, dict):
            continue
        try:
            operator_id = normalize_operator_id(item.get("id"))
        except ValueError:
            continue
        normalized.append({
            "id": operator_id,
            "display_name": str(item.get("display_name") or operator_id),
            "role": "owner" if item.get("role") == "owner" else "member",
            "enabled": bool(item.get("enabled", True)),
            "access_code_hash": str(item.get("access_code_hash") or ""),
            "created_at": str(item.get("created_at") or "unknown"),
            "updated_at": str(item.get("updated_at") or "unknown"),
        })
    return {"schema_version": "1.0.0", "operators": normalized or [_legacy_owner(root)]}


def public_operator(item: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": item["id"],
        "display_name": item["display_name"],
        "role": item["role"],
        "enabled": bool(item.get("enabled", True)),
        "access_code_configured": bool(item.get("access_code_hash")),
        "created_at": item.get("created_at") or "unknown",
        "updated_at": item.get("updated_at") or "unknown",
    }


def list_operators(root: Path) -> List[Dict[str, Any]]:
    return [public_operator(item) for item in load_registry(root)["operators"]]


def authenticate(root: Path, supplied_code: str) -> Optional[Dict[str, Any]]:
    supplied = str(supplied_code or "")
    for item in load_registry(root)["operators"]:
        if not item.get("enabled") or not item.get("access_code_hash"):
            continue
        if supplied and code_matches(supplied, str(item["access_code_hash"])):
            return public_operator(item)
    return None


def default_owner(root: Path) -> Dict[str, Any]:
    registry = load_registry(root)
    item = next((entry for entry in registry["operators"] if entry.get("role") == "owner" and entry.get("enabled")), None)
    return public_operator(item or registry["operators"][0])


def upsert_operator(root: Path, payload: Dict[str, Any], operator_id: Optional[str] = None) -> Tuple[Dict[str, Any], Optional[str]]:
    registry = load_registry(root)
    existing = next((item for item in registry["operators"] if item["id"] == operator_id), None) if operator_id else None
    target_id = normalize_operator_id(operator_id or payload.get("id") or payload.get("display_name"))
    if existing is None and any(item["id"] == target_id for item in registry["operators"]):
        raise ValueError("成员ID已存在")
    display_name = str(payload.get("display_name") or (existing or {}).get("display_name") or target_id).strip()
    if not display_name or len(display_name) > 80:
        raise ValueError("成员名称必须为1至80个字符")
    role = str(payload.get("role") or (existing or {}).get("role") or "member")
    if role not in {"owner", "member"}:
        raise ValueError("角色只能是负责人或成员")
    requested_code = str(payload.get("access_code") or "").strip()
    regenerate = bool(payload.get("regenerate_access_code"))
    one_time_code = requested_code or (secrets.token_urlsafe(7) if regenerate or not existing else None)
    item = {
        **(existing or {}),
        "id": target_id,
        "display_name": display_name,
        "role": role,
        "enabled": bool(payload.get("enabled", (existing or {}).get("enabled", True))),
        "access_code_hash": code_hash(one_time_code) if one_time_code else str((existing or {}).get("access_code_hash") or ""),
        "created_at": (existing or {}).get("created_at") or now_iso(),
        "updated_at": now_iso(),
    }
    registry["operators"] = [entry for entry in registry["operators"] if entry["id"] != target_id]
    registry["operators"].append(item)
    _write_json(root / "config/operators.json", registry)
    return public_operator(item), one_time_code


def delete_operator(root: Path, operator_id: str) -> None:
    operator_id = normalize_operator_id(operator_id)
    registry = load_registry(root)
    item = next((entry for entry in registry["operators"] if entry["id"] == operator_id), None)
    if not item:
        raise KeyError(operator_id)
    if item.get("role") == "owner" and sum(entry.get("role") == "owner" and entry.get("enabled") for entry in registry["operators"]) <= 1:
        raise ValueError("必须保留至少一名工作室负责人")
    registry["operators"] = [entry for entry in registry["operators"] if entry["id"] != operator_id]
    _write_json(root / "config/operators.json", registry)
