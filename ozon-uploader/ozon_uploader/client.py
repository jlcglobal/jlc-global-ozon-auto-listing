"""Minimal Ozon client with an explicit product-create allowlist."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, Optional

from ozon_adapter.config import OzonConfig


Transport = Callable[[str, Dict[str, Any]], Dict[str, Any]]


class OzonUploadApiError(RuntimeError):
    def __init__(self, endpoint: str, message: str, status_code: Optional[int] = None):
        self.endpoint = endpoint
        self.status_code = status_code
        prefix = f"Ozon request failed for {endpoint}"
        if status_code is not None:
            prefix += f" (HTTP {status_code})"
        super().__init__(f"{prefix}: {message}")


class OzonWriteClient:
    BASE_URL = "https://api-seller.ozon.ru"
    PRODUCT_IMPORT_ENDPOINT = "/v3/product/import"
    IMPORT_INFO_ENDPOINT = "/v1/product/import/info"
    PRODUCT_INFO_LIST_ENDPOINT = "/v3/product/info/list"
    PRODUCT_ATTRIBUTES_ENDPOINT = "/v4/product/info/attributes"
    PRODUCT_ATTRIBUTES_UPDATE_ENDPOINT = "/v1/product/attributes/update"
    ALLOWED_ENDPOINTS = frozenset({
        PRODUCT_IMPORT_ENDPOINT,
        IMPORT_INFO_ENDPOINT,
        PRODUCT_INFO_LIST_ENDPOINT,
        PRODUCT_ATTRIBUTES_ENDPOINT,
        PRODUCT_ATTRIBUTES_UPDATE_ENDPOINT,
    })

    def __init__(
        self,
        config: OzonConfig,
        transport: Optional[Transport] = None,
        allow_production_write: bool = False,
    ):
        if config.base_url.rstrip("/") != self.BASE_URL:
            raise ValueError("Ozon base URL is fixed")
        self.config = config
        self._transport = transport
        self._allow_production_write = allow_production_write

    def _post_json(self, endpoint: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        if endpoint not in self.ALLOWED_ENDPOINTS:
            raise ValueError(f"Endpoint is not in the uploader allowlist: {endpoint}")
        if not self._allow_production_write and os.environ.get("UPLOAD_MODE", "dry-run").strip().lower() != "production":
            raise ValueError(
                "Ozon uploader network calls require UPLOAD_MODE=production"
            )
        if self._transport is not None:
            result = self._transport(endpoint, payload)
            if not isinstance(result, dict):
                raise OzonUploadApiError(endpoint, "response must be a JSON object")
            return result
        request = urllib.request.Request(
            f"{self.BASE_URL}{endpoint}",
            data=json.dumps(payload).encode("utf-8"),
            headers=self.config.headers(),
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.config.timeout_seconds) as response:
                body = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            try:
                data = json.loads(body)
                message = data.get("message", data.get("error", "Ozon rejected the request"))
            except json.JSONDecodeError:
                message = "Ozon rejected the request"
            raise OzonUploadApiError(endpoint, str(message), exc.code) from exc
        except urllib.error.URLError as exc:
            raise OzonUploadApiError(endpoint, str(exc.reason)) from exc
        try:
            result = json.loads(body)
        except json.JSONDecodeError as exc:
            raise OzonUploadApiError(endpoint, "response was not valid JSON") from exc
        if not isinstance(result, dict):
            raise OzonUploadApiError(endpoint, "response must be a JSON object")
        return result

    def import_products(self, items: list[Dict[str, Any]]) -> Dict[str, Any]:
        """Create missing offers or update existing offers with the same offer_id."""
        return self._post_json(self.PRODUCT_IMPORT_ENDPOINT, {"items": items})

    def create_products(self, items: list[Dict[str, Any]]) -> Dict[str, Any]:
        return self.import_products(items)

    def get_import_info(self, task_id: int) -> Dict[str, Any]:
        return self._post_json(self.IMPORT_INFO_ENDPOINT, {"task_id": task_id})

    def get_products_info(self, offer_ids: list[str]) -> Dict[str, Any]:
        return self._post_json(self.PRODUCT_INFO_LIST_ENDPOINT, {
            "offer_id": offer_ids,
            "product_id": [],
            "sku": [],
        })

    def get_product_attributes(self, offer_ids: list[str]) -> Dict[str, Any]:
        return self._post_json(self.PRODUCT_ATTRIBUTES_ENDPOINT, {
            "filter": {"offer_id": offer_ids, "product_id": [], "visibility": "ALL"},
            "limit": 100,
            "sort_dir": "ASC",
        })

    def update_product_attributes(self, items: list[Dict[str, Any]]) -> Dict[str, Any]:
        return self._post_json(self.PRODUCT_ATTRIBUTES_UPDATE_ENDPOINT, {"items": items})
