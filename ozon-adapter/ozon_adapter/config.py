"""Environment-only Ozon Seller API configuration."""

from __future__ import annotations

import os
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Optional


class OzonConfigurationError(ValueError):
    """Raised when required Ozon credentials are unavailable."""


@dataclass(frozen=True)
class OzonConfig:
    client_id: str = field(repr=False)
    api_key: str = field(repr=False)
    shop_name: str = "default"
    base_url: str = "https://api-seller.ozon.ru"
    timeout_seconds: float = 30.0
    max_value_pages: int = 20

    @classmethod
    def from_env(cls, environ: Optional[Mapping[str, str]] = None) -> "OzonConfig":
        values = os.environ if environ is None else environ
        client_id = values.get("OZON_CLIENT_ID", "").strip()
        api_key = values.get("OZON_API_KEY", "").strip()
        missing = [
            name
            for name, value in (("OZON_CLIENT_ID", client_id), ("OZON_API_KEY", api_key))
            if not value
        ]
        if missing:
            raise OzonConfigurationError(
                "Missing required Ozon Seller API configuration: " + ", ".join(missing)
            )
        timeout = float(values.get("OZON_TIMEOUT_SECONDS", "30"))
        max_pages = int(values.get("OZON_ATTRIBUTE_VALUES_MAX_PAGES", "20"))
        if timeout <= 0 or max_pages <= 0:
            raise OzonConfigurationError("Ozon timeout and value-page limit must be positive")
        return cls(
            client_id=client_id,
            api_key=api_key,
            timeout_seconds=timeout,
            max_value_pages=max_pages,
        )

    @classmethod
    def from_shop(
        cls,
        shop_name: str,
        registry_path: Path,
        environ: Optional[Mapping[str, str]] = None,
    ) -> "OzonConfig":
        values = os.environ if environ is None else environ
        try:
            registry = json.loads(registry_path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise OzonConfigurationError(f"Ozon shop registry not found: {registry_path}") from exc
        shop = next((item for item in registry.get("shops", []) if item.get("name") == shop_name), None)
        if shop is None:
            available = ", ".join(item.get("name", "") for item in registry.get("shops", [])) or "none"
            raise OzonConfigurationError(
                f"Unknown Ozon shop '{shop_name}'. Configured shops: {available}"
            )
        client_id_env = shop["client_id_env"]
        api_key_env = shop["api_key_env"]
        client_id = values.get(client_id_env, "").strip()
        api_key = values.get(api_key_env, "").strip()
        missing = [name for name, value in ((client_id_env, client_id), (api_key_env, api_key)) if not value]
        if missing:
            raise OzonConfigurationError(
                f"Missing credentials for Ozon shop '{shop_name}': " + ", ".join(missing)
            )
        timeout = float(values.get("OZON_TIMEOUT_SECONDS", "30"))
        max_pages = int(values.get("OZON_ATTRIBUTE_VALUES_MAX_PAGES", "20"))
        if timeout <= 0 or max_pages <= 0:
            raise OzonConfigurationError("Ozon timeout and value-page limit must be positive")
        return cls(
            client_id=client_id,
            api_key=api_key,
            shop_name=shop_name,
            timeout_seconds=timeout,
            max_value_pages=max_pages,
        )

    def headers(self) -> dict[str, str]:
        return {
            "Client-Id": self.client_id,
            "Api-Key": self.api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
