"""Strictly read-only Ozon Seller API client."""

from __future__ import annotations

import json
import socket
import ssl
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, Optional

from .config import OzonConfig


Transport = Callable[[str, Dict[str, Any]], Dict[str, Any]]


class OzonApiError(RuntimeError):
    """Safe Ozon error that never includes credentials."""

    def __init__(self, endpoint: str, message: str, status_code: Optional[int] = None):
        self.endpoint = endpoint
        self.status_code = status_code
        prefix = f"Ozon read-only request failed for {endpoint}"
        if status_code is not None:
            prefix += f" (HTTP {status_code})"
        super().__init__(f"{prefix}: {message}")


class OzonReadOnlyClient:
    BASE_URL = "https://api-seller.ozon.ru"
    CATEGORY_TREE_ENDPOINT = "/v1/description-category/tree"
    CATEGORY_ATTRIBUTES_ENDPOINT = "/v1/description-category/attribute"
    ATTRIBUTE_VALUES_ENDPOINT = "/v1/description-category/attribute/values"
    ALLOWED_ENDPOINTS = frozenset({
        CATEGORY_TREE_ENDPOINT,
        CATEGORY_ATTRIBUTES_ENDPOINT,
        ATTRIBUTE_VALUES_ENDPOINT,
    })
    NETWORK_ATTEMPTS = 4
    RETRYABLE_HTTP_STATUS_CODES = frozenset({408, 425, 429, 500, 502, 503, 504})

    def __init__(self, config: OzonConfig, transport: Optional[Transport] = None):
        if config.base_url.rstrip("/") != self.BASE_URL:
            raise ValueError("Ozon base URL is fixed for the read-only adapter")
        self.config = config
        self._transport = transport

    @staticmethod
    def _retry_delay_seconds(attempt: int) -> float:
        return min(4.0, 0.5 * (2 ** max(0, attempt - 1)))

    @staticmethod
    def _is_transient_network_error(exc: Any) -> bool:
        if isinstance(exc, str):
            text = exc.casefold()
            return any(token in text for token in (
                "timeout", "timed out", "ssl", "handshake", "eof", "connection", "temporar"
            ))
        if isinstance(exc, (TimeoutError, socket.timeout, ssl.SSLError, ConnectionError)):
            return True
        if isinstance(exc, urllib.error.URLError):
            return OzonReadOnlyClient._is_transient_network_error(exc.reason)
        if isinstance(exc, OSError):
            return True
        return False

    def _pause_before_retry(self, attempt: int) -> None:
        time.sleep(self._retry_delay_seconds(attempt))

    def _post_json(self, endpoint: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        if endpoint not in self.ALLOWED_ENDPOINTS:
            raise ValueError(f"Endpoint is not in the read-only allowlist: {endpoint}")
        if self._transport is not None:
            for attempt in range(1, self.NETWORK_ATTEMPTS + 1):
                try:
                    response = self._transport(endpoint, payload)
                except Exception as exc:
                    if self._is_transient_network_error(exc) and attempt < self.NETWORK_ATTEMPTS:
                        self._pause_before_retry(attempt)
                        continue
                    raise OzonApiError(endpoint, str(exc)) from exc
                if not isinstance(response, dict):
                    raise OzonApiError(endpoint, "response must be a JSON object")
                return response

        request = urllib.request.Request(
            f"{self.BASE_URL}{endpoint}",
            data=json.dumps(payload).encode("utf-8"),
            headers=self.config.headers(),
            method="POST",
        )
        for attempt in range(1, self.NETWORK_ATTEMPTS + 1):
            try:
                with urllib.request.urlopen(request, timeout=self.config.timeout_seconds) as response:
                    body = response.read().decode("utf-8")
                break
            except urllib.error.HTTPError as exc:
                if exc.code in self.RETRYABLE_HTTP_STATUS_CODES and attempt < self.NETWORK_ATTEMPTS:
                    self._pause_before_retry(attempt)
                    continue
                body = exc.read().decode("utf-8", errors="replace")
                try:
                    message = json.loads(body).get("message", "Ozon API rejected the request")
                except json.JSONDecodeError:
                    message = "Ozon API rejected the request"
                raise OzonApiError(endpoint, str(message), exc.code) from exc
            except Exception as exc:
                if self._is_transient_network_error(exc) and attempt < self.NETWORK_ATTEMPTS:
                    self._pause_before_retry(attempt)
                    continue
                if self._is_transient_network_error(exc):
                    message = str(exc.reason) if isinstance(exc, urllib.error.URLError) else str(exc)
                    raise OzonApiError(endpoint, message) from exc
                raise
        try:
            result = json.loads(body)
        except json.JSONDecodeError as exc:
            raise OzonApiError(endpoint, "response was not valid JSON") from exc
        if not isinstance(result, dict):
            raise OzonApiError(endpoint, "response must be a JSON object")
        return result

    def get_category_tree(self, language: str = "DEFAULT") -> Dict[str, Any]:
        return self._post_json(self.CATEGORY_TREE_ENDPOINT, {"language": language})

    def get_category_attributes(
        self,
        description_category_id: int,
        type_id: int,
        language: str = "DEFAULT",
    ) -> Dict[str, Any]:
        return self._post_json(self.CATEGORY_ATTRIBUTES_ENDPOINT, {
            "description_category_id": description_category_id,
            "type_id": type_id,
            "language": language,
        })

    def get_attribute_values(
        self,
        description_category_id: int,
        type_id: int,
        attribute_id: int,
        language: str = "DEFAULT",
        limit: int = 5000,
    ) -> Dict[str, Any]:
        if not 1 <= limit <= 5000:
            raise ValueError("Attribute value page size must be between 1 and 5000")
        values = []
        last_value_id = 0
        truncated = False
        for _ in range(self.config.max_value_pages):
            response = self._post_json(self.ATTRIBUTE_VALUES_ENDPOINT, {
                "attribute_id": attribute_id,
                "description_category_id": description_category_id,
                "language": language,
                "last_value_id": last_value_id,
                "limit": limit,
                "type_id": type_id,
            })
            page = response.get("result", [])
            if not isinstance(page, list):
                raise OzonApiError(self.ATTRIBUTE_VALUES_ENDPOINT, "result must be an array")
            values.extend(page)
            if not response.get("has_next"):
                return {"values": values, "truncated": False}
            if not page:
                raise OzonApiError(self.ATTRIBUTE_VALUES_ENDPOINT, "pagination did not advance")
            last_value_id = int(page[-1].get("id", 0))
            if last_value_id <= 0:
                raise OzonApiError(self.ATTRIBUTE_VALUES_ENDPOINT, "last value ID is missing")
        truncated = True
        return {"values": values, "truncated": truncated}
