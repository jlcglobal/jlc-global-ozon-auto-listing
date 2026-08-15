"""Strictly read-only Ozon analytics client."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, Iterable, Optional


Transport = Callable[[str, Dict[str, Any]], Dict[str, Any]]


class OzonAnalyticsApiError(RuntimeError):
    def __init__(self, endpoint: str, message: str, status_code: Optional[int] = None):
        self.endpoint = endpoint
        self.status_code = status_code
        prefix = f"Ozon analytics read-only request failed for {endpoint}"
        if status_code is not None:
            prefix += f" (HTTP {status_code})"
        super().__init__(f"{prefix}: {message}")


class OzonAnalyticsPermissionError(OzonAnalyticsApiError):
    pass


class OzonAnalyticsReadOnlyClient:
    BASE_URL = "https://api-seller.ozon.ru"
    PRODUCT_LIST_ENDPOINT = "/v3/product/list"
    PRODUCT_INFO_ENDPOINT = "/v3/product/info/list"
    PRODUCT_ATTRIBUTES_ENDPOINT = "/v4/product/info/attributes"
    PRODUCT_QUERIES_ENDPOINT = "/v1/analytics/product-queries"
    PRODUCT_QUERY_DETAILS_ENDPOINT = "/v1/analytics/product-queries/details"
    FBO_POSTING_ENDPOINT = "/v2/posting/fbo/list"
    FBS_POSTING_ENDPOINT = "/v3/posting/fbs/list"
    ALLOWED_ENDPOINTS = frozenset({
        PRODUCT_LIST_ENDPOINT,
        PRODUCT_INFO_ENDPOINT,
        PRODUCT_ATTRIBUTES_ENDPOINT,
        PRODUCT_QUERIES_ENDPOINT,
        PRODUCT_QUERY_DETAILS_ENDPOINT,
        FBO_POSTING_ENDPOINT,
        FBS_POSTING_ENDPOINT,
    })

    def __init__(
        self,
        client_id: str,
        api_key: str,
        transport: Optional[Transport] = None,
        timeout_seconds: float = 30.0,
    ):
        if not client_id.strip() or not api_key.strip():
            raise ValueError("Ozon Client-Id and Api-Key are required")
        self._client_id = client_id.strip()
        self._api_key = api_key.strip()
        self._transport = transport
        self.timeout_seconds = float(timeout_seconds)

    def __repr__(self) -> str:
        return f"{type(self).__name__}(credentials=<hidden>)"

    def _post_json(self, endpoint: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        if endpoint not in self.ALLOWED_ENDPOINTS:
            raise ValueError(f"Endpoint is not in the analytics read-only allowlist: {endpoint}")
        if self._transport is not None:
            result = self._transport(endpoint, payload)
            if not isinstance(result, dict):
                raise OzonAnalyticsApiError(endpoint, "response must be a JSON object")
            return result

        request = urllib.request.Request(
            f"{self.BASE_URL}{endpoint}",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Client-Id": self._client_id,
                "Api-Key": self._api_key,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                body = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            try:
                message = str(json.loads(body).get("message") or "Ozon rejected the request")
            except json.JSONDecodeError:
                message = "Ozon rejected the request"
            error_type = OzonAnalyticsPermissionError if exc.code == 403 else OzonAnalyticsApiError
            raise error_type(endpoint, message, exc.code) from exc
        except urllib.error.URLError as exc:
            raise OzonAnalyticsApiError(endpoint, str(exc.reason)) from exc
        try:
            result = json.loads(body)
        except json.JSONDecodeError as exc:
            raise OzonAnalyticsApiError(endpoint, "response was not valid JSON") from exc
        if not isinstance(result, dict):
            raise OzonAnalyticsApiError(endpoint, "response must be a JSON object")
        return result

    def list_products(self, *, limit: int = 100, last_id: str = "", visibility: str = "ALL") -> Dict[str, Any]:
        if not 1 <= int(limit) <= 1000:
            raise ValueError("Product page size must be between 1 and 1000")
        return self._post_json(self.PRODUCT_LIST_ENDPOINT, {
            "filter": {"offer_id": [], "product_id": [], "visibility": visibility},
            "last_id": last_id,
            "limit": int(limit),
        })

    def get_product_info(self, product_ids: Iterable[int]) -> Dict[str, Any]:
        ids = [int(value) for value in product_ids]
        if not ids:
            raise ValueError("At least one product ID is required")
        return self._post_json(self.PRODUCT_INFO_ENDPOINT, {
            "offer_id": [],
            "product_id": ids,
            "sku": [],
        })

    def get_product_attributes(self, offer_ids: Iterable[str] = (), product_ids: Iterable[int] = ()) -> Dict[str, Any]:
        offer_values = [str(value).strip() for value in offer_ids if str(value).strip()]
        product_values = [int(value) for value in product_ids if str(value).isdigit()]
        if not offer_values and not product_values:
            raise ValueError("At least one product ID or offer ID is required")
        return self._post_json(self.PRODUCT_ATTRIBUTES_ENDPOINT, {
            "filter": {"offer_id": offer_values, "product_id": product_values, "visibility": "ALL"},
            "limit": max(1, min(100, len(offer_values) + len(product_values))),
            "sort_dir": "ASC",
        })

    def get_product_queries(
        self,
        skus: Iterable[int],
        *,
        date_from: str,
        date_to: str,
        page: int = 0,
        page_size: int = 100,
    ) -> Dict[str, Any]:
        sku_values = [str(int(value)) for value in skus]
        if not 1 <= len(sku_values) <= 1000:
            raise ValueError("Product queries require between 1 and 1000 SKUs")
        if not 1 <= int(page_size) <= 100:
            raise ValueError("Query page size must be between 1 and 100")
        return self._post_json(self.PRODUCT_QUERIES_ENDPOINT, {
            "date_from": date_from,
            "date_to": date_to,
            "page": int(page),
            "page_size": int(page_size),
            "skus": sku_values,
            "sort_by": "BY_SEARCHES",
            "sort_dir": "DESCENDING",
        })

    def get_product_query_details(
        self,
        skus: Iterable[int],
        *,
        date_from: str,
        date_to: str,
        limit_by_sku: int = 20,
        page: int = 0,
        page_size: int = 100,
    ) -> Dict[str, Any]:
        sku_values = [str(int(value)) for value in skus]
        if not 1 <= len(sku_values) <= 1000:
            raise ValueError("Product query details require between 1 and 1000 SKUs")
        if not 1 <= int(limit_by_sku) <= 15:
            raise ValueError("Per-SKU query limit must be between 1 and 15")
        if not 1 <= int(page_size) <= 100:
            raise ValueError("Query page size must be between 1 and 100")
        return self._post_json(self.PRODUCT_QUERY_DETAILS_ENDPOINT, {
            "date_from": date_from,
            "date_to": date_to,
            "limit_by_sku": int(limit_by_sku),
            "page": int(page),
            "page_size": int(page_size),
            "skus": sku_values,
            "sort_by": "BY_SEARCHES",
            "sort_dir": "DESCENDING",
        })

    def list_fbo_postings(self, *, since: str, to: str, limit: int = 1000, offset: int = 0) -> Dict[str, Any]:
        if not 1 <= int(limit) <= 1000:
            raise ValueError("Posting page size must be between 1 and 1000")
        return self._post_json(self.FBO_POSTING_ENDPOINT, {
            "dir": "ASC",
            "filter": {"since": since, "to": to, "status": ""},
            "limit": int(limit),
            "offset": int(offset),
            "with": {"analytics_data": True, "financial_data": False},
        })

    def list_fbs_postings(self, *, since: str, to: str, limit: int = 1000, offset: int = 0) -> Dict[str, Any]:
        if not 1 <= int(limit) <= 1000:
            raise ValueError("Posting page size must be between 1 and 1000")
        return self._post_json(self.FBS_POSTING_ENDPOINT, {
            "dir": "ASC",
            "filter": {"since": since, "to": to, "status": ""},
            "limit": int(limit),
            "offset": int(offset),
            "with": {"analytics_data": True, "financial_data": False},
        })
